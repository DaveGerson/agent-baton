"""Tests for R3.8 — deployment profiles.

Coverage (10 tests):
1.  Migration v16 applies cleanly on a fresh DB.
2.  Store CRUD roundtrip (save → get → delete → gone).
3.  JSON list fields round-trip without corruption.
4.  attach_to_release sets the FK column.
5.  list_all returns all profiles ordered by created_at.
6.  ProfileChecker happy path → empty dict.
7.  ProfileChecker missing gate → reports it.
8.  ProfileChecker risk violation → reports plan_id.
9.  ProfileChecker on missing tables → soft-skip (no crash).
10. ProfileChecker: untracked SLO reported when slo_definitions present.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from agent_baton.core.storage.deployment_profile_store import DeploymentProfileStore
from agent_baton.core.release.profile_checker import ProfileChecker
from agent_baton.models.deployment_profile import DeploymentProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILE_DDL = """
CREATE TABLE IF NOT EXISTS deployment_profiles (
    profile_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    environment         TEXT NOT NULL DEFAULT '',
    required_gates      TEXT NOT NULL DEFAULT '[]',
    target_slos         TEXT NOT NULL DEFAULT '[]',
    allowed_risk_levels TEXT NOT NULL DEFAULT '["LOW","MEDIUM"]',
    description         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS releases (
    release_id            TEXT PRIMARY KEY,
    name                  TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'planned',
    deployment_profile_id TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_releases_profile ON releases(deployment_profile_id);
"""

_GATE_DDL = """
CREATE TABLE IF NOT EXISTS gate_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   TEXT NOT NULL DEFAULT '',
    phase_id  INTEGER NOT NULL DEFAULT 0,
    gate_type TEXT NOT NULL DEFAULT '',
    passed    INTEGER NOT NULL DEFAULT 0,
    output    TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT ''
);
"""

_PLANS_DDL = """
CREATE TABLE IF NOT EXISTS plans (
    task_id    TEXT PRIMARY KEY,
    risk_level TEXT NOT NULL DEFAULT 'LOW',
    release_id TEXT
);
"""

_SLO_DDL = """
CREATE TABLE IF NOT EXISTS slo_definitions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
"""


def _make_db(extra_ddl: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_PROFILE_DDL + extra_ddl)
    return conn


def _make_profile(
    profile_id: str = "dp-test01",
    name: str = "Test Profile",
    environment: str = "staging",
    required_gates: list[str] | None = None,
    target_slos: list[str] | None = None,
    allowed_risk_levels: list[str] | None = None,
    description: str = "a test profile",
    created_at: str = "2026-01-01T00:00:00Z",
) -> DeploymentProfile:
    return DeploymentProfile(
        profile_id=profile_id,
        name=name,
        environment=environment,
        required_gates=required_gates or [],
        target_slos=target_slos or [],
        allowed_risk_levels=allowed_risk_levels or ["LOW", "MEDIUM"],
        description=description,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Test 1 — migration v16 applies cleanly on fresh DB
# ---------------------------------------------------------------------------

def test_migration_v16_applies_cleanly() -> None:
    """MIGRATIONS[16] DDL creates deployment_profiles and releases tables."""
    from agent_baton.core.storage.schema import MIGRATIONS, SCHEMA_VERSION

    assert 16 in MIGRATIONS, "MIGRATIONS[16] must exist"
    assert SCHEMA_VERSION == 16

    conn = sqlite3.connect(":memory:")
    conn.executescript(MIGRATIONS[16])

    # Both tables must exist
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "deployment_profiles" in tables
    assert "releases" in tables

    # Columns on deployment_profiles
    cols = {r[1] for r in conn.execute("PRAGMA table_info(deployment_profiles)").fetchall()}
    for expected in ("profile_id", "name", "environment", "required_gates",
                     "target_slos", "allowed_risk_levels", "description", "created_at"):
        assert expected in cols, f"Column {expected!r} missing from deployment_profiles"

    # releases has deployment_profile_id
    rel_cols = {r[1] for r in conn.execute("PRAGMA table_info(releases)").fetchall()}
    assert "deployment_profile_id" in rel_cols


# ---------------------------------------------------------------------------
# Test 2 — Store CRUD roundtrip
# ---------------------------------------------------------------------------

def test_store_crud_roundtrip() -> None:
    conn = _make_db()
    store = DeploymentProfileStore(conn)
    profile = _make_profile()

    # save → get
    store.save(profile)
    fetched = store.get(profile.profile_id)
    assert fetched is not None
    assert fetched.profile_id == profile.profile_id
    assert fetched.name == profile.name
    assert fetched.environment == profile.environment

    # delete → gone
    store.delete(profile.profile_id)
    assert store.get(profile.profile_id) is None


# ---------------------------------------------------------------------------
# Test 3 — JSON list fields round-trip without corruption
# ---------------------------------------------------------------------------

def test_list_fields_roundtrip() -> None:
    conn = _make_db()
    store = DeploymentProfileStore(conn)
    profile = _make_profile(
        required_gates=["test", "lint", "security"],
        target_slos=["p99_latency", "dispatch_success_rate"],
        allowed_risk_levels=["LOW"],
    )
    store.save(profile)
    fetched = store.get(profile.profile_id)
    assert fetched is not None
    assert fetched.required_gates == ["test", "lint", "security"]
    assert fetched.target_slos == ["p99_latency", "dispatch_success_rate"]
    assert fetched.allowed_risk_levels == ["LOW"]


# ---------------------------------------------------------------------------
# Test 4 — attach_to_release sets the FK column
# ---------------------------------------------------------------------------

def test_attach_to_release() -> None:
    conn = _make_db()
    store = DeploymentProfileStore(conn)
    profile = _make_profile()
    store.save(profile)

    store.attach_to_release("rel-001", profile.profile_id)

    row = conn.execute(
        "SELECT deployment_profile_id FROM releases WHERE release_id = ?",
        ("rel-001",),
    ).fetchone()
    assert row is not None
    assert row[0] == profile.profile_id


# ---------------------------------------------------------------------------
# Test 5 — list_all returns profiles
# ---------------------------------------------------------------------------

def test_list_all_returns_all_profiles() -> None:
    conn = _make_db()
    store = DeploymentProfileStore(conn)

    p1 = _make_profile("dp-a", created_at="2026-01-01T00:00:00Z")
    p2 = _make_profile("dp-b", created_at="2026-01-02T00:00:00Z")
    store.save(p1)
    store.save(p2)

    profiles = store.list_all()
    ids = [p.profile_id for p in profiles]
    assert "dp-a" in ids
    assert "dp-b" in ids


# ---------------------------------------------------------------------------
# Test 6 — ProfileChecker happy path → empty dict
# ---------------------------------------------------------------------------

def test_profile_checker_happy_path() -> None:
    conn = _make_db(_GATE_DDL + _PLANS_DDL)
    store = DeploymentProfileStore(conn)

    profile = _make_profile(
        required_gates=["test"],
        allowed_risk_levels=["LOW", "MEDIUM"],
    )
    store.save(profile)
    store.attach_to_release("rel-happy", profile.profile_id)

    # Insert a plan with LOW risk, tagged to this release
    conn.execute(
        "INSERT INTO plans (task_id, risk_level, release_id) VALUES (?, ?, ?)",
        ("task-1", "LOW", "rel-happy"),
    )
    # Insert a passing gate result for that plan
    conn.execute(
        "INSERT INTO gate_results (task_id, phase_id, gate_type, passed) VALUES (?, 1, 'test', 1)",
        ("task-1",),
    )
    conn.commit()

    checker = ProfileChecker(store)
    result = checker.check("rel-happy")
    assert result == {"missing_gates": [], "untracked_slos": [], "risk_violations": []}


# ---------------------------------------------------------------------------
# Test 7 — ProfileChecker missing gate → reports it
# ---------------------------------------------------------------------------

def test_profile_checker_missing_gate() -> None:
    conn = _make_db(_GATE_DDL + _PLANS_DDL)
    store = DeploymentProfileStore(conn)

    profile = _make_profile(
        profile_id="dp-mg",
        required_gates=["security", "lint"],
    )
    store.save(profile)
    store.attach_to_release("rel-mg", profile.profile_id)

    # Plan exists but only "lint" passed, not "security"
    conn.execute(
        "INSERT INTO plans (task_id, risk_level, release_id) VALUES ('task-mg', 'LOW', 'rel-mg')"
    )
    conn.execute(
        "INSERT INTO gate_results (task_id, phase_id, gate_type, passed) VALUES ('task-mg', 1, 'lint', 1)"
    )
    conn.commit()

    checker = ProfileChecker(store)
    result = checker.check("rel-mg")
    assert "security" in result["missing_gates"]
    assert "lint" not in result["missing_gates"]


# ---------------------------------------------------------------------------
# Test 8 — ProfileChecker risk violation → reports plan_id
# ---------------------------------------------------------------------------

def test_profile_checker_risk_violation() -> None:
    conn = _make_db(_GATE_DDL + _PLANS_DDL)
    store = DeploymentProfileStore(conn)

    profile = _make_profile(
        profile_id="dp-rv",
        allowed_risk_levels=["LOW"],
    )
    store.save(profile)
    store.attach_to_release("rel-rv", profile.profile_id)

    conn.execute(
        "INSERT INTO plans (task_id, risk_level, release_id) VALUES ('task-high', 'HIGH', 'rel-rv')"
    )
    conn.commit()

    checker = ProfileChecker(store)
    result = checker.check("rel-rv")
    assert "task-high" in result["risk_violations"]


# ---------------------------------------------------------------------------
# Test 9 — ProfileChecker on missing tables → soft-skip
# ---------------------------------------------------------------------------

def test_profile_checker_missing_tables_soft_skip() -> None:
    """Checker degrades gracefully when gate_results/plans tables are absent."""
    # DB has only deployment_profiles and releases — no gate_results, no plans
    conn = _make_db()
    store = DeploymentProfileStore(conn)

    profile = _make_profile(
        profile_id="dp-ms",
        required_gates=["test"],
        target_slos=["my_slo"],
        allowed_risk_levels=["LOW"],
    )
    store.save(profile)
    store.attach_to_release("rel-ms", profile.profile_id)

    checker = ProfileChecker(store)
    # Must not raise
    result = checker.check("rel-ms")
    assert isinstance(result, dict)
    assert "missing_gates" in result
    assert "untracked_slos" in result
    assert "risk_violations" in result


# ---------------------------------------------------------------------------
# Test 10 — Untracked SLO reported when slo_definitions present
# ---------------------------------------------------------------------------

def test_profile_checker_untracked_slo() -> None:
    conn = _make_db(_SLO_DDL + _PLANS_DDL)
    store = DeploymentProfileStore(conn)

    # Only "dispatch_success_rate" is defined; profile asks for that + unknown one
    conn.execute("INSERT INTO slo_definitions (name) VALUES ('dispatch_success_rate')")
    conn.commit()

    profile = _make_profile(
        profile_id="dp-slo",
        target_slos=["dispatch_success_rate", "p99_latency"],
    )
    store.save(profile)
    store.attach_to_release("rel-slo", profile.profile_id)

    checker = ProfileChecker(store)
    result = checker.check("rel-slo")
    assert "p99_latency" in result["untracked_slos"]
    assert "dispatch_success_rate" not in result["untracked_slos"]
