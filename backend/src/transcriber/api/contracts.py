"""Shared JSON contracts for the browser client."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from transcriber.models import Language, RecordingStatus, UploadStatus


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiContract(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class CreateUploadRequest(ApiContract):
    client_request_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    language: Language


class ConfirmedPart(ApiContract):
    part_number: int
    size_bytes: int


class AuthorizedPart(ApiContract):
    part_number: int
    url: str


class UploadStateResponse(ApiContract):
    recording_id: UUID
    upload_session_id: UUID
    part_size_bytes: int
    part_count: int
    expires_at: datetime
    status: UploadStatus
    confirmed_parts: list[ConfirmedPart]


class AuthorizePartsRequest(ApiContract):
    part_numbers: list[int] = Field(min_length=1, max_length=10)


class AuthorizedPartsResponse(ApiContract):
    authorized_parts: list[AuthorizedPart]
    confirmed_parts: list[ConfirmedPart]
    expires_at: datetime


class CompleteUploadResponse(ApiContract):
    recording_id: UUID
    status: RecordingStatus
