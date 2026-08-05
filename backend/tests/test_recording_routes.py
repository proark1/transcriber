from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_upload_routes import authenticated_headers
from transcriber.models import Language, Recording, RecordingStatus, User


def history_recording(
    database: Session,
    *,
    user: User,
    status: RecordingStatus = RecordingStatus.COMPLETED,
    transcript: str | None = "Hello world.\n",
    playback: bool = True,
) -> Recording:
    recording_id = uuid4()
    recording = Recording(
        id=recording_id,
        user_id=user.id,
        display_filename="İstanbul Gespräch.m4a",
        reported_content_type="audio/mp4",
        expected_bytes=5_000,
        verified_bytes=5_000,
        duration_seconds=125.5,
        container="mov",
        audio_codec="aac",
        language=Language.TURKISH,
        original_object_key=f"recordings/{recording_id}/original/private",
        playback_object_key=(f"recordings/{recording_id}/playback/audio.m4a" if playback else None),
        status=status,
        completed_chunks=2 if status is RecordingStatus.COMPLETED else 1,
        total_chunks=2,
        transcript_text=transcript,
        completed_at=(
            datetime(2026, 8, 4, 12, tzinfo=UTC) if status is RecordingStatus.COMPLETED else None
        ),
    )
    database.add(recording)
    database.commit()
    return recording


def test_history_and_detail_expose_progress_but_never_private_keys(
    api_client: TestClient, database_session: Session, test_user: User
) -> None:
    recording = history_recording(database_session, user=test_user)
    authenticated_headers(api_client)

    history = api_client.get("/api/recordings")
    detail = api_client.get(f"/api/recordings/{recording.id}")

    assert history.status_code == 200
    assert history.json() == [detail.json()]
    assert detail.json()["filename"] == "İstanbul Gespräch.m4a"
    assert detail.json()["language"] == "tr"
    assert detail.json()["status"] == "completed"
    assert detail.json()["completedChunks"] == 2
    assert detail.json()["totalChunks"] == 2
    assert detail.json()["hasPlayback"] is True
    assert detail.json()["hasTranscript"] is True
    assert "object" not in detail.text.lower()
    assert "private" not in detail.text.lower()
    assert detail.headers["cache-control"] == "no-store"


def test_displayed_and_downloaded_transcripts_are_byte_identical_utf8(
    api_client: TestClient, database_session: Session, test_user: User
) -> None:
    text = "Guten Morgen.\n\nİstanbul'da görüşürüz.\n"
    recording = history_recording(database_session, user=test_user, transcript=text)
    authenticated_headers(api_client)

    displayed = api_client.get(f"/api/recordings/{recording.id}/transcript")
    downloaded = api_client.get(f"/api/recordings/{recording.id}/transcript.txt")

    assert displayed.status_code == 200
    assert displayed.content == text.encode("utf-8")
    assert downloaded.content == displayed.content
    assert "attachment" in downloaded.headers["content-disposition"]
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    assert displayed.headers["cache-control"] == "no-store"


def test_transcript_is_rejected_until_completion_and_during_deletion(
    api_client: TestClient, database_session: Session, test_user: User
) -> None:
    recording = history_recording(
        database_session,
        user=test_user,
        status=RecordingStatus.FAILED,
        transcript=None,
    )
    authenticated_headers(api_client)

    assert api_client.get(f"/api/recordings/{recording.id}/transcript").status_code == 409


def test_manual_retry_queues_a_failed_recording(
    api_client: TestClient, database_session: Session, test_user: User
) -> None:
    recording = history_recording(
        database_session,
        user=test_user,
        status=RecordingStatus.FAILED,
        transcript=None,
        playback=False,
    )
    headers = authenticated_headers(api_client)

    response = api_client.post(f"/api/recordings/{recording.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["safeErrorCode"] is None


def test_manual_retry_rejects_when_another_recording_is_active(
    api_client: TestClient,
    database_session: Session,
    test_user: User,
) -> None:
    failed = history_recording(
        database_session,
        user=test_user,
        status=RecordingStatus.FAILED,
        transcript=None,
        playback=False,
    )
    active = history_recording(
        database_session,
        user=test_user,
        status=RecordingStatus.QUEUED,
        transcript=None,
        playback=False,
    )
    del active
    headers = authenticated_headers(api_client)

    response = api_client.post(f"/api/recordings/{failed.id}/retry", headers=headers)

    assert response.status_code == 409


def test_recording_routes_require_authentication(
    api_client: TestClient, database_session: Session, test_user: User
) -> None:
    recording = history_recording(database_session, user=test_user)

    assert api_client.get("/api/recordings").status_code == 401
    assert api_client.get(f"/api/recordings/{recording.id}").status_code == 401
    assert api_client.get(f"/api/recordings/{recording.id}/transcript").status_code == 401


def test_missing_recording_returns_a_safe_not_found(
    api_client: TestClient,
) -> None:
    authenticated_headers(api_client)

    response = api_client.get(f"/api/recordings/{UUID(int=0)}")

    assert response.status_code == 404
    assert "requestId" in response.json()["error"]


def test_recording_boundaries_return_not_found_across_users(
    api_client: TestClient,
    database_session: Session,
    test_user: User,
    user_factory: Callable[..., User],
) -> None:
    other_user = user_factory(username="other-user")
    own = history_recording(database_session, user=test_user)
    other_completed = history_recording(database_session, user=other_user)
    other_failed = history_recording(
        database_session,
        user=other_user,
        status=RecordingStatus.FAILED,
        transcript=None,
        playback=False,
    )
    headers = authenticated_headers(api_client)

    history = api_client.get("/api/recordings")
    assert [item["id"] for item in history.json()] == [str(own.id)]

    requests = [
        api_client.get(f"/api/recordings/{other_completed.id}"),
        api_client.get(f"/api/recordings/{other_completed.id}/playback"),
        api_client.get(f"/api/recordings/{other_completed.id}/transcript"),
        api_client.get(f"/api/recordings/{other_completed.id}/transcript.txt"),
        api_client.post(f"/api/recordings/{other_failed.id}/retry", headers=headers),
        api_client.delete(f"/api/recordings/{other_completed.id}", headers=headers),
    ]

    assert [response.status_code for response in requests] == [404, 404, 404, 404, 404, 404]
