"""Planner quality regression tests.

Captures bugs from beads bd-b3e1, bd-0e36, bd-021d, bd-0960, bd-1974.

Each test asserts the *fixed* behavior — they fail against the original
(buggy) planner and pass after the surgical fixes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.planner import (
    IntelligentPlanner,
    _step_type_for_agent,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_planner_governance.py — minimal agent set)
# ---------------------------------------------------------------------------


def _make_agent_dir(tmp_path: Path) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in [
        "backend-engineer",
        "frontend-engineer",
        "architect",
        "test-engineer",
        "code-reviewer",
        "auditor",
        "subject-matter-expert",
    ]:
        content = (
            f"---\nname: {name}\ndescription: {name} specialist.\n"
            f"model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return agents_dir


@pytest.fixture()
def planner(tmp_path: Path) -> IntelligentPlanner:
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    ctx = tmp_path / "team-context"
    ctx.mkdir()
    agents_dir = _make_agent_dir(tmp_path)

    p = IntelligentPlanner(team_context_root=ctx)
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


# ---------------------------------------------------------------------------
# bd-021d — concern-split misparses constraint clauses
# ---------------------------------------------------------------------------


def test_bd021d_concern_split_stops_at_constraint_keyword():
    """Concern-split must not consume a 'Must not ...' constraint sentence
    after the deliverable list as a phantom deliverable.

    Repro from bead: summary ends with 'Must not regress F0.3 hash chain
    or AuditorVerdict VETO behavior.' which previously got consumed as a
    phantom F0.3 deliverable step.
    """
    summary = (
        "Implement governance: G1.1 Spec entity. G1.5 Tenancy guard. "
        "G1.6 Verdict pipeline. G1.7 Hash chain. "
        "Must not regress F0.3 hash chain or AuditorVerdict VETO behavior."
    )
    concerns = IntelligentPlanner._parse_concerns(summary)
    markers = [m for m, _ in concerns]
    # Should detect the four G* concerns but NOT the F0.3 from the constraint.
    assert "F0.3" not in markers, (
        f"Concern split swallowed a constraint clause's F-marker. "
        f"Markers: {markers}"
    )
    assert all(m.startswith("G") for m in markers), (
        f"Expected only G-prefixed concerns, got: {markers}"
    )


def test_bd021d_concern_split_stops_at_do_not_keyword():
    """Lower-case 'do not regress' should also bound the deliverable list."""
    summary = (
        "Implement: F1.1 Login form. F1.2 Session store. F1.3 Audit log. "
        "Do not regress F9.9 legacy auth path."
    )
    concerns = IntelligentPlanner._parse_concerns(summary)
    markers = [m for m, _ in concerns]
    assert "F9.9" not in markers, f"'Do not' constraint leaked: {markers}"


# ---------------------------------------------------------------------------
# bd-b3e1 — Implement-phase steps must use 'developing' step_type
# ---------------------------------------------------------------------------


def test_bdb3e1_implement_phase_step_type_for_architect_overridden():
    """When an architect lands on an Implement phase (rare but possible
    in role-affinity passes), the step_type must reflect the phase, not
    the agent's default 'planning'.
    """
    # Direct unit on _step_type_for_agent with a phase hint.
    # (After fix: function takes optional phase_name kwarg.)
    st = _step_type_for_agent("architect", task_description="Implement: foo",
                              phase_name="Implement")
    assert st == "developing", (
        f"architect on Implement phase should be 'developing', got '{st}'"
    )


def test_bdb3e1_implement_phase_via_assign_agents(planner: IntelligentPlanner):
    """End-to-end: a phase named 'Implement' yields steps with step_type
    'developing' regardless of which agent wins routing.
    """
    from agent_baton.models.execution import PlanPhase

    phases = [PlanPhase(phase_id=1, name="Implement", steps=[])]
    # Force architect to win by being only candidate.
    result = planner._assign_agents_to_phases(
        phases, ["architect"], task_summary="Implement the thing"
    )
    for step in result[0].steps:
        assert step.step_type == "developing", (
            f"Implement-phase step has step_type={step.step_type!r} "
            f"(agent={step.agent_name})"
        )


# ---------------------------------------------------------------------------
# bd-0e36 — architect must not be routed to Implement phase
# ---------------------------------------------------------------------------


def test_bd0e36_implement_phase_prefers_engineer_over_architect(
    planner: IntelligentPlanner,
):
    """When backend-engineer is in the pool, it (not architect) must own
    the Implement phase even if architect is listed first."""
    from agent_baton.models.execution import PlanPhase

    phases = [
        PlanPhase(phase_id=1, name="Design", steps=[]),
        PlanPhase(phase_id=2, name="Implement", steps=[]),
    ]
    result = planner._assign_agents_to_phases(
        phases,
        ["architect", "backend-engineer"],
        task_summary="Build a Python service",
    )
    impl_phase = next(p for p in result if p.name == "Implement")
    impl_agents = [s.agent_name.split("--")[0] for s in impl_phase.steps]
    assert "architect" not in impl_agents, (
        f"architect leaked into Implement phase: {impl_agents}"
    )
    assert "backend-engineer" in impl_agents, (
        f"backend-engineer missing from Implement: {impl_agents}"
    )


def test_bd0e36_concern_split_skips_architect(planner: IntelligentPlanner):
    """_pick_agent_for_concern must filter architect-class agents the same
    way it filters reviewers."""
    candidates = ["architect", "backend-engineer", "frontend-engineer"]
    chosen = planner._pick_agent_for_concern(
        "Add an API endpoint that writes to the database",
        candidates,
    )
    assert chosen.split("--")[0] != "architect", (
        f"_pick_agent_for_concern returned architect: {chosen}"
    )


# ---------------------------------------------------------------------------
# bd-0960 — context_files must reject parse artifacts
# ---------------------------------------------------------------------------


def test_bd0960_extract_file_paths_rejects_parse_artifacts():
    """Slash-separated phrases that aren't real paths (e.g.
    'required_role/timeout_minutes/') must be filtered out."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    p = IntelligentPlanner.__new__(IntelligentPlanner)  # bare instance
    text = (
        "Add fields required_role/timeout_minutes/ to the schema. "
        "Also touch agent_baton/core/engine/planner.py and docs/spec.md."
    )
    paths = p._extract_file_paths(text)
    # The legitimate paths should be present.
    assert "agent_baton/core/engine/planner.py" in paths
    assert "docs/spec.md" in paths
    # The parse artifact must not be present.
    assert "required_role/timeout_minutes/" not in paths, (
        f"Parse artifact leaked into context_files: {paths}"
    )
    for path in paths:
        assert not path.endswith("/"), (
            f"Trailing-slash phrase leaked as path: {path!r}"
        )


def test_bd0960_extract_file_paths_accepts_known_extensions():
    """Files with known code/config extensions must still be extracted
    even without a slash (no regression)."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    p = IntelligentPlanner.__new__(IntelligentPlanner)
    paths = p._extract_file_paths("Edit pyproject.toml and CHANGELOG.md.")
    assert "pyproject.toml" in paths
    assert "CHANGELOG.md" in paths


# ---------------------------------------------------------------------------
# bd-1974 — Review-phase routing must prefer code-reviewer
# ---------------------------------------------------------------------------


def test_bd1974_review_phase_prefers_code_reviewer(planner: IntelligentPlanner):
    """A 'Review' phase must be assigned to code-reviewer (not
    backend-engineer) when code-reviewer is in the pool."""
    from agent_baton.models.execution import PlanPhase

    phases = [
        PlanPhase(phase_id=1, name="Implement", steps=[]),
        PlanPhase(phase_id=2, name="Review", steps=[]),
    ]
    result = planner._assign_agents_to_phases(
        phases,
        ["backend-engineer", "code-reviewer"],
        task_summary="Refactor the API layer",
    )
    review_phase = next(p for p in result if p.name == "Review")
    review_agents = [s.agent_name.split("--")[0] for s in review_phase.steps]
    assert "code-reviewer" in review_agents, (
        f"code-reviewer missing from Review phase: {review_agents}"
    )
    assert "backend-engineer" not in review_agents, (
        f"backend-engineer leaked into Review phase: {review_agents}"
    )
