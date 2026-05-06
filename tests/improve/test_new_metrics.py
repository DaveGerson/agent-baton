"""Tests for H3.5 new performance metrics (bd-0dea)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.cli.commands.improve.metrics_cmd import (
    handler as metrics_handler,
    register as metrics_register,
)
from agent_baton.core.improve.new_metrics import (
    AgentROI,
    DocContribution,
    ReviewerStats,
    SpecAuthorStats,
    SpecEffectivenessReport,
    compute_all_metrics,
    compute_delegation_roi,
    compute_knowledge_contribution,
    compute_review_quality,
    compute_spec_effectiveness,
    to_json,
    to_jsonable,
)
from agent_baton.core.storage import get_project_storage


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _init_db(project_root: Path) -> Path:
    """Create the team-context dir + baton.db with the project schema."""
    ctx = project_root / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    storage = get_project_storage(ctx)
    # Force schema materialisation by opening (and immediately closing) a
    # connection — ConnectionManager applies the DDL lazily on first use.
    storage._conn_mgr.get_connection()
    db_path = storage.db_path
    storage.close()
    return db_path


def _insert_execution(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    conn.execute(
        """
        INSERT INTO executions(task_id, status, started_at)
        VALUES (?, ?, '2026-04-01T00:00:00Z')
        """,
        (task_id, status),
    )


def _insert_spec(
    conn: sqlite3.Connection,
    spec_id: str,
    author: str,
    project_id: str = "default",
) -> None:
    conn.execute(
        """
        INSERT INTO specs(spec_id, project_id, author_id, title)
        VALUES (?, ?, ?, ?)
        """,
        (spec_id, project_id, author, f"title-{spec_id}"),
    )


def _link_spec(
    conn: sqlite3.Connection,
    spec_id: str,
    task_id: str,
    linked_at: str = "2026-04-10T00:00:00Z",
    project_id: str = "default",
) -> None:
    conn.execute(
        """
        INSERT INTO spec_plan_links(spec_id, task_id, project_id, linked_at)
        VALUES (?, ?, ?, ?)
        """,
        (spec_id, task_id, project_id, linked_at),
    )


def _insert_step_result(
    conn: sqlite3.Connection,
    task_id: str,
    step_id: str,
    agent_name: str,
    status: str = "complete",
    outcome: str = "",
    retries: int = 0,
    duration_seconds: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO step_results
            (task_id, step_id, agent_name, status, outcome, retries, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, step_id, agent_name, status, outcome, retries, duration_seconds),
    )


def _insert_knowledge_use(
    conn: sqlite3.Connection,
    task_id: str,
    step_id: str,
    pack: str,
    doc: str,
) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_telemetry
            (project_id, doc_name, pack_name, task_id, step_id)
        VALUES ('default', ?, ?, ?, ?)
        """,
        (doc, pack, task_id, step_id),
    )


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """A project root with an empty baton.db (schema only)."""
    _init_db(tmp_path)
    return tmp_path


@pytest.fixture
def seeded_project(tmp_path: Path) -> Path:
    """A project root with a representative seeded baton.db."""
    db_path = _init_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        # Two specs by alice (one shipped first-pass, one needed revisions),
        # one spec by bob (REQUEST_CHANGES), plus an unlinked task.
        _insert_execution(conn, "task-A", "complete")
        _insert_execution(conn, "task-B", "complete")
        _insert_execution(conn, "task-C", "complete")
        _insert_execution(conn, "task-D", "running")  # unrelated

        _insert_spec(conn, "spec-1", "alice")
        _insert_spec(conn, "spec-2", "alice")
        _insert_spec(conn, "spec-3", "bob")

        _link_spec(conn, "spec-1", "task-A", "2026-04-01T00:00:00Z")
        _link_spec(conn, "spec-2", "task-B", "2026-04-05T00:00:00Z")
        _link_spec(conn, "spec-3", "task-C", "2026-04-10T00:00:00Z")

        # task-A: clean APPROVE
        _insert_step_result(
            conn,
            "task-A",
            "s1",
            "backend-engineer",
            outcome="all good",
            retries=0,
            duration_seconds=120.0,
        )
        _insert_step_result(
            conn,
            "task-A",
            "s2",
            "auditor",
            outcome='```json\n{"verdict": "APPROVE", "rationale": "ok"}\n```',
            duration_seconds=300.0,
        )

        # task-B: revised by reviewer (REQUEST_CHANGES) -> NOT first-pass
        _insert_step_result(
            conn,
            "task-B",
            "s1",
            "backend-engineer",
            outcome="initial",
            retries=2,
            duration_seconds=180.0,
        )
        _insert_step_result(
            conn,
            "task-B",
            "s2",
            "code-reviewer",
            outcome=(
                '```json\n{"verdict": "REQUEST_CHANGES", "rationale": "fix it"}\n```'
            ),
            duration_seconds=600.0,
        )

        # task-C: VETO from auditor -> NOT first-pass
        _insert_step_result(
            conn,
            "task-C",
            "s1",
            "backend-engineer",
            status="failed",
            outcome="bad",
            retries=0,
        )
        _insert_step_result(
            conn,
            "task-C",
            "s2",
            "auditor",
            outcome='```json\n{"verdict": "VETO", "rationale": "no"}\n```',
            duration_seconds=420.0,
        )

        # Knowledge attachments:
        # doc-X attached twice; once paired with a complete step (success),
        # once paired with a failed step (no success).
        _insert_knowledge_use(conn, "task-A", "s1", "core", "doc-X")
        _insert_knowledge_use(conn, "task-C", "s1", "core", "doc-X")
        _insert_knowledge_use(conn, "task-A", "s2", "core", "doc-Y")

        conn.commit()
    finally:
        conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# Empty-DB shape tests
# ---------------------------------------------------------------------------


def test_spec_effectiveness_empty(empty_project: Path) -> None:
    rep = compute_spec_effectiveness(project_root=empty_project)
    assert isinstance(rep, SpecEffectivenessReport)
    assert rep.total_specs == 0
    assert rep.complete_first_pass == 0
    assert rep.rate == 0.0
    assert rep.sample_period == (None, None)
    assert rep.per_author == []


def test_delegation_roi_empty(empty_project: Path) -> None:
    rows = compute_delegation_roi(project_root=empty_project)
    assert rows == []


def test_knowledge_contribution_empty(empty_project: Path) -> None:
    rows = compute_knowledge_contribution(project_root=empty_project)
    assert rows == []


def test_review_quality_empty(empty_project: Path) -> None:
    rows = compute_review_quality(project_root=empty_project)
    assert rows == []


# ---------------------------------------------------------------------------
# Seeded compute tests
# ---------------------------------------------------------------------------


def test_spec_effectiveness_seeded(seeded_project: Path) -> None:
    rep = compute_spec_effectiveness(project_root=seeded_project)
    assert rep.total_specs == 3
    # spec-1 → first-pass, spec-2 → REQUEST_CHANGES, spec-3 → VETO
    assert rep.complete_first_pass == 1
    assert rep.rate == pytest.approx(1 / 3)
    assert rep.sample_period[0] is not None
    assert rep.sample_period[1] is not None
    assert rep.sample_period[0] <= rep.sample_period[1]

    by_author = {a.author: a for a in rep.per_author}
    assert set(by_author) == {"alice", "bob"}
    assert by_author["alice"].total_specs == 2
    assert by_author["alice"].complete_first_pass == 1
    assert by_author["alice"].rate == pytest.approx(0.5)
    assert by_author["bob"].total_specs == 1
    assert by_author["bob"].complete_first_pass == 0
    assert by_author["bob"].rate == 0.0


def test_delegation_roi_seeded(seeded_project: Path) -> None:
    rows = compute_delegation_roi(project_root=seeded_project)
    by_agent = {r.agent_name: r for r in rows}

    # backend-engineer: task-A s1 (accepted), task-B s1 (revised), task-C s1 (rejected)
    be = by_agent["backend-engineer"]
    assert be.total_dispatches == 3
    assert be.accepted == 1
    assert be.revised == 1
    assert be.rejected == 1
    # 1*30 - 1*30 - 1*45 = -45
    assert be.roi_minutes == pytest.approx(-45.0)

    # auditor: 2 accepted, no retries, no failures.
    aud = by_agent["auditor"]
    assert aud.accepted == 2
    assert aud.revised == 0
    assert aud.rejected == 0
    assert aud.roi_minutes == pytest.approx(60.0)

    # code-reviewer: 1 accepted (status='complete', retries=0).
    cr = by_agent["code-reviewer"]
    assert cr.accepted == 1
    assert cr.roi_minutes == pytest.approx(30.0)


def test_knowledge_contribution_seeded(seeded_project: Path) -> None:
    rows = compute_knowledge_contribution(project_root=seeded_project)
    by_doc = {(r.pack, r.doc): r for r in rows}

    # doc-X attached twice: task-A/s1 (complete), task-C/s1 (failed) -> 1/2
    x = by_doc[("core", "doc-X")]
    assert x.attachment_count == 2
    assert x.success_count == 1
    assert x.contribution_score == pytest.approx(0.5)

    # doc-Y attached once on a complete step -> 1/1
    y = by_doc[("core", "doc-Y")]
    assert y.attachment_count == 1
    assert y.success_count == 1
    assert y.contribution_score == pytest.approx(1.0)


def test_review_quality_seeded(seeded_project: Path) -> None:
    rows = compute_review_quality(project_root=seeded_project)
    by_rev = {r.reviewer: r for r in rows}

    # auditor: 1 APPROVE + 1 BLOCK (VETO).
    aud = by_rev["auditor"]
    assert aud.verdicts["APPROVE"] == 1
    assert aud.verdicts["BLOCK"] == 1
    assert aud.verdicts["FLAG"] == 0
    assert aud.approve_rate == pytest.approx(0.5)
    assert aud.block_rate == pytest.approx(0.5)
    # avg minutes = (300 + 420) / 2 / 60 = 6.0
    assert aud.avg_minutes == pytest.approx(6.0)

    # code-reviewer: 1 FLAG (REQUEST_CHANGES).
    cr = by_rev["code-reviewer"]
    assert cr.verdicts["FLAG"] == 1
    assert cr.verdicts["APPROVE"] == 0
    assert cr.verdicts["BLOCK"] == 0
    assert cr.approve_rate == 0.0
    assert cr.block_rate == 0.0
    assert cr.avg_minutes == pytest.approx(10.0)  # 600/60


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def test_json_round_trip_spec_effectiveness(seeded_project: Path) -> None:
    rep = compute_spec_effectiveness(project_root=seeded_project)
    raw = to_json(rep)
    parsed = json.loads(raw)
    assert parsed["total_specs"] == 3
    assert parsed["complete_first_pass"] == 1
    # sample_period should be ISO date strings (or None).
    period = parsed["sample_period"]
    assert isinstance(period, list) and len(period) == 2
    for entry in period:
        assert entry is None or isinstance(entry, str)
    assert isinstance(parsed["per_author"], list)


def test_to_jsonable_handles_lists(seeded_project: Path) -> None:
    rows = compute_delegation_roi(project_root=seeded_project)
    blob = to_jsonable(rows)
    assert isinstance(blob, list)
    assert all("agent_name" in r for r in blob)
    # Round-trips through json.dumps without raising.
    json.dumps(blob)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _build_args(**kwargs):
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    metrics_register(sub)
    argv = ["metrics", "show"]
    for k, v in kwargs.items():
        argv.append(f"--{k.replace('_', '-')}")
        argv.append(str(v))
    return parser.parse_args(argv)


def test_cli_default_runs_all(seeded_project: Path, capsys) -> None:
    args = _build_args(project_root=seeded_project)
    metrics_handler(args)
    out = capsys.readouterr().out
    assert "Spec effectiveness" in out
    assert "Delegation ROI" in out
    assert "Knowledge contribution" in out
    assert "Review quality" in out


def test_cli_per_metric_flag(seeded_project: Path, capsys) -> None:
    args = _build_args(project_root=seeded_project, metric="delegation_roi")
    metrics_handler(args)
    out = capsys.readouterr().out
    assert "Delegation ROI" in out
    # Should NOT include the other section headers.
    assert "Spec effectiveness" not in out
    assert "Knowledge contribution" not in out
    assert "Review quality" not in out


def test_cli_json_format(seeded_project: Path, capsys) -> None:
    args = _build_args(
        project_root=seeded_project,
        metric="spec_effectiveness",
        format="json",
    )
    metrics_handler(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["total_specs"] == 3


def test_cli_json_full_payload(seeded_project: Path, capsys) -> None:
    args = _build_args(project_root=seeded_project, format="json")
    metrics_handler(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert set(payload) == {
        "spec_effectiveness",
        "delegation_roi",
        "knowledge_contribution",
        "review_quality",
    }
    assert isinstance(payload["delegation_roi"], list)


# ---------------------------------------------------------------------------
# compute_all_metrics convenience
# ---------------------------------------------------------------------------


def test_compute_all_metrics_keys(seeded_project: Path) -> None:
    payload = compute_all_metrics(project_root=seeded_project)
    assert set(payload) == {
        "spec_effectiveness",
        "delegation_roi",
        "knowledge_contribution",
        "review_quality",
    }
    assert isinstance(payload["spec_effectiveness"], SpecEffectivenessReport)
    assert all(isinstance(x, AgentROI) for x in payload["delegation_roi"])
    assert all(
        isinstance(x, DocContribution)
        for x in payload["knowledge_contribution"]
    )
    assert all(isinstance(x, ReviewerStats) for x in payload["review_quality"])
