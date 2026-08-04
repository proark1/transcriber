"""Create authentication, upload, recording, and transcription tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

language = sa.Enum("en", "de", "tr", name="language", native_enum=False, create_constraint=True)
recording_status = sa.Enum(
    "uploading",
    "queued",
    "validating",
    "normalizing",
    "chunking",
    "transcribing",
    "assembling",
    "completed",
    "failed",
    "deleting",
    name="recording_status",
    native_enum=False,
    create_constraint=True,
)
upload_status = sa.Enum(
    "uploading",
    "completed",
    "aborted",
    "expired",
    name="upload_status",
    native_enum=False,
    create_constraint=True,
)
chunk_status = sa.Enum(
    "pending",
    "running",
    "completed",
    "failed",
    name="chunk_status",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hmac", sa.String(length=64), nullable=False),
        sa.Column("csrf_hmac", sa.String(length=64), nullable=False),
        sa.Column("credential_version", sa.String(length=64), nullable=False),
        sa.Column("security_key_hmac", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hmac"),
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "login_attempts",
        sa.Column("security_key_hmac", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("failure_count >= 0", name="ck_login_failure_count"),
        sa.PrimaryKeyConstraint("security_key_hmac"),
    )
    op.create_index("ix_login_attempts_locked_until", "login_attempts", ["locked_until"])

    op.create_table(
        "recordings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("reported_content_type", sa.String(length=255), nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=False),
        sa.Column("verified_bytes", sa.BigInteger()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("container", sa.String(length=64)),
        sa.Column("audio_codec", sa.String(length=64)),
        sa.Column("language", language, nullable=False),
        sa.Column("original_object_key", sa.String(length=512), nullable=False),
        sa.Column("playback_object_key", sa.String(length=512)),
        sa.Column("status", recording_status, nullable=False),
        sa.Column("completed_chunks", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64)),
        sa.Column("transcript_text", sa.Text()),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("deletion_started_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("expected_bytes > 0", name="ck_recording_expected_bytes"),
        sa.CheckConstraint("completed_chunks >= 0", name="ck_recording_completed_chunks"),
        sa.CheckConstraint("total_chunks >= 0", name="ck_recording_total_chunks"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_object_key"),
        sa.UniqueConstraint("playback_object_key"),
    )
    op.create_index("ix_recordings_status", "recordings", ["status"])
    op.create_index("ix_recordings_heartbeat_at", "recordings", ["heartbeat_at"])
    op.create_index("ix_recordings_lease_expires_at", "recordings", ["lease_expires_at"])
    op.create_index(
        "uq_recordings_one_active",
        "recordings",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(
            "status in ('uploading', 'queued', 'validating', 'normalizing', "
            "'chunking', 'transcribing', 'assembling', 'deleting')"
        ),
    )

    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("provider_upload_id", sa.String(length=512), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=False),
        sa.Column("part_size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", upload_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("expected_bytes > 0", name="ck_upload_expected_bytes"),
        sa.CheckConstraint("part_size_bytes > 0", name="ck_upload_part_size"),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_upload_id"),
    )
    op.create_index("ix_upload_sessions_recording_id", "upload_sessions", ["recording_id"])
    op.create_index("ix_upload_sessions_expires_at", "upload_sessions", ["expires_at"])

    op.create_table(
        "upload_parts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("etag", sa.String(length=256), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("part_number between 1 and 10000", name="ck_upload_part_number"),
        sa.CheckConstraint("size_bytes > 0", name="ck_upload_part_size_bytes"),
        sa.ForeignKeyConstraint(["upload_session_id"], ["upload_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upload_session_id", "part_number", name="uq_upload_part_number"),
    )
    op.create_index("ix_upload_parts_upload_session_id", "upload_parts", ["upload_session_id"])

    op.create_table(
        "transcription_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("core_start_seconds", sa.Float(), nullable=False),
        sa.Column("core_end_seconds", sa.Float(), nullable=False),
        sa.Column("audio_start_seconds", sa.Float(), nullable=False),
        sa.Column("audio_end_seconds", sa.Float(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("status", chunk_status, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("internal_segments", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("clean_text", sa.Text()),
        sa.Column("safe_error_code", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("chunk_index >= 0", name="ck_chunk_index"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_chunk_attempt_count"),
        sa.CheckConstraint("core_start_seconds >= 0", name="ck_chunk_core_start"),
        sa.CheckConstraint("core_end_seconds > core_start_seconds", name="ck_chunk_core_order"),
        sa.CheckConstraint("audio_start_seconds >= 0", name="ck_chunk_audio_start"),
        sa.CheckConstraint("audio_end_seconds > audio_start_seconds", name="ck_chunk_audio_order"),
        sa.CheckConstraint(
            "audio_start_seconds <= core_start_seconds and audio_end_seconds >= core_end_seconds",
            name="ck_chunk_core_inside_audio",
        ),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.UniqueConstraint("recording_id", "chunk_index", name="uq_recording_chunk_index"),
    )
    op.create_index(
        "ix_transcription_chunks_recording_id", "transcription_chunks", ["recording_id"]
    )
    op.create_index("ix_transcription_chunks_status", "transcription_chunks", ["status"])
    op.create_index(
        "ix_transcription_chunks_heartbeat_at", "transcription_chunks", ["heartbeat_at"]
    )
    op.create_index(
        "ix_transcription_chunks_lease_expires_at",
        "transcription_chunks",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_transcription_chunks_next_attempt_at", "transcription_chunks", ["next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_table("transcription_chunks")
    op.drop_table("upload_parts")
    op.drop_table("upload_sessions")
    op.drop_table("recordings")
    op.drop_table("login_attempts")
    op.drop_table("auth_sessions")
