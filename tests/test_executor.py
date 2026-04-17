"""Tests for agent_baton.core.engine.executor.ExecutionEngine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus


# ---------------------------------------------------------------------------
# Plan / phase / step factories
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    model: str = "sonnet",
    deliverables: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    context_files: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
        deliverables=deliverables or [],
        allowed_paths=allowed_paths or [],
        context_files=context_files or [],
    )


def _gate(gate_type: str = "test", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _phase(
    phase_id: int = 0,
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
    task_id: str = "task-001",
    task_summary: str = "Build a thing",
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
    shared_context: str = "",
) -> MachinePlan:
    if phases is None:
        phases = [_phase()]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        phases=phases,
        shared_context=shared_context,
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    """Return a fresh engine backed by *tmp_path*."""
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# start() — basic contract
# ---------------------------------------------------------------------------

class TestStart:
    # DECISION: test_returns_execution_action removed — trivial isinstance check.
    # DECISION: test_state_file_created / test_state_file_is_valid_json removed —
    #   covered by TestStartExecution in test_engine_integration.py.
    # DECISION: three dispatch-field tests collapsed into one parametrized test.

    def test_returns_dispatch_for_first_step(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(_plan())
        assert action.action_type == ActionType.DISPATCH

    @pytest.mark.parametrize("field,step_kw,expected", [
        ("agent_name", {"agent_name": "architect"}, "architect"),
        ("step_id",    {"step_id": "1.1"},           "1.1"),
        ("agent_model", {"model": "opus"},            "opus"),
    ])
    def test_dispatch_carries_field(
        self, tmp_path: Path, field: str, step_kw: dict, expected: str
    ) -> None:
        plan = _plan(phases=[_phase(steps=[_step(**step_kw)])])
        action = _engine(tmp_path).start(plan)
        assert getattr(action, field) == expected

    def test_state_status_is_running(self, tmp_path: Path) -> None:
        _engine(tmp_path).start(_plan())
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert data["status"] == "running"

    def test_plan_with_no_phases_raises_value_error(self, tmp_path: Path) -> None:
        plan = _plan(phases=[])
        with pytest.raises(ValueError, match="Plan has no phases"):
            _engine(tmp_path).start(plan)

    def test_delegation_prompt_contains_task(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step(task="Do something important")])])
        action = _engine(tmp_path).start(plan)
        assert "Do something important" in action.delegation_prompt

    def test_delegation_prompt_contains_shared_context(self, tmp_path: Path) -> None:
        plan = _plan(
            shared_context="Important context here",
            phases=[_phase(steps=[_step()])],
        )
        action = _engine(tmp_path).start(plan)
        assert "Important context here" in action.delegation_prompt

    def test_delegation_prompt_contains_context_files(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step(context_files=["src/main.py"])])])
        action = _engine(tmp_path).start(plan)
        assert "src/main.py" in action.delegation_prompt

    def test_delegation_prompt_does_not_re_inject_claude_md(self, tmp_path: Path) -> None:
        # CLAUDE.md is loaded by Claude Code into every agent's system prompt
        # at SessionStart. Re-injecting it in the per-DISPATCH delegation prompt
        # measurably bloats the orchestrator's cached context on every turn
        # (see docs/token-burn-audit). The line was removed from the dispatcher.
        plan = _plan(phases=[_phase(steps=[_step()])])
        action = _engine(tmp_path).start(plan)
        assert "Read `CLAUDE.md`" not in action.delegation_prompt


# ---------------------------------------------------------------------------
# record_step_result() — state mutation
# ---------------------------------------------------------------------------

class TestRecordStepResult:
    # DECISION: five field-storage tests (outcome, files_changed, tokens, duration,
    #   commit_hash) share identical setup — folded into one parametrized test.
    #   The shared @pytest.fixture for engine+start is inlined via a helper method
    #   to stay within the class (pytest class fixtures require specific scoping).

    @staticmethod
    def _started_engine(tmp_path: Path) -> ExecutionEngine:
        engine = _engine(tmp_path)
        engine.start(_plan())
        return engine

    def test_step_appears_in_completed_ids(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        assert "1.1" in engine._load_state().completed_step_ids

    def test_failed_step_appears_in_failed_ids(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="failed", error="oops")
        assert "1.1" in engine._load_state().failed_step_ids

    @pytest.mark.parametrize("record_kw,attr,expected", [
        ({"outcome": "Feature X done"},           "outcome",           "Feature X done"),
        ({"files_changed": ["a.py", "b.py"]},     "files_changed",     ["a.py", "b.py"]),
        ({"estimated_tokens": 5000},               "estimated_tokens",  5000),
        ({"duration_seconds": 42.5},               "duration_seconds",  42.5),
        ({"commit_hash": "abc123"},                "commit_hash",       "abc123"),
    ])
    def test_step_result_stores_field(
        self, tmp_path: Path, record_kw: dict, attr: str, expected
    ) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", **record_kw)
        result = engine._load_state().get_step_result("1.1")
        assert result is not None
        assert getattr(result, attr) == expected

    def test_state_persisted_to_disk(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer")
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert len(data["step_results"]) == 1

    def test_raises_without_active_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError):
            engine.record_step_result("1.1", "agent")


# ---------------------------------------------------------------------------
# next_action() — state machine progression
# ---------------------------------------------------------------------------

class TestNextAction:
    def test_returns_dispatch_before_first_step_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH

    def test_returns_complete_after_last_step(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_returns_gate_after_all_steps_complete_in_phase(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("test", "pytest"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_gate_action_carries_gate_type(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("lint", "ruff"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.gate_type == "lint"

    def test_gate_action_carries_command(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("build", "make build"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.gate_command == "make build"

    def test_gate_action_carries_phase_id(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.phase_id == 0

    def test_dispatch_second_step_in_phase(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(steps=[_step("1.1"), _step("1.2", agent_name="architect")])]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_returns_failed_when_step_failed(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_returns_no_state_error_without_start(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_complete_action_when_already_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # returns COMPLETE, does not mark complete
        # Manually mark complete
        state = engine._load_state()
        state.status = "complete"
        engine._save_state(state)
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE


# ---------------------------------------------------------------------------
# record_gate_result() — gate pass / fail behaviour
# ---------------------------------------------------------------------------

class TestRecordGateResult:
    # DECISION: shared helper _gate_engine builds a two-phase plan with gate on phase 0
    #   to eliminate repeated plan construction boilerplate.
    # DECISION: gate failures are retryable up to max_gate_retries (default 3).
    #   A single failure sets status="gate_failed" and next_action() returns GATE
    #   with a retry message.  Only after max_gate_retries failures does
    #   next_action() auto-terminate with FAILED.

    @staticmethod
    def _gate_engine(
        tmp_path: Path,
        two_phase: bool = False,
        max_gate_retries: int = 3,
    ) -> ExecutionEngine:
        """Return an engine that has started and completed step 1.1 in a gated phase."""
        if two_phase:
            plan = _plan(phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1")]),
            ])
        else:
            plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")], gate=_gate())])
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            max_gate_retries=max_gate_retries,
        )
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        return engine

    def test_passed_gate_advances_phase(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path, two_phase=True)
        engine.next_action()  # returns GATE
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        assert state.current_phase == 1
        assert state.status == "running"

    def test_gate_failure_first_attempt_sets_status_gate_failed(self, tmp_path: Path) -> None:
        """A single gate failure sets status to gate_failed (not failed) — the
        engine keeps the execution alive so the operator can retry."""
        engine = self._gate_engine(tmp_path)
        engine.next_action()  # returns GATE
        engine.record_gate_result(phase_id=0, passed=False, output="tests failed")
        assert engine._load_state().status == "gate_failed"

    def test_gate_result_stored(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path)
        engine.record_gate_result(phase_id=0, passed=True, output="all green")
        state = engine._load_state()
        assert len(state.gate_results) == 1
        assert state.gate_results[0].passed is True
        assert state.gate_results[0].output == "all green"

    def test_gate_failure_first_attempt_returns_retry_gate(self, tmp_path: Path) -> None:
        """After the first gate failure, next_action() returns GATE (not FAILED)
        so the operator can retry without manually intervening."""
        engine = self._gate_engine(tmp_path, max_gate_retries=3)
        engine.record_gate_result(phase_id=0, passed=False)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_gate_failure_exhausts_retries_returns_failed(self, tmp_path: Path) -> None:
        """Once max_gate_retries failures are recorded, next_action() flips to
        FAILED automatically to prevent infinite retry loops."""
        engine = self._gate_engine(tmp_path, max_gate_retries=1)
        engine.record_gate_result(phase_id=0, passed=False)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_raises_without_active_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError):
            engine.record_gate_result(phase_id=0, passed=True)


# ---------------------------------------------------------------------------
# Phase transitions — multi-phase plan
# ---------------------------------------------------------------------------

class TestPhaseTransitions:
    def _two_phase_plan(self) -> MachinePlan:
        return _plan(
            phases=[
                _phase(phase_id=0, name="Phase 1", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1", agent_name="architect")]),
            ]
        )

    def test_dispatches_phase1_step_first(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(self._two_phase_plan())
        assert action.step_id == "1.1"

    def test_gate_triggered_after_phase1_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_dispatches_phase2_step_after_gate_passes(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=0, passed=True)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_complete_after_phase2_step_done(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.record_gate_result(phase_id=0, passed=True)
        engine.next_action()
        engine.record_step_result("2.1", "architect")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_phase_pointer_incremented_after_gate(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        assert state.current_phase == 1


# ---------------------------------------------------------------------------
# complete() — finalisation artefacts
# ---------------------------------------------------------------------------

class TestComplete:
    def test_returns_string(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # COMPLETE action (state not yet marked)
        summary = engine.complete()
        assert isinstance(summary, str)

    def test_summary_contains_task_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="my-task"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        assert "my-task" in summary

    def test_state_status_set_to_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.complete()
        state = engine._load_state()
        assert state.status == "complete"

    def test_retrospective_written(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="retro-test"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.complete()
        retro_dir = tmp_path / "retrospectives"
        assert retro_dir.exists()
        retro_files = list(retro_dir.glob("*.md"))
        assert len(retro_files) == 1

    def test_summary_mentions_steps_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        assert "Steps" in summary or "1" in summary

    def test_no_state_returns_gracefully(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        result = engine.complete()
        assert isinstance(result, str)
        assert "No execution state found" in result

    def test_complete_writes_context_profile_json(self, tmp_path: Path) -> None:
        """complete() auto-invokes ContextProfiler and writes context-profile.json."""
        from agent_baton.core.observe.trace import TraceRecorder
        from agent_baton.models.trace import TraceEvent

        engine = _engine(tmp_path)
        engine.start(_plan(task_id="profile-task"))

        # Inject a file_read trace event so the profiler has data to save.
        tracer = TraceRecorder(team_context_root=tmp_path)
        trace = tracer.start_trace("profile-task")
        trace.events.append(TraceEvent(
            timestamp="2026-03-24T10:00:00+00:00",
            event_type="file_read",
            agent_name="backend-engineer",
            phase=1,
            step=1,
            details={"path": "src/main.py"},
        ))
        tracer.complete_trace(trace)

        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()

        profile_path = tmp_path / "context-profiles" / "profile-task.json"
        assert profile_path.exists(), "context-profile.json not written by complete()"
        import json as _json
        data = _json.loads(profile_path.read_text())
        assert data["task_id"] == "profile-task"
        assert "Context profile" in summary

    def test_complete_context_profiling_is_nonfatal_on_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """complete() must not raise even if ContextProfiler explodes."""
        from agent_baton.core.observe import context_profiler as cp_module

        def _boom(*args, **kwargs):
            raise RuntimeError("profiler exploded")

        monkeypatch.setattr(cp_module.ContextProfiler, "profile_task", _boom)

        engine = _engine(tmp_path)
        engine.start(_plan(task_id="safe-task"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()  # must not raise
        assert isinstance(summary, str)


# ---------------------------------------------------------------------------
# complete() — compliance report generation
# ---------------------------------------------------------------------------

class TestCompleteComplianceReport:
    """complete() must generate a compliance report for HIGH/CRITICAL plans
    and skip it for LOW/MEDIUM plans.  Failures must never block completion.
    """

    @staticmethod
    def _run_to_complete(
        engine: ExecutionEngine,
        task_id: str = "cr-task",
        files: list[str] | None = None,
        commit_hash: str = "",
    ) -> str:
        engine.start(_plan(task_id=task_id, risk_level="HIGH"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            files_changed=files or ["models.py"],
            commit_hash=commit_hash,
        )
        engine.next_action()
        return engine.complete()

    # -- Report written for HIGH risk ----------------------------------------

    def test_compliance_report_written_for_high_risk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(engine, task_id="high-task")
        report_dir = tmp_path / "compliance-reports"
        assert report_dir.exists(), "compliance-reports dir must be created for HIGH risk"
        md_files = list(report_dir.glob("*.md"))
        assert len(md_files) == 1

    def test_compliance_report_written_for_critical_risk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id="critical-task", risk_level="CRITICAL")
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.complete()
        report_dir = tmp_path / "compliance-reports"
        assert report_dir.exists()
        assert len(list(report_dir.glob("*.md"))) == 1

    def test_compliance_report_filename_matches_task_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(engine, task_id="audit-007")
        report_path = tmp_path / "compliance-reports" / "audit-007.md"
        assert report_path.exists()

    def test_compliance_report_content_contains_task_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(engine, task_id="content-check")
        report_path = tmp_path / "compliance-reports" / "content-check.md"
        content = report_path.read_text(encoding="utf-8")
        assert "content-check" in content

    def test_compliance_report_content_contains_risk_level(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(engine, task_id="risk-check")
        report_path = tmp_path / "compliance-reports" / "risk-check.md"
        content = report_path.read_text(encoding="utf-8")
        assert "HIGH" in content

    def test_compliance_report_summary_line_present(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        summary = self._run_to_complete(engine, task_id="summary-check")
        assert "Compliance report:" in summary

    # -- Report NOT written for LOW/MEDIUM risk ------------------------------

    @pytest.mark.parametrize("risk_level", ["LOW", "MEDIUM"])
    def test_compliance_report_skipped_for_low_medium(
        self, tmp_path: Path, risk_level: str
    ) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id=f"low-{risk_level}", risk_level=risk_level)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        report_dir = tmp_path / "compliance-reports"
        # Directory must not be created at all for non-regulated plans.
        assert not report_dir.exists()
        assert "Compliance report:" not in summary

    # -- Entry content -------------------------------------------------------

    def test_compliance_entries_include_files_changed(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(
            engine, task_id="files-check", files=["models.py", "migrations/001.py"]
        )
        content = (tmp_path / "compliance-reports" / "files-check.md").read_text()
        assert "models.py" in content

    def test_compliance_entry_action_modified_when_files_changed(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        self._run_to_complete(
            engine, task_id="action-check", files=["app.py"]
        )
        content = (tmp_path / "compliance-reports" / "action-check.md").read_text()
        assert "modified" in content

    def test_compliance_entry_action_reviewed_when_no_files(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        # complete step with no files_changed
        engine.start(_plan(task_id="reviewed-check", risk_level="HIGH"))
        engine.record_step_result("1.1", "backend-engineer", files_changed=[])
        engine.next_action()
        engine.complete()
        content = (tmp_path / "compliance-reports" / "reviewed-check.md").read_text()
        assert "reviewed" in content

    # -- Fail-graceful -------------------------------------------------------

    def test_compliance_report_failure_does_not_crash_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash inside ComplianceReportGenerator.save() must not propagate."""
        from agent_baton.core.govern import compliance as compliance_mod

        original_save = compliance_mod.ComplianceReportGenerator.save

        def _boom(self, report):  # noqa: ANN001
            raise OSError("Simulated disk failure")

        monkeypatch.setattr(compliance_mod.ComplianceReportGenerator, "save", _boom)

        engine = _engine(tmp_path)
        engine.start(_plan(task_id="safe-cr", risk_level="HIGH"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()  # must not raise
        assert isinstance(summary, str)
        # Path should not appear in summary when generation failed.
        assert "Compliance report:" not in summary

    # -- _should_generate_compliance_report unit tests -----------------------

    @pytest.mark.parametrize("risk_level,expected", [
        ("HIGH",     True),
        ("CRITICAL", True),
        ("LOW",      False),
        ("MEDIUM",   False),
        ("high",     True),   # case-insensitive
        ("critical", True),
    ])
    def test_should_generate_flag(
        self, tmp_path: Path, risk_level: str, expected: bool
    ) -> None:
        from agent_baton.models.execution import ExecutionState
        engine = _engine(tmp_path)
        plan = _plan(risk_level=risk_level)
        state = ExecutionState(task_id="x", plan=plan)
        assert engine._should_generate_compliance_report(state) is expected

    # -- _build_compliance_entries unit tests --------------------------------

    def test_build_entries_count_matches_step_results(self, tmp_path: Path) -> None:
        from agent_baton.models.execution import ExecutionState, StepResult
        engine = _engine(tmp_path)
        plan = _plan(
            risk_level="HIGH",
            phases=[
                _phase(steps=[_step("1.1"), _step("1.2", agent_name="auditor")]),
            ],
        )
        state = ExecutionState(task_id="entries-test", plan=plan)
        state.step_results = [
            StepResult(step_id="1.1", agent_name="backend-engineer",
                       status="complete", files_changed=["a.py"]),
            StepResult(step_id="1.2", agent_name="auditor",
                       status="complete", files_changed=[]),
        ]
        entries = engine._build_compliance_entries(state)
        assert len(entries) == 2

    def test_build_entries_gate_result_propagated(self, tmp_path: Path) -> None:
        from agent_baton.models.execution import ExecutionState, GateResult, StepResult
        engine = _engine(tmp_path)
        # Planner produces 1-based phase_ids; mirror that convention here so
        # the step→phase lookup and gate→phase lookup share the same key space.
        plan = _plan(
            risk_level="HIGH",
            phases=[_phase(phase_id=1, steps=[_step("1.1")])],
        )
        state = ExecutionState(task_id="gate-entries", plan=plan)
        state.step_results = [
            StepResult(step_id="1.1", agent_name="backend-engineer",
                       status="complete", files_changed=["x.py"]),
        ]
        state.gate_results = [GateResult(phase_id=1, gate_type="test", passed=True, output="ok")]
        entries = engine._build_compliance_entries(state)
        assert entries[0].gate_result == "PASS"

    def test_build_entries_failed_step_action_is_failed(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.models.execution import ExecutionState, StepResult
        engine = _engine(tmp_path)
        plan = _plan(risk_level="HIGH")
        state = ExecutionState(task_id="fail-entries", plan=plan)
        state.step_results = [
            StepResult(step_id="1.1", agent_name="backend-engineer",
                       status="failed", files_changed=[]),
        ]
        entries = engine._build_compliance_entries(state)
        assert entries[0].action == "failed"


# ---------------------------------------------------------------------------
# status() — snapshot dict
# ---------------------------------------------------------------------------

class TestStatus:
    # DECISION: test_steps_complete_initially_zero removed — trivial default-value check.
    # DECISION: test_current_phase_key_present + test_elapsed_seconds_present_and_non_negative
    #   folded into one parametrized key-presence test; the non-negative constraint for
    #   elapsed_seconds is preserved as a separate substantive check.

    def test_returns_dict(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        s = engine.status()
        assert isinstance(s, dict)

    def test_task_id_present(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="status-task"))
        s = engine.status()
        assert s["task_id"] == "status-task"

    def test_status_running_on_start(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        s = engine.status()
        assert s["status"] == "running"

    @pytest.mark.parametrize("key", ["current_phase", "elapsed_seconds"])
    def test_numeric_keys_present(self, tmp_path: Path, key: str) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        assert key in engine.status()

    def test_elapsed_seconds_non_negative(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        assert engine.status()["elapsed_seconds"] >= 0.0

    def test_steps_complete_increments(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        assert engine.status()["steps_complete"] == 1

    def test_steps_total_matches_plan(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        assert engine.status()["steps_total"] == 2

    def test_gates_passed_increments(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        assert engine.status()["gates_passed"] == 1

    def test_gates_failed_increments(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=False)
        assert engine.status()["gates_failed"] == 1

    def test_no_state_returns_indicator(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        s = engine.status()
        assert s["status"] == "no_active_execution"


# ---------------------------------------------------------------------------
# resume() — crash recovery
# ---------------------------------------------------------------------------

class TestResume:
    # DECISION: test_resume_returns_action removed — trivial isinstance check;
    #   crash-recovery behaviour is fully covered by TestCrashRecovery in
    #   test_engine_integration.py.

    def test_resume_returns_dispatch_if_step_pending(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        # Crash before recording step result.
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"

    def test_resume_returns_correct_action_after_partial_progress(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(steps=[_step("1.1"), _step("1.2", agent_name="architect")])]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        # Crash after step 1.1.
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_resume_returns_gate_if_pending(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        # Manually set gate_pending to simulate crash mid-gate.
        state = engine._load_state()
        state.status = "gate_pending"
        engine._save_state(state)
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.GATE

    def test_resume_returns_failed_on_no_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        action = engine.resume()
        assert action.action_type == ActionType.FAILED

    def test_resume_picks_up_completed_steps(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        resumed = _engine(tmp_path)
        action = resumed.resume()
        # 1.1 complete, so next should be 1.2
        assert action.step_id == "1.2"


# ---------------------------------------------------------------------------
# Resume: stale execution-state.json vs active-task-id.txt mismatch
# ---------------------------------------------------------------------------

class TestResumeStaleStateFix:
    """Verify that resume() loads the correct task when execution-state.json
    belongs to a different task than active-task-id.txt points to.

    Scenario:
        - Task A is started and its state is saved to execution-state.json.
        - Task B is started (e.g. by an e2e test) and stored in baton.db
          with active-task-id.txt updated to task-B.
        - execution-state.json still contains task A's stale state on disk.
        - ``baton execute resume`` must load task B from SQLite, not task A.
    """

    def test_resume_uses_sqlite_when_file_has_stale_task(self, tmp_path: Path) -> None:
        """SQLite takes precedence over a stale execution-state.json.

        After task-A finishes (its file state is written), task-B is created
        in baton.db and made active.  A new engine created with task_id=task-B
        must resume task-B from SQLite, ignoring the stale file for task-A.
        """
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        from agent_baton.core.engine.persistence import StatePersistence

        db = SqliteStorage(tmp_path / "baton.db")

        # --- Set up task-A via a file-only engine ----------------------------
        engine_a = ExecutionEngine(team_context_root=tmp_path, task_id="task-A")
        plan_a = _plan(task_id="task-A")
        engine_a.start(plan_a)
        # task-A's execution-state.json is now on disk at the legacy flat path
        # (no storage backend, so no SQLite row for task-A).
        stale_legacy = tmp_path / "execution-state.json"
        # Write a copy to the legacy flat file so it looks like the old-style
        # stale artifact that would confuse a file-only fallback.
        state_a_data = json.loads(
            (tmp_path / "executions" / "task-A" / "execution-state.json").read_text()
        )
        stale_legacy.write_text(json.dumps(state_a_data), encoding="utf-8")

        # --- Set up task-B via a SQLite-backed engine -------------------------
        plan_b = _plan(task_id="task-B")
        engine_b = ExecutionEngine(
            team_context_root=tmp_path,
            task_id="task-B",
            storage=db,
        )
        engine_b.start(plan_b)
        # Mark task-B as active in both backends.
        db.set_active_task("task-B")
        StatePersistence.get_active_task_id  # referenced for clarity
        (tmp_path / "active-task-id.txt").write_text("task-B", encoding="utf-8")

        # --- Resume using the active task (task-B) ---------------------------
        # Simulate what the CLI does: read active-task-id.txt → task-B, then
        # build an engine with that task_id and the SQLite storage backend.
        active_id = StatePersistence.get_active_task_id(tmp_path)
        assert active_id == "task-B"

        resume_engine = ExecutionEngine(
            team_context_root=tmp_path,
            task_id=active_id,
            storage=db,
        )
        action = resume_engine.resume()

        # Must resume task-B, not task-A.
        assert action.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH but got {action.action_type}: {action.message!r}"
        )
        assert action.step_id == "1.1"

    def test_resume_discards_stale_file_state_in_file_mode(self, tmp_path: Path) -> None:
        """In file mode, resume() returns FAILED when the file's task_id does
        not match the requested task_id (stale legacy flat file scenario).

        This prevents silently resuming the wrong task when no SQLite backend
        is available to reconstruct from.
        """
        from agent_baton.core.engine.persistence import StatePersistence

        # Write a state file for task-A at the legacy flat-file location.
        engine_a = ExecutionEngine(team_context_root=tmp_path, task_id="task-A")
        engine_a.start(_plan(task_id="task-A"))

        # Manually write task-A's state to the legacy flat file so it appears
        # stale when a task-B engine tries to read it.
        namespaced = tmp_path / "executions" / "task-A" / "execution-state.json"
        legacy = tmp_path / "execution-state.json"
        legacy.write_text(namespaced.read_text(encoding="utf-8"), encoding="utf-8")

        # Mark task-B as active on disk (no SQLite, no namespaced file for task-B).
        (tmp_path / "active-task-id.txt").write_text("task-B", encoding="utf-8")

        # Engine for task-B in file mode: its namespaced file doesn't exist,
        # and the legacy flat file has task-A state — must not resume task-A.
        engine_b = ExecutionEngine(team_context_root=tmp_path, task_id="task-B")
        action = engine_b.resume()

        assert action.action_type == ActionType.FAILED, (
            f"Expected FAILED for stale file state, got {action.action_type!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end: multi-phase plan
# ---------------------------------------------------------------------------

class TestMultiPhaseEndToEnd:
    def test_two_phase_two_step_with_gate_completes(self, tmp_path: Path) -> None:
        """Full happy-path: 2 phases, 1 step each, gate after phase 0."""
        plan = _plan(
            task_id="e2e-task",
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ],
        )
        engine = _engine(tmp_path)

        # Phase 0 — step 1.1
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"

        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=2000)

        # Gate for phase 0
        action = engine.next_action()
        assert action.action_type == ActionType.GATE
        engine.record_gate_result(phase_id=0, passed=True)

        # Phase 1 — step 2.1
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

        engine.record_step_result("2.1", "backend-engineer", estimated_tokens=1500)

        # Complete
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

        summary = engine.complete()
        assert "e2e-task" in summary

    def test_three_phases_no_gates_completes(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")]),
                _phase(phase_id=1, steps=[_step("2.1")]),
                _phase(phase_id=2, steps=[_step("3.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        for step_id in ("1.1", "2.1", "3.1"):
            engine.record_step_result(step_id, "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_gate_failure_exhausts_retries_halts_execution(self, tmp_path: Path) -> None:
        """Gate failure with max_gate_retries=1 terminates the execution on the
        first failure — verifies that the retry cap gates forward progress."""
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ]
        )
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            max_gate_retries=1,
        )
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=0, passed=False)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_step_failure_in_phase2_halts_execution(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        engine.next_action()  # DISPATCH 2.1
        engine.record_step_result("2.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED


# ---------------------------------------------------------------------------
# Failed step handling
# ---------------------------------------------------------------------------

class TestFailedStepHandling:
    def test_failed_step_triggers_failed_action(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed", error="import error")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_failed_action_message_contains_step_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert "1.1" in action.message or "1.1" in action.summary

    def test_status_reflects_failure(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        engine.next_action()  # triggers failed state update
        s = engine.status()
        assert s["status"] == "failed"


# ---------------------------------------------------------------------------
# _build_usage_record — unit tests for the conversion helper
# ---------------------------------------------------------------------------

class TestBuildUsageRecord:
    def test_task_id_matches(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="usage-test"))
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=500)
        state = engine._load_state()
        state.status = "complete"
        state.completed_at = "2026-03-20T10:00:00+00:00"
        record = engine._build_usage_record(state)
        assert record.task_id == "usage-test"

    def test_agents_used_populated(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=1000)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert len(record.agents_used) == 1
        assert record.agents_used[0].name == "backend-engineer"

    def test_tokens_aggregated_per_agent(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=1000)
        engine.record_step_result("1.2", "backend-engineer", estimated_tokens=2000)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.agents_used[0].estimated_tokens == 3000

    def test_outcome_ship_when_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        state = engine._load_state()
        state.status = "complete"
        record = engine._build_usage_record(state)
        assert record.outcome == "SHIP"

    def test_outcome_block_when_failed(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        state = engine._load_state()
        state.status = "failed"
        record = engine._build_usage_record(state)
        assert record.outcome == "BLOCK"

    def test_risk_level_propagated(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(risk_level="HIGH"))
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.risk_level == "HIGH"

    def test_gates_counted(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.gates_passed == 1
        assert record.gates_failed == 0


# ---------------------------------------------------------------------------
# _save_state / _load_state round-trip
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        plan = _plan(task_id="persist-test")
        state = ExecutionState(task_id="persist-test", plan=plan)
        engine = _engine(tmp_path)
        engine._save_state(state)
        loaded = engine._load_state()
        assert loaded is not None
        assert loaded.task_id == "persist-test"

    def test_load_returns_none_when_no_file(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        assert engine._load_state() is None

    def test_load_returns_none_on_corrupted_json(self, tmp_path: Path) -> None:
        (tmp_path / "execution-state.json").write_text("NOT JSON", encoding="utf-8")
        engine = _engine(tmp_path)
        assert engine._load_state() is None

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_root = tmp_path / "deep" / "context"
        plan = _plan()
        state = ExecutionState(task_id="deep-test", plan=plan)
        engine = ExecutionEngine(team_context_root=deep_root)
        engine._save_state(state)
        assert (deep_root / "execution-state.json").exists()

    def test_step_results_persist_through_disk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", outcome="done", commit_hash="aabbcc")
        loaded = engine._load_state()
        assert len(loaded.step_results) == 1
        assert loaded.step_results[0].commit_hash == "aabbcc"

    def test_gate_results_persist_through_disk(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True, output="green")
        loaded = engine._load_state()
        assert len(loaded.gate_results) == 1
        assert loaded.gate_results[0].passed is True

    def test_save_is_atomic_no_tmp_file_left(self, tmp_path: Path) -> None:
        """After save(), no .tmp file remains alongside execution-state.json.

        The atomic write pattern writes to a .json.tmp file first, then
        renames it to the target path.  A successful save must leave exactly
        one file — the final state file — with no leftover .tmp artefacts.
        """
        from agent_baton.core.engine.persistence import StatePersistence

        plan = _plan(task_id="atomic-test")
        state = ExecutionState(task_id="atomic-test", plan=plan)
        sp = StatePersistence(tmp_path)
        sp.save(state)

        # Target file must exist and be valid JSON.
        assert sp.path.exists()
        import json as _json
        data = _json.loads(sp.path.read_text(encoding="utf-8"))
        assert data["task_id"] == "atomic-test"

        # No .tmp file must remain after a successful save.
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    def test_save_overwrites_previous_state(self, tmp_path: Path) -> None:
        """A second save() replaces the state file cleanly with no residue."""
        from agent_baton.core.engine.persistence import StatePersistence
        import json as _json

        plan_a = _plan(task_id="state-a")
        plan_b = _plan(task_id="state-b")
        sp = StatePersistence(tmp_path)

        sp.save(ExecutionState(task_id="state-a", plan=plan_a))
        sp.save(ExecutionState(task_id="state-b", plan=plan_b))

        data = _json.loads(sp.path.read_text(encoding="utf-8"))
        assert data["task_id"] == "state-b"
        assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Phase-advance regression (1-based phase_ids, matching planner output)
# ---------------------------------------------------------------------------

class TestPhaseAdvanceWithOneBasedIds:
    """Regression tests for the gate phase-advance bug.

    The planner creates phases with 1-based phase_ids (1, 2, 3, ...) while
    current_phase is a 0-based index.  The old buggy code used
    ``state.current_phase = phase_id + 1`` which skipped phases when IDs
    were 1-based.
    """

    def test_three_phases_two_gates_no_skip(self, tmp_path: Path) -> None:
        """A→gate→B→gate→C must dispatch all three phases, not skip B."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, name="Design", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, name="Implement", steps=[_step("2.1")], gate=_gate()),
                _phase(phase_id=3, name="Review", steps=[_step("3.1")]),
            ]
        )
        engine = _engine(tmp_path)
        dispatched: list[str] = []

        action = engine.start(plan)
        for _ in range(30):
            if action.action_type == ActionType.COMPLETE:
                break
            if action.action_type == ActionType.FAILED:
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            elif action.action_type == ActionType.GATE:
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert dispatched == ["1.1", "2.1", "3.1"], (
            f"Expected all 3 phases dispatched in order, got {dispatched}"
        )

    def test_gate_on_phase1_reaches_phase2(self, tmp_path: Path) -> None:
        """Gate on 1-based phase_id=1 must advance to phase at index 1."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, steps=[_step("2.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()  # GATE
        assert action.action_type == ActionType.GATE

        engine.record_gate_result(action.phase_id, passed=True)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_four_phases_all_gated_dispatches_all(self, tmp_path: Path) -> None:
        """4 phases each with a gate — every phase must be visited."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, name="A", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, name="B", steps=[_step("2.1")], gate=_gate()),
                _phase(phase_id=3, name="C", steps=[_step("3.1")], gate=_gate()),
                _phase(phase_id=4, name="D", steps=[_step("4.1")]),
            ]
        )
        engine = _engine(tmp_path)
        dispatched = []
        gates_run = []

        action = engine.start(plan)
        for _ in range(40):
            if action.action_type in (ActionType.COMPLETE, ActionType.FAILED):
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            elif action.action_type == ActionType.GATE:
                gates_run.append(action.phase_id)
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert dispatched == ["1.1", "2.1", "3.1", "4.1"]
        assert len(gates_run) == 3


# ---------------------------------------------------------------------------
# Status validation
# ---------------------------------------------------------------------------

class TestStepStatusValidation:
    """record_step_result must reject invalid status values."""

    def test_invalid_status_raises_value_error(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        with pytest.raises(ValueError, match="Invalid step status 'success'"):
            engine.record_step_result("1.1", "backend-engineer", status="success")

    def test_valid_statuses_accepted(self, tmp_path: Path) -> None:
        for status in ("complete", "failed"):
            plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
            engine = _engine(tmp_path)
            engine.start(plan)
            engine.record_step_result("1.1", "backend-engineer", status=status)


# ---------------------------------------------------------------------------
# Bug 7: complete() saves trace to SQLite when storage is available
# ---------------------------------------------------------------------------

class TestCompleteSavesTraceToSQLite:
    """complete() must call storage.save_trace() when a storage backend is set.

    Pipeline Bug 7: previously complete_trace() wrote the trace to the
    filesystem but there was no call to self._storage.save_trace().
    """

    def _run_to_complete(self, engine: ExecutionEngine) -> None:
        """Drive a single-step plan from start through complete()."""
        engine.start(_plan(task_id="trace-task"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # COMPLETE action — does not yet finalize
        engine.complete()

    def _make_passthrough_storage(self, saved_traces: list) -> object:
        """Return a FakeStorage that delegates state persistence to an in-memory
        dict and records save_trace() calls in *saved_traces*.

        ExecutionEngine writes state via save_execution() and reads it back via
        load_execution() / get_active_task().  A FakeStorage that returns None
        from load_execution() causes record_step_result() to raise because there
        is no active state.  This implementation stores the most-recently saved
        ExecutionState so the engine can read it back correctly.
        """
        class FakeStorage:
            def __init__(self):
                self._states: dict[str, object] = {}
                self._active: str | None = None

            def save_execution(self, state):
                self._states[state.task_id] = state

            def load_execution(self, task_id):
                return self._states.get(task_id)

            def get_active_task(self):
                return self._active

            def set_active_task(self, task_id):
                self._active = task_id

            def log_usage(self, record):
                pass

            def log_telemetry(self, event):
                pass

            def save_retrospective(self, retro):
                pass

            def save_trace(self, trace):
                saved_traces.append(trace)

        return FakeStorage()

    def test_save_trace_called_on_storage_when_present(self, tmp_path: Path) -> None:
        """When a storage backend is provided, complete() calls save_trace()."""
        saved_traces: list = []
        storage = self._make_passthrough_storage(saved_traces)
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            storage=storage,
        )
        self._run_to_complete(engine)

        assert len(saved_traces) == 1, (
            "complete() must call storage.save_trace() exactly once"
        )
        trace = saved_traces[0]
        assert trace.task_id == "trace-task"

    def test_save_trace_not_called_when_no_storage(self, tmp_path: Path) -> None:
        """In legacy file mode (no storage), no save_trace() call is made
        (file-only path still works correctly — traces dir written to disk)."""
        engine = _engine(tmp_path)
        self._run_to_complete(engine)
        # Trace must be written to disk in file mode.
        traces_dir = tmp_path / "traces"
        assert traces_dir.exists()
        trace_files = list(traces_dir.glob("*.json"))
        assert len(trace_files) == 1

    def test_save_trace_failure_does_not_crash_complete(self, tmp_path: Path) -> None:
        """A storage.save_trace() exception must not propagate — complete() logs
        a warning and continues, returning the summary string."""
        saved_traces: list = []

        class BrokenTraceStorage:
            def __init__(self):
                self._states: dict[str, object] = {}
                self._active: str | None = None

            def save_execution(self, state):
                self._states[state.task_id] = state

            def load_execution(self, task_id):
                return self._states.get(task_id)

            def get_active_task(self):
                return self._active

            def set_active_task(self, task_id):
                self._active = task_id

            def log_usage(self, record):
                pass

            def log_telemetry(self, event):
                pass

            def save_retrospective(self, retro):
                pass

            def save_trace(self, trace):
                raise RuntimeError("SQLite trace write failed")

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            storage=BrokenTraceStorage(),
        )
        # Must not raise — warning is logged, complete() returns normally.
        engine.start(_plan(task_id="trace-err"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        assert isinstance(summary, str)



# ---------------------------------------------------------------------------
# Parallel step dispatch
# ---------------------------------------------------------------------------

class TestParallelDispatch:
    """Tests for dependency-aware parallel step dispatch."""

    def test_independent_steps_dispatch_in_order(self, tmp_path: Path) -> None:
        """Steps with no depends_on dispatch in phase order."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        action = engine.start(plan)
        assert action.step_id == "1.1"

    def test_dispatched_step_not_redispatched(self, tmp_path: Path) -> None:
        """A step marked as dispatched should not be returned again."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_blocked_step_returns_wait(self, tmp_path: Path) -> None:
        """Step blocked by dependency returns WAIT when all runnable are dispatched."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="depends on 1.1", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.WAIT

    def test_dependency_satisfied_enables_dispatch(self, tmp_path: Path) -> None:
        """Once dependency completes, blocked step becomes dispatchable."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="depends on 1.1", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_next_actions_returns_all_runnable(self, tmp_path: Path) -> None:
        """next_actions() returns all steps with satisfied dependencies."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
            PlanStep(step_id="1.3", agent_name="c",
                     task_description="depends on both", depends_on=["1.1", "1.2"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        actions = engine.next_actions()
        step_ids = {a.step_id for a in actions}
        assert step_ids == {"1.1", "1.2"}
        assert "1.3" not in step_ids

    def test_next_actions_empty_when_all_dispatched(self, tmp_path: Path) -> None:
        """next_actions() returns empty when all runnable steps are dispatched."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1"), _step("1.2"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.mark_dispatched("1.2", "backend-engineer")
        actions = engine.next_actions()
        assert actions == []

    def test_next_actions_unlocks_after_completion(self, tmp_path: Path) -> None:
        """Completing a dependency unlocks blocked steps in next_actions()."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="dep", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "a")
        actions = engine.next_actions()
        assert len(actions) == 1
        assert actions[0].step_id == "1.2"

    def test_dispatched_then_completed_advances(self, tmp_path: Path) -> None:
        """Full parallel flow: dispatch both -> complete both -> gate/complete."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        engine.mark_dispatched("1.2", "b")
        # Both in flight -- should WAIT
        action = engine.next_action()
        assert action.action_type == ActionType.WAIT
        # Complete both
        engine.record_step_result("1.1", "a")
        engine.record_step_result("1.2", "b")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_mark_dispatched_is_valid_status(self, tmp_path: Path) -> None:
        """mark_dispatched uses 'dispatched' status which is accepted."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        state = engine._load_state()
        assert "1.1" in state.dispatched_step_ids

    def test_backward_compat_no_depends_on(self, tmp_path: Path) -> None:
        """Plans without depends_on work exactly as before (sequential dispatch)."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1"), _step("1.2"), _step("1.3"),
        ])])
        engine = _engine(tmp_path)
        dispatched = []
        action = engine.start(plan)
        for _ in range(10):
            if action.action_type == ActionType.COMPLETE:
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            action = engine.next_action()
        assert dispatched == ["1.1", "1.2", "1.3"]


# ---------------------------------------------------------------------------
# FIX-1: EventPersistence wired as bus subscriber in storage mode
# ---------------------------------------------------------------------------

class TestEventPersistenceWiredInStorageMode:
    """EventPersistence must be registered as an EventBus subscriber even when
    a storage backend is active.  Before FIX-1, storage mode unconditionally
    set _event_persistence = None, so domain events were never written to disk.
    """

    def test_event_persistence_subscribed_when_bus_and_storage_provided(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.events.bus import EventBus
        from agent_baton.core.events.persistence import EventPersistence
        from unittest.mock import MagicMock

        bus = EventBus()
        mock_storage = MagicMock()
        mock_storage.load_execution.return_value = None
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=bus,
            task_id="fix1-task",
            storage=mock_storage,
        )
        # _event_persistence must be an EventPersistence instance, not None
        assert engine._event_persistence is not None
        assert isinstance(engine._event_persistence, EventPersistence)

    def test_event_persistence_is_none_when_no_bus_even_with_storage(
        self, tmp_path: Path
    ) -> None:
        """When no bus is provided, there is nothing to subscribe to."""
        from unittest.mock import MagicMock

        mock_storage = MagicMock()
        mock_storage.load_execution.return_value = None
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            task_id="fix1-task-no-bus",
            storage=mock_storage,
        )
        assert engine._event_persistence is None

    def test_events_written_to_jsonl_in_storage_mode(
        self, tmp_path: Path
    ) -> None:
        """Domain events fired during start() are persisted to JSONL when a bus
        is provided alongside a storage backend."""
        from agent_baton.core.events.bus import EventBus
        from agent_baton.core.events.persistence import EventPersistence
        from unittest.mock import MagicMock

        bus = EventBus()
        mock_storage = MagicMock()
        mock_storage.load_execution.return_value = None
        mock_storage.save_execution.return_value = None
        mock_storage.set_active_task.return_value = None

        task_id = "fix1-write-test"
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=bus,
            task_id=task_id,
            storage=mock_storage,
        )
        plan = _plan(task_id=task_id)
        engine.start(plan)

        # Events directory should have been created and contain at least
        # task.started and phase.started events.
        ep: EventPersistence = engine._event_persistence  # type: ignore[assignment]
        events = ep.read(task_id)
        assert len(events) >= 2
        topics = [e.topic for e in events]
        assert "task.started" in topics
        assert "phase.started" in topics

    def test_events_namespaced_under_task_dir_in_storage_mode(
        self, tmp_path: Path
    ) -> None:
        """Events directory path is namespaced under executions/<task_id>/events."""
        from agent_baton.core.events.bus import EventBus
        from unittest.mock import MagicMock

        bus = EventBus()
        mock_storage = MagicMock()
        mock_storage.load_execution.return_value = None
        task_id = "fix1-namespaced"
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=bus,
            task_id=task_id,
            storage=mock_storage,
        )
        expected_dir = tmp_path.resolve() / "executions" / task_id / "events"
        assert engine._event_persistence.events_dir == expected_dir

    def test_events_written_to_jsonl_in_file_mode_unchanged(
        self, tmp_path: Path
    ) -> None:
        """FIX-1 must not break the legacy file-mode wiring — events still
        persist when storage is None and a bus is provided."""
        from agent_baton.core.events.bus import EventBus
        from agent_baton.core.events.persistence import EventPersistence

        bus = EventBus()
        task_id = "fix1-legacy-mode"
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=bus,
            task_id=task_id,
        )
        plan = _plan(task_id=task_id)
        engine.start(plan)

        ep: EventPersistence = engine._event_persistence  # type: ignore[assignment]
        events = ep.read(task_id)
        assert len(events) >= 2
        assert any(e.topic == "task.started" for e in events)


# ---------------------------------------------------------------------------
# FIX-2: KnowledgeResolver constructor parameter on ExecutionEngine
# ---------------------------------------------------------------------------

class TestKnowledgeResolverConstructorInjection:
    """KnowledgeResolver must be injectable via the ExecutionEngine constructor
    so the runtime knowledge gap auto-resolve path fires in production.
    Before FIX-2, _knowledge_resolver was never set in __init__ — only tests
    that assigned it directly after construction could exercise the auto-resolve
    branch.
    """

    def test_knowledge_resolver_defaults_to_none(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        assert engine._knowledge_resolver is None

    def test_knowledge_resolver_set_via_constructor(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mock_resolver = MagicMock()
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            knowledge_resolver=mock_resolver,
        )
        assert engine._knowledge_resolver is mock_resolver

    def test_knowledge_resolver_constructor_injects_into_gap_handling(
        self, tmp_path: Path
    ) -> None:
        """When a KnowledgeResolver that returns attachments is injected via
        constructor, a KNOWLEDGE_GAP signal auto-resolves instead of queuing."""
        from unittest.mock import MagicMock
        from agent_baton.models.knowledge import KnowledgeAttachment

        mock_attachment = KnowledgeAttachment(
            source="planner-matched:tag",
            pack_name="test-pack",
            document_name="api-schema.md",
            path="/docs/api-schema.md",
            delivery="reference",
        )
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = [mock_attachment]

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            knowledge_resolver=mock_resolver,
        )
        plan = _plan(
            risk_level="LOW",
            phases=[_phase(phase_id=1, steps=[_step("1.1")])],
        )
        engine.start(plan)

        outcome_with_gap = (
            "Work in progress.\n"
            "KNOWLEDGE_GAP: Need the API schema for the orders endpoint\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=outcome_with_gap,
        )

        state = engine._load_state()
        assert state is not None
        # Auto-resolved: gap is NOT queued for a human gate
        assert len(state.pending_gaps) == 0
        # A ResolvedDecision was recorded
        assert len(state.resolved_decisions) == 1
        decision = state.resolved_decisions[0]
        assert "orders endpoint" in decision.gap_description
        assert "test-pack/api-schema.md" in decision.resolution

    def test_no_resolver_falls_back_to_best_effort_low_risk(
        self, tmp_path: Path
    ) -> None:
        """Without a resolver, LOW risk factual gaps still resolve to best-effort
        (gap not queued, no ResolvedDecision)."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        plan = _plan(
            risk_level="LOW",
            phases=[_phase(phase_id=1, steps=[_step("1.1")])],
        )
        engine.start(plan)

        outcome_with_gap = (
            "KNOWLEDGE_GAP: Preferred date format for log timestamps\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=outcome_with_gap,
        )

        state = engine._load_state()
        assert state is not None
        assert len(state.pending_gaps) == 0
        assert len(state.resolved_decisions) == 0

    def test_no_resolver_queues_gap_at_high_risk(self, tmp_path: Path) -> None:
        """Without a resolver, HIGH risk factual gaps queue for human review."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        plan = _plan(
            risk_level="HIGH",
            phases=[_phase(phase_id=1, steps=[_step("1.1")])],
        )
        engine.start(plan)

        outcome_with_gap = (
            "KNOWLEDGE_GAP: Compliance requirement for audit trail format\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=outcome_with_gap,
        )

        state = engine._load_state()
        assert state is not None
        assert len(state.pending_gaps) == 1


# ---------------------------------------------------------------------------
# FIX-3: complete() writes a trace even when self._trace is None (CLI mode)
# ---------------------------------------------------------------------------

def _drive_plan_through_fresh_engines(
    tmp_path: Path,
    plan: MachinePlan,
) -> None:
    """Simulate CLI mode: each baton execute call uses a fresh engine instance.

    This is the real failure scenario: start(), record(), and complete()
    all run in separate process invocations so self._trace is always None
    except in the start() call (which never persists it to disk).
    """
    # baton execute start
    engine_start = ExecutionEngine(team_context_root=tmp_path)
    action = engine_start.start(plan)

    # baton execute record (fresh engine per step)
    phase = plan.phases[0]
    for step in phase.steps:
        engine_record = ExecutionEngine(team_context_root=tmp_path)
        engine_record.record_step_result(
            step_id=step.step_id,
            agent_name=step.agent_name,
            status="complete",
            outcome="done",
            files_changed=["src/app.py"],
            duration_seconds=10.0,
        )

    # baton execute complete (fresh engine — self._trace is None)
    engine_complete = ExecutionEngine(team_context_root=tmp_path)
    engine_complete.complete()


class TestCliModeTraceReconstruction:
    """Verify that complete() writes a trace even when run in CLI mode.

    In CLI mode each `baton execute` call creates a fresh ExecutionEngine so
    self._trace is always None.  complete() must reconstruct the trace from
    the persisted ExecutionState.
    """

    def test_trace_file_created_after_cli_mode_complete(
        self, tmp_path: Path
    ) -> None:
        plan = _plan(task_id="cli-trace-001")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        trace_path = tmp_path / "traces" / "cli-trace-001.json"
        assert trace_path.exists(), "trace file must be written by complete()"

    def test_trace_is_valid_json_with_correct_task_id(
        self, tmp_path: Path
    ) -> None:
        import json
        plan = _plan(task_id="cli-trace-002")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        data = json.loads(
            (tmp_path / "traces" / "cli-trace-002.json").read_text()
        )
        assert data["task_id"] == "cli-trace-002"

    def test_trace_contains_step_events(self, tmp_path: Path) -> None:
        import json
        plan = _plan(task_id="cli-trace-003")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        data = json.loads(
            (tmp_path / "traces" / "cli-trace-003.json").read_text()
        )
        event_types = [e["event_type"] for e in data["events"]]
        assert "agent_complete" in event_types

    def test_trace_outcome_is_ship(self, tmp_path: Path) -> None:
        import json
        plan = _plan(task_id="cli-trace-004")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        data = json.loads(
            (tmp_path / "traces" / "cli-trace-004.json").read_text()
        )
        assert data["outcome"] == "SHIP"

    def test_trace_plan_snapshot_populated(self, tmp_path: Path) -> None:
        import json
        plan = _plan(task_id="cli-trace-005")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        data = json.loads(
            (tmp_path / "traces" / "cli-trace-005.json").read_text()
        )
        assert data["plan_snapshot"]  # non-empty dict
        assert data["plan_snapshot"].get("task_id") == "cli-trace-005"

    def test_trace_readable_by_trace_recorder(self, tmp_path: Path) -> None:
        from agent_baton.core.observe.trace import TraceRecorder
        plan = _plan(task_id="cli-trace-006")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        recorder = TraceRecorder(team_context_root=tmp_path)
        trace = recorder.load_trace("cli-trace-006")
        assert trace is not None
        assert trace.task_id == "cli-trace-006"

    def test_trace_get_last_trace_returns_reconstructed(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.observe.trace import TraceRecorder
        plan = _plan(task_id="cli-trace-007")
        _drive_plan_through_fresh_engines(tmp_path, plan)
        recorder = TraceRecorder(team_context_root=tmp_path)
        last = recorder.get_last_trace()
        assert last is not None
        assert last.task_id == "cli-trace-007"

    def test_single_engine_trace_still_written(self, tmp_path: Path) -> None:
        """Regression: single-instance path (daemon mode) must still work."""
        import json
        plan = _plan(task_id="single-engine-trace")
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(plan)
        while action.action_type not in (ActionType.COMPLETE, ActionType.FAILED):
            if action.action_type == ActionType.DISPATCH:
                engine.record_step_result(
                    action.step_id, action.agent_name, status="complete"
                )
            action = engine.next_action()
        engine.complete()
        data = json.loads(
            (tmp_path / "traces" / "single-engine-trace.json").read_text()
        )
        assert data["task_id"] == "single-engine-trace"
        assert data["outcome"] == "SHIP"

    def test_gate_results_appear_in_reconstructed_trace(
        self, tmp_path: Path
    ) -> None:
        import json
        plan = _plan(
            task_id="cli-trace-gate",
            phases=[_phase(phase_id=0, gate=_gate())],
        )
        # start
        engine_start = ExecutionEngine(team_context_root=tmp_path)
        action = engine_start.start(plan)
        # record step
        engine_record = ExecutionEngine(team_context_root=tmp_path)
        engine_record.record_step_result("1.1", "backend-engineer", status="complete")
        # record gate
        engine_gate = ExecutionEngine(team_context_root=tmp_path)
        engine_gate.record_gate_result(phase_id=0, passed=True, output="ok")
        # complete
        engine_complete = ExecutionEngine(team_context_root=tmp_path)
        engine_complete.complete()

        data = json.loads(
            (tmp_path / "traces" / "cli-trace-gate.json").read_text()
        )
        event_types = [e["event_type"] for e in data["events"]]
        assert "gate_result" in event_types


# ---------------------------------------------------------------------------
# FIX-5: estimated_tokens population in usage records
# ---------------------------------------------------------------------------

class TestEstimatedTokensInUsageRecords:
    """estimated_tokens must never be 0 in usage records after complete().

    When the caller does not pass --tokens (defaults to 0), the engine must
    auto-estimate from the plan step's task_description so BudgetTuner has
    real data to work with.
    """

    def _run_to_complete(self, tmp_path: Path, task: str = "Build feature X") -> None:
        """Drive a single-step plan to completion without supplying token counts."""
        plan = _plan(
            task_id="fix5-tokens",
            phases=[_phase(steps=[_step(task=task)])],
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        # Note: no estimated_tokens argument — defaults to 0
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        engine.next_action()
        engine.complete()

    def test_usage_record_estimated_tokens_nonzero(self, tmp_path: Path) -> None:
        """estimated_tokens in the logged usage record must be > 0."""
        self._run_to_complete(tmp_path)
        from agent_baton.core.observe.usage import UsageLogger
        records = UsageLogger(log_path=tmp_path / "usage-log.jsonl").read_all()
        assert records, "Expected at least one usage record"
        total_tokens = sum(
            agent.estimated_tokens
            for rec in records
            for agent in rec.agents_used
        )
        assert total_tokens > 0, "estimated_tokens must be non-zero after execution"

    def test_longer_task_description_yields_more_tokens(self, tmp_path: Path) -> None:
        """A longer task description should produce a higher token estimate."""
        short_dir = tmp_path / "short"
        long_dir = tmp_path / "long"
        short_dir.mkdir()
        long_dir.mkdir()

        self._run_to_complete(short_dir, task="Fix a bug")
        self._run_to_complete(
            long_dir,
            task=(
                "Implement a comprehensive new feature with multiple components, "
                "detailed documentation, and full test coverage across all layers "
                "of the application stack including unit tests, integration tests, "
                "and end-to-end tests using the full test suite."
            ),
        )

        from agent_baton.core.observe.usage import UsageLogger
        short_records = UsageLogger(log_path=short_dir / "usage-log.jsonl").read_all()
        long_records = UsageLogger(log_path=long_dir / "usage-log.jsonl").read_all()

        short_tokens = sum(
            a.estimated_tokens for r in short_records for a in r.agents_used
        )
        long_tokens = sum(
            a.estimated_tokens for r in long_records for a in r.agents_used
        )
        assert long_tokens > short_tokens, (
            f"Longer description should yield more tokens: {long_tokens} vs {short_tokens}"
        )

    def test_explicit_tokens_take_precedence_over_heuristic(self, tmp_path: Path) -> None:
        """When the caller passes estimated_tokens, it is used as-is (no heuristic)."""
        plan = _plan(
            task_id="fix5-explicit",
            phases=[_phase(steps=[_step(task="short task")])],
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
            estimated_tokens=99_999,  # explicit and distinctive
        )
        engine.next_action()
        engine.complete()

        from agent_baton.core.observe.usage import UsageLogger
        records = UsageLogger(log_path=tmp_path / "usage-log.jsonl").read_all()
        total = sum(a.estimated_tokens for r in records for a in r.agents_used)
        assert total == 99_999, f"Expected explicit 99999 tokens, got {total}"


# ---------------------------------------------------------------------------
# Fix 1: Risk-based pre-flight approval (HIGH / CRITICAL plans)
# ---------------------------------------------------------------------------

class TestRiskBasedApproval:
    """start() should return APPROVAL (not DISPATCH) for HIGH/CRITICAL plans
    unless Phase 1 already carries an approval gate."""

    @pytest.mark.parametrize("risk_level", ["HIGH", "CRITICAL"])
    def test_high_risk_start_returns_approval(
        self, tmp_path: Path, risk_level: str
    ) -> None:
        plan = _plan(risk_level=risk_level)
        action = _engine(tmp_path).start(plan)
        assert action.action_type == ActionType.APPROVAL, (
            f"Expected APPROVAL for {risk_level} plan, got {action.action_type}"
        )

    def test_low_risk_start_returns_dispatch(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(_plan(risk_level="LOW"))
        assert action.action_type == ActionType.DISPATCH

    def test_medium_risk_start_returns_dispatch(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(_plan(risk_level="MEDIUM"))
        assert action.action_type == ActionType.DISPATCH

    def test_approval_context_mentions_risk_level(self, tmp_path: Path) -> None:
        plan = _plan(risk_level="HIGH")
        action = _engine(tmp_path).start(plan)
        assert "HIGH" in action.approval_context

    def test_approval_context_mentions_task_summary(self, tmp_path: Path) -> None:
        plan = _plan(risk_level="HIGH", task_summary="Deploy to production")
        action = _engine(tmp_path).start(plan)
        assert "Deploy to production" in action.approval_context

    def test_approval_options_include_approve(self, tmp_path: Path) -> None:
        plan = _plan(risk_level="HIGH")
        action = _engine(tmp_path).start(plan)
        assert "approve" in action.approval_options

    def test_state_status_is_approval_pending(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(risk_level="CRITICAL"))
        state = engine._load_state()
        assert state.status == "approval_pending"

    def test_after_approval_next_action_is_dispatch(self, tmp_path: Path) -> None:
        """Approving the pre-flight checkpoint unblocks dispatch."""
        plan = _plan(risk_level="HIGH")
        engine = _engine(tmp_path)
        action = engine.start(plan)
        assert action.action_type == ActionType.APPROVAL

        phase_id = action.phase_id
        engine.record_approval_result(phase_id=phase_id, result="approve")
        next_action = engine.next_action()
        assert next_action.action_type == ActionType.DISPATCH

    def test_reject_approval_halts_execution(self, tmp_path: Path) -> None:
        plan = _plan(risk_level="HIGH")
        engine = _engine(tmp_path)
        action = engine.start(plan)
        engine.record_approval_result(phase_id=action.phase_id, result="reject")
        next_action = engine.next_action()
        assert next_action.action_type == ActionType.FAILED

    def test_existing_approval_gate_not_duplicated(self, tmp_path: Path) -> None:
        """If Phase 1 already has approval_required, start() must not add another.

        The planner-added approval gate fires after Phase 1 steps complete
        (post-phase check), not before dispatching.  Our pre-flight check
        should not inject a second approval before steps run.
        """
        phase = PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[_step()],
            approval_required=True,
            approval_description="Custom reviewer note",
        )
        plan = MachinePlan(
            task_id="dup-check",
            task_summary="High risk task",
            risk_level="HIGH",
            phases=[phase],
        )
        engine = _engine(tmp_path)
        action = engine.start(plan)
        # Pre-flight is skipped — the engine dispatches the first step directly.
        assert action.action_type == ActionType.DISPATCH
        # After the step completes the post-phase approval fires with the
        # original custom description intact.
        engine.record_step_result("1.1", "backend-engineer")
        post_action = engine.next_action()
        assert post_action.action_type == ActionType.APPROVAL
        assert "Custom reviewer note" in post_action.approval_context


# ---------------------------------------------------------------------------
# Fix 2: TaskViewSubscriber — materialized task-view.json
# ---------------------------------------------------------------------------

def _engine_with_bus(tmp_path: Path, task_id: str = "task-view-001") -> ExecutionEngine:
    """Return an engine wired with a live EventBus."""
    bus = EventBus()
    return ExecutionEngine(team_context_root=tmp_path, bus=bus, task_id=task_id)


class TestTaskViewSubscriber:
    """EventBus subscriber writes task-view.json to the execution directory."""

    def test_task_view_created_on_start(self, tmp_path: Path) -> None:
        tid = "tview-001"
        engine = _engine_with_bus(tmp_path, task_id=tid)
        engine.start(_plan(task_id=tid))
        view_path = tmp_path / "executions" / tid / "task-view.json"
        assert view_path.exists(), "task-view.json should be created after start()"

    def test_task_view_is_valid_json(self, tmp_path: Path) -> None:
        tid = "tview-002"
        engine = _engine_with_bus(tmp_path, task_id=tid)
        engine.start(_plan(task_id=tid))
        view_path = tmp_path / "executions" / tid / "task-view.json"
        data = json.loads(view_path.read_text())
        assert data["task_id"] == tid

    def test_task_view_status_running_after_start(self, tmp_path: Path) -> None:
        tid = "tview-003"
        engine = _engine_with_bus(tmp_path, task_id=tid)
        engine.start(_plan(task_id=tid))
        view_path = tmp_path / "executions" / tid / "task-view.json"
        data = json.loads(view_path.read_text())
        assert data["status"] == "running"

    def test_task_view_reflects_risk_level(self, tmp_path: Path) -> None:
        tid = "tview-004"
        engine = _engine_with_bus(tmp_path, task_id=tid)
        plan = _plan(task_id=tid, risk_level="HIGH")
        engine.start(plan)
        # Approve the pre-flight so the view proceeds past approval_pending.
        state = engine._load_state()
        phase_id = state.plan.phases[0].phase_id
        engine.record_approval_result(phase_id=phase_id, result="approve")
        view_path = tmp_path / "executions" / tid / "task-view.json"
        data = json.loads(view_path.read_text())
        assert data["risk_level"] == "HIGH"

    def test_task_view_total_steps_correct(self, tmp_path: Path) -> None:
        tid = "tview-005"
        engine = _engine_with_bus(tmp_path, task_id=tid)
        plan = _plan(
            task_id=tid,
            phases=[_phase(steps=[_step("1.1"), _step("1.2")])],
        )
        engine.start(plan)
        view_path = tmp_path / "executions" / tid / "task-view.json"
        data = json.loads(view_path.read_text())
        assert data["total_steps"] == 2

    def test_task_view_updates_on_step_complete(self, tmp_path: Path) -> None:
        """After a step.completed event, task-view.json should show steps_completed > 0."""
        from agent_baton.core.events import events as evt

        tid = "tview-006"
        bus = EventBus()
        engine = ExecutionEngine(team_context_root=tmp_path, bus=bus, task_id=tid)
        engine.start(_plan(task_id=tid))
        # Manually publish a step.completed event so the subscriber fires.
        bus.publish(evt.step_completed(tid, "1.1", "backend-engineer", outcome="done"))
        view_path = tmp_path / "executions" / tid / "task-view.json"
        data = json.loads(view_path.read_text())
        assert data["steps_completed"] == 1

    def test_task_view_not_created_without_bus(self, tmp_path: Path) -> None:
        """Without a bus, no task-view.json should be written."""
        tid = "tview-007"
        engine = _engine(tmp_path)  # no bus
        engine.start(_plan(task_id=tid))
        view_path = tmp_path / "executions" / tid / "task-view.json"
        assert not view_path.exists(), (
            "task-view.json must not be created when no EventBus is wired"
        )
