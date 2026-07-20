import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice
from .pokemon_common import POKEMON_API_BASE, HEADERS, extract_usd_prices
from .pokemon_lookup import lookup_card

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
    per page, ~82 requests) and matches against inventory as it goes,
    same shape as the MTG bulk refresh otherwise: batched commits,
    per-card error isolation, and live status for polling.
    """
    inventory_names = {row.card_name for row in db.query(Inventory.card_name).all()}

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
        return {"matched": 0, "unmatched": 0, "total_cards": 0, "skipped_errors": 0}

    logger.info("Pokemon bulk price refresh starting for %d cards in inventory", len(inventory_names))
    lookup_by_lower = {name.lower(): name for name in inventory_names}

    matched_names = set()
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
                    canonical = lookup_by_lower.get(name.lower())
                    if canonical is None:
                        continue

                    try:
                        price_usd, price_usd_foil = extract_usd_prices(card)
                    except (TypeError, ValueError):
                        logger.warning("Skipping '%s' — malformed price data from pokemontcg.io", canonical)
                        skipped_names.append(canonical)
                        continue

                    existing = db.query(CardPrice).filter(CardPrice.card_name == canonical).one_or_none()
                    if existing is None:
                        existing = CardPrice(card_name=canonical)
                        db.add(existing)

                    existing.price_usd = price_usd
                    existing.price_usd_foil = price_usd_foil
                    existing.updated_at = now
                    matched_names.add(canonical)

                    pending_since_commit += 1
                    if pending_since_commit >= BATCH_COMMIT_SIZE:
                        db.commit()
                        pending_since_commit = 0

                _status["cards_processed"] = page * PAGE_SIZE
                time.sleep(BETWEEN_PAGE_DELAY_SECONDS)

        _status["stage"] = "committing"
        db.commit()

        result = {
            "matched": len(matched_names),
            "unmatched": len(inventory_names) - len(matched_names),
            "total_cards": len(inventory_names),
            "skipped_errors": len(skipped_names),
            "skipped_pages": len(skipped_pages),
        }
        logger.info(
            "Pokemon bulk price refresh complete: %d/%d inventory cards matched "
            "(%d skipped due to errors, %d pages skipped)",
            result["matched"], result["total_cards"], result["skipped_errors"], result["skipped_pages"],
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


def refresh_single_price(db: Session, card_name: str) -> CardPrice | None:
    """On-demand lookup for one card via pokemontcg.io — same purpose
    as pricing.refresh_single_price (the per-row '$' button in Manage
    Collection) but querying the Pokemon API via pokemon_lookup instead
    of Scryfall. Not meant to be looped over an entire collection; use
    refresh_all_prices for that. Returns None if no match is found."""
    result = lookup_card(card_name)
    if result is None:
        return None

    existing = db.query(CardPrice).filter(CardPrice.card_name == card_name).one_or_none()
    if existing is None:
        existing = CardPrice(card_name=card_name)
        db.add(existing)

    existing.price_usd = result["price_usd"]
    existing.price_usd_foil = result["price_usd_foil"]
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return existing
