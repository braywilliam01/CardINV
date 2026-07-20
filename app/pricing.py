import gzip
import json
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice

logger = logging.getLogger("mtg_inventory.pricing")

SCRYFALL_BULK_INFO_URL = "https://api.scryfall.com/bulk-data"
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"

# Scryfall's API guidelines ask that clients identify themselves via
# User-Agent and Accept, and that rapid-fire single-card requests be
# spaced out. The bulk data download is one request regardless of
# collection size, so it doesn't need throttling — only
# refresh_single_price (used for on-demand single-card lookups) does.
HEADERS = {
    "User-Agent": "MTG-Inventory-Manager/1.0 (personal collection tool)",
    "Accept": "application/json",
}
PER_CARD_DELAY_SECONDS = 0.1

# How often (in cards processed) to update the in-memory progress
# counter during the bulk loop — frequent enough that /api/pricing/status
# feels live, infrequent enough not to add measurable overhead.
PROGRESS_UPDATE_INTERVAL = 5000

# Commit price updates in batches rather than one commit for the whole
# file — caps how much work a mid-refresh crash (or a restart) can lose,
# and keeps any single transaction from growing unboundedly on a large
# collection.
BATCH_COMMIT_SIZE = 250


class PricingError(Exception):
    pass


# ---------------------------------------------------------------------
# In-memory refresh status — lets you check progress server-side (via
# GET /api/pricing/status, or the Manage Collection tab) without
# waiting on the blocking POST request to finish. Reset each time a
# bulk refresh starts; not persisted across restarts, which is fine
# since a restart mid-refresh means the refresh itself was aborted.
# ---------------------------------------------------------------------
_status = {
    "in_progress": False,
    "stage": None,  # "fetching_index" | "downloading" | "matching" | "committing" | None
    "started_at": None,
    "finished_at": None,
    "cards_processed": 0,
    "total_cards_in_file": None,
    "last_result": None,
    "last_error": None,
}


def get_refresh_status() -> dict:
    return dict(_status)


def _get_bulk_entry(client: httpx.Client) -> dict:
    resp = client.get(SCRYFALL_BULK_INFO_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    for entry in data.get("data", []):
        # oracle_cards = one row per unique card (deduplicated across
        # printings/sets), which matches how this app tracks inventory
        # (by card name only, not by specific printing).
        if entry.get("type") == "oracle_cards":
            return entry
    raise PricingError("Could not find 'oracle_cards' bulk data entry from Scryfall.")


def _download_cards(client: httpx.Client, entry: dict) -> list[dict]:
    """
    Scryfall is retiring the plain-JSON bulk download in favor of
    gzipped JSONL (one card object per line) — the old `download_uri`
    stops working after July 20, 2026. This prefers `jsonl_download_uri`
    when present and falls back to the legacy format only if it isn't
    (e.g. if this runs against a stale/cached bulk-data index).
    """
    jsonl_uri = entry.get("jsonl_download_uri")

    if jsonl_uri:
        resp = client.get(jsonl_uri, headers=HEADERS, timeout=180)
        resp.raise_for_status()
        raw = resp.content
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass  # httpx/transport already decompressed it (Content-Encoding case)

        cards = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                cards.append(json.loads(line))
        return cards

    # Legacy path — retired by Scryfall after 2026-07-20. Kept only as a
    # fallback in case jsonl_download_uri is ever absent.
    download_uri = entry.get("download_uri")
    if not download_uri:
        raise PricingError("Bulk data entry has neither jsonl_download_uri nor download_uri.")
    resp = client.get(download_uri, headers=HEADERS, timeout=180)
    resp.raise_for_status()
    return resp.json()


def refresh_all_prices(db: Session) -> dict:
    """
    Downloads Scryfall's oracle_cards bulk data file and updates
    CardPrice for every card currently in inventory. One bulk download
    regardless of collection size — suitable for the weekly cron job or
    an on-demand "refresh everything" button. Cards not found in the
    bulk file (e.g. a typo'd name) are left with whatever price they
    had before, not wiped.

    Commits in batches of BATCH_COMMIT_SIZE cards rather than one commit
    for the whole file, and isolates per-card failures (e.g. malformed
    price data for a single card) so one bad record is skipped and
    logged instead of aborting the rest of the refresh.

    Logs progress at each stage (visible via `journalctl -u
    mtg-inventory -f`) and updates the in-memory status dict returned
    by get_refresh_status(), so progress is visible server-side while
    this runs, not just after the HTTP request completes.
    """
    inventory_names = {row.card_name for row in db.query(Inventory.card_name).all()}

    _status.update({
        "in_progress": True,
        "stage": "fetching_index",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "cards_processed": 0,
        "total_cards_in_file": None,
        "last_error": None,
    })

    if not inventory_names:
        _status.update({"in_progress": False, "stage": None, "finished_at": datetime.now(timezone.utc).isoformat()})
        return {"matched": 0, "unmatched": 0, "total_cards": 0}

    logger.info("Bulk price refresh starting for %d cards in inventory", len(inventory_names))
    lookup_by_lower = {name.lower(): name for name in inventory_names}

    try:
        with httpx.Client(follow_redirects=True) as client:
            logger.info("Fetching Scryfall bulk-data index...")
            entry = _get_bulk_entry(client)

            _status["stage"] = "downloading"
            size_hint = entry.get("size")
            logger.info(
                "Downloading oracle_cards bulk file (%s)...",
                f"~{size_hint / 1_000_000:.0f} MB" if size_hint else "size unknown",
            )
            cards = _download_cards(client, entry)

        _status["total_cards_in_file"] = len(cards)
        logger.info("Downloaded and parsed %d cards from Scryfall", len(cards))

        _status["stage"] = "matching"
        matched_names = set()
        skipped_names: list[str] = []
        now = datetime.now(timezone.utc)
        pending_since_commit = 0

        for i, card in enumerate(cards):
            if i % PROGRESS_UPDATE_INTERVAL == 0:
                _status["cards_processed"] = i

            name = card.get("name", "")
            canonical = lookup_by_lower.get(name.lower())
            if canonical is None:
                continue

            prices = card.get("prices", {}) or {}
            try:
                price_usd = float(prices["usd"]) if prices.get("usd") is not None else None
                price_usd_foil = float(prices["usd_foil"]) if prices.get("usd_foil") is not None else None
            except (TypeError, ValueError):
                # Skip just this card — e.g. Scryfall returning a
                # non-numeric price string — rather than losing the
                # whole refresh over one bad record.
                logger.warning("Skipping '%s' — malformed price data from Scryfall: %r", canonical, prices)
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

        _status["cards_processed"] = len(cards)
        _status["stage"] = "committing"
        db.commit()

        result = {
            "matched": len(matched_names),
            "unmatched": len(inventory_names) - len(matched_names),
            "total_cards": len(inventory_names),
            "skipped_errors": len(skipped_names),
        }
        logger.info(
            "Bulk price refresh complete: %d/%d inventory cards matched (%d skipped due to errors)",
            result["matched"], result["total_cards"], result["skipped_errors"],
        )

        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_result": result,
        })
        return result

    except Exception as e:
        logger.exception("Bulk price refresh failed")
        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_error": str(e),
        })
        raise


def refresh_single_price(db: Session, card_name: str) -> CardPrice | None:
    """
    On-demand lookup for one card via Scryfall's fuzzy-name endpoint.
    For "just added this card, get its price now" — not meant to be
    looped over an entire collection (use refresh_all_prices for that;
    it's a single bulk download instead of N individual requests, and
    N individual requests against Scryfall for a few hundred+ unique
    cards would be slow and impolite to their API).
    Returns None if Scryfall has no match for the name at all.
    """
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(
            SCRYFALL_NAMED_URL, params={"fuzzy": card_name}, headers=HEADERS, timeout=15
        )
    time.sleep(PER_CARD_DELAY_SECONDS)  # respect Scryfall's rate-limit guidance

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    card = resp.json()

    prices = card.get("prices", {}) or {}
    usd = prices.get("usd")
    usd_foil = prices.get("usd_foil")

    existing = db.query(CardPrice).filter(CardPrice.card_name == card_name).one_or_none()
    if existing is None:
        existing = CardPrice(card_name=card_name)
        db.add(existing)

    existing.price_usd = float(usd) if usd is not None else None
    existing.price_usd_foil = float(usd_foil) if usd_foil is not None else None
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return existing


def get_collection_value(db: Session) -> dict:
    """Total known value of the collection. Cards with no cached price
    (never refreshed, or not found on Scryfall) are excluded from the
    total but counted separately so the UI can flag them. Also reports
    when the most recent price was cached, so the UI can show
    "as of ...".
    """
    rows = (
        db.query(Inventory, CardPrice)
        .outerjoin(CardPrice, Inventory.card_name == CardPrice.card_name)
        .all()
    )

    total_value = 0.0
    priced_cards = 0
    unpriced_cards = 0
    last_updated = None

    for inv, price in rows:
        if price is not None and price.price_usd is not None:
            total_value += price.price_usd * inv.total_quantity
            priced_cards += 1
            if price.updated_at and (last_updated is None or price.updated_at > last_updated):
                last_updated = price.updated_at
        else:
            unpriced_cards += 1

    return {
        "total_value_usd": round(total_value, 2),
        "priced_cards": priced_cards,
        "unpriced_cards": unpriced_cards,
        "last_updated": last_updated.isoformat() if last_updated else None,
    }
