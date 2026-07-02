"""Plan-quality validation gates for actionable planner defects."""
from __future__ import annotations

from types import SimpleNamespace
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
from agent_baton.core.govern.classifier import ClassificationResult, DataClassifier
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import PlanPhase, PlanStep


def _stub_services() -> PlannerServices:
    planner = IntelligentPlanner()
    return planner._build_services(knowledge_registry=planner.knowledge_registry)


def _draft_with_phase(
    *,
    task_summary: str = "Ship an auth change",
    risk: RiskLevel = RiskLevel.LOW,
    phase_name: str = "Implement",
    agent_name: str = "backend-engineer",
) -> PlanDraft:
    draft = PlanDraft.from_inputs(task_summary)
    draft.task_id = "task-plan-quality"
    draft.risk_level_enum = risk
    draft.risk_level = risk.value
    draft.inferred_complexity = "medium"
    draft.resolved_agents = [agent_name]
    draft.plan_phases = [
        PlanPhase(
            phase_id=1,
            name=phase_name,
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name=agent_name,
                    task_description="Implement the requested change.",
                )
            ],
        )
    ]
    draft.review_result = None
    return draft


def _run_stage_with_critical_defect(stage: ValidationStage, draft: PlanDraft) -> None:
    with patch.object(stage, "_detect_defects") as detect:
        detect.return_value = [
            PlanDefect(
                code="empty_plan",
                severity="critical",
                message=(
                    "task_id=task-plan-quality phase_count=0. "
                    "Remediation: add phases."
                ),
            )
        ]
        with patch.object(stage, "_check_scores", return_value="standard"):
            with patch.object(stage, "_consolidate_team", return_value=([], None)):
                stage.run(draft, _stub_services())


class TestDefaultGatePolicy:
    def test_critical_defect_blocks_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BATON_PLANNER_HARD_GATE", raising=False)
        monkeypatch.delenv("BATON_DEV_MODE", raising=False)
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        stage = ValidationStage()
        draft = _draft_with_phase()

        with pytest.raises(PlanQualityError) as ei:
            _run_stage_with_critical_defect(stage, draft)

        assert "empty_plan" in str(ei.value)
        assert "Remediation:" in str(ei.value)

    def test_hard_gate_zero_does_not_disable_default_blocking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_PLANNER_HARD_GATE", "0")
        monkeypatch.delenv("BATON_DEV_MODE", raising=False)
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        stage = ValidationStage()
        draft = _draft_with_phase()

        with pytest.raises(PlanQualityError):
            _run_stage_with_critical_defect(stage, draft)

    @pytest.mark.parametrize(
        ("env_name", "env_value"),
        [("BATON_DEV_MODE", "1"), ("BATON_PLANNER_WARN_ONLY", "1")],
    )
    def test_explicit_warn_only_modes_allow_critical_defects_to_warn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_name: str,
        env_value: str,
    ) -> None:
        monkeypatch.delenv("BATON_PLANNER_HARD_GATE", raising=False)
        monkeypatch.delenv("BATON_DEV_MODE", raising=False)
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        monkeypatch.setenv(env_name, env_value)
        stage = ValidationStage()
        draft = _draft_with_phase()

        _run_stage_with_critical_defect(stage, draft)

        assert any("empty_plan" in warning for warning in draft.score_warnings)

    def test_legacy_hard_gate_overrides_dev_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_PLANNER_HARD_GATE", "1")
        monkeypatch.setenv("BATON_DEV_MODE", "1")
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        stage = ValidationStage()
        draft = _draft_with_phase()

        with pytest.raises(PlanQualityError):
            _run_stage_with_critical_defect(stage, draft)


class TestActionableDefectMessages:
    def test_empty_plan_message_includes_context_and_remediation(self) -> None:
        draft = PlanDraft.from_inputs("Add foo")
        draft.task_id = "task-empty"
        draft.plan_phases = []
        draft.review_result = None

        defects = ValidationStage()._detect_defects(draft)
        message = next(d.message for d in defects if d.code == "empty_plan")

        assert "task-empty" in message
        assert "phase_count=0" in message
        assert "Remediation:" in message
        assert "phase" in message.lower()
        assert "step" in message.lower()

    def test_empty_phase_message_includes_phase_context_and_remediation(self) -> None:
        draft = PlanDraft.from_inputs("Add foo")
        draft.task_id = "task-empty-phase"
        draft.plan_phases = [PlanPhase(phase_id=3, name="Implement", steps=[])]
        draft.review_result = None

        defects = ValidationStage()._detect_defects(draft)
        message = next(d.message for d in defects if d.code == "empty_phase")

        assert "phase_id=3" in message
        assert "Implement" in message
        assert "step_count=0" in message
        assert "Remediation:" in message

    def test_agent_phase_mismatch_message_includes_step_phase_and_remediation(self) -> None:
        draft = _draft_with_phase(agent_name="architect")

        defects = ValidationStage()._detect_defects(draft)
        message = next(d.message for d in defects if d.code == "agent_phase_mismatch")

        assert "phase_id=1" in message
        assert "step_id=1.1" in message
        assert "architect" in message
        assert "Implement" in message
        assert "Remediation:" in message

    def test_review_skipped_message_includes_review_context_and_remediation(self) -> None:
        draft = _draft_with_phase()
        draft.inferred_complexity = "heavy"
        draft.review_result = SimpleNamespace(source="skipped-light", warnings=[])

        defects = ValidationStage()._detect_defects(draft)
        message = next(d.message for d in defects if d.code == "review_skipped")

        assert "task-plan-quality" in message
        assert "source=skipped-light" in message
        assert "complexity=heavy" in message
        assert "Remediation:" in message

    def test_reviewer_critical_warning_appends_remediation_when_absent(self) -> None:
        draft = _draft_with_phase()
        draft.review_result = SimpleNamespace(
            source="reviewed",
            warnings=["[critical] Review phase missing for high-risk plan"],
        )

        defects = ValidationStage()._detect_defects(draft)
        message = next(d.message for d in defects if d.code == "reviewer_warning")

        assert message.startswith("[critical] Review phase missing")
        assert "Remediation:" in message


class TestReviewAuditCoverage:
    def test_pii_classifier_signal_staffs_audit_before_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BATON_DEV_MODE", raising=False)
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        monkeypatch.delenv("BATON_PLANNER_HARD_GATE", raising=False)

        planner = IntelligentPlanner(classifier=DataClassifier())

        plan = planner.create_plan(
            "Build a customer profile export that includes user email address "
            "and SSN fields."
        )

        audit_phase = next(
            phase
            for phase in plan.phases
            if phase.name.lower().split()[-1] == "audit"
        )
        assert any(step.agent_name == "auditor" for step in audit_phase.steps)

    def test_high_risk_plan_without_review_phase_is_critical(self) -> None:
        draft = _draft_with_phase(risk=RiskLevel.HIGH)

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "review_missing" for d in defects)
        message = next(d.message for d in defects if d.code == "review_missing")
        assert "risk=HIGH" in message
        assert "Review" in message
        assert "Remediation:" in message

    def test_compliance_plan_without_audit_phase_is_critical(self) -> None:
        draft = _draft_with_phase(
            task_summary="Update GDPR export compliance workflow",
            agent_name="auditor",
        )
        draft.resolved_agents = ["backend-engineer", "auditor"]

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "audit_missing" for d in defects)
        message = next(d.message for d in defects if d.code == "audit_missing")
        assert "auditor" in message
        assert "Audit" in message
        assert "Remediation:" in message

    def test_regulated_classification_without_audit_phase_is_critical(self) -> None:
        draft = _draft_with_phase(
            task_summary="Update FERPA student records export workflow",
            risk=RiskLevel.HIGH,
            agent_name="backend-engineer",
        )
        draft.classification = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Regulated Data",
            signals_found=["regulated:ferpa"],
            confidence="low",
        )

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "audit_missing" for d in defects)
        message = next(d.message for d in defects if d.code == "audit_missing")
        assert "Regulated Data" in message
        assert "Audit" in message
        assert "Remediation:" in message

    def test_review_phase_without_reviewer_is_missing_coverage(self) -> None:
        draft = _draft_with_phase(risk=RiskLevel.HIGH)
        draft.plan_phases.append(
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="architect",
                        task_description="Review the implementation.",
                    )
                ],
            )
        )

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "review_missing" for d in defects)

    def test_audit_phase_without_auditor_is_missing_coverage(self) -> None:
        draft = _draft_with_phase(
            task_summary="Update GDPR export compliance workflow",
            agent_name="backend-engineer",
        )
        draft.resolved_agents = ["backend-engineer", "auditor"]
        draft.plan_phases.append(
            PlanPhase(
                phase_id=2,
                name="Audit",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Audit the implementation.",
                    )
                ],
            )
        )

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "audit_missing" for d in defects)

    def test_reviewer_agent_in_implementation_phase_without_review_is_mismatch(
        self,
    ) -> None:
        draft = _draft_with_phase(agent_name="code-reviewer")
        draft.resolved_agents = ["backend-engineer", "code-reviewer"]

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "agent_phase_mismatch" for d in defects)
        message = next(d.message for d in defects if d.code == "agent_phase_mismatch")
        assert "code-reviewer" in message
        assert "Review" in message
        assert "Remediation:" in message
