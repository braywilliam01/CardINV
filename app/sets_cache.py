import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .database import DATA_DIR
from .pokemon_common import HEADERS as POKEMON_HEADERS, POKEMON_API_BASE

logger = logging.getLogger("mtg_inventory.sets_cache")

SCRYFALL_SETS_URL = "https://api.scryfall.com/sets"
SCRYFALL_HEADERS = {
    "User-Agent": "MTG-Inventory-Manager/1.0 (personal collection tool)",
    "Accept": "application/json",
}

REFERENCE_DIR = DATA_DIR / "reference"
REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

# Weekly — same cadence as the price-refresh cron. Set lists barely
# change (a new set every few weeks at most), so this is deliberately
# lazy: refreshed on access if stale, not on a background schedule —
# the app has no in-process scheduler, prices work the same way via
# an external cron hitting an endpoint, not a timer inside the app.
REFRESH_INTERVAL_SECONDS = 7 * 24 * 60 * 60

_cache: dict[str, list[dict]] = {}
_last_loaded: dict[str, float] = {}


def _cache_path(game: str) -> Path:
    return REFERENCE_DIR / f"{game}_sets.json"


def _fetch_mtg_sets() -> list[dict]:
    """Set codes are uppercased to match what card_lookup.py already
    puts in card records (card.get("set_code")), so a set picked from
    this list lines up with real card data."""
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(SCRYFALL_SETS_URL, headers=SCRYFALL_HEADERS, timeout=30)
        resp.raise_for_status()
    data = resp.json().get("data", [])
    return [
        {"code": (s.get("code") or "").upper(), "name": s.get("name"), "released_at": s.get("released_at")}
        for s in data
        if not s.get("digital")  # this is a physical-collection app — skip Arena/MTGO-only sets
    ]


def _fetch_one_pokemon_set_detail(client: httpx.Client, s: dict) -> dict | None:
    set_id = s.get("id")
    if not set_id:
        return None
    try:
        detail_resp = client.get(f"{POKEMON_API_BASE}/sets/{set_id}", headers=POKEMON_HEADERS, timeout=15)
        detail_resp.raise_for_status()
        detail = detail_resp.json()
    except httpx.HTTPError:
        logger.warning("Skipping Pokemon set '%s' — detail fetch failed", set_id)
        return None

    code = (detail.get("tcgOnline") or (detail.get("abbreviation") or {}).get("official") or set_id).upper()
    return {
        "code": code,
        "name": detail.get("name") or s.get("name"),
        "released_at": detail.get("releaseDate"),
        "id": set_id,
    }


def _fetch_pokemon_sets() -> list[dict]:
    """
    TCGdex's set list endpoint doesn't include the official PTCGO-style
    code (e.g. "DAA" for Darkness Ablaze) that this app's set_code has
    always used — matching what pokemontcg.io's ptcgoCode gave, since
    that's a Pokemon Company-standardized abbreviation, not something
    provider-specific — only the per-set detail endpoint does (under
    tcgOnline/abbreviation.official). So this fetches the list first,
    then one detail request per set to resolve each one's real code —
    ~220 sets. Sequential (with a polite delay between each) took
    ~30s in practice, which is too slow for this to ever run inline on
    a user's first Pokemon search of the week (see get_sets — this
    only refreshes when the weekly cache is stale). A small thread
    pool brings that down to a few seconds; still bounded and nowhere
    near enough concurrency to look like abuse.

    Each entry's `id` field is TCGdex's own internal set id (e.g.
    "swsh3") — pokemon_lookup.py needs it to resolve a stored set_code
    back to the right set for an exact-printing lookup, since TCGdex's
    card search can only filter by internal set id, not by code.
    """
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(f"{POKEMON_API_BASE}/sets", headers=POKEMON_HEADERS, timeout=30)
        resp.raise_for_status()
        set_list = resp.json()

        sets: list[dict] = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            for result in pool.map(lambda s: _fetch_one_pokemon_set_detail(client, s), set_list):
                if result is not None:
                    sets.append(result)
    return sets


_FETCHERS = {"mtg": _fetch_mtg_sets, "pokemon": _fetch_pokemon_sets}


def _load_from_disk(game: str) -> list[dict] | None:
    path = _cache_path(game)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_to_disk(game: str, sets: list[dict]) -> None:
    _cache_path(game).write_text(json.dumps(sets))


def get_sets(game: str, force_refresh: bool = False) -> list[dict]:
    """
    Returns the cached set list for `game`. Set lists are tiny
    (~1000 for MTG, ~200 for Pokemon) compared to card/price data, so
    this is just an in-memory list backed by a flat JSON file, not a
    database table — refetched from Scryfall/TCGdex only when missing,
    stale, or force_refresh is requested.
    """
    now = time.time()

    if not force_refresh:
        if game in _cache and now - _last_loaded.get(game, 0) < REFRESH_INTERVAL_SECONDS:
            return _cache[game]

        if game not in _cache:
            disk = _load_from_disk(game)
            if disk is not None:
                disk_age = now - _cache_path(game).stat().st_mtime
                _cache[game] = disk
                _last_loaded[game] = now - disk_age
                if disk_age < REFRESH_INTERVAL_SECONDS:
                    return disk

    fetcher = _FETCHERS.get(game)
    if fetcher is None:
        raise ValueError(f"Unknown game: '{game}'")

    try:
        sets = fetcher()
    except Exception:
        logger.exception("Failed to refresh %s sets — falling back to cached data if any", game)
        if game in _cache:
            return _cache[game]
        raise

    _cache[game] = sets
    _last_loaded[game] = now
    _save_to_disk(game, sets)
    logger.info("Refreshed %s sets cache: %d sets", game, len(sets))
    return sets


def search_sets(game: str, query: str, limit: int = 20) -> list[dict]:
    """Substring match on name or code, for the autocomplete endpoint
    — set lists are small enough that this doesn't need fuzzy
    matching or an index."""
    query = query.strip().lower()
    sets = get_sets(game)
    if not query:
        return sets[:limit]
    matches = [
        s for s in sets
        if query in (s.get("name") or "").lower() or query in (s.get("code") or "").lower()
    ]
    return matches[:limit]


def resolve_pokemon_set_id(set_code: str) -> str | None:
    """
    Maps a stored set_code (the PTCGO-style code, e.g. "DAA" — see
    _fetch_pokemon_sets) to TCGdex's own internal set id (e.g. "swsh3"),
    which is what its card-search API actually needs to filter by (it
    can't filter on the PTCGO code directly). Used by
    pokemon_lookup.lookup_card_printing for exact-printing lookups
    against printings already in a user's inventory, most of which
    predate this app's move to TCGdex and only have the PTCGO code
    on file.
    """
    set_code = (set_code or "").strip().upper()
    if not set_code:
        return None
    for s in get_sets("pokemon"):
        if s.get("code") == set_code:
            return s.get("id")
    return None
