"""price_estimation.refresh_estimated_prices — the unresolved bucket's
"cheapest known real printing" estimate, and how that flows into the
aggregate Manage Collection row. Prices are seeded via quick-add
(no network); refresh_estimated_prices itself is called directly
against a raw session, since it's normally only reached via a real
pricing-API refresh (refresh_single_price/refresh_all_prices), which
this suite avoids to stay network-free."""
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.database import get_user_engine
from app.price_estimation import refresh_estimated_prices


def _raw_session(username, game="mtg"):
    engine = get_user_engine(username, game)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def _seed_resolved_and_unresolved(client):
    """CMM#410 x2 @ $1.74 (real price) + an unresolved bucket x3."""
    client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 1, "set_code": "CMM", "collector_number": "410"},
    )
    client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "CMM", "collector_number": "410", "price_usd": 1.74},
    )
    client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 3})


def test_unresolved_bucket_estimated_as_cheapest_real_price(client, unique_username):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    _seed_resolved_and_unresolved(client)

    db = _raw_session(unique_username)
    written = refresh_estimated_prices(db, datetime.now(timezone.utc))
    db.close()
    assert written == 1

    printings = client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    resolved = next(p for p in printings if not p["is_unresolved"])
    unresolved = next(p for p in printings if p["is_unresolved"])

    assert resolved["price_usd"] == 1.74 and resolved["is_estimated"] is False
    assert unresolved["price_usd"] == 1.74 and unresolved["is_estimated"] is True


def test_aggregate_row_sums_line_value_and_flags_estimated(client, unique_username):
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    _seed_resolved_and_unresolved(client)

    db = _raw_session(unique_username)
    refresh_estimated_prices(db, datetime.now(timezone.utc))
    db.close()

    row = client.get("/api/inventory", params={"search": "Sol Ring"}).json()["cards"][0]
    assert row["price_usd"] is None, "ambiguous for a multi-printing card, only set for exactly one printing"
    assert row["line_value"] == round(1.74 * 2 + 1.74 * 3, 2)
    assert row["has_estimated"] is True


def test_estimate_picks_cheapest_across_finishes_of_the_same_printing(client, unique_username):
    """The cheapest-price query isn't scoped by finish (see
    price_estimation.py's docstring) -- a Foil row priced higher than
    its Nonfoil sibling of the *same* printing just shouldn't win the
    min(), same as it wouldn't across two different printings."""
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 1, "set_code": "CMR", "collector_number": "123", "finish": "Nonfoil"},
    )
    client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123", "finish": "Nonfoil", "price_usd": 2.00},
    )
    client.post(
        "/api/inventory",
        json={"card_name": "Sol Ring", "total_quantity": 1, "set_code": "CMR", "collector_number": "123", "finish": "Foil"},
    )
    client.post(
        "/api/inventory/quick-add",
        json={"card_name": "Sol Ring", "set_code": "CMR", "collector_number": "123", "finish": "Foil", "price_usd": 9.00},
    )
    client.post("/api/inventory", json={"card_name": "Sol Ring", "total_quantity": 3})

    db = _raw_session(unique_username)
    written = refresh_estimated_prices(db, datetime.now(timezone.utc))
    db.close()
    assert written == 1

    printings = client.get("/api/inventory/printings", params={"card_name": "Sol Ring"}).json()["printings"]
    unresolved = next(p for p in printings if p["is_unresolved"])
    assert unresolved["price_usd"] == 2.00, "cheapest across both finishes of the same printing, not the average or the foil price"


def test_estimation_skips_names_with_no_known_real_price(client, unique_username):
    """A name with only an unresolved bucket and no priced printing at
    all is left unpriced, not defaulted to 0 or errored on."""
    client.post("/api/auth/register", json={"username": unique_username, "password": "testpass123"})
    client.post("/api/inventory", json={"card_name": "Never Priced Card", "total_quantity": 2})

    db = _raw_session(unique_username)
    written = refresh_estimated_prices(db, datetime.now(timezone.utc))
    db.close()
    assert written == 0

    printings = client.get(
        "/api/inventory/printings", params={"card_name": "Never Priced Card"}
    ).json()["printings"]
    assert printings[0]["price_usd"] is None
