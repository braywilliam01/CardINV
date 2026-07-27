"""Agent API — personal API keys and the read-only /api/agent/*
endpoints they authenticate, used by external callers (e.g. an AI
agent suggesting deck builds) instead of a browser session."""


def _create_api_key(client, name="Test Agent"):
    r = client.post("/api/auth/api-keys", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_list_revoke_api_key(registered_client):
    created = _create_api_key(registered_client)
    assert created["token"].startswith("cardinv_")

    listed = registered_client.get("/api/auth/api-keys").json()["api_keys"]
    assert any(k["id"] == created["id"] for k in listed)
    # Metadata only -- never the token or its hash.
    assert all("token" not in k and "key_hash" not in k for k in listed)

    revoke = registered_client.delete(f"/api/auth/api-keys/{created['id']}")
    assert revoke.status_code == 200, revoke.text

    still_listed = registered_client.get("/api/auth/api-keys").json()["api_keys"]
    assert all(k["id"] != created["id"] for k in still_listed)


def test_api_key_limit_enforced(registered_client):
    _create_api_key(registered_client, name="Key One")
    _create_api_key(registered_client, name="Key Two")

    r = registered_client.post("/api/auth/api-keys", json={"name": "Key Three"})
    assert r.status_code == 400

    listed = registered_client.get("/api/auth/api-keys").json()["api_keys"]
    assert len(listed) == 2

    # Revoking one frees up a slot for a new key.
    registered_client.delete(f"/api/auth/api-keys/{listed[0]['id']}")
    r = registered_client.post("/api/auth/api-keys", json={"name": "Key Three"})
    assert r.status_code == 200, r.text


def test_revoke_unknown_key_returns_404(registered_client):
    r = registered_client.delete("/api/auth/api-keys/not-a-real-key-id")
    assert r.status_code == 404


def test_agent_endpoint_rejects_missing_or_invalid_token(registered_client):
    no_header = registered_client.get("/api/agent/collection")
    assert no_header.status_code == 401

    garbage = registered_client.get("/api/agent/collection", headers=_auth_headers("garbage"))
    assert garbage.status_code == 401

    unknown_key = registered_client.get(
        "/api/agent/collection", headers=_auth_headers("cardinv_abcdef123456_notarealsecret")
    )
    assert unknown_key.status_code == 401


def test_agent_endpoint_rejects_revoked_key(registered_client):
    created = _create_api_key(registered_client)
    token = created["token"]
    registered_client.delete(f"/api/auth/api-keys/{created['id']}")

    r = registered_client.get("/api/agent/collection", headers=_auth_headers(token))
    assert r.status_code == 401


def test_agent_collection_returns_owned_cards(registered_client):
    registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "4 Lightning Bolt\n2 Sol Ring", "location": "Box A"})
    token = _create_api_key(registered_client)["token"]

    r = registered_client.get("/api/agent/collection", headers=_auth_headers(token))
    assert r.status_code == 200, r.text
    cards = {c["card_name"]: c for c in r.json()["cards"]}
    assert cards["Lightning Bolt"]["total_quantity"] == 4
    assert cards["Sol Ring"]["total_quantity"] == 2
    assert cards["Sol Ring"]["available"] == 2


def test_agent_key_scoped_to_owning_user_only(registered_client, client, unique_username):
    registered_client.post("/api/inventory", json={"card_name": "Owner's Card", "total_quantity": 1})
    token = _create_api_key(registered_client)["token"]

    other_username = f"{unique_username}_other"
    r = client.post("/api/auth/register", json={"username": other_username, "password": "testpass123"})
    assert r.status_code == 200, r.text
    client.post("/api/inventory", json={"card_name": "Other User's Card", "total_quantity": 1})

    result = client.get("/api/agent/collection", headers=_auth_headers(token))
    assert result.status_code == 200
    names = {c["card_name"] for c in result.json()["cards"]}
    assert names == {"Owner's Card"}, "a token must only ever expose its own owner's collection"


def test_agent_decks_endpoint_matches_deck_cards(registered_client):
    registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "3 Sol Ring", "location": "Box A"})
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "RampDeck"})
    token = _create_api_key(registered_client)["token"]

    r = registered_client.get("/api/agent/decks", headers=_auth_headers(token))
    assert r.status_code == 200, r.text
    decks = {d["deck_name"]: d for d in r.json()["decks"]}
    assert "RampDeck" in decks

    expected_cards = registered_client.get("/api/decks/RampDeck/cards").json()["cards"]
    assert decks["RampDeck"]["cards"] == expected_cards


def test_agent_collection_respects_game_param(registered_client):
    # Default active game is mtg -- add a card there, then switch the
    # session to pokemon and add a differently-named card, so each
    # game's per-user database has exactly one, distinct card.
    registered_client.post("/api/inventory", json={"card_name": "Lightning Bolt", "total_quantity": 1})
    registered_client.put("/api/session/game", json={"game": "pokemon"})
    registered_client.post("/api/inventory", json={"card_name": "Pikachu", "total_quantity": 1})
    token = _create_api_key(registered_client)["token"]

    mtg_cards = registered_client.get(
        "/api/agent/collection", params={"game": "mtg"}, headers=_auth_headers(token)
    ).json()["cards"]
    assert {c["card_name"] for c in mtg_cards} == {"Lightning Bolt"}

    pokemon_cards = registered_client.get(
        "/api/agent/collection", params={"game": "pokemon"}, headers=_auth_headers(token)
    ).json()["cards"]
    assert {c["card_name"] for c in pokemon_cards} == {"Pikachu"}


def test_agent_collection_rejects_unknown_game(registered_client):
    token = _create_api_key(registered_client)["token"]
    r = registered_client.get(
        "/api/agent/collection", params={"game": "not-a-real-game"}, headers=_auth_headers(token)
    )
    assert r.status_code == 400
