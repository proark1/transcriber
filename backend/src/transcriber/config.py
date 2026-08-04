"""Validated application settings shared by the web and worker services."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from argon2.low_level import Type
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_RECORDING_BYTES = 5_000_000_000
MAX_RECORDING_SECONDS = 4 * 60 * 60
UPLOAD_PART_BYTES = 32 * 1024 * 1024
CHUNK_CORE_SECONDS = 20 * 60
CHUNK_BOUNDARY_SEARCH_SECONDS = 30
CHUNK_OVERLAP_SECONDS = 5
SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
UPLOAD_SESSION_SECONDS = 24 * 60 * 60
PRESIGNED_URL_SECONDS = 15 * 60
WORKER_LEASE_SECONDS = 5 * 60
WORKER_HEARTBEAT_SECONDS = 30
WORKER_POLL_SECONDS = 5
PLAYBACK_URL_SECONDS = 5 * 60


class AppSettings(BaseSettings):
    """Fail-closed settings loaded from Railway variables or a local `.env`."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "production"
    app_public_origin: str
    app_username: str
    app_pin_hash: str
    app_session_secret: SecretStr
    app_secure_cookies: bool = True
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    session_lifetime_seconds: int = SESSION_LIFETIME_SECONDS
    login_attempt_window_seconds: int = LOGIN_ATTEMPT_WINDOW_SECONDS
    login_lockout_seconds: int = LOGIN_LOCKOUT_SECONDS
    login_max_failures: int = LOGIN_MAX_FAILURES
    upload_session_seconds: int = UPLOAD_SESSION_SECONDS
    presigned_url_seconds: int = PRESIGNED_URL_SECONDS
    worker_lease_seconds: int = WORKER_LEASE_SECONDS
    worker_heartbeat_seconds: int = WORKER_HEARTBEAT_SECONDS
    worker_poll_seconds: int = WORKER_POLL_SECONDS
    playback_url_seconds: int = PLAYBACK_URL_SECONDS

    database_url: str

    bucket_endpoint: str
    bucket_name: str
    bucket_access_key_id: str
    bucket_secret_access_key: SecretStr
    bucket_region: str = "auto"
    bucket_url_style: Literal["path", "virtual"] = "virtual"

    max_recording_bytes: int = MAX_RECORDING_BYTES
    max_recording_seconds: int = MAX_RECORDING_SECONDS
    upload_part_bytes: int = UPLOAD_PART_BYTES
    chunk_core_seconds: int = CHUNK_CORE_SECONDS
    chunk_boundary_search_seconds: int = CHUNK_BOUNDARY_SEARCH_SECONDS
    chunk_overlap_seconds: int = CHUNK_OVERLAP_SECONDS

    whisper_model: str = "large-v3"
    whisper_device: Literal["cpu"] = "cpu"
    whisper_compute_type: Literal["int8"] = "int8"
    whisper_model_cache: Path = Path(".models")
    worker_scratch_dir: Path = Path(".scratch")
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @field_validator(
        "app_public_origin",
        "app_username",
        "app_pin_hash",
        "database_url",
        "bucket_endpoint",
        "bucket_name",
        "bucket_access_key_id",
        "bucket_region",
        "whisper_model",
        "ffmpeg_path",
        "ffprobe_path",
    )
    @classmethod
    def require_nonempty_string(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("must not be empty")
        return result

    @field_validator("app_public_origin", "bucket_endpoint")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @field_validator("bucket_secret_access_key")
    @classmethod
    def require_bucket_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("BUCKET_SECRET_ACCESS_KEY must not be empty")
        return value

    @model_validator(mode="after")
    def enforce_product_and_security_contract(self) -> Self:
        try:
            parameters = extract_parameters(self.app_pin_hash)
        except InvalidHashError as error:
            raise ValueError("APP_PIN_HASH must be a valid Argon2 hash") from error
        if parameters.type is not Type.ID:
            raise ValueError("APP_PIN_HASH must use Argon2id")
        if len(self.app_username) > 128:
            raise ValueError("APP_USERNAME must contain at most 128 characters")
        if len(self.app_session_secret.get_secret_value()) < 32:
            raise ValueError("APP_SESSION_SECRET must contain at least 32 characters")
        if self.max_recording_bytes != MAX_RECORDING_BYTES:
            raise ValueError(f"MAX_RECORDING_BYTES must equal {MAX_RECORDING_BYTES}")
        if self.max_recording_seconds != MAX_RECORDING_SECONDS:
            raise ValueError(f"MAX_RECORDING_SECONDS must equal {MAX_RECORDING_SECONDS}")
        if self.upload_part_bytes != UPLOAD_PART_BYTES:
            raise ValueError(f"UPLOAD_PART_BYTES must equal {UPLOAD_PART_BYTES}")
        if self.chunk_core_seconds != CHUNK_CORE_SECONDS:
            raise ValueError(f"CHUNK_CORE_SECONDS must equal {CHUNK_CORE_SECONDS}")
        if self.chunk_boundary_search_seconds != CHUNK_BOUNDARY_SEARCH_SECONDS:
            raise ValueError(
                f"CHUNK_BOUNDARY_SEARCH_SECONDS must equal {CHUNK_BOUNDARY_SEARCH_SECONDS}"
            )
        if self.chunk_overlap_seconds != CHUNK_OVERLAP_SECONDS:
            raise ValueError(f"CHUNK_OVERLAP_SECONDS must equal {CHUNK_OVERLAP_SECONDS}")
        if self.session_lifetime_seconds != SESSION_LIFETIME_SECONDS:
            raise ValueError(f"SESSION_LIFETIME_SECONDS must equal {SESSION_LIFETIME_SECONDS}")
        if self.login_attempt_window_seconds != LOGIN_ATTEMPT_WINDOW_SECONDS:
            raise ValueError(
                f"LOGIN_ATTEMPT_WINDOW_SECONDS must equal {LOGIN_ATTEMPT_WINDOW_SECONDS}"
            )
        if self.login_lockout_seconds != LOGIN_LOCKOUT_SECONDS:
            raise ValueError(f"LOGIN_LOCKOUT_SECONDS must equal {LOGIN_LOCKOUT_SECONDS}")
        if self.login_max_failures != LOGIN_MAX_FAILURES:
            raise ValueError(f"LOGIN_MAX_FAILURES must equal {LOGIN_MAX_FAILURES}")
        if self.upload_session_seconds != UPLOAD_SESSION_SECONDS:
            raise ValueError(f"UPLOAD_SESSION_SECONDS must equal {UPLOAD_SESSION_SECONDS}")
        if self.presigned_url_seconds != PRESIGNED_URL_SECONDS:
            raise ValueError(f"PRESIGNED_URL_SECONDS must equal {PRESIGNED_URL_SECONDS}")
        if self.worker_lease_seconds != WORKER_LEASE_SECONDS:
            raise ValueError(f"WORKER_LEASE_SECONDS must equal {WORKER_LEASE_SECONDS}")
        if self.worker_heartbeat_seconds != WORKER_HEARTBEAT_SECONDS:
            raise ValueError(f"WORKER_HEARTBEAT_SECONDS must equal {WORKER_HEARTBEAT_SECONDS}")
        if self.worker_poll_seconds != WORKER_POLL_SECONDS:
            raise ValueError(f"WORKER_POLL_SECONDS must equal {WORKER_POLL_SECONDS}")
        if self.playback_url_seconds != PLAYBACK_URL_SECONDS:
            raise ValueError(f"PLAYBACK_URL_SECONDS must equal {PLAYBACK_URL_SECONDS}")
        if self.app_env == "production":
            if urlparse(self.app_public_origin).scheme != "https":
                raise ValueError("APP_PUBLIC_ORIGIN must use HTTPS in production")
            if urlparse(self.bucket_endpoint).scheme != "https":
                raise ValueError("BUCKET_ENDPOINT must use HTTPS in production")
            if not self.app_secure_cookies:
                raise ValueError("APP_SECURE_COOKIES must be enabled in production")
        return self
