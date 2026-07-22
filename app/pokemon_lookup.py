import httpx

from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .pokemon_common import (
    POKEMON_API_BASE,
    HEADERS,
    REQUEST_TIMEOUT,
    PokemonRateLimitError,
    extract_all_usd_prices,
)
from .sets_cache import resolve_pokemon_set_id, get_sets

# TCGdex only tracks standard/expanded legality (no "unlimited" field
# in its legal object, unlike the previous provider).
DISPLAY_FORMATS = ["standard", "expanded"]


def _escape_query_value(value: str) -> str:
    """TCGdex's filter values go straight into a query string, not a
    Lucene query — stripping stray quotes just avoids accidentally
    terminating the filter early."""
    return value.replace('"', "").strip()


def _search_cards(client: httpx.Client, name_filter: str) -> list[dict]:
    """Returns brief card objects ({id, localId, name, image}) — TCGdex's
    search endpoint doesn't include pricing or full detail, unlike the
    previous provider's; see _fetch_card_detail for that."""
    resp = client.get(
        f"{POKEMON_API_BASE}/cards",
        params={"name": name_filter},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise PokemonRateLimitError("TCGdex's request limit was reached. Try again in a moment.")
    resp.raise_for_status()
    return resp.json()


def _fetch_card_detail(client: httpx.Client, card_id: str) -> dict | None:
    resp = client.get(f"{POKEMON_API_BASE}/cards/{card_id}", headers=HEADERS, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return None
    if resp.status_code == 429:
        raise PokemonRateLimitError("TCGdex's request limit was reached. Try again in a moment.")
    resp.raise_for_status()
    return resp.json()


def lookup_card(name: str) -> dict | None:
    """
    Looks up one card by name against TCGdex. Its search endpoint takes
    a substring filter (name=like:X) rather than offering true fuzzy
    matching, so this fetches every printing matching that substring,
    then picks the closest name via the same rapidfuzz matching the
    rest of the app already uses. Typo tolerance is real but weaker
    than the Scryfall side — it can't recover from a badly misspelled
    first letter the way Scryfall's ?fuzzy= can.

    A name can have dozens of printings (one row per set); search
    results don't include pricing, so rather than detail-fetching every
    candidate just to compare completeness, this takes the first
    matching printing and fetches its detail directly.
    """
    name = name.strip()
    if not name:
        return None

    safe_name = _escape_query_value(name)
    if not safe_name:
        return None

    with httpx.Client(follow_redirects=True) as client:
        cards = _search_cards(client, f"like:{safe_name}")
        if not cards:
            return None

        candidate_names = sorted({c["name"] for c in cards})
        best_name = find_best_match(name, candidate_names, threshold=DEFAULT_THRESHOLD)
        if best_name is None:
            best_name = candidate_names[0]

        printings = [c for c in cards if c["name"] == best_name]
        # Brief results don't carry pricing, so there's no cheap way to
        # pick "the most complete" printing the way the previous
        # provider let us — but they do carry `image`, which promos and
        # some obscure reprints lack; preferring one that has an image
        # is a free signal (no extra request) that tends to surface a
        # more mainstream printing than just taking whatever's first.
        with_image = [c for c in printings if c.get("image")]
        chosen = with_image[0] if with_image else printings[0]
        card = _fetch_card_detail(client, chosen["id"])

    if card is None:
        return None
    return _normalize(card)


def lookup_card_printing(name: str, set_code: str, collector_number: str) -> dict | None:
    """
    Precise lookup for one exact printing — unlike lookup_card, this
    doesn't fuzzy-match: it resolves set_code to TCGdex's own internal
    set id (see sets_cache.resolve_pokemon_set_id) and fetches that
    exact set+number directly. Falls back to a name+localId search if
    set_code doesn't resolve (e.g. the sets cache hasn't refreshed yet
    on a brand new install). Used by pokemon_pricing's per-printing
    refresh, where the caller already knows exactly which printing they
    own and wants that printing's price, not a best guess.
    """
    name = name.strip()
    collector_number = (collector_number or "").strip()
    if not name or not collector_number:
        return None

    set_id = resolve_pokemon_set_id(set_code) if set_code else None

    with httpx.Client(follow_redirects=True) as client:
        if set_id:
            resp = client.get(
                f"{POKEMON_API_BASE}/sets/{set_id}/{collector_number}", headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 429:
                raise PokemonRateLimitError("TCGdex's request limit was reached. Try again in a moment.")
            if resp.status_code == 404:
                card = None
            else:
                resp.raise_for_status()
                card = resp.json()
        else:
            safe_name = _escape_query_value(name)
            cards = _search_cards(client, f"eq:{safe_name}") if safe_name else []
            matches = [c for c in cards if c.get("localId") == collector_number]
            card = _fetch_card_detail(client, matches[0]["id"]) if matches else None

    if card is None:
        return None
    return _normalize(card)


def _set_code_for(tcgdex_set_id: str) -> str:
    """Reverse of sets_cache.resolve_pokemon_set_id — a card's own
    `set` object only carries TCGdex's internal id, not the PTCGO-style
    code this app displays/stores, so this looks it up from the same
    cached sets list rather than an extra per-card request."""
    for s in get_sets("pokemon"):
        if s.get("id") == tcgdex_set_id:
            return s.get("code") or tcgdex_set_id.upper()
    return tcgdex_set_id.upper()


def _tcgplayer_url(card: dict) -> str | None:
    tcgplayer = (card.get("pricing") or {}).get("tcgplayer") or {}
    for variant in tcgplayer.values():
        if isinstance(variant, dict) and variant.get("productId"):
            return f"https://www.tcgplayer.com/product/{variant['productId']}"
    return None


def _normalize(card: dict) -> dict:
    category = card.get("category", "")
    category_display = "Pokémon" if category == "Pokemon" else category

    # TCGdex gives a single "Stage2"-style string (Pokemon cards) rather
    # than the previous provider's subtypes array — reformatted to
    # "Stage 2" for display, matching how it always looked before.
    stage = card.get("stage") or ""
    stage_display = " ".join(_split_stage(stage)) if stage else ""
    type_line = f"{category_display} — {stage_display}" if stage_display else category_display

    legal = card.get("legal", {}) or {}
    set_id = (card.get("set") or {}).get("id", "")

    image_base = card.get("image")
    image_url = f"{image_base}/high.webp" if image_base else None

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
            "image_url": image_url,
        },
        "hp": card.get("hp"),
        "types": card.get("types") or [],
        "subtypes": [stage_display] if stage_display else [],
        "supertype": category_display,
        "evolves_from": card.get("evolveFrom"),
        "abilities": [
            {"type": a.get("type"), "name": a.get("name"), "text": a.get("effect")}
            for a in (card.get("abilities") or [])
        ],
        "attacks": [
            {
                "cost": atk.get("cost") or [],
                "name": atk.get("name"),
                "text": atk.get("effect"),
                "damage": atk.get("damage"),
            }
            for atk in (card.get("attacks") or [])
        ],
        "weaknesses": card.get("weaknesses") or [],
        "resistances": card.get("resistances") or [],
        # Only the count is ever displayed (see renderPokemonCardBody in
        # app.js) — TCGdex gives that count directly rather than a list
        # of cost pips, so this fabricates a same-length list to match
        # the shape the frontend already expects.
        "retreat_cost": [None] * (card.get("retreat") or 0),
        "flavor_text": card.get("description"),
        "set_name": (card.get("set") or {}).get("name"),
        "set_code": _set_code_for(set_id) if set_id else "",
        "collector_number": card.get("localId"),
        "rarity": card.get("rarity"),
        "artist": card.get("illustrator"),
        "prices": extract_all_usd_prices(card),
        "legalities": {fmt: ("legal" if legal.get(fmt) else "not_legal") for fmt in DISPLAY_FORMATS},
        "external_url": _tcgplayer_url(card),
        "external_url_label": "View on TCGPlayer",
    }


def _split_stage(stage: str) -> list[str]:
    """'Stage2' -> ['Stage', '2']; 'Basic' -> ['Basic']."""
    for i, ch in enumerate(stage):
        if ch.isdigit():
            return [stage[:i], stage[i:]]
    return [stage]
