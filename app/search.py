from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from .models import Inventory
from .parser import parse_decklist, ParsedLine, _QTY_PREFIX, _QTY_SUFFIX
from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .constants import is_basic_land
from .availability import get_location_availability


@dataclass
class PickListEntry:
    """One (card, printing, finish, location) slice of an 'available'
    line's fulfillment — Collection Search's pick list groups these by
    location so a person can walk to one box at a time. is_no_location
    flags a slice that can't actually be pointed at physically yet
    (see availability.get_location_availability and Manage
    Collection's no-location fix-up filter)."""
    card_name: str
    quantity: int
    location: str
    set_code: str = ""
    collector_number: str = ""
    finish: str = ""
    is_no_location: bool = False


@dataclass
class SplitResult:
    available_lines: list[str]
    missing_lines: list[str]
    warnings: list[str]  # unparseable lines, reported separately
    skipped_basic_lands: int = 0
    pick_list: list[PickListEntry] = field(default_factory=list)


def _render_line(parsed: ParsedLine, quantity: int) -> str:
    """
    Re-render a line with a (possibly new) quantity, preserving the
    original formatting style (prefix vs suffix, and any trailing
    set-code text after the quantity was stripped).
    """
    raw = parsed.raw_line.strip()

    prefix_match = _QTY_PREFIX.match(raw)
    if prefix_match:
        remainder = raw[prefix_match.end():]  # "Lightning Bolt (CLB) 304"
        return f"{quantity} {remainder}"

    suffix_match = _QTY_SUFFIX.search(raw)
    if suffix_match:
        remainder = raw[: suffix_match.start()]  # "Lightning Bolt"
        return f"{remainder} x{quantity}"

    # Fallback — shouldn't happen since parsed.valid implies one of the
    # above matched during parsing, but keeps this function total.
    return f"{quantity} {parsed.card_name}"


def _allocate_pick(
    db: Session, card_name: str, qty: int, reserved: dict[tuple, int]
) -> tuple[int, list[PickListEntry]]:
    """
    Decides which (printing, finish, location) rows would supply up to
    `qty` copies of card_name, without mutating anything — this is a
    dry preview for the pick list, mirroring checkout._draw_down_checkout's
    unpinned branch in shape (walk availability rows, cheapest/most-
    actionable first, claim from each until satisfied) but read-only:
    no DeckAssignment is created here, only a description of what
    *would* supply the line for display purposes.

    `reserved` is a running per-row claim guard (one axis more than
    checkout's equivalent — printing+finish+location, not just
    printing+finish) shared across every line in one search, so two
    lines for the same card in one paste can't double-count the same
    physical copies in the pick list.
    """
    used: list[PickListEntry] = []
    remaining = qty
    for row in get_location_availability(db, card_name):
        if remaining <= 0:
            break
        key = (card_name, row.set_code, row.collector_number, row.finish, row.location)
        already_claimed = reserved.get(key, 0)
        avail_here = max(0, row.available - already_claimed)
        if avail_here <= 0:
            continue
        take = min(avail_here, remaining)
        reserved[key] = already_claimed + take
        used.append(
            PickListEntry(
                card_name=card_name, quantity=take, location=row.location,
                set_code=row.set_code, collector_number=row.collector_number, finish=row.finish,
                is_no_location=(row.location == ""),
            )
        )
        remaining -= take

    return qty - remaining, used


def split_by_availability(
    db: Session,
    decklist_text: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
    ignore_basic_lands: bool = True,
) -> SplitResult:
    parsed_lines = parse_decklist(decklist_text)

    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

    available_out: list[str] = []
    missing_out: list[str] = []
    warnings: list[str] = []
    pick_list: list[PickListEntry] = []
    reserved: dict[tuple, int] = {}
    skipped_basic_lands = 0

    for parsed in parsed_lines:
        if not parsed.valid:
            warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            continue

        if ignore_basic_lands and is_basic_land(parsed.card_name):
            skipped_basic_lands += 1
            continue

        matched_name = find_best_match(
            parsed.card_name, all_card_names, threshold=fuzzy_threshold
        )

        if matched_name is None:
            # Card not in DB at all — whole requested quantity is missing
            missing_out.append(_render_line(parsed, parsed.quantity))
            continue

        available_qty, picked = _allocate_pick(db, matched_name, parsed.quantity, reserved)

        if available_qty <= 0:
            missing_out.append(_render_line(parsed, parsed.quantity))
        elif available_qty >= parsed.quantity:
            available_out.append(_render_line(parsed, parsed.quantity))
            pick_list.extend(picked)
        else:
            # Partial match: split the requested quantity
            available_out.append(_render_line(parsed, available_qty))
            missing_out.append(_render_line(parsed, parsed.quantity - available_qty))
            pick_list.extend(picked)

    return SplitResult(
        available_lines=available_out,
        missing_lines=missing_out,
        warnings=warnings,
        skipped_basic_lands=skipped_basic_lands,
        pick_list=pick_list,
    )
