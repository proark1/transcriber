from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from tests.test_worker_repository import NOW, create_recording
from tests.test_worker_runner import Clock, NoopPreparation, SequenceTranscriber, runner_settings
from transcriber.config import AppSettings
from transcriber.models import ChunkStatus, Recording, RecordingStatus
from transcriber.storage import ObjectMetadata
from transcriber.whisper_engine import TranscriptSegment
from transcriber.worker.repository import WorkerRepository, WorkKind
from transcriber.worker.runner import WorkerRunner


def test_restart_never_repeats_a_completed_chunk(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.PENDING, ChunkStatus.PENDING],
    )
    for chunk in recording.chunks:
        fake_storage.objects[chunk.object_key] = ObjectMetadata(100, "audio/flac")
    first_transcriber = SequenceTranscriber(
        [[TranscriptSegment(0, 5, "First chunk remains saved.")]]
    )
    first_runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        first_transcriber,
        preparation=NoopPreparation(),
        worker_id="first-worker",
        clock=Clock(NOW),
    )
    assert first_runner.run_one() is True

    with app_session_factory.begin() as database:
        crashed_claim = WorkerRepository(database).claim_next("crashed-worker", now=NOW)
        assert crashed_claim is not None
        assert crashed_claim.kind is WorkKind.CHUNK
        assert crashed_claim.chunk_index == 1

    second_transcriber = SequenceTranscriber(
        [[TranscriptSegment(0, 5, "Second chunk finishes after restart.")]]
    )
    restarted = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        second_transcriber,
        preparation=NoopPreparation(),
        worker_id="replacement-worker",
        clock=Clock(NOW.replace(minute=6)),
    )

    assert restarted.run_one() is True

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.chunks[0].status is ChunkStatus.COMPLETED
        assert stored.chunks[0].clean_text == "First chunk remains saved."
        assert stored.chunks[0].attempt_count == 1
        assert stored.chunks[1].status is ChunkStatus.COMPLETED
        assert stored.chunks[1].attempt_count == 2
    assert len(first_transcriber.calls) == 1
    assert len(second_transcriber.calls) == 1


def test_missed_post_assembly_cleanup_retries_until_storage_succeeds(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.COMPLETED,
        chunk_statuses=[ChunkStatus.COMPLETED],
    )
    chunk_key = recording.chunks[0].object_key
    fake_storage.objects[chunk_key] = ObjectMetadata(100, "audio/flac")
    fake_storage.fail_delete = True
    runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        SequenceTranscriber([]),
        preparation=NoopPreparation(),
        worker_id="cleaner",
        clock=Clock(NOW),
    )

    assert runner.run_one() is False
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None and stored.working_chunks_deleted_at is None

    fake_storage.fail_delete = False
    assert runner.run_one() is True
    assert chunk_key not in fake_storage.objects
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None and stored.working_chunks_deleted_at == NOW
