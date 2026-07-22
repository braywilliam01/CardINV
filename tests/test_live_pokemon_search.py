"""Pokemon Card Search against the real TCGdex API -- excluded from the
default run (see pytest.ini's `-m "not live"`), since it needs network
access and depends on a third party's uptime. Run explicitly with
`pytest -m live`."""
import pytest

pytestmark = pytest.mark.live


def _switch_to_pokemon(client):
    res = client.put("/api/session/game", json={"game": "pokemon"})
    assert res.status_code == 200, res.text


def test_name_search_resolves_a_real_printing(registered_client):
    _switch_to_pokemon(registered_client)
    r = registered_client.get("/api/card-lookup", params={"name": "Charizard"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Charizard"
    assert data["set_code"]
    assert data["collector_number"]
    assert data["primary"]["type_line"].startswith("Pok")  # Pokémon -- accent may or may not render depending on encoding


def test_search_prefers_a_printing_with_an_image(registered_client):
    """See pokemon_lookup.lookup_card's image-preferring heuristic --
    a well-known name shouldn't land on an image-less promo printing
    when better-illustrated printings exist among the results."""
    _switch_to_pokemon(registered_client)
    r = registered_client.get("/api/card-lookup", params={"name": "Charizard"})
    assert r.status_code == 200, r.text
    assert r.json()["primary"]["image_url"] is not None


def test_bogus_name_404s_cleanly(registered_client):
    _switch_to_pokemon(registered_client)
    r = registered_client.get("/api/card-lookup", params={"name": "zzznonexistentpokemonxyz123"})
    assert r.status_code == 404


def test_add_to_inventory_carries_over_fetched_price(registered_client):
    """End-to-end: Card Search fetches a real printing's price, and
    the per-variant Add action stores that one chip's price
    immediately -- no separate refresh needed. Not every printing has
    tracked pricing (promos especially), so this tries a few
    well-known names and accepts the first one that actually has price
    data, rather than asserting on one specific card that might
    legitimately come back unpriced."""
    _switch_to_pokemon(registered_client)

    card = None
    for name in ["Pikachu", "Charizard", "Mewtwo", "Blastoise", "Venusaur"]:
        lookup = registered_client.get("/api/card-lookup", params={"name": name})
        assert lookup.status_code == 200, lookup.text
        data = lookup.json()
        if data["prices"]:
            card = data
            break
    assert card is not None, "none of the sample names had any tracked pricing -- unexpected"

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
    assert matched["finish"] == chip["label"]  # Pokemon chip labels already match POKEMON_FINISHES 1:1


def test_set_and_number_query_resolves_exact_printing(registered_client):
    """The free-text "SET NUMBER" printing-reference syntax (see
    search_query.parse_search_query and the Card Search UI's own help
    text) -- previously only wired up for MTG; Pokemon's lookup_card
    just substring-searched the raw "DAA 010" string as a card name
    and found nothing. Darkness Ablaze #10 (Accelgor) is used because
    it's a plain, unlikely-to-change common printing."""
    _switch_to_pokemon(registered_client)
    r = registered_client.get("/api/card-lookup", params={"name": "DAA 10"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["set_code"] == "DAA"
    assert data["collector_number"] == "10"


def test_set_and_number_query_tolerates_zero_padded_number(registered_client):
    """The number printed on a physical card (and stored by the
    previous provider) is zero-padded ("010/189"); TCGdex's own ids
    aren't ("10"). A search or refresh using the padded form must
    still resolve -- see pokemon_common.normalize_collector_number."""
    _switch_to_pokemon(registered_client)
    r = registered_client.get("/api/card-lookup", params={"name": "DAA 010"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["set_code"] == "DAA"
    assert data["collector_number"] == "10"


def test_single_printing_refresh_matches_search_result(registered_client):
    """Exercises lookup_card_printing's set_code -> TCGdex internal-id
    resolution path (see sets_cache.resolve_pokemon_set_id) -- the
    same path a real "$" refresh button click takes."""
    _switch_to_pokemon(registered_client)
    lookup = registered_client.get("/api/card-lookup", params={"name": "Charizard"})
    card = lookup.json()

    registered_client.post(
        "/api/inventory",
        json={"card_name": card["inventory_name"], "total_quantity": 1, "set_code": card["set_code"], "collector_number": card["collector_number"]},
    )
    refresh = registered_client.post(
        "/api/pricing/refresh-card",
        params={"card_name": card["inventory_name"], "set_code": card["set_code"], "collector_number": card["collector_number"]},
    )
    assert refresh.status_code == 200, refresh.text
    printing = refresh.json()["printings"][0]
    assert printing["is_estimated"] is False
