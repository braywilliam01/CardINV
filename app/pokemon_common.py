import os

import httpx

POKEMON_API_BASE = "https://api.pokemontcg.io/v2"

# A single Card Search lookup can make up to two sequential requests
# (exact match, then a wildcard fallback) — keeping each request's
# worst case well under 10s keeps the whole endpoint responsive even
# when pokemontcg.io itself is slow, instead of tying up a request
# thread for up to a minute.
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)


class PokemonRateLimitError(Exception):
    """
    Raised when pokemontcg.io responds 429 — the keyless tier allows
    only 30 requests/min (1,000/day), which normal Card Search usage
    can exhaust. Kept distinct from a generic failure so callers can
    surface a clear, fast "try again shortly" message instead of a
    vague "failed to reach" one.
    """

HEADERS = {
    "User-Agent": "MTG-Inventory-Manager/1.0 (personal collection tool)",
    "Accept": "application/json",
}

# Optional — raises the rate limit from 1,000/day (30/min) to 20,000/day.
# Free to request at https://dev.pokemontcg.io. Not required: a full
# ~82-page price refresh comfortably fits under the keyless limit.
_API_KEY = os.environ.get("POKEMONTCG_API_KEY")
if _API_KEY:
    HEADERS["X-Api-Key"] = _API_KEY


def extract_usd_prices(card: dict) -> tuple[float | None, float | None]:
    """
    pokemontcg.io's tcgplayer.prices is keyed by print variant
    ("normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil",
    etc.) rather than the flat usd/usd_foil pair Scryfall gives us —
    picks a sensible non-foil-ish variant for price_usd and a foil-ish
    one for price_usd_foil, falling back to whatever's actually present
    for oddly-printed cards (promos, etc.) that don't have a "normal".
    """
    tcgplayer = card.get("tcgplayer") or {}
    prices = tcgplayer.get("prices") or {}
    if not prices:
        return None, None

    def market(variant: str) -> float | None:
        v = prices.get(variant)
        return v.get("market") if v else None

    price_usd = market("normal") or market("unlimited") or market("1stEdition")
    price_usd_foil = market("holofoil") or market("reverseHolofoil") or market("1stEditionHolofoil")

    if price_usd is None and price_usd_foil is None:
        first_variant = next(iter(prices.values()), {})
        price_usd = first_variant.get("market")

    return price_usd, price_usd_foil


# Display order/labels for Card Search's popup, which — unlike the
# inventory DB (a single price_usd/price_usd_foil pair per printing,
# see extract_usd_prices above) — shows every distinct USD variant a
# printing actually has, since Holofoil and Reverse Holofoil are
# genuinely different market prices, not interchangeable "foil".
_USD_PRICE_VARIANTS = [
    ("normal", "Normal"),
    ("holofoil", "Holofoil"),
    ("reverseHolofoil", "Reverse Holofoil"),
    ("1stEditionNormal", "1st Edition"),
    ("1stEditionHolofoil", "1st Edition Holofoil"),
    ("unlimited", "Unlimited"),
    ("1stEditionUnlimited", "1st Edition Unlimited"),
]


def extract_all_usd_prices(card: dict) -> list[dict]:
    tcgplayer = card.get("tcgplayer") or {}
    prices = tcgplayer.get("prices") or {}

    result = []
    for key, label in _USD_PRICE_VARIANTS:
        variant = prices.get(key)
        market = variant.get("market") if variant else None
        if market is not None:
            result.append({"label": label, "value": market})
    return result
