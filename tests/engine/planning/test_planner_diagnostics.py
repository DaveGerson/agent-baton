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
