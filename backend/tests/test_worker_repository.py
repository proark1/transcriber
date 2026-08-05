from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from transcriber.models import (
    ChunkStatus,
    Language,
    Recording,
    RecordingStatus,
    TranscriptionChunk,
    User,
)
from transcriber.worker.repository import (
    LeaseLost,
    RetryConflict,
    WorkerRepository,
    WorkKind,
)

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


def create_recording(
    database: Session,
    *,
    status: RecordingStatus,
    chunk_statuses: list[ChunkStatus] | None = None,
    user: User | None = None,
) -> Recording:
    if user is None:
        user = database.scalar(select(User).order_by(User.created_at).limit(1))
    if user is None:
        user = User(username="worker-user", pin_hash="test-only-pin-hash")
        database.add(user)
        database.flush()
    recording = Recording(
        id=uuid4(),
        user_id=user.id,
        display_filename="memo.m4a",
        reported_content_type="audio/mp4",
        expected_bytes=1_024,
        verified_bytes=1_024,
        duration_seconds=100,
        container="mov",
        audio_codec="aac",
        language=Language.ENGLISH,
        original_object_key=f"recordings/{uuid4()}/original/source",
        playback_object_key=f"recordings/{uuid4()}/playback/audio.m4a",
        status=status,
        total_chunks=len(chunk_statuses or []),
        completed_chunks=sum(
            chunk_status is ChunkStatus.COMPLETED for chunk_status in chunk_statuses or []
        ),
    )
    database.add(recording)
    database.flush()
    for index, chunk_status in enumerate(chunk_statuses or []):
        database.add(
            TranscriptionChunk(
                recording_id=recording.id,
                chunk_index=index,
                core_start_seconds=index * 50,
                core_end_seconds=(index + 1) * 50,
                audio_start_seconds=max(0, index * 50 - 5),
                audio_end_seconds=(index + 1) * 50 + (0 if index == 1 else 5),
                object_key=f"recordings/{recording.id}/chunks/{index:05d}.flac",
                size_bytes=100,
                status=chunk_status,
                attempt_count=1 if chunk_status is ChunkStatus.COMPLETED else 0,
                internal_segments=(
                    [{"start": 0, "end": 1, "text": "done", "words": []}]
                    if chunk_status is ChunkStatus.COMPLETED
                    else None
                ),
                clean_text="done" if chunk_status is ChunkStatus.COMPLETED else None,
            )
        )
    database.commit()
    return recording


def test_preparation_lease_is_reclaimed_after_five_minutes(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(database_session, status=RecordingStatus.QUEUED)

    with app_session_factory.begin() as database:
        claim = WorkerRepository(database).claim_next("worker-one", now=NOW)
        assert claim is not None and claim.kind is WorkKind.PREPARATION

    with app_session_factory.begin() as database:
        assert WorkerRepository(database).claim_next("worker-two", now=NOW) is None

    with app_session_factory.begin() as database:
        reclaimed = WorkerRepository(database).claim_next(
            "worker-two", now=NOW + timedelta(seconds=301)
        )
        assert reclaimed is not None
        assert reclaimed.recording_id == recording.id
        assert reclaimed.worker_id == "worker-two"


def test_skip_locked_prevents_duplicate_claims(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    create_recording(database_session, status=RecordingStatus.QUEUED)
    first = app_session_factory()
    second = app_session_factory()
    try:
        first.begin()
        claim = WorkerRepository(first).claim_next("one", now=NOW)
        assert claim is not None

        second.begin()
        assert WorkerRepository(second).claim_next("two", now=NOW) is None
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_chunk_retries_are_delayed_and_stop_after_three_attempts(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.PENDING],
    )
    current = NOW
    for attempt, delay in [(1, 61), (2, 301), (3, 0)]:
        with app_session_factory.begin() as database:
            repository = WorkerRepository(database)
            claim = repository.claim_next(f"worker-{attempt}", now=current)
            assert claim is not None and claim.attempt_count == attempt
            repository.fail_chunk(claim, "transcription_failed", now=current)
        if attempt < 3:
            with app_session_factory.begin() as database:
                assert WorkerRepository(database).claim_next("early", now=current) is None
        current += timedelta(seconds=delay)

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.FAILED
        assert stored.chunks[0].attempt_count == 3


def test_completed_chunks_lead_to_an_assembly_claim(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.COMPLETED, ChunkStatus.COMPLETED],
    )

    with app_session_factory.begin() as database:
        claim = WorkerRepository(database).claim_next("assembler", now=NOW)

    assert claim is not None
    assert claim.kind is WorkKind.ASSEMBLY
    assert claim.recording_id == recording.id


def test_manual_retry_preserves_completed_chunks_and_resets_only_incomplete(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.FAILED,
        chunk_statuses=[ChunkStatus.COMPLETED, ChunkStatus.FAILED],
    )
    with app_session_factory.begin() as database:
        failed_chunk = database.get(Recording, recording.id).chunks[1]  # type: ignore[union-attr]
        failed_chunk.attempt_count = 3
        retried = WorkerRepository(database).retry_failed(
            recording.id, user_id=recording.user_id
        )
        assert retried.status is RecordingStatus.TRANSCRIBING

    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.chunks[0].status is ChunkStatus.COMPLETED
        assert stored.chunks[0].clean_text == "done"
        assert stored.chunks[0].attempt_count == 1
        assert stored.chunks[1].status is ChunkStatus.PENDING
        assert stored.chunks[1].attempt_count == 0
        assert stored.completed_chunks == 1


def test_manual_retry_rejects_when_another_recording_is_active(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    failed = create_recording(database_session, status=RecordingStatus.FAILED)
    create_recording(database_session, status=RecordingStatus.QUEUED)

    with app_session_factory.begin() as database:
        with pytest.raises(RetryConflict):
            WorkerRepository(database).retry_failed(failed.id, user_id=failed.user_id)


def test_another_users_active_recording_does_not_block_retry(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    failed = create_recording(database_session, status=RecordingStatus.FAILED)
    other_user = User(username="other-worker-user", pin_hash="test-only-pin-hash")
    database_session.add(other_user)
    database_session.flush()
    create_recording(database_session, status=RecordingStatus.QUEUED, user=other_user)

    with app_session_factory.begin() as database:
        retried = WorkerRepository(database).retry_failed(
            failed.id, user_id=failed.user_id
        )

    assert retried.status is RecordingStatus.QUEUED


def test_global_worker_claims_keep_each_recordings_user(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    first = create_recording(database_session, status=RecordingStatus.QUEUED)
    second_user = User(username="second-worker-user", pin_hash="test-only-pin-hash")
    database_session.add(second_user)
    database_session.flush()
    second = create_recording(
        database_session, status=RecordingStatus.QUEUED, user=second_user
    )

    with app_session_factory.begin() as database:
        first_claim = WorkerRepository(database).claim_next("single-worker", now=NOW)
    with app_session_factory.begin() as database:
        second_claim = WorkerRepository(database).claim_next("single-worker", now=NOW)

    assert first_claim is not None and second_claim is not None
    claimed_ids = {first_claim.recording_id, second_claim.recording_id}
    assert claimed_ids == {first.id, second.id}
    with app_session_factory() as database:
        owners = {
            recording.id: recording.user_id
            for recording in database.scalars(
                select(Recording).where(Recording.id.in_(claimed_ids))
            )
        }
    assert owners == {first.id: first.user_id, second.id: second.user_id}


def test_old_owner_cannot_commit_after_a_stale_claim_is_replaced(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.PENDING],
    )
    with app_session_factory.begin() as database:
        old_claim = WorkerRepository(database).claim_next("old", now=NOW)
        assert old_claim is not None
    with app_session_factory.begin() as database:
        new_claim = WorkerRepository(database).claim_next("new", now=NOW + timedelta(seconds=301))
        assert new_claim is not None
    with app_session_factory.begin() as database:
        with pytest.raises(LeaseLost):
            WorkerRepository(database).complete_chunk(
                old_claim,
                internal_segments=[],
                clean_text="stale",
            )


def test_stale_assembly_is_reclaimable(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.COMPLETED],
    )
    with app_session_factory.begin() as database:
        first = WorkerRepository(database).claim_next("first", now=NOW)
        assert first is not None and first.kind is WorkKind.ASSEMBLY
    with app_session_factory.begin() as database:
        replacement = WorkerRepository(database).claim_next(
            "replacement", now=NOW + timedelta(seconds=301)
        )
        assert replacement is not None
        assert replacement.kind is WorkKind.ASSEMBLY
        assert replacement.worker_id == "replacement"


def test_a_third_attempt_crash_fails_instead_of_sticking_forever(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(
        database_session,
        status=RecordingStatus.TRANSCRIBING,
        chunk_statuses=[ChunkStatus.PENDING],
    )
    with app_session_factory.begin() as database:
        chunk = database.get(Recording, recording.id).chunks[0]  # type: ignore[union-attr]
        chunk.status = ChunkStatus.RUNNING
        chunk.attempt_count = 3
        chunk.lease_owner = "crashed"
        chunk.lease_expires_at = NOW - timedelta(seconds=1)
    with app_session_factory.begin() as database:
        assert WorkerRepository(database).claim_next("replacement", now=NOW) is None
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.status is RecordingStatus.FAILED
        assert stored.safe_error_code == "worker_lease_expired"


def test_heartbeat_extends_only_the_current_owner_lease(
    database_session: Session, app_session_factory: sessionmaker[Session]
) -> None:
    recording = create_recording(database_session, status=RecordingStatus.QUEUED)
    with app_session_factory.begin() as database:
        repository = WorkerRepository(database)
        claim = repository.claim_next("owner", now=NOW)
        assert claim is not None
    heartbeat_time = NOW + timedelta(seconds=100)
    with app_session_factory.begin() as database:
        WorkerRepository(database).heartbeat(claim, now=heartbeat_time)
    with app_session_factory() as database:
        stored = database.get(Recording, recording.id)
        assert stored is not None
        assert stored.heartbeat_at == heartbeat_time
        assert stored.lease_expires_at == heartbeat_time + timedelta(seconds=300)
