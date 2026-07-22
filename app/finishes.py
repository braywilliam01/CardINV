"""
Known finish vocabulary per game -- mirrors the label sets already
latent elsewhere in this codebase (card_lookup.py's "USD"/"Foil" MTG
labels, pokemon_common.py's _USD_PRICE_VARIANTS Pokemon labels), now
promoted from price-chip display labels to first-class printing
identity values (see models.py's Inventory/CardPrice/DeckAssignment).

"" ("unspecified") is deliberately not listed here -- it's the
sentinel for "don't know yet" (see normalize_finish), never a real
finish choice on its own.
"""

MTG_FINISHES = ["Nonfoil", "Foil"]

# Same order as pokemon_common._USD_PRICE_VARIANTS.
POKEMON_FINISHES = [
    "Normal",
    "Holofoil",
    "Reverse Holofoil",
    "1st Edition",
    "1st Edition Holofoil",
    "Unlimited",
    "Unlimited Holofoil",
]

FINISHES_BY_GAME = {"mtg": MTG_FINISHES, "pokemon": POKEMON_FINISHES}

_ALL_KNOWN = {f.lower(): f for finishes in FINISHES_BY_GAME.values() for f in finishes}


def normalize_finish(finish: str | None) -> str:
    """
    Empty string (never None) is the 'unspecified finish' sentinel --
    same convention as inventory_admin._norm_printing for set_code/
    collector_number. Canonicalizes case-insensitively against the
    known vocabulary above (so "holofoil" -> "Holofoil") but does NOT
    reject unknown values -- same latitude _norm_printing gives
    set_code (never validated against a real "known sets" list
    either), since MTG in particular has finishes beyond Nonfoil/Foil
    (Etched, Surge Foil, Galaxy Foil, ...) that this curated list
    deliberately doesn't try to enumerate up front.

    No `game` parameter: inventory_admin.py/checkout.py never receive
    `game` today (isolation is per-database-file -- see database.py),
    and the two vocabularies above don't collide case-insensitively,
    so one flat normalizer is enough. The frontend still only shows
    the relevant subset per active game.
    """
    cleaned = (finish or "").strip()
    if not cleaned:
        return ""
    return _ALL_KNOWN.get(cleaned.lower(), cleaned)
