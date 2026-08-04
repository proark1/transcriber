from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.fakes import FakeObjectStorage
from tests.test_recording_routes import history_recording
from tests.test_upload_routes import authenticated_headers
from transcriber.models import RecordingStatus
from transcriber.storage import ObjectMetadata


def test_playback_returns_only_a_short_lived_derived_audio_url(
    api_client: TestClient,
    database_session: Session,
    fake_storage: FakeObjectStorage,
) -> None:
    recording = history_recording(database_session)
    assert recording.playback_object_key is not None
    fake_storage.objects[recording.playback_object_key] = ObjectMetadata(1_024, "audio/mp4")
    headers = authenticated_headers(api_client)
    del headers

    response = api_client.get(f"/api/recordings/{recording.id}/playback")

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://bucket.test/")
    assert "expires=300" in response.json()["url"]
    assert recording.original_object_key not in response.json()["url"]
    assert response.headers["cache-control"] == "no-store"


def test_playback_requires_a_verified_object_and_non_deleting_state(
    api_client: TestClient,
    database_session: Session,
    fake_storage: FakeObjectStorage,
) -> None:
    missing = history_recording(database_session)
    authenticated_headers(api_client)
    assert api_client.get(f"/api/recordings/{missing.id}/playback").status_code == 409

    missing.status = RecordingStatus.DELETING
    database_session.commit()
    assert api_client.get(f"/api/recordings/{missing.id}/playback").status_code == 409


def test_playback_requires_authentication(
    api_client: TestClient, database_session: Session, fake_storage: FakeObjectStorage
) -> None:
    recording = history_recording(database_session)
    assert recording.playback_object_key is not None
    fake_storage.objects[recording.playback_object_key] = ObjectMetadata(100)

    assert api_client.get(f"/api/recordings/{recording.id}/playback").status_code == 401
