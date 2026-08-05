"""Recording history, playback, transcript, retry, and deletion endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from transcriber.api.contracts import (
    DeleteRecordingResponse,
    PlaybackResponse,
    RecordingResponse,
)
from transcriber.api.dependencies import Authenticated, MutationAuthenticated, Settings
from transcriber.deletion import DeletionConflict, DeletionService, RecordingNotFound
from transcriber.models import Recording, RecordingStatus
from transcriber.worker.repository import RetryConflict, WorkerRepository

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


@router.get("", response_model=list[RecordingResponse])
def list_recordings(auth: Authenticated) -> list[RecordingResponse]:
    recordings = list(
        auth.database.scalars(
            select(Recording)
            .where(Recording.user_id == auth.user.id)
            .order_by(Recording.created_at.desc())
        )
    )
    return [_recording_response(recording) for recording in recordings]


@router.get("/{recording_id}", response_model=RecordingResponse)
def get_recording(recording_id: UUID, auth: Authenticated) -> RecordingResponse:
    return _recording_response(_recording(auth, recording_id))


@router.get("/{recording_id}/playback", response_model=PlaybackResponse)
def playback(
    recording_id: UUID,
    request: Request,
    auth: Authenticated,
    settings: Settings,
) -> PlaybackResponse:
    recording = _recording(auth, recording_id)
    if recording.status is RecordingStatus.DELETING or not recording.playback_object_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    storage = request.app.state.storage
    try:
        metadata = storage.head_object(recording.playback_object_key)
        if metadata is None or metadata.size_bytes <= 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT)
        url = storage.presign_get(recording.playback_object_key, settings.playback_url_seconds)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from error
    return PlaybackResponse(
        url=url,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.playback_url_seconds),
    )


@router.get("/{recording_id}/transcript")
def transcript(recording_id: UUID, auth: Authenticated) -> Response:
    recording = _transcript_recording(auth, recording_id)
    return _transcript_response(recording, download=False)


@router.get("/{recording_id}/transcript.txt")
def download_transcript(recording_id: UUID, auth: Authenticated) -> Response:
    recording = _transcript_recording(auth, recording_id)
    return _transcript_response(recording, download=True)


@router.post("/{recording_id}/retry", response_model=RecordingResponse)
def retry_recording(
    recording_id: UUID, auth: MutationAuthenticated, settings: Settings
) -> RecordingResponse:
    _recording(auth, recording_id)
    try:
        recording = WorkerRepository(
            auth.database, lease_seconds=settings.worker_lease_seconds
        ).retry_failed(recording_id, user_id=auth.user.id)
    except RetryConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error
    return _recording_response(recording)


@router.delete("/{recording_id}")
def delete_recording(
    recording_id: UUID,
    request: Request,
    auth: MutationAuthenticated,
) -> Response:
    service = DeletionService(request.app.state.session_factory, request.app.state.storage)
    try:
        service.begin(recording_id, user_id=auth.user.id)
    except RecordingNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except DeletionConflict as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from error
    if service.reconcile(recording_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=DeleteRecordingResponse(status=RecordingStatus.DELETING).model_dump(
            mode="json", by_alias=True
        ),
    )


def _recording(auth: Authenticated, recording_id: UUID) -> Recording:
    recording = auth.database.scalar(
        select(Recording).where(
            Recording.id == recording_id,
            Recording.user_id == auth.user.id,
        )
    )
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return recording


def _transcript_recording(auth: Authenticated, recording_id: UUID) -> Recording:
    recording = _recording(auth, recording_id)
    if recording.status is not RecordingStatus.COMPLETED or recording.transcript_text is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)
    return recording


def _transcript_response(recording: Recording, *, download: bool) -> Response:
    headers = {"Cache-Control": "no-store"}
    if download:
        stem = recording.display_filename.rsplit(".", maxsplit=1)[0] or "transcript"
        encoded = quote(f"{stem}.txt", safe="")
        headers["Content-Disposition"] = (
            f"attachment; filename=\"transcript.txt\"; filename*=UTF-8''{encoded}"
        )
    return Response(
        content=(recording.transcript_text or "").encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


def _recording_response(recording: Recording) -> RecordingResponse:
    return RecordingResponse(
        id=recording.id,
        filename=recording.display_filename,
        content_type=recording.reported_content_type,
        language=recording.language,
        status=recording.status,
        created_at=recording.created_at,
        completed_at=recording.completed_at,
        duration_seconds=recording.duration_seconds,
        verified_bytes=recording.verified_bytes,
        completed_chunks=recording.completed_chunks,
        total_chunks=recording.total_chunks,
        safe_error_code=recording.safe_error_code,
        has_playback=recording.playback_object_key is not None,
        has_transcript=recording.transcript_text is not None,
    )
