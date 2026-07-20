import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Reads DATABASE_URL from environment if set (e.g. Docker deployments),
# otherwise falls back to a local file — works unmodified for a plain
# venv/systemd deployment too.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./mtg_inventory.db")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
