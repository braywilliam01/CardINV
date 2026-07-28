from dataclasses import dataclass, field
from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from .models import Inventory, DeckAssignment, CardPrice
from .parser import parse_decklist
from .csv_import import parse_csv_rows
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


def _norm_location(location: str | None) -> str:
    """Empty string (never None) is the 'not yet assigned a location'
    sentinel — same convention as set_code/collector_number/finish.
    No case-canonicalization (unlike normalize_finish): a location is
    free text a user types (e.g. "Box 3", "Binder A"), not a curated
    vocabulary to canonicalize against."""
    return (location or "").strip()


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
    level — see InventoryRow. price_usd/is_estimated mirror CardPrice
    for this exact printing/finish — is_estimated means the price is a
    stand-in (cheapest known printing, or Scryfall/TCGdex's own
    best-guess name match) rather than a fetch for this specific
    printing; see price_estimation.py.
    """
    set_code: str
    collector_number: str
    finish: str
    location: str
    total_quantity: int
    is_unresolved: bool
    is_finish_unspecified: bool
    is_no_location: bool
    price_usd: float | None = None
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
    line_value = round(price_usd * inv.total_quantity, 2) if price_usd is not None else None
    return PrintingRow(
        set_code=inv.set_code,
        collector_number=inv.collector_number,
        finish=inv.finish,
        location=inv.location,
        total_quantity=inv.total_quantity,
        is_unresolved=(inv.set_code == "" and inv.collector_number == ""),
        is_finish_unspecified=(inv.finish == ""),
        is_no_location=(inv.location == ""),
        price_usd=price_usd,
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
        key=lambda r: (
            r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish, r.location,
        )
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
    no_location_only: bool = False,
    location: str | None = None,
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

    # One row per card name: total quantity, whether any of its
    # printing rows is the unresolved ("", "") sentinel, and whether
    # any of its rows has no location assigned yet.
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
            func.max(
                case((Inventory.location == "", 1), else_=0)
            ).label("has_no_location"),
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
    if no_location_only:
        query = query.filter(inv_agg.c.has_no_location == 1)
    if location:
        # Substring match (like `search`'s card-name matching, not an
        # exact match) — a user typing "Box" should see everything
        # under "Box A"/"Box B"/"Box 3".
        query = query.filter(
            inv_agg.c.card_name.in_(
                db.query(Inventory.card_name).filter(Inventory.location.ilike(f"%{location}%"))
            )
        )

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
            key=lambda r: (
                r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish, r.location,
            )
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
    location: str = "",
) -> InventoryRow:
    """
    Creates one printing row: (card_name, set_code, collector_number,
    finish, location). Leaving set_code/collector_number blank
    creates/targets the 'unresolved' bucket for that name; leaving
    finish blank creates/targets the 'unspecified' finish for whatever
    printing was given; leaving location blank creates/targets the
    'not yet assigned' location — the same behavior as before
    per-printing tracking existed, extended with two more independent
    axes. Blocks case-insensitive exact duplicates of the same
    printing+finish+location (not the same fuzzy-match threshold as
    bulk_add_cards/add_one_copy: a fuzzy threshold that's fine when the
    worst case is "merges into the closest match" is too aggressive
    once the action is "block card creation entirely" — plenty of
    distinct real card names are only a few characters apart and would
    otherwise get wrongly rejected).
    """
    card_name = card_name.strip()
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    location = _norm_location(location)
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
            Inventory.location == location,
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
            location=location,
            total_quantity=total_quantity,
        )
    )
    db.commit()

    return build_group_row(db, card_name)


def get_owned_quantity(
    db: Session,
    card_name: str,
    set_code: str = "",
    collector_number: str = "",
    finish: str | None = None,
    location: str | None = None,
) -> int:
    """
    Fuzzy-matches card_name against inventory (same threshold as bulk
    add/remove). If set_code/collector_number are given, returns that
    printing's quantity — summed across every finish and location,
    unless `finish`/`location` are also given (a real value or
    explicitly ""), in which case the result is filtered to just that
    finish and/or that location. Without set_code/collector_number,
    returns the total across every printing, finish, and location of
    the name. Powers Card Search's '# in inventory' figure, which
    shows "how many of this printing, in any finish/location, do I
    own" since Card Search doesn't know which finish or location the
    user's copies are until they've actually been added with one.
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
        if location is not None:
            query = query.filter(Inventory.location == _norm_location(location))
        return query.scalar()

    total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(Inventory.card_name == matched_name)
        .scalar()
    )
    return total


def add_one_copy(
    db: Session,
    card_name: str,
    set_code: str = "",
    collector_number: str = "",
    finish: str = "",
    location: str = "",
) -> InventoryRow:
    """
    Increments one exact printing+finish+location row by one
    (fuzzy-matching only the card name, to avoid creating "Sol Ring"
    vs "sol ring" duplicates), creating that row with quantity 1 if it
    doesn't exist yet. Powers Card Search's "Add to Inventory" button —
    always adds exactly one copy per click. When Card Search knows the
    exact printing (set_code/collector_number from the lookup result),
    that's what gets incremented; otherwise it falls back to the
    unresolved bucket, same as before per-printing tracking existed.
    finish defaults to "" (unspecified) the same way — only set when
    the caller actually knows which finish this copy is (e.g. a
    specific price-variant "Add" action). location defaults to ""
    (not yet assigned) — Card Search has no location UI, so this
    caller never passes one.
    """
    card_name = card_name.strip()
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    location = _norm_location(location)

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
            Inventory.location == location,
        )
        .one_or_none()
    )
    if inv is None:
        inv = Inventory(
            card_name=target_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            location=location,
            total_quantity=0,
        )
        db.add(inv)
    inv.total_quantity += 1
    db.commit()

    return build_group_row(db, target_name)


def _location_sort_key(location: str) -> tuple[bool, str]:
    """"" (not yet assigned) sorts first, then alphabetical — the
    deterministic "which location's stock gets consumed first when the
    caller doesn't specify one" order used by assign_printing's
    source-side drain, bulk_remove_cards, and
    availability.get_location_availability's checked-out subtraction."""
    return (location != "", location)


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
    source row (or rows) and into the (set_code, collector_number,
    finish) target row, creating the target if it doesn't exist yet.
    Never changes the card's total_quantity — this only reclassifies
    which printing/finish bucket the copies live in.

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

    This function stays location-blind by design (see assign_location
    for relocating copies between locations) — but the source
    (card_name, source_set, source_number, source_finish) key can now
    match *several* Inventory rows if those copies are split across
    locations, so the source side sums across every matching location
    row and drains them in deterministic order (see
    _location_sort_key) rather than assuming a single row. The target
    is always written at location="" — resolving a printing or finish
    through this location-blind function leaves the result "not yet
    assigned" a location, the same layered-default precedent already
    established for finish (printing resolution already leaves finish
    unspecified by default).
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

    source_rows = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == source_set,
            Inventory.collector_number == source_number,
            Inventory.finish == source_finish,
        )
        .all()
    )
    source_rows.sort(key=lambda r: _location_sort_key(r.location))
    available = sum(r.total_quantity for r in source_rows)
    if quantity > available:
        raise ValueError(
            f"Only {available} unresolved cop{'y' if available == 1 else 'ies'} of "
            f"'{card_name}' available to assign."
        )

    remaining = quantity
    for row in source_rows:
        if remaining <= 0:
            break
        take = min(row.total_quantity, remaining)
        row.total_quantity -= take
        remaining -= take
        if row.total_quantity == 0:
            # Drained empty -- delete rather than leave a 0-quantity
            # row sitting around forever. Left alone, a fully-resolved
            # card would still trip has_unresolved/has_no_location
            # indefinitely, since those check "does *any* row have the
            # sentinel value" and don't know a 0-quantity row is really
            # gone in every way that matters. adjust_quantity, by
            # contrast, deliberately leaves an explicit 0 in place --
            # that's a direct user edit (they might restock the same
            # bucket), not automated drain-to-nothing.
            db.delete(row)

    target = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == "",
        )
        .one_or_none()
    )
    if target is None:
        target = Inventory(
            card_name=card_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            location="",
            total_quantity=0,
        )
        db.add(target)

    target.total_quantity += quantity
    db.commit()

    return build_group_row(db, card_name)


def assign_location(
    db: Session,
    card_name: str,
    quantity: int,
    location: str,
    set_code: str = "",
    collector_number: str = "",
    finish: str = "",
    *,
    from_location: str = "",
) -> InventoryRow:
    """
    Moves `quantity` copies of one exact (card_name, set_code,
    collector_number, finish) row from `from_location` (default "" —
    the not-yet-assigned bucket) to `location`. set_code/
    collector_number/finish default to "" so this also works on the
    fully-unresolved / unspecified-finish bucket — relocating copies
    is independent of whether their printing or finish is known yet.

    Mirrors assign_printing's shape (source/target rows, quantity
    moved, the card's total_quantity never changes) but is a
    standalone function rather than a mode of assign_printing: unlike
    assign_printing, which hard-requires a concrete target printing,
    this must work even when set_code/collector_number are both ""
    (relocating unresolved-printing copies between locations is a
    valid, common case with no printing to give assign_printing).
    """
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    location = _norm_location(location)
    from_location = _norm_location(from_location)

    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    if location == from_location:
        raise ValueError("Source and target location are the same — nothing to assign.")

    source = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == from_location,
        )
        .one_or_none()
    )
    available = source.total_quantity if source else 0
    if quantity > available:
        raise ValueError(
            f"Only {available} cop{'y' if available == 1 else 'ies'} of "
            f"'{card_name}' available at that location to move."
        )

    target = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == location,
        )
        .one_or_none()
    )
    if target is None:
        target = Inventory(
            card_name=card_name,
            set_code=set_code,
            collector_number=collector_number,
            finish=finish,
            location=location,
            total_quantity=0,
        )
        db.add(target)

    source.total_quantity -= quantity
    if source.total_quantity == 0:
        # See assign_printing's matching comment -- an automated drain
        # that empties a row deletes it, rather than leaving a stale
        # 0-quantity row that'd keep tripping has_no_location forever.
        db.delete(source)
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
    location: str = "",
) -> InventoryRow:
    """
    Sets one printing+finish+location row's total_quantity directly
    (used for both +/- nudges and manual edits from the UI — the
    frontend computes the new absolute value). Blocked if it would
    drop the *card's* total (this row plus every other printing/
    finish/location row of the same name) below what's currently
    checked out across decks — deck assignments aren't printing- or
    location-specific yet, so availability is only meaningful at the
    whole-card level. No force option: reducing inventory below what's
    checked out always requires checking cards in first.
    """
    if new_total_quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    location = _norm_location(location)

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == location,
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
                & (Inventory.location == location)
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
    location: str = "",
    force: bool = False,
) -> None:
    """
    Removes one printing+finish+location row. Blocked by default only
    if removing it would drop the card's total below what's checked
    out across decks (i.e. the other rows alone can't cover it) —
    raises BlockedDeleteError so the caller can surface a 409 with the
    deck breakdown and let the user confirm. With force=True, deletes
    the deck_assignments too in that case.
    """
    set_code, collector_number = _norm_printing(set_code, collector_number)
    finish = normalize_finish(finish)
    location = _norm_location(location)

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == card_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == location,
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
                & (Inventory.location == location)
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
        # the unresolved bucket, are untouched. Deck assignments have
        # no location column (decks stay location-blind), so this
        # still only keys on printing+finish.
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


def _bulk_add_one(
    db: Session,
    all_card_names: list[str],
    card_name_query: str,
    set_code: str,
    collector_number: str,
    finish: str,
    quantity: int,
    location: str,
) -> tuple[str, str, str]:
    """
    Fuzzy-matches card_name_query against known names, then adds
    `quantity` to the exact (name, set_code, collector_number, finish,
    location) row, creating it if it doesn't exist yet. Shared by both
    the pasted-decklist and CSV bulk-add paths so a pinned printing (or
    finish) behaves identically no matter which input format supplied
    it — leave set_code/collector_number/finish "" for the old
    unresolved/unspecified behavior. Returns (resolved_name, status,
    message).
    """
    matched_name = find_best_match(card_name_query, all_card_names, threshold=BULK_MATCH_THRESHOLD)

    if matched_name is None:
        # No close match — create a new inventory entry.
        new_name = card_name_query
        db.add(Inventory(
            card_name=new_name, set_code=set_code, collector_number=collector_number,
            finish=finish, total_quantity=quantity, location=location,
        ))
        # The session's autoflush is off (see get_db) -- without this,
        # a later line in the same batch that fuzzy-matches back to
        # new_name (e.g. the same card pasted twice) would run its
        # lookup query before this insert reaches the DB, find nothing,
        # and try to INSERT the identical primary key again, crashing
        # with a UNIQUE-constraint IntegrityError instead of just
        # adding to the row this line just created.
        db.flush()
        all_card_names.append(new_name)  # so later lines in this same batch can match it
        return new_name, "created", f"'{new_name}' was new — added to inventory."

    inv = (
        db.query(Inventory)
        .filter(
            Inventory.card_name == matched_name,
            Inventory.set_code == set_code,
            Inventory.collector_number == collector_number,
            Inventory.finish == finish,
            Inventory.location == location,
        )
        .one_or_none()
    )
    if inv is None:
        inv = Inventory(
            card_name=matched_name, set_code=set_code, collector_number=collector_number,
            finish=finish, total_quantity=0, location=location,
        )
        db.add(inv)
        db.flush()  # same reasoning as above -- make this row visible to the next duplicate line
    inv.total_quantity += quantity
    return matched_name, "ok", ""


def bulk_add_cards(
    db: Session,
    decklist_text: str,
    location: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """
    Adds quantities to inventory from a pasted list, all landing at
    `location` (required — this is the paste-based bulk-add flow used
    for physically adding a new stack of cards to one box/binder at a
    time, so every line in one call shares the same destination).
    Fuzzy-matches each line against existing card names first (so
    "Ligtning Bolt" adds to the existing "Lightning Bolt" row instead
    of creating a near-duplicate); if nothing matches closely enough, a
    new card is created with the typed name. A line with a trailing
    "(SET) 123"/"[SET] 123"/"SET-123" pins that exact printing (see
    parser.py); otherwise the add lands in the unresolved-printing
    bucket at the given location, creating it if this name's copies at
    that location don't already have such a row. A pasted decklist has
    no finish-pinning syntax, so finish is always left unspecified here
    — use the CSV bulk-add path (bulk_add_cards_csv) or the Manage
    Collection fix-up workflow to assign a finish.
    """
    location = _norm_location(location)
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

        resolved_name, status, message = _bulk_add_one(
            db, all_card_names, parsed.card_name,
            parsed.set_code, parsed.collector_number, "",
            parsed.quantity, location,
        )
        result.lines.append(
            BulkLineResult(parsed.raw_line, resolved_name, parsed.quantity, parsed.quantity, status, message)
        )

    db.commit()
    return result


def bulk_add_cards_csv(
    db: Session,
    csv_text: str,
    location: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """
    CSV counterpart to bulk_add_cards — same additive, single-location
    semantics, but each row can carry its own Set/Collector Number/Foil
    columns (see csv_import.parse_csv_rows), so a CSV bulk-add can pin
    an exact printing *and* finish per row, where a pasted decklist
    line can only ever pin a printing.
    """
    location = _norm_location(location)
    rows, skipped_basic_lands = parse_csv_rows(csv_text, ignore_basic_lands)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

    result = BulkResult(skipped_basic_lands=skipped_basic_lands)

    for row in rows:
        if not row.valid:
            result.warnings.append(f"Could not parse row: '{row.raw_line}'")
            result.lines.append(BulkLineResult(row.raw_line, "", 0, 0, "unparseable"))
            continue

        resolved_name, status, message = _bulk_add_one(
            db, all_card_names, row.card_name,
            row.set_code, row.collector_number, row.finish,
            row.quantity, location,
        )
        result.lines.append(
            BulkLineResult(row.raw_line, resolved_name, row.quantity, row.quantity, status, message)
        )

    db.commit()
    return result


def _bulk_remove_one(
    db: Session,
    all_card_names: list[str],
    card_name_query: str,
    set_code: str,
    collector_number: str,
    finish: str,
    quantity: int,
    location: str,
    already_removed: dict[str, int],
) -> tuple[str, int, str, str]:
    """
    Mirrors _bulk_add_one for removal: fuzzy-matches the name, then
    removes up to `quantity` from `location`, never dropping the
    card's total (across every location) below what's checked out
    across decks. When set_code/collector_number pin an exact printing
    (optionally narrowed further by finish), removal draws only from
    matching printing rows; otherwise it draws from the unresolved
    bucket first, then any specific printing, the same priority as
    before per-line pinning existed for removal. `already_removed` is
    a running guard (keyed by resolved name) against duplicate
    lines/rows in one batch double-claiming the same stock. Returns
    (resolved_name, applied_qty, status, message).
    """
    matched_name = find_best_match(card_name_query, all_card_names, threshold=BULK_MATCH_THRESHOLD)

    if matched_name is None:
        return card_name_query, 0, "not_found", f"'{card_name_query}' not found in inventory."

    decks = _decks_for(db, matched_name)
    checked_out = sum(d.quantity for d in decks)

    # Global floor: never drop the card's total (across every
    # location) below what's checked out anywhere — decks are
    # location-blind, so this has to stay a whole-card check.
    all_locations_total = (
        db.query(func.coalesce(func.sum(Inventory.total_quantity), 0))
        .filter(Inventory.card_name == matched_name)
        .scalar()
    )
    global_room = max(0, all_locations_total - checked_out)

    location_printings = (
        db.query(Inventory)
        .filter(Inventory.card_name == matched_name, Inventory.location == location)
        .all()
    )

    pinned_printing = bool(set_code and collector_number)
    pinned_finish = bool(finish)
    candidates = location_printings
    if pinned_printing:
        candidates = [p for p in candidates if p.set_code == set_code and p.collector_number == collector_number]
    if pinned_finish:
        candidates = [p for p in candidates if p.finish == finish]

    candidates.sort(
        key=lambda r: (r.set_code != "" or r.collector_number != "", r.set_code, r.collector_number, r.finish)
    )
    candidates_total = sum(p.total_quantity for p in candidates)

    already_claimed = already_removed.get(matched_name, 0)
    currently_removable = max(0, min(candidates_total, global_room) - already_claimed)

    to_remove = min(currently_removable, quantity)

    if to_remove > 0:
        remaining = to_remove
        for p in candidates:
            if remaining <= 0:
                break
            take = min(p.total_quantity, remaining)
            p.total_quantity -= take
            remaining -= take
            if p.total_quantity == 0:
                # See assign_printing's matching comment -- an
                # automated drain that empties a row deletes it.
                db.delete(p)
        already_removed[matched_name] = already_claimed + to_remove

    status = "ok" if to_remove == quantity else ("partial" if to_remove > 0 else "not_found")

    location_label = f"'{location}'" if location else "the unassigned-location bucket"
    printing_label = f" as {set_code} #{collector_number}" if pinned_printing else ""
    if status == "partial":
        message = (
            f"Only removed {to_remove}/{quantity} from {location_label}{printing_label} — the rest isn't "
            f"there, or is checked out across decks and can't be removed until checked in."
        )
    elif status == "not_found" and to_remove == 0 and candidates_total == 0:
        if pinned_printing and location_printings:
            message = f"'{matched_name}' isn't in {location_label}{printing_label} — other printings exist there, but not this one."
        else:
            message = f"'{matched_name}' has 0 in {location_label}{printing_label} — nothing to remove there."
    elif status == "not_found":
        message = f"'{matched_name}' in {location_label}{printing_label} is fully checked out or unavailable — nothing removed."
    else:
        message = ""

    return matched_name, to_remove, status, message


def bulk_remove_cards(
    db: Session,
    decklist_text: str,
    location: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """
    Removes quantities from inventory from a pasted list (e.g. pulling
    damaged or lost cards physically out of one named box/binder).
    `location` (required) scopes removal to just that location's
    stock — someone pulling cards out of a specific box shouldn't have
    the removal silently drain a *different* location's count instead,
    which would defeat the whole point of tracking location. Still
    only reduces down to what's currently checked out across decks —
    never below, since that would make a deck's assignment exceed what
    you own; that floor stays global (decks are location-blind — see
    models.py) even though the removal itself is location-scoped. If
    the requested removal would go below either limit, only the safe
    portion is removed and the line is marked "partial".

    A line with a trailing "(SET) 123" pins removal to that exact
    printing within the location (see parser.py); an unpinned line
    draws from the unresolved bucket first, then falls back to
    specific printings (in set/number order) if the unresolved bucket
    alone isn't enough — preferring to consume the least-specific data
    before touching copies already resolved to a known printing.
    """
    location = _norm_location(location)
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

        resolved_name, applied_qty, status, message = _bulk_remove_one(
            db, all_card_names, parsed.card_name,
            parsed.set_code, parsed.collector_number, "",
            parsed.quantity, location, already_removed,
        )
        result.lines.append(
            BulkLineResult(parsed.raw_line, resolved_name, parsed.quantity, applied_qty, status, message)
        )

    db.commit()
    return result


def bulk_remove_cards_csv(
    db: Session,
    csv_text: str,
    location: str,
    ignore_basic_lands: bool = True,
) -> BulkResult:
    """CSV counterpart to bulk_remove_cards — see bulk_add_cards_csv for
    why this is a separate path from the ManaBox reconcile import."""
    location = _norm_location(location)
    rows, skipped_basic_lands = parse_csv_rows(csv_text, ignore_basic_lands)
    all_card_names = [row.card_name for row in db.query(Inventory.card_name).distinct().all()]

    result = BulkResult(skipped_basic_lands=skipped_basic_lands)
    already_removed: dict[str, int] = {}

    for row in rows:
        if not row.valid:
            result.warnings.append(f"Could not parse row: '{row.raw_line}'")
            result.lines.append(BulkLineResult(row.raw_line, "", 0, 0, "unparseable"))
            continue

        resolved_name, applied_qty, status, message = _bulk_remove_one(
            db, all_card_names, row.card_name,
            row.set_code, row.collector_number, row.finish,
            row.quantity, location, already_removed,
        )
        result.lines.append(
            BulkLineResult(row.raw_line, resolved_name, row.quantity, applied_qty, status, message)
        )

    db.commit()
    return result
