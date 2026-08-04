"""Relational state for authentication, uploads, and recoverable transcription."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Language(StrEnum):
    ENGLISH = "en"
    GERMAN = "de"
    TURKISH = "tr"


class RecordingStatus(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    VALIDATING = "validating"
    NORMALIZING = "normalizing"
    CHUNKING = "chunking"
    TRANSCRIBING = "transcribing"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETING = "deleting"


class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class ChunkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


ACTIVE_RECORDING_STATUSES = (
    RecordingStatus.UPLOADING,
    RecordingStatus.QUEUED,
    RecordingStatus.VALIDATING,
    RecordingStatus.NORMALIZING,
    RecordingStatus.CHUNKING,
    RecordingStatus.TRANSCRIBING,
    RecordingStatus.ASSEMBLING,
    RecordingStatus.DELETING,
)


def enum_column(enum_type: type[StrEnum], *, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hmac: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hmac: Mapped[str] = mapped_column(String(64))
    credential_version: Mapped[str] = mapped_column(String(64))
    security_key_hmac: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    security_key_hmac: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("failure_count >= 0", name="ck_login_failure_count"),)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    display_filename: Mapped[str] = mapped_column(String(255))
    reported_content_type: Mapped[str] = mapped_column(String(255))
    expected_bytes: Mapped[int] = mapped_column(BigInteger)
    verified_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    container: Mapped[str | None] = mapped_column(String(64))
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[Language] = mapped_column(enum_column(Language, name="language"))
    original_object_key: Mapped[str] = mapped_column(String(512), unique=True)
    playback_object_key: Mapped[str | None] = mapped_column(String(512), unique=True)
    status: Mapped[RecordingStatus] = mapped_column(
        enum_column(RecordingStatus, name="recording_status"),
        default=RecordingStatus.UPLOADING,
        index=True,
    )
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    transcript_text: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    upload_sessions: Mapped[list[UploadSession]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[TranscriptionChunk]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="TranscriptionChunk.chunk_index",
    )

    __table_args__ = (
        CheckConstraint("expected_bytes > 0", name="ck_recording_expected_bytes"),
        CheckConstraint("completed_chunks >= 0", name="ck_recording_completed_chunks"),
        CheckConstraint("total_chunks >= 0", name="ck_recording_total_chunks"),
        Index(
            "uq_recordings_one_active",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "status in ('uploading', 'queued', 'validating', 'normalizing', "
                "'chunking', 'transcribing', 'assembling', 'deleting')"
            ),
        ),
    )


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recording_id: Mapped[UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    provider_upload_id: Mapped[str] = mapped_column(String(512), unique=True)
    object_key: Mapped[str] = mapped_column(String(512))
    expected_bytes: Mapped[int] = mapped_column(BigInteger)
    part_size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[UploadStatus] = mapped_column(
        enum_column(UploadStatus, name="upload_status"), default=UploadStatus.UPLOADING
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship(back_populates="upload_sessions")
    parts: Mapped[list[UploadPart]] = relationship(
        back_populates="upload_session",
        cascade="all, delete-orphan",
        order_by="UploadPart.part_number",
    )

    __table_args__ = (
        CheckConstraint("expected_bytes > 0", name="ck_upload_expected_bytes"),
        CheckConstraint("part_size_bytes > 0", name="ck_upload_part_size"),
    )


class UploadPart(Base):
    __tablename__ = "upload_parts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), index=True
    )
    part_number: Mapped[int] = mapped_column(Integer)
    etag: Mapped[str] = mapped_column(String(256))
    size_bytes: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    upload_session: Mapped[UploadSession] = relationship(back_populates="parts")

    __table_args__ = (
        UniqueConstraint("upload_session_id", "part_number", name="uq_upload_part_number"),
        CheckConstraint("part_number between 1 and 10000", name="ck_upload_part_number"),
        CheckConstraint("size_bytes > 0", name="ck_upload_part_size_bytes"),
    )


class TranscriptionChunk(Base):
    __tablename__ = "transcription_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recording_id: Mapped[UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    core_start_seconds: Mapped[float] = mapped_column(Float)
    core_end_seconds: Mapped[float] = mapped_column(Float)
    audio_start_seconds: Mapped[float] = mapped_column(Float)
    audio_end_seconds: Mapped[float] = mapped_column(Float)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[ChunkStatus] = mapped_column(
        enum_column(ChunkStatus, name="chunk_status"),
        default=ChunkStatus.PENDING,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    internal_segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    clean_text: Mapped[str | None] = mapped_column(Text)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("recording_id", "chunk_index", name="uq_recording_chunk_index"),
        CheckConstraint("chunk_index >= 0", name="ck_chunk_index"),
        CheckConstraint("attempt_count >= 0", name="ck_chunk_attempt_count"),
        CheckConstraint("core_start_seconds >= 0", name="ck_chunk_core_start"),
        CheckConstraint("core_end_seconds > core_start_seconds", name="ck_chunk_core_order"),
        CheckConstraint("audio_start_seconds >= 0", name="ck_chunk_audio_start"),
        CheckConstraint("audio_end_seconds > audio_start_seconds", name="ck_chunk_audio_order"),
        CheckConstraint(
            "audio_start_seconds <= core_start_seconds and audio_end_seconds >= core_end_seconds",
            name="ck_chunk_core_inside_audio",
        ),
    )
