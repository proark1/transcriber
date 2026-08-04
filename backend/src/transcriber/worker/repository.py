"""PostgreSQL work claiming, retries, and expected-owner transitions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from transcriber.models import (
    ACTIVE_RECORDING_STATUSES,
    ChunkStatus,
    Recording,
    RecordingStatus,
    TranscriptionChunk,
)


class WorkKind(StrEnum):
    PREPARATION = "preparation"
    CHUNK = "chunk"
    ASSEMBLY = "assembly"


@dataclass(frozen=True)
class WorkClaim:
    kind: WorkKind
    recording_id: UUID
    worker_id: str
    chunk_id: UUID | None = None
    chunk_index: int | None = None
    attempt_count: int = 0


class LeaseLost(RuntimeError):
    pass


class RetryConflict(RuntimeError):
    pass


PREPARATION_STATUSES = (
    RecordingStatus.VALIDATING,
    RecordingStatus.NORMALIZING,
    RecordingStatus.CHUNKING,
)


class WorkerRepository:
    def __init__(self, database: Session, *, lease_seconds: int = 300) -> None:
        self._database = database
        self._lease_seconds = lease_seconds

    def claim_next(self, worker_id: str, *, now: datetime | None = None) -> WorkClaim | None:
        current_time = now or datetime.now(UTC)
        lease_expires = current_time + timedelta(seconds=self._lease_seconds)
        self._fail_exhausted_stale_chunk(current_time)

        recording = self._database.scalar(
            select(Recording)
            .where(
                or_(
                    Recording.status == RecordingStatus.QUEUED,
                    and_(
                        Recording.status.in_(PREPARATION_STATUSES),
                        or_(
                            Recording.lease_expires_at.is_(None),
                            Recording.lease_expires_at <= current_time,
                        ),
                    ),
                )
            )
            .order_by(Recording.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if recording is not None:
            if recording.status is RecordingStatus.QUEUED:
                recording.status = RecordingStatus.VALIDATING
            _lease_recording(recording, worker_id, current_time, lease_expires)
            self._database.flush()
            return WorkClaim(WorkKind.PREPARATION, recording.id, worker_id)

        chunk = self._database.scalar(
            select(TranscriptionChunk)
            .join(Recording)
            .where(
                Recording.status == RecordingStatus.TRANSCRIBING,
                or_(
                    TranscriptionChunk.status == ChunkStatus.PENDING,
                    and_(
                        TranscriptionChunk.status == ChunkStatus.FAILED,
                        TranscriptionChunk.attempt_count < 3,
                        or_(
                            TranscriptionChunk.next_attempt_at.is_(None),
                            TranscriptionChunk.next_attempt_at <= current_time,
                        ),
                    ),
                    and_(
                        TranscriptionChunk.status == ChunkStatus.RUNNING,
                        TranscriptionChunk.attempt_count < 3,
                        or_(
                            TranscriptionChunk.lease_expires_at.is_(None),
                            TranscriptionChunk.lease_expires_at <= current_time,
                        ),
                    ),
                ),
            )
            .order_by(TranscriptionChunk.chunk_index)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if chunk is not None:
            chunk.status = ChunkStatus.RUNNING
            chunk.attempt_count += 1
            chunk.lease_owner = worker_id
            chunk.heartbeat_at = current_time
            chunk.lease_expires_at = lease_expires
            chunk.next_attempt_at = None
            chunk.safe_error_code = None
            self._database.flush()
            return WorkClaim(
                WorkKind.CHUNK,
                chunk.recording_id,
                worker_id,
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                attempt_count=chunk.attempt_count,
            )

        incomplete_chunk = exists(
            select(TranscriptionChunk.id).where(
                TranscriptionChunk.recording_id == Recording.id,
                TranscriptionChunk.status != ChunkStatus.COMPLETED,
            )
        )
        recording = self._database.scalar(
            select(Recording)
            .where(
                Recording.total_chunks > 0,
                ~incomplete_chunk,
                or_(
                    Recording.status == RecordingStatus.TRANSCRIBING,
                    and_(
                        Recording.status == RecordingStatus.ASSEMBLING,
                        or_(
                            Recording.lease_expires_at.is_(None),
                            Recording.lease_expires_at <= current_time,
                        ),
                    ),
                ),
            )
            .order_by(Recording.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if recording is None:
            return None
        recording.status = RecordingStatus.ASSEMBLING
        _lease_recording(recording, worker_id, current_time, lease_expires)
        self._database.flush()
        return WorkClaim(WorkKind.ASSEMBLY, recording.id, worker_id)

    def cleanup_candidate(self) -> tuple[UUID, list[str]] | None:
        recording = self._database.scalar(
            select(Recording)
            .options(selectinload(Recording.chunks))
            .where(
                Recording.status == RecordingStatus.COMPLETED,
                Recording.working_chunks_deleted_at.is_(None),
            )
            .order_by(Recording.completed_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if recording is None:
            return None
        return recording.id, [chunk.object_key for chunk in recording.chunks]

    def mark_chunks_cleaned(self, recording_id: UUID, *, now: datetime | None = None) -> None:
        recording = self._database.get(Recording, recording_id)
        if recording is None or recording.status is not RecordingStatus.COMPLETED:
            return
        recording.working_chunks_deleted_at = now or datetime.now(UTC)
        self._database.flush()

    def heartbeat(self, claim: WorkClaim, *, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        lease_expires = current_time + timedelta(seconds=self._lease_seconds)
        if claim.kind is WorkKind.CHUNK:
            statement = (
                update(TranscriptionChunk)
                .where(
                    TranscriptionChunk.id == claim.chunk_id,
                    TranscriptionChunk.status == ChunkStatus.RUNNING,
                    TranscriptionChunk.lease_owner == claim.worker_id,
                )
                .values(heartbeat_at=current_time, lease_expires_at=lease_expires)
            )
        else:
            expected_statuses: Sequence[RecordingStatus] = (
                PREPARATION_STATUSES
                if claim.kind is WorkKind.PREPARATION
                else (RecordingStatus.ASSEMBLING,)
            )
            statement = (
                update(Recording)
                .where(
                    Recording.id == claim.recording_id,
                    Recording.status.in_(expected_statuses),
                    Recording.lease_owner == claim.worker_id,
                )
                .values(heartbeat_at=current_time, lease_expires_at=lease_expires)
            )
        result = self._database.execute(statement)
        if not isinstance(result, CursorResult) or result.rowcount != 1:
            raise LeaseLost("The work lease is no longer owned by this worker.")
        self._database.flush()

    def finish_preparation(self, claim: WorkClaim) -> None:
        recording = self._owned_recording(claim, expected=(RecordingStatus.TRANSCRIBING,))
        _clear_recording_lease(recording)

    def fail_preparation(self, claim: WorkClaim, safe_code: str) -> None:
        recording = self._owned_recording(claim, expected=PREPARATION_STATUSES)
        recording.status = RecordingStatus.FAILED
        recording.safe_error_code = safe_code
        _clear_recording_lease(recording)

    def complete_chunk(
        self,
        claim: WorkClaim,
        *,
        internal_segments: list[dict[str, object]],
        clean_text: str,
        now: datetime | None = None,
    ) -> None:
        chunk = self._owned_chunk(claim)
        chunk.internal_segments = internal_segments
        chunk.clean_text = clean_text
        chunk.status = ChunkStatus.COMPLETED
        chunk.completed_at = now or datetime.now(UTC)
        chunk.lease_owner = None
        chunk.lease_expires_at = None
        chunk.next_attempt_at = None
        chunk.safe_error_code = None
        completed = self._database.scalar(
            select(func.count(TranscriptionChunk.id)).where(
                TranscriptionChunk.recording_id == claim.recording_id,
                TranscriptionChunk.status == ChunkStatus.COMPLETED,
            )
        )
        recording = self._database.get(Recording, claim.recording_id)
        if recording is None:
            raise LeaseLost("The recording no longer exists.")
        recording.completed_chunks = int(completed or 0)
        self._database.flush()

    def fail_chunk(
        self,
        claim: WorkClaim,
        safe_code: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        chunk = self._owned_chunk(claim)
        chunk.status = ChunkStatus.FAILED
        chunk.safe_error_code = safe_code
        chunk.lease_owner = None
        chunk.lease_expires_at = None
        if chunk.attempt_count >= 3:
            chunk.next_attempt_at = None
            recording = self._database.get(Recording, claim.recording_id)
            if recording is None:
                raise LeaseLost("The recording no longer exists.")
            recording.status = RecordingStatus.FAILED
            recording.safe_error_code = safe_code
            _clear_recording_lease(recording)
        else:
            delay_seconds = 60 if chunk.attempt_count == 1 else 300
            chunk.next_attempt_at = current_time + timedelta(seconds=delay_seconds)
        self._database.flush()

    def assembly_chunks(self, claim: WorkClaim) -> tuple[Recording, list[TranscriptionChunk]]:
        recording = self._owned_recording(claim, expected=(RecordingStatus.ASSEMBLING,))
        chunks = list(
            self._database.scalars(
                select(TranscriptionChunk)
                .where(TranscriptionChunk.recording_id == claim.recording_id)
                .order_by(TranscriptionChunk.chunk_index)
            )
        )
        return recording, chunks

    def complete_assembly(
        self, claim: WorkClaim, transcript_text: str, *, now: datetime | None = None
    ) -> None:
        recording = self._owned_recording(claim, expected=(RecordingStatus.ASSEMBLING,))
        recording.transcript_text = transcript_text
        recording.status = RecordingStatus.COMPLETED
        recording.completed_at = now or datetime.now(UTC)
        recording.safe_error_code = None
        recording.completed_chunks = recording.total_chunks
        _clear_recording_lease(recording)

    def fail_assembly(self, claim: WorkClaim, safe_code: str) -> None:
        recording = self._owned_recording(claim, expected=(RecordingStatus.ASSEMBLING,))
        recording.status = RecordingStatus.FAILED
        recording.safe_error_code = safe_code
        _clear_recording_lease(recording)

    def retry_failed(self, recording_id: UUID) -> Recording:
        recording = self._database.scalar(
            select(Recording)
            .options(selectinload(Recording.chunks))
            .where(Recording.id == recording_id)
            .with_for_update()
        )
        if recording is None or recording.status is not RecordingStatus.FAILED:
            raise RetryConflict("Only a failed recording can be retried.")
        active_exists = self._database.scalar(
            select(
                exists().where(
                    Recording.id != recording_id,
                    Recording.status.in_(ACTIVE_RECORDING_STATUSES),
                )
            )
        )
        if active_exists:
            raise RetryConflict("Another recording is active.")
        incomplete = [
            chunk for chunk in recording.chunks if chunk.status is not ChunkStatus.COMPLETED
        ]
        for chunk in incomplete:
            chunk.status = ChunkStatus.PENDING
            chunk.attempt_count = 0
            chunk.heartbeat_at = None
            chunk.lease_owner = None
            chunk.lease_expires_at = None
            chunk.next_attempt_at = None
            chunk.safe_error_code = None
        chunks_are_prepared = bool(recording.chunks) and all(
            chunk.size_bytes is not None for chunk in recording.chunks
        )
        recording.status = (
            RecordingStatus.TRANSCRIBING if chunks_are_prepared else RecordingStatus.QUEUED
        )
        recording.safe_error_code = None
        recording.lease_owner = None
        recording.lease_expires_at = None
        recording.heartbeat_at = None
        recording.completed_chunks = sum(
            chunk.status is ChunkStatus.COMPLETED for chunk in recording.chunks
        )
        try:
            self._database.flush()
        except IntegrityError as error:
            self._database.rollback()
            raise RetryConflict("Another recording is active.") from error
        return recording

    def _owned_recording(
        self, claim: WorkClaim, *, expected: Sequence[RecordingStatus]
    ) -> Recording:
        recording = self._database.scalar(
            select(Recording)
            .where(
                Recording.id == claim.recording_id,
                Recording.status.in_(expected),
                Recording.lease_owner == claim.worker_id,
            )
            .with_for_update()
        )
        if recording is None:
            raise LeaseLost("The recording lease was lost.")
        return recording

    def _owned_chunk(self, claim: WorkClaim) -> TranscriptionChunk:
        chunk = self._database.scalar(
            select(TranscriptionChunk)
            .where(
                TranscriptionChunk.id == claim.chunk_id,
                TranscriptionChunk.status == ChunkStatus.RUNNING,
                TranscriptionChunk.lease_owner == claim.worker_id,
            )
            .with_for_update()
        )
        if chunk is None:
            raise LeaseLost("The chunk lease was lost.")
        return chunk

    def _fail_exhausted_stale_chunk(self, now: datetime) -> None:
        chunk = self._database.scalar(
            select(TranscriptionChunk)
            .join(Recording)
            .where(
                Recording.status == RecordingStatus.TRANSCRIBING,
                TranscriptionChunk.status == ChunkStatus.RUNNING,
                TranscriptionChunk.attempt_count >= 3,
                or_(
                    TranscriptionChunk.lease_expires_at.is_(None),
                    TranscriptionChunk.lease_expires_at <= now,
                ),
            )
            .order_by(TranscriptionChunk.chunk_index)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if chunk is None:
            return
        chunk.status = ChunkStatus.FAILED
        chunk.safe_error_code = "worker_lease_expired"
        chunk.lease_owner = None
        chunk.lease_expires_at = None
        recording = self._database.get(Recording, chunk.recording_id)
        if recording is not None:
            recording.status = RecordingStatus.FAILED
            recording.safe_error_code = "worker_lease_expired"
            _clear_recording_lease(recording)
        self._database.flush()


def _lease_recording(
    recording: Recording, worker_id: str, now: datetime, lease_expires: datetime
) -> None:
    recording.lease_owner = worker_id
    recording.heartbeat_at = now
    recording.lease_expires_at = lease_expires


def _clear_recording_lease(recording: Recording) -> None:
    recording.lease_owner = None
    recording.lease_expires_at = None
