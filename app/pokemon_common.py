import os

POKEMON_API_BASE = "https://api.pokemontcg.io/v2"

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


def extract_eur_price(card: dict) -> float | None:
    cardmarket = card.get("cardmarket") or {}
    prices = cardmarket.get("prices") or {}
    return prices.get("averageSellPrice") or prices.get("trendPrice")
