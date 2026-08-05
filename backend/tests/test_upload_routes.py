from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.fakes import FakeObjectStorage
from transcriber.config import MAX_RECORDING_BYTES, UPLOAD_PART_BYTES
from transcriber.models import Recording, RecordingStatus, UploadSession, UploadStatus
from transcriber.storage import ObjectMetadata


def authenticated_headers(
    client: TestClient, *, username: str = "assad", pin: str = "123456"
) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert response.status_code == 200
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": response.json()["csrfToken"],
    }


def upload_payload(
    *,
    request_id: UUID | None = None,
    filename: str = "Voice Memo.m4a",
    content_type: str = "audio/mp4",
    size_bytes: int = 40 * 1024 * 1024,
    language: str = "de",
) -> dict[str, Any]:
    return {
        "clientRequestId": str(request_id or uuid4()),
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "language": language,
    }


def create_upload(
    client: TestClient,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post("/api/uploads", json=payload or upload_payload(), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_is_idempotent_and_allows_only_one_active_recording(
    api_client: TestClient, fake_storage: FakeObjectStorage
) -> None:
    headers = authenticated_headers(api_client)
    request_id = uuid4()
    payload = upload_payload(request_id=request_id)

    first = create_upload(api_client, headers, payload)
    repeated = create_upload(api_client, headers, payload)
    second = api_client.post("/api/uploads", json=upload_payload(), headers=headers)

    assert repeated == first
    assert len(fake_storage.created_keys) == 1
    assert "Voice Memo.m4a" not in fake_storage.created_keys[0]
    assert fake_storage.created_keys[0].startswith(f"recordings/{first['recordingId']}/original/")
    assert first["partSizeBytes"] == UPLOAD_PART_BYTES
    assert first["partCount"] == 2
    assert second.status_code == 409


def test_upload_accepts_supported_iphone_and_standard_file_types(
    api_client: TestClient,
) -> None:
    headers = authenticated_headers(api_client)

    for filename, content_type in [
        ("memo.m4a", "audio/mp4"),
        ("talk.mp3", "audio/mpeg"),
        ("lossless.flac", "audio/flac"),
        ("voice.opus", "audio/opus"),
        ("audio-only.mp4", "video/mp4"),
    ]:
        response = api_client.post(
            "/api/uploads",
            json=upload_payload(filename=filename, content_type=content_type),
            headers=headers,
        )
        assert response.status_code == 201
        upload_id = response.json()["uploadSessionId"]
        assert (
            api_client.post(f"/api/uploads/{upload_id}/abort", headers=headers).status_code == 204
        )


def test_authorization_reconciles_parts_and_returns_only_missing_urls(
    api_client: TestClient, fake_storage: FakeObjectStorage
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    upload_id = created["uploadSessionId"]

    initial = api_client.post(
        f"/api/uploads/{upload_id}/parts/authorize",
        json={"partNumbers": [1, 2]},
        headers=headers,
    )
    first_urls = {part["partNumber"]: part["url"] for part in initial.json()["authorizedParts"]}
    assert initial.status_code == 200
    assert set(first_urls) == {1, 2}
    assert fake_storage.presign_expirations == [900, 900]

    fake_storage.put_part(fake_storage.last_upload_id, 1, UPLOAD_PART_BYTES)
    resumed = api_client.post(
        f"/api/uploads/{upload_id}/parts/authorize",
        json={"partNumbers": [1, 2]},
        headers=headers,
    )

    assert resumed.status_code == 200
    assert resumed.json()["confirmedParts"] == [{"partNumber": 1, "sizeBytes": UPLOAD_PART_BYTES}]
    assert [part["partNumber"] for part in resumed.json()["authorizedParts"]] == [2]
    assert resumed.json()["authorizedParts"][0]["url"] != first_urls[2]

    state = api_client.get(f"/api/uploads/{upload_id}")
    assert state.json()["confirmedParts"] == resumed.json()["confirmedParts"]


def test_complete_requires_every_verified_part_and_is_idempotent(
    api_client: TestClient,
    fake_storage: FakeObjectStorage,
    app_session_factory: sessionmaker[Session],
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    upload_id = created["uploadSessionId"]
    provider_id = fake_storage.last_upload_id
    fake_storage.put_part(provider_id, 1, UPLOAD_PART_BYTES)

    missing = api_client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
    assert missing.status_code == 409

    fake_storage.put_part(provider_id, 2, 8 * 1024 * 1024)
    completed = api_client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
    repeated = api_client.post(f"/api/uploads/{upload_id}/complete", headers=headers)

    assert completed.status_code == 200
    assert completed.json()["status"] == "queued"
    assert repeated.json() == completed.json()
    assert fake_storage.complete_calls == 1
    with app_session_factory() as database:
        recording = database.get(Recording, UUID(created["recordingId"]))
        upload = database.get(UploadSession, UUID(upload_id))
        assert recording is not None and upload is not None
        assert recording.status is RecordingStatus.QUEUED
        assert recording.verified_bytes == 40 * 1024 * 1024
        assert upload.status is UploadStatus.COMPLETED


def test_wrong_part_size_is_not_confirmed(
    api_client: TestClient, fake_storage: FakeObjectStorage
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    fake_storage.put_part(fake_storage.last_upload_id, 1, 123)

    response = api_client.get(f"/api/uploads/{created['uploadSessionId']}")

    assert response.status_code == 409


def test_completion_recovers_when_storage_finished_before_the_response(
    api_client: TestClient, fake_storage: FakeObjectStorage
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    provider_id = fake_storage.last_upload_id
    object_key = fake_storage.uploads[provider_id][0]
    fake_storage.put_part(provider_id, 1, UPLOAD_PART_BYTES)
    fake_storage.put_part(provider_id, 2, 8 * 1024 * 1024)
    fake_storage.fail_complete = True
    fake_storage.objects[object_key] = ObjectMetadata(size_bytes=40 * 1024 * 1024)

    response = api_client.post(
        f"/api/uploads/{created['uploadSessionId']}/complete", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_abort_is_idempotent_and_releases_the_active_slot(
    api_client: TestClient,
    fake_storage: FakeObjectStorage,
    app_session_factory: sessionmaker[Session],
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    upload_id = created["uploadSessionId"]

    first = api_client.post(f"/api/uploads/{upload_id}/abort", headers=headers)
    repeated = api_client.post(f"/api/uploads/{upload_id}/abort", headers=headers)
    replacement = api_client.post("/api/uploads", json=upload_payload(), headers=headers)

    assert first.status_code == 204
    assert repeated.status_code == 204
    assert replacement.status_code == 201
    assert len(fake_storage.aborted_uploads) == 1
    with app_session_factory() as database:
        upload = database.get(UploadSession, UUID(upload_id))
        assert upload is not None
        assert upload.status is UploadStatus.ABORTED
        assert upload.recording.safe_error_code == "upload_aborted"


def test_expired_upload_is_closed_durably(
    api_client: TestClient,
    fake_storage: FakeObjectStorage,
    app_session_factory: sessionmaker[Session],
) -> None:
    headers = authenticated_headers(api_client)
    created = create_upload(api_client, headers)
    upload_id = UUID(created["uploadSessionId"])
    with app_session_factory.begin() as database:
        upload = database.get(UploadSession, upload_id)
        assert upload is not None
        upload.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    expired = api_client.post(
        f"/api/uploads/{upload_id}/parts/authorize",
        json={"partNumbers": [1]},
        headers=headers,
    )

    assert expired.status_code == 410
    assert len(fake_storage.aborted_uploads) == 1
    with app_session_factory() as database:
        upload = database.get(UploadSession, upload_id)
        assert upload is not None
        assert upload.status is UploadStatus.EXPIRED
        assert upload.recording.status is RecordingStatus.FAILED


def test_rejects_unsafe_unsupported_oversized_and_mismatched_requests(
    api_client: TestClient,
) -> None:
    headers = authenticated_headers(api_client)
    request_id = uuid4()

    unsafe = api_client.post(
        "/api/uploads",
        json=upload_payload(filename="../memo.m4a"),
        headers=headers,
    )
    unsupported = api_client.post(
        "/api/uploads",
        json=upload_payload(filename="notes.txt", content_type="text/plain"),
        headers=headers,
    )
    oversized = api_client.post(
        "/api/uploads",
        json=upload_payload(size_bytes=MAX_RECORDING_BYTES + 1),
        headers=headers,
    )
    accepted = api_client.post(
        "/api/uploads",
        json=upload_payload(request_id=request_id),
        headers=headers,
    )
    mismatch = api_client.post(
        "/api/uploads",
        json=upload_payload(request_id=request_id, size_bytes=1_024),
        headers=headers,
    )

    assert unsafe.status_code == 422
    assert unsupported.status_code == 415
    assert oversized.status_code == 413
    assert accepted.status_code == 201
    assert mismatch.status_code == 409


def test_upload_routes_require_authentication_and_csrf(api_client: TestClient) -> None:
    assert api_client.post("/api/uploads", json=upload_payload()).status_code == 401

    headers = authenticated_headers(api_client)
    no_csrf = api_client.post("/api/uploads", json=upload_payload())
    foreign_origin = api_client.post(
        "/api/uploads",
        json=upload_payload(),
        headers={**headers, "Origin": "https://evil.example"},
    )

    assert no_csrf.status_code == 403
    assert foreign_origin.status_code == 403


def test_two_users_upload_independently_and_cannot_cross_upload_boundaries(
    api_client: TestClient,
    fake_storage: FakeObjectStorage,
) -> None:
    request_id = uuid4()
    first_headers = authenticated_headers(api_client, username="first-user")
    first = create_upload(api_client, first_headers, upload_payload(request_id=request_id))
    first_upload_id = first["uploadSessionId"]
    first_provider_id = fake_storage.last_upload_id

    second_headers = authenticated_headers(api_client, username="second-user")
    second = create_upload(api_client, second_headers)
    assert second["uploadSessionId"] != first_upload_id

    presign_count = len(fake_storage.presign_expirations)
    assert api_client.get(f"/api/uploads/{first_upload_id}").status_code == 404
    assert (
        api_client.post(
            f"/api/uploads/{first_upload_id}/parts/authorize",
            json={"partNumbers": [1]},
            headers=second_headers,
        ).status_code
        == 404
    )
    assert (
        api_client.post(f"/api/uploads/{first_upload_id}/complete", headers=second_headers).status_code
        == 404
    )
    assert (
        api_client.post(f"/api/uploads/{first_upload_id}/abort", headers=second_headers).status_code
        == 404
    )
    assert len(fake_storage.presign_expirations) == presign_count
    assert first_provider_id not in fake_storage.aborted_uploads

    assert (
        api_client.post(
            f"/api/uploads/{second['uploadSessionId']}/abort", headers=second_headers
        ).status_code
        == 204
    )
    collision = api_client.post(
        "/api/uploads",
        json=upload_payload(request_id=request_id),
        headers=second_headers,
    )
    assert collision.status_code == 409
