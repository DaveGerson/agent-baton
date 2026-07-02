"""Tests for :mod:`agent_baton.core.manager.team_blueprint` and
:mod:`agent_baton.core.manager.role_cards` (M3 -- TeamBlueprintBuilder +
role cards).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 6 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §16
Milestone 3.

Test inputs are hand-constructed ``MachinePlan``/``ScopeMap`` objects --
the planner pipeline is never invoked.
"""
from __future__ import annotations

import json

from agent_baton.core.config.manager import (
    ManagerConfig,
    PhaseCompletionPolicy,
    PoliciesConfig,
    ProjectCompletionPolicy,
)
from agent_baton.core.manager.role_cards import render_role_card
from agent_baton.core.manager.team_blueprint import TeamBlueprintBuilder
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.manager import RoleCard, ScopeMap, TeamBlueprint, Workstream


def _two_workstream_plan(complexity: str = "medium") -> MachinePlan:
    """Medium-complexity, two-phase plan: backend-engineer then test-engineer.

    Mirrors the scope map built by :func:`_two_workstream_scope_map` --
    one workstream per phase, positionally aligned.
    """
    return MachinePlan(
        task_id="task-blueprint",
        task_summary="Add a reporting endpoint with tests and docs",
        task_type="feature",
        complexity=complexity,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the reporting endpoint",
                        deliverables=["endpoint handler"],
                        step_type="developing",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Test",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Add tests for the reporting endpoint",
                        deliverables=["endpoint tests"],
                        depends_on=["1.1"],
                        step_type="testing",
                    ),
                ],
            ),
        ],
    )


def _two_workstream_scope_map() -> ScopeMap:
    return ScopeMap(
        task_id="task-blueprint",
        workstreams=[
            Workstream(
                id="ws-1",
                name="Implement",
                objective="Implement the reporting endpoint",
                allowed_paths=["app/reporting/**"],
                owner_role="backend-engineer",
                deliverables=["endpoint handler"],
            ),
            Workstream(
                id="ws-2",
                name="Test",
                objective="Add tests for the reporting endpoint",
                allowed_paths=["tests/reporting/**"],
                owner_role="test-engineer",
                dependencies=["ws-1"],
                deliverables=["endpoint tests"],
            ),
        ],
        cross_cutting_concerns=["logging"],
        out_of_scope=["Repo areas outside the scope map"],
    )


def test_blueprint_written_fields_complete() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()

    blueprint, role_cards = TeamBlueprintBuilder(ManagerConfig()).build(scope_map, plan)

    assert isinstance(blueprint, TeamBlueprint)
    assert blueprint.task_id == "task-blueprint"
    assert blueprint.team_name
    assert blueprint.mission
    assert blueprint.roles
    assert blueprint.workstream_assignments
    assert blueprint.collaboration_rules
    assert blueprint.escalation_triggers
    assert blueprint.phase_policies
    assert role_cards  # non-empty dict returned alongside the blueprint


def test_every_workstream_has_owner() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()

    blueprint, _role_cards = TeamBlueprintBuilder(ManagerConfig()).build(scope_map, plan)

    role_names = {card.role for card in blueprint.roles}
    for ws in scope_map.workstreams:
        owner = blueprint.workstream_assignments.get(ws.id)
        assert owner, f"workstream {ws.id} has no owner"
        assert owner in role_names


def test_every_role_has_role_card() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()

    blueprint, role_cards = TeamBlueprintBuilder(ManagerConfig()).build(scope_map, plan)

    role_names = {card.role for card in blueprint.roles}
    assert role_names == set(role_cards.keys())
    for role in role_names:
        assert isinstance(role_cards[role], RoleCard)

    # Implementation roles from the plan and the (default "always")
    # adversarial-review role are all present.
    assert "backend-engineer" in role_names
    assert "test-engineer" in role_names
    assert ManagerConfig().policies.review_agents.adversarial_review in role_names


def test_role_card_required_sections() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()

    _blueprint, role_cards = TeamBlueprintBuilder(ManagerConfig()).build(scope_map, plan)

    for role in ("backend-engineer", "test-engineer"):
        card = role_cards[role]
        assert card.owns
        assert card.does_not_own
        assert card.required_knowledge_packs
        assert card.escalation_triggers

    review_role = ManagerConfig().policies.review_agents.adversarial_review
    review_card = role_cards[review_role]
    assert review_card.owns
    assert review_card.does_not_own
    assert review_card.required_knowledge_packs
    assert review_card.escalation_triggers


def test_prefer_specialists_avoids_single_broad_role() -> None:
    """A medium, 2-workstream plan whose steps all name a generalist agent
    ("claude") must not end up with that one role owning both workstreams
    -- the diversification rule reassigns owners via
    ``planning/rules/phase_roles.PHASE_IDEAL_ROLES``, keyed by the
    workstream's (phase) name.
    """
    plan = MachinePlan(
        task_id="task-specialist",
        task_summary="Design and implement the reporting service",
        complexity="medium",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="claude",
                        task_description="Design the reporting service",
                        deliverables=["design doc"],
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="claude",
                        task_description="Implement the reporting service",
                        deliverables=["service code"],
                    ),
                ],
            ),
        ],
    )
    scope_map = ScopeMap(
        task_id="task-specialist",
        workstreams=[
            Workstream(id="ws-1", name="Design", owner_role="claude", deliverables=["design doc"]),
            Workstream(id="ws-2", name="Implement", owner_role="claude", deliverables=["service code"]),
        ],
    )

    config = ManagerConfig()
    assert config.team.prefer_specialists_over_generalists is True  # precondition

    blueprint, _role_cards = TeamBlueprintBuilder(config).build(scope_map, plan)

    owners = set(blueprint.workstream_assignments.values())
    assert len(owners) >= 2, f"expected diversified owners, got {owners}"


def test_prefer_specialists_off_keeps_single_owner() -> None:
    """Sanity check for the guard: with the rule disabled, the generalist
    keeps owning both workstreams."""
    plan = MachinePlan(
        task_id="task-specialist-off",
        task_summary="Design and implement the reporting service",
        complexity="medium",
        phases=[
            PlanPhase(phase_id=1, name="Design", steps=[
                PlanStep(step_id="1.1", agent_name="claude", task_description="Design"),
            ]),
            PlanPhase(phase_id=2, name="Implement", steps=[
                PlanStep(step_id="2.1", agent_name="claude", task_description="Implement"),
            ]),
        ],
    )
    scope_map = ScopeMap(
        task_id="task-specialist-off",
        workstreams=[
            Workstream(id="ws-1", name="Design", owner_role="claude"),
            Workstream(id="ws-2", name="Implement", owner_role="claude"),
        ],
    )
    config = ManagerConfig(team={"prefer_specialists_over_generalists": False})

    blueprint, _role_cards = TeamBlueprintBuilder(config).build(scope_map, plan)

    owners = set(blueprint.workstream_assignments.values())
    assert owners == {"claude"}


def test_adversarial_always_adds_review_role() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()
    config = ManagerConfig(
        policies=PoliciesConfig(
            phase_completion=PhaseCompletionPolicy(adversarial_review="always"),
        )
    )

    blueprint, _role_cards = TeamBlueprintBuilder(config).build(scope_map, plan)

    role_names = {card.role for card in blueprint.roles}
    assert config.policies.review_agents.adversarial_review in role_names


def test_adversarial_off_no_review_role() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()
    config = ManagerConfig(
        policies=PoliciesConfig(
            phase_completion=PhaseCompletionPolicy(adversarial_review="off"),
            project_completion=ProjectCompletionPolicy(adversarial_review="off"),
        )
    )

    blueprint, _role_cards = TeamBlueprintBuilder(config).build(scope_map, plan)

    role_names = {card.role for card in blueprint.roles}
    assert config.policies.review_agents.adversarial_review not in role_names


def test_blueprint_round_trip() -> None:
    plan = _two_workstream_plan()
    scope_map = _two_workstream_scope_map()

    blueprint, _role_cards = TeamBlueprintBuilder(ManagerConfig()).build(scope_map, plan)

    serialized = json.dumps(blueprint.to_dict())  # must be JSON-serializable
    reloaded = TeamBlueprint.from_dict(json.loads(serialized))
    assert reloaded == blueprint


def test_render_role_card_sections_in_order() -> None:
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

    rendered = render_role_card(card)

    assert rendered.startswith("# Role Card: backend-engineer")
    headers = [
        "## Mission",
        "## Owns",
        "## Does Not Own",
        "## Required Knowledge Packs",
        "## Context Budget",
        "## Escalation Triggers",
        "## Handoff Requirements",
    ]
    positions = [rendered.index(h) for h in headers]
    assert positions == sorted(positions)
    assert "12,000 tokens" in rendered
    assert "- endpoint handler" in rendered
    assert "- handoff to test-engineer" in rendered


def test_render_role_card_empty_lists_render_placeholder() -> None:
    card = RoleCard(role="architect", agent_name="architect", mission="Own design")

    rendered = render_role_card(card)

    assert "- (none)" in rendered
