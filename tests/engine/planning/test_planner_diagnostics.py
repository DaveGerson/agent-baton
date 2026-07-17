from __future__ import annotations

from agent_baton.core.engine.planning.planner import build_plan_diagnostics
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep, TeamMember


def test_build_plan_diagnostics_preserves_existing_agents_and_includes_team_members() -> None:
    plan = MachinePlan(
        task_id="diag-team-task",
        task_summary="Run a coordinated team step",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="team",
                        task_description="Coordinate implementation",
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="backend-engineer",
                                role="lead",
                                task_description="Lead implementation",
                                sub_team=[
                                    TeamMember(
                                        member_id="1.1.a.i",
                                        agent_name="test-engineer",
                                        role="implementer",
                                        task_description="Add tests",
                                    )
                                ],
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="code-reviewer",
                                role="reviewer",
                                task_description="Review the implementation",
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    plan.plan_diagnostics = {
        "selected_agents": ["architect", "backend-engineer"],
    }

    diagnostics = build_plan_diagnostics(plan)

    assert diagnostics["selected_agents"] == [
        "architect",
        "backend-engineer",
        "test-engineer",
        "code-reviewer",
    ]


def test_build_plan_diagnostics_preserves_talent_factory_state_across_amend_cycles() -> None:
    """build_plan_diagnostics doesn't re-run capability-gap detection (only
    RosterStage does) -- a goal-driven amend cycle that re-diagnoses a plan
    must carry forward the capability_gaps / talent_lifecycle_decisions /
    talent_factory_outcomes recorded on the first pass rather than silently
    dropping them. See docs/internal/talent-factory-contract.md and
    agent_baton.core.engine.planning.talent_factory.TalentFactoryOutcome."""
    plan = MachinePlan(
        task_id="diag-talent-factory-task",
        task_summary="Add a specialist workflow",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="architect",
                        task_description="Do the fallback-resolved work",
                    )
                ],
            )
        ],
    )
    plan.plan_diagnostics = {
        "selected_agents": ["architect"],
        "capability_gaps": [
            {
                "requested_capability": "database-whisperer",
                "kind": "missing_role",
                "evidence": [{"source": "roster_stage", "detail": "no match"}],
                "permitted_artifacts": ["agent"],
                "fallback": "route to the closest existing generalist agent",
            }
        ],
        "talent_lifecycle_decisions": [
            {
                "action": "dispatch_talent_builder",
                "reason": "evidence-backed missing_role gap with budget remaining",
                "gap": {"requested_capability": "database-whisperer"},
            }
        ],
        "talent_factory_outcomes": [
            {
                "requested_capability": "database-whisperer",
                "kind": "missing_role",
                "action": "dispatch_talent_builder",
                "status": "generation_failed_fallback",
                "resolved_agent_name": "architect",
                "detail": "talent-builder dispatch failed: claude CLI not available",
                "validation_errors": [],
            }
        ],
    }

    diagnostics = build_plan_diagnostics(plan)

    assert diagnostics["capability_gaps"] == plan.plan_diagnostics["capability_gaps"]
    assert (
        diagnostics["talent_lifecycle_decisions"]
        == plan.plan_diagnostics["talent_lifecycle_decisions"]
    )
    assert (
        diagnostics["talent_factory_outcomes"] == plan.plan_diagnostics["talent_factory_outcomes"]
    )


def test_build_plan_diagnostics_defaults_talent_factory_state_to_empty() -> None:
    """A plan with no prior diagnostics (no capability gaps ever detected)
    gets empty-but-present talent-factory keys -- never a KeyError for a
    caller that always expects the shape."""
    plan = MachinePlan(
        task_id="diag-no-gaps-task",
        task_summary="A plan with no capability gaps",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Ordinary work",
                    )
                ],
            )
        ],
    )

    diagnostics = build_plan_diagnostics(plan)

    assert diagnostics["capability_gaps"] == []
    assert diagnostics["talent_lifecycle_decisions"] == []
    assert diagnostics["talent_factory_outcomes"] == []
