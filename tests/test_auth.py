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
