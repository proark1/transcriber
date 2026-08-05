from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_readiness_are_available(api_client: TestClient) -> None:
    assert api_client.get("/healthz").json() == {"status": "ok"}
    assert api_client.get("/readyz").json() == {"status": "ready"}


def test_security_headers_and_request_ids_are_present(api_client: TestClient) -> None:
    response = api_client.get("/healthz")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert len(response.headers["x-request-id"]) == 32
    assert "strict-transport-security" not in response.headers


def test_validation_errors_do_not_echo_submitted_values(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/auth/login",
        json={"username": "assad", "pin": "123456", "private": "do-not-echo"},
    )

    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    assert response.json()["error"]["code"] == "invalid_request"
