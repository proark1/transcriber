from __future__ import annotations

from fastapi.testclient import TestClient


def login(client: TestClient) -> dict[str, object]:
    response = client.post("/api/auth/login", json={"username": "owner", "pin": "123456"})
    assert response.status_code == 200
    return response.json()


def test_login_session_and_csrf_protected_logout(api_client: TestClient) -> None:
    login_body = login(api_client)

    cookie = api_client.cookies.get("transcriber_session")
    assert cookie is not None
    assert login_body["authenticated"] is True
    assert login_body["username"] == "owner"
    assert isinstance(login_body["csrfToken"], str)

    current = api_client.get("/api/auth/session")
    assert current.status_code == 200
    assert isinstance(current.json()["csrfToken"], str)
    assert current.headers["cache-control"] == "no-store"
    current_csrf = str(current.json()["csrfToken"])

    missing_csrf = api_client.post("/api/auth/logout")
    assert missing_csrf.status_code == 403

    logged_out = api_client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": current_csrf,
        },
    )
    assert logged_out.status_code == 204
    assert api_client.get("/api/auth/session").status_code == 401


def test_login_cookie_is_hardened(api_client: TestClient) -> None:
    response = api_client.post("/api/auth/login", json={"username": "owner", "pin": "123456"})

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_invalid_login_response_is_generic(api_client: TestClient) -> None:
    wrong_username = api_client.post(
        "/api/auth/login", json={"username": "somebody", "pin": "123456"}
    )
    wrong_pin = api_client.post("/api/auth/login", json={"username": "owner", "pin": "000000"})

    assert wrong_username.status_code == 401
    assert wrong_pin.status_code == 401
    assert wrong_username.json()["error"]["message"] == wrong_pin.json()["error"]["message"]
    assert "requestId" in wrong_pin.json()["error"]


def test_login_lockout_returns_retry_after(api_client: TestClient) -> None:
    responses = [
        api_client.post("/api/auth/login", json={"username": "owner", "pin": "000000"})
        for _ in range(5)
    ]

    assert [response.status_code for response in responses[:4]] == [401, 401, 401, 401]
    assert responses[4].status_code == 429
    assert responses[4].headers["retry-after"] == "900"


def test_csrf_rejects_a_foreign_origin(api_client: TestClient) -> None:
    login_body = login(api_client)

    response = api_client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://evil.example",
            "X-CSRF-Token": str(login_body["csrfToken"]),
        },
    )

    assert response.status_code == 403
    assert api_client.get("/api/auth/session").status_code == 200
