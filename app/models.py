from datetime import datetime
from sqlalchemy import Column, Integer, String, UniqueConstraint, Float, DateTime, Boolean
from .database import Base


class Inventory(Base):
    """
    Identity is (card_name, set_code, collector_number) — the same
    name can have multiple printings (different sets, or even the same
    set with a different collector number for variant art). set_code
    and collector_number default to "" (empty string), never NULL, to
    represent "unresolved / unspecified printing" — SQLite doesn't
    enforce uniqueness on NULL (two NULLs are never considered equal),
    so an empty-string sentinel is what actually makes "at most one
    unresolved row per name" a real constraint instead of an
    app-level assumption. See inventory_admin.py's fix-up workflow for
    how unresolved rows get reconciled to specific printings over time.
    """
    __tablename__ = "inventory"

    card_name = Column(String, primary_key=True, index=True)
    set_code = Column(String, primary_key=True, nullable=False, default="")
    collector_number = Column(String, primary_key=True, nullable=False, default="")
    total_quantity = Column(Integer, nullable=False, default=0)


class DeckAssignment(Base):
    """
    Identity is (card_name, deck_name, set_code, collector_number) — a
    deck can hold several printings of the same name as separate rows
    (e.g. 2 copies drawn from an unresolved bucket plus 1 from a
    specific printing). set_code/collector_number default to "" (the
    same sentinel as Inventory/CardPrice), used when a checkout wasn't
    pinned to a specific printing — see checkout.py's cheapest-first
    draw-down and printing-pinning, and parser.py for how a pasted
    line's trailing "(SET) NUM" becomes a pin. A checked-out row is
    always printing-concrete once created (even the "" / "" row is a
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
    quantity = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "card_name", "deck_name", "set_code", "collector_number", name="uq_card_deck_printing"
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
    collector_number), including the "" / "" unresolved sentinel — a
    printing's price is its own, so pricing has to follow identity the
    same way inventory does. A row at the unresolved key is always
    is_estimated=True: since we don't know which printing those copies
    actually are, that price is a stand-in (the cheapest known price
    among the name's other, resolved printings — see
    price_estimation.py) rather than a real fetched price. A resolved
    printing's row can also end up is_estimated=True temporarily, if a
    single-card refresh only had a name to go on (see
    pricing.refresh_single_price).
    """
    __tablename__ = "card_prices"

    card_name = Column(String, primary_key=True)
    set_code = Column(String, primary_key=True, nullable=False, default="")
    collector_number = Column(String, primary_key=True, nullable=False, default="")
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
