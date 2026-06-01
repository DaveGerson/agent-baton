"""Migration acceptance tests for v42 — ADR-13b WP-G bead table teardown.

Verifies that:

1. Fresh project installs (PROJECT_SCHEMA_DDL) do NOT create bead tables.
2. Fresh central installs (CENTRAL_SCHEMA_DDL) do NOT create bead tables
   or the v_cross_project_discoveries view.
3. Upgrading an old database from v41 to v42 DROPs the bead tables (and
   is idempotent if they were already absent).
4. SCHEMA_VERSION == 42 and MIGRATIONS[42] exists with DROP TABLE statements.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Tables and views that must NOT exist after v42 in any DB flavor.
# ---------------------------------------------------------------------------

DROPPED_TABLES = {
    "beads",
    "bead_tags",
    "bead_anchors",
    "bead_edges",
    "bead_clusters",
    "handoff_beads",
}

DROPPED_VIEWS = {
    "v_cross_project_discoveries",
}


def _table_set(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _view_set(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 1. Fresh project install
# ---------------------------------------------------------------------------


def test_fresh_project_install_has_no_bead_tables(tmp_path: Path) -> None:
    """A new baton.db must not contain any SQLite bead tables."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    cm = ConnectionManager(tmp_path / "baton.db")
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    tables = _table_set(conn)
    stray = DROPPED_TABLES & tables
    assert not stray, f"Fresh project DB still has bead tables: {stray}"


# ---------------------------------------------------------------------------
# 2. Fresh central install
# ---------------------------------------------------------------------------


def test_fresh_central_install_has_no_bead_tables(tmp_path: Path) -> None:
    """A new central.db must not contain any SQLite bead tables."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION

    cm = ConnectionManager(tmp_path / "central.db")
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    tables = _table_set(conn)
    stray = DROPPED_TABLES & tables
    assert not stray, f"Fresh central DB still has bead tables: {stray}"


def test_fresh_central_install_has_no_bead_views(tmp_path: Path) -> None:
    """A new central.db must not contain the v_cross_project_discoveries view."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION

    cm = ConnectionManager(tmp_path / "central.db")
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    views = _view_set(conn)
    stray = DROPPED_VIEWS & views
    assert not stray, f"Fresh central DB still has bead views: {stray}"


# ---------------------------------------------------------------------------
# 3. Migration path: v41 → v42 drops bead tables
# ---------------------------------------------------------------------------


def _build_v41_project_db(db_path: Path) -> None:
    """Create a minimal v41 baton.db with the bead tables that v42 drops."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL);
        INSERT INTO _schema_version (version) VALUES (41);

        -- Core table required so PROJECT_SCHEMA_DDL references resolve.
        CREATE TABLE IF NOT EXISTS executions (
            task_id TEXT PRIMARY KEY,
            status  TEXT NOT NULL DEFAULT 'pending'
        );

        -- Bead tables that v42 should drop.
        CREATE TABLE IF NOT EXISTS beads (
            bead_id   TEXT PRIMARY KEY,
            task_id   TEXT,
            step_id   TEXT NOT NULL DEFAULT '',
            agent_name TEXT NOT NULL DEFAULT '',
            bead_type  TEXT NOT NULL DEFAULT 'info',
            content    TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'medium',
            scope      TEXT NOT NULL DEFAULT 'step',
            tags       TEXT NOT NULL DEFAULT '[]',
            affected_files TEXT NOT NULL DEFAULT '[]',
            status     TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bead_tags (
            bead_id TEXT NOT NULL,
            tag     TEXT NOT NULL,
            PRIMARY KEY (bead_id, tag)
        );
        CREATE TABLE IF NOT EXISTS bead_anchors (
            bead_id       TEXT PRIMARY KEY,
            anchor_commit TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bead_edges (
            src_bead_id TEXT NOT NULL,
            dst_bead_id TEXT NOT NULL,
            edge_type   TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 0.0,
            created_at  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (src_bead_id, dst_bead_id, edge_type)
        );
        CREATE TABLE IF NOT EXISTS bead_clusters (
            cluster_id TEXT PRIMARY KEY,
            label      TEXT NOT NULL DEFAULT '',
            bead_ids   TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS handoff_beads (
            handoff_id   TEXT PRIMARY KEY,
            task_id      TEXT NOT NULL DEFAULT '',
            from_step_id TEXT NOT NULL DEFAULT '',
            to_step_id   TEXT NOT NULL DEFAULT '',
            content      TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()


def test_v42_migration_drops_bead_tables_from_existing_db(
    tmp_path: Path,
) -> None:
    """Upgrading v41 → v42 must drop all six bead tables."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    db = tmp_path / "baton.db"
    _build_v41_project_db(db)

    # Confirm bead tables exist before migration.
    raw_before = sqlite3.connect(str(db))
    tables_before = _table_set(raw_before)
    raw_before.close()
    assert DROPPED_TABLES <= tables_before, (
        "Test setup error — bead tables not present before migration"
    )

    # Open via ConnectionManager which triggers v41 → v42 migration.
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    tables_after = _table_set(conn)
    cm.close()

    stray = DROPPED_TABLES & tables_after
    assert not stray, (
        f"v42 migration failed to drop bead tables: {stray}"
    )


def test_v42_migration_is_idempotent_when_tables_absent(
    tmp_path: Path,
) -> None:
    """Applying v42 when bead tables were already absent must not raise."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    # Create a minimal DB at v41 WITHOUT bead tables (simulates a project
    # that only partially received earlier migrations).
    db = tmp_path / "baton.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL);
        INSERT INTO _schema_version (version) VALUES (41);
        CREATE TABLE IF NOT EXISTS executions (
            task_id TEXT PRIMARY KEY,
            status  TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    conn.commit()
    conn.close()

    # Must not raise even though the DROP TABLE targets don't exist.
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    cm.close()


# ---------------------------------------------------------------------------
# 4. Schema manifest checks
# ---------------------------------------------------------------------------


def test_schema_version_is_42() -> None:
    from agent_baton.core.storage.schema import SCHEMA_VERSION

    assert SCHEMA_VERSION == 42


def test_migrations_dict_has_v42_entry() -> None:
    from agent_baton.core.storage.schema import MIGRATIONS

    assert 42 in MIGRATIONS, "MIGRATIONS[42] entry is missing"
    body = MIGRATIONS[42]
    # Every dropped table must appear in the migration body.
    for table in DROPPED_TABLES:
        assert table in body, (
            f"MIGRATIONS[42] does not mention {table!r}"
        )
