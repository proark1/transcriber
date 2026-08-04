from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from tests.test_worker_repository import NOW, create_recording
from transcriber.config import AppSettings
from transcriber.media import MediaError
from transcriber.models import (
    ChunkStatus,
    Language,
    Recording,
    RecordingStatus,
    TranscriptionChunk,
)
from transcriber.storage import ObjectMetadata
from transcriber.whisper_engine import TranscriptionError, TranscriptSegment
from transcriber.worker.runner import WorkerRunner


class SequenceTranscriber:
    def __init__(self, results: list[list[TranscriptSegment]]) -> None:
        self.results = results
        self.calls: list[Language] = []

    def transcribe(self, audio_path: Path, language: Language) -> list[TranscriptSegment]:
        assert audio_path.exists()
        self.calls.append(language)
        return self.results.pop(0)


class FailingTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_path: Path, language: Language) -> list[TranscriptSegment]:
        del audio_path, language
        self.calls += 1
        raise TranscriptionError("transcription_failed")


class Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class NoopPreparation:
    def prepare(self, recording_id: object) -> None:
        raise AssertionError(f"Unexpected preparation claim for {recording_id}")


class DatabasePreparation:
    def __init__(self, sessions: sessionmaker[Session], *, fail: bool = False) -> None:
        self._sessions = sessions
        self._fail = fail

    def prepare(self, recording_id: object) -> None:
        if self._fail:
            raise MediaError("media_unreadable")
        with self._sessions.begin() as database:
            recording = database.get(Recording, recording_id)
            assert recording is not None
            recording.status = RecordingStatus.TRANSCRIBING
            recording.total_chunks = 1
            database.add(
                TranscriptionChunk(
                    recording_id=recording.id,
                    chunk_index=0,
                    core_start_seconds=0,
                    core_end_seconds=10,
                    audio_start_seconds=0,
                    audio_end_seconds=10,
                    object_key=f"recordings/{recording.id}/chunks/00000.flac",
                    size_bytes=10,
                    status=ChunkStatus.PENDING,
                )
            )


def runner_settings(app_settings: AppSettings, scratch: Path) -> AppSettings:
    return app_settings.model_copy(update={"worker_scratch_dir": scratch})


def add_chunk_objects(recording: Recording, storage: FakeObjectStorage) -> None:
    for chunk in recording.chunks:
        storage.objects[chunk.object_key] = ObjectMetadata(chunk.size_bytes or 1, "audio/flac")


def test_runner_transcribes_in_order_assembles_and_cleans_working_chunks(
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
    add_chunk_objects(recording, fake_storage)
    transcriber = SequenceTranscriber(
        [
            [TranscriptSegment(45, 55, "Hello repeated words.")],
            [TranscriptSegment(0, 10, "Repeated words. Continue.")],
        ]
    )
    runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        transcriber,
        preparation=NoopPreparation(),
        worker_id="worker",
        clock=Clock(),
    )

    assert runner.run_one() is True
    assert runner.run_one() is True
    assert runner.run_one() is True
    assert runner.run_one() is False

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.COMPLETED
        assert stored.transcript_text == "Hello repeated words. Continue.\n"
        assert stored.completed_chunks == 2
        chunks = list(
            database.scalars(
                select(TranscriptionChunk)
                .where(TranscriptionChunk.recording_id == recording.id)
                .order_by(TranscriptionChunk.chunk_index)
            )
        )
        assert all(chunk.internal_segments for chunk in chunks)
        assert all(chunk.clean_text for chunk in chunks)
        assert all(chunk.object_key not in fake_storage.objects for chunk in chunks)
    assert transcriber.calls == [Language.ENGLISH, Language.ENGLISH]


def test_runner_prepares_a_queued_recording_and_releases_its_lease(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
) -> None:
    recording = create_recording(database_session, status=RecordingStatus.QUEUED)
    runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        SequenceTranscriber([]),
        preparation=DatabasePreparation(app_session_factory),
        worker_id="worker",
        clock=Clock(),
    )

    assert runner.run_one() is True

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.TRANSCRIBING
        assert stored.lease_owner is None
        assert stored.total_chunks == 1


def test_preparation_failure_becomes_a_safe_failed_recording(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
) -> None:
    recording = create_recording(database_session, status=RecordingStatus.QUEUED)
    runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        SequenceTranscriber([]),
        preparation=DatabasePreparation(app_session_factory, fail=True),
        worker_id="worker",
        clock=Clock(),
    )

    runner.run_one()

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.FAILED
        assert stored.safe_error_code == "media_unreadable"
        assert stored.lease_owner is None


def test_runner_applies_one_and_five_minute_chunk_retries(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.PENDING],
    )
    add_chunk_objects(recording, fake_storage)
    clock = Clock(datetime(2026, 8, 4, 12, tzinfo=UTC))
    transcriber = FailingTranscriber()
    runner = WorkerRunner(
        runner_settings(app_settings, tmp_path),
        app_session_factory,
        fake_storage,
        transcriber,
        preparation=NoopPreparation(),
        worker_id="worker",
        clock=clock,
    )

    runner.run_one()
    assert runner.run_one() is False
    clock.advance(61)
    runner.run_one()
    assert runner.run_one() is False
    clock.advance(301)
    runner.run_one()

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.FAILED
        assert stored.chunks[0].attempt_count == 3
    assert transcriber.calls == 3
