"""Reviewer-class agents must never be stranded in an implementation phase.

``ValidationStage`` hard-blocks any plan that places a reviewer-class agent
(``code-reviewer``, ``security-reviewer``, ``plan-reviewer``,
``spec-document-reviewer``) inside an implement-type phase — defect code
``agent_phase_mismatch``.  The planner must therefore never *build* such a
plan: the roster-to-phase pairing has to hold on every phase-construction
path, not just the explicit-complexity override path.

The path exercised here is the *classifier* path — the one every real
``baton plan`` invocation takes.  ``ClassificationStage`` asks the task
classifier for ``(agents, phases)``; when the returned phase list carries no
review slot, ``RiskStage`` appends one, but ``assign_agents_to_phases()``
places at most one primary agent per phase.  Any reviewer that loses the race
for the single review slot is force-landed in the implement phase, and the
planner's own quality gate then rejects the plan it just built — so
``create_plan()`` raises ``PlanQualityError`` instead of returning a plan.

These tests stub the classifier so the classification is deterministic and
hermetic (the production default, ``FallbackClassifier``, shells out to the
``claude`` CLI, which makes plan shape depend on whether that binary is
installed).  The stubbed classifications mirror what the LLM classifier
actually returns for security/knowledge-flavoured prompts such as the
``knowledge-heavy`` golden case in ``test_plan_quality_golden.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_baton.core.engine.classifier import TaskClassification
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.rules.phase_roles import IMPLEMENT_PHASE_NAMES
from agent_baton.core.engine.planning.stages.validation import PlanQualityError
from agent_baton.core.orchestration.router import REVIEWER_AGENTS


# Phase list with no review slot — what the classifier returns for a
# medium-complexity task before risk is known.
PHASES_WITHOUT_REVIEW = ["Design", "Draft", "Test"]

# Rosters that carry more reviewer-class agents than the classifier's phase
# list has review slots for.
REVIEWER_ROSTERS = [
    pytest.param(
        ["architect", "backend-engineer", "code-reviewer", "security-reviewer"],
        id="two-reviewers-one-review-slot",
    ),
    pytest.param(
        ["architect", "backend-engineer", "plan-reviewer"],
        id="plan-reviewer",
    ),
    pytest.param(
        ["architect", "backend-engineer", "spec-document-reviewer"],
        id="spec-document-reviewer",
    ),
]

NEUTRAL_TASK = "Update the login form copy"

# The prompt behind the ``knowledge-heavy`` golden case.  A security-flavoured
# summary makes RiskStage inject ``code-reviewer`` on top of the rostered
# ``security-reviewer``, so two reviewers compete for one review slot.
KNOWLEDGE_HEAVY_TASK = (
    "Use project security knowledge to update JWT authentication guidance and tests"
)


class _StubClassifier:
    """Deterministic stand-in for the LLM-backed task classifier."""

    def __init__(
        self,
        *,
        agents: list[str],
        phases: list[str],
        task_type: str = "feature",
        complexity: str = "medium",
    ) -> None:
        self._agents = list(agents)
        self._phases = list(phases)
        self._task_type = task_type
        self._complexity = complexity

    def classify(
        self,
        summary: str,
        registry: Any = None,
        project_root: Any = None,
    ) -> TaskClassification:
        return TaskClassification(
            task_type=self._task_type,
            complexity=self._complexity,
            agents=list(self._agents),
            phases=list(self._phases),
            reasoning="stubbed classification for deterministic planning",
            source="stub",
        )


def _agent_bases(step: Any) -> set[str]:
    """Every agent base name attached to *step*, including nested teams."""
    names: list[str] = [step.agent_name or ""]

    def _walk(members: Any) -> None:
        for member in members or []:
            names.append(getattr(member, "agent_name", "") or "")
            _walk(getattr(member, "sub_team", None))

    _walk(getattr(step, "team", None))
    return {n.split("--")[0] for n in names if n}


def _reviewers_in_implementation_phases(plan: Any) -> list[tuple[str, str, str]]:
    """``(phase_name, step_id, agent)`` for every reviewer in an implement phase."""
    offenders: list[tuple[str, str, str]] = []
    for phase in plan.phases:
        if (phase.name or "").strip().lower() not in IMPLEMENT_PHASE_NAMES:
            continue
        for step in phase.steps:
            for base in sorted(_agent_bases(step) & REVIEWER_AGENTS):
                offenders.append((phase.name, step.step_id, base))
    return offenders


@pytest.fixture(autouse=True)
def _clear_planner_gate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """These assertions describe default gate behavior; strip ambient overrides."""
    for var in (
        "BATON_DEV_MODE",
        "BATON_PLANNER_WARN_ONLY",
        "BATON_PLANNER_HARD_GATE",
    ):
        monkeypatch.delenv(var, raising=False)


class TestReviewerPhasePlacement:
    @pytest.mark.parametrize("roster", REVIEWER_ROSTERS)
    def test_classifier_path_keeps_reviewers_out_of_implementation_phases(
        self, roster: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No reviewer-class agent may hold a step in an implement-type phase.

        Warn-only mode is used here so the assertion is about plan *shape*
        rather than about the gate that rejects the shape.
        """
        monkeypatch.setenv("BATON_PLANNER_WARN_ONLY", "1")
        planner = IntelligentPlanner(
            task_classifier=_StubClassifier(
                agents=roster, phases=PHASES_WITHOUT_REVIEW
            )
        )

        plan = planner.create_plan(NEUTRAL_TASK)

        offenders = _reviewers_in_implementation_phases(plan)
        assert not offenders, (
            f"reviewer-class agent(s) placed in an implementation phase: "
            f"{offenders}; phases="
            f"{[(p.name, [s.agent_name for s in p.steps]) for p in plan.phases]}"
        )

    @pytest.mark.parametrize("roster", REVIEWER_ROSTERS)
    def test_classifier_path_plan_survives_the_quality_gate(
        self, roster: list[str]
    ) -> None:
        """``create_plan()`` must not raise the planner's own quality error."""
        planner = IntelligentPlanner(
            task_classifier=_StubClassifier(
                agents=roster, phases=PHASES_WITHOUT_REVIEW
            )
        )

        try:
            plan = planner.create_plan(NEUTRAL_TASK)
        except PlanQualityError as exc:
            pytest.fail(
                f"planner emitted a plan its own ValidationStage rejects "
                f"for roster {roster}: {exc}"
            )

        assert plan.phases

    def test_knowledge_heavy_golden_prompt_produces_a_usable_plan(self) -> None:
        """The ``knowledge-heavy`` golden scenario must plan without blowing up.

        A rostered ``security-reviewer`` plus the ``code-reviewer`` RiskStage
        injects for a security-flavoured summary is the exact roster that
        strands a reviewer in the ``Draft`` phase.
        """
        planner = IntelligentPlanner(
            task_classifier=_StubClassifier(
                agents=["architect", "backend-engineer", "security-reviewer"],
                phases=PHASES_WITHOUT_REVIEW,
            )
        )

        try:
            plan = planner.create_plan(
                KNOWLEDGE_HEAVY_TASK, explicit_knowledge_packs=["security-pack"]
            )
        except PlanQualityError as exc:
            pytest.fail(
                f"knowledge-heavy planning blocked by the plan-quality gate: {exc}"
            )

        assert not _reviewers_in_implementation_phases(plan)
