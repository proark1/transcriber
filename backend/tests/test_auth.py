from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from transcriber.auth import (
    AuthenticationService,
    IncorrectPin,
    InvalidPin,
    InvalidUsername,
    LoginLocked,
    UsernameUnavailable,
    normalize_username,
)
from transcriber.config import AppSettings
from transcriber.models import AuthSession, User


def service(settings: AppSettings, hasher: PasswordHasher) -> AuthenticationService:
    return AuthenticationService(settings, password_hasher=hasher)


def test_registration_normalizes_username_and_stores_only_hmacs(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
) -> None:
    auth = service(app_settings, test_password_hasher)

    issued = auth.authenticate(
        database_session,
        username="  AsSaD  ",
        pin="123456",
        client_key="127.0.0.1",
    )
    stored = database_session.scalar(select(AuthSession))

    assert issued.account_created is True
    assert issued.user.username == "assad"
    assert stored is not None
    assert stored.user_id == issued.user.id
    assert stored.token_hmac == auth.digest(issued.token)
    assert issued.token not in stored.token_hmac
    assert stored.csrf_hmac == auth.digest(issued.csrf_token)
    assert auth.resolve_session(database_session, issued.token) is stored


def test_existing_username_requires_its_pin_case_insensitively(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
) -> None:
    auth = service(app_settings, test_password_hasher)
    auth.authenticate(
        database_session, username="Assad", pin="123456", client_key="register"
    )

    existing = auth.authenticate(
        database_session, username="ASSAD", pin="123456", client_key="login"
    )
    assert existing.account_created is False
    assert existing.user.username == "assad"

    with pytest.raises(IncorrectPin):
        auth.authenticate(
            database_session, username="assad", pin="000000", client_key="wrong"
        )


@pytest.mark.parametrize("username", ["ab", "has space", "bad/character", "a" * 33])
def test_invalid_usernames_create_no_rows(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
    username: str,
) -> None:
    auth = service(app_settings, test_password_hasher)

    with pytest.raises(InvalidUsername):
        auth.authenticate(
            database_session, username=username, pin="123456", client_key="invalid"
        )
    assert database_session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("pin", ["12345", "1234567890123", "abcdef", "１２３４５６"])
def test_invalid_pins_create_no_rows(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
    pin: str,
) -> None:
    auth = service(app_settings, test_password_hasher)

    with pytest.raises(InvalidPin):
        auth.authenticate(database_session, username="new-user", pin=pin, client_key="invalid")
    assert database_session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("username", ["owner", "Owner", "OWNER"])
def test_legacy_owner_is_permanently_unavailable(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
    username: str,
) -> None:
    auth = service(app_settings, test_password_hasher)

    with pytest.raises(UsernameUnavailable):
        auth.authenticate(database_session, username=username, pin="123456", client_key="legacy")
    assert database_session.scalar(select(func.count()).select_from(User)) == 0


def test_fifth_wrong_pin_locks_login_for_fifteen_minutes(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
) -> None:
    auth = service(app_settings, test_password_hasher)
    now = datetime(2026, 8, 5, tzinfo=UTC)
    auth.authenticate(
        database_session,
        username="locked-user",
        pin="123456",
        client_key="register",
        now=now,
    )

    for _ in range(4):
        with pytest.raises(IncorrectPin):
            auth.authenticate(
                database_session,
                username="locked-user",
                pin="000000",
                client_key="same-client",
                now=now,
            )

    with pytest.raises(LoginLocked) as locked:
        auth.authenticate(
            database_session,
            username="locked-user",
            pin="000000",
            client_key="same-client",
            now=now,
        )
    assert locked.value.retry_after_seconds == 900

    with pytest.raises(LoginLocked):
        auth.authenticate(
            database_session,
            username="locked-user",
            pin="123456",
            client_key="same-client",
            now=now + timedelta(minutes=14),
        )


def test_expired_and_user_credential_rotated_sessions_are_rejected(
    database_session: Session,
    app_settings: AppSettings,
    test_password_hasher: PasswordHasher,
) -> None:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    auth = service(app_settings, test_password_hasher)
    issued = auth.authenticate(
        database_session,
        username="session-user",
        pin="123456",
        client_key="client",
        now=now,
    )

    assert auth.resolve_session(database_session, issued.token, now=now + timedelta(days=7)) is None

    issued.user.pin_hash = test_password_hasher.hash("654321")
    database_session.flush()
    assert auth.resolve_session(database_session, issued.token, now=now) is None


def test_concurrent_registration_creates_one_user(
    database_session: Session,
    app_session_factory: sessionmaker[Session],
    app_settings: AppSettings,
) -> None:
    del database_session
    barrier = Barrier(2)

    def register(client_key: str) -> bool:
        auth = AuthenticationService(
            app_settings, password_hasher=PasswordHasher(time_cost=1, memory_cost=8_192)
        )
        with app_session_factory() as database:
            barrier.wait()
            issued = auth.authenticate(
                database,
                username="Race-User",
                pin="123456",
                client_key=client_key,
            )
            database.commit()
            return issued.account_created

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(register, ["one", "two"]))

    with app_session_factory() as database:
        users = list(database.scalars(select(User).where(User.username == "race-user")))
    assert sorted(created) == [False, True]
    assert len(users) == 1


def test_normalize_username_supports_approved_unicode_casefolding() -> None:
    assert normalize_username("  STRAẞE  ") == "strasse"
