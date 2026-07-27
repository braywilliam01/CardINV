import logging

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

from .database import Base
from . import models  # noqa: F401 -- import side effect registers Inventory/CardPrice/DeckAssignment on Base.metadata

logger = logging.getLogger("mtg_inventory.schema_migrations")

_TABLES_NEEDING_FINISH = ("inventory", "card_prices", "deck_assignments")
_TABLES_NEEDING_LOCATION = ("inventory",)


def migrate_finish_column(engine: Engine) -> None:
    """
    Adds `finish` (default "", meaning "unspecified") to inventory,
    card_prices, and deck_assignments on one per-user-per-game SQLite
    file. See _migrate_missing_columns for how.
    """
    _migrate_missing_columns(engine, _TABLES_NEEDING_FINISH, "finish")


def migrate_location_column(engine: Engine) -> None:
    """
    Adds `location` (default "", meaning "not yet assigned") to
    inventory only -- location is a PRIMARY KEY member there but
    deliberately not part of card_prices (price doesn't depend on
    location) or deck_assignments (decks stay location-blind, same
    reasoning as checkout.py never threading a location through a
    decklist pin). See _migrate_missing_columns for how.
    """
    _migrate_missing_columns(engine, _TABLES_NEEDING_LOCATION, "location")


def _migrate_missing_columns(engine: Engine, table_names: tuple[str, ...], column_name: str) -> None:
    """
    Adds `column_name` to every table in `table_names` that doesn't
    have it yet, on one per-user-per-game SQLite file. Both finish and
    location are PRIMARY KEY members on inventory -- plain `ALTER
    TABLE ADD COLUMN` can't touch an existing PRIMARY KEY (and, for
    deck_assignments' finish, there's no reliable way to predict its
    auto-generated unique-index name across SQLite versions to patch
    just that one table in-place either) -- so this does SQLite's
    standard recreate-table dance: build a shadow table matching
    models.py's *current* schema, copy every row across with any
    missing column(s) backfilled to '', drop the old table, rename the
    shadow table into place. Every table named here is migrated inside
    one transaction, so a mid-migration failure leaves the file
    completely untouched rather than partially migrated (SQLite's DDL
    is transactional).

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
        for table_name in table_names:
            if table_name not in existing_tables:
                continue  # brand-new file -- create_all() already built the current shape
            columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
            if column_name in columns:
                continue  # already migrated
            _recreate_table_adding_new_columns(conn, table_name)
            logger.info("Migrated %s: added missing column(s)", table_name)


def _recreate_table_adding_new_columns(conn, table_name: str) -> None:
    """
    Backfills every column that's in models.py's current shape for
    this table but missing from the live table -- not just whichever
    single column the caller happened to be checking for. This is what
    makes migrate_finish_column and migrate_location_column mutually
    order-independent: whichever one runs first against a table
    missing BOTH columns (e.g. a per-user file nobody's opened since
    before the finish rollout) picks up both in one rebuild; the other
    migration then finds its column already present and no-ops.
    """
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

    shadow_name = f"{table_name}_schema_migration"
    shadow_table = Base.metadata.tables[table_name].to_metadata(MetaData(), name=shadow_name)
    shadow_table.create(bind=conn)

    new_columns = [c.name for c in shadow_table.columns if c.name not in old_columns]

    old_column_list = ", ".join(old_columns)
    new_column_list = ", ".join(new_columns)
    new_column_blanks = ", ".join("''" for _ in new_columns)
    conn.execute(
        text(
            f"INSERT INTO {shadow_name} ({old_column_list}, {new_column_list}) "
            f"SELECT {old_column_list}, {new_column_blanks} FROM {table_name}"
        )
    )
    conn.execute(text(f"DROP TABLE {table_name}"))
    conn.execute(text(f"ALTER TABLE {shadow_name} RENAME TO {table_name}"))

    for ddl in index_ddls:
        conn.execute(text(ddl))
