from dataclasses import dataclass, field
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, DeckMeta
from .parser import parse_decklist
from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .constants import is_basic_land, canonical_basic_land_name
from .availability import get_printing_availability, get_assigned_printings


@dataclass
class LineResult:
    raw_line: str
    card_name: str
    requested_qty: int
    fulfilled_qty: int
    status: str  # "ok" | "partial" | "not_found" | "unparseable"
    message: str = ""  # human-readable explanation, for direct display in UI
    printings: list[dict] = field(default_factory=list)  # [{"set_code","collector_number","quantity"}, ...] actually touched


@dataclass
class ActionResult:
    lines: list[LineResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _describe_printings(used: list[dict]) -> str:
    """Human-readable ' (2x CLB #304, 1x unresolved)' suffix for a
    status message — omitted entirely for the common single-unresolved-
    printing case, since that's just "however much you had" with
    nothing extra worth calling out."""
    if not used:
        return ""
    if len(used) == 1 and not used[0]["set_code"] and not used[0]["collector_number"]:
        return ""
    parts = [
        f"{u['quantity']}x {u['set_code']} #{u['collector_number']}" if (u["set_code"] or u["collector_number"])
        else f"{u['quantity']}x unresolved"
        for u in used
    ]
    return " (" + ", ".join(parts) + ")"


def _draw_down_checkout(
    db: Session, card_name: str, deck_name: str, qty: int,
    set_code: str, collector_number: str, reserved: dict[tuple, int],
) -> tuple[int, list[dict]]:
    """
    Fulfills up to `qty` copies of card_name into deck_name.

    Pinned (set_code/collector_number given, from a "(SET) NUM" suffix
    — see parser.py): draws only from that exact printing, no
    spillover — the user named a specific printing, so partial
    fulfillment from a *different* one would silently substitute
    something they didn't ask for. Decklist text has no finish syntax
    (see parser.py), so a pinned line always targets that printing's
    unspecified-finish ("") row specifically — it will show 0
    available if every copy of that printing happens to be in a
    finish-resolved row instead, which is intentional (finish-pinning
    isn't supported), not a bug.

    Unpinned: draws cheapest-known-price printing first, spilling into
    the next-cheapest if the first isn't enough (see
    availability.get_printing_availability) — protects more valuable
    printings from getting tied up in a deck for ordinary play copies.
    Finish is just one more dimension of the rows iterated here; the
    resulting DeckAssignment inherits whichever row's finish it was
    actually drawn from.

    `reserved` is a running per-printing-per-finish claim guard, shared
    across every line in one request, so two lines for the same card
    (e.g. a pinned line and an unpinned line, or a typo'd duplicate)
    can't double-claim the same copies. Keyed the same 4-tuple shape
    in both branches below (finish="" for the pinned case) so a pinned
    claim and an unpinned draw that happens to land on the same row
    can't silently bypass each other.
    """
    used: list[dict] = []

    if set_code or collector_number:
        key = (card_name, set_code, collector_number, "")
        inv = (
            db.query(Inventory)
            .filter(
                Inventory.card_name == card_name,
                Inventory.set_code == set_code,
                Inventory.collector_number == collector_number,
                Inventory.finish == "",
            )
            .one_or_none()
        )
        total = inv.total_quantity if inv else 0
        checked_out = (
            db.query(func.coalesce(func.sum(DeckAssignment.quantity), 0))
            .filter(
                DeckAssignment.card_name == card_name,
                DeckAssignment.set_code == set_code,
                DeckAssignment.collector_number == collector_number,
                DeckAssignment.finish == "",
            )
            .scalar()
        )
        already_claimed = reserved.get(key, 0)
        available = max(0, total - checked_out - already_claimed)
        take = min(available, qty)
        if take > 0:
            _increment_assignment(db, card_name, deck_name, set_code, collector_number, "", take)
            reserved[key] = already_claimed + take
            used.append({"set_code": set_code, "collector_number": collector_number, "quantity": take})
        return take, used

    remaining = qty
    for p in get_printing_availability(db, card_name):
        if remaining <= 0:
            break
        key = (card_name, p.set_code, p.collector_number, p.finish)
        already_claimed = reserved.get(key, 0)
        avail_here = max(0, p.available - already_claimed)
        if avail_here <= 0:
            continue
        take = min(avail_here, remaining)
        _increment_assignment(db, card_name, deck_name, p.set_code, p.collector_number, p.finish, take)
        reserved[key] = already_claimed + take
        used.append({"set_code": p.set_code, "collector_number": p.collector_number, "quantity": take})
        remaining -= take

    return qty - remaining, used


def _draw_down_checkin(
    db: Session, card_name: str, deck_name: str, qty: int,
    set_code: str, collector_number: str, already_returned: dict[tuple, int],
) -> tuple[int, list[dict]]:
    """Mirror of _draw_down_checkout for returning cards: pinned
    targets that exact assignment row (finish="", same reasoning as
    _draw_down_checkout); unpinned returns the cheapest-assigned
    printing first (see availability.get_assigned_printings), keeping
    pricier printings in the deck as long as possible."""
    used: list[dict] = []

    if set_code or collector_number:
        key = (card_name, set_code, collector_number, "")
        assignment = (
            db.query(DeckAssignment)
            .filter(
                DeckAssignment.card_name == card_name,
                DeckAssignment.deck_name == deck_name,
                DeckAssignment.set_code == set_code,
                DeckAssignment.collector_number == collector_number,
                DeckAssignment.finish == "",
            )
            .one_or_none()
        )
        currently_assigned = assignment.quantity if assignment else 0
        already_claimed = already_returned.get(key, 0)
        returnable = max(0, currently_assigned - already_claimed)
        take = min(returnable, qty)
        if take > 0:
            _decrement_assignment(db, card_name, deck_name, set_code, collector_number, "", take)
            already_returned[key] = already_claimed + take
            used.append({"set_code": set_code, "collector_number": collector_number, "quantity": take})
        return take, used

    remaining = qty
    for p in get_assigned_printings(db, card_name, deck_name):
        if remaining <= 0:
            break
        key = (card_name, p.set_code, p.collector_number, p.finish)
        already_claimed = already_returned.get(key, 0)
        returnable = max(0, p.quantity - already_claimed)
        if returnable <= 0:
            continue
        take = min(returnable, remaining)
        _decrement_assignment(db, card_name, deck_name, p.set_code, p.collector_number, p.finish, take)
        already_returned[key] = already_claimed + take
        used.append({"set_code": p.set_code, "collector_number": p.collector_number, "quantity": take})
        remaining -= take

    return qty - remaining, used


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
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

    result = ActionResult()
    reserved: dict[tuple, int] = {}  # running deduction guard, keyed by (card_name, set_code, collector_number)
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
        # there; see ignore_basic_lands on the import endpoints). A pin
        # is still honored if given, just never blocked by availability.
        if is_basic_land(parsed.card_name):
            canonical = canonical_basic_land_name(parsed.card_name)
            _increment_assignment(
                db, canonical, deck_name, parsed.set_code, parsed.collector_number, "", parsed.quantity
            )
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

        fulfilled, printings_used = _draw_down_checkout(
            db, matched_name, deck_name, parsed.quantity, parsed.set_code, parsed.collector_number, reserved
        )
        if fulfilled > 0:
            any_change = True

        status = "ok" if fulfilled == parsed.quantity else (
            "partial" if fulfilled > 0 else "not_found"
        )

        printing_note = _describe_printings(printings_used)
        if status == "partial":
            message = f"Only {fulfilled}/{parsed.quantity} available{printing_note} — checked out {fulfilled}."
        elif status == "not_found":
            pin_note = f" ({parsed.set_code} #{parsed.collector_number})" if (parsed.set_code or parsed.collector_number) else ""
            message = f"'{matched_name}'{pin_note} has 0 available — nothing checked out."
        else:
            message = ""

        result.lines.append(
            LineResult(parsed.raw_line, matched_name, parsed.quantity, fulfilled, status, message, printings_used)
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
    already_returned: dict[tuple, int] = {}  # guard against over-returning within one request
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

        returned, printings_used = _draw_down_checkin(
            db, matched_name, deck_name, parsed.quantity, parsed.set_code, parsed.collector_number, already_returned
        )
        if returned > 0:
            any_change = True

        status = "ok" if returned == parsed.quantity else (
            "partial" if returned > 0 else "not_found"
        )

        printing_note = _describe_printings(printings_used)
        if status == "partial":
            message = f"Only {returned}/{parsed.quantity} were assigned to this deck{printing_note} — checked in {returned}."
        elif status == "not_found":
            pin_note = f" ({parsed.set_code} #{parsed.collector_number})" if (parsed.set_code or parsed.collector_number) else ""
            message = f"'{matched_name}'{pin_note} has 0 remaining assigned to this deck — nothing checked in."
        else:
            message = ""

        result.lines.append(
            LineResult(parsed.raw_line, matched_name, parsed.quantity, returned, status, message, printings_used)
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
    set_code: str = ""          # "" = this row is the unpinned/"everything else" target for the name
    collector_number: str = ""


@dataclass
class SyncResult:
    lines: list[SyncLineResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # unparseable / unmatched lines
    errors: list[str] = field(default_factory=list)  # availability failures — surfaced as a popup


def _collect_sync_targets(
    parsed_lines, candidate_names: list[str], fuzzy_threshold: int
) -> tuple[dict[str, dict], list[str], list[str]]:
    """
    Groups valid parsed lines by matched card name into per-name
    targets: `pinned` is {(set_code, collector_number): qty} (summed if
    the same printing appears on more than one line); `unpinned` is the
    summed quantity of every plain (no "(SET) NUM") line for that name
    — the target for "everything else", reconciled cheapest-first.
    Returns (targets_by_name, names_in_first-seen_order, warnings).
    """
    by_name: dict[str, dict] = {}
    order: list[str] = []
    warnings: list[str] = []

    for parsed in parsed_lines:
        if not parsed.valid:
            warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            continue

        if is_basic_land(parsed.card_name):
            name = canonical_basic_land_name(parsed.card_name)
        else:
            name = find_best_match(parsed.card_name, candidate_names, threshold=fuzzy_threshold)
            if name is None:
                warnings.append(f"'{parsed.card_name}' not found — skipped.")
                continue

        entry = by_name.setdefault(name, {"pinned": {}, "unpinned": 0})
        if name not in order:
            order.append(name)

        if parsed.set_code or parsed.collector_number:
            pkey = (parsed.set_code, parsed.collector_number)
            entry["pinned"][pkey] = entry["pinned"].get(pkey, 0) + parsed.quantity
        else:
            entry["unpinned"] += parsed.quantity

    return by_name, order, warnings


def _current_assignments_by_printing(db: Session, card_name: str, deck_name: str) -> dict[tuple, int]:
    """Pinned sync targets ("(SET) NUM" in decklist text) never
    specify finish (decks stay finish-blind — see _draw_down_checkout),
    so this only reflects each printing's unspecified-finish ("") row
    — any finish-resolved DeckAssignment rows for this deck (created
    via unpinned cheapest-first spillover into a finish-specific
    Inventory row) are intentionally excluded from pinned-target
    accounting; see _all_current_assignments for the unpinned pool,
    which does need to count them."""
    return {
        (a.set_code, a.collector_number): a.quantity
        for a in db.query(DeckAssignment)
        .filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.deck_name == deck_name,
            DeckAssignment.finish == "",
        )
        .all()
    }


def _all_current_assignments(db: Session, card_name: str, deck_name: str) -> dict[tuple, int]:
    """Every current assignment row for (card_name, deck_name),
    finish included — used for the unpinned "everything else" pool's
    current-count, which (unlike a pinned target) has to count
    finish-resolved rows too."""
    return {
        (a.set_code, a.collector_number, a.finish): a.quantity
        for a in db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name, DeckAssignment.deck_name == deck_name)
        .all()
    }


def _printing_growable(db: Session, card_name: str, set_code: str, collector_number: str) -> int:
    """How many more of a *pinned* printing (finish="", same reasoning
    as _current_assignments_by_printing) could be checked out."""
    if is_basic_land(card_name):
        return 10**9
    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == "",
        )
        .one_or_none()
    )
    total = inv.total_quantity if inv else 0
    checked_out = (
        db.query(func.coalesce(func.sum(DeckAssignment.quantity), 0))
        .filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.set_code == set_code,
            DeckAssignment.collector_number == collector_number,
            DeckAssignment.finish == "",
        )
        .scalar()
    )
    return max(0, total - checked_out)


def sync_checkout(
    db: Session,
    decklist_text: str,
    deck_name: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
) -> SyncResult:
    """
    Deck Checkout tab's "sync" mode: the box is treated as each card's
    target total in the deck, not an amount to add.

    A pinned line ("(SET) NUM") sets a target for that *exact printing*,
    independent of the name's other printings. An unpinned line sets a
    target for "everything else" — every printing of the name NOT
    explicitly pinned elsewhere in the same paste — reconciled
    cheapest-first, same draw-down rule as an additive checkout.

    Only grows (use sync_checkin to shrink); if a target can't be fully
    reached, that one printing/pool is skipped entirely (not partially
    fulfilled) and reported in `errors` for the caller to surface as a
    popup — every other line in the same request still applies normally.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

    by_name, order, warnings = _collect_sync_targets(parsed_lines, all_card_names, fuzzy_threshold)

    lines: list[SyncLineResult] = []
    errors: list[str] = []
    any_change = False

    for card_name in order:
        entry = by_name[card_name]
        current_by_printing = _current_assignments_by_printing(db, card_name, deck_name)

        # Step 1 — pinned printings, each its own independent target.
        for (set_code, collector_number), target_qty in entry["pinned"].items():
            current_qty = current_by_printing.get((set_code, collector_number), 0)
            delta = target_qty - current_qty

            if delta <= 0:
                lines.append(SyncLineResult(
                    card_name, current_qty, target_qty, 0, "no_change",
                    set_code=set_code, collector_number=collector_number,
                ))
                continue

            available = _printing_growable(db, card_name, set_code, collector_number)
            if delta > available:
                message = f"Only {available} available — reaching {target_qty} needs {delta} more."
                errors.append(f"'{card_name}' ({set_code} #{collector_number}): {message}")
                lines.append(SyncLineResult(
                    card_name, current_qty, target_qty, 0, "unavailable", message,
                    set_code=set_code, collector_number=collector_number,
                ))
                continue

            _increment_assignment(db, card_name, deck_name, set_code, collector_number, "", delta)
            any_change = True
            lines.append(SyncLineResult(
                card_name, current_qty, target_qty, delta, "ok",
                set_code=set_code, collector_number=collector_number,
            ))

        # Step 2 — the unpinned "everything else" pool for this name.
        # Only exclude the unspecified-finish row of a pinned printing
        # (already handled in Step 1 above) — a finish-resolved row of
        # that same printing (e.g. a Holofoil copy pulled in earlier by
        # unpinned spillover) is still fair game for this pool, since
        # pinning never specifies finish. Uses _all_current_assignments
        # (not current_by_printing, which is finish=""-only) since this
        # pool's current-count does need to include finish-resolved rows.
        pinned_keys_with_finish = {(sc, cn, "") for (sc, cn) in entry["pinned"]}
        all_current = _all_current_assignments(db, card_name, deck_name)
        other_current = sum(
            qty for key, qty in all_current.items() if key not in pinned_keys_with_finish
        )
        target_qty = entry["unpinned"]
        delta = target_qty - other_current

        if delta <= 0:
            lines.append(SyncLineResult(card_name, other_current, target_qty, 0, "no_change"))
            continue

        if is_basic_land(card_name):
            _increment_assignment(db, card_name, deck_name, "", "", "", delta)
            any_change = True
            lines.append(SyncLineResult(card_name, other_current, target_qty, delta, "ok"))
            continue

        eligible = [
            p for p in get_printing_availability(db, card_name)
            if (p.set_code, p.collector_number, p.finish) not in pinned_keys_with_finish
        ]
        available = sum(p.available for p in eligible)
        if delta > available:
            message = f"Only {available} available — reaching {target_qty} needs {delta} more."
            errors.append(f"'{card_name}': {message}")
            lines.append(SyncLineResult(card_name, other_current, target_qty, 0, "unavailable", message))
            continue

        remaining = delta
        for p in eligible:
            if remaining <= 0:
                break
            if p.available <= 0:
                continue
            take = min(p.available, remaining)
            _increment_assignment(db, card_name, deck_name, p.set_code, p.collector_number, p.finish, take)
            remaining -= take

        any_change = True
        lines.append(SyncLineResult(card_name, other_current, target_qty, delta, "ok"))

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
    each card's target total in the deck (same pinned-printing /
    unpinned-pool split as sync_checkout). Any printing whose target is
    lower than what's currently assigned — including a name or printing
    omitted from the box entirely, i.e. target 0 — gets the difference
    checked back in, unpinned amounts drawn cheapest-assigned-printing-
    first. Never adds cards — reaching a higher target is a no-op here
    (use sync_checkout to grow). Always fully satisfiable (you can't be
    short on cards you're returning), so there's no "unavailable" case.
    """
    parsed_lines = parse_decklist(decklist_text)

    deck_card_names_query = (
        db.query(DeckAssignment.card_name)
        .filter(DeckAssignment.deck_name == deck_name, DeckAssignment.quantity > 0)
        .distinct()
        .all()
    )
    deck_card_names = [row.card_name for row in deck_card_names_query]
    all_current_names = set(deck_card_names)

    by_name, order, warnings = _collect_sync_targets(parsed_lines, deck_card_names, fuzzy_threshold)

    # A name currently in the deck but not mentioned in the paste at all
    # still needs reconciling — omitted means target 0, same as an
    # explicit unpinned "0 <name>" line would.
    for name in all_current_names:
        if name not in by_name:
            by_name[name] = {"pinned": {}, "unpinned": 0}
            order.append(name)

    lines: list[SyncLineResult] = []
    any_change = False

    for card_name in order:
        entry = by_name[card_name]
        current_by_printing = _current_assignments_by_printing(db, card_name, deck_name)

        for (set_code, collector_number), target_qty in entry["pinned"].items():
            current_qty = current_by_printing.get((set_code, collector_number), 0)
            delta = current_qty - target_qty

            if delta <= 0:
                lines.append(SyncLineResult(
                    card_name, current_qty, target_qty, 0, "no_change",
                    set_code=set_code, collector_number=collector_number,
                ))
                continue

            _decrement_assignment(db, card_name, deck_name, set_code, collector_number, "", delta)
            any_change = True
            lines.append(SyncLineResult(
                card_name, current_qty, target_qty, -delta, "ok",
                set_code=set_code, collector_number=collector_number,
            ))

        # See sync_checkout's Step 2 for why this uses
        # _all_current_assignments (finish included) rather than
        # current_by_printing (finish=""-only).
        pinned_keys_with_finish = {(sc, cn, "") for (sc, cn) in entry["pinned"]}
        all_current = _all_current_assignments(db, card_name, deck_name)
        other_current = sum(
            qty for key, qty in all_current.items() if key not in pinned_keys_with_finish
        )
        target_qty = entry["unpinned"]
        delta = other_current - target_qty

        if delta <= 0:
            lines.append(SyncLineResult(card_name, other_current, target_qty, 0, "no_change"))
            continue

        remaining = delta
        for p in get_assigned_printings(db, card_name, deck_name):
            if remaining <= 0:
                break
            if (p.set_code, p.collector_number, p.finish) in pinned_keys_with_finish:
                continue
            take = min(p.quantity, remaining)
            if take <= 0:
                continue
            _decrement_assignment(db, card_name, deck_name, p.set_code, p.collector_number, p.finish, take)
            remaining -= take

        any_change = True
        lines.append(SyncLineResult(card_name, other_current, target_qty, -delta, "ok"))

    if any_change:
        _touch_deck(db, deck_name)
    db.commit()
    return SyncResult(lines=lines, warnings=warnings, errors=[])


def get_deck_cards(db: Session, deck_name: str) -> list[dict]:
    """
    Returns every (card, printing) currently checked out to
    `deck_name` — one row per printing, not per name (a name can span
    several rows; see models.py) — with quantity and how many more
    copies of that exact printing could still be pulled from inventory
    (0 if fully committed elsewhere or not in inventory).

    Batches inventory totals and cross-deck checkout totals into two
    queries up front (keyed by this deck's card names) rather than
    re-querying per row. Internally keyed including finish (so
    available_more is computed against the *specific* row a copy was
    drawn from, not conflated across finishes of the same printing)
    even though finish isn't part of this function's output — the
    Deck tab shows no finish UI (decks stay finish-blind), so two rows
    for the same printing in different finishes render identically
    here, which is expected, not a bug.
    """
    assignments = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.deck_name == deck_name, DeckAssignment.quantity > 0)
        .order_by(
            DeckAssignment.card_name.asc(),
            DeckAssignment.set_code.asc(),
            DeckAssignment.collector_number.asc(),
            DeckAssignment.finish.asc(),
        )
        .all()
    )

    if not assignments:
        return []

    card_names = list({a.card_name for a in assignments})

    inv_by_printing = {
        (row.card_name, row.set_code, row.collector_number, row.finish): row.total_quantity
        for row in db.query(Inventory).filter(Inventory.card_name.in_(card_names)).all()
    }

    checked_out_by_printing: dict[tuple, int] = {}
    for a in db.query(DeckAssignment).filter(DeckAssignment.card_name.in_(card_names)).all():
        key = (a.card_name, a.set_code, a.collector_number, a.finish)
        checked_out_by_printing[key] = checked_out_by_printing.get(key, 0) + a.quantity

    result = []
    for a in assignments:
        if is_basic_land(a.card_name):
            # Unlimited supply — never block the quick +1 action for basics.
            result.append({
                "card_name": a.card_name,
                "set_code": a.set_code,
                "collector_number": a.collector_number,
                "quantity": a.quantity,
                "available_more": 9999,
            })
            continue

        key = (a.card_name, a.set_code, a.collector_number, a.finish)
        total = inv_by_printing.get(key, 0)
        total_checked_out = checked_out_by_printing.get(key, 0)
        available_more = max(0, total - total_checked_out)

        result.append({
            "card_name": a.card_name,
            "set_code": a.set_code,
            "collector_number": a.collector_number,
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


def _increment_assignment(
    db: Session, card_name: str, deck_name: str, set_code: str, collector_number: str, finish: str, qty: int
) -> None:
    assignment = (
        db.query(DeckAssignment)
        .filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.deck_name == deck_name,
            DeckAssignment.set_code == set_code,
            DeckAssignment.collector_number == collector_number,
            DeckAssignment.finish == finish,
        )
        .one_or_none()
    )
    if assignment:
        assignment.quantity += qty
    else:
        db.add(DeckAssignment(
            card_name=card_name, deck_name=deck_name,
            set_code=set_code, collector_number=collector_number, finish=finish, quantity=qty,
        ))


def _decrement_assignment(
    db: Session, card_name: str, deck_name: str, set_code: str, collector_number: str, finish: str, qty: int
) -> None:
    assignment = (
        db.query(DeckAssignment)
        .filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.deck_name == deck_name,
            DeckAssignment.set_code == set_code,
            DeckAssignment.collector_number == collector_number,
            DeckAssignment.finish == finish,
        )
        .one_or_none()
    )
    if assignment is None:
        return
    assignment.quantity -= qty
    if assignment.quantity <= 0:
        db.delete(assignment)  # clean up zeroed-out rows rather than leaving quantity=0
