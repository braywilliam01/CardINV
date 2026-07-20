import httpx

from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .pokemon_common import POKEMON_API_BASE, HEADERS, extract_usd_prices, extract_eur_price

# Curated subset of the formats pokemontcg.io tracks legality for.
DISPLAY_FORMATS = ["standard", "expanded", "unlimited"]


def _escape_query_value(value: str) -> str:
    """Pokemontcg.io's search uses Lucene syntax — strip characters that
    would otherwise break or inject into the query string."""
    return value.replace('"', "").replace("\\", "").strip()


def _search_cards(client: httpx.Client, query: str) -> list[dict]:
    resp = client.get(
        f"{POKEMON_API_BASE}/cards",
        params={"q": query, "pageSize": 50},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def lookup_card(name: str) -> dict | None:
    """
    Looks up one card by name against pokemontcg.io. Unlike Scryfall,
    this API has no dedicated fuzzy-match endpoint — it's Lucene-query
    based — so this tries an exact match first, falls back to a
    substring search, and picks the closest name from whatever comes
    back via the same rapidfuzz matching the rest of the app already
    uses. Typo tolerance is real but weaker than the Scryfall side:
    it can't recover from a badly misspelled first letter the way
    Scryfall's ?fuzzy= can.

    A name can have dozens of printings (one row per set); this picks
    the closest-matching name, then the printing among those with the
    most complete pricing data.
    """
    name = name.strip()
    if not name:
        return None

    safe_name = _escape_query_value(name)
    if not safe_name:
        return None

    with httpx.Client(follow_redirects=True) as client:
        cards = _search_cards(client, f'name:"{safe_name}"')
        if not cards:
            cards = _search_cards(client, f"name:*{safe_name}*")

    if not cards:
        return None

    candidate_names = sorted({c["name"] for c in cards})
    best_name = find_best_match(name, candidate_names, threshold=DEFAULT_THRESHOLD)
    if best_name is None:
        best_name = candidate_names[0]

    printings = [c for c in cards if c["name"] == best_name]
    card = next((c for c in printings if c.get("tcgplayer")), printings[0])

    return _normalize(card)


def _normalize(card: dict) -> dict:
    supertype = card.get("supertype", "")
    subtypes = card.get("subtypes") or []
    type_line = f"{supertype} — {', '.join(subtypes)}" if subtypes else supertype

    price_usd, price_usd_foil = extract_usd_prices(card)
    legalities = card.get("legalities", {}) or {}

    return {
        "name": card.get("name"),
        "inventory_name": card.get("name"),
        # Mirrors card_lookup.py's `primary` shape (image_url/mana_cost/
        # type_line only) so record_card_view()/get_recent_cards() and
        # the Homepage's "Last Viewed" tiles work unchanged for Pokemon —
        # mana_cost has no Pokemon equivalent, left null on purpose.
        "primary": {
            "name": card.get("name"),
            "mana_cost": None,
            "type_line": type_line,
            "image_url": (card.get("images") or {}).get("large") or (card.get("images") or {}).get("small"),
        },
        "hp": card.get("hp"),
        "types": card.get("types") or [],
        "subtypes": subtypes,
        "supertype": supertype,
        "evolves_from": card.get("evolvesFrom"),
        "abilities": card.get("abilities") or [],
        "attacks": card.get("attacks") or [],
        "weaknesses": card.get("weaknesses") or [],
        "resistances": card.get("resistances") or [],
        "retreat_cost": card.get("retreatCost") or [],
        "flavor_text": card.get("flavorText"),
        "set_name": (card.get("set") or {}).get("name"),
        "set_code": ((card.get("set") or {}).get("ptcgoCode") or (card.get("set") or {}).get("id") or "").upper(),
        "collector_number": card.get("number"),
        "rarity": card.get("rarity"),
        "artist": card.get("artist"),
        "price_usd": price_usd,
        "price_usd_foil": price_usd_foil,
        "price_eur": extract_eur_price(card),
        "legalities": {fmt: legalities.get(fmt, "not_legal") for fmt in DISPLAY_FORMATS},
        "external_url": (card.get("tcgplayer") or {}).get("url"),
        "external_url_label": "View on TCGPlayer",
    }
