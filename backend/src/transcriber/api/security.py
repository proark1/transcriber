"""Request-boundary protections and safe error responses."""

from __future__ import annotations

import logging
import secrets
from typing import cast
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from transcriber.auth import CSRF_HEADER_NAME
from transcriber.config import AppSettings

logger = logging.getLogger("transcriber.http")


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "requestId": getattr(request.state, "request_id", "unknown"),
            }
        },
        headers=headers,
    )


async def http_exception_handler(request: Request, error: HTTPException) -> JSONResponse:
    messages = {
        status.HTTP_400_BAD_REQUEST: "The request could not be accepted.",
        status.HTTP_401_UNAUTHORIZED: "Authentication required.",
        status.HTTP_403_FORBIDDEN: "The request was not permitted.",
        status.HTTP_404_NOT_FOUND: "The requested item was not found.",
        status.HTTP_409_CONFLICT: "The request conflicts with the current state.",
        status.HTTP_429_TOO_MANY_REQUESTS: "Too many attempts. Please try again later.",
    }
    return error_response(
        request,
        status_code=error.status_code,
        code=_error_code(error.status_code),
        message=messages.get(error.status_code, "The request could not be completed."),
        headers=cast(dict[str, str] | None, error.headers),
    )


async def validation_exception_handler(
    request: Request, _error: RequestValidationError
) -> JSONResponse:
    return error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="invalid_request",
        message="Please check the submitted information.",
    )


async def unhandled_exception_handler(request: Request, error: Exception) -> JSONResponse:
    logger.error(
        "Unhandled request error",
        extra={
            "request_id": request.state.request_id,
            "exception_type": type(error).__name__,
        },
    )
    return error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="Something went wrong. Please try again.",
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, settings: AppSettings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings
        self._bucket_origin = _origin(settings.bucket_endpoint)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = secrets.token_hex(16)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            f"form-action 'self'; object-src 'none'; connect-src 'self' {self._bucket_origin}; "
            f"media-src 'self' {self._bucket_origin} blob:"
        )
        if self._settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def require_same_origin(request: Request, settings: AppSettings) -> None:
    expected = _origin(settings.app_public_origin)
    supplied = request.headers.get("origin")
    if supplied is None:
        referer = request.headers.get("referer")
        supplied = _origin(referer) if referer else None
    if supplied != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def require_csrf(request: Request, settings: AppSettings, expected_hmac: str) -> None:
    require_same_origin(request, settings)
    csrf_token = request.headers.get(CSRF_HEADER_NAME, "")
    service = request.app.state.auth_service
    if not csrf_token or not secrets.compare_digest(expected_hmac, service.digest(csrf_token)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        429: "rate_limited",
    }.get(status_code, "request_failed")
