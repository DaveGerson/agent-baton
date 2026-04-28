"""End-to-end tests for F0.2 Tenancy & cost attribution.

Covers:
* identity.yaml round-trip via TenancyStore.write_identity (the persistent
  side of `baton tenancy set-team`).
* v_usage_by_team / v_usage_by_org / v_usage_by_cost_center analytics views
  return the rows the strategic spec promises, when usage_records carry the
  tenancy columns.
* TenancyContext provenance is preserved through a write -> resolve cycle.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


def _build_central_like_db(tmp_path: Path) -> Path:
    """Build a minimal schema with the v16 tenancy tables + views."""
    db = tmp_path / "central.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        -- usage_records carries the tenancy columns added by v16
        CREATE TABLE IF NOT EXISTS usage_records (
            task_id      TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL DEFAULT 'default',
            timestamp    TEXT NOT NULL DEFAULT '',
            outcome      TEXT NOT NULL DEFAULT '',
            org_id       TEXT NOT NULL DEFAULT 'default',
            team_id      TEXT NOT NULL DEFAULT 'default',
            cost_center  TEXT NOT NULL DEFAULT '',
            user_id      TEXT NOT NULL DEFAULT 'local-user'
        );
        CREATE TABLE IF NOT EXISTS agent_usage (
            project_id        TEXT NOT NULL DEFAULT 'default',
            task_id           TEXT NOT NULL,
            agent             TEXT NOT NULL DEFAULT '',
            estimated_tokens  INTEGER NOT NULL DEFAULT 0,
            duration_seconds  REAL    NOT NULL DEFAULT 0.0,
            PRIMARY KEY (project_id, task_id, agent)
        );
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
        CREATE VIEW IF NOT EXISTS v_usage_by_team AS
        SELECT
            ur.team_id,
            ur.project_id,
            COUNT(DISTINCT ur.task_id)  AS task_count,
            SUM(au.estimated_tokens)    AS total_tokens,
            SUM(au.duration_seconds)    AS total_duration_seconds
        FROM usage_records ur
        LEFT JOIN agent_usage au
            ON au.project_id = ur.project_id AND au.task_id = ur.task_id
        GROUP BY ur.project_id, ur.team_id;
        CREATE VIEW IF NOT EXISTS v_usage_by_org AS
        SELECT
            ur.org_id,
            ur.project_id,
            COUNT(DISTINCT ur.task_id)  AS task_count,
            SUM(au.estimated_tokens)    AS total_tokens,
            SUM(au.duration_seconds)    AS total_duration_seconds
        FROM usage_records ur
        LEFT JOIN agent_usage au
            ON au.project_id = ur.project_id AND au.task_id = ur.task_id
        GROUP BY ur.project_id, ur.org_id;
        CREATE VIEW IF NOT EXISTS v_usage_by_cost_center AS
        SELECT
            ur.cost_center,
            ur.project_id,
            COUNT(DISTINCT ur.task_id)  AS task_count,
            SUM(au.estimated_tokens)    AS total_tokens,
            SUM(au.duration_seconds)    AS total_duration_seconds
        FROM usage_records ur
        LEFT JOIN agent_usage au
            ON au.project_id = ur.project_id AND au.task_id = ur.task_id
        GROUP BY ur.project_id, ur.cost_center;
        """
    )
    conn.commit()
    conn.close()
    return db


def test_e2e_set_team_then_resolve_returns_team(tmp_path: Path) -> None:
    """write_identity (the storage side of `baton tenancy set-team`) ->
    resolve_tenancy_context returns the persisted team."""
    from agent_baton.models.tenancy import (
        TenancyStore,
        resolve_tenancy_context,
    )

    identity_file = tmp_path / "identity.yaml"
    # Clear env + patch identity file path for both write and resolve.
    env_no_baton = {
        k: v for k, v in os.environ.items() if not k.startswith("BATON_")
    }
    with patch.dict(os.environ, env_no_baton, clear=True):
        with patch("agent_baton.models.tenancy._IDENTITY_FILE", identity_file):
            TenancyStore.write_identity(
                team_id="eng-platform",
                org_id="acme",
                user_id="alice",
                cost_center="cc-eng",
            )
            assert identity_file.exists()
            ctx = resolve_tenancy_context()
    assert ctx.team_id == "eng-platform"
    assert ctx.org_id == "acme"
    assert ctx.user_id == "alice"
    assert ctx.cost_center == "cc-eng"


def test_e2e_v_usage_by_team_aggregates_tokens(tmp_path: Path) -> None:
    """When usage_records carry team_id and agent_usage has tokens,
    v_usage_by_team must sum tokens per (project, team)."""
    db = _build_central_like_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO usage_records "
        "(task_id, project_id, team_id, org_id) "
        "VALUES (?, ?, ?, ?)",
        ("task-A", "proj1", "eng-platform", "acme"),
    )
    conn.execute(
        "INSERT INTO usage_records "
        "(task_id, project_id, team_id, org_id) "
        "VALUES (?, ?, ?, ?)",
        ("task-B", "proj1", "eng-platform", "acme"),
    )
    conn.execute(
        "INSERT INTO agent_usage "
        "(project_id, task_id, agent, estimated_tokens, duration_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        ("proj1", "task-A", "planner", 100, 5.0),
    )
    conn.execute(
        "INSERT INTO agent_usage "
        "(project_id, task_id, agent, estimated_tokens, duration_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        ("proj1", "task-B", "planner", 250, 12.0),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT * FROM v_usage_by_team WHERE team_id = 'eng-platform'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["task_count"] == 2
    assert row["total_tokens"] == 350
    assert row["total_duration_seconds"] == pytest.approx(17.0)
    conn.close()


def test_e2e_v_usage_by_org_groups_independently(tmp_path: Path) -> None:
    """Two orgs in same project must produce two rows in v_usage_by_org."""
    db = _build_central_like_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    for tid, org in [("t1", "acme"), ("t2", "acme"), ("t3", "globex")]:
        conn.execute(
            "INSERT INTO usage_records "
            "(task_id, project_id, org_id, team_id) "
            "VALUES (?, ?, ?, 'default')",
            (tid, "proj1", org),
        )
        conn.execute(
            "INSERT INTO agent_usage "
            "(project_id, task_id, agent, estimated_tokens, duration_seconds) "
            "VALUES ('proj1', ?, 'a', 10, 1.0)",
            (tid,),
        )
    conn.commit()
    rows = {r["org_id"]: r for r in conn.execute(
        "SELECT * FROM v_usage_by_org WHERE project_id = 'proj1'"
    )}
    assert set(rows) == {"acme", "globex"}
    assert rows["acme"]["task_count"] == 2
    assert rows["globex"]["task_count"] == 1
    conn.close()


def test_e2e_v_usage_by_cost_center_aggregates(tmp_path: Path) -> None:
    db = _build_central_like_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO usage_records "
        "(task_id, project_id, cost_center) VALUES ('t1', 'p', 'cc-eng')"
    )
    conn.execute(
        "INSERT INTO agent_usage "
        "(project_id, task_id, agent, estimated_tokens, duration_seconds) "
        "VALUES ('p', 't1', 'a', 500, 3.0)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM v_usage_by_cost_center WHERE cost_center = 'cc-eng'"
    ).fetchone()
    assert row is not None
    assert row["total_tokens"] == 500
    conn.close()
