import json
import logging
import time
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


def _fetch_pokemon_sets() -> list[dict]:
    """Mirrors pokemon_lookup.py's set_code choice: ptcgoCode when
    present, else id, uppercased."""
    sets: list[dict] = []
    with httpx.Client(follow_redirects=True) as client:
        page = 1
        while True:
            resp = client.get(
                f"{POKEMON_API_BASE}/sets",
                params={"page": page, "pageSize": 250},
                headers=POKEMON_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data", [])
            if not data:
                break
            for s in data:
                code = (s.get("ptcgoCode") or s.get("id") or "").upper()
                sets.append({"code": code, "name": s.get("name"), "released_at": s.get("releaseDate")})
            if len(data) < 250:
                break
            page += 1
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
    database table — refetched from Scryfall/pokemontcg.io only when
    missing, stale, or force_refresh is requested.
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
