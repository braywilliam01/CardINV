from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, DeckMeta
from .parser import parse_decklist
from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .constants import is_basic_land, canonical_basic_land_name


@dataclass
class LineResult:
    raw_line: str
    card_name: str
    requested_qty: int
    fulfilled_qty: int
    status: str  # "ok" | "partial" | "not_found" | "unparseable"
    message: str = ""  # human-readable explanation, for direct display in UI


@dataclass
class ActionResult:
    lines: list[LineResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _get_available_quantity(db: Session, card_name: str, reserved: dict[str, int]) -> int:
    """Same math as search.py, minus whatever this in-progress request has
    already claimed for this card (the running-deduction guard)."""
    inv = db.query(Inventory).filter(Inventory.card_name == card_name).one_or_none()
    if inv is None:
        return 0

    checked_out = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name)
        .all()
    )
    total_checked_out = sum(a.quantity for a in checked_out)
    already_claimed_this_request = reserved.get(card_name, 0)

    return max(0, inv.total_quantity - total_checked_out - already_claimed_this_request)


def checkout_cards(
    db: Session,
    decklist_text: str,
    deck_name: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> ActionResult:
    """
    Checks out cards to `deck_name`. Partial fulfillment is allowed per
    line (mirrors Tab 1's split behavior) — if only 2 of 4 requested are
    available, 2 are checked out and the line is marked "partial".
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]

    result = ActionResult()
    reserved: dict[str, int] = {}  # running deduction guard, keyed by canonical DB name
    any_change = False

    for parsed in parsed_lines:
        if not parsed.valid:
            result.warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            result.lines.append(
                LineResult(parsed.raw_line, "", 0, 0, "unparseable")
            )
            continue

        # Basic lands are treated as unlimited supply — decks still need
        # exact land counts tracked, but basics are never subject to the
        # collection's inventory limits (they're deliberately not counted
        # there; see ignore_basic_lands on the import endpoints).
        if is_basic_land(parsed.card_name):
            canonical = canonical_basic_land_name(parsed.card_name)
            _increment_assignment(db, canonical, deck_name, parsed.quantity)
            any_change = True
            result.lines.append(
                LineResult(parsed.raw_line, canonical, parsed.quantity, parsed.quantity, "ok")
            )
            continue

        matched_name = find_best_match(
            parsed.card_name, all_card_names, threshold=fuzzy_threshold
        )

        if matched_name is None:
            result.lines.append(
                LineResult(
                    parsed.raw_line, parsed.card_name, parsed.quantity, 0, "not_found",
                    message=f"'{parsed.card_name}' not found in inventory.",
                )
            )
            continue

        available = _get_available_quantity(db, matched_name, reserved)
        fulfilled = min(available, parsed.quantity)

        if fulfilled > 0:
            _increment_assignment(db, matched_name, deck_name, fulfilled)
            reserved[matched_name] = reserved.get(matched_name, 0) + fulfilled
            any_change = True

        status = "ok" if fulfilled == parsed.quantity else (
            "partial" if fulfilled > 0 else "not_found"
        )

        if status == "partial":
            message = f"Only {fulfilled}/{parsed.quantity} available — checked out {fulfilled}."
        elif status == "not_found":
            message = f"'{matched_name}' has 0 available — nothing checked out."
        else:
            message = ""

        result.lines.append(
            LineResult(parsed.raw_line, matched_name, parsed.quantity, fulfilled, status, message)
        )

    if any_change:
        _touch_deck(db, deck_name)
    db.commit()
    return result


def checkin_cards(
    db: Session,
    decklist_text: str,
    deck_name: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> ActionResult:
    """
    Checks in (returns to available pool) cards from `deck_name`.
    Fuzzy-matches against card names *currently assigned to that deck*,
    not the full inventory — you shouldn't be able to check in a card
    that was never checked out to this deck.
    """
    parsed_lines = parse_decklist(decklist_text)

    deck_card_names = [
        row.card_name
        for row in db.query(DeckAssignment.card_name)
        .filter(DeckAssignment.deck_name == deck_name)
        .distinct()
        .all()
    ]

    result = ActionResult()
    already_returned: dict[str, int] = {}  # guard against over-returning within one request
    any_change = False

    for parsed in parsed_lines:
        if not parsed.valid:
            result.warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            result.lines.append(
                LineResult(parsed.raw_line, "", 0, 0, "unparseable")
            )
            continue

        matched_name = find_best_match(
            parsed.card_name, deck_card_names, threshold=fuzzy_threshold
        )

        if matched_name is None:
            result.lines.append(
                LineResult(
                    parsed.raw_line, parsed.card_name, parsed.quantity, 0, "not_found",
                    message=f"'{parsed.card_name}' is not currently checked out to this deck.",
                )
            )
            continue

        assignment = (
            db.query(DeckAssignment)
            .filter(
                DeckAssignment.card_name == matched_name,
                DeckAssignment.deck_name == deck_name,
            )
            .one_or_none()
        )
        currently_assigned = assignment.quantity if assignment else 0
        already_claimed = already_returned.get(matched_name, 0)
        returnable = max(0, currently_assigned - already_claimed)

        to_return = min(returnable, parsed.quantity)

        if to_return > 0:
            _decrement_assignment(db, matched_name, deck_name, to_return)
            already_returned[matched_name] = already_claimed + to_return
            any_change = True

        status = "ok" if to_return == parsed.quantity else (
            "partial" if to_return > 0 else "not_found"
        )

        if status == "partial":
            message = f"Only {to_return}/{parsed.quantity} were assigned to this deck — checked in {to_return}."
        elif status == "not_found":
            message = f"'{matched_name}' has 0 remaining assigned to this deck — nothing checked in."
        else:
            message = ""

        result.lines.append(
            LineResult(parsed.raw_line, matched_name, parsed.quantity, to_return, status, message)
        )

    if any_change:
        _touch_deck(db, deck_name)
    db.commit()
    return result


@dataclass
class SyncLineResult:
    card_name: str
    current_qty: int
    target_qty: int
    applied_delta: int  # positive = checked out, negative = checked in, 0 = no change
    status: str  # "ok" | "unavailable" | "no_change"
    message: str = ""


@dataclass
class SyncResult:
    lines: list[SyncLineResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # unparseable / unmatched lines
    errors: list[str] = field(default_factory=list)  # availability failures — surfaced as a popup


def sync_checkout(
    db: Session,
    decklist_text: str,
    deck_name: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> SyncResult:
    """
    Deck Checkout tab's "sync" mode: the box is treated as each card's
    target total in the deck, not an amount to add. For every card whose
    target exceeds what's currently assigned, checks out the difference;
    cards at or below their current amount are left untouched (use
    sync_checkin to shrink). If a card doesn't have enough available
    copies to reach its target, that one card is skipped entirely (not
    partially fulfilled) and reported in `errors` for the caller to
    surface as a popup — every other card in the same request still
    applies normally.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]

    targets: dict[str, int] = {}
    warnings: list[str] = []

    for parsed in parsed_lines:
        if not parsed.valid:
            warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            continue

        if is_basic_land(parsed.card_name):
            canonical = canonical_basic_land_name(parsed.card_name)
            targets[canonical] = targets.get(canonical, 0) + parsed.quantity
            continue

        matched_name = find_best_match(parsed.card_name, all_card_names, threshold=fuzzy_threshold)
        if matched_name is None:
            warnings.append(f"'{parsed.card_name}' not found in inventory — skipped.")
            continue

        targets[matched_name] = targets.get(matched_name, 0) + parsed.quantity

    current_assignments = {
        a.card_name: a.quantity
        for a in db.query(DeckAssignment).filter(DeckAssignment.deck_name == deck_name).all()
    }

    lines: list[SyncLineResult] = []
    errors: list[str] = []
    any_change = False

    for card_name, target_qty in targets.items():
        current_qty = current_assignments.get(card_name, 0)
        delta = target_qty - current_qty

        if delta <= 0:
            lines.append(SyncLineResult(card_name, current_qty, target_qty, 0, "no_change"))
            continue

        if is_basic_land(card_name):
            _increment_assignment(db, card_name, deck_name, delta)
            any_change = True
            lines.append(SyncLineResult(card_name, current_qty, target_qty, delta, "ok"))
            continue

        available = _get_available_quantity(db, card_name, {})
        if delta > available:
            message = f"Only {available} available — reaching {target_qty} needs {delta} more."
            errors.append(f"'{card_name}': {message}")
            lines.append(SyncLineResult(card_name, current_qty, target_qty, 0, "unavailable", message))
            continue

        _increment_assignment(db, card_name, deck_name, delta)
        any_change = True
        lines.append(SyncLineResult(card_name, current_qty, target_qty, delta, "ok"))

    if any_change:
        _touch_deck(db, deck_name)
    db.commit()
    return SyncResult(lines=lines, warnings=warnings, errors=errors)


def sync_checkin(
    db: Session,
    decklist_text: str,
    deck_name: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> SyncResult:
    """
    Deck Checkout tab's "sync" mode for Check In: the box is treated as
    each card's target total in the deck. Any currently-assigned card
    whose target is lower than its current amount (including cards
    omitted from the box entirely, i.e. target 0) gets the difference
    checked back in. Never adds cards — a target at or above the
    current amount is a no-op here (use sync_checkout to grow).
    """
    parsed_lines = parse_decklist(decklist_text)

    deck_card_names = [
        row.card_name
        for row in db.query(DeckAssignment.card_name)
        .filter(DeckAssignment.deck_name == deck_name, DeckAssignment.quantity > 0)
        .distinct()
        .all()
    ]

    targets: dict[str, int] = {}
    warnings: list[str] = []

    for parsed in parsed_lines:
        if not parsed.valid:
            warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            continue

        if is_basic_land(parsed.card_name):
            canonical = canonical_basic_land_name(parsed.card_name)
            targets[canonical] = targets.get(canonical, 0) + parsed.quantity
            continue

        matched_name = find_best_match(parsed.card_name, deck_card_names, threshold=fuzzy_threshold)
        if matched_name is None:
            warnings.append(f"'{parsed.card_name}' is not currently checked out to this deck — ignored.")
            continue

        targets[matched_name] = targets.get(matched_name, 0) + parsed.quantity

    current_assignments = {
        a.card_name: a.quantity
        for a in db.query(DeckAssignment)
        .filter(DeckAssignment.deck_name == deck_name, DeckAssignment.quantity > 0)
        .all()
    }

    lines: list[SyncLineResult] = []
    any_change = False

    for card_name, current_qty in current_assignments.items():
        target_qty = targets.get(card_name, 0)
        delta = current_qty - target_qty  # positive = amount to check in

        if delta <= 0:
            lines.append(SyncLineResult(card_name, current_qty, target_qty, 0, "no_change"))
            continue

        _decrement_assignment(db, card_name, deck_name, delta)
        any_change = True
        lines.append(SyncLineResult(card_name, current_qty, target_qty, -delta, "ok"))

    if any_change:
        _touch_deck(db, deck_name)
    db.commit()
    return SyncResult(lines=lines, warnings=warnings, errors=[])


def get_deck_cards(db: Session, deck_name: str) -> list[dict]:
    """
    Returns every card currently checked out to `deck_name`, with
    quantity and how many more of that card could still be pulled from
    inventory (0 if fully committed elsewhere or not in inventory).
    """
    assignments = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.deck_name == deck_name, DeckAssignment.quantity > 0)
        .order_by(DeckAssignment.card_name.asc())
        .all()
    )

    result = []
    for a in assignments:
        if is_basic_land(a.card_name):
            # Unlimited supply — never block the quick +1 action for basics.
            result.append({
                "card_name": a.card_name,
                "quantity": a.quantity,
                "available_more": 9999,
            })
            continue

        inv = db.query(Inventory).filter(Inventory.card_name == a.card_name).one_or_none()
        total = inv.total_quantity if inv else 0
        checked_out_everywhere = (
            db.query(DeckAssignment)
            .filter(DeckAssignment.card_name == a.card_name)
            .all()
        )
        total_checked_out = sum(x.quantity for x in checked_out_everywhere)
        available_more = max(0, total - total_checked_out)

        result.append({
            "card_name": a.card_name,
            "quantity": a.quantity,
            "available_more": available_more,
        })

    return result


def _touch_deck(db: Session, deck_name: str) -> None:
    """Records that `deck_name` was just checked out to / checked in
    from, creating its DeckMeta row on first use. Powers the Homepage's
    "recently changed decks" shortcut list.

    Callers must call this at most once per deck per request (e.g. once
    at the end of checkout_cards/checkin_cards, not per line) — the
    session is autoflush=False, so a second call for the same
    not-yet-committed deck wouldn't see the first call's pending insert
    and would attempt a duplicate INSERT, violating the primary key.
    """
    meta = db.query(DeckMeta).filter(DeckMeta.deck_name == deck_name).one_or_none()
    if meta is None:
        meta = DeckMeta(deck_name=deck_name, is_favorite=False)
        db.add(meta)
    meta.last_modified = datetime.now(timezone.utc)


def _increment_assignment(db: Session, card_name: str, deck_name: str, qty: int) -> None:
    assignment = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name, DeckAssignment.deck_name == deck_name)
        .one_or_none()
    )
    if assignment:
        assignment.quantity += qty
    else:
        db.add(DeckAssignment(card_name=card_name, deck_name=deck_name, quantity=qty))


def _decrement_assignment(db: Session, card_name: str, deck_name: str, qty: int) -> None:
    assignment = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name, DeckAssignment.deck_name == deck_name)
        .one_or_none()
    )
    if assignment is None:
        return
    assignment.quantity -= qty
    if assignment.quantity <= 0:
        db.delete(assignment)  # clean up zeroed-out rows rather than leaving quantity=0
