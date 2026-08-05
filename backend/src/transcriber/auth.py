"""Self-registering user authentication with durable sessions and bounded attempts."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from transcriber.config import AppSettings
from transcriber.models import AuthSession, LoginAttempt, User

PIN_PATTERN = re.compile(r"^[0-9]{6,12}$")
USERNAME_EXTRA_CHARACTERS = frozenset("._-")
RESERVED_USERNAMES = frozenset({"owner"})
SESSION_COOKIE_NAME = "transcriber_session"
CSRF_HEADER_NAME = "X-CSRF-Token"


class InvalidUsername(RuntimeError):
    """The submitted username does not match the public account contract."""


class UsernameUnavailable(RuntimeError):
    """The normalized username is permanently reserved."""


class InvalidPin(RuntimeError):
    """The submitted PIN does not contain 6–12 ASCII digits."""


class IncorrectPin(RuntimeError):
    """An existing username was submitted with the wrong PIN."""


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
    user: User
    account_created: bool


def normalize_username(value: str) -> str:
    """Return the canonical account identity or reject unsupported characters."""
    normalized = unicodedata.normalize("NFKC", value.strip()).casefold()
    if not 3 <= len(normalized) <= 32:
        raise InvalidUsername("Username length is outside the supported range.")
    if any(
        unicodedata.category(character)[0] not in {"L", "N"}
        and character not in USERNAME_EXTRA_CHARACTERS
        for character in normalized
    ):
        raise InvalidUsername("Username contains unsupported characters.")
    return normalized


class AuthenticationService:
    """Create or authenticate private users without persisting bearer secrets."""

    def __init__(
        self, settings: AppSettings, *, password_hasher: PasswordHasher | None = None
    ) -> None:
        self._settings = settings
        self._secret = settings.app_session_secret.get_secret_value().encode()
        self._password_hasher = password_hasher or PasswordHasher()

    def authenticate(
        self,
        database: Session,
        *,
        username: str,
        pin: str,
        client_key: str,
        now: datetime | None = None,
    ) -> IssuedSession:
        normalized_username = normalize_username(username)
        if PIN_PATTERN.fullmatch(pin) is None:
            raise InvalidPin("PIN must contain 6–12 ASCII digits.")
        if normalized_username in RESERVED_USERNAMES:
            raise UsernameUnavailable("Username is permanently reserved.")

        current_time = now or datetime.now(UTC)
        security_key_hmac = self.security_key_hmac(normalized_username, client_key)
        attempt = self._lock_login_attempt(database, security_key_hmac, current_time)
        if attempt.locked_until is not None and attempt.locked_until > current_time:
            retry_after = max(1, int((attempt.locked_until - current_time).total_seconds()))
            raise LoginLocked(retry_after)

        user = database.scalar(
            select(User).where(User.username == normalized_username).with_for_update()
        )
        account_created = False
        if user is None:
            user, account_created = self._create_user_or_resolve_race(
                database,
                username=normalized_username,
                pin=pin,
            )

        if not account_created and not self._pin_matches(user.pin_hash, pin):
            self._record_failure(attempt, current_time)
            database.flush()
            if attempt.locked_until is not None and attempt.locked_until > current_time:
                raise LoginLocked(self._settings.login_lockout_seconds)
            raise IncorrectPin("The submitted PIN does not match the existing account.")

        database.delete(attempt)
        return self._issue_session(
            database,
            user=user,
            security_key_hmac=security_key_hmac,
            account_created=account_created,
            now=current_time,
        )

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
            select(AuthSession)
            .options(joinedload(AuthSession.user))
            .where(AuthSession.token_hmac == self.digest(token))
        )
        if auth_session is None:
            return None
        if (
            auth_session.revoked_at is not None
            or auth_session.expires_at <= current_time
            or not hmac.compare_digest(
                auth_session.credential_version,
                self.credential_version(auth_session.user),
            )
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

    def credential_version(self, user: User) -> str:
        return self._digest_parts("credential", user.pin_hash)

    def security_key_hmac(self, username: str, client_key: str) -> str:
        return self._digest_parts("login", username, client_key)

    def digest(self, value: str) -> str:
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()

    def _digest_parts(self, *parts: str) -> str:
        framed = b"".join(len(part.encode()).to_bytes(4, "big") + part.encode() for part in parts)
        return hmac.new(self._secret, framed, hashlib.sha256).hexdigest()

    def _pin_matches(self, pin_hash: str, pin: str) -> bool:
        try:
            return bool(self._password_hasher.verify(pin_hash, pin))
        except (InvalidHashError, VerifyMismatchError):
            return False

    def _create_user_or_resolve_race(
        self,
        database: Session,
        *,
        username: str,
        pin: str,
    ) -> tuple[User, bool]:
        pin_hash = self._password_hasher.hash(pin)
        try:
            with database.begin_nested():
                user = User(username=username, pin_hash=pin_hash)
                database.add(user)
                database.flush()
            return user, True
        except IntegrityError as error:
            if _constraint_name(error) not in {"users_username_key", "uq_users_username"}:
                raise
        resolved_user = database.scalar(
            select(User).where(User.username == username).with_for_update()
        )
        if resolved_user is None:
            raise RuntimeError("Concurrent account creation did not persist a user.")
        return resolved_user, False

    def _issue_session(
        self,
        database: Session,
        *,
        user: User,
        security_key_hmac: str,
        account_created: bool,
        now: datetime,
    ) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=self._settings.session_lifetime_seconds)
        database.add(
            AuthSession(
                user=user,
                token_hmac=self.digest(token),
                csrf_hmac=self.digest(csrf_token),
                credential_version=self.credential_version(user),
                security_key_hmac=security_key_hmac,
                created_at=now,
                last_used_at=now,
                expires_at=expires_at,
            )
        )
        database.flush()
        return IssuedSession(
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            user=user,
            account_created=account_created,
        )

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


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
