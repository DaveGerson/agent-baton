"""Tests for WS3: Governance Runtime Enforcement.

Covers:
  - Part 1: Policy pre-dispatch enforcement (block-severity → APPROVAL injection)
  - Part 2: Compliance audit trail (dispatch, policy-violation, gate events)
  - Part 3 invariant: knowledge_gap escalation path is not touched
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.executor import (
    ExecutionEngine,
    _build_policy_approval_context,
    _risk_level_to_preset,
)
from agent_baton.core.govern.policy import PolicyEngine, PolicyRule, PolicySet, PolicyViolation
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement X",
    allowed_paths: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model="sonnet",
        deliverables=[],
        allowed_paths=allowed_paths or [],
        context_files=[],
    )


def _phase(
    phase_id: int = 1,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-ws3",
    risk_level: str = "LOW",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="WS3 governance test",
        risk_level=risk_level,
        phases=phases or [_phase()],
    )


def _block_rule(name: str = "block_env", pattern: str = "**/.env") -> PolicyRule:
    return PolicyRule(
        name=name,
        description="Block .env writes",
        scope="all",
        rule_type="path_block",
        pattern=pattern,
        severity="block",
    )


def _warn_rule(name: str = "warn_data", pattern: str = "**/data/**") -> PolicyRule:
    return PolicyRule(
        name=name,
        description="Warn on data writes",
        scope="all",
        rule_type="path_block",
        pattern=pattern,
        severity="warn",
    )


def _policy_set_with_block() -> PolicySet:
    return PolicySet(
        name="standard_dev",
        description="Test preset with one block rule",
        rules=[_block_rule()],
    )


def _policy_set_warn_only() -> PolicySet:
    return PolicySet(
        name="standard_dev",
        description="Test preset with only warn rules",
        rules=[_warn_rule()],
    )


def _mock_policy_engine(preset: PolicySet) -> MagicMock:
    """Return a PolicyEngine mock that always returns *preset* from load_preset."""
    engine = MagicMock(spec=PolicyEngine)
    engine.load_preset.return_value = preset
    # Use the real evaluate() method so we exercise actual rule logic.
    real_engine = PolicyEngine()
    engine.evaluate.side_effect = real_engine.evaluate
    return engine


def _engine_with_policy(tmp_path: Path, policy_engine) -> ExecutionEngine:
    return ExecutionEngine(
        team_context_root=tmp_path,
        policy_engine=policy_engine,
    )


# ---------------------------------------------------------------------------
# Part 1: Policy block → APPROVAL injection
# ---------------------------------------------------------------------------

class TestPolicyBlockInjection:
    """A block-severity violation before dispatch must produce an APPROVAL action."""

    def test_block_violation_returns_approval_not_dispatch(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert action.action_type == ActionType.APPROVAL

    def test_approval_message_names_step(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", agent_name="backend-engineer", allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert "1.1" in action.message
        assert "backend-engineer" in action.message

    def test_approval_context_lists_block_violation(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert "block_env" in action.approval_context

    def test_approval_phase_id_is_sentinel(self, tmp_path: Path) -> None:
        """Policy-block approvals use phase_id=-1 to distinguish from phase approvals."""
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert action.phase_id == -1

    def test_approval_options_are_approve_and_reject_only(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert set(action.approval_options) == {"approve", "reject"}

    def test_step_id_carried_in_summary_for_routing(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert action.summary == "1.1"

    def test_warn_only_violation_does_not_block_dispatch(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=["data/report.csv"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_warn_only()))
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

    def test_no_policy_engine_always_dispatches(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(_plan())
        assert action.action_type == ActionType.DISPATCH

    def test_clean_step_dispatches_when_policy_engine_set(self, tmp_path: Path) -> None:
        """A step with no matching violations should still get DISPATCH."""
        step = _step(allowed_paths=["src/main.py"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH


class TestPolicyApprovalUnblock:
    """record_policy_approval('approve') lets the step proceed on next next_action()."""

    def test_approve_unblocks_step(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        engine.record_policy_approval("1.1", "approve")
        # The engine's in-memory approved set should now include the step.
        assert "1.1" in engine._policy_approved_steps

    def test_after_approve_next_action_returns_dispatch(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        engine.record_policy_approval("1.1", "approve")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH

    def test_reject_marks_step_failed(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        engine.record_policy_approval("1.1", "reject")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_invalid_result_raises_value_error(self, tmp_path: Path) -> None:
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(_plan())
        with pytest.raises(ValueError, match="Invalid policy approval result"):
            engine.record_policy_approval("1.1", "approve-with-feedback")


# ---------------------------------------------------------------------------
# Part 1: policy check failure safety — engine must not crash
# ---------------------------------------------------------------------------

class TestPolicyCheckSafety:
    """A failing policy check must never crash the engine."""

    def test_policy_engine_exception_falls_through_to_dispatch(self, tmp_path: Path) -> None:
        bad_engine = MagicMock(spec=PolicyEngine)
        bad_engine.load_preset.side_effect = RuntimeError("unexpected error")
        engine = _engine_with_policy(tmp_path, bad_engine)
        action = engine.start(_plan())
        # Exception is swallowed; dispatch proceeds normally.
        assert action.action_type == ActionType.DISPATCH


# ---------------------------------------------------------------------------
# Part 2: Compliance audit trail
# ---------------------------------------------------------------------------

def _read_compliance_log(tmp_path: Path) -> list[dict]:
    log_path = tmp_path / "compliance-audit.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lines if l.strip()]


class TestComplianceDispatchEntry:
    """Every agent dispatch writes a compliance audit entry."""

    def test_dispatch_writes_audit_entry(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        entries = _read_compliance_log(tmp_path)
        dispatch_entries = [e for e in entries if e["event_type"] == "agent_dispatch"]
        assert len(dispatch_entries) >= 1

    def test_dispatch_entry_has_required_fields(self, tmp_path: Path) -> None:
        plan = _plan(task_id="task-audit-001")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "agent_dispatch")
        assert entry["task_id"] == "task-audit-001"
        assert entry["step_id"] == "1.1"
        assert entry["agent_name"] == "backend-engineer"
        assert "timestamp" in entry
        assert "risk_level" in entry
        assert "policy_context" in entry

    def test_dispatch_entry_records_policy_context(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=["src/main.py"])
        plan = _plan(risk_level="LOW", phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "agent_dispatch")
        # policy_context carries the preset name derived from risk_level
        assert entry["policy_context"] == "standard_dev"


class TestCompliancePolicyViolationEntry:
    """Policy violations (block or warn) must write a policy_violation audit entry."""

    def test_block_violation_writes_policy_event(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        violation_entries = [e for e in entries if e["event_type"] == "policy_violation"]
        assert len(violation_entries) >= 1

    def test_policy_violation_entry_has_required_fields(self, tmp_path: Path) -> None:
        step = _step(step_id="1.1", agent_name="backend-engineer", allowed_paths=[".env"])
        plan = _plan(task_id="task-policy", phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "policy_violation")
        assert entry["task_id"] == "task-policy"
        assert entry["step_id"] == "1.1"
        assert entry["agent_name"] == "backend-engineer"
        assert "violations" in entry
        assert len(entry["violations"]) >= 1
        assert entry["action_taken"] == "block_approval"

    def test_policy_violation_entry_includes_rule_name(self, tmp_path: Path) -> None:
        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "policy_violation")
        rule_names = [v["rule_name"] for v in entry["violations"]]
        assert "block_env" in rule_names

    def test_warn_only_violation_writes_policy_event_with_warn_action(
        self, tmp_path: Path
    ) -> None:
        step = _step(allowed_paths=["data/report.csv"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_warn_only()))
        engine.start(plan)
        entries = _read_compliance_log(tmp_path)
        violation_entries = [e for e in entries if e["event_type"] == "policy_violation"]
        assert len(violation_entries) >= 1
        assert violation_entries[0]["action_taken"] == "warn"


class TestComplianceGateEntry:
    """Gate pass and fail events must write gate_result audit entries."""

    def test_gate_pass_writes_compliance_entry(self, tmp_path: Path) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        plan = _plan(phases=[_phase(gate=gate)])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", "complete", "done")
        engine.record_gate_result(phase_id=1, passed=True, output="all passed")
        entries = _read_compliance_log(tmp_path)
        gate_entries = [e for e in entries if e["event_type"] == "gate_result"]
        assert len(gate_entries) >= 1

    def test_gate_entry_has_required_fields(self, tmp_path: Path) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        plan = _plan(task_id="task-gate", phases=[_phase(gate=gate)])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", "complete", "done")
        engine.record_gate_result(phase_id=1, passed=True, output="ok")
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "gate_result")
        assert entry["task_id"] == "task-gate"
        assert entry["gate_type"] == "test"
        assert entry["passed"] is True
        assert "timestamp" in entry
        assert "risk_level" in entry

    def test_gate_fail_writes_compliance_entry_with_passed_false(
        self, tmp_path: Path
    ) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        plan = _plan(phases=[_phase(gate=gate)])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", "complete", "done")
        engine.record_gate_result(phase_id=1, passed=False, output="3 failed")
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "gate_result")
        assert entry["passed"] is False

    def test_gate_entry_output_is_truncated_to_500_chars(self, tmp_path: Path) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        plan = _plan(phases=[_phase(gate=gate)])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", "complete", "done")
        long_output = "x" * 2000
        engine.record_gate_result(phase_id=1, passed=True, output=long_output)
        entries = _read_compliance_log(tmp_path)
        entry = next(e for e in entries if e["event_type"] == "gate_result")
        assert len(entry["output_snippet"]) <= 500


class TestComplianceLogResilience:
    """Compliance write failure must never block execution (default mode)."""

    def test_bad_log_path_does_not_crash_dispatch(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        # Point log at a path that cannot be written (file as directory name).
        engine._compliance_log_path = tmp_path / "nonexistent_dir" / "sub" / "audit.jsonl"
        # The mkdir inside _write_compliance_entry should create it — so use a
        # read-only parent to force failure.
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        engine._compliance_log_path = ro_dir / "nested" / "audit.jsonl"
        try:
            action = engine.start(_plan())
            # Dispatch must still succeed despite log write failure.
            assert action.action_type == ActionType.DISPATCH
        finally:
            ro_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# Hole 2: BATON_COMPLIANCE_FAIL_CLOSED behavior
# ---------------------------------------------------------------------------

class TestComplianceFailClosed:
    """``BATON_COMPLIANCE_FAIL_CLOSED=1`` must halt + raise on write failure.

    Default (off) keeps the historical best-effort behavior — but Hole 2 also
    upgrades the silent default to emit a bead warning so the failure is
    visible in the audit trail rather than buried in logs.
    """

    def _force_write_to_raise(self, engine: ExecutionEngine) -> None:
        """Patch the compliance writer to always raise OSError."""
        # Point at a non-writable path.  The chain writer will attempt to
        # mkdir and open it, raising PermissionError or OSError.  We use
        # monkeypatching on the writer module to make the failure deterministic.
        import agent_baton.core.engine.executor as _exec_mod

        original = _exec_mod.ComplianceChainWriter

        class _FailingWriter:
            def __init__(self, *args, **kwargs):
                pass

            def append(self, _entry):
                raise OSError("simulated compliance write failure")

        engine._patched_writer_orig = original
        _exec_mod.ComplianceChainWriter = _FailingWriter

    def _restore_writer(self, engine: ExecutionEngine) -> None:
        import agent_baton.core.engine.executor as _exec_mod

        if hasattr(engine, "_patched_writer_orig"):
            _exec_mod.ComplianceChainWriter = engine._patched_writer_orig

    def test_default_mode_continues_and_emits_bead_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default mode: write failure is logged + bead-warned, execution continues."""
        monkeypatch.delenv("BATON_COMPLIANCE_FAIL_CLOSED", raising=False)

        engine = ExecutionEngine(team_context_root=tmp_path)
        # Capture bead warnings.
        bead_calls: list[dict] = []

        def _capture_bead(*, exc, log_path, fail_closed, entry):
            bead_calls.append(
                {
                    "exc_type": type(exc).__name__,
                    "fail_closed": fail_closed,
                    "event_type": entry.get("event_type"),
                }
            )

        monkeypatch.setattr(
            engine, "_file_compliance_bead_warning", _capture_bead
        )
        self._force_write_to_raise(engine)
        try:
            # Direct call so we don't need to set up a full plan execution.
            engine._write_compliance_entry(
                {
                    "timestamp": "now",
                    "event_type": "agent_dispatch",
                    "task_id": "t1",
                    "plan_id": "t1",
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                }
            )
        finally:
            self._restore_writer(engine)

        # Default mode: no exception raised, execution continued.
        # Bead warning was emitted with fail_closed=False.
        assert len(bead_calls) == 1
        assert bead_calls[0]["fail_closed"] is False
        assert bead_calls[0]["exc_type"] == "OSError"
        assert bead_calls[0]["event_type"] == "agent_dispatch"

    def test_fail_closed_raises_compliance_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed mode: ComplianceWriteError is raised."""
        from agent_baton.core.engine.errors import ComplianceWriteError

        monkeypatch.setenv("BATON_COMPLIANCE_FAIL_CLOSED", "1")

        engine = ExecutionEngine(team_context_root=tmp_path)
        self._force_write_to_raise(engine)
        try:
            with pytest.raises(ComplianceWriteError) as exc_info:
                engine._write_compliance_entry(
                    {
                        "timestamp": "now",
                        "event_type": "agent_dispatch",
                        "task_id": "t-fail-closed",
                        "plan_id": "t-fail-closed",
                        "step_id": "1.1",
                        "agent_name": "backend-engineer",
                    }
                )
        finally:
            self._restore_writer(engine)

        # The wrapped exception preserves the underlying error and log path.
        assert isinstance(exc_info.value.underlying, OSError)
        assert "compliance" in str(exc_info.value).lower()

    def test_fail_closed_marks_execution_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed mode: state.status flips to 'failed' before raising."""
        from agent_baton.core.engine.errors import ComplianceWriteError

        # Build an engine with a real plan so _save/_load_execution work.
        monkeypatch.delenv("BATON_COMPLIANCE_FAIL_CLOSED", raising=False)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        # Sanity: state is running before the write failure.
        state = engine._load_execution()
        assert state is not None
        assert state.status == "running"

        # Now turn on fail-closed and force the next compliance write to fail.
        monkeypatch.setenv("BATON_COMPLIANCE_FAIL_CLOSED", "1")
        self._force_write_to_raise(engine)
        try:
            with pytest.raises(ComplianceWriteError):
                engine._write_compliance_entry(
                    {
                        "timestamp": "now",
                        "event_type": "agent_dispatch",
                        "task_id": state.task_id,
                        "plan_id": state.plan.task_id,
                        "step_id": "1.1",
                        "agent_name": "backend-engineer",
                    }
                )
        finally:
            self._restore_writer(engine)

        # State must be marked failed with a clear status reason.
        post_state = engine._load_execution()
        assert post_state is not None
        assert post_state.status == "failed"
        assert "compliance_write_failed" in post_state.override_justification


# ---------------------------------------------------------------------------
# Part 3 invariant: escalation paths stay separate
# ---------------------------------------------------------------------------

class TestEscalationPathsSeparate:
    """The knowledge_gap escalation path must not be touched by governance code."""

    def test_determine_escalation_still_importable(self) -> None:
        from agent_baton.core.engine.knowledge_gap import determine_escalation
        assert callable(determine_escalation)

    def test_escalation_manager_still_importable(self) -> None:
        from agent_baton.core.govern.escalation import EscalationManager
        assert EscalationManager is not None

    def test_record_policy_approval_does_not_call_determine_escalation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ensure the two escalation paths never share a call chain."""
        called: list[bool] = []

        def _spy(*args, **kwargs):
            called.append(True)
            # Call real implementation so nothing else breaks.
            from agent_baton.core.engine.knowledge_gap import determine_escalation as _real
            return _real(*args, **kwargs)

        monkeypatch.setattr(
            "agent_baton.core.engine.executor.determine_escalation",
            _spy,
            raising=False,
        )

        step = _step(allowed_paths=[".env"])
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine_with_policy(tmp_path, _mock_policy_engine(_policy_set_with_block()))
        engine.start(plan)
        engine.record_policy_approval("1.1", "approve")

        assert not called, (
            "record_policy_approval() must not invoke determine_escalation()"
        )


# ---------------------------------------------------------------------------
# Module-level helper unit tests
# ---------------------------------------------------------------------------

class TestRiskLevelToPreset:
    @pytest.mark.parametrize("risk,expected", [
        ("LOW", "standard_dev"),
        ("MEDIUM", "standard_dev"),
        ("HIGH", "regulated"),
        ("CRITICAL", "regulated"),
        ("low", "standard_dev"),
        ("UNKNOWN", "standard_dev"),
    ])
    def test_mapping(self, risk: str, expected: str) -> None:
        assert _risk_level_to_preset(risk) == expected


class TestBuildPolicyApprovalContext:
    def test_contains_step_id(self) -> None:
        step = _step(step_id="2.3")
        block = [PolicyViolation("backend-engineer", _block_rule(), "path matched")]
        ctx = _build_policy_approval_context(step, block, [], "standard_dev")
        assert "2.3" in ctx

    def test_contains_rule_name(self) -> None:
        step = _step()
        block = [PolicyViolation("backend-engineer", _block_rule(name="block_secrets"), "match")]
        ctx = _build_policy_approval_context(step, block, [], "standard_dev")
        assert "block_secrets" in ctx

    def test_contains_preset_name(self) -> None:
        step = _step()
        block = [PolicyViolation("backend-engineer", _block_rule(), "match")]
        ctx = _build_policy_approval_context(step, block, [], "infrastructure")
        assert "infrastructure" in ctx

    def test_warn_section_absent_when_no_warns(self) -> None:
        step = _step()
        block = [PolicyViolation("backend-engineer", _block_rule(), "match")]
        ctx = _build_policy_approval_context(step, block, [], "standard_dev")
        assert "Warn-severity" not in ctx

    def test_warn_section_present_when_warns_provided(self) -> None:
        step = _step()
        block = [PolicyViolation("backend-engineer", _block_rule(), "match")]
        warn = [PolicyViolation("backend-engineer", _warn_rule(), "advisory")]
        ctx = _build_policy_approval_context(step, block, warn, "standard_dev")
        assert "Warn-severity" in ctx
