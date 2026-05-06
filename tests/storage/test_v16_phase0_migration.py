"""Schema-level acceptance tests for the v16 Phase 0 foundation migration.

The strategic spec promises that all four F0.x primitives have backing
DDL that lands in BOTH fresh installs and via incremental migration on
existing databases.  These tests verify that promise.

Findings from this suite (filed as BEAD_WARNING):
* SCHEMA_VERSION is still 15 after Phase 0; no MIGRATIONS[16] entry.
* PROJECT_SCHEMA_DDL omits the F0.x tables — fresh project DBs have no
  specs/tenancy/compliance_log/knowledge_telemetry tables.
* CENTRAL_SCHEMA_DDL DOES include the F0.x tables, so fresh central.db
  installs work.

These gaps are documented as xfail tests so the follow-up bead has a
machine-checkable acceptance criterion.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# Tables expected to exist after v16 migration on either DB flavor.
F0_TABLES = {
    "specs",
    "spec_plan_links",
    "tenancy_orgs",
    "tenancy_teams",
    "tenancy_cost_centers",
    "compliance_log",
    "knowledge_telemetry",
    "knowledge_doc_meta",
}

F0_VIEWS = {
    "v_usage_by_team",
    "v_usage_by_org",
    "v_usage_by_cost_center",
    "v_knowledge_effectiveness",
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


# ---------------------------------------------------------------------
# Fresh-install central.db: should have all F0.x tables
# ---------------------------------------------------------------------

def test_fresh_central_install_has_all_f0_tables(tmp_path: Path) -> None:
    """A brand-new central.db must contain every F0.x table."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        CENTRAL_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "central.db"
    cm = ConnectionManager(db)
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    tables = _table_set(conn)
    missing = F0_TABLES - tables
    assert not missing, f"central.db missing F0.x tables: {missing}"


def test_fresh_central_install_has_all_f0_views(tmp_path: Path) -> None:
    """Analytics views must also be present."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        CENTRAL_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "central.db"
    cm = ConnectionManager(db)
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    views = _view_set(conn)
    missing = F0_VIEWS - views
    assert not missing, f"central.db missing F0.x views: {missing}"


# ---------------------------------------------------------------------
# Schema initialization is idempotent: re-running causes no errors
# ---------------------------------------------------------------------

def test_central_schema_initialization_is_idempotent(tmp_path: Path) -> None:
    """Re-applying CENTRAL_SCHEMA_DDL on an already-initialized DB must
    not raise — the strategic spec calls for safe re-init on upgrade."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        CENTRAL_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "central.db"
    # First init.
    cm1 = ConnectionManager(db)
    cm1.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    cm1.get_connection()
    cm1.close()

    # Second init on the same path — must not raise.
    cm2 = ConnectionManager(db)
    cm2.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm2.get_connection()
    tables = _table_set(conn)
    assert F0_TABLES <= tables


# ---------------------------------------------------------------------
# Hash-chain table shape (compliance_log)
# ---------------------------------------------------------------------

def test_compliance_log_table_has_chain_columns(tmp_path: Path) -> None:
    """compliance_log must expose prev_hash + entry_hash for hash-chain
    tamper-evidence (F0.3 acceptance criterion)."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        CENTRAL_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "central.db"
    cm = ConnectionManager(db)
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    cols = {
        r[1]
        for r in conn.execute(
            "PRAGMA table_info(compliance_log)"
        ).fetchall()
    }
    assert "prev_hash" in cols
    assert "entry_hash" in cols


# ---------------------------------------------------------------------
# DEFERRED gaps: filed as xfail so follow-up beads have an acceptance test
# ---------------------------------------------------------------------

def test_schema_version_bumped_to_16() -> None:
    from agent_baton.core.storage.schema import SCHEMA_VERSION

    assert SCHEMA_VERSION >= 16


def test_fresh_project_install_has_all_f0_tables(tmp_path: Path) -> None:
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        PROJECT_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "baton.db"
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    tables = _table_set(conn)
    missing = F0_TABLES - tables
    assert not missing, f"project baton.db missing F0.x tables: {missing}"


def test_migrations_dict_has_v16_entry() -> None:
    from agent_baton.core.storage.schema import MIGRATIONS

    assert 16 in MIGRATIONS
    body = MIGRATIONS[16]
    assert "specs" in body
    assert "compliance_log" in body
    assert "knowledge_telemetry" in body


# ---------------------------------------------------------------------
# View-shape parity (bd-87ea): v_usage_by_* must have identical column
# shape in PROJECT and CENTRAL DBs so consumers can issue identical SQL.
# ---------------------------------------------------------------------

_USAGE_VIEWS = ("v_usage_by_team", "v_usage_by_org", "v_usage_by_cost_center")


def _columns_for_view(conn: sqlite3.Connection, view: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({view})").fetchall()]


def _fresh_db(
    tmp_path: Path, name: str, ddl_attr: str
) -> sqlite3.Connection:
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage import schema as schema_mod

    ddl = getattr(schema_mod, ddl_attr)
    cm = ConnectionManager(tmp_path / name)
    cm.configure_schema(ddl, schema_mod.SCHEMA_VERSION)
    return cm.get_connection()


def test_v_usage_views_have_identical_shape_across_db_flavors(
    tmp_path: Path,
) -> None:
    """Both DB flavors must expose the same columns for each tenancy view.

    Per bd-87ea, the per-project DB synthesises ``project_id`` as the
    constant ``'default'`` while central uses the real column.  Either
    way, ``PRAGMA table_info`` should return identical column names.
    """
    proj_dir = tmp_path / "proj"
    cent_dir = tmp_path / "cent"
    proj_dir.mkdir()
    cent_dir.mkdir()
    proj_conn = _fresh_db(proj_dir, "baton.db", "PROJECT_SCHEMA_DDL")
    cent_conn = _fresh_db(cent_dir, "central.db", "CENTRAL_SCHEMA_DDL")

    for view in _USAGE_VIEWS:
        proj_cols = _columns_for_view(proj_conn, view)
        cent_cols = _columns_for_view(cent_conn, view)
        assert proj_cols == cent_cols, (
            f"{view} column shape diverges:\n"
            f"  project: {proj_cols}\n"
            f"  central: {cent_cols}"
        )
        # Sanity: project_id must always be present so downstream
        # GROUP BY / WHERE clauses work uniformly.
        assert "project_id" in proj_cols


def test_v_usage_views_queryable_on_both_db_flavors(tmp_path: Path) -> None:
    """A SELECT against each view must succeed on both DB types."""
    proj_dir = tmp_path / "proj"
    cent_dir = tmp_path / "cent"
    proj_dir.mkdir()
    cent_dir.mkdir()
    proj_conn = _fresh_db(proj_dir, "baton.db", "PROJECT_SCHEMA_DDL")
    cent_conn = _fresh_db(cent_dir, "central.db", "CENTRAL_SCHEMA_DDL")

    for conn in (proj_conn, cent_conn):
        for view in _USAGE_VIEWS:
            # Empty result is fine; we only assert no column-name error.
            rows = conn.execute(
                f"SELECT project_id, task_count, total_tokens, "
                f"total_duration_seconds FROM {view}"
            ).fetchall()
            assert isinstance(rows, list)


def test_v16_migration_drops_and_recreates_views(tmp_path: Path) -> None:
    """Re-running v16 against an existing v16 DB must drop + recreate
    the tenancy views (so a stale shape from an earlier dev build gets
    upgraded to the canonical shape on next open)."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        PROJECT_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "baton.db"

    # 1) Initialise at v15 (no views yet) so migrations fire on next open.
    cm1 = ConnectionManager(db)
    cm1.configure_schema(PROJECT_SCHEMA_DDL, 15)
    cm1.get_connection()
    cm1.close()

    # 2) Open at current version — should run MIGRATIONS[16] and create
    #    the canonical views.
    cm2 = ConnectionManager(db)
    cm2.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn2 = cm2.get_connection()
    first_shape = {v: _columns_for_view(conn2, v) for v in _USAGE_VIEWS}
    cm2.close()

    # 3) Manually corrupt one view to simulate a stale dev definition,
    #    bump the schema version backwards, and re-open: the migration
    #    must DROP the corrupt view and recreate the canonical one.
    raw = sqlite3.connect(db)
    raw.execute("DROP VIEW v_usage_by_team")
    raw.execute(
        "CREATE VIEW v_usage_by_team AS SELECT 1 AS bogus_col"
    )
    raw.execute("UPDATE _schema_version SET version = 15")
    raw.commit()
    raw.close()

    cm3 = ConnectionManager(db)
    cm3.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn3 = cm3.get_connection()
    rebuilt_shape = {v: _columns_for_view(conn3, v) for v in _USAGE_VIEWS}

    assert rebuilt_shape == first_shape, (
        "Re-running v16 migration did not restore the canonical view shape: "
        f"before={first_shape} after={rebuilt_shape}"
    )
    assert "bogus_col" not in rebuilt_shape["v_usage_by_team"]
