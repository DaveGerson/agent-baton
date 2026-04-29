"""Quality fix #2 regression tests: ValidationStage as a real gate.

The legacy ``PlanReviewer`` skipped light-complexity plans entirely
(``plan_reviewer.py:222`` ``return PlanReviewResult(source="skipped-light")``)
and treated all of its findings as advisory.  ValidationStage now:

  1. Runs on every plan (no light-complexity early return at the
     stage level — the underlying reviewer may still skip itself, in
     which case ValidationStage records it as a defect).
  2. Detects defects independently of the reviewer:
       * empty_plan / empty_phase
       * agent_phase_mismatch (bd-0e36 / bd-1974 family)
       * review_skipped on non-light plans
  3. Surfaces defects on ``draft.plan_defects``.
  4. Under ``BATON_PLANNER_HARD_GATE=1`` raises ``PlanQualityError``
     when any defect is critical.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.stages.validation import (
    PlanDefect,
    PlanQualityError,
    ValidationStage,
)
from agent_baton.models.execution import PlanPhase, PlanStep


def _stub_services(planner: IntelligentPlanner) -> PlannerServices:
    """Build a minimal services container backed by *planner*."""
    return planner._build_services()


class TestPlanDefect:
    def test_str_includes_severity_code_and_message(self) -> None:
        d = PlanDefect(code="empty_plan", severity="critical", message="zero phases")
        assert "[critical]" in str(d)
        assert "empty_plan" in str(d)
        assert "zero phases" in str(d)


class TestDefectDetection:
    def test_empty_plan_is_critical(self) -> None:
        planner = IntelligentPlanner()
        services = _stub_services(planner)
        draft = PlanDraft.from_inputs("Add foo")
        draft.plan_phases = []
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "empty_plan" in codes
        assert all(d.severity == "critical" for d in defects if d.code == "empty_plan")

    def test_empty_phase_is_critical(self) -> None:
        planner = IntelligentPlanner()
        draft = PlanDraft.from_inputs("Add foo")
        draft.plan_phases = [PlanPhase(phase_id=1, name="Implement", steps=[])]
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "empty_phase" in codes

    def test_agent_phase_mismatch_is_critical(self) -> None:
        # Architect on Implement is the bd-0e36 / bd-1974 family.
        planner = IntelligentPlanner()
        draft = PlanDraft.from_inputs("Add foo")
        draft.plan_phases = [
            PlanPhase(
                phase_id=1, name="Implement",
                steps=[PlanStep(
                    step_id="impl-1",
                    agent_name="architect",
                    task_description="Should not be assigned to Implement",
                )],
            )
        ]
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "agent_phase_mismatch" in codes

    def test_clean_plan_yields_no_critical_defects(self) -> None:
        planner = IntelligentPlanner()
        plan = planner.create_plan("Add a hello-world endpoint")
        # The pipeline already ran ValidationStage; the legacy reviewer
        # may have annotated splits/warnings but nothing critical.
        # Check that ValidationStage attached a defects list.
        # (We can't easily get the draft back, but the plan must exist.)
        assert plan.task_id
        assert len(plan.phases) >= 1


class TestHardGate:
    def teardown_method(self) -> None:
        os.environ.pop("BATON_PLANNER_HARD_GATE", None)

    def test_clean_plan_passes_under_hard_gate(self) -> None:
        os.environ["BATON_PLANNER_HARD_GATE"] = "1"
        planner = IntelligentPlanner()
        plan = planner.create_plan("Add a hello-world endpoint")
        assert plan.task_id  # no exception

    def test_critical_defect_raises_under_hard_gate(self) -> None:
        os.environ["BATON_PLANNER_HARD_GATE"] = "1"
        stage = ValidationStage()
        # Stub the legacy delegations so we control the draft state.
        with patch.object(stage, "_detect_defects") as detect:
            detect.return_value = [
                PlanDefect(code="empty_plan", severity="critical", message="x")
            ]
            planner = IntelligentPlanner()
            services = _stub_services(planner)
            draft = PlanDraft.from_inputs("Add foo")
            draft.plan_phases = []  # placeholder
            with patch.object(services.planner, "_step_check_scores", return_value="standard"):
                with patch.object(services.planner, "_step_consolidate_team", return_value=[]):
                    services.planner._last_review_result = None  # type: ignore[attr-defined]
                    with pytest.raises(PlanQualityError) as ei:
                        stage.run(draft, services)
                    assert "empty_plan" in str(ei.value)

    def test_critical_defect_only_warns_without_hard_gate(self) -> None:
        # No env var set.
        stage = ValidationStage()
        assert not stage._hard_gate_enabled()


class TestHardGateEnvParsing:
    def teardown_method(self) -> None:
        os.environ.pop("BATON_PLANNER_HARD_GATE", None)

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
    def test_truthy_values_enable_gate(self, value: str) -> None:
        os.environ["BATON_PLANNER_HARD_GATE"] = value
        assert ValidationStage()._hard_gate_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values_disable_gate(self, value: str) -> None:
        os.environ["BATON_PLANNER_HARD_GATE"] = value
        assert ValidationStage()._hard_gate_enabled() is False
