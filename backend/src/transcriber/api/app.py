"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from transcriber.api.routes_auth import router as auth_router
from transcriber.api.routes_recordings import router as recordings_router
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
from transcriber.logging_config import configure_logging
from transcriber.storage import BotoObjectStorage, ObjectStorage


def create_app(
    settings: AppSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    storage: ObjectStorage | None = None,
) -> FastAPI:
    resolved_settings = settings or AppSettings()  # type: ignore[call-arg]
    configure_logging(resolved_settings.app_log_level)
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
    app.include_router(recordings_router)

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

    _mount_frontend(app, resolved_settings.frontend_dist)

    return app


def _mount_frontend(app: FastAPI, frontend_dist: Path) -> None:
    index = frontend_dist / "index.html"
    if not index.is_file():
        return
    assets = frontend_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{frontend_path:path}", include_in_schema=False)
    def frontend(request: Request, frontend_path: str = "") -> FileResponse:
        del request
        if frontend_path == "api" or frontend_path.startswith("api/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if frontend_path in {"healthz", "readyz"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(index, headers={"Cache-Control": "no-store"})
