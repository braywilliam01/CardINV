from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from .models import Inventory, DeckAssignment, DeckMeta
from .pricing import get_collection_value
from .database import get_user_engine, GAMES

MAX_DECK_SHORTCUTS = 3


def get_summary(db: Session) -> dict:
    """
    Stats for the Homepage: total physical cards owned, unique card
    names tracked, number of decks currently holding at least one card,
    and total collection value (delegates to pricing.get_collection_value
    so the dollar figure always matches the Manage Collection tab).
    """
    unique_cards = db.query(Inventory).count()
    total_quantity = db.query(func.coalesce(func.sum(Inventory.total_quantity), 0)).scalar()
    deck_count = (
        db.query(DeckAssignment.deck_name)
        .filter(DeckAssignment.quantity > 0)
        .distinct()
        .count()
    )
    value = get_collection_value(db)

    return {
        "unique_cards": unique_cards,
        "total_quantity": total_quantity,
        "deck_count": deck_count,
        "collection_value_usd": value["total_value_usd"],
    }


def get_everything_summary(username: str) -> dict:
    """
    Combined stats across every game for the 'Everything' homescreen —
    opens each game's per-user database directly (bypassing the
    single-active-game Depends(get_db)) rather than merging tables,
    since each game already lives in its own file. Short-lived,
    manually-closed sessions here since this runs outside a normal
    request's Depends(get_db) lifecycle.
    """
    per_game = {}
    for game in GAMES:
        engine = get_user_engine(username, game)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            per_game[game] = get_summary(db)
        finally:
            db.close()

    return {
        "total_quantity": sum(s["total_quantity"] for s in per_game.values()),
        "unique_cards": sum(s["unique_cards"] for s in per_game.values()),
        "deck_count": sum(s["deck_count"] for s in per_game.values()),
        "collection_value_usd": round(sum(s["collection_value_usd"] for s in per_game.values()), 2),
        "per_game": per_game,
    }


def _meta_sort_key(meta: DeckMeta):
    # A deck touched before DeckMeta existed (or never touched) sorts as
    # "oldest" rather than raising, so it doesn't crowd out real recency.
    return meta.last_modified or datetime.min


def get_deck_shortcuts(db: Session, limit: int = MAX_DECK_SHORTCUTS) -> list[dict]:
    """
    Up to `limit` decks for the Homepage quick-access buttons: favorited
    decks first (most-recently-modified first), then non-favorite decks
    by recency filling any remaining slots. Only considers decks that
    currently hold at least one card — a favorited-but-emptied deck
    isn't a useful shortcut to click into.
    """
    active_deck_names = {
        name
        for (name,) in db.query(DeckAssignment.deck_name)
        .filter(DeckAssignment.quantity > 0)
        .distinct()
        .all()
    }
    if not active_deck_names:
        return []

    metas = db.query(DeckMeta).filter(DeckMeta.deck_name.in_(active_deck_names)).all()
    meta_by_name = {m.deck_name: m for m in metas}

    # Decks with assignments but no DeckMeta row yet (e.g. existing decks
    # from before this feature shipped) — treat as never-favorited/never-touched.
    for name in active_deck_names:
        if name not in meta_by_name:
            meta_by_name[name] = DeckMeta(deck_name=name, is_favorite=False, last_modified=None)

    favorites = sorted(
        (m for m in meta_by_name.values() if m.is_favorite), key=_meta_sort_key, reverse=True
    )
    non_favorites = sorted(
        (m for m in meta_by_name.values() if not m.is_favorite), key=_meta_sort_key, reverse=True
    )

    chosen = (favorites + non_favorites)[:limit]

    return [
        {
            "deck_name": m.deck_name,
            "is_favorite": m.is_favorite,
            "last_modified": m.last_modified.isoformat() if m.last_modified else None,
        }
        for m in chosen
    ]


def get_deck_meta(db: Session, deck_name: str) -> dict:
    meta = db.query(DeckMeta).filter(DeckMeta.deck_name == deck_name).one_or_none()
    return {
        "deck_name": deck_name,
        "is_favorite": meta.is_favorite if meta else False,
        "last_modified": meta.last_modified.isoformat() if meta and meta.last_modified else None,
    }


def set_favorite(db: Session, deck_name: str, is_favorite: bool) -> dict:
    meta = db.query(DeckMeta).filter(DeckMeta.deck_name == deck_name).one_or_none()
    if meta is None:
        meta = DeckMeta(deck_name=deck_name, is_favorite=is_favorite, last_modified=None)
        db.add(meta)
    else:
        meta.is_favorite = is_favorite
    db.commit()
    return get_deck_meta(db, deck_name)
