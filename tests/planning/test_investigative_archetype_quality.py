"""Plan-quality behaviour for the INVESTIGATIVE archetype.

The investigative archetype builds a terminal reviewer phase (``Verify``)
carrying a ``code-reviewer`` step.  ValidationStage's ``review_missing``
gate must recognise that phase as review coverage; otherwise every
debugging / RCA task is hard-blocked by ``PlanQualityError`` and no plan
is produced at all.

These tests drive the *real* planning pipeline (classification → roster →
risk → decomposition → enrichment → validation) rather than a
hand-assembled draft, so they pin the user-visible behaviour: an
investigative task must survive the plan-quality hard gate.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.classifier import KeywordClassifier
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.stages.classification import ClassificationStage
from agent_baton.core.engine.planning.stages.decomposition import DecompositionStage
from agent_baton.core.engine.planning.stages.enrichment import EnrichmentStage
from agent_baton.core.engine.planning.stages.risk import RiskStage
from agent_baton.core.engine.planning.stages.roster import RosterStage
from agent_baton.core.engine.planning.stages.validation import (
    PlanQualityError,
    ValidationStage,
)
from agent_baton.core.orchestration.router import REVIEWER_AGENTS
from agent_baton.models.execution import PlanPhase, PlanStep


# Tasks whose wording trips ``_INVESTIGATIVE_SIGNALS`` in the keyword
# classifier — debugging / RCA work, the reason the archetype exists.
INVESTIGATIVE_TASKS = [
    "Debug the intermittent 500 error in the checkout API",
    "Investigate the failing regression in the reporting module",
    "Diagnose the memory leak in the worker pool",
    "Triage the flaky test in the billing suite",
]


def _planner() -> IntelligentPlanner:
    return IntelligentPlanner(task_classifier=KeywordClassifier())


def _services(planner: IntelligentPlanner) -> PlannerServices:
    return planner._build_services(knowledge_registry=planner.knowledge_registry)


def _run_pipeline(task_summary: str, *, through_validation: bool) -> PlanDraft:
    """Run the real planning stages over *task_summary*.

    Stops before AssemblyStage so this test exercises the plan-quality
    gate only.  ``through_validation=False`` yields the draft the gate
    would inspect; ``True`` runs the gate itself.
    """
    planner = _planner()
    services = _services(planner)
    draft = PlanDraft.from_inputs(task_summary)

    stages = [
        ClassificationStage(),
        RosterStage(),
        RiskStage(),
        DecompositionStage(),
        EnrichmentStage(),
    ]
    if through_validation:
        stages.append(ValidationStage())

    for stage in stages:
        draft = stage.run(draft, services)
    return draft


def _agent_bases(step: PlanStep) -> set[str]:
    names = [step.agent_name or ""]
    for member in getattr(step, "team_members", None) or []:
        names.append(getattr(member, "agent_name", "") or "")
    return {n.split("--")[0] for n in names if n}


class TestInvestigativeReviewCoverage:
    def test_has_review_coverage_accepts_verify_phase(self):
        """A terminal ``Verify`` phase with a reviewer counts as coverage."""
        draft = PlanDraft.from_inputs("Debug the failing checkout request")
        draft.task_id = "task-investigative-verify"
        draft.plan_phases = [
            PlanPhase(
                phase_id=4,
                name="Verify",
                steps=[
                    PlanStep(
                        step_id="4.1",
                        agent_name="code-reviewer",
                        task_description="Verify the root-cause fix.",
                        step_type="reviewing",
                    )
                ],
            )
        ]

        assert ValidationStage()._has_review_coverage(draft) is True

    @pytest.mark.parametrize("task_summary", INVESTIGATIVE_TASKS)
    def test_investigative_plan_has_no_review_missing_defect(self, task_summary):
        draft = _run_pipeline(task_summary, through_validation=False)

        assert draft.planning_archetype == "investigative"
        assert len(draft.plan_phases) == 4

        defects = ValidationStage()._detect_defects(draft)
        assert not [d for d in defects if d.code == "review_missing"], (
            f"review_missing raised for {task_summary!r}; "
            f"phases={[p.name for p in draft.plan_phases]}"
        )

    @pytest.mark.parametrize("task_summary", INVESTIGATIVE_TASKS)
    def test_investigative_plan_survives_the_quality_gate(self, task_summary):
        """ValidationStage must not hard-block investigative planning."""
        try:
            draft = _run_pipeline(task_summary, through_validation=True)
        except PlanQualityError as exc:
            pytest.fail(
                f"investigative task {task_summary!r} blocked by the "
                f"plan-quality gate: {exc}"
            )

        assert draft.planning_archetype == "investigative"
        assert not [
            d for d in getattr(draft, "plan_defects", []) if d.severity == "critical"
        ]

    @pytest.mark.parametrize("task_summary", INVESTIGATIVE_TASKS)
    def test_investigative_plan_keeps_a_terminal_reviewer_step(self, task_summary):
        """Coverage must come from a real reviewer, not from dropping one."""
        draft = _run_pipeline(task_summary, through_validation=False)

        terminal_phase = draft.plan_phases[-1]
        reviewer_bases = REVIEWER_AGENTS - {"auditor"}
        assert any(
            _agent_bases(step) & reviewer_bases for step in terminal_phase.steps
        ), (
            f"terminal phase {terminal_phase.name!r} has no reviewer step: "
            f"{[s.agent_name for s in terminal_phase.steps]}"
        )
