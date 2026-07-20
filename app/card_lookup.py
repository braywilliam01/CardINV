import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import CardSearchHistory
from .pricing import HEADERS, SCRYFALL_NAMED_URL, PER_CARD_DELAY_SECONDS

# Curated subset of Scryfall's ~18 tracked formats — the ones players
# actually check, rather than a wall of badges for formats like
# "oldschool" or "predh".
DISPLAY_FORMATS = ["standard", "pioneer", "modern", "legacy", "vintage", "commander", "pauper"]

RECENT_CARDS_LIMIT = 3


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


def lookup_card(name: str) -> dict | None:
    """
    Fuzzy-looks up one card by name via Scryfall's /cards/named endpoint
    (same fuzzy-match Scryfall does server-side, so no local matching
    needed) and returns a normalized dict of everything the Card Search
    view displays — image, oracle text, prices, legalities, etc.

    Double-faced cards (transform/MDFC) carry their printed info under
    `card_faces` instead of at the top level; those are normalized into
    `faces` (a list of both sides) so the frontend doesn't need to know
    the difference. Returns None if Scryfall has no match at all.
    """
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, headers=HEADERS, timeout=15)
    time.sleep(PER_CARD_DELAY_SECONDS)  # respect Scryfall's rate-limit guidance

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    card = resp.json()

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

    prices = card.get("prices", {}) or {}
    legalities = card.get("legalities", {}) or {}

    return {
        "name": card.get("name"),
        "faces": faces,  # None for single-faced cards, else [front, back, ...]
        "primary": primary,  # top-level info, or the front face for double-faced cards
        "set_name": card.get("set_name"),
        "set_code": (card.get("set") or "").upper(),
        "collector_number": card.get("collector_number"),
        "rarity": card.get("rarity"),
        "artist": card.get("artist"),
        "price_usd": prices.get("usd"),
        "price_usd_foil": prices.get("usd_foil"),
        "legalities": {fmt: legalities.get(fmt, "not_legal") for fmt in DISPLAY_FORMATS},
        "scryfall_uri": card.get("scryfall_uri"),
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
