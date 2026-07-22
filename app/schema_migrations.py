import logging

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from .database import Base
from . import models  # noqa: F401 -- import side effect registers Inventory/CardPrice/DeckAssignment on Base.metadata

logger = logging.getLogger("mtg_inventory.schema_migrations")

_TABLES_NEEDING_FINISH = ("inventory", "card_prices", "deck_assignments")


def migrate_finish_column(engine: Engine) -> None:
    """
    Adds `finish` (default "", meaning "unspecified") to inventory,
    card_prices, and deck_assignments on one per-user-per-game SQLite
    file. finish is a PRIMARY KEY member on inventory/card_prices and
    a UniqueConstraint member on deck_assignments -- plain `ALTER
    TABLE ADD COLUMN` can't touch either (SQLite forbids adding a
    column to an existing PRIMARY KEY, and there's no reliable way to
    predict deck_assignments' auto-generated unique-index name across
    SQLite versions to patch just that one table in-place) -- so this
    does SQLite's standard recreate-table dance for all three tables
    uniformly: build a shadow table matching models.py's *current*
    schema, copy every row across with finish='' backfilled, drop the
    old table, rename the shadow table into place. All three tables
    are migrated inside one transaction, so a mid-migration failure
    leaves the file completely untouched rather than partially
    migrated (SQLite's DDL is transactional).

    Idempotent (checked via PRAGMA table_info) and safe to call on
    every engine open -- see database.get_user_engine, which calls
    this immediately after create_all() so it naturally covers every
    existing per-user file the first time it's opened post-deploy, and
    is an instant no-op on a freshly created file (create_all already
    built the current shape) or an already-migrated one.
    """
    with engine.begin() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }
        for table_name in _TABLES_NEEDING_FINISH:
            if table_name not in existing_tables:
                continue  # brand-new file -- create_all() already built the current shape
            columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
            if "finish" in columns:
                continue  # already migrated
            _add_finish_column(conn, table_name)
            logger.info("Migrated %s: added finish column", table_name)


def _add_finish_column(conn, table_name: str) -> None:
    old_columns = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()]

    # Preserve any explicit (named) indexes the table had -- SQLite's
    # auto-managed indexes for PRIMARY KEY/UNIQUE constraints (sql IS
    # NULL) are recreated automatically by the shadow table's own
    # constraints and don't need replaying.
    index_ddls = [
        row[0]
        for row in conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:t AND sql IS NOT NULL"),
            {"t": table_name},
        ).fetchall()
    ]

    shadow_name = f"{table_name}_finish_migration"
    shadow_table = Base.metadata.tables[table_name].to_metadata(MetaData(), name=shadow_name)
    shadow_table.create(bind=conn)

    column_list = ", ".join(old_columns)
    conn.execute(
        text(
            f"INSERT INTO {shadow_name} ({column_list}, finish) "
            f"SELECT {column_list}, '' FROM {table_name}"
        )
    )
    conn.execute(text(f"DROP TABLE {table_name}"))
    conn.execute(text(f"ALTER TABLE {shadow_name} RENAME TO {table_name}"))

    for ddl in index_ddls:
        conn.execute(text(ddl))
