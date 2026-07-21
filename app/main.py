import logging
import os

from fastapi import FastAPI, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mtg_inventory.main")

from .database import auth_engine, AuthBase, get_auth_db, GAMES
from .auth import (
    get_db,
    get_current_username,
    get_current_game,
    get_current_admin,
    register_user,
    authenticate_user,
    change_password,
    admin_reset_password,
    list_users,
)
from .auth_models import User
from .models import DeckAssignment, Inventory
from .search import split_by_availability
from .checkout import checkout_cards, checkin_cards, sync_checkout, sync_checkin, get_deck_cards
from .csv_import import bulk_load_inventory
from .inventory_admin import (
    list_inventory,
    add_card,
    adjust_quantity,
    delete_card,
    delete_card_group,
    assign_printing,
    get_printings_for_card,
    bulk_add_cards,
    bulk_remove_cards,
    get_owned_quantity,
    add_one_copy,
    BlockedDeleteError,
    DuplicateCardError,
    build_group_row,
)
from .pricing import refresh_all_prices, refresh_single_price, get_collection_value, get_refresh_status, PricingError
from .pokemon_pricing import (
    refresh_all_prices as pokemon_refresh_all_prices,
    refresh_single_price as pokemon_refresh_single_price,
    get_refresh_status as pokemon_get_refresh_status,
)
from .homepage import get_summary, get_deck_shortcuts, get_deck_meta, set_favorite, get_everything_summary
from .deck_admin import rename_deck, delete_deck, DeckNotFoundError, DuplicateDeckError
from .card_lookup import lookup_card, record_card_view, get_recent_cards
from .pokemon_lookup import lookup_card as pokemon_lookup_card
from .sets_cache import search_sets

app = FastAPI(title="MTG Inventory Manager")

SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "dev-insecure-change-me-in-production")
if SESSION_SECRET_KEY == "dev-insecure-change-me-in-production":
    logger.warning(
        "SESSION_SECRET_KEY is not set — using an insecure default. "
        "Set it to a random value before deploying anywhere real (see DEPLOY.md)."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    same_site="lax",
    https_only=os.environ.get("SESSION_HTTPS_ONLY", "false").lower() == "true",
)

AuthBase.metadata.create_all(bind=auth_engine)


def _migrate_is_admin_column() -> None:
    """
    is_admin was added to the User model after this table could
    already exist on a real deployment — create_all() only creates
    *missing* tables, it doesn't alter existing ones, so this adds the
    column by hand if it's not there yet. Since no account created
    before this existed was ever flagged admin, it also promotes the
    earliest-registered user (there's always at least one, or there
    are no users yet and this is a no-op) so there's still someone who
    can reset another account's password.
    """
    with auth_engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "is_admin" in columns:
            return
        conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
        conn.execute(text("UPDATE users SET is_admin = 1 WHERE id = (SELECT id FROM users ORDER BY id ASC LIMIT 1)"))
        conn.commit()
        logger.info("Migrated users table: added is_admin, promoted earliest account to admin.")


_migrate_is_admin_column()


@app.get("/healthz")
def healthz():
    """Liveness + DB check for the reverse proxy / uptime monitoring —
    checks the shared auth database directly (not Depends(get_db),
    which requires a logged-in session) so this stays reachable without
    auth, the way a health check needs to be."""
    with auth_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------
class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
def auth_register(req: RegisterRequest, request: Request, auth_db: Session = Depends(get_auth_db)):
    try:
        user = register_user(auth_db, req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.session["username"] = user.username
    return {"username": user.username, "is_admin": user.is_admin}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, auth_db: Session = Depends(get_auth_db)):
    user = authenticate_user(auth_db, req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    request.session["username"] = user.username
    return {"username": user.username, "is_admin": user.is_admin}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return {"logged_out": True}


@app.get("/api/auth/me")
def auth_me(
    username: str = Depends(get_current_username),
    game: str = Depends(get_current_game),
    auth_db: Session = Depends(get_auth_db),
):
    user = auth_db.query(User).filter(User.username == username).one_or_none()
    return {"username": username, "game": game, "is_admin": bool(user and user.is_admin)}


class SetGameRequest(BaseModel):
    game: str


@app.put("/api/session/game")
def session_set_game(
    req: SetGameRequest, request: Request, username: str = Depends(get_current_username)
):
    """Switches the active game for the current session — everything
    behind Depends(get_db) (Manage Collection, Decks, Search, Card
    Search, pricing) is scoped to whichever game is set here."""
    if req.game not in GAMES:
        raise HTTPException(status_code=400, detail=f"game must be one of {GAMES}.")
    request.session["game"] = req.game
    return {"game": req.game}


@app.get("/api/sets")
def get_sets_endpoint(q: str = "", game: str = Depends(get_current_game)):
    """Set autocomplete for the current game — backs the Set field on
    Manage Collection's 'Add a card' form and the fix-up workflow."""
    return {"sets": search_sets(game, q)}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.put("/api/auth/password")
def auth_change_password(
    req: ChangePasswordRequest,
    username: str = Depends(get_current_username),
    auth_db: Session = Depends(get_auth_db),
):
    """Settings tab's self-service password change — always requires
    the current password, admins included."""
    try:
        change_password(auth_db, username, req.current_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"changed": True}


# ---------------------------------------------------------------------
# Admin — user management. Every route here requires get_current_admin,
# which 403s anyone whose account isn't flagged is_admin.
# ---------------------------------------------------------------------
@app.get("/api/admin/users")
def admin_list_users(
    admin_username: str = Depends(get_current_admin), auth_db: Session = Depends(get_auth_db)
):
    users = list_users(auth_db)
    return {
        "users": [
            {"username": u.username, "is_admin": u.is_admin, "created_at": u.created_at.isoformat()}
            for u in users
        ]
    }


class AdminResetPasswordRequest(BaseModel):
    new_password: str


@app.put("/api/admin/users/{target_username}/reset-password")
def admin_reset_user_password(
    target_username: str,
    req: AdminResetPasswordRequest,
    admin_username: str = Depends(get_current_admin),
    auth_db: Session = Depends(get_auth_db),
):
    """Lets an admin set a new password for another account directly —
    no current-password check, since the point is helping someone
    who's locked out. Relay the new password to them out of band."""
    try:
        admin_reset_password(auth_db, target_username, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"reset": target_username}


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


class RenameDeckRequest(BaseModel):
    new_name: str


@app.put("/api/decks/{deck_name}/rename")
def deck_rename(deck_name: str, req: RenameDeckRequest, db: Session = Depends(get_db)):
    try:
        new_name = rename_deck(db, deck_name, req.new_name)
    except DuplicateDeckError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (DeckNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deck_name": new_name}


@app.delete("/api/decks/{deck_name}")
def deck_delete(deck_name: str, db: Session = Depends(get_db)):
    """Checks every card in the deck back in and removes it entirely —
    the frontend confirms this with the user first, since it's not
    reversible."""
    try:
        checked_in = delete_deck(db, deck_name)
    except DeckNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": deck_name, "cards_checked_in": checked_in}


# ---------------------------------------------------------------------
# Homepage — landing-page stats and deck shortcuts.
# ---------------------------------------------------------------------
@app.get("/api/homepage/summary")
def homepage_summary(db: Session = Depends(get_db)):
    return get_summary(db)


@app.get("/api/homepage/everything")
def homepage_everything(username: str = Depends(get_current_username)):
    """The combined 'Everything' homescreen — stats across every game,
    not just the currently-active one. Deliberately doesn't use
    Depends(get_db), which only ever has access to one game at a time."""
    return get_everything_summary(username)


@app.get("/api/homepage/deck-shortcuts")
def homepage_deck_shortcuts(db: Session = Depends(get_db)):
    """Up to 3 decks for the Homepage quick-access buttons — favorites
    first, then most-recently-modified decks filling remaining slots."""
    return {"decks": get_deck_shortcuts(db)}


@app.get("/api/card-lookup")
def card_lookup(name: str, db: Session = Depends(get_db), game: str = Depends(get_current_game)):
    """Homepage's Card Search — fuzzy lookup (Scryfall for MTG,
    pokemontcg.io for Pokemon, chosen by the session's active game) for
    one card's full printed info, plus how many copies are in your
    inventory. The only local writes are bumping the "Last Viewed"
    cache; use POST /api/inventory/quick-add to actually add a copy."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Enter a card name to search.")

    provider_name = "Scryfall" if game == "mtg" else "pokemontcg.io"
    try:
        result = lookup_card(name.strip()) if game == "mtg" else pokemon_lookup_card(name.strip())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach {provider_name}: {e}")
    if result is None:
        raise HTTPException(status_code=404, detail=f"No {provider_name} match found for '{name}'.")
    record_card_view(db, result)
    result["owned_quantity"] = get_owned_quantity(
        db, result["inventory_name"], result.get("set_code") or "", result.get("collector_number") or ""
    )
    return result


class QuickAddRequest(BaseModel):
    card_name: str
    set_code: str = ""
    collector_number: str = ""


@app.post("/api/inventory/quick-add")
def inventory_quick_add(req: QuickAddRequest, db: Session = Depends(get_db)):
    """Card Search's 'Add to Inventory' button — adds exactly one copy
    of the exact printing shown (falling back to the unresolved bucket
    if no set/number is given), incrementing an existing (fuzzy-matched
    on name) row or creating a new one."""
    row = add_one_copy(db, req.card_name, req.set_code, req.collector_number)
    return _row_to_dict(row)


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
        "printings_added": result.printings_added,
        "printings_updated": result.printings_updated,
        "printings_removed": result.printings_removed,
        "warnings": result.warnings,
    }


# ---------------------------------------------------------------------
# Tab 4: Manage Collection (inventory admin)
# ---------------------------------------------------------------------
class AddCardRequest(BaseModel):
    card_name: str
    total_quantity: int
    set_code: str = ""
    collector_number: str = ""


class AdjustQuantityRequest(BaseModel):
    card_name: str
    total_quantity: int
    set_code: str = ""
    collector_number: str = ""


class AssignPrintingRequest(BaseModel):
    card_name: str
    quantity: int
    set_code: str
    collector_number: str


def _printing_to_dict(p):
    return {
        "set_code": p.set_code,
        "collector_number": p.collector_number,
        "total_quantity": p.total_quantity,
        "is_unresolved": p.is_unresolved,
        "price_usd": p.price_usd,
        "price_usd_foil": p.price_usd_foil,
        "is_estimated": p.is_estimated,
        "line_value": p.line_value,
    }


def _row_to_dict(row):
    return {
        "card_name": row.card_name,
        "total_quantity": row.total_quantity,
        "checked_out": row.checked_out,
        "available": row.available,
        "decks": [{"deck_name": d.deck_name, "quantity": d.quantity} for d in row.decks],
        "price_usd": row.price_usd,
        "line_value": row.line_value,
        "printing_count": row.printing_count,
        "has_unresolved": row.has_unresolved,
        "has_estimated": row.has_estimated,
        "printings": [_printing_to_dict(p) for p in row.printings],
    }


VALID_PAGE_SIZES = (25, 50, 100)


@app.get("/api/inventory")
def get_inventory(
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """Paginated for the Manage Collection table — see /api/inventory/names
    for an unpaginated list of every card name (e.g. for autocomplete)."""
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be 1 or greater.")
    if page_size not in VALID_PAGE_SIZES:
        raise HTTPException(status_code=400, detail=f"page_size must be one of {VALID_PAGE_SIZES}.")

    result = list_inventory(db, search=search, page=page, page_size=page_size)
    total_pages = max(1, -(-result.total_count // page_size))  # ceil division
    return {
        "cards": [_row_to_dict(r) for r in result.rows],
        "total_count": result.total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@app.get("/api/inventory/names")
def get_inventory_names(db: Session = Depends(get_db)):
    """Every card name in inventory, unpaginated — powers the 'Add a
    card to this deck' autocomplete in View Decks, which needs the full
    list rather than one page of it."""
    rows = db.query(Inventory.card_name).distinct().order_by(Inventory.card_name.asc()).all()
    return {"card_names": [r.card_name for r in rows]}


@app.post("/api/inventory")
def create_card(req: AddCardRequest, db: Session = Depends(get_db)):
    try:
        row = add_card(db, req.card_name, req.total_quantity, req.set_code, req.collector_number)
    except DuplicateCardError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _row_to_dict(row)


@app.get("/api/inventory/printings")
def get_card_printings(card_name: str, db: Session = Depends(get_db)):
    """Per-printing breakdown for one card name — powers expanding a
    Manage Collection row and the fix-up modal.

    card_name is a query param, not a path segment — a card name can
    itself contain "/" (a split/DFC card added under its full combined
    name, e.g. "Fire // Ice"), and a "/" in a path segment gets
    decoded and treated as an extra path separator well before it
    reaches route matching, breaking the match entirely. Every
    endpoint below that takes a card_name follows the same rule for
    the same reason."""
    printings = get_printings_for_card(db, card_name)
    return {"printings": [_printing_to_dict(p) for p in printings]}


@app.post("/api/inventory/assign-printing")
def assign_printing_endpoint(req: AssignPrintingRequest, db: Session = Depends(get_db)):
    """Fix-up workflow: moves `quantity` copies of card_name out of the
    unresolved bucket and into a specific (set_code, collector_number)
    printing."""
    try:
        row = assign_printing(db, req.card_name, req.quantity, req.set_code, req.collector_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _row_to_dict(row)


@app.patch("/api/inventory")
def update_card_quantity(req: AdjustQuantityRequest, db: Session = Depends(get_db)):
    """Sets one printing's total_quantity — set_code/collector_number
    default to the unresolved bucket, which is also the only printing
    a simple (not-yet-expanded) card has."""
    try:
        row = adjust_quantity(db, req.card_name, req.total_quantity, req.set_code, req.collector_number)
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


@app.delete("/api/inventory")
def remove_card(card_name: str, force: bool = False, db: Session = Depends(get_db)):
    """Deletes every printing of card_name — the delete button on
    Manage Collection's main (collapsed) row. For deleting just one
    printing, see DELETE /api/inventory/printing."""
    try:
        delete_card_group(db, card_name, force=force)
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


@app.delete("/api/inventory/printing")
def remove_card_printing(
    card_name: str,
    set_code: str = "",
    collector_number: str = "",
    force: bool = False,
    db: Session = Depends(get_db),
):
    """Deletes just one printing row — used from the expanded per-printing view."""
    try:
        delete_card(db, card_name, set_code=set_code, collector_number=collector_number, force=force)
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
    return {"deleted": card_name, "set_code": set_code, "collector_number": collector_number, "force": force}


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
# Pricing — bulk refresh for weekly/on-demand use, plus single-card
# on-demand lookups. Scryfall for MTG, pokemontcg.io for Pokemon,
# chosen by the session's active game.
# ---------------------------------------------------------------------
@app.post("/api/pricing/refresh-bulk")
def pricing_refresh_bulk(db: Session = Depends(get_db), game: str = Depends(get_current_game)):
    """
    Refreshes prices for every card in inventory in one go — MTG via
    Scryfall's single bulk-data file, Pokemon via paginating
    pokemontcg.io's catalog (no bulk-price file exists there). This is
    the endpoint to hit from a weekly cron job (see DEPLOY.md) or an
    on-demand "refresh all prices" button.
    """
    provider_name = "Scryfall" if game == "mtg" else "pokemontcg.io"
    try:
        result = refresh_all_prices(db) if game == "mtg" else pokemon_refresh_all_prices(db)
    except PricingError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach {provider_name}: {e}")
    return result


@app.post("/api/pricing/refresh-card")
def pricing_refresh_card(
    card_name: str,
    set_code: str = "",
    collector_number: str = "",
    db: Session = Depends(get_db),
    game: str = Depends(get_current_game),
):
    """On-demand price lookup for one printing (set_code/collector_number
    given — the '$' button on an expanded printing row) or the
    unresolved bucket (omitted — the '$' button on a collapsed/
    single-printing row). Returns the card's full updated group row so
    the caller can refresh both the aggregate and per-printing display
    in one round trip."""
    provider_name = "Scryfall" if game == "mtg" else "pokemontcg.io"
    try:
        result = (
            refresh_single_price(db, card_name, set_code, collector_number)
            if game == "mtg"
            else pokemon_refresh_single_price(db, card_name, set_code, collector_number)
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach {provider_name}: {e}")

    if result is None:
        raise HTTPException(status_code=404, detail=f"No {provider_name} match found for '{card_name}'.")

    return _row_to_dict(build_group_row(db, card_name))


@app.get("/api/pricing/status")
def pricing_status(game: str = Depends(get_current_game)):
    """
    Check progress of an in-flight or most recent bulk refresh without
    waiting on the (blocking) POST /api/pricing/refresh-bulk request —
    useful from a second terminal or for the UI to poll while a refresh
    is running. Each game tracks its own refresh status.
    """
    return get_refresh_status() if game == "mtg" else pokemon_get_refresh_status()


@app.get("/api/pricing/summary")
def pricing_summary(db: Session = Depends(get_db)):
    return get_collection_value(db)


# ---------------------------------------------------------------------
# Static frontend — MUST be mounted last. StaticFiles mounted at "/"
# will shadow any /api/... routes registered after it, so this stays
# at the bottom of the file regardless of edit order.
# ---------------------------------------------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
