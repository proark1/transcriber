"""Checkpoint cleanup of temporary transcription chunk objects."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_chunk_cleanup_checkpoint"
down_revision: str | None = "0002_upload_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("working_chunks_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recordings_working_chunks_deleted_at",
        "recordings",
        ["working_chunks_deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_recordings_working_chunks_deleted_at", table_name="recordings")
    op.drop_column("recordings", "working_chunks_deleted_at")
