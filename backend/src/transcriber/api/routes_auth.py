"""Login, session inspection, and logout routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict

from transcriber.api.dependencies import (
    Authenticated,
    AuthService,
    Database,
    MutationAuthenticated,
    Settings,
)
from transcriber.api.security import ApiProblem
from transcriber.auth import (
    SESSION_COOKIE_NAME,
    IncorrectPin,
    InvalidPin,
    InvalidUsername,
    LoginLocked,
    UsernameUnavailable,
)

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
    accountCreated: bool = False


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
        raise ApiProblem(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message="Too many attempts. Please try again later.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    except IncorrectPin as error:
        database.commit()
        raise ApiProblem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="incorrect_pin",
            message="That PIN is incorrect for this username.",
        ) from error
    except InvalidUsername as error:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_username",
            message="Use 3–32 letters or numbers. You may also use ., _ or -.",
        ) from error
    except UsernameUnavailable as error:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="username_unavailable",
            message="That username is unavailable.",
        ) from error
    except InvalidPin as error:
        raise ApiProblem(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_pin",
            message="Use a 6–12 digit PIN.",
        ) from error

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
        username=issued.user.username,
        csrfToken=issued.csrf_token,
        expiresAt=issued.expires_at,
        accountCreated=issued.account_created,
    )


@router.get("/session", response_model=SessionResponse)
def session(response: Response, auth: Authenticated) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    return SessionResponse(
        username=auth.user.username,
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
