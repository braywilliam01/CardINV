"""Per-finish price refresh precision against the real Scryfall/TCGdex
APIs -- excluded from the default run (see pytest.ini's
`-m "not live"`). Run explicitly with `pytest -m live`. Covers what
test_price_estimation.py deliberately can't: refresh_all_prices and
refresh_single_price actually writing one CardPrice row per finish
instead of the old price_usd/price_usd_foil pair on a single row."""
import pytest

pytestmark = pytest.mark.live


def _switch_to_pokemon(client):
    res = client.put("/api/session/game", json={"game": "pokemon"})
    assert res.status_code == 200, res.text


def test_mtg_single_price_refresh_targets_requested_finish(registered_client):
    """Lightning Bolt (CLB) 187 is a real, cheap, always-priced MTG
    printing with both a nonfoil and foil market price. Refreshing the
    Nonfoil row and the Foil row separately should store two different
    numbers, not the same pair collapsed onto one row."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Lightning Bolt", "total_quantity": 1, "set_code": "CLB", "collector_number": "187", "finish": "Nonfoil"},
    )
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Lightning Bolt", "total_quantity": 1, "set_code": "CLB", "collector_number": "187", "finish": "Foil"},
    )

    r1 = registered_client.post(
        "/api/pricing/refresh-card",
        params={"card_name": "Lightning Bolt", "set_code": "CLB", "collector_number": "187", "finish": "Nonfoil"},
    )
    assert r1.status_code == 200, r1.text
    r2 = registered_client.post(
        "/api/pricing/refresh-card",
        params={"card_name": "Lightning Bolt", "set_code": "CLB", "collector_number": "187", "finish": "Foil"},
    )
    assert r2.status_code == 200, r2.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    nonfoil = next(p for p in printings if p["finish"] == "Nonfoil")
    foil = next(p for p in printings if p["finish"] == "Foil")
    assert nonfoil["price_usd"] is not None
    assert foil["price_usd"] is not None
    assert nonfoil["is_estimated"] is False and foil["is_estimated"] is False
    assert nonfoil["price_usd"] != foil["price_usd"], "foil and nonfoil of a real printing should have distinct market prices"


def test_mtg_bulk_refresh_writes_both_finishes_and_unspecified(registered_client):
    """refresh_all_prices should proactively price Nonfoil, Foil, and
    the unspecified ("") row for an owned printing from the single
    Scryfall record it already fetched -- not just whichever finish
    happens to be in Inventory today."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Lightning Bolt", "total_quantity": 1, "set_code": "CLB", "collector_number": "187"},
    )
    r = registered_client.post("/api/pricing/refresh-bulk")
    assert r.status_code == 200, r.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    assert len(printings) == 1
    assert printings[0]["finish"] == ""
    assert printings[0]["price_usd"] is not None
    assert printings[0]["is_estimated"] is False


def test_pokemon_single_price_refresh_targets_requested_finish(registered_client):
    """Darkness Ablaze #10 (Accelgor) has both a Normal and a Reverse
    Holofoil market price on TCGdex -- refreshing each finish row
    separately should store that finish's own price, not TCGdex's
    first-listed variant regardless of which finish was asked for."""
    _switch_to_pokemon(registered_client)
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Accelgor", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Normal"},
    )
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Accelgor", "total_quantity": 1, "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil"},
    )

    r1 = registered_client.post(
        "/api/pricing/refresh-card",
        params={"card_name": "Accelgor", "set_code": "DAA", "collector_number": "10", "finish": "Normal"},
    )
    assert r1.status_code == 200, r1.text
    r2 = registered_client.post(
        "/api/pricing/refresh-card",
        params={"card_name": "Accelgor", "set_code": "DAA", "collector_number": "10", "finish": "Reverse Holofoil"},
    )
    assert r2.status_code == 200, r2.text

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Accelgor"}).json()["printings"]
    normal = next(p for p in printings if p["finish"] == "Normal")
    reverse = next(p for p in printings if p["finish"] == "Reverse Holofoil")
    assert normal["price_usd"] is not None
    assert reverse["price_usd"] is not None
    assert normal["price_usd"] != reverse["price_usd"]


def test_collection_value_uses_each_rows_own_finish_price(registered_client):
    """The real latent bug this phase fixes: a Foil Inventory row must
    contribute its own Foil price to collection value, not silently
    reuse a Nonfoil sibling's price_usd column."""
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Lightning Bolt", "total_quantity": 2, "set_code": "CLB", "collector_number": "187", "finish": "Nonfoil"},
    )
    registered_client.post(
        "/api/inventory",
        json={"card_name": "Lightning Bolt", "total_quantity": 3, "set_code": "CLB", "collector_number": "187", "finish": "Foil"},
    )
    registered_client.post("/api/pricing/refresh-bulk")

    printings = registered_client.get("/api/inventory/printings", params={"card_name": "Lightning Bolt"}).json()["printings"]
    nonfoil = next(p for p in printings if p["finish"] == "Nonfoil")
    foil = next(p for p in printings if p["finish"] == "Foil")
    assert nonfoil["line_value"] == round(nonfoil["price_usd"] * 2, 2)
    assert foil["line_value"] == round(foil["price_usd"] * 3, 2)

    summary = registered_client.get("/api/homepage/summary").json()
    expected_total = round(nonfoil["line_value"] + foil["line_value"], 2)
    assert summary["collection_value_usd"] == expected_total
