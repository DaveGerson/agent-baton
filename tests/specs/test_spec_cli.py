"""Smoke tests for F0.1 baton spec CLI commands."""
from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specs (
            spec_id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'default',
            author_id TEXT NOT NULL DEFAULT 'local-user', task_type TEXT NOT NULL DEFAULT '',
            template_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'draft', content TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '', score_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
            approved_at TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS spec_plan_links (
            spec_id TEXT NOT NULL, task_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default', linked_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (spec_id, task_id)
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def _run_cli(argv: list[str]) -> int:
    """Run the baton CLI with given args; return exit code (0 = success)."""
    from agent_baton.cli.main import main
    try:
        main(argv)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def test_spec_create_smoke(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Test Feature"])


def test_spec_list_smoke(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Feature A"])
    _run_cli(["spec", "--db", str(db), "list"])


def test_spec_list_json(tmp_path: Path, capsys) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Feature B"])
    capsys.readouterr()  # flush create output
    _run_cli(["spec", "--db", str(db), "list", "--json"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["title"] == "Feature B"


def test_spec_show_smoke(tmp_path: Path, capsys) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Show Me"])
    captured = capsys.readouterr()
    # Extract spec_id from "Created spec <id>" line
    spec_id = captured.out.split("Created spec ")[1].split("\n")[0].strip()
    _run_cli(["spec", "--db", str(db), "show", spec_id])
    captured2 = capsys.readouterr()
    assert "Show Me" in captured2.out


def test_spec_approve_smoke(tmp_path: Path, capsys) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Approve Me"])
    captured = capsys.readouterr()
    spec_id = captured.out.split("Created spec ")[1].split("\n")[0].strip()
    # draft → approved (direct transition allowed per store)
    _run_cli(["spec", "--db", str(db), "approve", spec_id])
    captured2 = capsys.readouterr()
    assert "Approved" in captured2.out


def test_spec_link_smoke(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Link Test"])
    from agent_baton.core.specs.store import SpecStore
    store = SpecStore(db_path=db)
    specs = store.list()
    assert len(specs) == 1
    spec_id = specs[0].spec_id
    _run_cli(["spec", "--db", str(db), "link", spec_id, "task-test-123"])
    fetched = store.get(spec_id)
    assert "task-test-123" in fetched.linked_plan_ids


def test_spec_score_smoke(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Score Test"])
    from agent_baton.core.specs.store import SpecStore
    store = SpecStore(db_path=db)
    specs = store.list()
    spec_id = specs[0].spec_id
    _run_cli([
        "spec", "--db", str(db), "score", spec_id,
        "--scorecard", '{"clarity": 0.9}'
    ])
    fetched = store.get(spec_id)
    assert fetched.score()["clarity"] == pytest.approx(0.9)


def test_spec_export_import_cli(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _run_cli(["spec", "--db", str(db), "create", "--title", "Export CLI"])
    from agent_baton.core.specs.store import SpecStore
    store = SpecStore(db_path=db)
    spec_id = store.list()[0].spec_id
    out_file = tmp_path / "exported.json"
    _run_cli(["spec", "--db", str(db), "export", spec_id, "--out", str(out_file)])
    assert out_file.exists()
    # Import into a fresh DB (use a sub-dir so test.db filename doesn't collide)
    import_dir = tmp_path / "import_target"
    import_dir.mkdir(parents=True, exist_ok=True)
    db2 = _make_db(import_dir)
    _run_cli(["spec", "--db", str(db2), "import", str(out_file)])
    store2 = SpecStore(db_path=db2)
    imported = store2.get(spec_id)
    assert imported is not None
    assert imported.title == "Export CLI"
