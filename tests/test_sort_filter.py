"""Manage Collection's /api/inventory sort/filter/pagination — all pure
SQL-level logic (see inventory_admin.list_inventory), no external API
involved, so this runs fully offline."""


def _seed(client):
    client.post("/api/inventory", json={"card_name": "Alpha Card", "total_quantity": 10})
    client.post("/api/inventory", json={"card_name": "Beta Card", "total_quantity": 3})
    client.post("/api/inventory", json={"card_name": "Gamma Card", "total_quantity": 7})
    # A known-priced printing, seeded directly via quick-add's price
    # fields rather than a real pricing-API refresh, so this test suite
    # doesn't depend on network access.
    client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "MSC", "collector_number": "211", "price_usd": 1.48},
    )
    client.post("/api/inventory", json={"card_name": "Unresolved Card", "total_quantity": 1})
    client.post("/api/checkout", json={"decklist_text": "3 Gamma Card", "deck_name": "Test Deck"})


def test_sort_by_name_asc(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "name", "sort_dir": "asc"})
    names = [c["card_name"] for c in r.json()["cards"]]
    assert names == sorted(names)


def test_sort_by_name_desc(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "name", "sort_dir": "desc"})
    names = [c["card_name"] for c in r.json()["cards"]]
    assert names == sorted(names, reverse=True)


def test_sort_by_total_quantity_desc(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "total_quantity", "sort_dir": "desc"})
    qtys = [c["total_quantity"] for c in r.json()["cards"]]
    assert qtys == sorted(qtys, reverse=True)


def test_sort_by_checked_out_desc_puts_checked_out_card_first(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "checked_out", "sort_dir": "desc"})
    cards = r.json()["cards"]
    assert cards[0]["card_name"] == "Gamma Card"
    assert cards[0]["checked_out"] == 3


def test_sort_by_available_asc(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "available", "sort_dir": "asc"})
    avail = [c["available"] for c in r.json()["cards"]]
    assert avail == sorted(avail)


def test_sort_by_value_desc_puts_priced_card_first(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "value", "sort_dir": "desc"})
    cards = r.json()["cards"]
    assert cards[0]["card_name"] == "Sol Ring"
    assert cards[0]["line_value"] is not None
    assert all(c["line_value"] is None for c in cards[1:])


def test_sort_by_value_asc_still_puts_priced_card_first(registered_client):
    """Unpriced (null) rows sort last regardless of direction —
    .nullslast() in list_inventory, not plain ascending null-first."""
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"sort_by": "value", "sort_dir": "asc"})
    cards = r.json()["cards"]
    assert cards[0]["card_name"] == "Sol Ring"


def test_filter_unresolved_only(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"unresolved_only": "true"})
    names = {c["card_name"] for c in r.json()["cards"]}
    assert "Sol Ring" not in names
    assert "Unresolved Card" in names


def test_filter_checked_out_only(registered_client):
    _seed(registered_client)
    r = registered_client.get("/api/inventory", params={"checked_out_only": "true"})
    names = {c["card_name"] for c in r.json()["cards"]}
    assert names == {"Gamma Card"}


def test_search_and_sort_combined(registered_client):
    _seed(registered_client)
    r = registered_client.get(
        "/api/inventory", params={"search": "Card", "sort_by": "total_quantity", "sort_dir": "asc"}
    )
    names = [c["card_name"] for c in r.json()["cards"]]
    assert "Sol Ring" not in names


def test_invalid_sort_by_rejected(registered_client):
    r = registered_client.get("/api/inventory", params={"sort_by": "bogus"})
    assert r.status_code == 400


def test_invalid_sort_dir_rejected(registered_client):
    r = registered_client.get("/api/inventory", params={"sort_dir": "bogus"})
    assert r.status_code == 400
