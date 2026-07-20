from dataclasses import dataclass
from sqlalchemy.orm import Session

from .models import Inventory
from .parser import parse_decklist, ParsedLine, _QTY_PREFIX, _QTY_SUFFIX
from .fuzzy import find_best_match, DEFAULT_THRESHOLD
from .constants import is_basic_land
from .availability import get_available_quantity as _get_available_quantity


@dataclass
class SplitResult:
    available_lines: list[str]
    missing_lines: list[str]
    warnings: list[str]  # unparseable lines, reported separately
    skipped_basic_lands: int = 0


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


def split_by_availability(
    db: Session,
    decklist_text: str,
    fuzzy_threshold: int = DEFAULT_THRESHOLD,
    ignore_basic_lands: bool = True,
) -> SplitResult:
    parsed_lines = parse_decklist(decklist_text)

    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]

    available_out: list[str] = []
    missing_out: list[str] = []
    warnings: list[str] = []
    reserved: dict[str, int] = {}
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

        available_qty = _get_available_quantity(db, matched_name, reserved)

        if available_qty <= 0:
            missing_out.append(_render_line(parsed, parsed.quantity))
        elif available_qty >= parsed.quantity:
            available_out.append(_render_line(parsed, parsed.quantity))
            reserved[matched_name] = reserved.get(matched_name, 0) + parsed.quantity
        else:
            # Partial match: split the requested quantity
            available_out.append(_render_line(parsed, available_qty))
            missing_out.append(_render_line(parsed, parsed.quantity - available_qty))
            reserved[matched_name] = reserved.get(matched_name, 0) + available_qty

    return SplitResult(
        available_lines=available_out,
        missing_lines=missing_out,
        warnings=warnings,
        skipped_basic_lands=skipped_basic_lands,
    )
