"""Tests for token tracking across the record_step_result / launcher pipeline.

Covers:
- executor.record_step_result applies _estimate_tokens_for_step fallback when
  estimated_tokens=0 and status != "dispatched"
- Explicit caller-supplied tokens are never overwritten by the fallback
- "dispatched" steps are exempt from the fallback (no outcome yet)
- StepResult.estimated_tokens is persisted and round-trips through SqliteStorage
- ClaudeCodeLauncher._parse_output populates estimated_tokens from raw text
  (the non-JSON fallback path)
- ClaudeCodeLauncher._parse_output zero-tokens edge case (empty stdout)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Minimal plan factories
# ---------------------------------------------------------------------------

def _make_plan(task_id: str = "task-tok-001") -> MachinePlan:
    """Return a single-phase, single-step plan with a known description length."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        # 40 chars → fallback = max(1, 40 // 4) = 10 tokens
        task_description="A" * 40,
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implement",
        steps=[step],
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="token tracking test",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        pattern_source="",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_engine(tmp_path: Path, task_id: str = "task-tok-001") -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path, task_id=task_id)


# ---------------------------------------------------------------------------
# Helpers to advance engine to the point where record_step_result is valid
# ---------------------------------------------------------------------------

def _start_and_dispatch(engine: ExecutionEngine, plan: MachinePlan) -> str:
    """Start execution and consume the first DISPATCH action.

    Returns the step_id of the dispatched step.
    """
    action = engine.start(plan)
    assert action.action_type == ActionType.DISPATCH, f"Expected DISPATCH, got {action.action_type}"
    step_id = action.step_id
    # Mark dispatched so the engine tracks the in-flight step.
    engine.mark_dispatched(step_id=step_id, agent_name=action.agent_name)
    return step_id


# ===========================================================================
# Tests: executor fallback when estimated_tokens=0
# ===========================================================================

class TestRecordStepResultTokenFallback:
    """record_step_result must populate estimated_tokens via plan heuristic
    when the caller passes 0 (the default).
    """

    def test_fallback_applied_when_tokens_zero(self, tmp_path: Path) -> None:
        """Zero tokens → fallback estimate derived from plan step description."""
        plan = _make_plan()
        engine = _make_engine(tmp_path)
        step_id = _start_and_dispatch(engine, plan)

        engine.record_step_result(
            step_id=step_id,
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=0,
        )

        state = engine._load_state()
        assert state is not None
        results = [r for r in state.step_results if r.step_id == step_id and r.status == "complete"]
        assert results, "No complete StepResult found after record_step_result"
        # 40-char description ÷ 4 = 10 tokens minimum
        assert results[-1].estimated_tokens > 0, (
            "expected non-zero estimated_tokens after fallback"
        )

    def test_fallback_value_matches_description_heuristic(self, tmp_path: Path) -> None:
        """Fallback value should equal len(task_description) // 4."""
        description = "X" * 200  # 200 chars → 50 tokens
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description=description,
            model="sonnet",
            depends_on=[],
            deliverables=[],
            allowed_paths=[],
            blocked_paths=[],
            context_files=[],
        )
        phase = PlanPhase(phase_id=1, name="Impl", steps=[step], approval_required=False)
        plan = MachinePlan(
            task_id="task-tok-heuristic",
            task_summary="heuristic test",
            risk_level="LOW",
            budget_tier="standard",
            execution_mode="phased",
            git_strategy="commit-per-agent",
            phases=[phase],
            shared_context="",
            pattern_source="",
            created_at="2026-01-01T00:00:00+00:00",
        )
        engine = _make_engine(tmp_path, task_id="task-tok-heuristic")
        step_id = _start_and_dispatch(engine, plan)

        engine.record_step_result(
            step_id=step_id,
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=0,
        )

        state = engine._load_state()
        assert state is not None
        result = next(
            (r for r in state.step_results if r.step_id == step_id and r.status == "complete"),
            None,
        )
        assert result is not None
        # 200 chars // 4 = 50
        assert result.estimated_tokens == 50

    def test_explicit_tokens_not_overwritten(self, tmp_path: Path) -> None:
        """When caller provides non-zero tokens, the fallback must NOT fire."""
        plan = _make_plan()
        engine = _make_engine(tmp_path)
        step_id = _start_and_dispatch(engine, plan)

        engine.record_step_result(
            step_id=step_id,
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=99999,
        )

        state = engine._load_state()
        assert state is not None
        result = next(
            (r for r in state.step_results if r.step_id == step_id and r.status == "complete"),
            None,
        )
        assert result is not None
        assert result.estimated_tokens == 99999

    def test_dispatched_status_exempt_from_fallback(self, tmp_path: Path) -> None:
        """Steps recorded with status='dispatched' must not have the fallback applied."""
        plan = _make_plan()
        engine = _make_engine(tmp_path)
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # record_step_result with status="dispatched" is called internally by
        # mark_dispatched; call it directly here to confirm exemption.
        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="dispatched",
            outcome="",
            estimated_tokens=0,
        )

        state = engine._load_state()
        assert state is not None
        dispatched_results = [
            r for r in state.step_results
            if r.step_id == action.step_id and r.status == "dispatched"
        ]
        assert dispatched_results, "Expected a dispatched StepResult"
        assert dispatched_results[-1].estimated_tokens == 0, (
            "dispatched step should keep estimated_tokens=0 (no fallback)"
        )

    def test_failed_step_also_gets_fallback(self, tmp_path: Path) -> None:
        """Failed steps (not dispatched) must also receive the token fallback."""
        plan = _make_plan()
        engine = _make_engine(tmp_path)
        step_id = _start_and_dispatch(engine, plan)

        engine.record_step_result(
            step_id=step_id,
            agent_name="backend-engineer--python",
            status="failed",
            outcome="something went wrong",
            estimated_tokens=0,
            error="agent crashed",
        )

        state = engine._load_state()
        assert state is not None
        result = next(
            (r for r in state.step_results if r.step_id == step_id and r.status == "failed"),
            None,
        )
        assert result is not None
        assert result.estimated_tokens > 0, (
            "failed steps should also receive fallback token estimate"
        )


# ===========================================================================
# Tests: estimated_tokens round-trips through SqliteStorage
# ===========================================================================

class TestTokenPersistence:
    """estimated_tokens must survive save_step_result → load round-trip."""

    def _make_state(self, plan: MachinePlan) -> ExecutionState:
        return ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            current_phase=0,
            current_step_index=0,
            status="running",
            started_at="2026-01-01T00:00:00+00:00",
        )

    def test_save_and_reload_preserves_nonzero_tokens(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "baton.db")
        plan = _make_plan(task_id="task-persist-01")
        state = self._make_state(plan)
        store.save_execution(state)

        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=12345,
            duration_seconds=10.0,
            completed_at="2026-01-01T01:00:00+00:00",
        )
        store.save_step_result(task_id=plan.task_id, result=result)

        loaded = store.load_execution(plan.task_id)
        assert loaded is not None
        persisted = next(
            (r for r in loaded.step_results if r.step_id == "1.1"),
            None,
        )
        assert persisted is not None
        assert persisted.estimated_tokens == 12345

    def test_save_and_reload_zero_tokens_stays_zero(self, tmp_path: Path) -> None:
        """The storage layer must not silently transform 0 → something else."""
        store = SqliteStorage(tmp_path / "baton.db")
        plan = _make_plan(task_id="task-persist-02")
        state = self._make_state(plan)
        store.save_execution(state)

        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=0,
            completed_at="2026-01-01T01:00:00+00:00",
        )
        store.save_step_result(task_id=plan.task_id, result=result)

        loaded = store.load_execution(plan.task_id)
        assert loaded is not None
        persisted = next(
            (r for r in loaded.step_results if r.step_id == "1.1"),
            None,
        )
        assert persisted is not None
        assert persisted.estimated_tokens == 0

    def test_full_state_save_and_load_preserves_tokens(self, tmp_path: Path) -> None:
        """bulk save_execution → load_execution path must also preserve tokens."""
        store = SqliteStorage(tmp_path / "baton.db")
        plan = _make_plan(task_id="task-persist-03")
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=7777,
            completed_at="2026-01-01T01:00:00+00:00",
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            current_phase=0,
            current_step_index=0,
            status="running",
            started_at="2026-01-01T00:00:00+00:00",
            step_results=[result],
        )
        store.save_execution(state)

        loaded = store.load_execution(plan.task_id)
        assert loaded is not None
        persisted = next(
            (r for r in loaded.step_results if r.step_id == "1.1"),
            None,
        )
        assert persisted is not None
        assert persisted.estimated_tokens == 7777


# ===========================================================================
# Tests: ClaudeCodeLauncher raw-text fallback path
# ===========================================================================

class FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self._killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self.stdout, self.stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> None:
        pass


def _make_launcher(monkeypatch: pytest.MonkeyPatch):
    """Return a ClaudeCodeLauncher with the claude binary resolved via monkeypatch."""
    import shutil
    from agent_baton.core.runtime.claude_launcher import ClaudeCodeConfig, ClaudeCodeLauncher

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
    return ClaudeCodeLauncher(config=ClaudeCodeConfig())


class TestLauncherRawTextTokens:
    """_parse_output raw-text path must set estimated_tokens from stdout length."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_raw_text_success_has_nonzero_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plain text stdout (non-JSON) must produce estimated_tokens > 0."""
        raw_output = b"I completed the task and created the files."

        async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            return FakeProcess(stdout=raw_output, returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        launcher = _make_launcher(monkeypatch)

        result = self._run(launcher.launch(
            agent_name="backend-engineer--python",
            model="sonnet",
            prompt="do something",
            step_id="1.1",
        ))

        assert result.estimated_tokens > 0
        # 43 chars // 4 = 10 tokens
        assert result.estimated_tokens == max(1, len(raw_output) // 4)

    def test_raw_text_failure_has_nonzero_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-JSON stdout with non-zero exit code must also have tokens estimated."""
        raw_output = b"Error: something went wrong during execution"

        async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            return FakeProcess(stdout=raw_output, stderr=b"fatal error", returncode=1)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        launcher = _make_launcher(monkeypatch)

        result = self._run(launcher.launch(
            agent_name="backend-engineer--python",
            model="sonnet",
            prompt="do something",
            step_id="1.1",
        ))

        assert result.status == "failed"
        assert result.estimated_tokens > 0
        assert result.estimated_tokens == max(1, len(raw_output) // 4)

    def test_raw_text_empty_stdout_tokens_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty stdout on raw-text path must produce estimated_tokens=0."""
        async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            return FakeProcess(stdout=b"", returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        launcher = _make_launcher(monkeypatch)

        result = self._run(launcher.launch(
            agent_name="backend-engineer--python",
            model="sonnet",
            prompt="do something",
            step_id="1.1",
        ))

        assert result.estimated_tokens == 0

    def test_json_path_still_uses_usage_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JSON output path must continue using usage.input_tokens + output_tokens."""
        payload = json.dumps({
            "result": "all done",
            "is_error": False,
            "usage": {"input_tokens": 300, "output_tokens": 150},
            "duration_ms": 2000,
        }).encode()

        async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            return FakeProcess(stdout=payload, returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        launcher = _make_launcher(monkeypatch)

        result = self._run(launcher.launch(
            agent_name="backend-engineer--python",
            model="sonnet",
            prompt="do something",
            step_id="1.1",
        ))

        assert result.status == "complete"
        assert result.estimated_tokens == 450  # 300 + 150

    def test_raw_text_tokens_proportional_to_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token estimate must scale linearly with output size (4 chars per token)."""
        raw_output = b"A" * 400  # 400 chars → 100 tokens

        async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            return FakeProcess(stdout=raw_output, returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        launcher = _make_launcher(monkeypatch)

        result = self._run(launcher.launch(
            agent_name="backend-engineer--python",
            model="sonnet",
            prompt="do something",
            step_id="1.1",
        ))

        assert result.estimated_tokens == 100
