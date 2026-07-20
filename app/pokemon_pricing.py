import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice
from .pokemon_common import POKEMON_API_BASE, HEADERS, extract_usd_prices
from .pokemon_lookup import lookup_card, lookup_card_printing
from .price_estimation import refresh_estimated_prices

logger = logging.getLogger("mtg_inventory.pokemon_pricing")

PAGE_SIZE = 250  # pokemontcg.io's max page size
BETWEEN_PAGE_DELAY_SECONDS = 0.05  # light politeness delay; ~82 pages total
PAGE_TIMEOUT_SECONDS = 30
MAX_PAGE_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3

# Commit price updates in batches rather than one commit for the whole
# ~20k-card catalog — same reasoning as the MTG side: caps how much
# work a mid-refresh crash can lose.
BATCH_COMMIT_SIZE = 250


class PricingError(Exception):
    pass


# In-memory refresh status, mirroring pricing.py's shape so the same
# frontend polling logic (GET /api/pricing/status) works for both games.
_status = {
    "in_progress": False,
    "stage": None,  # "downloading" | "matching" | "committing" | None
    "started_at": None,
    "finished_at": None,
    "cards_processed": 0,
    "total_cards_in_file": None,
    "last_result": None,
    "last_error": None,
}


def get_refresh_status() -> dict:
    return dict(_status)


def _fetch_page(client: httpx.Client, page: int) -> dict:
    """
    Fetches one page of the catalog, retrying failures a few times
    before giving up — an ~82-page fetch has a lot of chances to hit
    one flaky request, and without this a single timeout on page 13
    used to throw away all progress from a run that had already been
    going for minutes. In practice this API has been observed
    returning a spurious 404 for a page well within range (confirmed
    transient — an immediate manual retry succeeded), so this treats
    everything as retryable except the small set of genuinely
    permanent client errors (bad request / auth) that a retry can't
    fix.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_PAGE_RETRIES + 1):
        try:
            resp = client.get(
                f"{POKEMON_API_BASE}/cards",
                params={"page": page, "pageSize": PAGE_SIZE},
                headers=HEADERS,
                timeout=PAGE_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            last_exc = e
            retryable = isinstance(e, httpx.TransportError) or (
                isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in (400, 401, 403)
            )
            if not retryable or attempt == MAX_PAGE_RETRIES:
                raise
            logger.warning(
                "Page %d fetch failed (attempt %d/%d): %s — retrying in %ds",
                page, attempt, MAX_PAGE_RETRIES, e, RETRY_BACKOFF_SECONDS,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_exc


def refresh_all_prices(db: Session) -> dict:
    """
    Pokemontcg.io has no single bulk-price-download file the way
    Scryfall does (their static data dump deliberately excludes
    prices) — so this paginates the full ~20k-card catalog (250/card
    per page, ~82 requests) and matches against *owned printings* as it
    goes (name+set+number — excludes the unresolved bucket, which gets
    an estimated price afterward instead), same shape as the MTG bulk
    refresh otherwise: batched commits, per-card error isolation, and
    live status for polling.
    """
    inventory_names = {row.card_name for row in db.query(Inventory.card_name).distinct().all()}

    _status.update({
        "in_progress": True,
        "stage": "downloading",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "cards_processed": 0,
        "total_cards_in_file": None,
        "last_error": None,
    })

    if not inventory_names:
        _status.update({"in_progress": False, "stage": None, "finished_at": datetime.now(timezone.utc).isoformat()})
        return {"matched": 0, "unmatched": 0, "total_cards": 0, "skipped_errors": 0, "estimated": 0}

    printing_lookup = {
        (r.card_name.lower(), r.set_code, r.collector_number): r.card_name
        for r in db.query(Inventory.card_name, Inventory.set_code, Inventory.collector_number)
        .filter(~((Inventory.set_code == "") & (Inventory.collector_number == "")))
        .all()
    }

    logger.info(
        "Pokemon bulk price refresh starting for %d owned printings across %d inventory names",
        len(printing_lookup), len(inventory_names),
    )

    matched_keys = set()
    skipped_names: list[str] = []
    skipped_pages: list[int] = []
    pending_since_commit = 0
    now = datetime.now(timezone.utc)

    try:
        with httpx.Client(follow_redirects=True) as client:
            # Page 1 determines total_pages for everything after it, so
            # a failure here is a genuine hard-stop — there's no way to
            # know how many pages to expect without it. Every page
            # after this one is treated as skippable: in practice this
            # API has occasionally failed a page 3 times in a row under
            # sustained pagination (confirmed transient — the same page
            # succeeds instantly on a fresh run), and losing one page's
            # ~250 cards from a price refresh is a far better outcome
            # than losing the entire ~20k-card run over it.
            first_body = _fetch_page(client, 1)
            total_count = first_body.get("totalCount") or 0
            _status["total_cards_in_file"] = total_count
            logger.info("Pokemon catalog has %d total cards", total_count)
            total_pages = max(1, -(-total_count // PAGE_SIZE))  # ceil division

            for page in range(1, total_pages + 1):
                if page == 1:
                    body = first_body
                else:
                    try:
                        body = _fetch_page(client, page)
                    except (httpx.TransportError, httpx.HTTPStatusError) as e:
                        logger.warning(
                            "Skipping page %d/%d after %d failed attempts: %s",
                            page, total_pages, MAX_PAGE_RETRIES, e,
                        )
                        skipped_pages.append(page)
                        _status["cards_processed"] = page * PAGE_SIZE
                        time.sleep(BETWEEN_PAGE_DELAY_SECONDS)
                        continue

                cards = body.get("data", [])
                _status["stage"] = "matching"
                for card in cards:
                    name = card.get("name", "")
                    set_code = ((card.get("set") or {}).get("ptcgoCode") or (card.get("set") or {}).get("id") or "").upper()
                    collector_number = card.get("number") or ""
                    key = (name.lower(), set_code, collector_number)
                    canonical = printing_lookup.get(key)
                    if canonical is None:
                        continue

                    try:
                        price_usd, price_usd_foil = extract_usd_prices(card)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Skipping '%s' (%s #%s) — malformed price data from pokemontcg.io",
                            canonical, set_code, collector_number,
                        )
                        skipped_names.append(canonical)
                        continue

                    existing = (
                        db.query(CardPrice)
                        .filter(
                            CardPrice.card_name == canonical,
                            CardPrice.set_code == set_code,
                            CardPrice.collector_number == collector_number,
                        )
                        .one_or_none()
                    )
                    if existing is None:
                        existing = CardPrice(card_name=canonical, set_code=set_code, collector_number=collector_number)
                        db.add(existing)

                    existing.price_usd = price_usd
                    existing.price_usd_foil = price_usd_foil
                    existing.is_estimated = False
                    existing.updated_at = now
                    matched_keys.add(key)

                    pending_since_commit += 1
                    if pending_since_commit >= BATCH_COMMIT_SIZE:
                        db.commit()
                        pending_since_commit = 0

                _status["cards_processed"] = page * PAGE_SIZE
                time.sleep(BETWEEN_PAGE_DELAY_SECONDS)

        db.commit()

        _status["stage"] = "estimating"
        estimated = refresh_estimated_prices(db, now)

        matched_canonical_names = {printing_lookup[k] for k in matched_keys}

        _status["stage"] = "committing"
        db.commit()

        result = {
            "matched": len(matched_canonical_names),
            "unmatched": len(inventory_names) - len(matched_canonical_names),
            "total_cards": len(inventory_names),
            "matched_printings": len(matched_keys),
            "total_printings": len(printing_lookup),
            "skipped_errors": len(skipped_names),
            "skipped_pages": len(skipped_pages),
            "estimated": estimated,
        }
        logger.info(
            "Pokemon bulk price refresh complete: %d/%d owned printings matched across %d/%d inventory names "
            "(%d skipped due to errors, %d pages skipped, %d unresolved buckets estimated)",
            result["matched_printings"], result["total_printings"], result["matched"], result["total_cards"],
            result["skipped_errors"], result["skipped_pages"], result["estimated"],
        )

        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_result": result,
        })
        return result

    except Exception as e:
        logger.exception("Pokemon bulk price refresh failed")
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
    """On-demand lookup for one printing via pokemontcg.io — same
    purpose as pricing.refresh_single_price (the '$' button in Manage
    Collection) but querying pokemontcg.io via pokemon_lookup instead
    of Scryfall. Not meant to be looped over an entire collection; use
    refresh_all_prices for that.

    With set_code/collector_number given, fetches that exact printing
    and stores a real (is_estimated=False) price. Without them (the
    unresolved bucket's own "$" action), this first tries the free
    option — reusing the cheapest already-cached real price among the
    name's other printings (see price_estimation.py) — and only falls
    back to lookup_card's fuzzy name match (stored as is_estimated=True)
    when no real price is cached yet for the name. Returns None if no
    match is found.
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

    existing.price_usd = result["price_usd"]
    existing.price_usd_foil = result["price_usd_foil"]
    existing.is_estimated = is_estimated
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return existing
