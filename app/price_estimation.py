from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice


def refresh_estimated_prices(db: Session, now: datetime, card_names: set[str] | None = None) -> int:
    """
    For every card name with an unresolved inventory row (copies not
    yet assigned to a specific printing — see models.py), estimates
    that bucket's price as the *cheapest* known real (non-estimated)
    printing price for the name, and upserts it as a CardPrice row at
    the unresolved key ("", "") with is_estimated=True.

    Cheapest rather than average or most-recent, to match the same
    "assume the cheapest printing" philosophy used for deck checkout's
    draw-down rule — a conservative estimate that doesn't overstate
    collection value. A name with no known real-printing price at all
    is left unpriced (no row written), same as any other unmatched
    card.

    `card_names` scopes the estimation pass to just those names (used
    after a single-printing refresh, to avoid a full-table scan for a
    one-card click); omit it to cover every unresolved name (used
    after a bulk refresh). Returns how many estimated rows were
    written.
    """
    query = db.query(Inventory.card_name).filter(Inventory.set_code == "", Inventory.collector_number == "")
    if card_names is not None:
        query = query.filter(Inventory.card_name.in_(card_names))
    unresolved_names = {row.card_name for row in query.distinct().all()}
    if not unresolved_names:
        return 0

    cheapest = (
        db.query(CardPrice.card_name, func.min(CardPrice.price_usd).label("min_price"))
        .filter(
            CardPrice.card_name.in_(unresolved_names),
            CardPrice.is_estimated.is_(False),
            CardPrice.price_usd.isnot(None),
        )
        .group_by(CardPrice.card_name)
        .all()
    )

    written = 0
    for card_name, min_price in cheapest:
        existing = (
            db.query(CardPrice)
            .filter(CardPrice.card_name == card_name, CardPrice.set_code == "", CardPrice.collector_number == "")
            .one_or_none()
        )
        if existing is None:
            existing = CardPrice(card_name=card_name, set_code="", collector_number="")
            db.add(existing)

        existing.price_usd = min_price
        existing.price_usd_foil = None  # which printing (and thus foil status) is unknown for an unresolved bucket
        existing.is_estimated = True
        existing.updated_at = now
        written += 1

    db.commit()
    return written
