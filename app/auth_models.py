from sqlalchemy import Column, Integer, String, DateTime, Boolean

from .database import AuthBase


class User(AuthBase):
    """
    Lives in the master data/users.db, not a per-user database — this
    is the one table that has to be shared, since you need to look up
    who's logging in before you know which per-user database to open.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False)


class ApiKey(AuthBase):
    """
    Long-lived tokens for non-browser callers (e.g. an AI agent reading
    a user's collection via /api/agent/*) — lives in the same shared
    users.db as User, for the same reason: a request needs to be
    resolved to a username before it's clear which per-user database to
    open at all.

    key_id is a short, non-secret public identifier embedded in the
    issued token (see auth.create_api_key) so a lookup can go straight
    to one row by an indexed equality match, rather than bcrypt-
    checking the caller's token against every stored key_hash in turn.
    key_hash is the bcrypt hash of only the token's secret half — same
    treatment as password_hash, so a stolen users.db doesn't hand over
    usable tokens outright. username is stored directly (not a
    User.id foreign key) to match how the rest of the app already
    treats username as the identity that matters — nothing else in
    this codebase joins against User by id either, since per-user data
    lives in separate per-user database files rather than rows keyed
    by it.
    """
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    key_id = Column(String, unique=True, nullable=False, index=True)
    key_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
