from datetime import datetime, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from .auth_models import User
from .database import get_user_engine, get_auth_db, is_valid_username, GAMES, DEFAULT_GAME

MIN_PASSWORD_LENGTH = 8

# A precomputed hash checked against when a login's username doesn't
# exist at all, so authenticate_user always pays bcrypt's (deliberately
# slow) cost once, regardless of whether the account is real. Without
# this, a "no such user" response returns measurably faster than a
# "wrong password" one, leaking which usernames are registered via
# simple response-timing measurement. Computed once at import time,
# not per-request — gensalt() itself isn't free either.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-account-timing-decoy", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def register_user(db: Session, username: str, password: str) -> User:
    username = username.strip()
    if not is_valid_username(username):
        raise ValueError(
            "Username must be 3-32 characters: letters, numbers, underscores, or hyphens only."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    existing = db.query(User).filter(User.username == username).one_or_none()
    if existing:
        raise ValueError(f"Username '{username}' is already taken.")

    # The very first account on a fresh install becomes an admin, so
    # there's always someone who can reset another user's password —
    # nobody else who registers afterward gets this automatically.
    is_first_user = db.query(User).count() == 0

    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_first_user,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()

    # Creates this user's per-game databases up front (rather than
    # waiting for their first real request in each) so a freshly
    # registered account isn't left in a half-initialized state —
    # cheap, since these start out as empty SQLite files.
    for game in GAMES:
        get_user_engine(username, game)

    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username.strip()).one_or_none()
    # Always check against *some* hash — a real one if the account
    # exists, the fixed decoy if it doesn't — so this function takes
    # the same time either way (see _DUMMY_PASSWORD_HASH above).
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, password_hash)
    if user is None or not password_ok:
        return None
    return user


def change_password(db: Session, username: str, current_password: str, new_password: str) -> None:
    """Self-service password change — always requires the current
    password, even for admins. Admin-initiated resets for *other*
    users go through admin_reset_password instead, which doesn't."""
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        raise ValueError("User not found.")
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")

    user.password_hash = hash_password(new_password)
    db.commit()


def admin_reset_password(db: Session, target_username: str, new_password: str) -> None:
    """Admin-initiated reset for another account — no current-password
    check, since the whole point is helping someone who's locked out."""
    user = db.query(User).filter(User.username == target_username).one_or_none()
    if user is None:
        raise ValueError(f"User '{target_username}' not found.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")

    user.password_hash = hash_password(new_password)
    db.commit()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.username.asc()).all()


def get_current_username(request: Request) -> str:
    username = request.session.get("username")
    if username is None:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return username


def get_current_admin(
    username: str = Depends(get_current_username), auth_db: Session = Depends(get_auth_db)
) -> str:
    """Admin-only route dependency — 403s anyone who isn't flagged
    is_admin, including a logged-in but ordinary user."""
    user = auth_db.query(User).filter(User.username == username).one_or_none()
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return username


def get_current_game(request: Request) -> str:
    """Defaults to 'mtg' — both for brand-new sessions and for anyone
    who logged in before the game switcher existed."""
    return request.session.get("game", DEFAULT_GAME)


def get_db(username: str = Depends(get_current_username), game: str = Depends(get_current_game)):
    """
    The app's main DB dependency — used by every existing /api/*
    endpoint via Depends(get_db). Routing per-user *and* per-game
    happens entirely here: since this depends on get_current_username,
    an unauthenticated request gets a 401 before ever touching a
    database, and an authenticated one gets a Session bound to that
    user's database for whichever game is currently active in their
    session — every route that already took
    `db: Session = Depends(get_db)` is scoped to both without needing
    to change its own logic.
    """
    engine = get_user_engine(username, game)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
