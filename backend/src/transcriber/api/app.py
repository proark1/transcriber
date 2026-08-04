"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from transcriber.api.routes_auth import router as auth_router
from transcriber.api.routes_uploads import router as uploads_router
from transcriber.api.security import (
    SecurityHeadersMiddleware,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from transcriber.auth import AuthenticationService
from transcriber.config import AppSettings
from transcriber.database import create_database_engine, create_session_factory
from transcriber.storage import BotoObjectStorage, ObjectStorage


def create_app(
    settings: AppSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    storage: ObjectStorage | None = None,
) -> FastAPI:
    resolved_settings = settings or AppSettings()  # type: ignore[call-arg]
    if session_factory is None:
        engine = create_database_engine(resolved_settings)
        session_factory = create_session_factory(engine)

    app = FastAPI(title="Transcriber", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = resolved_settings
    app.state.session_factory = session_factory
    app.state.auth_service = AuthenticationService(resolved_settings)
    app.state.storage = storage or BotoObjectStorage(resolved_settings)
    app.add_middleware(SecurityHeadersMiddleware, settings=resolved_settings)
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(auth_router)
    app.include_router(uploads_router)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness() -> dict[str, str]:
        try:
            with session_factory() as database:
                database.execute(text("select 1"))
        except SQLAlchemyError as error:
            raise HTTPException(status_code=503) from error
        return {"status": "ready"}

    return app
