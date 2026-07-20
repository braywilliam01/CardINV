from datetime import datetime, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from .auth_models import User
from .database import get_user_engine, is_valid_username

MIN_PASSWORD_LENGTH = 8


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

    user = User(
        username=username,
        password_hash=hash_password(password),
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()

    # Creates this user's per-user database up front (rather than
    # waiting for their first real request) so a freshly registered
    # account isn't left in a half-initialized state.
    get_user_engine(username)

    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username.strip()).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def get_current_username(request: Request) -> str:
    username = request.session.get("username")
    if username is None:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return username


def get_db(username: str = Depends(get_current_username)):
    """
    The app's main DB dependency — used by every existing /api/*
    endpoint via Depends(get_db). Routing per-user happens entirely
    here: since this depends on get_current_username, an unauthenticated
    request gets a 401 before ever touching a database, and an
    authenticated one gets a Session bound to *that user's* SQLite
    file — every route that already took `db: Session = Depends(get_db)`
    is now user-scoped without needing to change its own logic.
    """
    engine = get_user_engine(username)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
