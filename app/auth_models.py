from sqlalchemy import Column, Integer, String, DateTime

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
    created_at = Column(DateTime, nullable=False)
