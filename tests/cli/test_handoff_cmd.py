"""Tests for ``baton handoff`` and ``baton execute handoff`` (DX.3 / bd-d136).

Coverage:
- HandoffStore round-trip: record -> get / list_recent.
- v18 schema migration: handoffs table appears in PROJECT_SCHEMA_DDL and
  in MIGRATIONS.
- CLI ``handoff record`` writes a row, prints the score column on
  ``--score``, and the score reflects what score_handoff() would return.
- CLI ``handoff list`` prints the table with score column.
- CLI ``handoff show`` prints the full record + breakdown.
- ``baton execute handoff --note ...`` form (subcommand of execute) also
  records a row.
- ``--note`` with no value fails validation.
- The graceful-degradation path returns "" when the table is absent.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.execution import execute as _execute_mod
from agent_baton.cli.commands.execution import handoff as _handoff_mod
from agent_baton.core.improve.handoff_score import (
    BranchState,
    score_handoff,
)
from agent_baton.core.storage.handoff_store import HandoffRecord, HandoffStore
from agent_baton.core.storage.schema import (
    MIGRATIONS,
    PROJECT_SCHEMA_DDL,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    """Return an absolute baton.db path inside a fresh project layout."""
    project_root = tmp_path / "proj"
    (project_root / ".claude" / "team-context").mkdir(parents=True)
    return project_root / "baton.db"


def _build_execute_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    _execute_mod.register(sub)
    return root


def _build_handoff_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    _handoff_mod.register(sub)
    return root


def _good_note() -> str:
    return (
        "Wired HandoffStore and v18 migration in agent_baton/core/storage/handoff_store.py. "
        "Tests passing locally; no blockers. Next: hook the CLI tomorrow."
    )


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_schema_version_at_least_18():
    assert SCHEMA_VERSION >= 18


def test_handoffs_table_in_project_schema_ddl():
    assert "CREATE TABLE IF NOT EXISTS handoffs" in PROJECT_SCHEMA_DDL


def test_handoffs_migration_registered():
    # The migration's version is whatever introduced the handoffs table.
    versions_with_handoffs = [
        v for v, ddl in MIGRATIONS.items()
        if "CREATE TABLE IF NOT EXISTS handoffs" in ddl
    ]
    assert versions_with_handoffs, "no migration introduces the handoffs table"
    assert max(versions_with_handoffs) <= SCHEMA_VERSION


# ---------------------------------------------------------------------------
# HandoffStore round-trip
# ---------------------------------------------------------------------------


def test_handoff_store_record_get_round_trip(tmp_path: Path):
    db = _make_db(tmp_path)
    store = HandoffStore(db)
    hid = store.record(
        task_id="task-abc",
        note=_good_note(),
        branch="feature/dx3",
        commits_ahead=2,
        git_dirty=False,
        quality_score=0.8,
        score_breakdown={"length_and_specificity": 0.2, "next_step": 0.2,
                         "blocker": 0.2, "branch_state": 0.2, "test_state": 0.0},
    )
    assert hid.startswith("ho-")
    rec = store.get(hid)
    assert rec is not None
    assert isinstance(rec, HandoffRecord)
    assert rec.handoff_id == hid
    assert rec.task_id == "task-abc"
    assert rec.note == _good_note()
    assert rec.branch == "feature/dx3"
    assert rec.commits_ahead == 2
    assert rec.git_dirty is False
    assert rec.quality_score == pytest.approx(0.8)
    assert rec.score_breakdown["next_step"] == pytest.approx(0.2)


def test_handoff_store_list_recent_orders_newest_first(tmp_path: Path):
    db = _make_db(tmp_path)
    store = HandoffStore(db)
    h1 = store.record(task_id="t1", note="first one", created_at="2026-04-25T10:00:00Z")
    h2 = store.record(task_id="t1", note="second", created_at="2026-04-25T11:00:00Z")
    h3 = store.record(task_id="t2", note="other task", created_at="2026-04-25T12:00:00Z")

    rows = store.list_recent(limit=10)
    ids_in_order = [r.handoff_id for r in rows]
    assert ids_in_order == [h3, h2, h1]

    rows_t1 = store.list_recent(task_id="t1", limit=10)
    assert [r.handoff_id for r in rows_t1] == [h2, h1]


def test_handoff_store_get_returns_none_for_missing(tmp_path: Path):
    db = _make_db(tmp_path)
    store = HandoffStore(db)
    assert store.get("ho-nonexistent") is None


def test_handoff_store_has_any_for_task(tmp_path: Path):
    db = _make_db(tmp_path)
    store = HandoffStore(db)
    assert store.has_any_for_task("t1") is False
    store.record(task_id="t1", note="hi")
    assert store.has_any_for_task("t1") is True
    assert store.has_any_for_task("t-other") is False


def test_handoff_store_graceful_degradation_when_table_absent(tmp_path: Path):
    db = tmp_path / "stale.db"
    # Hand-create a DB with the schema-version table at v3 (pre-handoffs)
    # and NO _schema_version row, so ConnectionManager will think it's a
    # legacy DB and try to migrate -- but we drop the handoffs table
    # immediately after to simulate the table being absent.
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    # Bypass the migration by patching ``_table_exists`` to False -- this
    # mirrors the production "older schema" path.
    store = HandoffStore(db)
    with patch.object(store, "_table_exists", return_value=False):
        assert store.record(task_id="t", note="x") == ""
        assert store.get("ho-x") is None
        assert store.list_recent() == []
        assert store.has_any_for_task("t") is False


# ---------------------------------------------------------------------------
# CLI: top-level ``baton handoff``
# ---------------------------------------------------------------------------


def test_cli_top_level_handoff_record(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    # Anchor the CWD to the project root so context-root resolution works.
    monkeypatch.chdir(db.parent)

    parser = _build_handoff_parser()
    note = _good_note()
    args = parser.parse_args([
        "handoff", "record",
        "--note", note,
        "--task-id", "task-cli",
        "--score",
    ])
    # _detect_branch_state uses git; force it to return a known clean state
    # so the test does not depend on the current working tree.
    with patch.object(_handoff_mod, "_detect_branch_state",
                      return_value=BranchState(branch="", commits_ahead=0, dirty=False)):
        _handoff_mod.handler(args)

    out = capsys.readouterr().out
    assert "Recorded handoff: ho-" in out
    assert "task-cli" in out
    assert "quality_score:" in out
    # Verify the row landed in the DB.
    store = HandoffStore(db)
    rows = store.list_recent(task_id="task-cli")
    assert len(rows) == 1
    assert rows[0].note == note
    # The score the CLI persists should match what score_handoff would say.
    expected = score_handoff(note, BranchState()).total
    assert rows[0].quality_score == pytest.approx(expected)


def test_cli_top_level_handoff_record_json(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    parser = _build_handoff_parser()
    args = parser.parse_args([
        "handoff", "record",
        "--note", _good_note(),
        "--task-id", "tjson",
        "--output", "json",
    ])
    with patch.object(_handoff_mod, "_detect_branch_state",
                      return_value=BranchState()):
        _handoff_mod.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == "tjson"
    assert payload["handoff_id"].startswith("ho-")
    assert "quality_score" in payload
    assert "score_breakdown" in payload


def test_cli_top_level_handoff_list_shows_score_column(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    store = HandoffStore(db)
    store.record(task_id="task-list", note="alpha", quality_score=0.4,
                 score_breakdown={"x": 0.2}, created_at="2026-04-25T08:00:00Z")
    store.record(task_id="task-list", note="beta", quality_score=0.8,
                 score_breakdown={"y": 0.4}, created_at="2026-04-25T09:00:00Z")

    parser = _build_handoff_parser()
    args = parser.parse_args(["handoff", "list", "--task-id", "task-list"])
    _handoff_mod.handler(args)
    out = capsys.readouterr().out
    assert "SCORE" in out
    # Both notes should be present.
    assert "alpha" in out
    assert "beta" in out
    # And both scores should be rendered with two decimals.
    assert "0.40" in out
    assert "0.80" in out


def test_cli_top_level_handoff_show(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    store = HandoffStore(db)
    hid = store.record(
        task_id="t-show",
        note=_good_note(),
        quality_score=0.6,
        score_breakdown={"length_and_specificity": 0.2, "next_step": 0.2,
                         "blocker": 0.2, "branch_state": 0.0, "test_state": 0.0},
    )

    parser = _build_handoff_parser()
    args = parser.parse_args(["handoff", "show", hid])
    _handoff_mod.handler(args)
    out = capsys.readouterr().out
    assert hid in out
    assert "quality_score: 0.60" in out
    assert "next_step: 0.20" in out
    assert "--- Note ---" in out
    assert _good_note() in out


def test_cli_top_level_handoff_record_requires_note(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    parser = _build_handoff_parser()
    # --note missing on the explicit ``record`` subcommand triggers
    # argparse-level required failure (SystemExit 2).
    with pytest.raises(SystemExit):
        parser.parse_args(["handoff", "record", "--task-id", "x"])


def test_cli_top_level_handoff_bare_form_requires_note(
    tmp_path: Path, monkeypatch, capsys,
):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    parser = _build_handoff_parser()
    args = parser.parse_args(["handoff"])  # no note, no subcommand
    with pytest.raises(SystemExit):
        _handoff_mod.handler(args)
    err = capsys.readouterr().err
    assert "--note is required" in err


# ---------------------------------------------------------------------------
# CLI: ``baton execute handoff --note ...`` form (the spec's headline form)
# ---------------------------------------------------------------------------


def test_cli_execute_handoff_records_row(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    parser = _build_execute_parser()
    args = parser.parse_args([
        "execute", "handoff",
        "--note", _good_note(),
        "--task-id", "task-exec",
        "--score",
    ])
    with patch.object(_handoff_mod, "_detect_branch_state",
                      return_value=BranchState()):
        _execute_mod.handler(args)

    out = capsys.readouterr().out
    assert "Recorded handoff: ho-" in out
    rows = HandoffStore(db).list_recent(task_id="task-exec")
    assert len(rows) == 1
    assert rows[0].note == _good_note()


def test_cli_execute_handoff_list_subcommand(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    HandoffStore(db).record(task_id="t-el", note="entry one",
                            quality_score=0.6, score_breakdown={})

    parser = _build_execute_parser()
    args = parser.parse_args([
        "execute", "handoff", "list",
        "--task-id", "t-el",
    ])
    _execute_mod.handler(args)
    out = capsys.readouterr().out
    assert "SCORE" in out
    assert "entry one" in out


def test_cli_execute_handoff_show_subcommand(tmp_path: Path, capsys, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    monkeypatch.chdir(db.parent)

    hid = HandoffStore(db).record(
        task_id="t-es", note="show me",
        quality_score=0.4,
        score_breakdown={"blocker": 0.2, "test_state": 0.2},
    )

    parser = _build_execute_parser()
    args = parser.parse_args([
        "execute", "handoff", "show", hid,
    ])
    _execute_mod.handler(args)
    out = capsys.readouterr().out
    assert hid in out
    assert "blocker: 0.20" in out
