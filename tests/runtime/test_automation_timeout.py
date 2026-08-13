"""Regression tests: automation steps must honour their own ``timeout_seconds``.

Defect (MINOR): both automation runners hard-coded ``timeout=300`` — the
synchronous ``baton execute run`` loop in
``agent_baton/cli/commands/execution/execute.py`` and the daemon
``TaskWorker._run_automation``.  A plan step carrying a longer budget (e.g.
``workflow.external_timeout_seconds``, default **1800**) was silently
truncated to five minutes.  Both runners now resolve the budget through the
single :func:`resolve_automation_timeout` helper.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest

from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.worker import (
    DEFAULT_AUTOMATION_TIMEOUT_S,
    TaskWorker,
    resolve_automation_timeout,
)
from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


def _state(timeout_seconds: int = 0) -> ExecutionState:
    plan = MachinePlan(
        task_id="task-automation-timeout",
        task_summary="external verifier",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implementation Verification",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="task-runner",
                        task_description="run the external verifier",
                        step_type="automation",
                        command="./verify.sh",
                        timeout_seconds=timeout_seconds,
                    )
                ],
            )
        ],
    )
    return ExecutionState(task_id=plan.task_id, plan=plan)


class _FakeEngine:
    """Minimal stand-in exposing only what the resolver needs."""

    def __init__(self, state: ExecutionState | None) -> None:
        self._state = state

    def _load_execution(self) -> ExecutionState | None:
        return self._state


class _ExplodingEngine:
    def _load_execution(self):  # noqa: ANN201
        raise RuntimeError("state unavailable")


# ── resolve_automation_timeout ──────────────────────────────────────────────


def test_step_timeout_seconds_is_honoured() -> None:
    """A workflow-stamped budget (external_timeout_seconds) survives."""
    engine = _FakeEngine(_state(timeout_seconds=1800))
    assert resolve_automation_timeout(engine, "1.1") == 1800


def test_falls_back_to_default_when_step_declares_none() -> None:
    engine = _FakeEngine(_state(timeout_seconds=0))
    assert resolve_automation_timeout(engine, "1.1") == DEFAULT_AUTOMATION_TIMEOUT_S
    assert DEFAULT_AUTOMATION_TIMEOUT_S == 300


def test_env_default_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "42")
    engine = _FakeEngine(_state(timeout_seconds=0))
    assert resolve_automation_timeout(engine, "1.1") == 42


def test_step_override_beats_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "42")
    engine = _FakeEngine(_state(timeout_seconds=1800))
    assert resolve_automation_timeout(engine, "1.1") == 1800


def test_unknown_step_falls_back() -> None:
    engine = _FakeEngine(_state(timeout_seconds=1800))
    assert resolve_automation_timeout(engine, "9.9") == DEFAULT_AUTOMATION_TIMEOUT_S


def test_resolution_failure_is_non_fatal() -> None:
    assert resolve_automation_timeout(_ExplodingEngine(), "1.1") == (
        DEFAULT_AUTOMATION_TIMEOUT_S
    )
    assert resolve_automation_timeout(object(), "1.1") == DEFAULT_AUTOMATION_TIMEOUT_S


def test_explicit_default_override() -> None:
    engine = _FakeEngine(_state(timeout_seconds=0))
    assert resolve_automation_timeout(engine, "1.1", default=7) == 7


# ── Both runners resolve through the same helper ────────────────────────────


def test_worker_run_automation_enforces_step_timeout() -> None:
    """A 1s step budget kills a 10s command at ~1s (not at the old 300s)."""
    engine = _FakeEngine(_state(timeout_seconds=1))
    worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
    action = ExecutionAction(
        action_type=ActionType.DISPATCH,
        step_id="1.1",
        step_type="automation",
        command="sleep 10",
    )

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        asyncio.run(worker._run_automation(action))
    elapsed = time.monotonic() - started

    assert excinfo.value.timeout == 1
    assert elapsed < 5, f"timeout was not enforced at the step budget ({elapsed:.1f}s)"


def test_cli_run_loop_resolves_the_same_helper() -> None:
    """``baton execute run``'s automation branch imports the shared resolver.

    Guards against the two runners drifting apart again (the CLI branch used
    to carry its own literal ``timeout=300``).
    """
    from agent_baton.cli.commands.execution import execute as execute_mod

    source = Path(execute_mod.__file__).read_text(encoding="utf-8")
    assert "resolve_automation_timeout" in source
    assert "text=True, timeout=300," not in source
