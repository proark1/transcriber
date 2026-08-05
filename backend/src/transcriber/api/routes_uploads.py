"""Restart-safe direct multipart upload endpoints."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from transcriber.api.contracts import (
    AuthorizedPart,
    AuthorizedPartsResponse,
    AuthorizePartsRequest,
    CompleteUploadResponse,
    ConfirmedPart,
    CreateUploadRequest,
    UploadStateResponse,
)
from transcriber.api.dependencies import Authenticated, MutationAuthenticated, Settings
from transcriber.models import (
    Recording,
    RecordingStatus,
    UploadPart,
    UploadSession,
    UploadStatus,
)
from transcriber.repositories import ActiveRecordingExists, RecordingRepository
from transcriber.storage import ObjectMetadata, ObjectStorage, StorageError, StoredPart

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ACCEPTED_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4"}
ACCEPTED_CONTENT_TYPES = {
    "application/octet-stream",
    "application/ogg",
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/opus",
    "audio/vnd.wave",
    "audio/wav",
    "audio/x-aac",
    "audio/x-flac",
    "audio/x-m4a",
    "audio/x-wav",
    "video/mp4",
}
SAFE_FILENAME = re.compile(r"^[^\x00-\x1f\x7f/\\]+$")


@router.post("", response_model=UploadStateResponse, status_code=status.HTTP_201_CREATED)
def create_upload(
    payload: CreateUploadRequest,
    request: Request,
    auth: MutationAuthenticated,
    settings: Settings,
) -> UploadStateResponse:
    database = auth.database
    storage = _storage(request)
    existing = database.scalar(
        select(UploadSession)
        .join(UploadSession.recording)
        .options(selectinload(UploadSession.recording), selectinload(UploadSession.parts))
        .where(
            UploadSession.client_request_id == payload.client_request_id,
            Recording.user_id == auth.user.id,
        )
    )
    if existing is not None:
        if not _same_request(existing, payload):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT)
        return _state_response(existing)

    _validate_file(payload, settings.max_recording_bytes)
    recording_id = uuid4()
    object_key = f"recordings/{recording_id}/original/{uuid4().hex}"
    repository = RecordingRepository(database)
    try:
        recording = repository.create_uploading_recording(
            user_id=auth.user.id,
            recording_id=recording_id,
            display_filename=payload.filename,
            reported_content_type=payload.content_type,
            expected_bytes=payload.size_bytes,
            language=payload.language,
            original_object_key=object_key,
        )
    except ActiveRecordingExists as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error

    try:
        provider_upload_id = storage.create_multipart(object_key, payload.content_type)
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error

    upload = UploadSession(
        client_request_id=payload.client_request_id,
        recording=recording,
        provider_upload_id=provider_upload_id,
        object_key=object_key,
        expected_bytes=payload.size_bytes,
        part_size_bytes=settings.upload_part_bytes,
        status=UploadStatus.UPLOADING,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.upload_session_seconds),
    )
    database.add(upload)
    try:
        database.flush()
    except IntegrityError as error:
        try:
            storage.abort_multipart(object_key, provider_upload_id)
        except StorageError:
            pass
        if _constraint_name(error) in {
            "upload_sessions_client_request_id_key",
            "uq_upload_sessions_client_request_id",
        }:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error
        raise
    except Exception:
        try:
            storage.abort_multipart(object_key, provider_upload_id)
        except StorageError:
            pass
        raise
    return _state_response(upload)


@router.get("/{upload_session_id}", response_model=UploadStateResponse)
def get_upload(
    upload_session_id: UUID,
    request: Request,
    auth: Authenticated,
) -> UploadStateResponse:
    upload = _load_upload(auth.database, upload_session_id, user_id=auth.user.id)
    if upload.status is UploadStatus.UPLOADING:
        parts = _list_and_reconcile(auth.database, _storage(request), upload)
        return _state_response(upload, parts)
    return _state_response(upload)


@router.post("/{upload_session_id}/parts/authorize", response_model=AuthorizedPartsResponse)
def authorize_parts(
    upload_session_id: UUID,
    payload: AuthorizePartsRequest,
    request: Request,
    auth: MutationAuthenticated,
    settings: Settings,
) -> AuthorizedPartsResponse:
    upload = _load_upload(
        auth.database, upload_session_id, user_id=auth.user.id, for_update=True
    )
    _require_uploading(upload)
    _require_not_expired(auth.database, _storage(request), upload)
    part_count = _part_count(upload)
    if len(set(payload.part_numbers)) != len(payload.part_numbers) or any(
        part_number < 1 or part_number > part_count for part_number in payload.part_numbers
    ):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    parts = _list_and_reconcile(auth.database, _storage(request), upload)
    confirmed_numbers = {part.part_number for part in parts}
    authorized = [
        AuthorizedPart(
            part_number=part_number,
            url=_presign_part(_storage(request), upload, part_number, settings),
        )
        for part_number in payload.part_numbers
        if part_number not in confirmed_numbers
    ]
    return AuthorizedPartsResponse(
        authorized_parts=authorized,
        confirmed_parts=_confirmed_parts(parts),
        expires_at=upload.expires_at,
    )


@router.post("/{upload_session_id}/complete", response_model=CompleteUploadResponse)
def complete_upload(
    upload_session_id: UUID,
    request: Request,
    auth: MutationAuthenticated,
) -> CompleteUploadResponse:
    database = auth.database
    storage = _storage(request)
    upload = _load_upload(database, upload_session_id, user_id=auth.user.id, for_update=True)
    if upload.status is UploadStatus.COMPLETED:
        return CompleteUploadResponse(
            recording_id=upload.recording_id, status=upload.recording.status
        )
    _require_uploading(upload)
    _require_not_expired(database, storage, upload)

    try:
        parts = storage.list_parts(upload.object_key, upload.provider_upload_id)
    except StorageError as error:
        metadata = _safe_head(storage, upload.object_key)
        if metadata is not None and metadata.size_bytes == upload.expected_bytes:
            return _finalize_completed(database, upload)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error

    _validate_complete_parts(upload, parts)
    _persist_parts(database, upload, parts)
    try:
        storage.complete_multipart(upload.object_key, upload.provider_upload_id, parts)
    except StorageError as error:
        metadata = _safe_head(storage, upload.object_key)
        if metadata is None or metadata.size_bytes != upload.expected_bytes:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error

    metadata = _safe_head(storage, upload.object_key)
    if metadata is None or metadata.size_bytes != upload.expected_bytes:
        upload.status = UploadStatus.ABORTED
        upload.recording.status = RecordingStatus.FAILED
        upload.recording.safe_error_code = "upload_size_mismatch"
        database.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    return _finalize_completed(database, upload)


@router.post("/{upload_session_id}/abort", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    auth: MutationAuthenticated,
) -> None:
    upload = _load_upload(
        auth.database, upload_session_id, user_id=auth.user.id, for_update=True
    )
    if upload.status in {UploadStatus.ABORTED, UploadStatus.EXPIRED}:
        response.status_code = status.HTTP_204_NO_CONTENT
        return
    if upload.status is UploadStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    try:
        _storage(request).abort_multipart(upload.object_key, upload.provider_upload_id)
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    upload.status = UploadStatus.ABORTED
    upload.recording.status = RecordingStatus.FAILED
    upload.recording.safe_error_code = "upload_aborted"
    auth.database.flush()


def _storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.storage)


def _validate_file(payload: CreateUploadRequest, maximum_bytes: int) -> None:
    if payload.size_bytes > maximum_bytes:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    if SAFE_FILENAME.fullmatch(payload.filename) is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    extension = PurePath(payload.filename).suffix.lower()
    content_type = payload.content_type.lower().split(";", maxsplit=1)[0].strip()
    if extension not in ACCEPTED_EXTENSIONS and content_type not in ACCEPTED_CONTENT_TYPES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)


def _same_request(upload: UploadSession, payload: CreateUploadRequest) -> bool:
    recording = upload.recording
    return (
        upload.expected_bytes == payload.size_bytes
        and recording.display_filename == payload.filename
        and recording.reported_content_type == payload.content_type
        and recording.language is payload.language
    )


def _state_response(
    upload: UploadSession, provider_parts: list[StoredPart] | None = None
) -> UploadStateResponse:
    parts = (
        provider_parts
        if provider_parts is not None
        else [StoredPart(part.part_number, part.etag, part.size_bytes) for part in upload.parts]
    )
    return UploadStateResponse(
        recording_id=upload.recording_id,
        upload_session_id=upload.id,
        part_size_bytes=upload.part_size_bytes,
        part_count=_part_count(upload),
        expires_at=upload.expires_at,
        status=upload.status,
        confirmed_parts=_confirmed_parts(parts),
    )


def _load_upload(
    database: Session,
    upload_session_id: UUID,
    *,
    user_id: UUID,
    for_update: bool = False,
) -> UploadSession:
    statement = (
        select(UploadSession)
        .join(UploadSession.recording)
        .options(selectinload(UploadSession.recording), selectinload(UploadSession.parts))
        .where(UploadSession.id == upload_session_id, Recording.user_id == user_id)
    )
    if for_update:
        statement = statement.with_for_update()
    upload = database.scalar(statement)
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return upload


def _require_uploading(upload: UploadSession) -> None:
    if upload.status is not UploadStatus.UPLOADING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)


def _require_not_expired(database: Session, storage: ObjectStorage, upload: UploadSession) -> None:
    if upload.expires_at > datetime.now(UTC):
        return
    try:
        storage.abort_multipart(upload.object_key, upload.provider_upload_id)
    except StorageError:
        pass
    upload.status = UploadStatus.EXPIRED
    upload.recording.status = RecordingStatus.FAILED
    upload.recording.safe_error_code = "upload_expired"
    database.commit()
    raise HTTPException(status_code=status.HTTP_410_GONE)


def _part_count(upload: UploadSession) -> int:
    return math.ceil(upload.expected_bytes / upload.part_size_bytes)


def _expected_part_size(upload: UploadSession, part_number: int) -> int:
    if part_number < _part_count(upload):
        return upload.part_size_bytes
    return upload.expected_bytes - upload.part_size_bytes * (_part_count(upload) - 1)


def _list_and_reconcile(
    database: Session, storage: ObjectStorage, upload: UploadSession
) -> list[StoredPart]:
    try:
        parts = storage.list_parts(upload.object_key, upload.provider_upload_id)
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    for part in parts:
        if (
            part.part_number < 1
            or part.part_number > _part_count(upload)
            or part.size_bytes != _expected_part_size(upload, part.part_number)
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    _persist_parts(database, upload, parts)
    return parts


def _persist_parts(database: Session, upload: UploadSession, parts: list[StoredPart]) -> None:
    numbers = [part.part_number for part in parts]
    if numbers:
        database.execute(
            delete(UploadPart).where(
                UploadPart.upload_session_id == upload.id,
                UploadPart.part_number.not_in(numbers),
            )
        )
    else:
        database.execute(delete(UploadPart).where(UploadPart.upload_session_id == upload.id))
    for part in parts:
        database.execute(
            insert(UploadPart)
            .values(
                upload_session_id=upload.id,
                part_number=part.part_number,
                etag=part.etag,
                size_bytes=part.size_bytes,
            )
            .on_conflict_do_update(
                constraint="uq_upload_part_number",
                set_={"etag": part.etag, "size_bytes": part.size_bytes},
            )
        )
    database.flush()


def _confirmed_parts(parts: list[StoredPart]) -> list[ConfirmedPart]:
    return [
        ConfirmedPart(part_number=part.part_number, size_bytes=part.size_bytes)
        for part in sorted(parts, key=lambda part: part.part_number)
    ]


def _presign_part(
    storage: ObjectStorage,
    upload: UploadSession,
    part_number: int,
    settings: Settings,
) -> str:
    try:
        return storage.presign_upload_part(
            upload.object_key,
            upload.provider_upload_id,
            part_number,
            settings.presigned_url_seconds,
        )
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error


def _validate_complete_parts(upload: UploadSession, parts: list[StoredPart]) -> None:
    expected_numbers = list(range(1, _part_count(upload) + 1))
    if [part.part_number for part in parts] != expected_numbers:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    if any(part.size_bytes != _expected_part_size(upload, part.part_number) for part in parts):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)


def _safe_head(storage: ObjectStorage, object_key: str) -> ObjectMetadata | None:
    try:
        return storage.head_object(object_key)
    except StorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error


def _finalize_completed(database: Session, upload: UploadSession) -> CompleteUploadResponse:
    upload.status = UploadStatus.COMPLETED
    upload.completed_at = datetime.now(UTC)
    upload.recording.verified_bytes = upload.expected_bytes
    upload.recording.status = RecordingStatus.QUEUED
    database.flush()
    return CompleteUploadResponse(
        recording_id=upload.recording_id,
        status=RecordingStatus.QUEUED,
    )


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
