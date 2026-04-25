"""Tests for the Merge-Readiness Pack (MRP) builder."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Iterator

import pytest

from agent_baton.core.release.mrp import (
    MergeReadinessPack,
    MRPBuilder,
    REVIEWER_CHECKLIST,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(
        cmd, cwd=str(cwd), check=True, capture_output=True, text=True
    )
    return res.stdout


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Build a fake project directory with .claude/team-context layout."""
    root = tmp_path / "proj"
    (root / ".claude" / "team-context" / "executions" / "task-x").mkdir(
        parents=True
    )
    return root


@pytest.fixture
def git_project(project: Path) -> Path:
    """Initialise *project* as a git repo with two commits on a branch."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "Jane Dev", "GIT_AUTHOR_EMAIL": "j@e",
           "GIT_COMMITTER_NAME": "Jane Dev", "GIT_COMMITTER_EMAIL": "j@e"}
    subprocess.run(["git", "init", "-q", "-b", "master", str(project)],
                   check=True, env=env)
    # Identity for the repo (avoids global config dependence).
    subprocess.run(["git", "-C", str(project), "config", "user.email", "j@e"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "Jane Dev"], check=True)
    # Initial commit on master.
    (project / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "init"], check=True)
    # Branch + change.
    subprocess.run(["git", "-C", str(project), "checkout", "-q", "-b", "feature/x"], check=True)
    (project / "src.py").write_text("print('a')\n")
    subprocess.run(["git", "-C", str(project), "add", "src.py"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-q", "-m", "add src"], check=True)
    return project


def _init_db(project: Path, *, task_id: str = "task-x") -> Path:
    """Create a minimal baton.db with plan + step_results + gate_results + beads."""
    db = project / ".claude" / "team-context" / "baton.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE active_task (id INTEGER PRIMARY KEY, task_id TEXT NOT NULL);
    CREATE TABLE plans (
        task_id TEXT PRIMARY KEY,
        task_summary TEXT,
        risk_level TEXT,
        budget_tier TEXT
    );
    CREATE TABLE plan_phases (
        task_id TEXT, phase_id INTEGER, name TEXT
    );
    CREATE TABLE plan_steps (
        task_id TEXT, step_id TEXT, phase_id INTEGER, agent_name TEXT
    );
    CREATE TABLE step_results (
        task_id TEXT,
        step_id TEXT,
        agent_name TEXT,
        status TEXT,
        outcome TEXT,
        estimated_tokens INTEGER DEFAULT 0,
        model_id TEXT DEFAULT '',
        duration_seconds REAL DEFAULT 0,
        completed_at TEXT DEFAULT ''
    );
    CREATE TABLE gate_results (
        task_id TEXT,
        phase_id INTEGER,
        gate_type TEXT,
        passed INTEGER,
        output TEXT DEFAULT '',
        command TEXT DEFAULT ''
    );
    CREATE TABLE beads (
        bead_id TEXT PRIMARY KEY,
        task_id TEXT,
        step_id TEXT,
        agent_name TEXT,
        bead_type TEXT,
        content TEXT,
        confidence TEXT DEFAULT 'medium',
        scope TEXT DEFAULT 'step',
        tags TEXT DEFAULT '[]',
        affected_files TEXT DEFAULT '[]',
        status TEXT DEFAULT 'open',
        created_at TEXT,
        closed_at TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        links TEXT DEFAULT '[]',
        source TEXT DEFAULT 'agent-signal',
        token_estimate INTEGER DEFAULT 0
    );
    CREATE TABLE bead_tags (bead_id TEXT, tag TEXT);
    """)
    conn.execute(
        "INSERT INTO active_task (id, task_id) VALUES (1, ?)", (task_id,)
    )
    conn.execute(
        "INSERT INTO plans (task_id, task_summary, risk_level, budget_tier) "
        "VALUES (?, ?, ?, ?)",
        (task_id, "Build the doohickey", "MEDIUM", "standard"),
    )
    conn.executemany(
        "INSERT INTO plan_phases (task_id, phase_id, name) VALUES (?, ?, ?)",
        [(task_id, 0, "Plan"), (task_id, 1, "Implement")],
    )
    conn.executemany(
        "INSERT INTO plan_steps (task_id, step_id, phase_id, agent_name) "
        "VALUES (?, ?, ?, ?)",
        [
            (task_id, "s0", 0, "architect"),
            (task_id, "s1", 1, "backend-engineer"),
            (task_id, "s2", 1, "test-engineer"),
        ],
    )
    conn.executemany(
        "INSERT INTO step_results "
        "(task_id, step_id, agent_name, status, outcome, estimated_tokens, "
        " model_id, duration_seconds, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (task_id, "s0", "architect", "complete", "designed", 1500,
             "claude-sonnet-4-5", 12.0, "2026-04-25T10:00:00Z"),
            (task_id, "s1", "backend-engineer", "complete", "shipped", 4200,
             "claude-sonnet-4-5", 95.5, "2026-04-25T10:30:00Z"),
        ],
    )
    conn.execute(
        "INSERT INTO gate_results (task_id, phase_id, gate_type, passed, output, command) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, 1, "test", 1, "12 passed", "pytest -x"),
    )
    # Two beads: one general discovery, one open follow-up.
    conn.executemany(
        "INSERT INTO beads "
        "(bead_id, task_id, step_id, agent_name, bead_type, content, status, "
        " created_at, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("bd-aaaa", task_id, "s1", "backend-engineer", "discovery",
             "found a fast path", "closed", "2026-04-25T10:10:00Z",
             "found a fast path"),
            ("bd-bbbb", task_id, "s1", "backend-engineer", "warning",
             "needs follow-up", "open", "2026-04-25T10:20:00Z",
             "needs follow-up"),
        ],
    )
    conn.execute("INSERT INTO bead_tags (bead_id, tag) VALUES (?, ?)",
                 ("bd-bbbb", "follow-up"))
    conn.commit()
    conn.close()
    return db


def _write_plan_md(project: Path, task_id: str = "task-x") -> Path:
    plan = (
        project
        / ".claude"
        / "team-context"
        / "executions"
        / task_id
        / "plan.md"
    )
    plan.write_text(
        "# Plan\n\nBuild the doohickey reliably.\n\n"
        "- **Risk**: MEDIUM\n"
        "- **Budget**: standard\n\n"
        "## Phase 0\n- Step s0\n\n## Phase 1\n- Step s1\n- Step s2\n",
        encoding="utf-8",
    )
    return plan


def _write_compliance_log(project: Path) -> Path:
    """Write a hash-chained compliance-audit.jsonl using stdlib only.

    The chain shape mirrors agent_baton.core.govern.compliance:
      - prev_hash field (genesis = 64 zero bytes)
      - entry_hash field = sha256(prev_hash + canonical_json(payload))
        where canonical_json excludes ``prev_hash`` and ``entry_hash``.
    """
    import hashlib
    log = project / ".claude" / "team-context" / "compliance-audit.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    payloads = [
        {"kind": "dispatch", "agent": "backend-engineer"},
        {"kind": "override", "agent": "auditor", "reason": "force"},
    ]
    prev_hash = "0" * 64
    lines: list[str] = []
    for payload in payloads:
        entry = {**payload, "prev_hash": prev_hash}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        entry_hash = hashlib.sha256(
            (prev_hash + canonical).encode("utf-8")
        ).hexdigest()
        entry["entry_hash"] = entry_hash
        lines.append(json.dumps(entry, sort_keys=True))
        prev_hash = entry_hash
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mrp_builds_from_stub_plan_and_step_results(project: Path) -> None:
    """A bare project with plan + step_results yields a populated pack."""
    _init_db(project)
    _write_plan_md(project)

    pack = MRPBuilder(project).build(branch="feature/x", base="master")

    assert isinstance(pack, MergeReadinessPack)
    assert pack.plan.task_summary == "Build the doohickey"
    assert pack.plan.risk_tier == "MEDIUM"
    assert pack.plan.budget_tier == "standard"
    assert pack.plan.phase_count == 2
    assert pack.plan.step_count == 3

    # Two step results were recorded.
    assert len(pack.steps) == 2
    assert pack.steps[0].step_id == "s0"
    assert pack.steps[0].agent == "architect"
    assert pack.steps[1].agent == "backend-engineer"
    # Phase mapping resolves via plan_steps.
    assert pack.steps[0].phase_id == 0
    assert pack.steps[1].phase_id == 1
    # Tokens accumulate.
    assert pack.total_tokens == 5700
    # Cost is non-negative; > 0 when cost_estimator is importable.
    assert pack.total_cost_usd >= 0.0

    # Gate row is present.
    assert len(pack.gates) == 1
    assert pack.gates[0].passed is True
    assert pack.gates[0].command == "pytest -x"


def test_markdown_output_contains_all_nine_sections(project: Path) -> None:
    """to_markdown() emits all nine titled sections."""
    _init_db(project)
    _write_plan_md(project)
    _write_compliance_log(project)

    md = MRPBuilder(project).build(branch="feature/x", base="master").to_markdown()

    expected_headings = [
        "## 1. Header",
        "## 2. Plan Summary",
        "## 3. Execution Trace",
        "## 4. Gates Run",
        "## 5. Beads Filed",
        "## 6. Compliance Summary",
        "## 7. Outstanding Follow-ups",
        "## 8. Reviewer Checklist",
        "## 9. Diff Stats",
    ]
    for h in expected_headings:
        assert h in md, f"missing section header: {h}\n---\n{md}"


def test_reviewer_checklist_items_match_expected_text(project: Path) -> None:
    """Every checklist line from REVIEWER_CHECKLIST is rendered as a [ ] item."""
    md = MRPBuilder(project).build(branch="feature/x", base="master").to_markdown()
    for item in REVIEWER_CHECKLIST:
        assert f"- [ ] {item}" in md, f"missing checklist item: {item}"
    # Sanity: the canonical list is six items long.
    assert len(REVIEWER_CHECKLIST) == 6


def test_diff_stats_section_pulls_from_git(git_project: Path) -> None:
    """The diff stats section captures git diff --stat output."""
    pack = MRPBuilder(git_project).build(branch="feature/x", base="master")
    assert "src.py" in pack.diff_stats
    md = pack.to_markdown()
    assert "src.py" in md
    # Header gets author + commit count from git.
    assert pack.header.commit_count == 1
    assert "Jane Dev" in pack.header.authors


def test_hash_anchor_matches_chain_head(project: Path) -> None:
    """Compliance chain head hash equals the entry_hash on the last log row."""
    _init_db(project)
    log = _write_compliance_log(project)

    # The last line's entry_hash is the chain head.
    last_entry: dict = {}
    for raw in log.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            last_entry = json.loads(raw)
    expected_head = last_entry["entry_hash"]

    pack = MRPBuilder(project).build(branch="feature/x", base="master")

    # MRP reports a 16-char prefix of the head.
    assert pack.compliance.chain_head_hash == expected_head[:16]
    assert pack.compliance.chain_intact is True
    # Override entry was counted.
    assert pack.compliance.override_count == 1


def test_open_follow_up_beads_listed_separately(project: Path) -> None:
    """Beads tagged ``follow-up`` and still ``open`` appear in section 7."""
    _init_db(project)
    pack = MRPBuilder(project).build(branch="feature/x", base="master")

    follow_up_ids = {b.bead_id for b in pack.follow_ups}
    assert "bd-bbbb" in follow_up_ids
    assert "bd-aaaa" not in follow_up_ids
    # All beads also appear in section 5.
    all_ids = {b.bead_id for b in pack.beads}
    assert {"bd-aaaa", "bd-bbbb"} <= all_ids


def test_missing_inputs_render_unavailable_messages(tmp_path: Path) -> None:
    """A bare directory with no plan / db / git still produces a valid pack."""
    pack = MRPBuilder(tmp_path).build(branch="x", base="master")
    md = pack.to_markdown()
    assert "(no plan.md found)" in md
    assert "(no step results recorded)" in md
    assert "(no compliance-audit.jsonl found)" in md
