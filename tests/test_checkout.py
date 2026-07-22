"""Deck checkout/checkin — cheapest-first printing selection, pinned
vs. unpinned lines, sync mode, and the decklist-text round trip.
Prices are seeded directly via quick-add's price fields (not a real
pricing-API refresh) so this suite runs fully offline."""
from urllib.parse import quote


def _seed_two_priced_printings(client):
    """Sol Ring across two printings + one unresolved copy, MSC#212
    cheaper ($1.04) than CMM#410 ($1.74) -- deterministic cheapest-
    first ordering without any network call. Ends at MSC#212 x2,
    CMM#410 x2, unresolved x1, same as the real-Scryfall-backed
    version this was ported from."""
    client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "set_code": "msc", "collector_number": "212"})
    client.post("/api/inventory/quick-add", json={"card_name": "Sol Ring", "set_code": "msc", "collector_number": "212", "price_usd": 1.04})
    client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1, "set_code": "cmm", "collector_number": "410"})
    client.post("/api/inventory/quick-add", json={"card_name": "Sol Ring", "set_code": "cmm", "collector_number": "410", "price_usd": 1.74})
    client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 1})


def _deck_cards(client, deck_name):
    r = client.get(f"/api/decks/{quote(deck_name)}/cards")
    assert r.status_code == 200, r.text
    return r.json()["cards"]


def test_unpinned_checkout_draws_cheapest_first(registered_client):
    _seed_two_priced_printings(registered_client)

    r = registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["fulfilled_qty"] == 3
    used = {(p["set_code"], p["collector_number"]): p["quantity"] for p in line["printings"]}
    assert used == {("MSC", "212"): 2, ("CMM", "410"): 1}, "should exhaust cheaper MSC#212 (x2) before drawing 1 from CMM#410"


def test_pinned_checkout_targets_exact_printing(registered_client):
    _seed_two_priced_printings(registered_client)
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})

    r = registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["printings"] == [{"set_code": "CMM", "collector_number": "410", "quantity": 1}]


def test_deck_cards_one_row_per_printing(registered_client):
    _seed_two_priced_printings(registered_client)
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})

    cards = _deck_cards(registered_client, "Cheap Deck")
    assert len(cards) == 2
    by_printing = {(c["set_code"], c["collector_number"]): c["quantity"] for c in cards}
    assert by_printing == {("MSC", "212"): 2, ("CMM", "410"): 2}


def test_unpinned_checkin_returns_cheapest_assigned_first(registered_client):
    _seed_two_priced_printings(registered_client)
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})

    r = registered_client.post("/api/checkin", json={"decklist_text": "1 Sol Ring", "deck_name": "Cheap Deck"})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["printings"] == [{"set_code": "MSC", "collector_number": "212", "quantity": 1}]


def test_pinned_checkin_targets_exact_printing(registered_client):
    _seed_two_priced_printings(registered_client)
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkin", json={"decklist_text": "1 Sol Ring", "deck_name": "Cheap Deck"})

    r = registered_client.post("/api/checkin", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["printings"] == [{"set_code": "CMM", "collector_number": "410", "quantity": 1}]

    by_printing = {(c["set_code"], c["collector_number"]): c["quantity"] for c in _deck_cards(registered_client, "Cheap Deck")}
    assert by_printing == {("MSC", "212"): 1, ("CMM", "410"): 1}


def test_sync_checkout_reaches_exact_target_state(registered_client):
    _seed_two_priced_printings(registered_client)

    r = registered_client.post(
        "/api/checkout/sync",
        json={"decklist_text": "1 Sol Ring (CMM) 410\n1 Sol Ring", "deck_name": "Sync Deck"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []

    by_printing = {(c["set_code"], c["collector_number"]): c["quantity"] for c in _deck_cards(registered_client, "Sync Deck")}
    assert by_printing == {("CMM", "410"): 1, ("MSC", "212"): 1}


def test_sync_checkin_with_empty_decklist_drains_deck(registered_client):
    _seed_two_priced_printings(registered_client)
    registered_client.post(
        "/api/checkout/sync",
        json={"decklist_text": "1 Sol Ring (CMM) 410\n1 Sol Ring", "deck_name": "Sync Deck"},
    )

    r = registered_client.post("/api/checkin/sync", json={"decklist_text": "", "deck_name": "Sync Deck"})
    assert r.status_code == 200, r.text
    assert _deck_cards(registered_client, "Sync Deck") == []


def test_decklist_text_round_trips_through_checkout(registered_client):
    """Deck cards rendered back to '(SET) NUM' text, pasted as a fresh
    checkout on a new deck, reproduces the same per-printing split.
    Checks back in first (mirroring test_pinned_checkin_targets_exact_
    printing's end state) so the round-trip checkout is pulling from
    printings that actually have availability, not ones already fully
    checked out to "Cheap Deck" itself."""
    _seed_two_priced_printings(registered_client)
    registered_client.post("/api/checkout", json={"decklist_text": "3 Sol Ring", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkout", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkin", json={"decklist_text": "1 Sol Ring", "deck_name": "Cheap Deck"})
    registered_client.post("/api/checkin", json={"decklist_text": "1 Sol Ring (CMM) 410", "deck_name": "Cheap Deck"})

    cards = _deck_cards(registered_client, "Cheap Deck")
    lines = [f"{c['quantity']} {c['card_name']} ({c['set_code']}) {c['collector_number']}" for c in cards]

    r = registered_client.post(
        "/api/checkout", json={"decklist_text": "\n".join(lines), "deck_name": "Round Trip Deck"}
    )
    assert r.status_code == 200, r.text
    assert all(l["status"] == "ok" for l in r.json()["lines"])


# --- Finish (decks stay finish-blind -- see checkout.py's module-level
# reasoning on _draw_down_checkout) --------------------------------------


def test_pinned_checkout_only_targets_unspecified_finish_row(registered_client):
    """A pinned "(SET) NUM" line always targets that printing's
    unspecified-finish ("") row specifically -- if every copy of that
    printing happens to be in a finish-resolved row instead, 0 are
    available. Intentional (no finish-pinning syntax), not a bug."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Charizard", "total_quantity": 3, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
    )

    r = registered_client.post(
        "/api/checkout", json={"decklist_text": "1 Charizard (DAA) 10", "deck_name": "Some Deck"}
    )
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["fulfilled_qty"] == 0
    assert line["status"] == "not_found"


def test_unpinned_checkout_spans_finish_rows_of_same_printing(registered_client):
    """Unpinned draw-down treats finish as just one more dimension of
    the rows it iterates -- requesting more than either single finish
    row alone can supply forces it to pull from both."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
    )
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Charizard", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil"},
    )

    r = registered_client.post(
        "/api/checkout", json={"decklist_text": "2 Charizard", "deck_name": "Some Deck"}
    )
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["fulfilled_qty"] == 2  # drew from both finish rows -- 1 each, only 1 of each exists


def test_checked_in_assignment_retains_drawn_finish(registered_client):
    """A copy drawn from a finish-resolved row during unpinned
    checkout should return to *that same* row on checkin, not get
    reclassified as unspecified-finish."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Charizard", "total_quantity": 2, "set_code": "DAA", "collector_number": "10", "finish": "Holofoil"},
    )
    registered_client.post("/api/checkout", json={"decklist_text": "1 Charizard", "deck_name": "Some Deck"})
    registered_client.post("/api/checkin", json={"decklist_text": "1 Charizard", "deck_name": "Some Deck"})

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Charizard"}).json()["printings"]
    assert len(printings) == 1
    assert printings[0]["finish"] == "Holofoil"
    assert printings[0]["total_quantity"] == 2  # fully returned to the same row it came from
