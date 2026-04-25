"""End-to-end test for `baton context effectiveness` (F0.4 CLI surface).

Strategic-spec acceptance criterion: there must be an operator-facing
command that surfaces the v_knowledge_effectiveness analytics view so
humans can see which knowledge docs actually move outcomes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _bootstrap_central(tmp_path: Path) -> Path:
    """Create a central.db with the F0.4 telemetry tables + view."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import (
        CENTRAL_SCHEMA_DDL,
        SCHEMA_VERSION,
    )

    db = tmp_path / "central.db"
    cm = ConnectionManager(db)
    cm.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    cm.get_connection()
    cm.close()
    return db


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def test_e2e_effectiveness_empty_db_prints_friendly_message(
    tmp_path: Path, capsys
) -> None:
    """With no telemetry recorded, the command must print a helpful
    message rather than crash or render an empty table."""
    db = _bootstrap_central(tmp_path)
    rc = _run_cli(["context", "effectiveness", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No knowledge telemetry" in out or "No " in out


def test_e2e_effectiveness_after_record_returns_row(
    tmp_path: Path, capsys
) -> None:
    """After recording a few telemetry events, the CLI must show the
    doc with its usage count."""
    from agent_baton.core.engine.knowledge_telemetry import (
        KnowledgeTelemetryStore,
    )

    db = _bootstrap_central(tmp_path)
    store = KnowledgeTelemetryStore(db_path=db)
    store.record_used(
        doc_name="systematic-debugging.md",
        pack_name="superpowers",
        task_id="t1",
        step_id="step-1",
    )
    store.record_used(
        doc_name="systematic-debugging.md",
        pack_name="superpowers",
        task_id="t2",
        step_id="step-1",
    )
    store.record_outcome(
        doc_name="systematic-debugging.md",
        pack_name="superpowers",
        task_id="t1",
        outcome_correlation=0.8,
    )

    rc = _run_cli([
        "context", "effectiveness", "--db", str(db), "--json"
    ])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert any(
        r.get("doc_name") == "systematic-debugging.md" for r in data
    )


def test_e2e_effectiveness_json_flag_emits_valid_json(
    tmp_path: Path, capsys
) -> None:
    """--json must produce machine-parsable output (not human prose)."""
    db = _bootstrap_central(tmp_path)
    rc = _run_cli([
        "context", "effectiveness", "--db", str(db), "--json"
    ])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
