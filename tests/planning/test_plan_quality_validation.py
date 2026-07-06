"""Plan-quality validation gates for actionable planner defects."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.stages.risk import RiskStage
from agent_baton.core.engine.planning.stages.validation import (
    PlanDefect,
    PlanQualityError,
    ValidationStage,
    validate_assembled_plan,
)
from agent_baton.core.engine.planning.utils.risk_and_policy import (
    audit_coverage_requirement,
    requires_audit_coverage,
)
from agent_baton.core.govern.classifier import ClassificationResult, DataClassifier
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


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


def _pack_classification(risk: object) -> SimpleNamespace:
    """A pack-classified result (guardrail_preset='pack:<name>')."""
    return SimpleNamespace(
        guardrail_preset="pack:phi-hipaa",
        risk_level=risk,
        signals_found=["path:phi/"],
        explanation="",
        confidence="high",
    )


class TestA1PackClassifiedAuditCoverage:
    """A1: pack-classified regulated tasks must pull in audit coverage.

    Pack classification sets ``guardrail_preset='pack:<name>'`` (not the
    ``Regulated Data`` literal), so the exact-match branch missed them and
    both RiskStage auditor injection and the ValidationStage audit gate were
    bypassed for pack-classified HIGH/CRITICAL tasks.
    """

    def test_pack_high_risk_enum_requires_audit(self) -> None:
        reason = audit_coverage_requirement("benign task", _pack_classification(RiskLevel.HIGH))
        assert reason == "guardrail_preset=pack:phi-hipaa"
        assert requires_audit_coverage("benign task", _pack_classification(RiskLevel.CRITICAL))

    def test_pack_risk_as_string_is_coerced(self) -> None:
        # _classification_from_plan can hand back a string risk_level.
        assert requires_audit_coverage("benign task", _pack_classification("HIGH"))
        assert requires_audit_coverage("benign task", _pack_classification("CRITICAL"))

    def test_pack_low_or_medium_does_not_require_audit(self) -> None:
        # Risk-gated: only HIGH/CRITICAL pack tasks pull in the auditor.
        assert audit_coverage_requirement("benign task", _pack_classification(RiskLevel.MEDIUM)) is None
        assert audit_coverage_requirement("benign task", _pack_classification(RiskLevel.LOW)) is None

    def test_riskstage_injects_auditor_for_pack_high(self) -> None:
        # Benign summary (no audit keyword) so injection can only come from
        # the pack-preset branch, proving RiskStage now covers pack tasks.
        draft = _draft_with_phase(task_summary="Adjust widget layout", risk=RiskLevel.HIGH)
        draft.resolved_agents = ["backend-engineer"]
        draft.classification = _pack_classification(RiskLevel.HIGH)

        RiskStage()._ensure_safety_roster(draft)

        assert "auditor" in draft.resolved_agents

    def test_validation_gate_blocks_pack_plan_without_audit(self) -> None:
        draft = _draft_with_phase(task_summary="Adjust widget layout", risk=RiskLevel.HIGH)
        draft.resolved_agents = ["backend-engineer"]
        draft.classification = _pack_classification(RiskLevel.HIGH)

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "audit_missing" for d in defects)


class TestA2NestedTeamCoverage:
    """A2: sub_team is unbounded — coverage/mismatch checks must recurse."""

    @staticmethod
    def _depth2_step(deep_agent: str) -> PlanStep:
        deep = TeamMember(member_id="m3", agent_name=deep_agent, role="implementer")
        mid = TeamMember(member_id="m2", agent_name="backend-engineer", role="lead", sub_team=[deep])
        lead = TeamMember(member_id="m1", agent_name="backend-engineer", role="lead", sub_team=[mid])
        return PlanStep(
            step_id="1.1",
            agent_name="team",
            task_description="Team step with a depth-2 sub-team member.",
            team=[lead],
        )

    def test_step_agent_bases_finds_depth2_member(self) -> None:
        step = self._depth2_step("auditor")
        assert "auditor" in ValidationStage()._step_agent_bases(step)

    def test_depth2_auditor_satisfies_audit_coverage(self) -> None:
        # Regulated task requires audit; auditor buried at depth-2 in the
        # Audit phase must be found (else a false audit_missing hard-block).
        draft = _draft_with_phase(task_summary="Update GDPR compliance export")
        draft.resolved_agents = ["backend-engineer", "auditor"]
        draft.plan_phases.append(
            PlanPhase(phase_id=2, name="Audit", steps=[self._depth2_step("auditor")])
        )

        defects = ValidationStage()._detect_defects(draft)

        assert not any(d.code == "audit_missing" for d in defects)

    def test_depth2_reviewer_in_implement_is_flagged(self) -> None:
        # A reviewer buried at depth-2 in an Implement phase must not evade
        # the reviewer-in-implement mismatch check.
        draft = _draft_with_phase(phase_name="Implement")
        draft.plan_phases = [
            PlanPhase(phase_id=1, name="Implement", steps=[self._depth2_step("code-reviewer")])
        ]

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "agent_phase_mismatch" for d in defects)


class TestA3HeadlessAuditParity:
    """A3: validate_assembled_plan (forge/headless) hard-fails with an
    actionable defect instead of auto-remediating — outcome parity with the
    interactive RiskStage path is achieved by regenerating, not injecting."""

    def test_assembled_plan_missing_auditor_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BATON_DEV_MODE", raising=False)
        monkeypatch.delenv("BATON_PLANNER_WARN_ONLY", raising=False)
        monkeypatch.setenv("BATON_PLANNER_HARD_GATE", "1")

        plan = MachinePlan(
            task_id="task-a3-headless",
            task_summary="Update GDPR compliance data export workflow",
            risk_level="HIGH",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="Implement the export change.",
                        )
                    ],
                )
            ],
            task_type="feature",
            complexity="medium",
        )

        with pytest.raises(PlanQualityError) as ei:
            validate_assembled_plan(plan, services=_stub_services())

        audit_defects = [d for d in ei.value.defects if d.code == "audit_missing"]
        assert audit_defects, "headless path must surface an audit_missing defect"
        message = audit_defects[0].message
        # Actionable: names the auditor agent + Audit phase remediation and
        # documents that parity is outcome parity (auditor on both paths).
        assert "auditor" in message
        assert "Audit" in message
        assert "parity" in message
        assert "Remediation:" in message


class TestA4PolicyFailureVisible:
    """A4: a policy-engine failure must be visible (logged + surfaced on
    score_warnings), not silently swallowed — the silent branch voided the
    policy-driven audit-requirement path."""

    def test_policy_engine_error_is_recorded_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dataclasses

        class _BoomEngine:
            def load_preset(self, name: str) -> object:
                raise RuntimeError("boom")

        services = dataclasses.replace(_stub_services(), policy_engine=_BoomEngine())
        draft = _draft_with_phase()

        # Must not raise — planning stays alive.
        ValidationStage()._check_scores(draft=draft, services=services)

        assert any("policy_validation_failed" in w for w in draft.score_warnings)
        assert any("boom" in w for w in draft.score_warnings)


class TestA5DraftPhaseReviewerCheck:
    """A5: the documentation archetype's Draft phase is an implement phase —
    reviewer-class agents in it must be flagged."""

    def test_reviewer_in_draft_phase_is_mismatch(self) -> None:
        draft = _draft_with_phase(phase_name="Draft", agent_name="code-reviewer")
        draft.resolved_agents = ["code-reviewer"]

        defects = ValidationStage()._detect_defects(draft)

        assert any(d.code == "agent_phase_mismatch" for d in defects)
