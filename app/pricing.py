import json
import logging
import time
import zlib
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .models import Inventory, CardPrice
from .price_estimation import refresh_estimated_prices

logger = logging.getLogger("mtg_inventory.pricing")

SCRYFALL_BULK_INFO_URL = "https://api.scryfall.com/bulk-data"
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_CARDS_BASE = "https://api.scryfall.com/cards"

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
    "stage": None,  # "fetching_index" | "downloading" | "matching" | "estimating" | "committing" | None
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
        # default_cards = one row per unique printing (every set/
        # collector-number combination), needed now that pricing is
        # per-printing (see models.py) rather than deduplicated by name
        # the way oracle_cards is.
        if entry.get("type") == "default_cards":
            return entry
    raise PricingError("Could not find 'default_cards' bulk data entry from Scryfall.")


def _iter_cards(client: httpx.Client, entry: dict):
    """
    Streams and decompresses Scryfall's default_cards JSONL file
    (jsonl_download_uri), yielding one parsed card dict at a time.

    This *must* stream rather than download-then-parse-all-at-once: the
    file is 500MB+ gzipped and multiple GB decompressed, and a previous
    version of this function built the whole thing as one Python list
    in memory before processing anything, which could exhaust RAM on a
    modestly-provisioned deployment (see DEPLOY.md's RAM guidance).
    Peak memory here is bounded to roughly one HTTP chunk plus one
    decompression buffer, regardless of file size.
    """
    jsonl_uri = entry.get("jsonl_download_uri")
    if not jsonl_uri:
        raise PricingError("Bulk data entry has no jsonl_download_uri — Scryfall's bulk format may have changed.")

    # wbits = MAX_WBITS | 16 tells zlib to expect (and strip) a gzip
    # header/trailer, so this decompresses gzip directly from a stream
    # of arbitrarily-sized chunks rather than needing the whole
    # compressed payload in memory first (as gzip.decompress() would).
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    buffer = b""

    with client.stream("GET", jsonl_uri, headers=HEADERS, timeout=180) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            buffer += decompressor.decompress(chunk)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if line:
                    yield json.loads(line)

        buffer += decompressor.flush()
        line = buffer.strip()
        if line:
            yield json.loads(line)


def refresh_all_prices(db: Session) -> dict:
    """
    Streams Scryfall's default_cards bulk data file (every printing)
    and updates CardPrice for every *owned* printing currently in
    inventory — i.e. every non-unresolved Inventory row. One bulk
    download regardless of collection size, suitable for the weekly
    cron job or an on-demand "refresh everything" button. Only writes a
    CardPrice row for a printing actually in inventory (default_cards
    has ~10x the rows of oracle_cards; matching indiscriminately would
    bloat the price table with printings nobody owns). Streamed via
    _iter_cards rather than loaded into memory all at once — see that
    function for why.

    A split/double-faced/adventure card's `name` in the bulk file is
    Scryfall's combined "Front // Back" — matched against both that and
    the front face's name alone, since inventory (and Card Search)
    store only the front face by convention, but a manual add or a
    pasted decklist line can end up with the combined name instead.

    After matching, backfills an estimated price (cheapest known
    printing) for every name that also has an unresolved bucket — see
    price_estimation.py.

    Commits in batches of BATCH_COMMIT_SIZE cards rather than one commit
    for the whole file, and isolates per-card failures (e.g. malformed
    price data for a single card) so one bad record is skipped and
    logged instead of aborting the rest of the refresh.

    Logs progress at each stage (visible via `journalctl -u
    mtg-inventory -f`) and updates the in-memory status dict returned
    by get_refresh_status(), so progress is visible server-side while
    this runs, not just after the HTTP request completes. The total
    card count isn't known upfront when streaming (Scryfall's bulk-data
    index reports a byte size, not a row count), so total_cards_in_file
    stays null until the run finishes.
    """
    inventory_names = {row.card_name for row in db.query(Inventory.card_name).distinct().all()}

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
        return {"matched": 0, "unmatched": 0, "total_cards": 0, "skipped_errors": 0, "estimated": 0}

    # Only printings actually owned (excludes the "" / "" unresolved
    # bucket, which gets an estimated price afterward instead).
    printing_lookup = {
        (r.card_name.lower(), r.set_code, r.collector_number): r.card_name
        for r in db.query(Inventory.card_name, Inventory.set_code, Inventory.collector_number)
        .filter(~((Inventory.set_code == "") & (Inventory.collector_number == "")))
        .all()
    }

    logger.info(
        "Bulk price refresh starting for %d owned printings across %d inventory names",
        len(printing_lookup), len(inventory_names),
    )

    try:
        with httpx.Client(follow_redirects=True) as client:
            logger.info("Fetching Scryfall bulk-data index...")
            entry = _get_bulk_entry(client)

            _status["stage"] = "matching"
            size_hint = entry.get("size")
            logger.info(
                "Streaming default_cards bulk file (%s)...",
                f"~{size_hint / 1_000_000:.0f} MB compressed" if size_hint else "size unknown",
            )

            matched_keys = set()
            skipped_names: list[str] = []
            now = datetime.now(timezone.utc)
            pending_since_commit = 0
            total_processed = 0

            for card in _iter_cards(client, entry):
                total_processed += 1
                if total_processed % PROGRESS_UPDATE_INTERVAL == 0:
                    _status["cards_processed"] = total_processed

                name = card.get("name", "")
                set_code = (card.get("set") or "").upper()
                collector_number = card.get("collector_number") or ""

                # Try the bulk file's name as-is, and (for split/DFC/
                # adventure cards) the front face's name alone too —
                # see the docstring above.
                candidate_names = {name}
                raw_faces = card.get("card_faces") or []
                if raw_faces:
                    front_name = raw_faces[0].get("name")
                    if front_name:
                        candidate_names.add(front_name)

                canonical = None
                for candidate in candidate_names:
                    canonical = printing_lookup.get((candidate.lower(), set_code, collector_number))
                    if canonical is not None:
                        break
                if canonical is None:
                    continue

                prices = card.get("prices", {}) or {}
                try:
                    price_usd = float(prices["usd"]) if prices.get("usd") is not None else None
                    price_usd_foil = float(prices["usd_foil"]) if prices.get("usd_foil") is not None else None
                except (TypeError, ValueError):
                    # Skip just this printing — e.g. Scryfall returning a
                    # non-numeric price string — rather than losing the
                    # whole refresh over one bad record.
                    logger.warning("Skipping '%s' (%s #%s) — malformed price data: %r", canonical, set_code, collector_number, prices)
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
                matched_keys.add((canonical.lower(), set_code, collector_number))

                pending_since_commit += 1
                if pending_since_commit >= BATCH_COMMIT_SIZE:
                    db.commit()
                    pending_since_commit = 0

        _status["cards_processed"] = total_processed
        _status["total_cards_in_file"] = total_processed
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
            "estimated": estimated,
        }
        logger.info(
            "Bulk price refresh complete: %d/%d owned printings matched across %d/%d inventory names "
            "(%d skipped due to errors, %d unresolved buckets estimated)",
            result["matched_printings"], result["total_printings"], result["matched"], result["total_cards"],
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
        logger.exception("Bulk price refresh failed")
        _status.update({
            "in_progress": False,
            "stage": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "last_error": str(e),
        })
        raise


def refresh_single_price(db: Session, card_name: str, set_code: str = "", collector_number: str = "") -> CardPrice | None:
    """
    On-demand lookup for one printing. For "just added this card, get
    its price now" — not meant to be looped over an entire collection
    (use refresh_all_prices for that).

    With set_code/collector_number given, fetches that exact printing
    via Scryfall's precise /cards/{set}/{number} endpoint and stores a
    real (is_estimated=False) price. Without them (the unresolved
    bucket's own "$" action), this first tries the free option — if any
    of the name's other printings already has a real cached price,
    reuses the cheapest of those as the estimate (see
    price_estimation.py) rather than spending an API call. Only when no
    real price is cached yet for the name does it fall back to
    Scryfall's fuzzy name endpoint — its own guess at "the" printing —
    storing that as is_estimated=True, same as the bulk refresh's
    estimate: it's not a price for any specific printing you're known
    to own.

    Returns None if Scryfall has no match at all.
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

    with httpx.Client(follow_redirects=True) as client:
        if set_code and collector_number:
            resp = client.get(
                f"{SCRYFALL_CARDS_BASE}/{set_code.lower()}/{collector_number}", headers=HEADERS, timeout=15
            )
            target_set, target_number, is_estimated = set_code.upper(), collector_number, False
        else:
            resp = client.get(SCRYFALL_NAMED_URL, params={"fuzzy": card_name}, headers=HEADERS, timeout=15)
            target_set, target_number, is_estimated = "", "", True
    time.sleep(PER_CARD_DELAY_SECONDS)  # respect Scryfall's rate-limit guidance

    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    card = resp.json()

    prices = card.get("prices", {}) or {}
    usd = prices.get("usd")
    usd_foil = prices.get("usd_foil")

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

    existing.price_usd = float(usd) if usd is not None else None
    existing.price_usd_foil = float(usd_foil) if usd_foil is not None else None
    existing.is_estimated = is_estimated
    existing.updated_at = datetime.now(timezone.utc)

    db.commit()
    return existing


def get_collection_value(db: Session) -> dict:
    """Total known value of the collection, printing by printing —
    each Inventory row is joined to its own exact CardPrice row (real
    or estimated), since price is per-printing now (see models.py).
    Printings with no cached price (never refreshed, or not found on
    Scryfall) are excluded from the total but counted separately so
    the UI can flag them; estimated printings count as priced but are
    also reported separately. Also reports when the most recent price
    was cached, so the UI can show "as of ...".
    """
    rows = (
        db.query(Inventory, CardPrice)
        .outerjoin(
            CardPrice,
            (Inventory.card_name == CardPrice.card_name)
            & (Inventory.set_code == CardPrice.set_code)
            & (Inventory.collector_number == CardPrice.collector_number),
        )
        .all()
    )

    total_value = 0.0
    priced_printings = 0
    unpriced_printings = 0
    estimated_printings = 0
    last_updated = None

    for inv, price in rows:
        if inv.total_quantity <= 0:
            continue  # a zeroed-out printing row shouldn't count as "unpriced" clutter
        if price is not None and price.price_usd is not None:
            total_value += price.price_usd * inv.total_quantity
            priced_printings += 1
            if price.is_estimated:
                estimated_printings += 1
            if price.updated_at and (last_updated is None or price.updated_at > last_updated):
                last_updated = price.updated_at
        else:
            unpriced_printings += 1

    return {
        "total_value_usd": round(total_value, 2),
        "priced_cards": priced_printings,
        "unpriced_cards": unpriced_printings,
        "estimated_cards": estimated_printings,
        "last_updated": last_updated.isoformat() if last_updated else None,
    }
