"""Tests for :mod:`agent_baton.models.manager` (M1 — PMO domain models).

See docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 2 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §10.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from agent_baton.models.manager import (
    ContextBundle,
    ContextReference,
    KnowledgePackReference,
    KnowledgePlan,
    ManagerDecision,
    MissingKnowledgePack,
    ProjectCharter,
    RoleCard,
    ScopeContract,
    ScopeMap,
    TeamBlueprint,
    Workstream,
)


def _round_trip(instance: Any) -> None:
    """construct -> to_dict() -> json.dumps -> from_dict -> equality."""
    d = instance.to_dict()
    serialized = json.dumps(d)  # must be JSON-serializable
    reloaded_dict = json.loads(serialized)
    reloaded = type(instance).from_dict(reloaded_dict)
    assert reloaded == instance


def test_project_charter_round_trip() -> None:
    charter = ProjectCharter(
        task_id="task-1",
        title="Reporting endpoint",
        objective="Add a reporting endpoint with tests and docs.",
        background="Medium complexity feature task.",
        in_scope=["Design", "Implementation"],
        out_of_scope=["Unrelated refactors"],
        assumptions=["API is REST"],
        constraints=["No schema migration"],
        risks=["Low risk"],
        manager_decision_points=["Confirm endpoint auth model"],
        success_criteria=["pytest passes"],
        likely_repo_areas=["app/reporting"],
    )
    _round_trip(charter)


def test_charter_requires_task_id_and_objective() -> None:
    with pytest.raises(ValidationError):
        ProjectCharter()  # missing task_id and objective

    with pytest.raises(ValidationError):
        ProjectCharter(task_id="task-1")  # missing objective

    with pytest.raises(ValidationError):
        ProjectCharter(objective="Do the thing")  # missing task_id

    # Both present: constructs fine.
    ProjectCharter(task_id="task-1", objective="Do the thing")


def test_workstream_round_trip() -> None:
    ws = Workstream(
        id="ws-1",
        name="Backend",
        objective="Implement the endpoint",
        likely_paths=["app/reporting"],
        allowed_paths=["app/reporting/**"],
        owner_role="backend-engineer",
        dependencies=[],
        deliverables=["endpoint handler"],
        risks=["schema drift"],
    )
    _round_trip(ws)


def test_scope_map_round_trip() -> None:
    scope_map = ScopeMap(
        task_id="task-1",
        workstreams=[
            Workstream(id="ws-1", name="Backend", owner_role="backend-engineer"),
            Workstream(id="ws-2", name="Testing", owner_role="test-engineer"),
        ],
        cross_cutting_concerns=["logging"],
        out_of_scope=["Repo areas outside the scope map"],
        scope_expansion_policy="queue_for_manager",
    )
    _round_trip(scope_map)


def test_role_card_round_trip() -> None:
    card = RoleCard(
        role="backend-engineer",
        agent_name="backend-engineer",
        mission="Own the reporting endpoint",
        owns=["endpoint handler"],
        does_not_own=["frontend"],
        required_knowledge_packs=["coding-conventions"],
        default_context_budget=12000,
        expected_handoffs=["handoff to test-engineer"],
        escalation_triggers=["scope expansion"],
    )
    _round_trip(card)


def test_team_blueprint_round_trip() -> None:
    blueprint = TeamBlueprint(
        task_id="task-1",
        team_name="Reporting Team",
        mission="Ship the reporting endpoint",
        roles=[
            RoleCard(role="backend-engineer", agent_name="backend-engineer"),
            RoleCard(role="test-engineer", agent_name="test-engineer"),
        ],
        workstream_assignments={"ws-1": "backend-engineer", "ws-2": "test-engineer"},
        collaboration_rules=["hand off via decision log"],
        escalation_triggers=["scope expansion"],
        phase_policies={"adversarial_review": "always"},
    )
    _round_trip(blueprint)


def test_scope_contract_round_trip() -> None:
    contract = ScopeContract(
        step_id="2.1",
        agent_name="backend-engineer",
        workstream_id="ws-1",
        mission="Implement the endpoint handler",
        in_scope=["app/reporting/service.py"],
        out_of_scope=["app/auth"],
        allowed_paths=["app/reporting/**"],
        expected_artifacts=["endpoint handler"],
        definition_of_done=["tests pass", "handoff summary written"],
        escalation_triggers=["scope expansion", "missing knowledge pack"],
    )
    _round_trip(contract)


def test_context_reference_round_trip() -> None:
    ref = ContextReference(
        path="app/reporting/service.py",
        kind="file",
        reason="primary implementation file",
        token_estimate=500,
    )
    _round_trip(ref)


def test_knowledge_pack_reference_round_trip() -> None:
    ref = KnowledgePackReference(
        name="coding-conventions",
        path=".claude/knowledge/coding-conventions",
        reason="config: default_packs",
        confidence="high",
        status="active",
        token_estimate=800,
        documents=["conventions.md"],
    )
    _round_trip(ref)


def test_missing_knowledge_pack_round_trip() -> None:
    missing = MissingKnowledgePack(
        name="repo-architecture",
        reason="config: required_for_code_steps",
        proposed_sources=["docs/architecture.md"],
    )
    _round_trip(missing)


def test_context_bundle_round_trip() -> None:
    bundle = ContextBundle(
        task_id="task-1",
        step_id="2.1",
        agent_name="backend-engineer",
        scope_contract_path="scope-contracts/2_1.md",
        must_read=[ContextReference(path="scope-contracts/2_1.md", kind="doc")],
        reference_only=[ContextReference(path="README.md", kind="doc")],
        knowledge_packs=[KnowledgePackReference(name="coding-conventions")],
        prior_handoffs=["handoffs/phase-1-handoff.md"],
        decisions=["dec-1234abcd"],
        constraints=["no unrelated refactors"],
        token_budget=12000,
        estimated_tokens=4200,
        truncation_warnings=["dropped 2 reference docs"],
    )
    _round_trip(bundle)


def test_knowledge_plan_round_trip() -> None:
    plan = KnowledgePlan(
        task_id="task-1",
        selected_packs=[KnowledgePackReference(name="coding-conventions")],
        missing_packs=[MissingKnowledgePack(name="repo-architecture")],
        stale_packs=["testing-strategy"],
        per_role_packs={"backend-engineer": ["coding-conventions"]},
        per_step_packs={"2.1": ["coding-conventions"]},
    )
    _round_trip(plan)


def test_manager_decision_round_trip() -> None:
    decision = ManagerDecision(
        decision_id="dec-abc12345",
        task_id="task-1",
        decision_type="scope_expansion",
        summary="Agent requested access to app/auth/session.py",
        context="Reporting endpoint needs session metadata",
        options=["allow", "block", "queue"],
        recommended_option="queue",
        created_at="2026-07-02T00:00:00+00:00",
        resolved_at=None,
        resolution=None,
    )
    _round_trip(decision)


def test_manager_decision_requires_decision_type() -> None:
    with pytest.raises(ValidationError):
        ManagerDecision(decision_id="dec-1", task_id="task-1", summary="x")

    with pytest.raises(ValidationError):
        ManagerDecision(
            decision_id="dec-1", task_id="task-1", summary="x", decision_type="bogus"
        )
