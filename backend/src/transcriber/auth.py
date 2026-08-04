"""Single-user authentication with durable sessions and bounded login attempts."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from transcriber.config import AppSettings
from transcriber.models import AuthSession, LoginAttempt

PIN_PATTERN = re.compile(r"^[0-9]{6,12}$")
SESSION_COOKIE_NAME = "transcriber_session"
CSRF_HEADER_NAME = "X-CSRF-Token"


class InvalidCredentials(RuntimeError):
    """The supplied username/PIN pair was not accepted."""


class LoginLocked(RuntimeError):
    """The security key has exceeded its permitted login attempts."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many login attempts.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


class AuthenticationService:
    """Authenticate the configured owner without persisting bearer secrets."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._secret = settings.app_session_secret.get_secret_value().encode()
        self._password_hasher = PasswordHasher()

    def authenticate(
        self,
        database: Session,
        *,
        username: str,
        pin: str,
        client_key: str,
        now: datetime | None = None,
    ) -> IssuedSession:
        current_time = now or datetime.now(UTC)
        security_key_hmac = self.security_key_hmac(username, client_key)
        attempt = self._lock_login_attempt(database, security_key_hmac, current_time)
        if attempt.locked_until is not None and attempt.locked_until > current_time:
            retry_after = max(1, int((attempt.locked_until - current_time).total_seconds()))
            raise LoginLocked(retry_after)

        if not self._credentials_match(username, pin):
            self._record_failure(attempt, current_time)
            database.flush()
            if attempt.locked_until is not None and attempt.locked_until > current_time:
                raise LoginLocked(self._settings.login_lockout_seconds)
            raise InvalidCredentials("Invalid username or PIN.")

        database.delete(attempt)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = current_time + timedelta(seconds=self._settings.session_lifetime_seconds)
        database.add(
            AuthSession(
                token_hmac=self.digest(token),
                csrf_hmac=self.digest(csrf_token),
                credential_version=self.credential_version,
                security_key_hmac=security_key_hmac,
                created_at=current_time,
                last_used_at=current_time,
                expires_at=expires_at,
            )
        )
        database.flush()
        return IssuedSession(token=token, csrf_token=csrf_token, expires_at=expires_at)

    def resolve_session(
        self,
        database: Session,
        token: str,
        *,
        now: datetime | None = None,
    ) -> AuthSession | None:
        if not token:
            return None
        current_time = now or datetime.now(UTC)
        auth_session = database.scalar(
            select(AuthSession).where(AuthSession.token_hmac == self.digest(token))
        )
        if auth_session is None:
            return None
        if (
            auth_session.revoked_at is not None
            or auth_session.expires_at <= current_time
            or not hmac.compare_digest(auth_session.credential_version, self.credential_version)
        ):
            return None
        auth_session.last_used_at = current_time
        database.flush()
        return auth_session

    def revoke(
        self,
        database: Session,
        auth_session: AuthSession,
        *,
        now: datetime | None = None,
    ) -> None:
        auth_session.revoked_at = now or datetime.now(UTC)
        database.flush()

    def rotate_csrf(self, database: Session, auth_session: AuthSession) -> str:
        csrf_token = secrets.token_urlsafe(32)
        auth_session.csrf_hmac = self.digest(csrf_token)
        database.flush()
        return csrf_token

    def csrf_matches(self, auth_session: AuthSession, csrf_token: str) -> bool:
        return bool(csrf_token) and hmac.compare_digest(
            auth_session.csrf_hmac, self.digest(csrf_token)
        )

    def delete_expired(self, database: Session, *, now: datetime | None = None) -> int:
        current_time = now or datetime.now(UTC)
        result = database.execute(delete(AuthSession).where(AuthSession.expires_at <= current_time))
        if not isinstance(result, CursorResult):
            return 0
        return int(result.rowcount or 0)

    @property
    def credential_version(self) -> str:
        return self._digest_parts("credential", self._settings.app_pin_hash)

    def security_key_hmac(self, username: str, client_key: str) -> str:
        return self._digest_parts("login", username, client_key)

    def digest(self, value: str) -> str:
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()

    def _digest_parts(self, *parts: str) -> str:
        framed = b"".join(len(part.encode()).to_bytes(4, "big") + part.encode() for part in parts)
        return hmac.new(self._secret, framed, hashlib.sha256).hexdigest()

    def _credentials_match(self, username: str, pin: str) -> bool:
        username_matches = hmac.compare_digest(
            self.digest(username), self.digest(self._settings.app_username)
        )
        pin_has_valid_shape = PIN_PATTERN.fullmatch(pin) is not None
        try:
            pin_matches: bool = self._password_hasher.verify(self._settings.app_pin_hash, pin)
        except (InvalidHashError, VerifyMismatchError):
            pin_matches = False
        return username_matches and pin_has_valid_shape and pin_matches

    def _lock_login_attempt(
        self, database: Session, security_key_hmac: str, now: datetime
    ) -> LoginAttempt:
        database.execute(
            insert(LoginAttempt)
            .values(
                security_key_hmac=security_key_hmac,
                failure_count=0,
                window_started_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[LoginAttempt.security_key_hmac])
        )
        attempt = database.scalar(
            select(LoginAttempt)
            .where(LoginAttempt.security_key_hmac == security_key_hmac)
            .with_for_update()
        )
        if attempt is None:
            raise RuntimeError("Failed to lock login attempt state.")
        return attempt

    def _record_failure(self, attempt: LoginAttempt, now: datetime) -> None:
        window = timedelta(seconds=self._settings.login_attempt_window_seconds)
        if now - attempt.window_started_at >= window:
            attempt.failure_count = 0
            attempt.window_started_at = now
            attempt.locked_until = None
        attempt.failure_count += 1
        attempt.updated_at = now
        if attempt.failure_count >= self._settings.login_max_failures:
            attempt.locked_until = now + timedelta(seconds=self._settings.login_lockout_seconds)
