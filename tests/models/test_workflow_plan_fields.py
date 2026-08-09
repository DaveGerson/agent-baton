"""Round-trip + absent-when-empty tests for the two workflow plan fields.

Spec: docs/internal/adversarial-tdd-workflow-design.md decision #5 —
``MachinePlan.workflow: str = ""`` and ``PlanStep.workflow_stage: str = ""``,
declared on the models and emitted by ``to_dict()`` **only when non-empty** so
the existing golden fixtures (and every plan.json written before this feature)
stay byte-identical (acceptance criterion #5).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

GOLDEN_DIR = Path(__file__).parent / "golden_states"


def _golden(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _reload(plan: MachinePlan) -> MachinePlan:
    """JSON round-trip through the canonical on-disk representation."""
    return MachinePlan.from_dict(json.loads(json.dumps(plan.to_dict())))


def _plan(**overrides: Any) -> MachinePlan:
    fields: dict[str, Any] = {
        "task_id": "task-workflow-fields",
        "task_summary": "add rate limiting to the public API",
    }
    fields.update(overrides)
    return MachinePlan(**fields)


def _staged_plan() -> MachinePlan:
    return _plan(
        workflow="adversarial-tdd",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Brainstorm & Spec",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="architect",
                        task_description="Write the spec",
                        model="fable",
                        step_type="planning",
                        workflow_stage="spec",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="backend-engineer",
                        task_description="Build the limiter",
                        model="sonnet",
                        workflow_stage="implementation",
                    )
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# MachinePlan.workflow
# ---------------------------------------------------------------------------

class TestMachinePlanWorkflowField:
    def test_defaults_to_empty_string(self) -> None:
        assert _plan().workflow == ""

    def test_accepts_a_preset_name(self) -> None:
        assert _plan(workflow="adversarial-tdd").workflow == "adversarial-tdd"

    def test_absent_from_to_dict_when_empty(self) -> None:
        assert "workflow" not in _plan().to_dict()

    def test_present_in_to_dict_when_set(self) -> None:
        assert _plan(workflow="adversarial-tdd").to_dict()["workflow"] == (
            "adversarial-tdd"
        )

    def test_round_trips(self) -> None:
        assert _reload(_plan(workflow="adversarial-tdd")).workflow == "adversarial-tdd"

    def test_round_trips_as_empty_when_unset(self) -> None:
        assert _reload(_plan()).workflow == ""

    def test_round_trip_is_idempotent(self) -> None:
        plan = _plan(workflow="adversarial-tdd")
        assert _reload(_reload(plan)).to_dict() == plan.to_dict()

    def test_markdown_shows_the_workflow_when_set(self) -> None:
        assert "adversarial-tdd" in _plan(workflow="adversarial-tdd").to_markdown()

    def test_markdown_omits_the_workflow_when_unset(self) -> None:
        assert "adversarial-tdd" not in _plan().to_markdown()


# ---------------------------------------------------------------------------
# PlanStep.workflow_stage
# ---------------------------------------------------------------------------

class TestPlanStepWorkflowStageField:
    def _step(self, **overrides: Any) -> PlanStep:
        fields: dict[str, Any] = {
            "step_id": "1.1",
            "agent_name": "architect",
            "task_description": "Write the spec",
        }
        fields.update(overrides)
        return PlanStep(**fields)

    def test_defaults_to_empty_string(self) -> None:
        assert self._step().workflow_stage == ""

    def test_accepts_a_stage_id(self) -> None:
        assert self._step(workflow_stage="spec").workflow_stage == "spec"

    def test_absent_from_to_dict_when_empty(self) -> None:
        assert "workflow_stage" not in self._step().to_dict()

    def test_present_in_to_dict_when_set(self) -> None:
        assert self._step(workflow_stage="spec").to_dict()["workflow_stage"] == "spec"

    def test_round_trips(self) -> None:
        step = self._step(workflow_stage="test_verification")
        assert PlanStep.from_dict(step.to_dict()).workflow_stage == (
            "test_verification"
        )

    def test_carryover_stage_round_trips(self) -> None:
        step = self._step(agent_name="auditor", workflow_stage="carryover")
        assert PlanStep.from_dict(step.to_dict()).workflow_stage == "carryover"


class TestPlanStepWorkflowStageMarkdown:
    """§4: PlanStep.workflow_stage 'shown in to_markdown() when set'."""

    def _plan_with_step(self, **step_overrides: Any) -> MachinePlan:
        fields: dict[str, Any] = {
            "step_id": "1.1",
            "agent_name": "architect",
            "task_description": "Write the spec",
        }
        fields.update(step_overrides)
        return MachinePlan(
            task_id="task-workflow-markdown",
            task_summary="add rate limiting",
            phases=[PlanPhase(phase_id=1, name="Brainstorm & Spec", steps=[PlanStep(**fields)])],
        )

    def test_markdown_shows_the_stage_when_set(self) -> None:
        plan = self._plan_with_step(workflow_stage="spec")
        assert "**Workflow stage**: spec" in plan.to_markdown()

    def test_markdown_omits_the_stage_when_unset(self) -> None:
        plan = self._plan_with_step()
        assert "**Workflow stage**" not in plan.to_markdown()


# ---------------------------------------------------------------------------
# Nested round-trip through a whole plan
# ---------------------------------------------------------------------------

class TestNestedRoundTrip:
    def test_stage_stamps_survive_a_plan_round_trip(self) -> None:
        reloaded = _reload(_staged_plan())
        assert reloaded.workflow == "adversarial-tdd"
        assert [s.workflow_stage for s in reloaded.all_steps] == [
            "spec",
            "implementation",
        ]

    def test_whole_plan_to_dict_is_stable(self) -> None:
        plan = _staged_plan()
        assert _reload(plan).to_dict() == plan.to_dict()

    def test_mixed_stamped_and_unstamped_steps(self) -> None:
        plan = _staged_plan()
        plan.phases[1].steps.append(
            PlanStep(
                step_id="2.2",
                agent_name="auditor",
                task_description="Unstamped legacy step",
            )
        )
        payload = plan.to_dict()
        assert payload["phases"][1]["steps"][0]["workflow_stage"] == "implementation"
        assert "workflow_stage" not in payload["phases"][1]["steps"][1]


# ---------------------------------------------------------------------------
# Golden-fixture compatibility (acceptance criterion #5)
# ---------------------------------------------------------------------------

class TestGoldenCompatibility:
    def test_golden_machine_plan_gains_no_workflow_key(self) -> None:
        golden = _golden("MachinePlan")
        assert MachinePlan.from_dict(golden).to_dict() == golden

    def test_golden_plan_step_gains_no_workflow_stage_key(self) -> None:
        golden = _golden("PlanStep")
        assert PlanStep.from_dict(golden).to_dict() == golden

    def test_unstamped_plan_key_set_is_unchanged(self) -> None:
        golden = _golden("MachinePlan")
        assert set(_plan().to_dict()) <= set(golden) | {"plan_diagnostics"}

    def test_unknown_future_field_is_still_ignored(self) -> None:
        # Forward-compat contract of PlanModel(extra="ignore") must not be
        # broken by the new declared fields.
        PlanStep.from_dict({**_golden("PlanStep"), "_future_field": "ignored"})
        MachinePlan.from_dict({**_golden("MachinePlan"), "_future_field": "ignored"})
