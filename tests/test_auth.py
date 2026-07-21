from fastapi.testclient import TestClient

from app.main import app


def test_register_and_me(client, unique_username):
    res = client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == unique_username

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == unique_username


def test_register_duplicate_username_rejected(client, unique_username):
    r1 = client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    assert r1.status_code == 200

    r2 = client.post("/api/auth/register", json={"username": unique_username, "password": "anotherpass123"})
    assert r2.status_code == 400


def test_register_short_password_rejected(client, unique_username):
    res = client.post("/api/auth/register", json={"username": unique_username, "password": "short"})
    assert res.status_code == 400


def test_login_wrong_password_rejected(client, unique_username):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/auth/logout")

    res = client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})
    assert res.status_code == 401


def test_login_logout_round_trip(client, unique_username):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/auth/logout")

    assert client.get("/api/auth/me").status_code == 401

    login_res = client.post("/api/auth/login", json={"username": unique_username, "password": "testpass123"})
    assert login_res.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_unauthenticated_request_rejected(client):
    res = client.get("/api/inventory")
    assert res.status_code == 401


def test_change_password(registered_client, unique_username):
    res = registered_client.put(
        "/api/auth/password",
        json={"current_password": "testpass123", "new_password": "newpassword456"},
    )
    assert res.status_code == 200
    registered_client.post("/api/auth/logout")

    old_login = registered_client.post(
        "/api/auth/login", json={"username": unique_username, "password": "testpass123"}
    )
    assert old_login.status_code == 401, "old password should no longer work"

    new_login = registered_client.post(
        "/api/auth/login", json={"username": unique_username, "password": "newpassword456"}
    )
    assert new_login.status_code == 200, "new password should work"


def test_change_password_wrong_current_rejected(registered_client):
    res = registered_client.put(
        "/api/auth/password",
        json={"current_password": "wrongcurrent", "new_password": "newpassword456"},
    )
    assert res.status_code == 400


def test_admin_only_endpoint_rejects_non_admin(client, unique_username):
    # The very first user registered in the whole test session becomes
    # admin (see auth.register_user) — any later registration is
    # guaranteed non-admin, which is exactly what this test needs.
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    res = client.get("/api/admin/users")
    assert res.status_code == 403


def test_admin_reset_password_for_other_user(client, unique_username, admin_client):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})

    reset_res = admin_client.put(
        f"/api/admin/users/{unique_username}/reset-password", json={"new_password": "resetpassword789"}
    )
    assert reset_res.status_code == 200

    old_login = client.post("/api/auth/login", json={"username": unique_username, "password": "testpass123"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"username": unique_username, "password": "resetpassword789"})
    assert new_login.status_code == 200


def test_login_rate_limited_after_repeated_attempts(client, unique_username):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/auth/logout")

    # The limiter allows 10 attempts per 5-minute window (see main.py's
    # _login_rate_limiter) -- exhaust it with wrong-password attempts,
    # then confirm the next one is rejected before even checking
    # credentials.
    for _ in range(10):
        client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})

    res = client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


def test_register_rate_limited_after_repeated_attempts(client):
    # The limiter allows 5 registrations per hour per IP (see main.py's
    # _register_rate_limiter) -- exhaust it, then confirm the 6th is
    # rejected even with an otherwise-valid, unused username.
    for i in range(5):
        r = client.post(
            "/api/auth/register", json={"username": f"ratelimit_reg_{i}", "password": "testpass123"}
        )
        assert r.status_code == 200, r.text

    res = client.post("/api/auth/register", json={"username": "ratelimit_reg_overflow", "password": "testpass123"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


def test_rate_limit_is_per_ip_not_global(client, unique_username):
    """A different client (different X-Real-IP, see conftest._new_client)
    isn't affected by another client's exhausted limit."""
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/auth/logout")
    for _ in range(10):
        client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})
    assert client.post(
        "/api/auth/login", json={"username": unique_username, "password": "wrongpassword"}
    ).status_code == 429

    other_client = TestClient(app)
    other_client.headers.update({"X-Real-IP": "test-client-a-completely-different-visitor"})
    res = other_client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})
    assert res.status_code == 401, "a different IP should not be affected by another IP's rate limit"


def test_rate_limit_prefers_cf_connecting_ip_over_x_real_ip(client, unique_username):
    """On a Cloudflare Tunnel deployment (no local nginx — see
    DEPLOY.md), Cf-Connecting-Ip carries the real visitor address;
    that should be what actually keys the rate limiter, not whatever
    else might be in X-Real-IP."""
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/auth/logout")

    # This client's X-Real-IP (set by the `client` fixture) is already
    # unique to this test; overriding Cf-Connecting-Ip to something
    # else entirely and exhausting the limit under *that* identity
    # should still get this client rate limited, proving Cf-Connecting-Ip
    # won out over X-Real-IP.
    client.headers.update({"Cf-Connecting-Ip": f"cf-{unique_username}"})
    for _ in range(10):
        client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})

    res = client.post("/api/auth/login", json={"username": unique_username, "password": "wrongpassword"})
    assert res.status_code == 429
