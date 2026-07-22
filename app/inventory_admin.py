from dataclasses import dataclass, field
from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, CardPrice
from .parser import parse_decklist
from .fuzzy import find_best_match
from .constants import is_basic_land
from .finishes import normalize_finish

# Fixed high-confidence threshold for bulk add/remove — these are direct
# inventory edits (not deck-list matching against a big fuzzy pool), so
# a stricter threshold avoids accidentally merging two similarly-named
# but distinct cards.
BULK_MATCH_THRESHOLD = 90


def _norm_printing(set_code: str | None, collector_number: str | None) -> tuple[str, str]:
    """Empty string (never None) is the 'unresolved printing' sentinel
    — see models.py for why that matters under SQLite."""
    return (set_code or "").strip().upper(), (collector_number or "").strip()


@dataclass
class DeckHold:
    deck_name: str
    quantity: int


@dataclass
class PrintingRow:
    """One (set_code, collector_number, finish) row for a card name.
    set_code/collector_number both empty means 'unresolved' (quantity
    not yet tied to a specific printing); finish empty means
    'unspecified' (printing may be known, but which finish these
    copies are isn't) -- see models.py's Inventory docstring for why
    these are two independent axes, not one. No checked_out/available
    here: deck assignments aren't printing-specific yet (that's a
    later phase), so availability is only meaningful at the card-name
    level — see InventoryRow. price_usd/price_usd_foil/is_estimated
    mirror CardPrice for this exact printing/finish — is_estimated
    means the price is a stand-in (cheapest known printing, or
    Scryfall/TCGdex's own best-guess name match) rather than a fetch
    for this specific printing; see price_estimation.py.
    """
    set_code: str
    collector_number: str
    finish: str
    total_quantity: int
    is_unresolved: bool
    is_finish_unspecified: bool
    price_usd: float | None = None
    price_usd_foil: float | None = None
    is_estimated: bool = False
    line_value: float | None = None


@dataclass
class InventoryRow:
    """One grouped row per card name, aggregated across every printing
    — what Manage Collection's main table renders. total_quantity/
    checked_out/available are summed across all of that name's
    printing rows. `printings` is the per-printing breakdown shown
    when the row is expanded. price_usd is only set when the card has
    exactly one printing (otherwise "the" price is ambiguous — expand
    the row to see each printing's own price); line_value always sums
    every priced printing's own line value regardless of count.
    """
    card_name: str
    total_quantity: int
    checked_out: int
    available: int
    decks: list[DeckHold] = field(default_factory=list)
    price_usd: float | None = None
    line_value: float | None = None
    printing_count: int = 1
    has_unresolved: bool = False
    has_estimated: bool = False
    printings: list[PrintingRow] = field(default_factory=list)


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
    def __init__(self, card_name: str, set_code: str = "", collector_number: str = "", finish: str = ""):
        self.card_name = card_name
        self.set_code = set_code
        self.collector_number = collector_number
        self.finish = finish
        printing = f"{set_code} #{collector_number}".strip(" #") if (set_code or collector_number) else "unresolved printing"
        if finish:
            printing = f"{printing}, {finish}"
        super().__init__(
            f"'{card_name}' ({printing}) already exists in inventory — "
            f"use the edit action to adjust its quantity."
        )


def _decks_for(db: Session, card_name: str) -> list[DeckHold]:
    """Which decks hold this card, and how much — summed across
    printings: a deck can now hold several DeckAssignment rows for the
    same name (one per printing it drew from — see checkout.py), and
    "how much of this card is checked out to Deck X" is a per-deck
    total, not a per-printing one."""
    rows = (
        db.query(DeckAssignment.deck_name, func.sum(DeckAssignment.quantity).label("total"))
        .filter(DeckAssignment.card_name == card_name, DeckAssignment.quantity > 0)
        .group_by(DeckAssignment.deck_name)
        .all()
    )
    return [DeckHold(deck_name=r.deck_name, quantity=r.total) for r in rows]


def _to_printing_row(inv: Inventory, price: CardPrice | None) -> PrintingRow:
    price_usd = price.price_usd if price else None
    price_usd_foil = price.price_usd_foil if price else None
    line_value = round(price_usd * inv.total_quantity, 2) if price_usd is not None else None
    return PrintingRow(
        set_code=inv.set_code,
        collector_number=inv.collector_number,
        finish=inv.finish,
        total_quantity=inv.total_quantity,
        is_unresolved=(inv.set_code == "" and inv.collector_number == ""),
        is_finish_unspecified=(inv.finish == ""),
        price_usd=price_usd,
        price_usd_foil=price_usd_foil,
        is_estimated=price.is_estimated if price else False,
        line_value=line_value,
    )


def _aggregate_pricing(printing_rows: list[PrintingRow]) -> tuple[float | None, float | None, bool]:
    """Rolls per-printing prices up to the group level — see
    InventoryRow for what price_usd/line_value mean at that level.
    has_estimated flags if any priced printing's price is a stand-in
    rather than a real fetch for that exact printing."""
    line_value = None
    for p in printing_rows:
        if p.line_value is not None:
            line_value = (line_value or 0) + p.line_value
    price_usd = printing_rows[0].price_usd if len(printing_rows) == 1 else None
    has_estimated = any(p.is_estimated and p.price_usd is not None for p in printing_rows)
    return price_usd, (round(line_value, 2) if line_value is not None else None), has_estimated


def get_printings_for_card(db: Session, card_name: str) -> list[PrintingRow]:
    """Every printing row for one card name, for the fix-up modal /
    expanded row view. Ordered with the unresolved bucket first (it's
    the one you're usually trying to resolve), then by set/number,
    then by finish (so a printing's finishes group together as
    sub-rows under it)."""
    rows = (
        db.query(Inventory)
        .filter(Inventory.card_name == card_name)
        .all()
    )
    rows.sort(
        key=lambda r: (r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish)
    )

    price_by_key = {
        (p.set_code, p.collector_number, p.finish): p
        for p in db.query(CardPrice).filter(CardPrice.card_name == card_name).all()
    }
    return [_to_printing_row(r, price_by_key.get((r.set_code, r.collector_number, r.finish))) for r in rows]


def build_group_row(db: Session, card_name: str) -> InventoryRow:
    """Recomputes the full aggregate row for one card name after a
    write — used by the single-card mutation functions (add/adjust/
    delete/assign) so they can return an up-to-date row without the
    caller needing a second round-trip."""
    printings = get_printings_for_card(db, card_name)
    total_quantity = sum(p.total_quantity for p in printings)
    decks = _decks_for(db, card_name)
    checked_out = sum(d.quantity for d in decks)

    price_usd, line_value, has_estimated = _aggregate_pricing(printings)

    return InventoryRow(
        card_name=card_name,
        total_quantity=total_quantity,
        checked_out=checked_out,
        available=max(0, total_quantity - checked_out),
        decks=decks,
        price_usd=price_usd,
        line_value=line_value,
        printing_count=len(printings),
        has_unresolved=any(p.is_unresolved for p in printings),
        has_estimated=has_estimated,
        printings=printings,
    )


SORT_FIELDS = ("name", "total_quantity", "checked_out", "available", "value")


def list_inventory(
    db: Session,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "name",
    sort_dir: str = "asc",
    unresolved_only: bool = False,
    checked_out_only: bool = False,
) -> InventoryPage:
    """
    Returns one page of *grouped* inventory rows (one per card name,
    aggregated across every printing) plus the total distinct-name
    count, for the Manage Collection tab's pagination, filtering, and
    sorting controls.

    Sorting/filtering/pagination all happen in one SQL query, computing
    each name's aggregates (total quantity, checked-out, available,
    value) via GROUP BY + outer joins — this has to happen in SQL
    rather than Python, since "page 2 sorted by value" needs to know
    every name's aggregate value to decide what belongs on page 2, not
    just whichever names happen to land there alphabetically. Once the
    page's card_names are settled, prices/decks/printing rows are
    batched into three more queries scoped to just those names (as
    before), rather than one query per row.
    """
    if sort_by not in SORT_FIELDS:
        sort_by = "name"
    descending = sort_dir == "desc"

    # One row per card name: total quantity, and whether any of its
    # printing rows is the unresolved ("", "") sentinel.
    inv_agg = (
        db.query(
            Inventory.card_name.label("card_name"),
            func.sum(Inventory.total_quantity).label("total_quantity"),
            func.max(
                case(
                    (and_(Inventory.set_code == "", Inventory.collector_number == ""), 1),
                    else_=0,
                )
            ).label("has_unresolved"),
        )
        .group_by(Inventory.card_name)
        .subquery()
    )

    # One row per card name: total checked-out across every deck and printing.
    deck_agg = (
        db.query(
            DeckAssignment.card_name.label("card_name"),
            func.sum(DeckAssignment.quantity).label("checked_out"),
        )
        .filter(DeckAssignment.quantity > 0)
        .group_by(DeckAssignment.card_name)
        .subquery()
    )

    # One row per card name: total collection value, summing each
    # printing's own price * quantity (same join shape as
    # pricing.get_collection_value). NULL (not len(rows) == 0) when no
    # printing has a cached price, so it can sort last either direction.
    price_agg = (
        db.query(
            Inventory.card_name.label("card_name"),
            func.sum(CardPrice.price_usd * Inventory.total_quantity).label("line_value"),
        )
        .join(
            CardPrice,
            and_(
                Inventory.card_name == CardPrice.card_name,
                Inventory.set_code == CardPrice.set_code,
                Inventory.collector_number == CardPrice.collector_number,
                Inventory.finish == CardPrice.finish,
            ),
        )
        .filter(CardPrice.price_usd.isnot(None))
        .group_by(Inventory.card_name)
        .subquery()
    )

    checked_out_expr = func.coalesce(deck_agg.c.checked_out, 0)
    available_expr = inv_agg.c.total_quantity - checked_out_expr

    query = (
        db.query(
            inv_agg.c.card_name,
            inv_agg.c.total_quantity,
            inv_agg.c.has_unresolved,
            checked_out_expr.label("checked_out"),
            available_expr.label("available"),
            price_agg.c.line_value,
        )
        .outerjoin(deck_agg, inv_agg.c.card_name == deck_agg.c.card_name)
        .outerjoin(price_agg, inv_agg.c.card_name == price_agg.c.card_name)
    )

    if search:
        query = query.filter(inv_agg.c.card_name.ilike(f"%{search}%"))
    if unresolved_only:
        query = query.filter(inv_agg.c.has_unresolved == 1)
    if checked_out_only:
        query = query.filter(checked_out_expr > 0)

    total_count = query.count()

    sort_columns = {
        "name": inv_agg.c.card_name,
        "total_quantity": inv_agg.c.total_quantity,
        "checked_out": checked_out_expr,
        "available": available_expr,
        "value": price_agg.c.line_value,
    }
    sort_col = sort_columns[sort_by]
    primary_order = sort_col.desc() if descending else sort_col.asc()
    if sort_by == "value":
        # Unpriced cards have a NULL aggregate value — always push them
        # to the end regardless of sort direction, rather than letting
        # "unknown" masquerade as the smallest value on an ascending sort.
        primary_order = primary_order.nullslast()

    # Secondary sort by name keeps ties (e.g. several cards with the
    # same checked_out count) in a stable, predictable order.
    name_rows = (
        query.order_by(primary_order, inv_agg.c.card_name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    card_names = [r.card_name for r in name_rows]

    price_map = {}
    deck_map: dict[str, list[DeckHold]] = {}
    printing_map: dict[str, list[Inventory]] = {}
    if card_names:
        price_map = {
            (p.card_name, p.set_code, p.collector_number, p.finish): p
            for p in db.query(CardPrice).filter(CardPrice.card_name.in_(card_names)).all()
        }
        for card_name, deck_name, total in (
            db.query(
                DeckAssignment.card_name, DeckAssignment.deck_name,
                func.sum(DeckAssignment.quantity).label("total"),
            )
            .filter(DeckAssignment.card_name.in_(card_names), DeckAssignment.quantity > 0)
            .group_by(DeckAssignment.card_name, DeckAssignment.deck_name)
            .all()
        ):
            deck_map.setdefault(card_name, []).append(DeckHold(deck_name=deck_name, quantity=total))
        for inv in db.query(Inventory).filter(Inventory.card_name.in_(card_names)).all():
            printing_map.setdefault(inv.card_name, []).append(inv)

    result = []
    for card_name in card_names:
        printings = printing_map.get(card_name, [])
        printings.sort(
            key=lambda r: (r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish)
        )
        printing_rows = [
            _to_printing_row(p, price_map.get((card_name, p.set_code, p.collector_number, p.finish)))
            for p in printings
        ]
        total_quantity = sum(p.total_quantity for p in printing_rows)

        decks = deck_map.get(card_name, [])
        checked_out = sum(d.quantity for d in decks)

        price_usd, line_value, has_estimated = _aggregate_pricing(printing_rows)

        result.append(
            InventoryRow(
                card_name=card_name,
                total_quantity=total_quantity,
                checked_out=checked_out,
                available=max(0, total_quantity - checked_out),
                decks=decks,
                price_usd=price_usd,
                line_value=line_value,
                printing_count=len(printing_rows),
                has_unresolved=any(p.is_unresolved for p in printing_rows),
                has_estimated=has_estimated,
                printings=printing_rows,
            )
        )
    return InventoryPage(rows=result, total_count=total_count)


def add_card(
    db: Session,
    card_name: str,
    total_quantity: int,
    set_code: str = "",
    collector_number: str = "",
    finish: str = "",
) -> InventoryRow:
    """
    Creates one printing row: (card_name, set_code, collector_number,
    finish). Leaving set_code/collector_number blank creates/targets
    the 'unresolved' bucket for that name; leaving finish blank
    creates/targets the 'unspecified' finish for whatever printing was
    given — the same behavior as before per-printing tracking existed,
    extended with one more independent axis. Blocks case-insensitive
    exact duplicates of the same printing+finish (not the same
    fuzzy-match threshold as bulk_add_cards/add_one_copy: a fuzzy
    threshold that's fine when the worst case is "merges into the
    closest match" is too aggressive once the action is "block card
    creation entirely" — plenty of distinct real card names are only a
    few characters apart and would otherwise get wrongly rejected).
    """
    card_name = card_name.strip()
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    if not card_name:
        raise ValueError("Card name cannot be empty.")
    if total_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    existing = (
        db.query(Inventory)
        .filter(
            Inventory.card_name.ilike(card_name),
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
        )
        .one_or_none()
    )
    if existing:
        raise DuplicateCardError(existing.card_name, set_code, collector_number, finish)

    db.add(
        Inventory(
            card_name=card_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            total_quantity=total_quantity,
        )
    )
    db.commit()

    return build_group_row(db, card_name)


def get_owned_quantity(
    db: Session, card_name: str, set_code: str = "", collector_number: str = "", finish: str | None = None
) -> int:
    """
    Fuzzy-matches card_name against inventory (same threshold as bulk
    add/remove). If set_code/collector_number are given, returns that
    printing's quantity — summed across every finish, unless `finish`
    is also given (a real value or explicitly ""), in which case it
    returns just that one finish's quantity. Without set_code/
    collector_number, returns the total across every printing and
    finish of the name. Powers Card Search's '# in inventory' figure,
    which shows "how many of this printing, in any finish, do I own"
    since Card Search doesn't know which finish the user's copies are
    until they've actually been added with one.
    """
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]
    matched_name = find_best_match(card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)
    if matched_name is None:
        return 0

    set_code, collector_number = _norm_printing(set_code, collector_number)
    if set_code or collector_number:
        query = db.query(func.coalesce(func.sum(Inventory.total_quantity), 0)).filter(
            Inventory.card_name == matched_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
        )
        if finish is not None:
            query = query.filter(Inventory.finish == normalize_finish(finish))
        return query.scalar()

    total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(Inventory.card_name == matched_name)
        .scalar()
    )
    return total


def add_one_copy(
    db: Session, card_name: str, set_code: str = "", collector_number: str = "", finish: str = ""
) -> InventoryRow:
    """
    Increments one exact printing+finish row by one (fuzzy-matching
    only the card name, to avoid creating "Sol Ring" vs "sol ring"
    duplicates), creating that row with quantity 1 if it doesn't exist
    yet. Powers Card Search's "Add to Inventory" button — always adds
    exactly one copy per click. When Card Search knows the exact
    printing (set_code/collector_number from the lookup result), that's
    what gets incremented; otherwise it falls back to the unresolved
    bucket, same as before per-printing tracking existed. finish
    defaults to "" (unspecified) the same way — only set when the
    caller actually knows which finish this copy is (e.g. a specific
    price-variant "Add" action).
    """
    card_name = card_name.strip()
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)

    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]
    matched_name = find_best_match(card_name, all_card_names, threshold=BULK_MATCH_THRESHOLD)
    target_name = matched_name or card_name

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == target_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
        )
        .one_or_none()
    )
    if inv is None:
        inv = Inventory(
            card_name=target_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            total_quantity=0,
        )
        db.add(inv)
    inv.total_quantity += 1
    db.commit()

    return build_group_row(db, target_name)


def assign_printing(
    db: Session,
    card_name: str,
    quantity: int,
    set_code: str,
    collector_number: str,
    finish: str = "",
    *,
    from_finish: str | None = None,
) -> InventoryRow:
    """
    The fix-up workflow: moves `quantity` copies of card_name out of a
    source row and into the (set_code, collector_number, finish)
    target row, creating the target if it doesn't exist yet. Never
    changes the card's total_quantity — this only reclassifies which
    printing/finish bucket the copies live in.

    Two use cases share this one function:
    - Resolving a whole printing (the original, still-default case):
      target is a real (set_code, collector_number); source is the
      fully unresolved ("", "", "") bucket. finish on the target
      defaults to "" too — "resolve the printing, leave finish
      unspecified for now" is a valid intermediate state.
    - Resolving just a finish on an already-printing-resolved row:
      caller passes from_finish explicitly (typically "", the
      unspecified finish) with the SAME set_code/collector_number as
      the target — source is (set_code, collector_number, from_finish).

    from_finish=None (the default) means "source from the fully
    unresolved bucket", i.e. the original behavior, unchanged unless
    the caller opts into the finish-only-reassignment case.
    """
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    if not set_code and not collector_number:
        raise ValueError("Set and/or collector number is required to resolve a printing.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    if from_finish is None:
        source_set, source_number, source_finish = "", "", ""
    else:
        source_set, source_number, source_finish = set_code, collector_number, normalize_finish(from_finish)

    if (source_set, source_number, source_finish) == (set_code, collector_number, finish):
        raise ValueError("Source and target printing/finish are the same — nothing to assign.")

    source = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == source_set,
            Inventory.collector_number == source_number,
            Inventory.finish == source_finish,
        )
        .one_or_none()
    )
    available = source.total_quantity if source else 0
    if quantity > available:
        raise ValueError(
            f"Only {available} unresolved cop{'y' if available == 1 else 'ies'} of "
            f"'{card_name}' available to assign."
        )

    target = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
        )
        .one_or_none()
    )
    if target is None:
        target = Inventory(
            card_name=card_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            total_quantity=0,
        )
        db.add(target)

    source.total_quantity -= quantity
    target.total_quantity += quantity
    db.commit()

    return build_group_row(db, card_name)


def adjust_quantity(
    db: Session,
    card_name: str,
    new_total_quantity: int,
    set_code: str = "",
    collector_number: str = "",
    finish: str = "",
) -> InventoryRow:
    """
    Sets one printing+finish row's total_quantity directly (used for
    both +/- nudges and manual edits from the UI — the frontend
    computes the new absolute value). Blocked if it would drop the
    *card's* total (this row plus every other printing/finish row of
    the same name) below what's currently checked out across decks —
    deck assignments aren't printing-specific yet, so availability is
    only meaningful at the whole-card level. No force option: reducing
    inventory below what's checked out always requires checking cards
    in first.
    """
    if new_total_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
        )
        .one_or_none()
    )
    if inv is None:
        raise ValueError(f"'{card_name}' not found in inventory for that printing.")

    decks = _decks_for(db, card_name)
    checked_out = sum(d.quantity for d in decks)

    other_printings_total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(
            Inventory.card_name == card_name,
            ~(
                (Inventory.set_code == set_code)
                & (Inventory.collector_number == collector_number)
                & (Inventory.finish == finish)
            ),
        )
        .scalar()
    )

    if other_printings_total + new_total_quantity < checked_out:
        raise BlockedDeleteError(card_name, decks)

    inv.total_quantity = new_total_quantity
    db.commit()

    return build_group_row(db, card_name)


def delete_card(
    db: Session,
    card_name: str,
    set_code: str = "",
    collector_number: str = "",
    finish: str = "",
    force: bool = False,
) -> None:
    """
    Removes one printing+finish row. Blocked by default only if
    removing it would drop the card's total below what's checked out
    across decks (i.e. the other rows alone can't cover it) — raises
    BlockedDeleteError so the caller can surface a 409 with the deck
    breakdown and let the user confirm. With force=True, deletes the
    deck_assignments too in that case.
    """
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
        )
        .one_or_none()
    )
    if inv is None:
        raise ValueError(f"'{card_name}' not found in inventory for that printing.")

    decks = _decks_for(db, card_name)
    checked_out = sum(d.quantity for d in decks)

    other_printings_total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(
            Inventory.card_name == card_name,
            ~(
                (Inventory.set_code == set_code)
                & (Inventory.collector_number == collector_number)
                & (Inventory.finish == finish)
            ),
        )
        .scalar()
    )
    would_shortfall = decks and other_printings_total < checked_out

    if would_shortfall and not force:
        raise BlockedDeleteError(card_name, decks)
    if would_shortfall and force:
        # Deck assignments are printing-concrete (see models.py) — only
        # the ones actually pinned to *this* printing+finish become
        # invalid; assignments drawn from other printings/finishes, or
        # the unresolved bucket, are untouched.
        db.query(DeckAssignment).filter(
            DeckAssignment.card_name == card_name,
            DeckAssignment.set_code == set_code,
            DeckAssignment.collector_number == collector_number,
            DeckAssignment.finish == finish,
        ).delete()

    db.delete(inv)
    db.commit()


def delete_card_group(db: Session, card_name: str, force: bool = False) -> None:
    """
    Deletes every printing row for card_name — the group-level delete
    button on Manage Collection's main (collapsed) table row, mirroring
    the old single-row delete semantics now that a name can span
    multiple printing rows. With force=True, deletes the
    deck_assignments too.
    """
    printings = db.query(Inventory).filter(Inventory.card_name == card_name).all()
    if not printings:
        raise ValueError(f"'{card_name}' not found in inventory.")

    decks = _decks_for(db, card_name)
    if decks and not force:
        raise BlockedDeleteError(card_name, decks)
    if force and decks:
        db.query(DeckAssignment).filter(DeckAssignment.card_name == card_name).delete()

    for inv in printings:
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
    typed name. A pasted decklist carries no set/number info, so every
    add lands in the unresolved bucket, creating it if this name's
    copies are all currently resolved to specific printings — use the
    Manage Collection fix-up workflow afterward to assign copies to
    specific printings.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

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

        inv = (
            db.query(Inventory)
            .filter(
                Inventory.card_name == matched_name,
                Inventory.set_code == "",
                Inventory.collector_number == "",
                Inventory.finish == "",
            )
            .one_or_none()
        )
        if inv is None:
            inv = Inventory(card_name=matched_name, total_quantity=0)
            db.add(inv)
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

    A pasted line carries no set/number info, so removal draws from the
    unresolved bucket first, then falls back to specific printings (in
    set/number order) if the unresolved bucket alone isn't enough —
    preferring to consume the least-specific data before touching
    copies already resolved to a known printing.
    """
    parsed_lines = parse_decklist(decklist_text)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

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

        printings = db.query(Inventory).filter(Inventory.card_name == matched_name).all()
        printings.sort(
            key=lambda r: (r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish)
        )
        group_total = sum(p.total_quantity for p in printings)

        decks = _decks_for(db, matched_name)
        checked_out = sum(d.quantity for d in decks)

        already_claimed = already_removed.get(matched_name, 0)
        removable_floor = checked_out  # can't drop the card's total below what's checked out
        currently_removable = max(0, group_total - already_claimed - removable_floor)

        to_remove = min(currently_removable, parsed.quantity)

        if to_remove > 0:
            remaining = to_remove
            for p in printings:
                if remaining <= 0:
                    break
                take = min(p.total_quantity, remaining)
                p.total_quantity -= take
                remaining -= take
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
