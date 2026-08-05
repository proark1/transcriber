from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from transcriber.config import AppSettings
from transcriber.media import AudioProbe, ChunkPlan, MediaError, Silence
from transcriber.models import Language, Recording, RecordingStatus, TranscriptionChunk, User
from transcriber.storage import ObjectMetadata
from transcriber.worker.preparation import PreparationService


class FakeMediaProcessor:
    def __init__(self, *, fail_once_at: int | None = None) -> None:
        self.fail_once_at = fail_once_at
        self.rendered_chunks: list[int] = []
        self.playback_calls = 0

    def probe(self, source: Path) -> AudioProbe:
        assert source.exists()
        return AudioProbe(container="mov", audio_codec="aac", duration_seconds=2_500)

    def normalize(self, source: Path, output: Path) -> None:
        assert source.exists()
        output.write_bytes(b"normalized")

    def create_playback(self, source: Path, output: Path) -> None:
        assert source.exists()
        self.playback_calls += 1
        output.write_bytes(b"playback")

    def detect_silences(self, normalized: Path, duration_seconds: float) -> list[Silence]:
        assert normalized.exists() and duration_seconds == 2_500
        return [Silence(1_198, 1_202), Silence(2_398, 2_402)]

    def render_chunk(self, normalized: Path, output: Path, plan: ChunkPlan) -> None:
        assert normalized.exists()
        self.rendered_chunks.append(plan.chunk_index)
        if self.fail_once_at == plan.chunk_index:
            self.fail_once_at = None
            raise MediaError("media_chunk_failed")
        output.write_bytes(f"chunk-{plan.chunk_index}".encode())


def create_queued_recording(
    database: Session,
    storage: FakeObjectStorage,
    *,
    user: User,
    expected_bytes: int = 5,
) -> Recording:
    recording_id = uuid4()
    object_key = f"recordings/{recording_id}/original/source"
    recording = Recording(
        id=recording_id,
        user_id=user.id,
        display_filename="memo.m4a",
        reported_content_type="audio/mp4",
        expected_bytes=expected_bytes,
        verified_bytes=expected_bytes,
        language=Language.ENGLISH,
        original_object_key=object_key,
        status=RecordingStatus.QUEUED,
    )
    database.add(recording)
    database.commit()
    storage.objects[object_key] = ObjectMetadata(expected_bytes, "audio/mp4")
    return recording


def preparation_service(
    app_settings: AppSettings,
    sessions: sessionmaker[Session],
    storage: FakeObjectStorage,
    media: FakeMediaProcessor,
    scratch: Path,
) -> PreparationService:
    settings = app_settings.model_copy(update={"worker_scratch_dir": scratch})
    return PreparationService(settings, sessions, storage, media)


def test_preparation_persists_playback_and_every_chunk_before_transcription(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
    test_user: User,
) -> None:
    recording = create_queued_recording(database_session, fake_storage, user=test_user)
    media = FakeMediaProcessor()
    service = preparation_service(app_settings, app_session_factory, fake_storage, media, tmp_path)

    service.prepare(recording.id)

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        chunks = list(
            database.scalars(
                select(TranscriptionChunk)
                .where(TranscriptionChunk.recording_id == recording.id)
                .order_by(TranscriptionChunk.chunk_index)
            )
        )
        assert stored is not None
        assert stored.status is RecordingStatus.TRANSCRIBING
        assert stored.duration_seconds == 2_500
        assert stored.playback_object_key is not None
        assert stored.total_chunks == 3
        assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
        assert all(chunk.size_bytes is not None for chunk in chunks)
        assert chunks[0].audio_end_seconds > chunks[0].core_end_seconds

    service.prepare(recording.id)
    assert media.rendered_chunks == [0, 1, 2]
    assert media.playback_calls == 1


def test_preparation_restart_skips_already_uploaded_chunks(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
    test_user: User,
) -> None:
    recording = create_queued_recording(database_session, fake_storage, user=test_user)
    media = FakeMediaProcessor(fail_once_at=1)
    service = preparation_service(app_settings, app_session_factory, fake_storage, media, tmp_path)

    with pytest.raises(MediaError, match="media_chunk_failed"):
        service.prepare(recording.id)

    with app_session_factory() as database:
        chunks = list(
            database.scalars(
                select(TranscriptionChunk)
                .where(TranscriptionChunk.recording_id == recording.id)
                .order_by(TranscriptionChunk.chunk_index)
            )
        )
        assert len(chunks) == 3
        assert chunks[0].size_bytes is not None
        assert chunks[1].size_bytes is None

    service.prepare(recording.id)

    assert media.rendered_chunks == [0, 1, 1, 2]
    assert media.playback_calls == 1
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.TRANSCRIBING


def test_preparation_rejects_a_download_size_mismatch(
    database_session: Session,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
    tmp_path: Path,
    test_user: User,
) -> None:
    recording = create_queued_recording(
        database_session, fake_storage, user=test_user, expected_bytes=10
    )
    fake_storage.objects[recording.original_object_key] = ObjectMetadata(5)
    service = preparation_service(
        app_settings,
        app_session_factory,
        fake_storage,
        FakeMediaProcessor(),
        tmp_path,
    )

    with pytest.raises(MediaError, match="media_size_mismatch"):
        service.prepare(recording.id)
