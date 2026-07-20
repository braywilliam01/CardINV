from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment


def get_available_quantity(db: Session, card_name: str, reserved: dict[str, int]) -> int:
    """
    Available = total_quantity - SUM(deck_assignments.quantity) for this
    card, minus whatever the caller's in-progress request has already
    claimed for it (a running-deduction guard — prevents two lines in
    the same paste, e.g. a typo'd duplicate, from double-claiming the
    same pool).

    Deck assignments aren't printing-specific (see models.py), so
    availability is summed across every printing row for this name —
    a card is "available" if any printing of it is on the shelf.

    Shared by search.py (Collection Search) and checkout.py (Deck
    Checkout, both additive and sync modes) so this math can't quietly
    drift between them — it used to be defined identically in both
    places.
    """
    total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(Inventory.card_name == card_name)
        .scalar()
    )
    if not total:
        return 0

    checked_out = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name)
        .all()
    )
    total_checked_out = sum(a.quantity for a in checked_out)
    already_claimed_this_request = reserved.get(card_name, 0)
    return max(0, total - total_checked_out - already_claimed_this_request)
