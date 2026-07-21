import re
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import CardSearchHistory
from .pricing import HEADERS, SCRYFALL_NAMED_URL, SCRYFALL_CARDS_BASE, PER_CARD_DELAY_SECONDS

# Curated subset of Scryfall's ~18 tracked formats — the ones players
# actually check, rather than a wall of badges for formats like
# "oldschool" or "predh".
DISPLAY_FORMATS = ["standard", "pioneer", "modern", "legacy", "vintage", "commander", "pauper"]

RECENT_CARDS_LIMIT = 3

# Matches a trailing "SET NUMBER" printing reference in Card Search's
# free-text input, e.g. "CLB 304" — a set code (letters and/or digits;
# some real codes are alphanumeric, e.g. "40k") followed by whitespace
# and a collector number. The number MUST start with a digit — without
# that, a plain two-word card name with no comma (e.g. "Sol Ring") would
# itself match "SET NUMBER" (SOL + Ring) and get misparsed as a printing
# reference instead of searched by name. An optional leading "#" is
# tolerated on the number, since the UI's own help text shows the format
# as "SET #" and users understandably type that "#" literally.
_PRINTING_QUERY = re.compile(r"^([A-Za-z0-9]{2,5})\s+#?(\d+\S*)$")


def _parse_search_query(query: str) -> tuple[str, str, str]:
    """
    Parses Card Search's input for an optional exact-printing reference,
    so a search can pin one specific card instead of relying on
    Scryfall's fuzzy name match:
      "Lightning Bolt, CLB 304"  -> ("Lightning Bolt", "CLB", "304")
      "CLB 304"                  -> ("", "CLB", "304")
      "Lightning Bolt"           -> ("Lightning Bolt", "", "")  (fuzzy, as before)

    set_code/collector_number are "" when no printing reference was
    recognized. Many real card names contain a comma of their own (e.g.
    "Urza, Lord High Artificer") — splits on the *last* comma (so a
    printing suffix still works after one of those, e.g. "Jhoira,
    Weatherlight Captain, CLB 5") and, if what follows doesn't actually
    parse as "SET NUMBER", assumes the comma belongs to the name itself
    and returns the *whole* original query untouched rather than
    truncating it.
    """
    query = query.strip()

    if "," in query:
        name_part, _, printing_part = query.rpartition(",")
        match = _PRINTING_QUERY.match(printing_part.strip())
        if match:
            return name_part.strip(), match.group(1).upper(), match.group(2)
        return query, "", ""

    match = _PRINTING_QUERY.match(query)
    if match:
        return "", match.group(1).upper(), match.group(2)

    return query, "", ""


def _face_info(face: dict) -> dict:
    return {
        "name": face.get("name"),
        "mana_cost": face.get("mana_cost"),
        "type_line": face.get("type_line"),
        "oracle_text": face.get("oracle_text"),
        "power": face.get("power"),
        "toughness": face.get("toughness"),
        "loyalty": face.get("loyalty"),
        "flavor_text": face.get("flavor_text"),
        "image_url": (face.get("image_uris") or {}).get("normal"),
    }


def lookup_card(query: str) -> dict | None:
    """
    Looks up one card from Card Search's free-text input.

    If the query names an exact printing — a trailing "SET NUMBER",
    optionally after "Card Name, " (see _parse_search_query) — fetches
    that precise printing via Scryfall's /cards/{set}/{number} endpoint.
    No fuzzy matching involved there: set+number alone already uniquely
    identifies one specific printing, unlike a bare name. Falls back to
    fuzzy name matching via /cards/named (Scryfall's own fuzzy-match,
    same as always) when the query doesn't parse as a printing
    reference, or when a named printing 404s but a name was also given
    (e.g. a typo'd collector number) — better to surface *a* match than
    a hard failure on a near-miss. Returns None if Scryfall has no
    match at all.
    """
    name, set_code, collector_number = _parse_search_query(query)

    if set_code and collector_number:
        card = _fetch_by_printing(set_code, collector_number)
        if card is not None:
            return _normalize_card(card)
        if not name:
            return None
        # Fall through to the fuzzy name search below using just `name`.

    if not name:
        return None

    card = _fetch_by_name(name)
    if card is None:
        return None
    return _normalize_card(card)


def _fetch_by_name(name: str) -> dict | None:
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, headers=HEADERS, timeout=15)
    time.sleep(PER_CARD_DELAY_SECONDS)  # respect Scryfall's rate-limit guidance

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _fetch_by_printing(set_code: str, collector_number: str) -> dict | None:
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(
            f"{SCRYFALL_CARDS_BASE}/{set_code.lower()}/{collector_number}", headers=HEADERS, timeout=15
        )
    time.sleep(PER_CARD_DELAY_SECONDS)  # respect Scryfall's rate-limit guidance

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _normalize_card(card: dict) -> dict:
    """
    Returns a normalized dict of everything the Card Search view
    displays — image, oracle text, prices, legalities, etc. — from a
    raw Scryfall card object, however it was fetched.

    Double-faced cards (transform/MDFC) carry their printed info under
    `card_faces` instead of at the top level; those are normalized into
    `faces` (a list of both sides) so the frontend doesn't need to know
    the difference.
    """
    raw_faces = card.get("card_faces") or []
    # Transform / modal-DFC / reversible cards: each face is visually a
    # separate card with its own image. Split / Adventure cards also
    # have card_faces, but only one physical image — at the top level,
    # not per-face — so that's the signal for which shape we're in.
    faces_have_own_images = bool(raw_faces) and all(f.get("image_uris") for f in raw_faces)

    if faces_have_own_images:
        faces = [_face_info(f) for f in raw_faces]
        primary = faces[0]
    else:
        faces = None
        primary = _face_info(card)
        if raw_faces:
            # Split/Adventure: mana_cost and type_line are already
            # Scryfall-combined ("X // Y") at the top level, but
            # oracle_text is only present per-face — stitch it together.
            primary["oracle_text"] = "\n\n".join(
                f"{f.get('name', '')}: {f.get('oracle_text', '')}" for f in raw_faces if f.get("oracle_text")
            )
            if not primary.get("flavor_text"):
                primary["flavor_text"] = raw_faces[0].get("flavor_text")

    raw_prices = card.get("prices", {}) or {}
    legalities = card.get("legalities", {}) or {}

    # Card Search's price display shows every distinct USD variant a
    # printing has (e.g. a Pokemon card's Holofoil/Reverse Holofoil are
    # genuinely different market prices) — Scryfall only ever has these
    # two for MTG, but the frontend renders whatever's in this list
    # generically, same as Pokemon's normalize() below.
    prices = []
    if raw_prices.get("usd") is not None:
        prices.append({"label": "USD", "value": float(raw_prices["usd"])})
    if raw_prices.get("usd_foil") is not None:
        prices.append({"label": "Foil", "value": float(raw_prices["usd_foil"])})

    # Inventory (and ManaBox exports) track a double-faced or split/
    # adventure card under its front face's name alone, never Scryfall's
    # combined "X // Y" — so this is deliberately always the front face,
    # not `primary["name"]` (which is the combined name for split/
    # adventure cards, since those don't hit the faces_have_own_images
    # branch above).
    inventory_name = raw_faces[0].get("name") if raw_faces else card.get("name")

    return {
        "name": card.get("name"),
        "inventory_name": inventory_name,
        "faces": faces,  # None for single-faced cards, else [front, back, ...]
        "primary": primary,  # top-level info, or the front face for double-faced cards
        "set_name": card.get("set_name"),
        "set_code": (card.get("set") or "").upper(),
        "collector_number": card.get("collector_number"),
        "rarity": card.get("rarity"),
        "artist": card.get("artist"),
        "prices": prices,
        "legalities": {fmt: legalities.get(fmt, "not_legal") for fmt in DISPLAY_FORMATS},
        "external_url": card.get("scryfall_uri"),
        "external_url_label": "View on Scryfall",
    }


def record_card_view(db: Session, card: dict) -> None:
    """
    Upserts `card` into the Card Search history (bumping its viewed_at
    if already present) and trims down to the most recent
    RECENT_CARDS_LIMIT distinct cards, powering the Homepage's
    "Last Viewed" tiles.
    """
    name = card.get("name")
    if not name:
        return

    primary = card.get("primary") or {}

    existing = db.query(CardSearchHistory).filter(CardSearchHistory.card_name == name).one_or_none()
    if existing is None:
        existing = CardSearchHistory(card_name=name)
        db.add(existing)

    existing.image_url = primary.get("image_url")
    existing.mana_cost = primary.get("mana_cost")
    existing.type_line = primary.get("type_line")
    existing.viewed_at = datetime.now(timezone.utc)

    # This session is autoflush=False, so the update above wouldn't
    # otherwise be visible to the SELECT below — flush explicitly.
    db.flush()

    # Fetch-then-delete-in-Python rather than an SQL OFFSET-without-LIMIT
    # (invalid in SQLite) — this table only ever holds a handful of rows,
    # so there's no real cost to it.
    all_rows = db.query(CardSearchHistory).order_by(CardSearchHistory.viewed_at.desc()).all()
    for row in all_rows[RECENT_CARDS_LIMIT:]:
        db.delete(row)

    db.commit()


def get_recent_cards(db: Session) -> list[dict]:
    rows = (
        db.query(CardSearchHistory)
        .order_by(CardSearchHistory.viewed_at.desc())
        .limit(RECENT_CARDS_LIMIT)
        .all()
    )
    return [
        {
            "card_name": r.card_name,
            "image_url": r.image_url,
            "mana_cost": r.mana_cost,
            "type_line": r.type_line,
            "viewed_at": r.viewed_at.isoformat() if r.viewed_at else None,
        }
        for r in rows
    ]
