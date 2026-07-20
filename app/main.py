import logging

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from .database import engine, Base, get_db
from .models import DeckAssignment
from .search import split_by_availability
from .checkout import checkout_cards, checkin_cards, sync_checkout, sync_checkin, get_deck_cards
from .csv_import import bulk_load_inventory
from .inventory_admin import (
    list_inventory,
    add_card,
    adjust_quantity,
    delete_card,
    bulk_add_cards,
    bulk_remove_cards,
    BlockedDeleteError,
    DuplicateCardError,
)
from .pricing import refresh_all_prices, refresh_single_price, get_collection_value, get_refresh_status, PricingError
from .homepage import get_summary, get_deck_shortcuts, get_deck_meta, set_favorite
from .card_lookup import lookup_card, record_card_view, get_recent_cards

app = FastAPI(title="MTG Inventory Manager")

Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------
# Tab 1: Collection Search & Output Splitter
# ---------------------------------------------------------------------
class SearchRequest(BaseModel):
    decklist_text: str
    fuzzy_threshold: int = 85
    ignore_basic_lands: bool = True


@app.post("/api/search")
def search_collection(req: SearchRequest, db: Session = Depends(get_db)):
    result = split_by_availability(db, req.decklist_text, req.fuzzy_threshold, req.ignore_basic_lands)
    return {
        "available": result.available_lines,
        "missing": result.missing_lines,
        "warnings": result.warnings,
        "skipped_basic_lands": result.skipped_basic_lands,
    }


# ---------------------------------------------------------------------
# Tab 2: Deck Checkout & Check-In
# ---------------------------------------------------------------------
class CheckoutRequest(BaseModel):
    decklist_text: str
    deck_name: str
    fuzzy_threshold: int = 85


def _serialize(result):
    return {
        "lines": [vars(l) for l in result.lines],
        "warnings": result.warnings,
    }


@app.post("/api/checkout")
def checkout(req: CheckoutRequest, db: Session = Depends(get_db)):
    return _serialize(checkout_cards(db, req.decklist_text, req.deck_name, req.fuzzy_threshold))


@app.post("/api/checkin")
def checkin(req: CheckoutRequest, db: Session = Depends(get_db)):
    return _serialize(checkin_cards(db, req.decklist_text, req.deck_name, req.fuzzy_threshold))


def _serialize_sync(result):
    return {
        "lines": [vars(l) for l in result.lines],
        "warnings": result.warnings,
        "errors": result.errors,
    }


@app.post("/api/checkout/sync")
def checkout_sync(req: CheckoutRequest, db: Session = Depends(get_db)):
    """
    Deck Checkout tab's sync mode: the box holds each card's target
    total (pre-loaded from the deck's current contents), not an amount
    to add on top. Distinct from POST /api/checkout, which stays
    additive for the Collection Search 'Add to Deck' action and Tab 5's
    quick +1/-1 controls.
    """
    return _serialize_sync(sync_checkout(db, req.decklist_text, req.deck_name, req.fuzzy_threshold))


@app.post("/api/checkin/sync")
def checkin_sync(req: CheckoutRequest, db: Session = Depends(get_db)):
    """Deck Checkout tab's sync mode for Check In — see checkout_sync."""
    return _serialize_sync(sync_checkin(db, req.decklist_text, req.deck_name, req.fuzzy_threshold))


@app.get("/api/decks")
def list_decks(db: Session = Depends(get_db)):
    """Powers the deck-name dropdown in Tab 2 and Tab 5."""
    rows = db.query(DeckAssignment.deck_name).distinct().all()
    return {"decks": sorted(r.deck_name for r in rows)}


@app.get("/api/decks/{deck_name}/cards")
def deck_cards(deck_name: str, db: Session = Depends(get_db)):
    """Powers Tab 5's deck contents view."""
    return {"deck_name": deck_name, "cards": get_deck_cards(db, deck_name)}


@app.get("/api/decks/{deck_name}/meta")
def deck_meta_get(deck_name: str, db: Session = Depends(get_db)):
    """Favorite status + last-modified for one deck, e.g. to render the
    star toggle in Tab 5 for the currently-selected deck."""
    return get_deck_meta(db, deck_name)


class FavoriteRequest(BaseModel):
    is_favorite: bool


@app.put("/api/decks/{deck_name}/favorite")
def deck_meta_set_favorite(deck_name: str, req: FavoriteRequest, db: Session = Depends(get_db)):
    return set_favorite(db, deck_name, req.is_favorite)


# ---------------------------------------------------------------------
# Homepage — landing-page stats and deck shortcuts.
# ---------------------------------------------------------------------
@app.get("/api/homepage/summary")
def homepage_summary(db: Session = Depends(get_db)):
    return get_summary(db)


@app.get("/api/homepage/deck-shortcuts")
def homepage_deck_shortcuts(db: Session = Depends(get_db)):
    """Up to 3 decks for the Homepage quick-access buttons — favorites
    first, then most-recently-modified decks filling remaining slots."""
    return {"decks": get_deck_shortcuts(db)}


@app.get("/api/card-lookup")
def card_lookup(name: str, db: Session = Depends(get_db)):
    """Homepage's Card Search — fuzzy Scryfall lookup for one card's
    full printed info (image, oracle text, prices, legalities). Doesn't
    touch inventory/pricing tables (see /api/pricing/refresh-card for
    that); the only local write is bumping the "Last Viewed" cache."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Enter a card name to search.")
    try:
        result = lookup_card(name.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Scryfall: {e}")
    if result is None:
        raise HTTPException(status_code=404, detail=f"No Scryfall match found for '{name}'.")
    record_card_view(db, result)
    return result


@app.get("/api/homepage/recent-cards")
def homepage_recent_cards(db: Session = Depends(get_db)):
    """Last few cards viewed via Card Search, for the Homepage's
    'Last Viewed' tiles."""
    return {"cards": get_recent_cards(db)}


# ---------------------------------------------------------------------
# Tab 3: Bulk Update (ManaBox CSV import)
# ---------------------------------------------------------------------
@app.post("/api/bulk-upload")
async def bulk_upload(
    file: UploadFile = File(...),
    ignore_basic_lands: bool = Form(True),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv export from ManaBox.")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")  # utf-8-sig handles ManaBox's BOM if present
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Could not decode file as UTF-8 CSV.")

    try:
        result = bulk_load_inventory(db, csv_text, ignore_basic_lands)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "unique_cards_loaded": result.unique_cards_loaded,
        "total_quantity_loaded": result.total_quantity_loaded,
        "assignments_preserved": result.assignments_preserved,
        "skipped_basic_lands": result.skipped_basic_lands,
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------
# Tab 4: Manage Collection (inventory admin)
# ---------------------------------------------------------------------
class AddCardRequest(BaseModel):
    card_name: str
    total_quantity: int


class AdjustQuantityRequest(BaseModel):
    total_quantity: int


def _row_to_dict(row):
    return {
        "card_name": row.card_name,
        "total_quantity": row.total_quantity,
        "checked_out": row.checked_out,
        "available": row.available,
        "decks": [{"deck_name": d.deck_name, "quantity": d.quantity} for d in row.decks],
        "price_usd": row.price_usd,
        "line_value": row.line_value,
    }


@app.get("/api/inventory")
def get_inventory(search: str | None = None, db: Session = Depends(get_db)):
    rows = list_inventory(db, search=search)
    return {"cards": [_row_to_dict(r) for r in rows]}


@app.post("/api/inventory")
def create_card(req: AddCardRequest, db: Session = Depends(get_db)):
    try:
        row = add_card(db, req.card_name, req.total_quantity)
    except DuplicateCardError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _row_to_dict(row)


@app.patch("/api/inventory/{card_name}")
def update_card_quantity(card_name: str, req: AdjustQuantityRequest, db: Session = Depends(get_db)):
    try:
        row = adjust_quantity(db, card_name, req.total_quantity)
    except BlockedDeleteError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "decks": [{"deck_name": d.deck_name, "quantity": d.quantity} for d in e.decks],
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _row_to_dict(row)


@app.delete("/api/inventory/{card_name}")
def remove_card(card_name: str, force: bool = False, db: Session = Depends(get_db)):
    try:
        delete_card(db, card_name, force=force)
    except BlockedDeleteError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "decks": [{"deck_name": d.deck_name, "quantity": d.quantity} for d in e.decks],
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deleted": card_name, "force": force}


class BulkInventoryRequest(BaseModel):
    decklist_text: str
    ignore_basic_lands: bool = True


def _serialize_bulk(result):
    return {
        "lines": [vars(l) for l in result.lines],
        "warnings": result.warnings,
        "skipped_basic_lands": result.skipped_basic_lands,
    }


@app.post("/api/inventory/bulk-add")
def bulk_add(req: BulkInventoryRequest, db: Session = Depends(get_db)):
    result = bulk_add_cards(db, req.decklist_text, req.ignore_basic_lands)
    return _serialize_bulk(result)


@app.post("/api/inventory/bulk-remove")
def bulk_remove(req: BulkInventoryRequest, db: Session = Depends(get_db)):
    result = bulk_remove_cards(db, req.decklist_text, req.ignore_basic_lands)
    return _serialize_bulk(result)


# ---------------------------------------------------------------------
# Pricing (Scryfall) — bulk refresh for weekly/on-demand use, plus
# single-card on-demand lookups.
# ---------------------------------------------------------------------
@app.post("/api/pricing/refresh-bulk")
def pricing_refresh_bulk(db: Session = Depends(get_db)):
    """
    Downloads Scryfall's bulk price data once and updates every card in
    inventory. This is the endpoint to hit from a weekly cron job
    (see DEPLOY.md) or an on-demand "refresh all prices" button — it's
    a single external request regardless of collection size, so it's
    safe to trigger manually as often as you like too.
    """
    try:
        result = refresh_all_prices(db)
    except PricingError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Scryfall: {e}")
    return result


@app.post("/api/pricing/refresh-card/{card_name}")
def pricing_refresh_card(card_name: str, db: Session = Depends(get_db)):
    """On-demand price lookup for a single card, e.g. right after adding it."""
    try:
        result = refresh_single_price(db, card_name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Scryfall: {e}")

    if result is None:
        raise HTTPException(status_code=404, detail=f"No Scryfall match found for '{card_name}'.")

    return {
        "card_name": result.card_name,
        "price_usd": result.price_usd,
        "price_usd_foil": result.price_usd_foil,
    }


@app.get("/api/pricing/status")
def pricing_status():
    """
    Check progress of an in-flight or most recent bulk refresh without
    waiting on the (blocking) POST /api/pricing/refresh-bulk request —
    useful from a second terminal (`curl http://127.0.0.1:8000/api/pricing/status`)
    or for the UI to poll while a refresh is running.
    """
    return get_refresh_status()


@app.get("/api/pricing/summary")
def pricing_summary(db: Session = Depends(get_db)):
    return get_collection_value(db)


# ---------------------------------------------------------------------
# Static frontend — MUST be mounted last. StaticFiles mounted at "/"
# will shadow any /api/... routes registered after it, so this stays
# at the bottom of the file regardless of edit order.
# ---------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
