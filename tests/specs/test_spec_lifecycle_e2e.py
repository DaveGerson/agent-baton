"""End-to-end lifecycle test for F0.1 Spec entity.

Exercises the full create -> list -> approve -> link -> score round-trip
through the CLI surface against a real SQLite store.  This covers the
strategic-spec acceptance criterion that the four lifecycle states + plan
linking + scoring all flow through one integrated path.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _make_db(tmp_path: Path) -> Path:
    """Build a project-flavoured SQLite DB with the v16 specs tables."""
    db_path = tmp_path / "central.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS specs (
            spec_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT 'default',
            author_id TEXT NOT NULL DEFAULT 'local-user',
            task_type TEXT NOT NULL DEFAULT '',
            template_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'draft',
            content TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            score_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            approved_at TEXT NOT NULL DEFAULT '',
            approved_by TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS spec_plan_links (
            spec_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            linked_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (spec_id, task_id)
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def test_e2e_create_list_approve_link_score_roundtrip(
    tmp_path: Path, capsys
) -> None:
    """Full CLI lifecycle: create -> list -> approve -> link -> score -> show.

    Verifies the CLI commands cooperate against a single store and that
    state, scorecard and plan link survive each transition.
    """
    db = _make_db(tmp_path)

    # 1. Create
    rc = _run_cli(["spec", "--db", str(db), "create",
                   "--title", "E2E Lifecycle Spec",
                   "--task-type", "feature"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Created spec " in out
    spec_id = out.split("Created spec ")[1].split("\n")[0].strip()

    # 2. List (should include our spec, in draft state)
    _run_cli(["spec", "--db", str(db), "list", "--json"])
    listed = json.loads(capsys.readouterr().out)
    matched = [s for s in listed if s["spec_id"] == spec_id]
    assert len(matched) == 1
    assert matched[0]["state"] == "draft"

    # 3. Approve (draft -> approved)
    rc = _run_cli(["spec", "--db", str(db), "approve", spec_id])
    assert rc == 0
    assert "Approved" in capsys.readouterr().out

    # 4. Link to a plan task id
    rc = _run_cli(["spec", "--db", str(db), "link", spec_id, "task-e2e-001"])
    assert rc == 0
    capsys.readouterr()

    # 5. Score
    rc = _run_cli(["spec", "--db", str(db), "score", spec_id,
                   "--scorecard", '{"clarity": 0.92, "feasibility": 0.85}'])
    assert rc == 0
    capsys.readouterr()

    # 6. Show — must reflect approved state, link, and scorecard
    from agent_baton.core.specs.store import SpecStore
    fetched = SpecStore(db_path=db).get(spec_id)
    assert fetched is not None
    assert fetched.state == "approved"
    assert fetched.approved_at != ""
    assert "task-e2e-001" in fetched.linked_plan_ids
    score = fetched.score()
    assert score["clarity"] == pytest.approx(0.92)
    assert score["feasibility"] == pytest.approx(0.85)


def test_e2e_invalid_state_transition_is_rejected(tmp_path: Path) -> None:
    """approved -> draft should be rejected as an invalid transition."""
    from agent_baton.core.specs.store import SpecStore

    db = _make_db(tmp_path)
    store = SpecStore(db_path=db)
    spec = store.create(title="Bad Transition Test")
    store.update_state(spec.spec_id, "approved")
    with pytest.raises(ValueError, match="Cannot transition"):
        store.update_state(spec.spec_id, "draft")


def test_e2e_archived_terminal_state(tmp_path: Path) -> None:
    """archived is a terminal state; no transitions out are permitted."""
    from agent_baton.core.specs.store import SpecStore

    db = _make_db(tmp_path)
    store = SpecStore(db_path=db)
    spec = store.create(title="Archive Me")
    store.update_state(spec.spec_id, "archived")
    for target in ("draft", "reviewed", "approved", "executing", "completed"):
        with pytest.raises(ValueError):
            store.update_state(spec.spec_id, target)


def test_e2e_content_hash_changes_when_content_updated(tmp_path: Path) -> None:
    """content_hash must change when content body changes — required for
    tamper-evidence parity with the strategic spec's audit story."""
    from agent_baton.core.specs.store import SpecStore

    db = _make_db(tmp_path)
    store = SpecStore(db_path=db)
    spec = store.create(title="Hash Test", content="original body")
    h1 = spec.content_hash
    assert h1 != ""
    updated = store.update_content(spec.spec_id, "modified body")
    assert updated.content_hash != h1
    assert updated.content_hash != ""


def test_e2e_templates_directory_ships_seven_yaml_files() -> None:
    """The strategic spec promises 7 spec templates ship under templates/specs/.

    feature, bug-fix, refactor, migration, briefing-script, mentor-script,
    loop-script — verify all are present so the planner template loader
    has the full set.
    """
    repo_root = Path(__file__).resolve().parents[2]
    tmpl_dir = repo_root / "templates" / "specs"
    assert tmpl_dir.is_dir(), f"missing template dir: {tmpl_dir}"
    expected = {
        "feature.yaml",
        "bug-fix.yaml",
        "refactor.yaml",
        "migration.yaml",
        "briefing-script.yaml",
        "mentor-script.yaml",
        "loop-script.yaml",
    }
    present = {p.name for p in tmpl_dir.glob("*.yaml")}
    missing = expected - present
    assert not missing, f"missing templates: {missing}"
