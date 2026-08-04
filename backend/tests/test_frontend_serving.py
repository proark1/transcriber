from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from transcriber.api.app import create_app
from transcriber.config import AppSettings


def test_built_frontend_has_safe_spa_fallback_and_immutable_assets(
    tmp_path: Path,
    app_settings: AppSettings,
    app_session_factory: sessionmaker[Session],
    fake_storage: FakeObjectStorage,
) -> None:
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text("<main>transcriber</main>", encoding="utf-8")
    (assets / "app-abc123.js").write_text("export {};", encoding="utf-8")
    settings = app_settings.model_copy(update={"frontend_dist": frontend})
    app = create_app(settings, app_session_factory, fake_storage)

    with TestClient(app, base_url="https://testserver") as client:
        home = client.get("/")
        nested = client.get("/recordings/saved-item")
        asset = client.get("/assets/app-abc123.js")
        missing_api = client.get("/api/not-a-route")

    assert home.text == "<main>transcriber</main>"
    assert nested.text == home.text
    assert home.headers["cache-control"] == "no-store"
    assert asset.text == "export {};"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")
