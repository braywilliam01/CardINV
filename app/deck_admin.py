from sqlalchemy.orm import Session

from .models import DeckAssignment, DeckMeta


class DeckNotFoundError(Exception):
    def __init__(self, deck_name: str):
        super().__init__(f"Deck '{deck_name}' not found.")


class DuplicateDeckError(Exception):
    def __init__(self, deck_name: str):
        super().__init__(f"A deck named '{deck_name}' already exists.")


def _deck_exists(db: Session, deck_name: str) -> bool:
    has_assignment = db.query(DeckAssignment).filter(DeckAssignment.deck_name == deck_name).first() is not None
    has_meta = db.query(DeckMeta).filter(DeckMeta.deck_name == deck_name).first() is not None
    return has_assignment or has_meta


def rename_deck(db: Session, old_name: str, new_name: str) -> str:
    """
    Renames a deck by bulk-updating every DeckAssignment row's
    deck_name and carrying its DeckMeta (favorite status, last-modified
    history) over to the new name — a rename shouldn't reset either.
    Blocked if new_name collides with an existing deck; this isn't a
    merge operation.
    """
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("New deck name cannot be empty.")
    if new_name == old_name:
        return new_name

    if not _deck_exists(db, old_name):
        raise DeckNotFoundError(old_name)
    if _deck_exists(db, new_name):
        raise DuplicateDeckError(new_name)

    db.query(DeckAssignment).filter(DeckAssignment.deck_name == old_name).update(
        {DeckAssignment.deck_name: new_name}, synchronize_session=False
    )

    meta = db.query(DeckMeta).filter(DeckMeta.deck_name == old_name).one_or_none()
    if meta is not None:
        db.add(DeckMeta(deck_name=new_name, is_favorite=meta.is_favorite, last_modified=meta.last_modified))
        db.delete(meta)

    db.commit()
    return new_name


def delete_deck(db: Session, deck_name: str) -> int:
    """
    Checks every card in `deck_name` back in (restoring inventory
    availability — basics just disappear, since they were never
    inventory-tracked) and removes the deck's DeckMeta row, so it drops
    out of the deck selector, Homepage shortcuts, and favorites.
    Returns how many total cards were checked in.
    """
    deck_name = deck_name.strip()
    if not _deck_exists(db, deck_name):
        raise DeckNotFoundError(deck_name)

    assignments = db.query(DeckAssignment).filter(DeckAssignment.deck_name == deck_name).all()
    total_checked_in = sum(a.quantity for a in assignments)
    for a in assignments:
        db.delete(a)

    meta = db.query(DeckMeta).filter(DeckMeta.deck_name == deck_name).one_or_none()
    if meta is not None:
        db.delete(meta)

    db.commit()
    return total_checked_in
