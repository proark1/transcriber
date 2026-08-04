from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from transcriber.api.app import create_app
from transcriber.config import AppSettings

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


@pytest.fixture(scope="session")
def app_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        app_public_origin="https://testserver",
        app_username="owner",
        app_pin_hash=PasswordHasher(time_cost=1, memory_cost=8_192).hash("123456"),
        app_session_secret=SecretStr("test-session-secret-that-is-at-least-32-bytes"),
        app_secure_cookies=True,
        database_url=TEST_DATABASE_URL,
        bucket_endpoint="http://localhost:9000",
        bucket_name="transcriber",
        bucket_access_key_id="access",
        bucket_secret_access_key=SecretStr("secret"),
    )


@pytest.fixture
def app_session_factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, expire_on_commit=False)


@pytest.fixture
def api_client(
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    database_session: Session,
) -> Iterator[TestClient]:
    del database_session
    app = create_app(app_settings, app_session_factory)
    with TestClient(app, base_url="https://testserver") as client:
        yield client
