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
