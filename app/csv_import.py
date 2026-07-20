import csv
import io
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment
from .constants import is_basic_land

# ManaBox export column names (case-insensitive match; header has varied
# slightly across ManaBox versions, so we check a couple of aliases).
NAME_COLUMNS = ["name", "card name"]
QTY_COLUMNS = ["quantity", "qty"]


@dataclass
class ImportResult:
    unique_cards_loaded: int = 0
    total_quantity_loaded: int = 0
    rows_skipped: int = 0
    skipped_basic_lands: int = 0
    warnings: list[str] = field(default_factory=list)
    assignments_preserved: int = 0


def _find_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    lowered = {f.strip().lower(): f for f in fieldnames}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _aggregate_csv(csv_text: str, ignore_basic_lands: bool = True) -> tuple[dict[str, int], list[str], int]:
    """
    Parses the ManaBox CSV and aggregates quantity by card name, since a
    single card can appear on multiple rows (different printings/foils).
    Returns (name -> total_quantity, warnings, skipped_basic_land_rows).
    """
    warnings: list[str] = []
    skipped_basic_lands = 0
    reader = csv.DictReader(io.StringIO(csv_text))

    if reader.fieldnames is None:
        raise ValueError("CSV file appears to be empty or has no header row.")

    name_col = _find_column(reader.fieldnames, NAME_COLUMNS)
    qty_col = _find_column(reader.fieldnames, QTY_COLUMNS)

    if name_col is None or qty_col is None:
        raise ValueError(
            f"CSV missing required columns. Found headers: {reader.fieldnames}. "
            f"Expected a name column ({NAME_COLUMNS}) and quantity column ({QTY_COLUMNS})."
        )

    aggregated: dict[str, int] = {}

    for i, row in enumerate(reader, start=2):  # start=2: row 1 is the header
        raw_name = (row.get(name_col) or "").strip()
        raw_qty = (row.get(qty_col) or "").strip()

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

        aggregated[raw_name] = aggregated.get(raw_name, 0) + qty

    return aggregated, warnings, skipped_basic_lands


def bulk_load_inventory(db: Session, csv_text: str, ignore_basic_lands: bool = True) -> ImportResult:
    """
    Truncates the inventory table and loads the new ManaBox export,
    preserving all existing deck_assignments regardless of whether the
    new inventory still contains those cards. Warns (rather than blocks)
    on any assignment left referencing a card with 0 total inventory
    after the load — excluding basic lands, which are expected to have
    0 total inventory when ignore_basic_lands is on, since they're
    tracked per-deck instead (see checkout.py's unlimited-supply
    handling for basics).
    """
    aggregated, parse_warnings, skipped_basic_lands = _aggregate_csv(csv_text, ignore_basic_lands)

    if not aggregated:
        raise ValueError("No valid card rows found in CSV — aborting to avoid wiping inventory with an empty load.")

    result = ImportResult(warnings=parse_warnings, skipped_basic_lands=skipped_basic_lands)

    try:
        # Bulk delete — bypasses ORM cascade, so deck_assignments survive.
        # (Using db.delete() per-object in a loop would instead trigger
        # cascade="all, delete-orphan" on the Inventory relationship and
        # wipe deck_assignments — bulk delete is the mechanism that makes
        # preservation work.)
        db.query(Inventory).delete()

        for card_name, total_qty in aggregated.items():
            db.add(Inventory(card_name=card_name, total_quantity=total_qty))

        db.flush()  # surface any DB-level errors before we commit

        result.unique_cards_loaded = len(aggregated)
        result.total_quantity_loaded = sum(aggregated.values())
        result.assignments_preserved = db.query(DeckAssignment).count()

        # Warn about any deck_assignments referencing cards with 0 total
        # inventory after this load — i.e. checked out but no longer
        # owned per the new CSV. Basic lands are excluded: they're
        # intentionally absent from inventory when ignored on import.
        orphaned = (
            db.query(DeckAssignment.card_name, DeckAssignment.deck_name, DeckAssignment.quantity)
            .outerjoin(Inventory, DeckAssignment.card_name == Inventory.card_name)
            .filter((Inventory.total_quantity == None) | (Inventory.total_quantity == 0))  # noqa: E711
            .all()
        )
        for card_name, deck_name, qty in orphaned:
            if ignore_basic_lands and is_basic_land(card_name):
                continue
            result.warnings.append(
                f"'{card_name}' is checked out ({qty}x) to deck '{deck_name}' "
                f"but shows 0 total quantity in the new inventory."
            )

        db.commit()
    except Exception:
        db.rollback()
        raise

    return result
