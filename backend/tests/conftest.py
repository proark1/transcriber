from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://transcriber:transcriber_dev@localhost:5432/transcriber_test",
)


def _ensure_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    database = url.database
    if database is None or re.fullmatch(r"[a-zA-Z0-9_]+", database) is None:
        raise RuntimeError("TEST_DATABASE_URL must name a simple PostgreSQL database.")
    admin_url = url.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.scalar(
                text("select 1 from pg_database where datname = :database"),
                {"database": database},
            )
            if exists is None:
                connection.execute(text(f'create database "{database}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    _ensure_test_database()
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("drop schema if exists public cascade"))
        connection.execute(text("create schema public"))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def database_session(migrated_engine: Engine) -> Iterator[Session]:
    with migrated_engine.begin() as connection:
        for table in (
            "transcription_chunks",
            "upload_parts",
            "upload_sessions",
            "recordings",
            "login_attempts",
            "auth_sessions",
        ):
            connection.execute(text(f"delete from {table}"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
