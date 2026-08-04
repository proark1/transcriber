"""Login, session inspection, and logout routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from transcriber.api.dependencies import (
    Authenticated,
    AuthService,
    Database,
    MutationAuthenticated,
    Settings,
)
from transcriber.auth import SESSION_COOKIE_NAME, InvalidCredentials, LoginLocked

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    pin: str


class SessionResponse(BaseModel):
    authenticated: bool = True
    username: str
    csrfToken: str | None = None
    expiresAt: datetime


@router.post("/login", response_model=SessionResponse)
def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    database: Database,
    settings: Settings,
    service: AuthService,
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    client_key = request.client.host if request.client is not None else "unknown"
    try:
        issued = service.authenticate(
            database,
            username=credentials.username,
            pin=credentials.pin,
            client_key=client_key,
        )
    except LoginLocked as error:
        database.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    except InvalidCredentials as error:
        database.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error

    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        max_age=settings.session_lifetime_seconds,
        secure=settings.app_secure_cookies,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return SessionResponse(
        username=settings.app_username,
        csrfToken=issued.csrf_token,
        expiresAt=issued.expires_at,
    )


@router.get("/session", response_model=SessionResponse)
def session(response: Response, auth: Authenticated, settings: Settings) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        username=settings.app_username,
        csrfToken=auth.service.rotate_csrf(auth.database, auth.auth_session),
        expiresAt=auth.auth_session.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: MutationAuthenticated,
    settings: Settings,
) -> None:
    response.headers["Cache-Control"] = "no-store"
    auth.service.revoke(auth.database, auth.auth_session)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=settings.app_secure_cookies,
        httponly=True,
        samesite="lax",
        path="/",
    )
