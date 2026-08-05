"""Remove the one temporary Railway smoke account after its recording cleanup."""

from __future__ import annotations

import argparse
import os
import re
from uuid import UUID

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from transcriber.config import normalize_database_url
from transcriber.models import LoginAttempt, Recording, User

SMOKE_USERNAME = re.compile(r"^railway-smoke-[a-z0-9]{8,16}$")


def cleanup_smoke_user(database: Session, username: str) -> UUID | None:
    normalized = username.strip().lower()
    if SMOKE_USERNAME.fullmatch(normalized) is None:
        raise ValueError("Cleanup accepts only a generated railway-smoke account.")

    user = database.scalar(select(User).where(User.username == normalized).with_for_update())
    other_users = database.scalar(
        select(func.count()).select_from(User).where(User.username != normalized)
    )
    if int(other_users or 0):
        raise RuntimeError("Cleanup refuses to run after another account exists.")

    recording_count = database.scalar(select(func.count()).select_from(Recording))
    if int(recording_count or 0):
        raise RuntimeError("Delete every smoke recording before removing the smoke account.")

    database.execute(delete(LoginAttempt))
    if user is None:
        return None
    user_id = user.id
    database.execute(delete(User).where(User.id == user_id))
    database.flush()
    return user_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required.")

    engine = create_engine(
        normalize_database_url(database_url),
        hide_parameters=True,
        pool_pre_ping=True,
    )
    try:
        with Session(engine) as database:
            user_id = cleanup_smoke_user(database, args.username)
            database.commit()
    finally:
        engine.dispose()
    if user_id is None:
        print("Smoke account was already absent.")
    else:
        print(f"Smoke account cleanup complete for user ID {user_id}.")


if __name__ == "__main__":
    main()
