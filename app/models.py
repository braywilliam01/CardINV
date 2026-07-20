from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, Float, DateTime, Boolean
from sqlalchemy.orm import relationship
from .database import Base


class Inventory(Base):
    __tablename__ = "inventory"

    card_name = Column(String, primary_key=True, index=True)
    total_quantity = Column(Integer, nullable=False, default=0)

    assignments = relationship(
        "DeckAssignment", back_populates="card", cascade="all, delete-orphan"
    )


class DeckAssignment(Base):
    __tablename__ = "deck_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    card_name = Column(String, ForeignKey("inventory.card_name"), nullable=False, index=True)
    deck_name = Column(String, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)

    card = relationship("Inventory", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("card_name", "deck_name", name="uq_card_deck"),
    )


class CardPrice(Base):
    """
    Cached pricing data, refreshed either via the weekly bulk job or an
    on-demand single-card lookup. Kept as its own table (rather than
    columns on Inventory) so a price refresh never touches inventory
    counts, and a missing row just means "not priced yet" instead of
    needing a sentinel value.
    """
    __tablename__ = "card_prices"

    card_name = Column(String, ForeignKey("inventory.card_name"), primary_key=True)
    price_usd = Column(Float, nullable=True)
    price_usd_foil = Column(Float, nullable=True)
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
