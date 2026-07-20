BASIC_LAND_NAMES = ["Plains", "Island", "Swamp", "Mountain", "Forest"]

_BASIC_LAND_LOOKUP = {name.lower(): name for name in BASIC_LAND_NAMES}


def is_basic_land(card_name: str) -> bool:
    """Case-insensitive check against the 5 basic land types."""
    return card_name.strip().lower() in _BASIC_LAND_LOOKUP


def canonical_basic_land_name(card_name: str) -> str | None:
    """Returns the properly-capitalized basic land name, or None if not a basic."""
    return _BASIC_LAND_LOOKUP.get(card_name.strip().lower())
