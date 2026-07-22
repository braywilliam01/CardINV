"""Card Search against the real Scryfall API -- excluded from the
default run (see pytest.ini's `-m "not live"`), since it needs network
access and depends on a third party's uptime. Run explicitly with
`pytest -m live`."""
import pytest

pytestmark = pytest.mark.live


def test_set_and_number_only_resolves_exact_printing(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "CLB 187"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Lightning Bolt"
    assert data["set_code"] == "CLB"
    assert data["collector_number"] == "187"


def test_name_comma_set_number_pins_specific_printing(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "Lightning Bolt, MSC 806"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["set_code"] == "MSC"
    assert data["collector_number"] == "806"


def test_plain_name_search_still_works(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "Sol Ring"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Sol Ring"


def test_comma_in_real_card_name_not_truncated(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "Urza, Lord High Artificer"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Urza, Lord High Artificer"


def test_typo_printing_falls_back_to_name_search(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "Sol Ring, CMR 99999"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Sol Ring"


def test_bogus_printing_with_no_name_404s(registered_client):
    r = registered_client.get("/api/card-lookup", params={"name": "ZZZ 99999"})
    assert r.status_code == 404


def test_add_to_inventory_carries_over_fetched_price(registered_client):
    """End-to-end: Card Search fetches a real printing's price, and
    the per-variant Add action stores that one chip's price
    immediately -- no separate refresh needed. Sends the chip's own
    label as `finish` (see main.py's _finish_for_chip_label -- MTG's
    "USD" chip maps to the real "Nonfoil" finish server-side)."""
    lookup = registered_client.get("/api/card-lookup", params={"name": "CLB 304"})
    assert lookup.status_code == 200, lookup.text
    card = lookup.json()
    assert card["prices"], "expected at least one price variant for a real, common printing"

    chip = card["prices"][0]
    add = registered_client.post(
        "/api/inventory/quick-add",
        json={
            "card_name": card["inventory_name"],
            "set_code": card["set_code"],
            "collector_number": card["collector_number"],
            "finish": chip["label"],
            "price_usd": chip["value"],
        },
    )
    assert add.status_code == 200, add.text

    printings = registered_client.get(
        "/api/inventory/printings", params={"card_name": card["inventory_name"]}
    ).json()["printings"]
    matched = next(p for p in printings if p["set_code"] == card["set_code"] and p["collector_number"] == card["collector_number"])
    assert matched["price_usd"] == chip["value"]
    assert matched["is_finish_unspecified"] is False
