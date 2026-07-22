import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice
from .pokemon_common import POKEMON_API_BASE, HEADERS, extract_usd_prices
from .pokemon_lookup import lookup_card, lookup_card_printing
from .sets_cache import resolve_pokemon_set_id
from .price_estimation import refresh_estimated_prices

logger = logging.getLogger("mtg_inventory.pokemon_pricing")

REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2
BETWEEN_REQUEST_DELAY_SECONDS = 0.05  # light politeness delay -- TCGdex publishes no hard rate limit, but asks callers to "be considerate"

# Commit price updates in batches rather than one commit for the whole
# run — caps how much work a mid-refresh crash can lose. Owned
# printings are typically in the tens to low hundreds for a personal
# collection, well under this, so most runs commit once at the end
# anyway.
BATCH_COMMIT_SIZE = 50


class PricingError(Exception):
    pass


# In-memory refresh status, mirroring pricing.py's shape so the same
# frontend polling logic (GET /api/pricing/status) works for both games.
_status = {
    "in_progress": False,
    "stage": None,  # "matching" | "estimating" | "committing" | None
    "started_at": None,
    "finished_at": None,
    "cards_processed": 0,
    "total_cards_in_file": None,
    "last_result": None,
    "last_error": None,
}


def get_refresh_status() -> dict:
    return dict(_status)


def _fetch_printing(client: httpx.Client, set_id: str, collector_number: str) -> dict | None:
    """
    Fetches one printing directly by TCGdex's own set id + local
    number, retrying transient failures a couple of times — a run over
    many owned printings has plenty of chances to hit one flaky
    request, and without this a single timeout would throw away
    otherwise-good progress.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(
                f"{POKEMON_API_BASE}/sets/{set_id}/{collector_number}", headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            last_exc = e
            retryable = isinstance(e, httpx.TransportError) or (
                isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in (400, 401, 403)
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
            logger.warning(
                "Printing fetch failed (%s/%s, attempt %d/%d): %s — retrying in %ds",
                set_id, collector_number, attempt, MAX_RETRIES, e, RETRY_BACKOFF_SECONDS,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_exc


def refresh_all_prices(db: Session) -> dict:
    """
    Refreshes prices for every owned printing directly — one request
    per printing via TCGdex's set+number endpoint. Unlike the previous
    provider (paginate the ~20k-card catalog, match against owned
    printings as you go) or the MTG side (one big bulk-price file),
    TCGdex's card-listing endpoint doesn't include pricing at all —
    only name/id/localId — so downloading the whole catalog wouldn't
    even get us prices. Fetching directly is actually the more
    efficient approach anyway for a typical personal collection (tens
    to low hundreds of distinct printings, not tens of thousands).

    Unresolved-bucket rows get an estimated price afterward, same as
    the MTG side (see price_estimation.py).
    """
    inventory_names = {row.card_name for row in db.query(Inventory.card_name).distinct().all()}

    _status.update({
        "in_progress": True,
        "stage": "matching",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "cards_processed": 0,
        "total_cards_in_file": None,
        "last_error": None,
    })

    if not inventory_names:
        _status.update({"in_progress": False, "stage": None, "finished_at": datetime.now(timezone.utc).isoformat()})
        return {"matched": 0, "unmatched": 0, "total_cards": 0, "skipped_errors": 0, "skipped_pages": 0, "estimated": 0}

    owned_printings = (
        db.query(Inventory.card_name, Inventory.set_code, Inventory.collector_number)
        .filter(~((Inventory.set_code == "") & (Inventory.collector_number == "")))
        .distinct()
        .all()
    )
    _status["total_cards_in_file"] = len(owned_printings)

    logger.info(
        "Pokemon price refresh starting for %d owned printings across %d inventory names",
        len(owned_printings), len(inventory_names),
    )

    matched_names: set[str] = set()
    matched_printings_count = 0
    skipped_names: list[str] = []
    pending_since_commit = 0
    now = datetime.now(timezone.utc)

    try:
        with httpx.Client(follow_redirects=True) as client:
            for i, (card_name, set_code, collector_number) in enumerate(owned_printings, start=1):
                set_id = resolve_pokemon_set_id(set_code)
                if set_id is None:
                    # Set code doesn't match anything in the cached sets
                    # list (e.g. a typo, or the sets cache hasn't
                    # refreshed since a very new set was added) — not a
                    # transient failure, so counted the same way as one.
                    skipped_names.append(card_name)
                    _status["cards_processed"] = i
                    continue

                try:
                    card = _fetch_printing(client, set_id, collector_number)
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    logger.warning(
                        "Skipping '%s' (%s #%s) after %d failed attempts: %s",
                        card_name, set_code, collector_number, MAX_RETRIES, e,
                    )
                    skipped_names.append(card_name)
                    _status["cards_processed"] = i
                    time.sleep(BETWEEN_REQUEST_DELAY_SECONDS)
                    continue

                if card is None:
                    # Legitimately not found (e.g. a mis-entered
                    # collector number) -- not an error, just unmatched.
                    _status["cards_processed"] = i
                    time.sleep(BETWEEN_REQUEST_DELAY_SECONDS)
                    continue

                price_usd, price_usd_foil = extract_usd_prices(card)

                existing = (
                    db.query(CardPrice)
                    .filter(
                        CardPrice.card_name == card_name,
                        CardPrice.set_code == set_code,
                        CardPrice.collector_number == collector_number,
                    )
                    .one_or_none()
                )
                if existing is None:
                    existing = CardPrice(card_name=card_name, set_code=set_code, collector_number=collector_number)
                    db.add(existing)

                existing.price_usd = price_usd
                existing.price_usd_foil = price_usd_foil
                existing.is_estimated = False
                existing.updated_at = now
                matched_names.add(card_name)
                matched_printings_count += 1

                pending_since_commit += 1
                if pending_since_commit >= BATCH_COMMIT_SIZE:
                    db.commit()
                    pending_since_commit = 0

                _status["cards_processed"] = i
                time.sleep(BETWEEN_REQUEST_DELAY_SECONDS)

        db.commit()

        _status["stage"] = "estimating"
        estimated = refresh_estimated_prices(db, now)

        _status["stage"] = "committing"
        db.commit()

        result = {
            "matched": len(matched_names),
            "unmatched": len(inventory_names) - len(matched_names),
            "total_cards": len(inventory_names),
            "matched_printings": matched_printings_count,
            "total_printings": len(owned_printings),
            "skipped_errors": len(skipped_names),
            "skipped_pages": 0,  # no pagination in this architecture -- kept for frontend compatibility
            "estimated": estimated,
        }
        logger.info(
            "Pokemon price refresh complete: %d/%d owned printings priced across %d/%d inventory names "
            "(%d skipped, %d unresolved buckets estimated)",
            matched_printings_count, len(owned_printings), result["matched"], result["total_cards"],
            result["skipped_errors"], result["estimated"],
        )

        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_result": result,
        })
        return result

    except Exception as e:
        logger.exception("Pokemon price refresh failed")
        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_error": str(e),
        })
        raise


def refresh_single_price(
    db: Session, card_name: str, set_code: str = "", collector_number: str = ""
) -> CardPrice | None:
    """On-demand lookup for one printing via TCGdex — same purpose as
    pricing.refresh_single_price (the '$' button in Manage Collection)
    but querying TCGdex via pokemon_lookup instead of Scryfall. Not
    meant to be looped over an entire collection; use
    refresh_all_prices for that.

    With set_code/collector_number given, fetches that exact printing
    and stores a real (is_estimated=False) price. Without them (the
    unresolved bucket's own "$" action), this first tries the free
    option — reusing the cheapest already-cached real price among the
    name's other printings (see price_estimation.py) — and only falls
    back to lookup_card's best-guess name match (stored as
    is_estimated=True) when no real price is cached yet for the name.
    Returns None if no match is found.
    """
    set_code = (set_code or "").strip()
    collector_number = (collector_number or "").strip()

    if not set_code and not collector_number:
        estimated = refresh_estimated_prices(db, datetime.now(timezone.utc), {card_name})
        if estimated:
            return (
                db.query(CardPrice)
                .filter(CardPrice.card_name == card_name, CardPrice.set_code == "", CardPrice.collector_number == "")
                .one_or_none()
            )

    if set_code or collector_number:
        result = lookup_card_printing(card_name, set_code, collector_number)
        target_set, target_number, is_estimated = set_code.upper(), collector_number, False
    else:
        result = lookup_card(card_name)
        target_set, target_number, is_estimated = "", "", True

    if result is None:
        return None

    existing = (
        db.query(CardPrice)
        .filter(
            CardPrice.card_name == card_name,
            CardPrice.set_code == target_set,
            CardPrice.collector_number == target_number,
        )
        .one_or_none()
    )
    if existing is None:
        existing = CardPrice(card_name=card_name, set_code=target_set, collector_number=target_number)
        db.add(existing)

    # result["prices"] (the Card Search popup shape) rather than a
    # collapsed price_usd/price_usd_foil pair -- see pokemon_lookup's
    # _normalize. Same primary/secondary convention as
    # pricing.store_known_price: first entry is the non-foil-ish price,
    # second (if any) the foil-ish one.
    prices = result.get("prices") or []
    existing.price_usd = prices[0]["value"] if len(prices) > 0 else None
    existing.price_usd_foil = prices[1]["value"] if len(prices) > 1 else None
    existing.is_estimated = is_estimated
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return existing
