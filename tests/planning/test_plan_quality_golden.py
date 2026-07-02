"""Snapshot-backed golden coverage for representative planner outputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner


SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "snapshots" / "plans"


def _agent_base(agent_name: str) -> str:
    return (agent_name or "").split("--")[0]


def _normalize_member(member: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "member_id": member.member_id,
        "agent": _agent_base(member.agent_name),
        "role": member.role,
    }
    if member.depends_on:
        data["depends_on"] = list(member.depends_on)
    if member.sub_team:
        data["sub_team"] = [_normalize_member(m) for m in member.sub_team]
    return data


def _normalize_plan(plan: Any) -> dict[str, Any]:
    return {
        "task_type": plan.task_type,
        "complexity": plan.complexity,
        "risk_level": plan.risk_level,
        "budget_tier": plan.budget_tier,
        "phases": [
            {
                "phase_id": phase.phase_id,
                "name": phase.name,
                "approval_required": phase.approval_required,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "agent": _agent_base(step.agent_name),
                        "team": [_normalize_member(m) for m in step.team],
                        "depends_on": list(step.depends_on),
                        "step_type": step.step_type,
                    }
                    for step in phase.steps
                ],
            }
            for phase in plan.phases
        ],
    }


def _load_snapshot(case_id: str) -> dict[str, Any]:
    path = SNAPSHOT_DIR / f"{case_id}.json"
    assert path.exists(), f"Missing golden plan snapshot: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


GOLDEN_CASES = [
    pytest.param(
        "direct-light",
        "Fix typo in README copy",
        {"complexity": "light"},
        id="direct-light",
    ),
    pytest.param(
        "investigative-bug",
        "Investigate intermittent timeout in API requests and identify the likely root cause",
        {"task_type": "bugfix"},
        id="investigative-bug",
    ),
    pytest.param(
        "compound-multi-concern",
        "Update the backend API, frontend dashboard, and test coverage for account settings",
        {},
        id="compound-multi-concern",
    ),
    pytest.param(
        "high-risk-security",
        "Refactor authentication and payment authorization logic",
        {},
        id="high-risk-security",
    ),
    pytest.param(
        "compliance-audit",
        "Ensure GDPR compliance for the user data export workflow",
        {},
        id="compliance-audit",
    ),
    pytest.param(
        "knowledge-heavy",
        "Use project security knowledge to update JWT authentication guidance and tests",
        {"explicit_knowledge_packs": ["security-pack"]},
        id="knowledge-heavy",
    ),
]


@pytest.mark.parametrize(("case_id", "prompt", "kwargs"), GOLDEN_CASES)
def test_representative_plan_shapes_match_golden_snapshots(
    case_id: str, prompt: str, kwargs: dict[str, Any]
) -> None:
    plan = IntelligentPlanner().create_plan(prompt, **kwargs)

    assert _normalize_plan(plan) == _load_snapshot(case_id)
