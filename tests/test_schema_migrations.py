"""app/schema_migrations.py's finish-column migration -- the riskiest
piece of the finish-tracking feature, since it recreates tables (PK
change) on real per-user SQLite files with no Alembic/versioning.
Builds a pre-migration-shape database by hand (raw SQL, not via
models.py, which already has `finish`) to simulate a real production
file predating this change."""
import sqlite3

from sqlalchemy import create_engine, text

from app.database import Base
from app.schema_migrations import migrate_finish_column


def _make_pre_migration_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE inventory (
            card_name VARCHAR NOT NULL,
            set_code VARCHAR NOT NULL DEFAULT '',
            collector_number VARCHAR NOT NULL DEFAULT '',
            total_quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (card_name, set_code, collector_number)
        );
        CREATE INDEX ix_inventory_card_name ON inventory (card_name);

        CREATE TABLE card_prices (
            card_name VARCHAR NOT NULL,
            set_code VARCHAR NOT NULL DEFAULT '',
            collector_number VARCHAR NOT NULL DEFAULT '',
            price_usd FLOAT,
            price_usd_foil FLOAT,
            is_estimated BOOLEAN NOT NULL DEFAULT 0,
            updated_at DATETIME,
            PRIMARY KEY (card_name, set_code, collector_number)
        );

        CREATE TABLE deck_assignments (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            card_name VARCHAR NOT NULL,
            deck_name VARCHAR NOT NULL,
            set_code VARCHAR NOT NULL DEFAULT '',
            collector_number VARCHAR NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_card_deck_printing UNIQUE (card_name, deck_name, set_code, collector_number)
        );
        CREATE INDEX ix_deck_assignments_card_name ON deck_assignments (card_name);
        CREATE INDEX ix_deck_assignments_deck_name ON deck_assignments (deck_name);

        CREATE TABLE deck_meta (
            deck_name VARCHAR NOT NULL PRIMARY KEY,
            is_favorite BOOLEAN NOT NULL DEFAULT 0,
            last_modified DATETIME
        );

        CREATE TABLE card_search_history (
            card_name VARCHAR NOT NULL PRIMARY KEY,
            image_url VARCHAR,
            mana_cost VARCHAR,
            type_line VARCHAR,
            viewed_at DATETIME NOT NULL
        );

        INSERT INTO inventory (card_name, set_code, collector_number, total_quantity)
        VALUES ('Lightning Bolt', 'CLB', '304', 3), ('Sol Ring', '', '', 1);

        INSERT INTO card_prices (card_name, set_code, collector_number, price_usd, price_usd_foil, is_estimated)
        VALUES ('Lightning Bolt', 'CLB', '304', 0.83, 2.68, 0);

        INSERT INTO deck_assignments (card_name, deck_name, set_code, collector_number, quantity)
        VALUES ('Lightning Bolt', 'My Deck', 'CLB', '304', 2);
        """
    )
    conn.commit()
    conn.close()


def test_migration_backfills_finish_and_preserves_data(tmp_path):
    db_path = tmp_path / "pre_migration.db"
    _make_pre_migration_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    migrate_finish_column(engine)

    with engine.connect() as conn:
        inv_rows = conn.execute(
            text("SELECT card_name, set_code, collector_number, finish, total_quantity FROM inventory ORDER BY card_name")
        ).fetchall()
        assert [tuple(r) for r in inv_rows] == [
            ("Lightning Bolt", "CLB", "304", "", 3),
            ("Sol Ring", "", "", "", 1),
        ]

        price_rows = conn.execute(
            text("SELECT card_name, set_code, collector_number, finish, price_usd, price_usd_foil FROM card_prices")
        ).fetchall()
        assert [tuple(r) for r in price_rows] == [("Lightning Bolt", "CLB", "304", "", 0.83, 2.68)]

        deck_rows = conn.execute(
            text("SELECT card_name, deck_name, set_code, collector_number, finish, quantity FROM deck_assignments")
        ).fetchall()
        assert [tuple(r) for r in deck_rows] == [("Lightning Bolt", "My Deck", "CLB", "304", "", 2)]

        index_names = {
            row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()
        }
        assert "ix_inventory_card_name" in index_names
        assert "ix_deck_assignments_card_name" in index_names
        assert "ix_deck_assignments_deck_name" in index_names


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "pre_migration.db"
    _make_pre_migration_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    migrate_finish_column(engine)
    migrate_finish_column(engine)  # must be a safe no-op, not raise or duplicate rows

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM inventory")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM card_prices")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM deck_assignments")).scalar() == 1


def test_migration_on_freshly_created_schema_is_noop(tmp_path):
    """A file created via create_all() already has `finish` (it's in
    models.py) -- the 'table doesn't need migrating' path."""
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    migrate_finish_column(engine)  # must not raise

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(inventory)")).fetchall()}
        assert "finish" in columns


def test_unresolved_and_unspecified_finish_axes_are_independent(tmp_path):
    """Sol Ring (fully-unresolved: '', '') and Lightning Bolt
    (printing-resolved, finish-unspecified: 'CLB', '304', '') both
    land at finish='' post-migration -- confirms the migration itself
    doesn't conflate the two axes (see models.py's Inventory
    docstring)."""
    db_path = tmp_path / "pre_migration.db"
    _make_pre_migration_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    migrate_finish_column(engine)

    with engine.connect() as conn:
        rows = {
            (r[0], r[1] == "" and r[2] == "")
            for r in conn.execute(text("SELECT card_name, set_code, collector_number FROM inventory")).fetchall()
        }
        assert rows == {("Lightning Bolt", False), ("Sol Ring", True)}
