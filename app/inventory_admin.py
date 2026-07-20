from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, CardPrice
from .parser import parse_decklist
from .fuzzy import find_best_match
from .constants import is_basic_land

# Fixed high-confidence threshold for bulk add/remove — these are direct
# inventory edits (not deck-list matching against a big fuzzy pool), so
# a stricter threshold avoids accidentally merging two similarly-named
# but distinct cards.
BULK_MATCH_THRESHOLD = 90


@dataclass
class DeckHold:
    deck_name: str
    quantity: int


@dataclass
class InventoryRow:
    card_name: str
    total_quantity: int
    checked_out: int
    available: int
    decks: list[DeckHold] = field(default_factory=list)
    price_usd: float | None = None
    line_value: float | None = None


@dataclass
class InventoryPage:
    rows: list[InventoryRow]
    total_count: int


class BlockedDeleteError(Exception):
    """Raised when a delete/reduce would leave deck_assignments dangling
    and the caller hasn't opted in via force=True."""

    def __init__(self, card_name: str, decks: list[DeckHold]):
        self.card_name = card_name
        self.decks = decks
        total = sum(d.quantity for d in decks)
        deck_list = ", ".join(f"{d.quantity}x in '{d.deck_name}'" for d in decks)
        super().__init__(
            f"'{card_name}' has {total} checked out ({deck_list}). "
            f"Check them in first, or confirm to remove from those decks too."
        )


class DuplicateCardError(Exception):
    def __init__(self, card_name: str):
        super().__init__(f"'{card_name}' already exists in inventory — use the edit action to adjust its quantity.")


def _decks_for(db: Session, card_name: str) -> list[DeckHold]:
    rows = (
        db.query(DeckAssignment)
        .filter(DeckAssignment.card_name == card_name, DeckAssignment.quantity > 0)
        .all()
    )
    return [DeckHold(deck_name=r.deck_name, quantity=r.quantity) for r in rows]


def list_inventory(
    db: Session, search: str | None = None, page: int = 1, page_size: int = 50
) -> InventoryPage:
    """
    Returns one page of inventory rows plus the total match count (for
    the Manage Collection tab's pagination controls). Batches prices
    and deck assignments into two queries scoped to just this page's
    card names, rather than one query per row — with a large collection
    this used to mean a query per card just to render the table.
    """
    query = db.query(Inventory)
    if search:
        query = query.filter(Inventory.card_name.ilike(f"%{search}%"))

    total_count = query.count()

    rows = (
        query.order_by(Inventory.card_name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    card_names = [r.card_name for r in rows]

    price_map = {}
    deck_map: dict[str, list[DeckHold]] = {}
    if card_names:
        price_map = {
            p.card_name: p
            for p in db.query(CardPrice).filter(CardPrice.card_name.in_(card_names)).all()
        }
        for a in (
            db.query(DeckAssignment)
            .filter(DeckAssignment.card_name.in_(card_names), DeckAssignment.quantity > 0)
            .all()
        ):
            deck_map.setdefault(a.card_name, []).append(DeckHold(deck_name=a.deck_name, quantity=a.quantity))

    result = []
    for inv in rows:
        decks = deck_map.get(inv.card_name, [])
        checked_out = sum(d.quantity for d in decks)

        price = price_map.get(inv.card_name)
        price_usd = price.price_usd if price else None
        line_value = round(price_usd * inv.total_quantity, 2) if price_usd is not None else None

        result.append(
            InventoryRow(
                card_name=inv.card_name,
                total_quantity=inv.total_quantity,
                checked_out=checked_out,
                available=max(0, inv.total_quantity - checked_out),
                decks=decks,
                price_usd=price_usd,
                line_value=line_value,
            )
        )
    return InventoryPage(rows=result, total_count=total_count)


def add_card(db: Session, card_name: str, total_quantity: int) -> InventoryRow:
    """
    Blocks case-insensitive exact duplicates ("sol ring" is caught as
    the same card as an existing "Sol Ring") without fuzzy matching.
    Deliberately not the same check as bulk_add_cards/add_one_copy: a
    fuzzy threshold that's fine when the worst case is "merges into the
    closest match" is too aggressive once the action is "block card
    creation entirely" — plenty of distinct real card names (different
    printings, "Elite Vanguard" vs "Elite Guardmage", etc.) are only a
    few characters apart and would otherwise get wrongly rejected.
    """
    card_name = card_name.strip()
    if not card_name:
        raise ValueError("Card name cannot be empty.")
    if total_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    existing = db.query(Inventory).filter(Inventory.card_name.ilike(card_name)).one_or_none()
    if existing:
        raise DuplicateCardError(existing.card_name)

    db.add(Inventory(card_name=card_name, total_quantity=total_quantity))
    db.commit()

    return InventoryRow(card_name=card_name, total_quantity=total_quantity, checked_out=0, available=total_quantity)


def get_owned_quantity(db: Session, card_name: str) -> int:
    """
    Fuzzy-matches card_name against inventory (same threshold as bulk
    add/remove) and returns how many copies are owned — 0 if there's no
    close match. Powers Card Search's "# in inventory" figure.
    """
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]
    matched_name = find_best_match(card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)
    if matched_name is None:
        return 0
    inv = db.query(Inventory).filter(Inventory.card_name == matched_name).one()
    return inv.total_quantity


def add_one_copy(db: Session, card_name: str) -> InventoryRow:
    """
    Increments an existing (fuzzy-matched) inventory row by one, or
    creates a new one with quantity 1 if there's no close match. Powers
    Card Search's "Add to Inventory" button — always adds exactly one
    copy per click, mirroring the qty-nudge +1 buttons in Manage
    Collection rather than asking for a quantity up front.
    """
    card_name = card_name.strip()
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]
    matched_name = find_best_match(card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)

    if matched_name is None:
        db.add(Inventory(card_name=card_name, total_quantity=1))
        db.commit()
        return InventoryRow(card_name=card_name, total_quantity=1, checked_out=0, available=1)

    inv = db.query(Inventory).filter(Inventory.card_name == matched_name).one()
    inv.total_quantity += 1
    db.commit()

    decks = _decks_for(db, matched_name)
    checked_out = sum(d.quantity for d in decks)
    return InventoryRow(
        card_name=matched_name,
        total_quantity=inv.total_quantity,
        checked_out=checked_out,
        available=inv.total_quantity - checked_out,
        decks=decks,
    )


def adjust_quantity(db: Session, card_name: str, new_total_quantity: int) -> InventoryRow:
    """
    Sets total_quantity directly (used for both +/- nudges and manual
    edits from the UI — the frontend computes the new absolute value).
    Blocked if the new total would be less than what's currently checked
    out across decks, since that would silently make availability
    negative-equivalent. No force option here — reducing inventory below
    what's checked out always requires checking cards in first; unlike
    delete, there's no single "confirm" action that unambiguously
    resolves which deck to pull the shortfall from.
    """
    if new_total_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    inv = db.query(Inventory).filter(Inventory.card_name == card_name).one_or_none()
    if inv is None:
        raise ValueError(f"'{card_name}' not found in inventory.")

    decks = _decks_for(db, card_name)
    checked_out = sum(d.quantity for d in decks)

    if new_total_quantity < checked_out:
        raise BlockedDeleteError(card_name, decks)

    inv.total_quantity = new_total_quantity
    db.commit()

    return InventoryRow(
        card_name=card_name,
        total_quantity=new_total_quantity,
        checked_out=checked_out,
        available=new_total_quantity - checked_out,
        decks=decks,
    )


def delete_card(db: Session, card_name: str, force: bool = False) -> None:
    """
    Removes a card from inventory entirely. Blocked by default if any
    deck_assignments reference it — raises BlockedDeleteError so the
    caller (API layer) can surface a 409 with the deck breakdown and let
    the user confirm. With force=True, deletes the deck_assignments too
    (the confirmed "remove from both" path).
    """
    inv = db.query(Inventory).filter(Inventory.card_name == card_name).one_or_none()
    if inv is None:
        raise ValueError(f"'{card_name}' not found in inventory.")

    decks = _decks_for(db, card_name)

    if decks and not force:
        raise BlockedDeleteError(card_name, decks)

    if force and decks:
        db.query(DeckAssignment).filter(DeckAssignment.card_name == card_name).delete()

    db.delete(inv)
    db.commit()


@dataclass
class BulkLineResult:
    raw_line: str
    card_name: str
    requested_qty: int
    applied_qty: int
    status: str  # "ok" | "partial" | "not_found" | "unparseable" | "created"
    message: str = ""


@dataclass
class BulkResult:
    lines: list[BulkLineResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_basic_lands: int = 0


def bulk_add_cards(
    db: Session,
    decklist_text: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """
    Adds quantities to inventory from a pasted list. Fuzzy-matches each
    line against existing card names first (so "Ligtning Bolt" adds to
    the existing "Lightning Bolt" row instead of creating a near-duplicate);
    if nothing matches closely enough, a new card is created with the
    typed name.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]

    result = BulkResult()

    for parsed in parsed_lines:
        if not parsed.valid:
            result.warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            result.lines.append(BulkLineResult(parsed.raw_line, "", 0, 0, "unparseable"))
            continue

        if ignore_basic_lands and is_basic_land(parsed.card_name):
            result.skipped_basic_lands += 1
            continue

        matched_name = find_best_match(parsed.card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)

        if matched_name is None:
            # No close match — create a new inventory entry.
            new_name = parsed.card_name
            db.add(Inventory(card_name=new_name, total_quantity=parsed.quantity))
            all_card_names.append(new_name)  # so later lines in this same paste can match it
            result.lines.append(
                BulkLineResult(
                    parsed.raw_line, new_name, parsed.quantity, parsed.quantity, "created",
                    message=f"'{new_name}' was new — added to inventory.",
                )
            )
            continue

        inv = db.query(Inventory).filter(Inventory.card_name == matched_name).one()
        inv.total_quantity += parsed.quantity
        result.lines.append(
            BulkLineResult(parsed.raw_line, matched_name, parsed.quantity, parsed.quantity, "ok")
        )

    db.commit()
    return result


def bulk_remove_cards(
    db: Session,
    decklist_text: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """
    Removes quantities from inventory from a pasted list (e.g. pulling
    damaged or lost cards). Only reduces down to what's currently
    checked out across decks — never below, since that would make a
    deck's assignment exceed what you own. If the requested removal
    would go below that floor, only the safe portion is removed and the
    line is marked "partial" with an explanation.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).all()]

    result = BulkResult()
    already_removed: dict[str, int] = {}  # running guard for duplicate lines in one paste

    for parsed in parsed_lines:
        if not parsed.valid:
            result.warnings.append(f"Could not parse line: '{parsed.raw_line}'")
            result.lines.append(BulkLineResult(parsed.raw_line, "", 0, 0, "unparseable"))
            continue

        if ignore_basic_lands and is_basic_land(parsed.card_name):
            result.skipped_basic_lands += 1
            continue

        matched_name = find_best_match(parsed.card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)

        if matched_name is None:
            result.lines.append(
                BulkLineResult(
                    parsed.raw_line, parsed.card_name, parsed.quantity, 0, "not_found",
                    message=f"'{parsed.card_name}' not found in inventory.",
                )
            )
            continue

        inv = db.query(Inventory).filter(Inventory.card_name == matched_name).one()
        decks = _decks_for(db, matched_name)
        checked_out = sum(d.quantity for d in decks)

        already_claimed = already_removed.get(matched_name, 0)
        removable_floor = checked_out  # can't drop total below what's checked out
        currently_removable = max(0, inv.total_quantity - already_claimed - removable_floor)

        to_remove = min(currently_removable, parsed.quantity)

        if to_remove > 0:
            inv.total_quantity -= to_remove
            already_removed[matched_name] = already_claimed + to_remove

        status = "ok" if to_remove == parsed.quantity else ("partial" if to_remove > 0 else "not_found")

        if status == "partial":
            message = (
                f"Only removed {to_remove}/{parsed.quantity} — the rest is checked out "
                f"across decks and can't be removed until checked in."
            )
        elif status == "not_found" and to_remove == 0 and checked_out > 0:
            message = f"'{matched_name}' is fully checked out ({checked_out}) — nothing available to remove."
        elif status == "not_found":
            message = f"'{matched_name}' has 0 in inventory — nothing to remove."
        else:
            message = ""

        result.lines.append(
            BulkLineResult(parsed.raw_line, matched_name, parsed.quantity, to_remove, status, message)
        )

    db.commit()
    return result
