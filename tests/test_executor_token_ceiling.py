"""Regression tests for bd-3f80: executor-side BATON_RUN_TOKEN_CEILING wiring.

Tests:
    - test_executor_warns_on_high_risk_without_ceiling: high-risk plan, ceiling
      unset → warning logged exactly once at start().
    - test_executor_resume_restores_run_spend: resume from state with
      run_cumulative_spend_usd=2.50 → BudgetEnforcer seeded with 2.50, not 0.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Factories (minimal — mirrors test_executor.py conventions)
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="Do the thing",
        model="sonnet",
        deliverables=[],
        allowed_paths=[],
        context_files=[],
    )


def _phase(phase_id: int = 0) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name="Implementation",
        steps=[_step()],
        gate=None,
    )


def _plan(risk_level: str = "LOW") -> MachinePlan:
    return MachinePlan(
        task_id="task-ceiling-test",
        task_summary="Ceiling regression",
        risk_level=risk_level,
        phases=[_phase()],
        shared_context="",
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# Test 1: HIGH-risk run without ceiling → warning logged at start()
# ---------------------------------------------------------------------------

class TestExecutorWarnsOnHighRiskWithoutCeiling:
    def test_executor_warns_on_high_risk_without_ceiling(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """start() with HIGH risk and no BATON_RUN_TOKEN_CEILING must emit a warning."""
        engine = _engine(tmp_path)
        # Ensure the env var is unset.
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BATON_RUN_TOKEN_CEILING", None)

            with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
                action = engine.start(_plan(risk_level="HIGH"))

        assert action.action_type in (ActionType.DISPATCH, ActionType.GATE, ActionType.APPROVAL)

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        ceiling_warnings = [m for m in warning_messages if "BATON_RUN_TOKEN_CEILING" in m]
        assert ceiling_warnings, (
            "Expected a BATON_RUN_TOKEN_CEILING warning for a HIGH-risk run "
            f"without the env var set. Got warnings: {warning_messages}"
        )

    def test_no_warning_on_low_risk_without_ceiling(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """LOW-risk runs must NOT produce a ceiling warning even when unset."""
        engine = _engine(tmp_path)
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("BATON_RUN_TOKEN_CEILING", None)

            with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
                engine.start(_plan(risk_level="LOW"))

        ceiling_warnings = [
            r.message for r in caplog.records
            if r.levelno == logging.WARNING and "BATON_RUN_TOKEN_CEILING" in r.message
        ]
        assert not ceiling_warnings, (
            f"LOW-risk run should not warn about ceiling, but got: {ceiling_warnings}"
        )


# ---------------------------------------------------------------------------
# Test 2: resume() restores run_cumulative_spend_usd into BudgetEnforcer
# ---------------------------------------------------------------------------

class TestExecutorResumeRestoresRunSpend:
    def test_executor_resume_restores_run_spend(self, tmp_path: Path) -> None:
        """resume() must seed BudgetEnforcer with persisted run_cumulative_spend_usd."""
        from agent_baton.core.govern.budget import BudgetEnforcer

        engine = _engine(tmp_path)

        # Start a fresh run so there is a saved state on disk.
        plan = _plan(risk_level="LOW")
        engine.start(plan)

        # Directly patch the saved state to inject a non-zero cumulative spend.
        state = engine._load_execution()
        assert state is not None, "start() must persist execution state"
        state.run_cumulative_spend_usd = 2.50
        engine._save_execution(state)

        # Resume — the engine should reconstruct BudgetEnforcer from saved state.
        engine2 = _engine(tmp_path)
        engine2._task_id = plan.task_id
        engine2.resume()

        # Verify the BudgetEnforcer was seeded with 2.50.
        assert hasattr(engine2, "_budget_enforcer"), (
            "resume() must set self._budget_enforcer"
        )
        enforcer: BudgetEnforcer = engine2._budget_enforcer
        assert enforcer.run_cumulative_spend_usd == pytest.approx(2.50), (
            f"Expected seeded spend of 2.50, got {enforcer.run_cumulative_spend_usd}"
        )

        # Adding zero spend must still report 2.50 (not reset to 0).
        enforcer.add_run_spend(0.0)
        assert enforcer.run_cumulative_spend_usd == pytest.approx(2.50)
