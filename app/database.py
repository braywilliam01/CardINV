import os
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Root directory for all per-deployment data: the shared user-accounts
# DB, plus one subdirectory per user holding their own full copy of
# the app's schema. Env-configurable for Docker/systemd deployments,
# same pattern as the old single-file DATABASE_URL.
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------
# Master auth database — just user accounts (app/auth_models.py). Has
# to be a single shared database: you need somewhere to look up which
# user this is *before* you know which per-user database to open.
# ---------------------------------------------------------------------
AUTH_DATABASE_URL = os.environ.get("AUTH_DATABASE_URL", f"sqlite:///{DATA_DIR / 'users.db'}")
auth_engine = create_engine(AUTH_DATABASE_URL, connect_args={"check_same_thread": False})
AuthSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=auth_engine)
AuthBase = declarative_base()


def get_auth_db():
    db = AuthSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------
# Per-user, per-game databases — one full SQLite file per (user, game)
# pair, all sharing the same schema (app/models.py: Inventory,
# DeckAssignment, CardPrice, DeckMeta, CardSearchHistory). That schema
# is already game-agnostic — "card name + quantity" doesn't care
# whether the card is Magic or Pokemon — so isolation between both
# users AND games comes entirely from which file a request is routed
# to, rather than user_id/game columns threaded through every table
# and every query. Engines are created lazily on first use and cached
# for the process lifetime — creating a SQLAlchemy engine isn't free,
# and a personal/family-scale deployment has few enough (user, game)
# pairs that caching all of them in memory is a non-issue.
# ---------------------------------------------------------------------
Base = declarative_base()

GAMES = ("mtg", "pokemon")
DEFAULT_GAME = "mtg"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_user_engines: dict[tuple[str, str], Engine] = {}


def is_valid_username(username: str) -> bool:
    """3-32 chars, letters/digits/underscore/hyphen only. Enforced both
    at registration and defensively here, since usernames become
    filesystem directory names — this also blocks path traversal via a
    crafted username like '../../etc'."""
    return bool(_USERNAME_RE.match(username))


def get_user_engine(username: str, game: str = DEFAULT_GAME):
    if game not in GAMES:
        raise ValueError(f"Unknown game: '{game}' (expected one of {GAMES})")

    key = (username, game)
    if key not in _user_engines:
        if not is_valid_username(username):
            raise ValueError(f"Invalid username: '{username}'")
        user_dir = DATA_DIR / "users" / username / game
        user_dir.mkdir(parents=True, exist_ok=True)
        db_path = user_dir / "inventory.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        _user_engines[key] = engine
    return _user_engines[key]
