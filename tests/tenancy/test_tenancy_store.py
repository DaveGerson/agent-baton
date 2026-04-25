"""Tests for F0.2 TenancyStore — org/team/cost-center CRUD."""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from agent_baton.models.tenancy import TenancyStore, Org, Team, CostCenter


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "tenancy_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenancy_orgs (
            org_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tenancy_teams (
            team_id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tenancy_cost_centers (
            cost_center_id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL DEFAULT 'default',
            display_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS usage_records (
            task_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL DEFAULT '',
            total_agents INTEGER NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'LOW',
            sequencing_mode TEXT NOT NULL DEFAULT '',
            gates_passed INTEGER NOT NULL DEFAULT 0,
            gates_failed INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            org_id TEXT NOT NULL DEFAULT '',
            team_id TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def store(db: Path) -> TenancyStore:
    return TenancyStore(db_path=db)


def test_create_org(store: TenancyStore) -> None:
    org = store.create_org("acme", "Acme Corp")
    assert org.org_id == "acme"
    assert org.display_name == "Acme Corp"


def test_get_org_returns_created(store: TenancyStore) -> None:
    store.create_org("test-org", "Test Org")
    org = store.get_org("test-org")
    assert org is not None
    assert org.org_id == "test-org"


def test_get_org_nonexistent_returns_none(store: TenancyStore) -> None:
    assert store.get_org("nope") is None


def test_list_orgs(store: TenancyStore) -> None:
    store.create_org("org-a")
    store.create_org("org-b")
    orgs = store.list_orgs()
    ids = [o.org_id for o in orgs]
    assert "org-a" in ids
    assert "org-b" in ids


def test_create_team(store: TenancyStore) -> None:
    store.create_org("myorg")
    team = store.create_team("eng-platform", "myorg", "Engineering Platform")
    assert team.team_id == "eng-platform"
    assert team.org_id == "myorg"
    assert team.display_name == "Engineering Platform"


def test_get_team(store: TenancyStore) -> None:
    store.create_team("alpha")
    team = store.get_team("alpha")
    assert team is not None
    assert team.team_id == "alpha"


def test_get_team_nonexistent(store: TenancyStore) -> None:
    assert store.get_team("nope") is None


def test_list_teams_all(store: TenancyStore) -> None:
    store.create_team("t1", "org-x")
    store.create_team("t2", "org-y")
    teams = store.list_teams()
    assert len(teams) >= 2


def test_list_teams_filtered_by_org(store: TenancyStore) -> None:
    store.create_team("ta", "org-a")
    store.create_team("tb", "org-b")
    teams = store.list_teams(org_id="org-a")
    assert all(t.org_id == "org-a" for t in teams)


def test_create_cost_center(store: TenancyStore) -> None:
    cc = store.create_cost_center("cc-eng", "myorg", "Engineering")
    assert cc.cost_center_id == "cc-eng"
    assert cc.org_id == "myorg"


def test_create_team_idempotent(store: TenancyStore) -> None:
    store.create_team("same-team")
    store.create_team("same-team")  # second call must not error
    teams = store.list_teams()
    assert sum(1 for t in teams if t.team_id == "same-team") == 1


def test_migrate_existing_updates_rows(store: TenancyStore) -> None:
    db = store._db_path
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO usage_records (task_id, timestamp) VALUES ('t1', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    updated = store.migrate_existing(org_id="acme", team_id="eng")
    assert updated >= 1
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT org_id, team_id FROM usage_records WHERE task_id='t1'"
    ).fetchone()
    conn.close()
    assert row[0] == "acme"
    assert row[1] == "eng"
