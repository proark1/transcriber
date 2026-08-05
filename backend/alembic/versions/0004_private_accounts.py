"""Add self-registering users and private recording ownership."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_private_accounts"
down_revision: str | None = "0003_chunk_cleanup_checkpoint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_RECORDING_PREDICATE = sa.text(
    "status in ('uploading', 'queued', 'validating', 'normalizing', "
    "'chunking', 'transcribing', 'assembling', 'deleting')"
)


def upgrade() -> None:
    connection = op.get_bind()
    recording_count = connection.scalar(sa.text("select count(*) from recordings"))
    upload_count = connection.scalar(sa.text("select count(*) from upload_sessions"))
    if int(recording_count or 0) or int(upload_count or 0):
        raise RuntimeError(
            "Private-account migration requires empty recordings and upload_sessions tables."
        )

    connection.execute(sa.text("delete from auth_sessions"))
    connection.execute(sa.text("delete from login_attempts"))

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("pin_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "char_length(username) between 3 and 32", name="ck_user_username_length"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.add_column("auth_sessions", sa.Column("user_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_auth_sessions_user_id_users",
        "auth_sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.add_column("recordings", sa.Column("user_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_recordings_user_id_users",
        "recordings",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index("ix_recordings_user_id", "recordings", ["user_id"])
    op.drop_index("uq_recordings_one_active", table_name="recordings")
    op.create_index(
        "uq_recordings_one_active_per_user",
        "recordings",
        ["user_id"],
        unique=True,
        postgresql_where=ACTIVE_RECORDING_PREDICATE,
    )


def downgrade() -> None:
    raise RuntimeError("The private-account migration is forward-only.")
