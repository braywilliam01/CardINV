"""Card Search's per-IP rate limit (see main.py's _card_lookup_rate_limiter)
-- protects pokemontcg.io's shared keyless quota (30 req/min *total*,
not per-visitor) from being exhausted by one rapid searcher. Uses an
empty name so the limiter is exercised without spending real
Scryfall/pokemontcg.io API calls -- the check happens before the
name-validation 400, so this never reaches the network."""
import uuid

from fastapi.testclient import TestClient

from app.main import app


def test_card_lookup_rate_limited_after_repeated_attempts(registered_client):
    for _ in range(30):
        r = registered_client.get("/api/card-lookup", params={"name": ""})
        assert r.status_code == 400  # empty name, rejected before any network call

    r = registered_client.get("/api/card-lookup", params={"name": ""})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_card_lookup_rate_limit_is_per_ip(registered_client):
    for _ in range(30):
        registered_client.get("/api/card-lookup", params={"name": ""})
    assert registered_client.get("/api/card-lookup", params={"name": ""}).status_code == 429

    # A genuinely independent second client (own TestClient instance, own
    # X-Real-IP) isn't affected by another client's exhausted limit.
    # Note: the `registered_client` fixture is built *on top of* the
    # `client` fixture (same underlying instance, just logged in) -- so
    # requesting both in one test signature would give the same object
    # twice, not two independent visitors. Building a fresh one directly
    # here avoids that trap.
    other = TestClient(app)
    other.headers.update({"X-Real-IP": f"test-client-{uuid.uuid4().hex}"})
    other.post(
        "/api/auth/register", json={"username": f"test_{uuid.uuid4().hex[:12]}", "password": "testpass123"}
    )
    res = other.get("/api/card-lookup", params={"name": ""})
    assert res.status_code == 400, "a different IP should not be rate limited by another IP's usage"
