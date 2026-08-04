from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError

from transcriber.config import (
    LOGIN_MAX_FAILURES,
    MAX_RECORDING_BYTES,
    SESSION_LIFETIME_SECONDS,
    AppSettings,
)


def valid_settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "app_env": "test",
        "app_public_origin": "http://localhost:5173",
        "app_username": "owner",
        "app_pin_hash": PasswordHasher().hash("123456"),
        "app_session_secret": "s" * 32,
        "app_secure_cookies": False,
        "database_url": "postgresql+psycopg://localhost/transcriber",
        "bucket_endpoint": "http://localhost:9000",
        "bucket_name": "transcriber",
        "bucket_access_key_id": "access",
        "bucket_secret_access_key": "secret",
    }
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[arg-type]


def test_settings_accept_the_approved_contract() -> None:
    settings = valid_settings()

    assert settings.max_recording_bytes == MAX_RECORDING_BYTES
    assert settings.session_lifetime_seconds == SESSION_LIFETIME_SECONDS
    assert settings.login_max_failures == LOGIN_MAX_FAILURES
    assert settings.whisper_model == "large-v3"
    assert settings.whisper_compute_type == "int8"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_recording_bytes", MAX_RECORDING_BYTES - 1),
        ("max_recording_seconds", 14_399),
        ("upload_part_bytes", 1_024),
        ("chunk_core_seconds", 600),
        ("chunk_overlap_seconds", 0),
        ("session_lifetime_seconds", 60),
        ("login_max_failures", 10),
        ("upload_session_seconds", 60),
        ("presigned_url_seconds", 60),
        ("worker_lease_seconds", 60),
        ("worker_heartbeat_seconds", 60),
        ("worker_poll_seconds", 60),
        ("playback_url_seconds", 60),
    ],
)
def test_settings_reject_contract_drift(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        valid_settings(**{field: value})


def test_production_requires_https_and_secure_cookies() -> None:
    with pytest.raises(ValidationError):
        valid_settings(app_env="production", app_secure_cookies=False)

    with pytest.raises(ValidationError):
        valid_settings(
            app_env="production",
            app_public_origin="http://transcriber.example",
            app_secure_cookies=True,
        )


def test_settings_reject_an_invalid_pin_hash() -> None:
    with pytest.raises(ValidationError):
        valid_settings(app_pin_hash="not-a-hash")


def test_settings_reject_an_empty_bucket_secret() -> None:
    with pytest.raises(ValidationError):
        valid_settings(bucket_secret_access_key="")
