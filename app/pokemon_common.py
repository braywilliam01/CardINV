import httpx

# pokemontcg.io (the previous provider here) is still technically live,
# but the team behind it has shifted focus entirely to Scrydex, a paid
# successor with no free tier — and its reliability had already
# degraded noticeably (frequent 500s) before this migration. TCGdex
# (tcgdex.dev) is used instead: open source, no API key, no published
# hard rate limit, and includes TCGplayer/Cardmarket pricing directly
# on every card response.
POKEMON_API_BASE = "https://api.tcgdex.net/v2/en"

# A single Card Search lookup can make up to two sequential requests
# (a name search, then a detail fetch for the chosen printing) —
# keeping each request's worst case well under 10s keeps the whole
# endpoint responsive even if TCGdex itself is slow, instead of tying
# up a request thread for a long time.
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)

HEADERS = {
    "User-Agent": "MTG-Inventory-Manager/1.0 (personal collection tool)",
    "Accept": "application/json",
}


class PokemonRateLimitError(Exception):
    """
    Raised if TCGdex ever responds 429. TCGdex publishes no hard rate
    limit ("please be considerate" is the only stated guidance), so
    this is defensive rather than an expected/documented case — kept
    distinct from a generic failure so callers can surface a clear
    "try again shortly" message instead of a vague "failed to reach"
    one, same as the app's own login/card-lookup rate limiter does.
    """


def extract_usd_prices(card: dict) -> tuple[float | None, float | None]:
    """
    TCGdex's card.pricing.tcgplayer is keyed by print variant
    ("normal", "holofoil", "reverse-holofoil", "1st-edition-holofoil",
    etc.) rather than the flat usd/usd_foil pair Scryfall gives us —
    picks a sensible non-foil-ish variant for price_usd and a foil-ish
    one for price_usd_foil, falling back to whatever's actually present
    for oddly-printed cards (promos, etc.) that don't have a "normal".
    """
    tcgplayer = (card.get("pricing") or {}).get("tcgplayer") or {}
    if not tcgplayer:
        return None, None

    def market(variant: str) -> float | None:
        v = tcgplayer.get(variant)
        return v.get("marketPrice") if isinstance(v, dict) else None

    price_usd = market("normal") or market("unlimited") or market("1st-edition")
    price_usd_foil = market("holofoil") or market("reverse-holofoil") or market("1st-edition-holofoil")

    if price_usd is None and price_usd_foil is None:
        # unit/updated are metadata, not a price variant — skip them
        # when falling back to "whatever's actually here".
        variant_values = [v for k, v in tcgplayer.items() if isinstance(v, dict)]
        if variant_values:
            price_usd = variant_values[0].get("marketPrice")

    return price_usd, price_usd_foil


# Display order/labels for Card Search's popup, which — unlike the
# inventory DB (a single price_usd/price_usd_foil pair per printing,
# see extract_usd_prices above) — shows every distinct USD variant a
# printing actually has, since Holofoil and Reverse Holofoil are
# genuinely different market prices, not interchangeable "foil".
_USD_PRICE_VARIANTS = [
    ("normal", "Normal"),
    ("holofoil", "Holofoil"),
    ("reverse-holofoil", "Reverse Holofoil"),
    ("1st-edition", "1st Edition"),
    ("1st-edition-holofoil", "1st Edition Holofoil"),
    ("unlimited", "Unlimited"),
    ("unlimited-holofoil", "Unlimited Holofoil"),
]


def extract_all_usd_prices(card: dict) -> list[dict]:
    tcgplayer = (card.get("pricing") or {}).get("tcgplayer") or {}

    result = []
    for key, label in _USD_PRICE_VARIANTS:
        variant = tcgplayer.get(key)
        market = variant.get("marketPrice") if isinstance(variant, dict) else None
        if market is not None:
            result.append({"label": label, "value": market})
    return result
