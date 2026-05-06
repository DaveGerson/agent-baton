"""Shared pytest fixtures for agent_baton tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

import pytest

from agent_baton.core.orchestration.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Sample agent markdown content strings
# ---------------------------------------------------------------------------

BACKEND_PYTHON_CONTENT = """\
---
name: backend-engineer--python
description: Python backend specialist. Knows FastAPI, Django, Flask.
model: sonnet
permissionMode: auto-edit
color: blue
tools: Read, Write, Edit, Glob, Grep, Bash
---

# Backend Engineer — Python

You are a senior Python backend engineer.
"""

ARCHITECT_CONTENT = """\
---
name: architect
description: |
  Specialist for system design and architectural planning. Use for data model
  design, API contract definition, and technology selection.
model: opus
permissionMode: default
color: red
tools: Read, Glob, Grep
---

# Software Architect

You are a senior software architect.
"""

FRONTEND_REACT_CONTENT = """\
---
name: frontend-engineer--react
description: React/TypeScript frontend specialist.
model: sonnet
permissionMode: auto-edit
color: green
tools: Read, Write, Edit, Bash
---

# Frontend Engineer — React

You are a senior React frontend engineer.
"""

SECURITY_REVIEWER_CONTENT = """\
---
name: security-reviewer
description: Security-focused code reviewer.
model: opus
permissionMode: default
tools: Read, Glob, Grep
---

# Security Reviewer

You audit code for security vulnerabilities.
"""

BACKEND_NODE_CONTENT = """\
---
name: backend-engineer--node
description: Node.js backend specialist.
model: sonnet
permissionMode: auto-edit
color: yellow
tools: Read, Write, Edit, Bash
---

# Backend Engineer — Node

You are a senior Node.js backend engineer.
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_agent_content() -> str:
    """Raw string content of a valid agent .md file."""
    return BACKEND_PYTHON_CONTENT


@pytest.fixture
def tmp_agents_dir(tmp_path: Path) -> Path:
    """A tmp directory containing 4-5 sample agent .md files."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    (agents_dir / "backend-engineer--python.md").write_text(
        BACKEND_PYTHON_CONTENT, encoding="utf-8"
    )
    (agents_dir / "architect.md").write_text(
        ARCHITECT_CONTENT, encoding="utf-8"
    )
    (agents_dir / "frontend-engineer--react.md").write_text(
        FRONTEND_REACT_CONTENT, encoding="utf-8"
    )
    (agents_dir / "security-reviewer.md").write_text(
        SECURITY_REVIEWER_CONTENT, encoding="utf-8"
    )
    (agents_dir / "backend-engineer--node.md").write_text(
        BACKEND_NODE_CONTENT, encoding="utf-8"
    )

    return agents_dir


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    """A tmp directory with fake stack-detection marker files."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\n', encoding="utf-8"
    )
    (project / "package.json").write_text(
        '{"name": "myapp"}\n', encoding="utf-8"
    )
    return project


@pytest.fixture
def tmp_team_context(tmp_path: Path) -> Path:
    """A tmp directory to use as the team-context directory root."""
    ctx_dir = tmp_path / "team-context"
    # Deliberately do NOT create the directory — ContextManager.ensure_dir()
    # should create it on first write, which is part of what we test.
    return ctx_dir


@pytest.fixture
def registry_with_agents(tmp_agents_dir: Path) -> AgentRegistry:
    """An AgentRegistry pre-loaded from tmp_agents_dir."""
    registry = AgentRegistry()
    registry.load_directory(tmp_agents_dir)
    return registry


# ---------------------------------------------------------------------------
# 005b followup bundle fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_bead_store(request, monkeypatch) -> Generator[None, None, None]:
    """No-op autouse fixture — retained for backward compatibility with
    tests/test_followup_fixtures.py which exercises the monkeypatch path
    directly.

    bd-9de9 replaced the underlying mechanism: all IntelligentPlanner
    constructions in test_planner_governance.py now pass ``emit_beads=False``
    explicitly, which suppresses planning-bead writes at the constructor level
    without relying on a class-level monkeypatch.  This autouse fixture is
    therefore a no-op but is kept so that existing references in
    TestIsolatedBeadStoreFixture still compile and the conftest API surface
    does not change.
    """
    yield


@pytest.fixture
def bead_store_count_baseline(tmp_path: Path):
    """Return a callable that snapshots + asserts bead row growth.

    Usage in a test::

        def test_no_bead_leak(bead_store_count_baseline):
            check = bead_store_count_baseline()  # capture baseline
            # ... code under test that must NOT write beads ...
            check()   # asserts count did not grow

    The fixture creates an isolated SQLite database in *tmp_path* and
    applies the project schema so that the ``beads`` table is present.
    The returned factory captures the current row count and returns an
    assertion callable.
    """
    from agent_baton.core.engine.bead_store import BeadStore

    db_path = tmp_path / "bead_baseline.db"
    store = BeadStore(db_path)
    # Force the ConnectionManager to apply the schema DDL so the beads table
    # exists before any raw sqlite3.connect() call tries to SELECT from it.
    store._conn()

    def factory():
        """Snapshot the current bead count and return an assertion callable."""
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM beads").fetchone()
            baseline: int = row[0] if row else 0
        finally:
            conn.close()

        def assert_no_growth() -> None:
            conn2 = sqlite3.connect(str(db_path))
            try:
                row2 = conn2.execute("SELECT COUNT(*) FROM beads").fetchone()
                current: int = row2[0] if row2 else 0
            finally:
                conn2.close()
            assert current == baseline, (
                f"BeadStore grew unexpectedly: baseline={baseline} current={current}"
            )

        return assert_no_growth

    return factory


@pytest.fixture
def synthetic_parallel_plan():
    """Factory that returns a MachinePlan ready for parallel-safe annotation tests.

    The plan has one phase (phase 1) containing two sibling steps that:
    - Both depend on step ``"1.1"`` (i.e. ``depends_on=["1.1"]``).
    - Have disjoint ``allowed_paths`` — step 1.2 owns ``agent_baton/foo.py``
      and step 1.3 owns ``agent_baton/bar.py``.

    After calling ``annotate_parallel_safe(plan.phases)`` both steps should
    have ``parallel_safe=True``.

    Returns a zero-argument callable so each test gets a fresh instance::

        def test_something(synthetic_parallel_plan):
            plan = synthetic_parallel_plan()
            annotate_parallel_safe(plan.phases)
            assert plan.phases[0].steps[0].parallel_safe
    """
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

    def _build() -> MachinePlan:
        # Phase 1, step 1 — the common prerequisite (no depends_on).
        prereq = PlanStep(
            step_id="1.1",
            agent_name="architect",
            task_description="Design the interface",
            allowed_paths=["agent_baton/interfaces.py"],
        )
        # Phase 2 sibling A — depends on 1.1, writes to foo.py.
        sibling_a = PlanStep(
            step_id="1.2",
            agent_name="backend-engineer",
            task_description="Implement foo module",
            depends_on=["1.1"],
            allowed_paths=["agent_baton/foo.py"],
        )
        # Phase 2 sibling B — depends on 1.1, writes to bar.py (disjoint).
        sibling_b = PlanStep(
            step_id="1.3",
            agent_name="backend-engineer",
            task_description="Implement bar module",
            depends_on=["1.1"],
            allowed_paths=["agent_baton/bar.py"],
        )
        phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[prereq, sibling_a, sibling_b],
        )
        return MachinePlan(
            task_id="test-synthetic-parallel-plan",
            task_summary="Synthetic plan for parallel-safe annotation tests",
            phases=[phase],
        )

    return _build

