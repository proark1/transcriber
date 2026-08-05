from __future__ import annotations

from fastapi.testclient import TestClient


def login(client: TestClient, *, username: str = "assad", pin: str = "123456") -> dict[str, object]:
    response = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert response.status_code == 200
    return response.json()


def test_registration_session_and_csrf_protected_logout(api_client: TestClient) -> None:
    login_body = login(api_client, username="AsSaD")

    cookie = api_client.cookies.get("transcriber_session")
    assert cookie is not None
    assert login_body["authenticated"] is True
    assert login_body["accountCreated"] is True
    assert login_body["username"] == "assad"
    assert isinstance(login_body["csrfToken"], str)

    current = api_client.get("/api/auth/session")
    assert current.status_code == 200
    assert current.json()["accountCreated"] is False
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


def test_existing_account_login_is_not_reported_as_created(api_client: TestClient) -> None:
    login(api_client, username="Case-User")
    response = api_client.post(
        "/api/auth/login", json={"username": "CASE-USER", "pin": "123456"}
    )

    assert response.status_code == 200
    assert response.json()["username"] == "case-user"
    assert response.json()["accountCreated"] is False


def test_login_cookie_is_hardened(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/auth/login", json={"username": "cookie-user", "pin": "123456"}
    )

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_existing_username_wrong_pin_has_exact_safe_error(api_client: TestClient) -> None:
    login(api_client, username="pin-user")
    wrong_pin = api_client.post(
        "/api/auth/login", json={"username": "PIN-USER", "pin": "000000"}
    )

    assert wrong_pin.status_code == 401
    assert wrong_pin.json()["error"]["code"] == "incorrect_pin"
    assert wrong_pin.json()["error"]["message"] == "That PIN is incorrect for this username."
    assert "requestId" in wrong_pin.json()["error"]


def test_invalid_inputs_and_reserved_owner_have_exact_errors(api_client: TestClient) -> None:
    cases = [
        ("bad name", "123456", "invalid_username", "Use 3–32 letters or numbers. You may also use ., _ or -."),
        ("valid-name", "12345", "invalid_pin", "Use a 6–12 digit PIN."),
        ("OWNER", "123456", "username_unavailable", "That username is unavailable."),
    ]

    for username, pin, code, message in cases:
        response = api_client.post("/api/auth/login", json={"username": username, "pin": pin})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["message"] == message


def test_login_lockout_returns_retry_after(api_client: TestClient) -> None:
    login(api_client, username="locked-user")
    responses = [
        api_client.post(
            "/api/auth/login", json={"username": "locked-user", "pin": "000000"}
        )
        for _ in range(5)
    ]

    assert [response.status_code for response in responses[:4]] == [401, 401, 401, 401]
    assert responses[4].status_code == 429
    assert responses[4].headers["retry-after"] == "900"


def test_csrf_rejects_a_foreign_origin(api_client: TestClient) -> None:
    login_body = login(api_client, username="csrf-user")

    response = api_client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://evil.example",
            "X-CSRF-Token": str(login_body["csrfToken"]),
        },
    )

    assert response.status_code == 403
    assert api_client.get("/api/auth/session").status_code == 200
