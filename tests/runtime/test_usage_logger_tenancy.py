"""Tests for tenancy tagging in the usage logger (bd-c44c).

Covers:

* Default env -> defaults populated.
* Env var wins.
* identity.yaml beats defaults but loses to env var.
* SQLite v_usage_by_team aggregates non-NULL groups after 3 logs.
* agent_type round-trips through AgentUsageRecord.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.runtime import tenancy_context
from agent_baton.core.runtime.tenancy_context import (
    DEFAULT_ORG_ID,
    DEFAULT_TEAM_ID,
    DEFAULT_USER_ID,
    get_current_tenancy,
)
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all BATON_* tenancy env vars and the resolver cache."""
    for key in (
        "BATON_ORG_ID",
        "BATON_TEAM_ID",
        "BATON_USER_ID",
        "BATON_SPEC_AUTHOR_ID",
        "BATON_COST_CENTER",
    ):
        monkeypatch.delenv(key, raising=False)
    tenancy_context.reset_tenancy_cache()
    yield
    tenancy_context.reset_tenancy_cache()


def _make_record(task_id: str = "t1") -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-04-25T00:00:00Z",
        agents_used=[
            AgentUsageRecord(
                name="architect",
                model="sonnet",
                steps=1,
                estimated_tokens=100,
                duration_seconds=1.5,
            )
        ],
        total_agents=1,
    )


# ── Resolver ──────────────────────────────────────────────────────────────


def test_default_env_yields_defaults(tmp_path: Path) -> None:
    ctx = get_current_tenancy(refresh=True, identity_path=tmp_path / "missing.yaml")
    assert ctx.org_id == DEFAULT_ORG_ID
    assert ctx.team_id == DEFAULT_TEAM_ID
    assert ctx.user_id == DEFAULT_USER_ID
    assert ctx.spec_author_id == ""
    assert ctx.cost_center == ""


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BATON_TEAM_ID", "eng-platform")
    ctx = get_current_tenancy(refresh=True, identity_path=tmp_path / "missing.yaml")
    assert ctx.team_id == "eng-platform"
    assert ctx.org_id == DEFAULT_ORG_ID  # unaffected


def test_identity_yaml_beats_default_loses_to_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    yaml_path = tmp_path / "identity.yaml"
    yaml_path.write_text("team_id: data-platform\norg_id: acme\n", encoding="utf-8")

    # YAML beats default
    ctx = get_current_tenancy(refresh=True, identity_path=yaml_path)
    assert ctx.team_id == "data-platform"
    assert ctx.org_id == "acme"

    # Env beats YAML
    monkeypatch.setenv("BATON_TEAM_ID", "eng-platform")
    ctx = get_current_tenancy(refresh=True, identity_path=yaml_path)
    assert ctx.team_id == "eng-platform"
    assert ctx.org_id == "acme"  # YAML still wins where env is unset


# ── JSONL UsageLogger writes tenancy ──────────────────────────────────────


def test_jsonl_logger_stamps_defaults(tmp_path: Path) -> None:
    log_path = tmp_path / "usage.jsonl"
    logger = UsageLogger(log_path=log_path)
    logger.log(_make_record())

    records = logger.read_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.org_id == DEFAULT_ORG_ID
    assert rec.team_id == DEFAULT_TEAM_ID
    assert rec.user_id == DEFAULT_USER_ID


def test_jsonl_logger_picks_up_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BATON_TEAM_ID", "eng-platform")
    log_path = tmp_path / "usage.jsonl"
    logger = UsageLogger(log_path=log_path)
    logger.log(_make_record())

    rec = logger.read_all()[0]
    assert rec.team_id == "eng-platform"


# ── SQLite writer + v_usage_by_team aggregation ──────────────────────────


def _open_sqlite(db_path: Path) -> SqliteStorage:
    """Open a SqliteStorage with the project schema applied."""
    storage = SqliteStorage(db_path=db_path)
    # Force schema init by triggering a connection.
    storage._conn()
    return storage


def test_sqlite_writer_populates_team_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BATON_TEAM_ID", "eng-platform")
    monkeypatch.setenv("BATON_ORG_ID", "acme")
    storage = _open_sqlite(tmp_path / "baton.db")

    for i in range(3):
        rec = _make_record(task_id=f"task-{i}")
        storage.log_usage(rec)

    conn = sqlite3.connect(tmp_path / "baton.db")
    conn.row_factory = sqlite3.Row
    rows = list(
        conn.execute(
            "SELECT * FROM v_usage_by_team WHERE team_id = ?",
            ("eng-platform",),
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["team_id"] == "eng-platform"
    assert row["task_count"] == 3
    assert row["total_tokens"] == 300  # 100 per task


def test_sqlite_writer_populates_agent_type(tmp_path: Path) -> None:
    storage = _open_sqlite(tmp_path / "baton.db")
    rec = TaskUsageRecord(
        task_id="t-with-type",
        timestamp="2026-04-25T00:00:00Z",
        agents_used=[
            AgentUsageRecord(
                name="architect",
                model="sonnet",
                agent_type="ENGINEERING",
                estimated_tokens=42,
            )
        ],
        total_agents=1,
    )
    storage.log_usage(rec)

    conn = sqlite3.connect(tmp_path / "baton.db")
    row = conn.execute(
        "SELECT agent_type FROM agent_usage WHERE task_id = ?",
        ("t-with-type",),
    ).fetchone()
    assert row is not None
    assert row[0] == "ENGINEERING"
