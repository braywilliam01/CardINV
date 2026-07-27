"""Collection Search (/api/search) -- the available/missing split, and
the location-aware pick list built on top of it. No existing coverage
of this endpoint predates this file. All network-free."""


def test_available_and_missing_split(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2})

    r = registered_client.post("/api/search", json={"decklist_text": "1 Sol Ring\n1 Brainstorm"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] == ["1 Sol Ring"]
    assert data["missing"] == ["1 Brainstorm"]


def test_partial_availability_splits_the_line(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1})

    r = registered_client.post("/api/search", json={"decklist_text": "3 Sol Ring"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] == ["1 Sol Ring"]
    assert data["missing"] == ["2 Sol Ring"]


def test_pick_list_splits_across_two_locations(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2, "location": "Box A"})
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 3, "location": "Box B"})

    r = registered_client.post("/api/search", json={"decklist_text": "4 Sol Ring"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] == ["4 Sol Ring"]

    by_location = {e["location"]: e["quantity"] for e in data["pick_list"]}
    assert sum(by_location.values()) == 4
    assert set(by_location) <= {"Box A", "Box B"}
    for e in data["pick_list"]:
        assert e["card_name"] == "Sol Ring"
        assert e["is_no_location"] is False


def test_pick_list_entry_flags_no_location(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1})

    r = registered_client.post("/api/search", json={"decklist_text": "1 Sol Ring"})
    assert r.status_code == 200, r.text
    pick_list = r.json()["pick_list"]
    assert len(pick_list) == 1
    assert pick_list[0]["location"] == ""
    assert pick_list[0]["is_no_location"] is True


def test_pick_list_only_covers_the_fulfilled_portion(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"})

    r = registered_client.post("/api/search", json={"decklist_text": "3 Sol Ring"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] == ["1 Sol Ring"]
    assert data["missing"] == ["2 Sol Ring"]
    assert sum(e["quantity"] for e in data["pick_list"]) == 1


def test_pick_list_prefers_real_locations_over_no_location(registered_client):
    """A pick list should be maximally actionable -- when both a real
    location and the unassigned bucket could supply the same card, the
    real location is drawn from first (see
    availability.get_location_availability's return-order docstring)."""
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1})  # no location
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"})

    r = registered_client.post("/api/search", json={"decklist_text": "1 Sol Ring"})
    assert r.status_code == 200, r.text
    pick_list = r.json()["pick_list"]
    assert len(pick_list) == 1
    assert pick_list[0]["location"] == "Box A"


def test_basic_lands_never_appear_in_pick_list(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "location": "Box A"})

    r = registered_client.post(
        "/api/search", json={"decklist_text": "1 Sol Ring\n10 Forest", "ignore_basic_lands": True}
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["skipped_basic_lands"] == 1
    assert all(e["card_name"] != "Forest" for e in data["pick_list"])


def test_pick_list_deduplicates_repeated_lines_for_the_same_card(registered_client):
    """Two lines for the same card in one paste shouldn't double-claim
    the same physical copies -- the reserved dict guard, one axis
    deeper than checkout's equivalent (printing+finish+location)."""
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2, "location": "Box A"})

    r = registered_client.post("/api/search", json={"decklist_text": "1 Sol Ring\n1 Sol Ring"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["available"] == ["1 Sol Ring", "1 Sol Ring"]
    assert sum(e["quantity"] for e in data["pick_list"]) == 2
