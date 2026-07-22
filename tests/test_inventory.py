"""Manage Collection's core CRUD: bulk add/remove, per-printing
add/adjust/delete, the fix-up (assign-printing) workflow, pagination,
and quick-add's price carryover from Card Search. All network-free."""


def test_bulk_add_lands_in_unresolved_bucket(registered_client):
    r = registered_client.post(
        "/api/inventory/bulk-add",
        json={"decklist_text": "4 Lightning Bolt\n2 Sol Ring\n1 Brainstorm"},
    )
    assert r.status_code == 200, r.text
    assert all(l["status"] in ("ok", "created") for l in r.json()["lines"])


def test_add_second_printing_increases_printing_count(registered_client):
    registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "2 Sol Ring"})

    r = registered_client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 3, "set_code": "cmr", "collector_number": "123"},
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["total_quantity"] == 5  # 2 unresolved + 3 CMR
    assert row["printing_count"] == 2


def test_bulk_remove_draws_from_unresolved_first(registered_client):
    registered_client.post("/api/inventory/bulk-add", json={"decklist_text": "2 Sol Ring"})
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 3, "set_code": "cmr", "collector_number": "123"},
    )

    r = registered_client.post("/api/inventory/bulk-remove", json={"decklist_text": "1 Sol Ring"})
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    unresolved = next(p for p in printings if p["is_unresolved"])
    cmr = next(p for p in printings if not p["is_unresolved"])
    assert unresolved["total_quantity"] == 1
    assert cmr["total_quantity"] == 3


def test_pagination_page_sizes(registered_client):
    for size in (25, 50, 100):
        r = registered_client.get("/api/inventory", params={"page": 1, "page_size": size})
        assert r.status_code == 200
        assert r.json()["page_size"] == size


def test_pagination_rejects_invalid_page_size(registered_client):
    r = registered_client.get("/api/inventory", params={"page": 1, "page_size": 17})
    assert r.status_code == 400


def test_fixup_assigns_unresolved_copies_to_printing(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 5})

    r = registered_client.post(
        "/api/inventory/assign-printing",
        json={"card_name": "Sol Ring", "quantity": 2, "set_code": "CMR", "collector_number": "123"},
    )
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    unresolved = next(p for p in printings if p["is_unresolved"])
    cmr = next(p for p in printings if not p["is_unresolved"])
    assert unresolved["total_quantity"] == 3
    assert cmr["total_quantity"] == 2
    # Fix-up only reclassifies which printing bucket copies live in --
    # the card's total must be unchanged.
    row = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
    assert row["total_quantity"] == 5


def test_adjust_quantity_sets_exact_value(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 3})

    r = registered_client.patch("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 7})
    assert r.status_code == 200, r.text
    assert r.json()["total_quantity"] == 7


def test_delete_printing_removes_just_that_printing(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2})
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 3, "set_code": "CMR", "collector_number": "123"},
    )

    r = registered_client.delete(
        "/api/inventory/printing", params={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123"}
    )
    assert r.status_code == 200, r.text

    row = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
    assert row["printing_count"] == 1
    assert row["total_quantity"] == 2


def test_delete_card_removes_every_printing(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 2})
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 3, "set_code": "CMR", "collector_number": "123"},
    )

    r = registered_client.delete("/api/inventory", params={"card_name": "Sol Ring"})
    assert r.status_code == 200, r.text

    result = registered_client.get("/api/inventory", params={"search": "Sol Ring"}).json()
    assert result["total_count"] == 0


def test_delete_checked_out_card_blocked_without_force(registered_client):
    registered_client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 3})
    registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring", "deck_name": "Some Deck"})

    r = registered_client.delete("/api/inventory", params={"card_name": "Sol Ring"})
    assert r.status_code == 409
    assert "decks" in r.json()["detail"]

    r_forced = registered_client.delete("/api/inventory", params={"card_name": "Sol Ring", "force": "true"})
    assert r_forced.status_code == 200


def test_quick_add_carries_over_known_price(registered_client):
    """Card Search's per-variant 'Add' action, with the price it
    already fetched sent along -- should be stored immediately, no
    separate refresh needed (see pricing.store_known_price). One price
    per finish now -- 'Foil' here is a real Card Search price-chip
    label, already a recognized finish (see finishes.MTG_FINISHES)."""
    r = registered_client.post(
        "/api/inventory/quick-add",
        json={
            "card_name": "Campfire",
            "set_code": "CLB",
            "collector_number": "304",
            "finish": "Foil",
            "price_usd": 1.36,
        },
    )
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Campfire"}).json()["printings"]
    assert printings == [
        {
            "set_code": "CLB",
            "collector_number": "304",
            "finish": "Foil",
            "is_finish_unspecified": False,
            "total_quantity": 1,
            "is_unresolved": False,
            "price_usd": 1.36,
            "price_usd_foil": None,
            "is_estimated": False,
            "line_value": 1.36,
        }
    ]


def test_quick_add_without_price_leaves_printing_unpriced(registered_client):
    r = registered_client.post(
        "/api/inventory/quick-add", json={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123"}
    )
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    assert printings[0]["price_usd"] is None


def test_quick_add_increments_existing_printing(registered_client):
    registered_client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123", "price_usd": 1.5},
    )
    r = registered_client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123", "price_usd": 1.6},
    )
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    assert printings[0]["total_quantity"] == 2
    assert printings[0]["price_usd"] == 1.6  # most recent price wins
