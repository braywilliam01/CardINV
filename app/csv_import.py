import csv
import io
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment
from .constants import is_basic_land

# ManaBox export column names (case-insensitive match; header has varied
# slightly across ManaBox versions, so we check a couple of aliases).
# Set code/Collector number are optional — a plain Name,Quantity CSV
# (or a row missing either one) still imports fine, just landing in the
# unresolved bucket like a manual add with no printing specified.
NAME_COLUMNS = ["name", "card name"]
QTY_COLUMNS = ["quantity", "qty"]
SET_COLUMNS = ["set code", "set"]
NUMBER_COLUMNS = ["collector number", "collector_number", "card number", "number"]


@dataclass
class ImportResult:
    unique_cards_loaded: int = 0
    total_quantity_loaded: int = 0
    rows_skipped: int = 0
    skipped_basic_lands: int = 0
    warnings: list[str] = field(default_factory=list)
    assignments_preserved: int = 0
    printings_added: int = 0
    printings_updated: int = 0
    printings_removed: int = 0


def _find_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    lowered = {f.strip().lower(): f for f in fieldnames}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _aggregate_csv(
    csv_text: str, ignore_basic_lands: bool = True
) -> tuple[dict[tuple[str, str, str], int], list[str], int]:
    """
    Parses the ManaBox CSV and aggregates quantity by (name, set_code,
    collector_number), since a single printing can appear on multiple
    rows (e.g. foil and non-foil are separate ManaBox rows but one
    Inventory total — see models.py). Rows with no set/number columns,
    or with only one of the two filled in, land in the unresolved
    bucket ("", "") — a row needs both to pin an exact printing, same
    rule as a manual add via Manage Collection.
    Returns ((name, set_code, collector_number) -> total_quantity, warnings, skipped_basic_land_rows).
    """
    warnings: list[str] = []
    skipped_basic_lands = 0
    reader = csv.DictReader(io.StringIO(csv_text))

    if reader.fieldnames is None:
        raise ValueError("CSV file appears to be empty or has no header row.")

    name_col = _find_column(reader.fieldnames, NAME_COLUMNS)
    qty_col = _find_column(reader.fieldnames, QTY_COLUMNS)
    set_col = _find_column(reader.fieldnames, SET_COLUMNS)
    number_col = _find_column(reader.fieldnames, NUMBER_COLUMNS)

    if name_col is None or qty_col is None:
        raise ValueError(
            f"CSV missing required columns. Found headers: {reader.fieldnames}. "
            f"Expected a name column ({NAME_COLUMNS}) and quantity column ({QTY_COLUMNS})."
        )

    aggregated: dict[tuple[str, str, str], int] = {}

    for i, row in enumerate(reader, start=2):  # start=2: row 1 is the header
        raw_name = (row.get(name_col) or "").strip()
        raw_qty = (row.get(qty_col) or "").strip()
        raw_set = (row.get(set_col) or "").strip().upper() if set_col else ""
        raw_number = (row.get(number_col) or "").strip() if number_col else ""

        if not raw_name:
            warnings.append(f"Row {i}: missing card name, skipped.")
            continue

        if ignore_basic_lands and is_basic_land(raw_name):
            skipped_basic_lands += 1
            continue

        try:
            qty = int(raw_qty)
        except (ValueError, TypeError):
            warnings.append(f"Row {i}: invalid quantity '{raw_qty}' for '{raw_name}', skipped.")
            continue

        if qty <= 0:
            continue  # zero/negative quantity rows contribute nothing; not an error

        if not (raw_set and raw_number):
            raw_set, raw_number = "", ""

        key = (raw_name, raw_set, raw_number)
        aggregated[key] = aggregated.get(key, 0) + qty

    return aggregated, warnings, skipped_basic_lands


def bulk_load_inventory(db: Session, csv_text: str, ignore_basic_lands: bool = True) -> ImportResult:
    """
    Reconciles inventory to match the new ManaBox export exactly,
    printing by printing: existing printings still present in the file
    get their quantity updated (or left alone if unchanged), printings
    new to the file are created, and printings no longer in the file
    are removed. A printing that survives reconciliation keeps its
    identity (and anything else tied to it) rather than being wiped and
    recreated — only genuinely absent printings disappear.

    deck_assignments is never touched here (no FK to inventory — see
    models.py) — preserved regardless of what this import does. Warns
    (rather than blocks) on any assignment left short after the load,
    i.e. checked out from a printing that this import reduced or
    removed — excluding basic lands, which are expected to have no
    inventory row at all when ignore_basic_lands is on, since they're
    tracked per-deck instead (see checkout.py's unlimited-supply
    handling for basics).
    """
    aggregated, parse_warnings, skipped_basic_lands = _aggregate_csv(csv_text, ignore_basic_lands)

    if not aggregated:
        raise ValueError("No valid card rows found in CSV — aborting to avoid wiping inventory with an empty load.")

    result = ImportResult(warnings=parse_warnings, skipped_basic_lands=skipped_basic_lands)

    try:
        existing = {
            (inv.card_name, inv.set_code, inv.collector_number): inv
            for inv in db.query(Inventory).all()
        }

        added = 0
        updated = 0
        for (card_name, set_code, collector_number), qty in aggregated.items():
            inv = existing.pop((card_name, set_code, collector_number), None)
            if inv is None:
                db.add(Inventory(
                    card_name=card_name, set_code=set_code, collector_number=collector_number, total_quantity=qty
                ))
                added += 1
            elif inv.total_quantity != qty:
                inv.total_quantity = qty
                updated += 1

        # Whatever's left in `existing` wasn't in the new file at all —
        # no longer part of the collection per this export.
        removed = len(existing)
        for inv in existing.values():
            db.delete(inv)

        db.flush()  # surface any DB-level errors, and make the reconciliation visible to the queries below

        result.unique_cards_loaded = len({card_name for card_name, _, _ in aggregated})
        result.total_quantity_loaded = sum(aggregated.values())
        result.printings_added = added
        result.printings_updated = updated
        result.printings_removed = removed
        result.assignments_preserved = db.query(DeckAssignment).count()

        # Printing-level shortfall check: for every printing with deck
        # assignments, is there still enough inventory (after this
        # reconciliation) to back what's checked out?
        assigned_by_printing: dict[tuple[str, str, str], list[tuple[str, int]]] = {}
        for a in db.query(DeckAssignment).filter(DeckAssignment.quantity > 0).all():
            key = (a.card_name, a.set_code, a.collector_number)
            assigned_by_printing.setdefault(key, []).append((a.deck_name, a.quantity))

        inv_qty_by_printing = {
            (inv.card_name, inv.set_code, inv.collector_number): inv.total_quantity
            for inv in db.query(Inventory).all()
        }

        for (card_name, set_code, collector_number), holds in assigned_by_printing.items():
            if ignore_basic_lands and is_basic_land(card_name):
                continue
            available = inv_qty_by_printing.get((card_name, set_code, collector_number), 0)
            assigned_total = sum(qty for _, qty in holds)
            if assigned_total > available:
                printing_label = f" ({set_code} #{collector_number})" if (set_code or collector_number) else ""
                deck_breakdown = ", ".join(f"{qty}x in '{deck_name}'" for deck_name, qty in holds)
                result.warnings.append(
                    f"'{card_name}'{printing_label} is checked out ({deck_breakdown}) "
                    f"but this import only accounts for {available} — {assigned_total - available} short."
                )

        db.commit()
    except Exception:
        db.rollback()
        raise

    return result
