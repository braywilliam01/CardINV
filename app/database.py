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
# Per-user databases — one full SQLite file per user (same schema as
# app/models.py: Inventory, DeckAssignment, CardPrice, DeckMeta,
# CardSearchHistory), so isolation between users comes from which file
# a request is routed to rather than a user_id column on every table.
# Engines are created lazily on first use and cached for the process
# lifetime — creating a SQLAlchemy engine isn't free, and a personal/
# family-scale deployment has few enough users that caching all of
# them in memory is a non-issue.
# ---------------------------------------------------------------------
Base = declarative_base()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_user_engines: dict[str, Engine] = {}


def is_valid_username(username: str) -> bool:
    """3-32 chars, letters/digits/underscore/hyphen only. Enforced both
    at registration and defensively here, since usernames become
    filesystem directory names — this also blocks path traversal via a
    crafted username like '../../etc'."""
    return bool(_USERNAME_RE.match(username))


def get_user_engine(username: str):
    if username not in _user_engines:
        if not is_valid_username(username):
            raise ValueError(f"Invalid username: '{username}'")
        user_dir = DATA_DIR / "users" / username
        user_dir.mkdir(parents=True, exist_ok=True)
        db_path = user_dir / "mtg_inventory.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        _user_engines[username] = engine
    return _user_engines[username]
