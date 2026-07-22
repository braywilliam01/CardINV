from datetime import datetime
from sqlalchemy import Column, Integer, String, UniqueConstraint, Float, DateTime, Boolean
from .database import Base


class Inventory(Base):
    """
    Identity is (card_name, set_code, collector_number, finish) — the
    same name can have multiple printings (different sets, or even the
    same set with a different collector number for variant art), and
    the same printing can have multiple finishes (e.g. Holofoil vs
    Reverse Holofoil). set_code, collector_number, and finish all
    default to "" (empty string), never NULL, to represent "unresolved
    / unspecified" on that dimension — SQLite doesn't enforce
    uniqueness on NULL (two NULLs are never considered equal), so an
    empty-string sentinel is what actually makes "at most one
    unresolved row per name/printing" a real constraint instead of an
    app-level assumption.

    Printing-unresolved (set_code == "" and collector_number == "")
    and finish-unspecified (finish == "") are independent axes — a row
    can be in either, neither, or both states. finish == "" is never
    an assumed base finish (e.g. Nonfoil/Normal): a copy's finish is
    only known once something actually recorded it (Card Search's
    per-variant Add, a manual entry, or the fix-up workflow), so
    "" means genuinely unspecified. See inventory_admin.py's fix-up
    workflow for how both unresolved printings and unspecified
    finishes get reconciled over time.
    """
    __tablename__ = "inventory"

    card_name = Column(String, primary_key=True, index=True)
    set_code = Column(String, primary_key=True, nullable=False, default="")
    collector_number = Column(String, primary_key=True, nullable=False, default="")
    finish = Column(String, primary_key=True, nullable=False, default="")
    total_quantity = Column(Integer, nullable=False, default=0)


class DeckAssignment(Base):
    """
    Identity is (card_name, deck_name, set_code, collector_number,
    finish) — a deck can hold several printings (or finishes of the
    same printing) of the same name as separate rows (e.g. 2 copies
    drawn from an unresolved bucket plus 1 from a specific printing).
    set_code/collector_number/finish default to "" (the same sentinel
    as Inventory/CardPrice), used when a checkout wasn't pinned to a
    specific printing — see checkout.py's cheapest-first draw-down and
    printing-pinning, and parser.py for how a pasted line's trailing
    "(SET) NUM" becomes a pin. Decklist text has no finish-pinning
    syntax (parser.py deliberately discards foil/etched markers as
    noise) — a checked-out row's finish is always inherited from
    whichever Inventory row it was actually drawn from, never chosen
    directly by a decklist line. A checked-out row is always
    printing-concrete once created (even the "" / "" / "" row is a
    specific, trackable slice of the unresolved bucket) — "pinned vs.
    unpinned" is a property of a *request*, not of a stored row.

    No FK to Inventory: even a single-column FK isn't valid once
    card_name alone isn't unique there, and referential integrity here
    was already informal in practice (e.g. basic lands get assignment
    rows with no Inventory row at all).
    """
    __tablename__ = "deck_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    card_name = Column(String, nullable=False, index=True)
    deck_name = Column(String, nullable=False, index=True)
    set_code = Column(String, nullable=False, default="")
    collector_number = Column(String, nullable=False, default="")
    finish = Column(String, nullable=False, default="")
    quantity = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "card_name", "deck_name", "set_code", "collector_number", "finish",
            name="uq_card_deck_printing",
        ),
    )


class CardPrice(Base):
    """
    Cached pricing data, refreshed either via the weekly bulk job or an
    on-demand single-card lookup. Kept as its own table (rather than
    columns on Inventory) so a price refresh never touches inventory
    counts, and a missing row just means "not priced yet" instead of
    needing a sentinel value.

    Keyed the same way as Inventory: (card_name, set_code,
    collector_number, finish), including the "" sentinels — a specific
    finish's price is its own row now (Holofoil and Reverse Holofoil
    of the same printing are genuinely different market prices), so
    pricing has to follow identity the same way inventory does. A row
    at the fully-unresolved key is always is_estimated=True: since we
    don't know which printing (or finish) those copies actually are,
    that price is a stand-in (the cheapest known price among the
    name's other, resolved printings — see price_estimation.py) rather
    than a real fetched price. A resolved printing's row can also end
    up is_estimated=True temporarily, if a single-card refresh only
    had a name to go on (see pricing.refresh_single_price).

    price_usd_foil predates finish-as-identity and is now vestigial —
    new writes only ever set price_usd (the price of *this* row's own
    finish). It's kept, unwritten, only as a legacy read-fallback for
    finish=="" rows that were priced before this column split existed.
    """
    __tablename__ = "card_prices"

    card_name = Column(String, primary_key=True)
    set_code = Column(String, primary_key=True, nullable=False, default="")
    collector_number = Column(String, primary_key=True, nullable=False, default="")
    finish = Column(String, primary_key=True, nullable=False, default="")
    price_usd = Column(Float, nullable=True)
    price_usd_foil = Column(Float, nullable=True)
    is_estimated = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=True)


class DeckMeta(Base):
    """
    Per-deck metadata that isn't tied to any single card assignment —
    favorite status and when the deck was last checked out to / checked
    in from. Kept as its own row (rather than derived from
    deck_assignments) so a favorited deck keeps its favorite/history
    even if every card is checked back in and it briefly has zero
    assignments.
    """
    __tablename__ = "deck_meta"

    deck_name = Column(String, primary_key=True)
    is_favorite = Column(Boolean, nullable=False, default=False)
    last_modified = Column(DateTime, nullable=True)


class CardSearchHistory(Base):
    """
    Cache of the most recently viewed Card Search results, powering the
    Homepage's "Last Viewed" tiles. Not tied to Inventory — a searched
    card doesn't have to be one you own. Trimmed to the most recent few
    rows after every view (see card_lookup.record_card_view), so this
    is a small rolling window, not a full search log.
    """
    __tablename__ = "card_search_history"

    card_name = Column(String, primary_key=True)
    image_url = Column(String, nullable=True)
    mana_cost = Column(String, nullable=True)
    type_line = Column(String, nullable=True)
    viewed_at = Column(DateTime, nullable=False)
