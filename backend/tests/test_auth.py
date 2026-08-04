from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from transcriber.auth import AuthenticationService, InvalidCredentials, LoginLocked
from transcriber.config import AppSettings
from transcriber.models import AuthSession


def test_authentication_stores_only_hmacs(
    database_session: Session, app_settings: AppSettings
) -> None:
    service = AuthenticationService(app_settings)

    issued = service.authenticate(
        database_session,
        username="owner",
        pin="123456",
        client_key="127.0.0.1",
    )
    stored = database_session.scalar(select(AuthSession))

    assert stored is not None
    assert stored.token_hmac == service.digest(issued.token)
    assert issued.token not in stored.token_hmac
    assert stored.csrf_hmac == service.digest(issued.csrf_token)
    assert service.resolve_session(database_session, issued.token) is stored


@pytest.mark.parametrize(
    ("username", "pin"),
    [("wrong", "123456"), ("owner", "000000"), ("owner", "12345"), ("owner", "abcdef")],
)
def test_invalid_credentials_are_rejected(
    database_session: Session,
    app_settings: AppSettings,
    username: str,
    pin: str,
) -> None:
    service = AuthenticationService(app_settings)

    with pytest.raises(InvalidCredentials):
        service.authenticate(
            database_session,
            username=username,
            pin=pin,
            client_key=f"client-{username}-{pin}",
        )


def test_fifth_failure_locks_login_for_fifteen_minutes(
    database_session: Session, app_settings: AppSettings
) -> None:
    service = AuthenticationService(app_settings)
    now = datetime(2026, 8, 4, tzinfo=UTC)

    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            service.authenticate(
                database_session,
                username="owner",
                pin="000000",
                client_key="same-client",
                now=now,
            )

    with pytest.raises(LoginLocked) as locked:
        service.authenticate(
            database_session,
            username="owner",
            pin="000000",
            client_key="same-client",
            now=now,
        )
    assert locked.value.retry_after_seconds == 900

    with pytest.raises(LoginLocked):
        service.authenticate(
            database_session,
            username="owner",
            pin="123456",
            client_key="same-client",
            now=now + timedelta(minutes=14),
        )


def test_expired_and_rotated_sessions_are_rejected(
    database_session: Session, app_settings: AppSettings
) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    service = AuthenticationService(app_settings)
    issued = service.authenticate(
        database_session,
        username="owner",
        pin="123456",
        client_key="client",
        now=now,
    )

    assert (
        service.resolve_session(database_session, issued.token, now=now + timedelta(days=7)) is None
    )

    stored = database_session.scalar(select(AuthSession))
    assert stored is not None
    stored.credential_version = "rotated"
    database_session.flush()
    assert service.resolve_session(database_session, issued.token, now=now) is None
