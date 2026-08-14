"""Team consolidation must remap ``depends_on`` when it deletes step ids.

``consolidate_team_step()`` (planning/utils/phase_builder.py) collapses a
multi-step Implement/Fix phase into a *single* ``agent="team"`` step whose id
is ``<phase_id>.1``.  Every other step id in that phase is thereby DELETED.
``PlanStep.depends_on`` holds step *ids*, so any downstream step pointing at a
deleted id is left dangling — and ``MachinePlan``'s plan-graph invariant
(models/execution.py, invariant 4) rejects the whole plan::

    Plan-graph invariant violation: step '4.1' depends on '3.2',
    which is not declared as an earlier step

That is not a cosmetic problem: it is why the INVESTIGATIVE archetype
(``Fix`` = [test-engineer, backend-engineer] followed by ``Verify`` =
[code-reviewer depends_on Fix's last step]) produces *no plan at all*.

The existing tests/planning/test_investigative_archetype_quality.py stops the
pipeline before AssemblyStage, so it cannot see this; the tests below drive the
observable seams instead:

* ``ValidationStage._consolidate_team`` — the caller that collapses phases.
* ``IntelligentPlanner.create_plan`` — end to end, must return a MachinePlan.
* ``baton plan ... --dry-run`` — the user-visible command.
* ``ForesightEngine.analyze`` — the other renumbering path, which must keep
  satisfying the same invariant (a fix landed there already; both paths are
  expected to share one remap helper, so this pins behavioural parity).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from agent_baton.core.engine.classifier import KeywordClassifier
from agent_baton.core.engine.foresight import ForesightEngine
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.stages.validation import ValidationStage
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

from tests._subprocess_helpers import cli_subprocess_env


# Every phrasing reproduced by the RW-1 verifier: the 4 INVESTIGATIVE_TASKS
# from tests/planning/test_investigative_archetype_quality.py plus 3 more
# investigative phrasings.  All 7 must yield a plan.
INVESTIGATIVE_TASKS = [
    "Debug the intermittent 500 error in the checkout API",
    "Investigate the failing regression in the reporting module",
    "Diagnose the memory leak in the worker pool",
    "Triage the flaky test in the billing suite",
    "Add a null check to the parser to avoid the exception",
    "Reproduce the crash in the importer and fix the traceback",
    "Root cause the symptom reported by the customer",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _planner() -> IntelligentPlanner:
    return IntelligentPlanner(task_classifier=KeywordClassifier())


def _services(planner: IntelligentPlanner) -> PlannerServices:
    return planner._build_services(knowledge_registry=planner.knowledge_registry)


def _all_steps(phases: list[PlanPhase]) -> list[PlanStep]:
    return [step for phase in phases for step in phase.steps]


def _dangling_edges(phases: list[PlanPhase]) -> list[tuple[str, str]]:
    """Return ``(step_id, dep)`` pairs whose *dep* names no existing step."""
    known = {step.step_id for step in _all_steps(phases)}
    return [
        (step.step_id, dep)
        for step in _all_steps(phases)
        for dep in step.depends_on
        if dep not in known
    ]


def _consolidate(
    *,
    task_summary: str,
    phases: list[PlanPhase],
) -> PlanDraft:
    """Run the real team-consolidation step over *phases*.

    Exercises ``ValidationStage._consolidate_team`` (the only caller of
    ``consolidate_team_step``) so the assertions stay independent of the
    helper's signature.
    """
    planner = _planner()
    services = _services(planner)
    draft = PlanDraft.from_inputs(task_summary)
    draft.task_id = "rw1-consolidation"
    draft.inferred_type = "bugfix"
    draft.inferred_complexity = "medium"
    draft.risk_level = "medium"
    draft.resolved_agents = [
        "backend-engineer", "test-engineer", "code-reviewer",
    ]
    draft.plan_phases = phases

    extracted_paths, review_result = ValidationStage()._consolidate_team(
        draft=draft, services=services
    )
    draft.extracted_paths = extracted_paths
    draft.review_result = review_result
    return draft


def _investigative_fix_and_verify() -> list[PlanPhase]:
    """The exact shape DecompositionStage emits for an investigative task."""
    return [
        PlanPhase(
            phase_id=1,
            name="Investigate",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Investigate the failure",
                ),
            ],
        ),
        PlanPhase(
            phase_id=3,
            name="Fix",
            steps=[
                PlanStep(
                    step_id="3.1",
                    agent_name="test-engineer",
                    task_description="Write the failing regression test",
                    depends_on=["1.1"],
                ),
                PlanStep(
                    step_id="3.2",
                    agent_name="backend-engineer",
                    task_description="Apply the fix",
                    depends_on=["3.1"],
                ),
            ],
        ),
        PlanPhase(
            phase_id=4,
            name="Verify",
            steps=[
                PlanStep(
                    step_id="4.1",
                    agent_name="code-reviewer",
                    task_description="Review the fix",
                    depends_on=["3.2"],
                ),
            ],
        ),
    ]


def _as_machine_plan(draft: PlanDraft) -> MachinePlan:
    return MachinePlan(
        task_id=draft.task_id,
        task_summary=draft.task_summary,
        risk_level=draft.risk_level,
        budget_tier="standard",
        phases=draft.plan_phases,
        task_type=draft.inferred_type,
        complexity=draft.inferred_complexity,
    )


# ---------------------------------------------------------------------------
# Requirement 1 — consolidation remaps depends_on onto the surviving step
# ---------------------------------------------------------------------------

class TestTeamConsolidationRemapsDependencies:

    def test_dependent_on_collapsed_step_is_remapped_to_survivor(self):
        """Verify's edge must follow the Fix phase into its team step."""
        phases = _investigative_fix_and_verify()
        draft = _consolidate(
            task_summary="Debug the intermittent 500 error in the checkout API",
            phases=phases,
        )

        fix_phase = next(p for p in draft.plan_phases if p.name == "Fix")
        assert len(fix_phase.steps) == 1, (
            "scenario is only meaningful when the Fix phase actually collapsed"
        )
        survivor = fix_phase.steps[0]
        assert survivor.agent_name == "team"

        verify_step = next(
            s for s in _all_steps(draft.plan_phases) if s.agent_name == "code-reviewer"
        )
        assert verify_step.depends_on == [survivor.step_id], (
            f"expected the Verify step to depend on the surviving team step "
            f"{survivor.step_id!r}; got {verify_step.depends_on!r}"
        )

    def test_no_dangling_dependency_survives_consolidation(self):
        phases = _investigative_fix_and_verify()
        draft = _consolidate(
            task_summary="Debug the intermittent 500 error in the checkout API",
            phases=phases,
        )

        assert _dangling_edges(draft.plan_phases) == [], (
            "consolidation left dependency edges pointing at deleted step ids: "
            f"{_dangling_edges(draft.plan_phases)}"
        )

    def test_consolidated_phases_form_a_constructible_machine_plan(self):
        """The plan-graph invariant is the product requirement, not a detail."""
        phases = _investigative_fix_and_verify()
        draft = _consolidate(
            task_summary="Debug the intermittent 500 error in the checkout API",
            phases=phases,
        )

        plan = _as_machine_plan(draft)
        assert plan.total_steps >= 3

    def test_every_dependent_is_remapped_not_just_the_first(self):
        """A three-step phase collapses three ids; all dependents must move."""
        phases = [
            PlanPhase(
                phase_id=1,
                name="Fix",
                steps=[
                    PlanStep(
                        step_id="1.1", agent_name="test-engineer",
                        task_description="Write the failing test",
                    ),
                    PlanStep(
                        step_id="1.2", agent_name="backend-engineer",
                        task_description="Fix the handler",
                    ),
                    PlanStep(
                        step_id="1.3", agent_name="backend-engineer",
                        task_description="Fix the serializer",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Verify",
                steps=[
                    PlanStep(
                        step_id="2.1", agent_name="code-reviewer",
                        task_description="Review the handler change",
                        depends_on=["1.2"],
                    ),
                    PlanStep(
                        step_id="2.2", agent_name="test-engineer",
                        task_description="Run the suite",
                        depends_on=["1.3", "1.1"],
                    ),
                ],
            ),
        ]
        draft = _consolidate(task_summary="Fix the broken checkout flow", phases=phases)

        survivor = draft.plan_phases[0].steps[0]
        assert len(draft.plan_phases[0].steps) == 1
        verify = draft.plan_phases[1].steps
        assert set(verify[0].depends_on) == {survivor.step_id}, (
            f"single-edge dependent not remapped: {verify[0].depends_on!r}"
        )
        assert set(verify[1].depends_on) == {survivor.step_id}, (
            f"multi-edge dependent not remapped: {verify[1].depends_on!r}"
        )
        assert _dangling_edges(draft.plan_phases) == []

    def test_dependency_on_a_filtered_reviewer_step_is_remapped(self):
        """Reviewer steps are dropped from team membership — their ids die too.

        ``consolidate_team_step`` filters reviewer agents out of an
        implement-type phase, so the reviewer's step id disappears along with
        every other non-surviving id.  A downstream edge pointing at it must
        land on the survivor rather than dangle.
        """
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1", agent_name="backend-engineer",
                        task_description="Implement the endpoint",
                    ),
                    PlanStep(
                        step_id="1.2", agent_name="code-reviewer",
                        task_description="Review inline",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Verify",
                steps=[
                    PlanStep(
                        step_id="2.1", agent_name="test-engineer",
                        task_description="Verify the endpoint",
                        depends_on=["1.2"],
                    ),
                ],
            ),
        ]
        draft = _consolidate(task_summary="Add the reporting endpoint", phases=phases)

        survivor = draft.plan_phases[0].steps[0]
        assert len(draft.plan_phases[0].steps) == 1
        assert draft.plan_phases[1].steps[0].depends_on == [survivor.step_id], (
            "edge onto the filtered reviewer step was not remapped: "
            f"{draft.plan_phases[1].steps[0].depends_on!r}"
        )

    def test_team_signal_phase_outside_implement_also_remaps(self):
        """The team-signal path (``is_team_phase`` via task wording) collapses
        non-implement phases too, and must remap just the same."""
        phases = [
            PlanPhase(
                phase_id=1,
                name="Analysis",
                steps=[
                    PlanStep(
                        step_id="1.1", agent_name="backend-engineer",
                        task_description="Analyse the data layer",
                    ),
                    PlanStep(
                        step_id="1.2", agent_name="frontend-engineer",
                        task_description="Analyse the view layer",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Report",
                steps=[
                    PlanStep(
                        step_id="2.1", agent_name="technical-writer",
                        task_description="Write up the findings",
                        depends_on=["1.2"],
                    ),
                ],
            ),
        ]
        draft = _consolidate(
            task_summary="Do a paired adversarial review together as a team",
            phases=phases,
        )

        analysis = draft.plan_phases[0]
        assert len(analysis.steps) == 1, (
            "scenario requires the team-signal collapse to fire"
        )
        assert draft.plan_phases[1].steps[0].depends_on == [analysis.steps[0].step_id]
        assert _dangling_edges(draft.plan_phases) == []

    def test_surviving_team_step_does_not_depend_on_itself(self):
        """Intra-phase edges collapse onto one id — that must not self-loop.

        ``MachinePlan`` records a step id before checking its own edges, so a
        self-dependency passes validation and deadlocks the dispatcher instead
        of failing loudly.  Guard it here.
        """
        phases = _investigative_fix_and_verify()
        draft = _consolidate(
            task_summary="Debug the intermittent 500 error in the checkout API",
            phases=phases,
        )

        for step in _all_steps(draft.plan_phases):
            assert step.step_id not in step.depends_on, (
                f"step {step.step_id!r} depends on itself: {step.depends_on!r}"
            )


# ---------------------------------------------------------------------------
# Requirement 1 (second half) — parity with the other renumbering path
# ---------------------------------------------------------------------------

class TestRemapParityAcrossRenumberingPaths:
    """Both id-rewriting paths must satisfy the same graph invariant.

    ``ForesightEngine.analyze`` renumbers ids on insertion; team consolidation
    deletes ids on collapse.  Whichever helper does the remap, neither path may
    leave a dangling edge or produce a plan ``MachinePlan`` rejects.
    """

    def test_foresight_renumbering_leaves_no_dangling_edge(self):
        phases = [
            PlanPhase(
                phase_id=1,
                name="Investigate",
                steps=[
                    PlanStep(
                        step_id="1.1", agent_name="backend-engineer",
                        task_description=(
                            "Investigate the failing database migration and "
                            "identify the root cause"
                        ),
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Fix",
                steps=[
                    PlanStep(
                        step_id="2.1", agent_name="backend-engineer",
                        task_description="Apply the fix identified by the investigation",
                        depends_on=["1.1"],
                    ),
                ],
            ),
        ]
        result, insights = ForesightEngine().analyze(
            phases, "Fix the failing database migration"
        )
        assert insights, "scenario requires a foresight insertion"
        assert _dangling_edges(result) == []
        MachinePlan(
            task_id="rw1-foresight",
            task_summary="Fix the failing database migration",
            risk_level="medium",
            budget_tier="standard",
            phases=result,
        )

    def test_consolidation_leaves_no_dangling_edge(self):
        draft = _consolidate(
            task_summary="Debug the intermittent 500 error in the checkout API",
            phases=_investigative_fix_and_verify(),
        )
        assert _dangling_edges(draft.plan_phases) == []
        _as_machine_plan(draft)


# ---------------------------------------------------------------------------
# Requirement 2 — create_plan must return a MachinePlan end to end
# ---------------------------------------------------------------------------

class TestInvestigativeTaskProducesAPlan:
    """End-to-end: the full pipeline including AssemblyStage.

    tests/planning/test_investigative_archetype_quality.py deliberately stops
    before AssemblyStage, which is why it stays green while the product is
    broken.  These tests run ``create_plan`` itself.
    """

    @pytest.mark.parametrize("task_summary", INVESTIGATIVE_TASKS)
    def test_create_plan_returns_a_machine_plan(self, task_summary: str):
        plan = _planner().create_plan(task_summary)

        assert isinstance(plan, MachinePlan), (
            f"create_plan({task_summary!r}) did not return a MachinePlan"
        )
        assert plan.phases, "plan has no phases"
        assert plan.total_steps > 0, "plan has no steps"

    @pytest.mark.parametrize("task_summary", INVESTIGATIVE_TASKS)
    def test_created_plan_has_no_dangling_dependencies(self, task_summary: str):
        plan = _planner().create_plan(task_summary)

        assert _dangling_edges(plan.phases) == [], (
            f"plan for {task_summary!r} has dangling dependency edges: "
            f"{_dangling_edges(plan.phases)}"
        )
        for step in _all_steps(plan.phases):
            assert step.step_id not in step.depends_on, (
                f"step {step.step_id!r} depends on itself"
            )


# ---------------------------------------------------------------------------
# Requirement 3 — the user-visible command exits 0 and prints a plan
# ---------------------------------------------------------------------------

class TestPlanCliDryRun:

    @pytest.mark.parametrize(
        "task_summary",
        [
            "fix a bug",
            "Debug the intermittent 500 error in the checkout API",
        ],
    )
    def test_dry_run_exits_zero_and_prints_a_plan(self, task_summary: str, tmp_path):
        proc = subprocess.run(
            [
                sys.executable, "-m", "agent_baton.cli.main",
                "plan", task_summary, "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=cli_subprocess_env(),
            timeout=300,
        )

        assert proc.returncode == 0, (
            f"`baton plan {task_summary!r} --dry-run` exited "
            f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert "Plan Preview" in proc.stdout, (
            f"no plan preview printed\nstdout:\n{proc.stdout}"
        )
        assert "invariant violation" not in (proc.stdout + proc.stderr)
