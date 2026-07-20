from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, CardPrice


def get_available_quantity(db: Session, card_name: str, reserved: dict[str, int]) -> int:
    """
    Available = total_quantity - SUM(deck_assignments.quantity) for this
    card, minus whatever the caller's in-progress request has already
    claimed for it (a running-deduction guard — prevents two lines in
    the same paste, e.g. a typo'd duplicate, from double-claiming the
    same pool).

    Pooled across every printing row for this name (both Inventory and
    DeckAssignment are printing-specific — see models.py — but this
    function deliberately isn't: it answers "is any copy of this card,
    in any printing, available at all", the same question Collection
    Search and Deck Checkout's sync mode ask before deciding whether a
    card can be fulfilled at all. For *which* printing(s) actually get
    drawn from, see get_printing_availability.

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


@dataclass
class PrintingAvailability:
    set_code: str
    collector_number: str
    available: int
    price_usd: float | None  # None sorts last — see get_printing_availability


def _cheapest_first(rows):
    rows.sort(key=lambda r: (r.price_usd is None, r.price_usd if r.price_usd is not None else 0))
    return rows


def get_printing_availability(db: Session, card_name: str) -> list[PrintingAvailability]:
    """
    Every printing of card_name with how many copies of *that exact
    printing* are still available (its own Inventory total minus
    what's already checked out from that same printing across every
    deck), ordered cheapest-known-price first and unpriced printings
    last.

    This is the draw-down order checkout.py uses for an *unpinned*
    line (no "(SET) NUM" in the pasted text — see parser.py): pull
    from the cheapest copies first, so more valuable printings stay on
    the shelf rather than getting tied up in a deck. A pinned line
    skips this entirely and targets one printing directly.
    """
    inv_rows = db.query(Inventory).filter(Inventory.card_name == card_name).all()
    if not inv_rows:
        return []

    checked_out_by_printing = {
        (set_code, collector_number): qty
        for set_code, collector_number, qty in (
            db.query(
                DeckAssignment.set_code, DeckAssignment.collector_number,
                func.sum(DeckAssignment.quantity),
            )
            .filter(DeckAssignment.card_name == card_name)
            .group_by(DeckAssignment.set_code, DeckAssignment.collector_number)
            .all()
        )
    }
    price_by_printing = {
        (p.set_code, p.collector_number): p.price_usd
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }

    result = [
        PrintingAvailability(
            set_code=inv.set_code,
            collector_number=inv.collector_number,
            available=max(0, inv.total_quantity - checked_out_by_printing.get((inv.set_code, inv.collector_number), 0)),
            price_usd=price_by_printing.get((inv.set_code, inv.collector_number)),
        )
        for inv in inv_rows
    ]
    return _cheapest_first(result)


@dataclass
class AssignedPrinting:
    set_code: str
    collector_number: str
    quantity: int
    price_usd: float | None  # None sorts last — see get_printing_availability


def get_assigned_printings(db: Session, card_name: str, deck_name: str) -> list[AssignedPrinting]:
    """
    Every printing of card_name currently assigned to deck_name (with
    quantity > 0), ordered cheapest-known-price first — the drawn-from
    order checkout.py uses for an *unpinned* checkin line: return the
    least valuable copies to the shelf first, keeping pricier printings
    in the deck as long as possible (the mirror image of
    get_printing_availability's checkout-time rule).
    """
    rows = (
        db.query(DeckAssignment)
        .filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.deck_name == deck_name,
            DeckAssignment.quantity > 0,
        )
        .all()
    )
    if not rows:
        return []

    price_by_printing = {
        (p.set_code, p.collector_number): p.price_usd
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }

    result = [
        AssignedPrinting(
            set_code=r.set_code,
            collector_number=r.collector_number,
            quantity=r.quantity,
            price_usd=price_by_printing.get((r.set_code, r.collector_number)),
        )
        for r in rows
    ]
    return _cheapest_first(result)
