from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transcriber.models import Language, LoginAttempt, Recording, RecordingStatus, User


def cleanup_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "cleanup_smoke_user.py"
    spec = spec_from_file_location("cleanup_smoke_user_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the smoke cleanup script.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke_user(database: Session, username: str = "railway-smoke-12345678") -> User:
    user = User(username=username, pin_hash="test-only-pin-hash")
    database.add(user)
    database.flush()
    return user


def test_cleanup_accepts_only_generated_smoke_usernames(database_session: Session) -> None:
    cleanup = cleanup_module().cleanup_smoke_user

    with pytest.raises(ValueError, match="only a generated"):
        cleanup(database_session, "ordinary-user")


def test_cleanup_refuses_other_users_or_remaining_recordings(database_session: Session) -> None:
    cleanup = cleanup_module().cleanup_smoke_user
    target = smoke_user(database_session)
    database_session.add(User(username="real-user", pin_hash="test-only-pin-hash"))
    database_session.flush()

    with pytest.raises(RuntimeError, match="another account"):
        cleanup(database_session, target.username)

    database_session.rollback()
    target = smoke_user(database_session)
    recording_id = uuid4()
    database_session.add(
        Recording(
            id=recording_id,
            user_id=target.id,
            display_filename="smoke.m4a",
            reported_content_type="audio/mp4",
            expected_bytes=1,
            language=Language.ENGLISH,
            original_object_key=f"recordings/{recording_id}/original/source",
            status=RecordingStatus.COMPLETED,
        )
    )
    database_session.flush()

    with pytest.raises(RuntimeError, match="Delete every smoke recording"):
        cleanup(database_session, target.username)


def test_cleanup_removes_the_only_user_and_login_attempts(database_session: Session) -> None:
    cleanup = cleanup_module().cleanup_smoke_user
    target = smoke_user(database_session)
    database_session.add(
        LoginAttempt(
            security_key_hmac="a" * 64,
            failure_count=1,
            window_started_at=target.created_at,
            updated_at=target.created_at,
        )
    )
    database_session.flush()

    removed_id = cleanup(database_session, target.username)

    assert removed_id == target.id
    assert database_session.scalar(select(func.count()).select_from(User)) == 0
    assert database_session.scalar(select(func.count()).select_from(LoginAttempt)) == 0
