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
       * review_missing / audit_missing for missing quality coverage
       * review_skipped on non-light plans
  3. Surfaces defects on ``draft.plan_defects``.
  4. Raises ``PlanQualityError`` for critical defects by default; explicit
     warn-only/dev mode downgrades to warnings unless the legacy hard-gate
     override is truthy.
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
    return planner._build_services(knowledge_registry=planner.knowledge_registry)


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


class TestShallowDecompositionDetection:
    """Phase 6 6.1: generic placeholder language and empty deliverables/
    scope are validated for heavy-complexity plans. See
    ``planning.utils.repo_grounding`` for the grounding pipeline this
    validation is checking the output of.
    """

    def _heavy_draft(self, steps: list[PlanStep], phase_name: str = "Implement") -> PlanDraft:
        draft = PlanDraft.from_inputs("Redesign the whole subsystem", complexity="heavy")
        draft.inferred_complexity = "heavy"
        draft.plan_phases = [PlanPhase(phase_id=1, name=phase_name, steps=steps)]
        draft.review_result = None
        return draft

    def test_bare_agent_template_suffix_is_warning_not_critical(self) -> None:
        """The literal "(as <agent>)" fallback is a real signal worth
        surfacing, but STEP_TEMPLATES coverage gaps make it a legitimate
        outcome for some agent/phase pairs on an otherwise-fine plan (see
        ``tests/test_engine_planner.py::
        TestOriginalProblemScenario::test_multi_concern_task_decomposes_correctly``
        for a real example) -- so it must not block.
        """
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement: redesign the whole subsystem (as backend-engineer)",
            deliverables=["Working implementation with tests"],
            allowed_paths=["app"],
        )
        draft = self._heavy_draft([step])
        defects = ValidationStage()._detect_defects(draft)
        matches = [d for d in defects if d.code == "bare_agent_template"]
        assert matches
        assert all(d.severity == "warning" for d in matches)
        assert "generic_placeholder" not in [d.code for d in defects]

    def test_tbd_placeholder_marker_is_critical(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="TBD — figure out scope later",
            deliverables=["x"],
            allowed_paths=["app"],
        )
        draft = self._heavy_draft([step])
        defects = ValidationStage()._detect_defects(draft)
        assert "generic_placeholder" in [d.code for d in defects]

    def test_todo_and_placeholder_as_product_nouns_are_not_flagged(self) -> None:
        """Phase 6 review regression: "todo" and "placeholder" are also
        legitimate product/feature nouns. A heavy plan for "build a todo
        list application" (or "add placeholder text") must NOT be blocked
        as generic_placeholder -- only genuine marker usages ("TODO: scope
        this", "details tbd") should."""
        for desc in (
            "Implement: build a todo list application with sync",
            "Implement the todo-app REST endpoints",
            "Add placeholder text to the empty search results panel",
            "Render a placeholder image while avatars load",
        ):
            step = PlanStep(
                step_id="1.1",
                agent_name="backend-engineer",
                task_description=desc,
                deliverables=["Concrete change in app/todo.py"],
                allowed_paths=["app"],
            )
            draft = self._heavy_draft([step])
            defects = ValidationStage()._detect_defects(draft)
            assert "generic_placeholder" not in [d.code for d in defects], desc

        # Genuine markers still blocked.
        for desc in (
            "TODO: scope this properly",
            "Implement auth (details tbd)",
            "This step is a placeholder for the real work package",
        ):
            step = PlanStep(
                step_id="1.1",
                agent_name="backend-engineer",
                task_description=desc,
                deliverables=["x"],
                allowed_paths=["app"],
            )
            draft = self._heavy_draft([step])
            defects = ValidationStage()._detect_defects(draft)
            assert "generic_placeholder" in [d.code for d in defects], desc

    def test_concrete_description_is_not_flagged(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description=(
                "Implement render_widget in app/widget.py. Repository scope — "
                "files: app/widget.py; existing tests: tests/test_widget.py."
            ),
            deliverables=["Concrete change in app/widget.py"],
            allowed_paths=["app"],
        )
        draft = self._heavy_draft([step])
        defects = ValidationStage()._detect_defects(draft)
        assert "generic_placeholder" not in [d.code for d in defects]
        assert "empty_deliverables" not in [d.code for d in defects]
        assert "empty_scope" not in [d.code for d in defects]

    def test_empty_deliverables_on_implement_phase_is_warning_not_critical(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement: a concrete grounded change",
            deliverables=[],
            allowed_paths=["app"],
        )
        draft = self._heavy_draft([step])
        defects = ValidationStage()._detect_defects(draft)
        matches = [d for d in defects if d.code == "empty_deliverables"]
        assert matches
        assert all(d.severity == "warning" for d in matches)

    def test_empty_scope_on_write_capable_step_is_warning_not_critical(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement: a concrete grounded change",
            deliverables=["Concrete change in app/widget.py"],
            allowed_paths=[],
        )
        draft = self._heavy_draft([step])
        defects = ValidationStage()._detect_defects(draft)
        matches = [d for d in defects if d.code == "empty_scope"]
        assert matches
        assert all(d.severity == "warning" for d in matches)

    def test_review_only_step_missing_scope_is_not_flagged(self) -> None:
        step = PlanStep(
            step_id="2.1",
            agent_name="code-reviewer",
            task_description="Review the change",
            step_type="reviewing",
            allowed_paths=[],
        )
        draft = self._heavy_draft([step], phase_name="Review")
        defects = ValidationStage()._detect_defects(draft)
        assert "empty_scope" not in [d.code for d in defects]

    def test_light_complexity_plan_is_not_checked(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement: foo (as backend-engineer)",
        )
        draft = PlanDraft.from_inputs("Add foo")
        draft.inferred_complexity = "light"
        draft.plan_phases = [PlanPhase(phase_id=1, name="Implement", steps=[step])]
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "generic_placeholder" not in codes
        assert "bare_agent_template" not in codes
        assert "empty_deliverables" not in codes
        assert "empty_scope" not in codes

    def test_heavy_plan_with_placeholder_marker_blocks_create_plan(self, tmp_path) -> None:
        """End-to-end: a heavy plan whose (hand-supplied) step description
        contains a literal placeholder marker is rejected by the pipeline
        -- not just detected by the unit-level defect check.
        """
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend-engineer.md").write_text(
            "---\nname: backend-engineer\ndescription: backend specialist.\n"
            "model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n",
            encoding="utf-8",
        )
        planner = IntelligentPlanner(team_context_root=tmp_path / "team-context")
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        planner._registry = reg
        planner._router = AgentRouter(reg)

        with pytest.raises(PlanQualityError) as ei:
            planner.create_plan(
                "Redesign the whole subsystem TODO: scope this properly",
                complexity="heavy",
                phases=[{"name": "Implement", "agents": ["backend-engineer"]}],
            )
        codes = [d.code for d in ei.value.defects]
        assert "generic_placeholder" in codes


class TestGatePolicy:
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
        with patch.object(stage, "_detect_defects") as detect:
            detect.return_value = [
                PlanDefect(code="empty_plan", severity="critical", message="x")
            ]
            planner = IntelligentPlanner()
            services = _stub_services(planner)
            draft = PlanDraft.from_inputs("Add foo")
            draft.plan_phases = []
            draft.review_result = None
            with patch.object(stage, "_check_scores", return_value="standard"):
                with patch.object(stage, "_consolidate_team", return_value=([], None)):
                    with pytest.raises(PlanQualityError) as ei:
                        stage.run(draft, services)
                    assert "empty_plan" in str(ei.value)

    def test_legacy_hard_gate_env_defaults_false(self) -> None:
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
