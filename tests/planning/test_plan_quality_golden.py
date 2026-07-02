"""Stable golden-shape coverage for representative planner outputs."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner


def _phase_names(plan) -> list[str]:
    return [phase.name for phase in plan.phases]


def _agent_bases_for_phase(phase) -> set[str]:
    agents: set[str] = set()
    for step in phase.steps:
        if step.agent_name:
            agents.add(step.agent_name.split("--")[0])
        for member in step.team:
            if member.agent_name:
                agents.add(member.agent_name.split("--")[0])
    agents.discard("team")
    agents.discard("team-lead")
    return agents


def _phase_agents(plan, phase_name: str) -> set[str]:
    for phase in plan.phases:
        if phase.name.lower() == phase_name.lower():
            return _agent_bases_for_phase(phase)
    return set()


def _assert_plan_has_no_empty_phases(plan) -> None:
    assert plan.phases, "golden plan must contain at least one phase"
    empty = [phase.name for phase in plan.phases if not phase.steps]
    assert empty == []


GOLDEN_CASES = [
    pytest.param(
        "Fix typo in README copy",
        {"complexity": "light"},
        {"phases": {"Implement"}, "risk": "LOW"},
        id="direct-light",
    ),
    pytest.param(
        "Investigate intermittent timeout in API requests and identify the likely root cause",
        {"task_type": "bugfix"},
        {"phases": {"Design", "Implement", "Test", "Review"}},
        id="investigative-bug",
    ),
    pytest.param(
        "Update the backend API, frontend dashboard, and test coverage for account settings",
        {},
        {"phases": {"Implement", "Review"}},
        id="compound-multi-concern",
    ),
    pytest.param(
        "Refactor authentication and payment authorization logic",
        {},
        {"phases": {"Review"}, "risk": "HIGH", "review_agents": {"code-reviewer"}},
        id="high-risk-security",
    ),
    pytest.param(
        "Ensure GDPR compliance for the user data export workflow",
        {},
        {
            "phases": {"Review", "Audit"},
            "risk": "HIGH",
            "review_agents": {"code-reviewer"},
            "audit_agents": {"auditor"},
        },
        id="compliance-audit",
    ),
    pytest.param(
        "Use project security knowledge to update JWT authentication guidance and tests",
        {"explicit_knowledge_packs": ["security-pack"]},
        {"phases": {"Implement", "Review"}, "risk": "HIGH"},
        id="knowledge-heavy",
    ),
]


@pytest.mark.parametrize(("prompt", "kwargs", "expected"), GOLDEN_CASES)
def test_representative_plan_shapes_are_stable(prompt, kwargs, expected) -> None:
    plan = IntelligentPlanner().create_plan(prompt, **kwargs)

    _assert_plan_has_no_empty_phases(plan)
    phase_names = set(_phase_names(plan))
    assert expected["phases"] <= phase_names

    if "risk" in expected:
        assert plan.risk_level == expected["risk"]
    if "review_agents" in expected:
        assert expected["review_agents"] <= _phase_agents(plan, "Review")
    if "audit_agents" in expected:
        assert expected["audit_agents"] <= _phase_agents(plan, "Audit")
