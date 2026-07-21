import os
import shutil
import tempfile
import uuid

import pytest

# app/main.py reads static/index.html, app.js, and app.css via paths
# relative to the process's working directory (the same constraint
# uvicorn already has — see DEPLOY.md, which always cds into the
# project root first) — so pytest needs to run from there too,
# regardless of where it was invoked from.
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must happen before any `app.*` module is imported anywhere in the
# test session — app/database.py reads DATA_DIR from the environment
# at import time, so setting it here (before the imports below) keeps
# every test's data in an isolated temp directory instead of the real
# project's ./data.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="cardinv-test-")
os.environ["DATA_DIR"] = _TEST_DATA_DIR
os.environ.setdefault("SESSION_SECRET_KEY", "test-secret-key-not-for-production")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data_dir():
    yield
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


def _new_client() -> TestClient:
    """
    A fresh TestClient with its own synthetic X-Real-IP — every
    request from a Starlette TestClient reports the same
    request.client.host ("testclient"), which would collapse every
    test into one shared bucket for main.py's login/register rate
    limiters (see rate_limit.py, which reads X-Real-IP the same way
    it would behind the real nginx deployment). A unique IP per
    client keeps each test isolated, the same way each represents a
    different real-world visitor.
    """
    c = TestClient(app)
    # Doesn't need to look like a real IP -- rate_limit.get_client_ip
    # treats the header as an opaque key, and a full UUID keeps this
    # collision-free across an entire test run (a small numeric range,
    # e.g. "203.0.113.N" for small N, collides constantly at this
    # volume of client instances — verified empirically while writing
    # this suite).
    c.headers.update({"X-Real-IP": f"test-client-{uuid.uuid4().hex}"})
    return c


@pytest.fixture
def client():
    """A fresh, unauthenticated TestClient. Cookies persist for this
    client's lifetime (same as `requests.Session()`), so a test that
    registers/logs in with this client stays logged in across
    subsequent calls on the same instance."""
    return _new_client()


@pytest.fixture
def unique_username():
    """Every test that registers a user should use a fresh username —
    all users share one temp DATA_DIR for the whole test session (real
    per-user isolation comes from each getting its own SQLite files,
    same as production), so reusing a name across tests would collide."""
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def registered_client(client, unique_username):
    """A logged-in TestClient for a fresh, unique, non-deterministic-
    admin-status user — the very first account registered in a given
    DATA_DIR becomes admin (see auth.register_user), so most tests
    that don't care about admin-ness should use this rather than
    asserting one way or the other about is_admin."""
    res = client.post(
        "/api/auth/register", json={"username": unique_username, "password": "testpass123"}
    )
    assert res.status_code == 200, res.text
    return client


_ADMIN_USERNAME = "test_admin_bootstrap"
_ADMIN_PASSWORD = "admin-bootstrap-pass123"


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_admin(_cleanup_test_data_dir):
    """
    Registers a known admin account as the very first thing in the
    test session — the first user ever registered in a DATA_DIR
    becomes admin (see auth.register_user), and this claims that slot
    deterministically so admin-only tests don't depend on test
    execution order. Runs before any test body via session-scoped
    autouse; depending on _cleanup_test_data_dir (not using it
    directly) just pins this to run after DATA_DIR exists.
    """
    _new_client().post(
        "/api/auth/register", json={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD}
    )


@pytest.fixture
def admin_client():
    """A fresh, logged-in TestClient for the bootstrapped admin account."""
    c = _new_client()
    res = c.post("/api/auth/login", json={"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD})
    assert res.status_code == 200, res.text
    return c
