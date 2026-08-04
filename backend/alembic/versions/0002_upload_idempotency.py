"""Add a browser-generated idempotency key to multipart uploads."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_upload_idempotency"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "upload_sessions",
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "update upload_sessions set client_request_id = gen_random_uuid() "
        "where client_request_id is null"
    )
    op.alter_column("upload_sessions", "client_request_id", nullable=False)
    op.create_unique_constraint(
        "uq_upload_sessions_client_request_id",
        "upload_sessions",
        ["client_request_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_upload_sessions_client_request_id", "upload_sessions", type_="unique")
    op.drop_column("upload_sessions", "client_request_id")
