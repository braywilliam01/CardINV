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
    finish: str
    available: int
    price_usd: float | None  # None sorts last — see get_printing_availability


def _cheapest_first(rows):
    rows.sort(key=lambda r: (r.price_usd is None, r.price_usd if r.price_usd is not None else 0))
    return rows


def get_printing_availability(db: Session, card_name: str) -> list[PrintingAvailability]:
    """
    Every printing (and finish) of card_name with how many copies are
    still available (the Inventory total for that printing+finish,
    summed across every location — decks are location-blind, same
    reasoning as checkout.py never threading a location through a
    pin — minus what's already checked out across every deck), ordered
    cheapest-known-price first and unpriced rows last.

    This is the draw-down order checkout.py uses for an *unpinned*
    line (no "(SET) NUM" in the pasted text — see parser.py): pull
    from the cheapest copies first, so more valuable printings stay on
    the shelf rather than getting tied up in a deck. A pinned line
    skips this entirely and targets one printing directly (see
    checkout.py — pinning still doesn't specify finish, so it only
    ever targets the unspecified-finish row of that printing).
    """
    inv_rows = db.query(Inventory).filter(Inventory.card_name == card_name).all()
    if not inv_rows:
        return []

    # Sum Inventory rows by (set_code, collector_number, finish) --
    # the same printing+finish can now be split across several
    # location rows (see models.py), but checkout stays location-blind,
    # so this function's granularity is unchanged: one entry per
    # printing+finish, location-summed.
    total_by_printing: dict[tuple[str, str, str], int] = {}
    for inv in inv_rows:
        key = (inv.set_code, inv.collector_number, inv.finish)
        total_by_printing[key] = total_by_printing.get(key, 0) + inv.total_quantity

    checked_out_by_printing = {
        (set_code, collector_number, finish): qty
        for set_code, collector_number, finish, qty in (
            db.query(
                DeckAssignment.set_code, DeckAssignment.collector_number, DeckAssignment.finish,
                func.sum(DeckAssignment.quantity),
            )
            .filter(DeckAssignment.card_name == card_name)
            .group_by(DeckAssignment.set_code, DeckAssignment.collector_number, DeckAssignment.finish)
            .all()
        )
    }
    price_by_printing = {
        (p.set_code, p.collector_number, p.finish): p.price_usd
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }

    result = [
        PrintingAvailability(
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            available=max(0, total - checked_out_by_printing.get((set_code, collector_number, finish), 0)),
            price_usd=price_by_printing.get((set_code, collector_number, finish)),
        )
        for (set_code, collector_number, finish), total in total_by_printing.items()
    ]
    return _cheapest_first(result)


@dataclass
class AssignedPrinting:
    set_code: str
    collector_number: str
    finish: str
    quantity: int
    price_usd: float | None  # None sorts last — see get_printing_availability


def get_assigned_printings(db: Session, card_name: str, deck_name: str) -> list[AssignedPrinting]:
    """
    Every printing (and finish) of card_name currently assigned to
    deck_name (with quantity > 0), ordered cheapest-known-price first
    — the drawn-from order checkout.py uses for an *unpinned* checkin
    line: return the least valuable copies to the shelf first, keeping
    pricier printings in the deck as long as possible (the mirror
    image of get_printing_availability's checkout-time rule).
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
        (p.set_code, p.collector_number, p.finish): p.price_usd
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }

    result = [
        AssignedPrinting(
            set_code=r.set_code,
            collector_number=r.collector_number,
            finish=r.finish,
            quantity=r.quantity,
            price_usd=price_by_printing.get((r.set_code, r.collector_number, r.finish)),
        )
        for r in rows
    ]
    return _cheapest_first(result)


@dataclass
class LocationAvailability:
    set_code: str
    collector_number: str
    finish: str
    location: str
    available: int
    price_usd: float | None  # None sorts last — see get_printing_availability


def get_location_availability(db: Session, card_name: str) -> list[LocationAvailability]:
    """
    Every (printing, finish, location) row of card_name with how many
    copies are actually available there — the location-granular
    counterpart to get_printing_availability, which deliberately stays
    location-blind for checkout's sake (decks have no location
    syntax). This one powers Collection Search's pick list (see
    search.py), which needs to know *where* the available copies of a
    card actually are.

    Checked-out totals are only known per printing+finish, never per
    location (decks are location-blind — see models.py's Inventory
    docstring), so this heuristically assumes checked-out copies came
    from the "least specific" location first: within each
    printing+finish group, rows are ordered "" (not yet assigned)
    first, then alphabetically, and the group's checked-out total is
    subtracted cumulatively down that order. This is a read-only
    preview (nothing is committed here — nothing is checked out by
    calling this), so a heuristic is fine; it's the same drain-order
    convention already used by inventory_admin.assign_printing's
    source-side drain and bulk_remove_cards.

    The *returned* order is different from that subtraction order, and
    deliberately so — don't conflate the two: real (non-empty)
    locations sort first (alphabetically), "" (not yet assigned) sorts
    last, with cheapest-known-price as a tiebreak within the same
    location. Unlike get_printing_availability's cheapest-first bias
    (which matters because checkout commits cards to a deck, so
    protecting valuable printings is the priority), a pick list commits
    nothing — what matters is producing a maximally *actionable* list,
    so a row with a real location should be preferred over the
    unassigned-location bucket whenever both could supply the same
    card.
    """
    inv_rows = db.query(Inventory).filter(Inventory.card_name == card_name).all()
    if not inv_rows:
        return []

    by_printing: dict[tuple[str, str, str], list[Inventory]] = {}
    for inv in inv_rows:
        key = (inv.set_code, inv.collector_number, inv.finish)
        by_printing.setdefault(key, []).append(inv)

    checked_out_by_printing = {
        (set_code, collector_number, finish): qty
        for set_code, collector_number, finish, qty in (
            db.query(
                DeckAssignment.set_code, DeckAssignment.collector_number, DeckAssignment.finish,
                func.sum(DeckAssignment.quantity),
            )
            .filter(DeckAssignment.card_name == card_name)
            .group_by(DeckAssignment.set_code, DeckAssignment.collector_number, DeckAssignment.finish)
            .all()
        )
    }
    price_by_printing = {
        (p.set_code, p.collector_number, p.finish): p.price_usd
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }

    result: list[LocationAvailability] = []
    for (set_code, collector_number, finish), rows in by_printing.items():
        rows = sorted(rows, key=lambda r: (r.location != "", r.location))  # "" first — absorbs checked-out first
        remaining_checked_out = checked_out_by_printing.get((set_code, collector_number, finish), 0)
        price_usd = price_by_printing.get((set_code, collector_number, finish))
        for row in rows:
            consumed = min(row.total_quantity, remaining_checked_out)
            remaining_checked_out -= consumed
            available = row.total_quantity - consumed
            if available <= 0:
                continue
            result.append(
                LocationAvailability(
                    set_code=set_code, collector_number=collector_number, finish=finish,
                    location=row.location, available=available, price_usd=price_usd,
                )
            )

    result.sort(
        key=lambda r: (r.location == "", r.location, r.price_usd is None, r.price_usd if r.price_usd is not None else 0)
    )
    return result
