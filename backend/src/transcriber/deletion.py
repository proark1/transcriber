"""Durable recording deletion across PostgreSQL and private object storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from transcriber.models import (
    ACTIVE_RECORDING_STATUSES,
    ChunkStatus,
    Recording,
    RecordingStatus,
    UploadStatus,
)
from transcriber.storage import ObjectStorage, StorageError


class RecordingNotFound(RuntimeError):
    pass


class DeletionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class DeletionTarget:
    recording_id: UUID
    object_keys: tuple[str, ...]
    multipart_uploads: tuple[tuple[str, str], ...]


class DeletionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage

    def begin(self, recording_id: UUID, *, now: datetime | None = None) -> None:
        try:
            with self._sessions.begin() as database:
                recording = database.scalar(
                    select(Recording).where(Recording.id == recording_id).with_for_update()
                )
                if recording is None:
                    raise RecordingNotFound
                if recording.status is not RecordingStatus.DELETING:
                    another_active = database.scalar(
                        select(
                            exists().where(
                                Recording.id != recording_id,
                                Recording.status.in_(ACTIVE_RECORDING_STATUSES),
                            )
                        )
                    )
                    if another_active:
                        raise DeletionConflict
                    recording.status = RecordingStatus.DELETING
                    recording.deletion_started_at = now or datetime.now(UTC)
        except IntegrityError as error:
            raise DeletionConflict from error

    def reconcile(self, recording_id: UUID, *, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(UTC)
        with self._sessions() as database:
            recording = database.scalar(
                select(Recording)
                .options(
                    selectinload(Recording.chunks),
                    selectinload(Recording.upload_sessions),
                )
                .where(Recording.id == recording_id)
            )
            if recording is None:
                return True
            if recording.status is not RecordingStatus.DELETING:
                return False
            if _has_live_work(recording, current_time):
                return False
            target = _target(recording)

        for object_key, provider_upload_id in target.multipart_uploads:
            try:
                self._storage.abort_multipart(object_key, provider_upload_id)
            except StorageError:
                return False
        try:
            failed = self._storage.delete_objects(list(target.object_keys))
        except StorageError:
            return False
        if failed:
            return False
        for object_key in target.object_keys:
            try:
                if self._storage.head_object(object_key) is not None:
                    return False
            except StorageError:
                return False

        with self._sessions.begin() as database:
            recording = database.scalar(
                select(Recording).where(Recording.id == recording_id).with_for_update()
            )
            if recording is None:
                return True
            if recording.status is not RecordingStatus.DELETING:
                return False
            database.delete(recording)
        return True

    def reconcile_one(self, *, now: datetime | None = None) -> bool:
        with self._sessions() as database:
            recording_id = database.scalar(
                select(Recording.id)
                .where(Recording.status == RecordingStatus.DELETING)
                .order_by(Recording.deletion_started_at)
                .limit(1)
            )
        if recording_id is None:
            return False
        return self.reconcile(recording_id, now=now)


def _has_live_work(recording: Recording, now: datetime) -> bool:
    if recording.lease_expires_at is not None and recording.lease_expires_at > now:
        return True
    return any(
        chunk.status is ChunkStatus.RUNNING
        and chunk.lease_expires_at is not None
        and chunk.lease_expires_at > now
        for chunk in recording.chunks
    )


def _target(recording: Recording) -> DeletionTarget:
    object_keys = {
        recording.original_object_key,
        f"recordings/{recording.id}/playback/audio.m4a",
        *(chunk.object_key for chunk in recording.chunks),
    }
    if recording.playback_object_key:
        object_keys.add(recording.playback_object_key)
    multipart_uploads = tuple(
        (upload.object_key, upload.provider_upload_id)
        for upload in recording.upload_sessions
        if upload.status is UploadStatus.UPLOADING
    )
    return DeletionTarget(
        recording_id=recording.id,
        object_keys=tuple(sorted(object_keys)),
        multipart_uploads=multipart_uploads,
    )
