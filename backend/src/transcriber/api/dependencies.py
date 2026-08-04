"""FastAPI dependency wiring for settings, database sessions, and authentication."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from transcriber.api.security import require_csrf
from transcriber.auth import SESSION_COOKIE_NAME, AuthenticationService
from transcriber.config import AppSettings
from transcriber.models import AuthSession


@dataclass(frozen=True)
class RequestAuth:
    database: Session
    auth_session: AuthSession
    service: AuthenticationService


def get_settings(request: Request) -> AppSettings:
    return cast(AppSettings, request.app.state.settings)


def get_auth_service(request: Request) -> AuthenticationService:
    return cast(AuthenticationService, request.app.state.auth_service)


def get_database(request: Request) -> Iterator[Session]:
    factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with factory() as database:
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise


Database = Annotated[Session, Depends(get_database)]
Settings = Annotated[AppSettings, Depends(get_settings)]
AuthService = Annotated[AuthenticationService, Depends(get_auth_service)]


def require_authentication(
    database: Database,
    service: AuthService,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> RequestAuth:
    auth_session = service.resolve_session(database, session_token or "")
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return RequestAuth(database=database, auth_session=auth_session, service=service)


Authenticated = Annotated[RequestAuth, Depends(require_authentication)]


def require_mutation_authentication(
    request: Request,
    auth: Authenticated,
    settings: Settings,
) -> RequestAuth:
    require_csrf(request, settings, auth.auth_session.csrf_hmac)
    return auth


MutationAuthenticated = Annotated[RequestAuth, Depends(require_mutation_authentication)]
