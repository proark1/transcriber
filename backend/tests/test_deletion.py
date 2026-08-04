from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from tests.test_recording_routes import history_recording
from tests.test_upload_routes import authenticated_headers
from transcriber.deletion import DeletionService
from transcriber.models import (
    ChunkStatus,
    Language,
    Recording,
    RecordingStatus,
    TranscriptionChunk,
    UploadSession,
    UploadStatus,
)
from transcriber.storage import ObjectMetadata
from transcriber.worker.repository import LeaseLost, WorkerRepository


def add_chunk(database: Session, recording: Recording) -> TranscriptionChunk:
    chunk = TranscriptionChunk(
        recording_id=recording.id,
        chunk_index=0,
        core_start_seconds=0,
        core_end_seconds=10,
        audio_start_seconds=0,
        audio_end_seconds=10,
        object_key=f"recordings/{recording.id}/chunks/00000.flac",
        size_bytes=100,
        status=ChunkStatus.COMPLETED,
        internal_segments=[{"start": 0, "end": 1, "text": "done", "words": []}],
        clean_text="done",
    )
    database.add(chunk)
    database.commit()
    return chunk


def seed_all_objects(
    storage: FakeObjectStorage, recording: Recording, chunk: TranscriptionChunk
) -> None:
    storage.objects[recording.original_object_key] = ObjectMetadata(5_000)
    assert recording.playback_object_key is not None
    storage.objects[recording.playback_object_key] = ObjectMetadata(1_000)
    storage.objects[chunk.object_key] = ObjectMetadata(100)


def test_deletion_removes_original_playback_chunks_transcript_and_database_rows(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    recording = history_recording(database_session)
    chunk = add_chunk(database_session, recording)
    seed_all_objects(fake_storage, recording, chunk)
    service = DeletionService(app_session_factory, fake_storage)

    service.begin(recording.id)
    deleted = service.reconcile(recording.id)

    assert deleted is True
    assert fake_storage.objects == {}
    with app_session_factory() as database:
        assert database.get(Recording, recording.id) is None
        assert database.get(TranscriptionChunk, chunk.id) is None


def test_storage_failure_keeps_deleting_visible_and_retryable(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    recording = history_recording(database_session)
    chunk = add_chunk(database_session, recording)
    seed_all_objects(fake_storage, recording, chunk)
    fake_storage.fail_delete = True
    service = DeletionService(app_session_factory, fake_storage)

    service.begin(recording.id)
    assert service.reconcile(recording.id) is False
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.DELETING
        assert stored.transcript_text is not None

    fake_storage.fail_delete = False
    assert service.reconcile_one() is True
    with app_session_factory() as database:
        assert database.get(Recording, recording.id) is None


def test_deletion_waits_for_a_live_worker_lease(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    recording = history_recording(database_session)
    recording.lease_owner = "worker"
    recording.lease_expires_at = now + timedelta(minutes=5)
    database_session.commit()
    fake_storage.objects[recording.original_object_key] = ObjectMetadata(5_000)
    service = DeletionService(app_session_factory, fake_storage)

    service.begin(recording.id, now=now)
    assert service.reconcile(recording.id, now=now) is False
    assert recording.original_object_key in fake_storage.objects
    assert service.reconcile(recording.id, now=now + timedelta(minutes=5, seconds=1)) is True


def test_deletion_aborts_an_unfinished_multipart_upload(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    recording_id = uuid4()
    object_key = f"recordings/{recording_id}/original/source"
    provider_id = fake_storage.create_multipart(object_key, "audio/mp4")
    recording = Recording(
        id=recording_id,
        display_filename="memo.m4a",
        reported_content_type="audio/mp4",
        expected_bytes=1_024,
        language=Language.ENGLISH,
        original_object_key=object_key,
        status=RecordingStatus.UPLOADING,
    )
    recording.upload_sessions.append(
        UploadSession(
            client_request_id=uuid4(),
            provider_upload_id=provider_id,
            object_key=object_key,
            expected_bytes=1_024,
            part_size_bytes=32 * 1024 * 1024,
            status=UploadStatus.UPLOADING,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    database_session.add(recording)
    database_session.commit()
    service = DeletionService(app_session_factory, fake_storage)

    service.begin(recording.id)
    assert service.reconcile(recording.id) is True
    assert fake_storage.aborted_uploads == [provider_id]


def test_delete_route_returns_pending_until_cleanup_succeeds(
    api_client: TestClient,
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    recording = history_recording(database_session)
    chunk = add_chunk(database_session, recording)
    seed_all_objects(fake_storage, recording, chunk)
    headers = authenticated_headers(api_client)
    fake_storage.fail_delete = True

    pending = api_client.delete(f"/api/recordings/{recording.id}", headers=headers)

    assert pending.status_code == 202
    assert pending.json() == {"status": "deleting"}
    detail = api_client.get(f"/api/recordings/{recording.id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "deleting"
    assert api_client.get(f"/api/recordings/{recording.id}/transcript").status_code == 409
    assert api_client.get(f"/api/recordings/{recording.id}/playback").status_code == 409
    assert (
        api_client.post(f"/api/recordings/{recording.id}/retry", headers=headers).status_code == 409
    )

    fake_storage.fail_delete = False
    completed = api_client.delete(f"/api/recordings/{recording.id}", headers=headers)
    assert completed.status_code == 204
    with app_session_factory() as database:
        assert database.get(Recording, recording.id) is None


def test_delete_route_requires_authentication_and_csrf(
    api_client: TestClient, database_session: Session
) -> None:
    recording = history_recording(database_session)
    assert api_client.delete(f"/api/recordings/{recording.id}").status_code == 401

    authenticated_headers(api_client)
    assert api_client.delete(f"/api/recordings/{recording.id}").status_code == 403


def test_delete_route_returns_not_found_for_missing_recording(
    api_client: TestClient,
) -> None:
    headers = authenticated_headers(api_client)

    response = api_client.delete(f"/api/recordings/{UUID(int=0)}", headers=headers)

    assert response.status_code == 404


def test_deleting_history_waits_until_the_active_recording_finishes(
    api_client: TestClient,
    database_session: Session,
) -> None:
    completed = history_recording(database_session)
    active = history_recording(
        database_session,
        status=RecordingStatus.QUEUED,
        transcript=None,
        playback=False,
    )
    del active
    headers = authenticated_headers(api_client)

    response = api_client.delete(f"/api/recordings/{completed.id}", headers=headers)

    assert response.status_code == 409


def test_a_worker_cannot_commit_new_text_after_deletion_begins(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    recording = history_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        transcript=None,
    )
    chunk = TranscriptionChunk(
        recording_id=recording.id,
        chunk_index=0,
        core_start_seconds=0,
        core_end_seconds=10,
        audio_start_seconds=0,
        audio_end_seconds=10,
        object_key=f"recordings/{recording.id}/chunks/00000.flac",
        size_bytes=100,
        status=ChunkStatus.PENDING,
    )
    recording.total_chunks = 1
    database_session.add(chunk)
    database_session.commit()
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    with app_session_factory.begin() as database:
        claim = WorkerRepository(database).claim_next("worker", now=now)
        assert claim is not None

    service = DeletionService(app_session_factory, fake_storage)
    service.begin(recording.id, now=now)
    with app_session_factory.begin() as database:
        with pytest.raises(LeaseLost):
            WorkerRepository(database).complete_chunk(
                claim,
                internal_segments=[{"start": 0, "end": 1, "text": "late"}],
                clean_text="late",
            )
