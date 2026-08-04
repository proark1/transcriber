"""Small repositories that enforce state transitions at the database boundary."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from transcriber.models import Language, Recording, RecordingStatus


class ActiveRecordingExists(RuntimeError):
    pass


class RecordingTransitionConflict(RuntimeError):
    pass


class RecordingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_uploading_recording(
        self,
        *,
        recording_id: UUID | None = None,
        display_filename: str,
        reported_content_type: str,
        expected_bytes: int,
        language: Language,
        original_object_key: str,
    ) -> Recording:
        recording_values: dict[str, object] = {
            "display_filename": display_filename,
            "reported_content_type": reported_content_type,
            "expected_bytes": expected_bytes,
            "language": language,
            "original_object_key": original_object_key,
            "status": RecordingStatus.UPLOADING,
        }
        if recording_id is not None:
            recording_values["id"] = recording_id
        recording = Recording(**recording_values)
        self._session.add(recording)
        try:
            self._session.flush()
        except IntegrityError as error:
            self._session.rollback()
            if _constraint_name(error) == "uq_recordings_one_active":
                raise ActiveRecordingExists("Another recording is already active.") from error
            raise
        return recording

    def get(self, recording_id: UUID) -> Recording | None:
        return self._session.scalar(select(Recording).where(Recording.id == recording_id))

    def transition(
        self,
        recording_id: UUID,
        *,
        expected: Iterable[RecordingStatus],
        target: RecordingStatus,
        values: dict[str, object] | None = None,
    ) -> Recording:
        expected_statuses = tuple(expected)
        if not expected_statuses:
            raise ValueError("A recording transition requires at least one expected status.")
        update_values = {**(values or {}), "status": target, "updated_at": datetime.now(UTC)}
        statement = (
            update(Recording)
            .where(Recording.id == recording_id, Recording.status.in_(expected_statuses))
            .values(**update_values)
            .returning(Recording)
        )
        recording = self._session.scalars(statement).one_or_none()
        if recording is None:
            raise RecordingTransitionConflict(
                f"Recording {recording_id} was not in an expected state."
            )
        self._session.flush()
        return recording


def _constraint_name(error: IntegrityError) -> str | None:
    original = error.orig
    diagnostic = getattr(original, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
