"""Tests for BATON_RUN_TOKEN_CEILING run-level kill-switch (end-user readiness #7).

Covers the four scenarios required by the spec:

1. test_run_token_ceiling_blocks_excess_call
   Set ceiling, simulate spend, assert the next over-budget call raises
   RunTokenCeilingExceeded.

2. test_run_token_ceiling_persists_across_resume
   Set ceiling, spend, serialise ExecutionState, deserialise into a new
   BudgetEnforcer via initial_run_spend_usd, verify the counter is restored
   and further over-budget calls are refused.

3. test_run_token_ceiling_unset_defaults_unlimited_with_warning
   No env var set — verify that check_run_ceiling() never raises regardless
   of spend, and that warn_if_ceiling_unset_for_high_risk() emits a warning
   for HIGH/CRITICAL plans but not for LOW/MEDIUM.

4. test_run_token_ceiling_immune_daemon_suspends_on_ceiling
   ImmuneDaemon ticks once within budget, then ceiling trips on the next
   pre-flight check — daemon must stop sweeping (ceiling_suspended=True)
   and not call sweeper.sweep() again.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.govern.budget import BudgetEnforcer, RunTokenCeilingExceeded
from agent_baton.core.engine.selfheal import SelfHealEscalator, EscalationTier
from agent_baton.core.engine.speculator import SpeculativePipeliner, SpeculationTrigger
from agent_baton.core.immune.daemon import ImmuneConfig, ImmuneDaemon
from agent_baton.core.immune.scheduler import SweepScheduler, SweepTarget
from agent_baton.core.immune.sweeper import SweepFinding, Sweeper
from agent_baton.core.immune.triage import FindingTriage
from agent_baton.models.execution import ExecutionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _make_target(path: str = "/a/file.py", kind: str = "stale-comment") -> SweepTarget:
    return SweepTarget(
        path=Path(path),
        kind=kind,
        last_swept_at="2020-01-01T00:00:00Z",
        priority=1.0,
    )


def _make_finding(kind: str = "stale-comment", confidence: float = 0.92) -> SweepFinding:
    return SweepFinding(
        target=_make_target(kind=kind),
        confidence=confidence,
        description="Test finding",
        affected_lines=[5],
        auto_fix_directive="Remove stale comment at L5",
        kind=kind,
    )


def _make_minimal_plan():
    """Return a one-phase one-step MachinePlan for ExecutionState construction."""
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
    return MachinePlan(
        task_id="task-ceiling-test",
        task_summary="Ceiling test plan",
        risk_level="HIGH",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Impl",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement foo",
                        model="sonnet",
                        step_type="implementation",
                    )
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Test 1 — ceiling blocks excess call
# ---------------------------------------------------------------------------


class TestRunTokenCeilingBlocksExcessCall:
    def test_run_token_ceiling_blocks_excess_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set a tight ceiling, record spend that fills it, then verify the next
        call raises RunTokenCeilingExceeded with the correct attributes."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.01")

        enforcer = BudgetEnforcer()
        # Spend $0.009 directly (just under ceiling).
        enforcer.add_run_spend(0.009)
        assert enforcer.run_cumulative_spend_usd == pytest.approx(0.009)

        # A $0.002 call would push us to $0.011 — over the $0.01 ceiling.
        with pytest.raises(RunTokenCeilingExceeded) as exc_info:
            enforcer.check_run_ceiling(0.002, "selfheal haiku-1")

        exc = exc_info.value
        assert exc.ceiling_usd == pytest.approx(0.01)
        assert exc.current_spend_usd == pytest.approx(0.009)
        assert exc.estimated_call_usd == pytest.approx(0.002)
        assert exc.intent == "selfheal haiku-1"
        assert "selfheal haiku-1" in str(exc)
        assert "ceiling" in str(exc).lower()

    def test_ceiling_exactly_at_limit_allows_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A call whose projected total equals the ceiling exactly is allowed
        (ceiling is exclusive: > not >=)."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.01")
        enforcer = BudgetEnforcer()
        enforcer.add_run_spend(0.008)
        # $0.008 + $0.002 = $0.010 which is NOT > $0.010 — should not raise.
        enforcer.check_run_ceiling(0.002, "selfheal haiku-1")  # must not raise

    def test_no_ceiling_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When BATON_RUN_TOKEN_CEILING is unset, check_run_ceiling never raises."""
        monkeypatch.delenv("BATON_RUN_TOKEN_CEILING", raising=False)
        enforcer = BudgetEnforcer()
        enforcer.add_run_spend(9_999_999.0)
        enforcer.check_run_ceiling(9_999_999.0, "anything")  # must not raise

    def test_selfheal_escalator_ceiling_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SelfHealEscalator.next_tier_with_ceiling_check() returns None and
        records a ceiling-abort attempt when the ceiling trips."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.001")

        enforcer = BudgetEnforcer()
        # Pre-fill spend so even the cheapest Haiku tier (4K in + 1K out) would exceed.
        enforcer.add_run_spend(0.0009)

        escalator = SelfHealEscalator(
            step_id="step-1.1",
            gate_command="pytest tests/",
            worktree_path=tmp_path,
            budget_enforcer=enforcer,
        )
        result = escalator.next_tier_with_ceiling_check()
        assert result is None

        # A ceiling-abort attempt should have been recorded.
        attempts = escalator.attempts
        assert len(attempts) == 1
        assert attempts[0].status == "ceiling-abort"
        assert attempts[0].tier == EscalationTier.HAIKU_1.value

    def test_speculator_should_speculate_false_on_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SpeculativePipeliner.should_speculate() returns False when ceiling trips."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.001")

        enforcer = BudgetEnforcer()
        enforcer.add_run_spend(0.0009)  # near ceiling

        pipeliner = SpeculativePipeliner(
            budget_enforcer=enforcer,
            enabled=True,
        )
        result = pipeliner.should_speculate(
            block_reason="awaiting_human_approval",
            next_step_id="step-2.1",
        )
        assert result is False


# ---------------------------------------------------------------------------
# Test 2 — ceiling persists across resume
# ---------------------------------------------------------------------------


class TestRunTokenCeilingPersistsAcrossResume:
    def test_run_token_ceiling_persists_across_resume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spend is persisted in ExecutionState and restored when a new
        BudgetEnforcer is created with initial_run_spend_usd from the state."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.05")

        # Phase 1: original run — spend some money.
        enforcer_orig = BudgetEnforcer()
        enforcer_orig.add_run_spend(0.03)
        assert enforcer_orig.run_cumulative_spend_usd == pytest.approx(0.03)

        # Simulate persisting to ExecutionState.
        state = ExecutionState(
            task_id="task-resume-test",
            plan=_make_minimal_plan(),
        )
        state.run_cumulative_spend_usd = enforcer_orig.run_cumulative_spend_usd

        # Serialise and deserialise (simulates crash + resume from JSON).
        state_dict = state.to_dict()
        assert state_dict["run_cumulative_spend_usd"] == pytest.approx(0.03)

        restored_state = ExecutionState.from_dict(state_dict)
        assert restored_state.run_cumulative_spend_usd == pytest.approx(0.03)

        # Phase 2: resumed run — new BudgetEnforcer with restored spend.
        enforcer_resumed = BudgetEnforcer(
            initial_run_spend_usd=restored_state.run_cumulative_spend_usd
        )
        assert enforcer_resumed.run_cumulative_spend_usd == pytest.approx(0.03)

        # $0.03 + $0.025 = $0.055 > $0.05 ceiling — must raise.
        with pytest.raises(RunTokenCeilingExceeded) as exc_info:
            enforcer_resumed.check_run_ceiling(0.025, "selfheal sonnet-1")

        exc = exc_info.value
        assert exc.current_spend_usd == pytest.approx(0.03)
        assert exc.ceiling_usd == pytest.approx(0.05)

    def test_legacy_state_without_field_defaults_to_zero(self) -> None:
        """ExecutionState.from_dict() with no run_cumulative_spend_usd key
        defaults to 0.0 (legacy state file compatibility)."""
        state_dict = {
            "task_id": "old-task",
            "plan": _make_minimal_plan().to_dict(),
            # run_cumulative_spend_usd intentionally absent
        }
        state = ExecutionState.from_dict(state_dict)
        assert state.run_cumulative_spend_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Test 3 — unset ceiling defaults to unlimited with warning
# ---------------------------------------------------------------------------


class TestRunTokenCeilingUnsetDefaultsUnlimited:
    def test_run_token_ceiling_unset_defaults_unlimited_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When BATON_RUN_TOKEN_CEILING is unset on a HIGH-risk run:
        - check_run_ceiling() never raises
        - warn_if_ceiling_unset_for_high_risk() emits a WARNING log line
        """
        monkeypatch.delenv("BATON_RUN_TOKEN_CEILING", raising=False)

        enforcer = BudgetEnforcer()
        # Enormous spend — still no raise when ceiling is unset.
        enforcer.add_run_spend(1_000_000.0)
        enforcer.check_run_ceiling(1_000_000.0, "selfheal opus")  # must not raise

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
            enforcer.warn_if_ceiling_unset_for_high_risk("HIGH")

        assert any(
            "BATON_RUN_TOKEN_CEILING" in record.message and "HIGH" in record.message
            for record in caplog.records
        ), f"Expected ceiling warning in logs, got: {[r.message for r in caplog.records]}"

    def test_no_warning_for_critical_when_ceiling_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No warning when ceiling IS set, even on CRITICAL risk."""
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "100.0")

        enforcer = BudgetEnforcer()
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
            enforcer.warn_if_ceiling_unset_for_high_risk("CRITICAL")

        ceiling_warnings = [
            r for r in caplog.records if "BATON_RUN_TOKEN_CEILING" in r.message
        ]
        assert len(ceiling_warnings) == 0

    def test_no_warning_for_low_risk(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No warning for LOW or MEDIUM risk even when ceiling is unset."""
        monkeypatch.delenv("BATON_RUN_TOKEN_CEILING", raising=False)

        enforcer = BudgetEnforcer()
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
            enforcer.warn_if_ceiling_unset_for_high_risk("LOW")
            enforcer.warn_if_ceiling_unset_for_high_risk("MEDIUM")

        ceiling_warnings = [
            r for r in caplog.records if "BATON_RUN_TOKEN_CEILING" in r.message
        ]
        assert len(ceiling_warnings) == 0

    def test_warning_for_critical_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """warn_if_ceiling_unset_for_high_risk() also warns for CRITICAL."""
        monkeypatch.delenv("BATON_RUN_TOKEN_CEILING", raising=False)

        enforcer = BudgetEnforcer()
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.budget"):
            enforcer.warn_if_ceiling_unset_for_high_risk("CRITICAL")

        assert any(
            "BATON_RUN_TOKEN_CEILING" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Test 4 — immune daemon suspends on ceiling
# ---------------------------------------------------------------------------


class TestRunTokenCeilingImmuneDaemonSuspends:
    def test_run_token_ceiling_immune_daemon_suspends_on_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ImmuneDaemon stops sweeping once the run-level ceiling is tripped.

        Scenario:
        - Ceiling = $0.01
        - Pre-fill spend so that the SECOND sweep would exceed the ceiling.
        - Daemon runs: first tick is gated by the ceiling pre-flight;
          if it trips immediately, no sweeps happen.  We verify:
          (a) ceiling_suspended is True after the relevant tick.
          (b) sweeper.sweep() is called at most once (0 if ceiling trips on
              tick 1, 1 if it trips after the first successful sweep).
          (c) daemon shuts down cleanly.
        """
        # Each Haiku sweep: 12K in + 1K out.
        # _cost_usd("haiku", 12_000, 1_000) = 12_000*0.25/1M + 1_000*1.25/1M
        #   = 0.003 + 0.00000125 ≈ $0.003001
        # Set ceiling to $0.005 and pre-fill to $0.003 so the second sweep
        # would cost ~$0.003 putting us at ~$0.006 > $0.005.
        monkeypatch.setenv("BATON_RUN_TOKEN_CEILING", "0.005")

        enforcer = BudgetEnforcer(initial_run_spend_usd=0.003)

        conn = _conn()
        scheduler = SweepScheduler(project_root=tmp_path, conn=conn)
        for i in range(10):
            scheduler.seed([Path(f"/a/file{i}.py")], kind="stale-comment")

        mock_sweeper = MagicMock(spec=Sweeper)
        mock_sweeper.sweep.return_value = None  # no findings

        bead_store = MagicMock()
        bead_store.write.return_value = "bd-ceiling-test"

        config = ImmuneConfig(
            enabled=True,
            daily_cap_usd=50.0,  # daily cap is NOT the constraint here
            sweep_kinds=["stale-comment"],
            auto_fix=False,
            tick_interval_sec=0,
        )
        triage = FindingTriage(
            bead_store=bead_store,
            budget=enforcer,
            config=config,
            launcher=MagicMock(),
        )
        daemon = ImmuneDaemon(
            config=config,
            budget=enforcer,
            scheduler=scheduler,
            sweeper=mock_sweeper,
            triage=triage,
        )

        tick_count = [0]

        def _counting_sleep(seconds: int) -> None:
            tick_count[0] += 1
            # Allow at most 3 ticks to observe the ceiling suspension.
            if tick_count[0] >= 3 or daemon.ceiling_suspended:
                daemon.shutdown()

        daemon._sleep = _counting_sleep
        daemon.run()

        # The ceiling must have been tripped and the daemon must be suspended.
        assert daemon.ceiling_suspended is True

        # Once suspended, sweeper must NOT have been called again on any
        # subsequent tick.  Record the call count at suspension and verify
        # it does not grow after that point (our loop shuts down immediately
        # when suspended, so total calls <= 1).
        assert mock_sweeper.sweep.call_count <= 1

    def test_immune_daemon_ceiling_suspended_property_false_initially(
        self, tmp_path: Path
    ) -> None:
        """ceiling_suspended is False before any ceiling event."""
        enforcer = BudgetEnforcer()
        conn = _conn()
        scheduler = SweepScheduler(project_root=tmp_path, conn=conn)
        mock_sweeper = MagicMock(spec=Sweeper)
        mock_triage = MagicMock()
        config = ImmuneConfig(enabled=True, tick_interval_sec=0)

        daemon = ImmuneDaemon(
            config=config,
            budget=enforcer,
            scheduler=scheduler,
            sweeper=mock_sweeper,
            triage=mock_triage,
        )
        assert daemon.ceiling_suspended is False

    def test_immune_daemon_no_ceiling_runs_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When BATON_RUN_TOKEN_CEILING is unset, the daemon sweeps normally
        and ceiling_suspended remains False."""
        monkeypatch.delenv("BATON_RUN_TOKEN_CEILING", raising=False)

        enforcer = BudgetEnforcer()
        conn = _conn()
        scheduler = SweepScheduler(project_root=tmp_path, conn=conn)
        scheduler.seed([Path("/a/file.py")], kind="stale-comment")

        mock_sweeper = MagicMock(spec=Sweeper)
        mock_sweeper.sweep.return_value = None

        config = ImmuneConfig(
            enabled=True,
            daily_cap_usd=50.0,
            sweep_kinds=["stale-comment"],
            auto_fix=False,
            tick_interval_sec=0,
        )
        mock_triage = MagicMock()

        daemon = ImmuneDaemon(
            config=config,
            budget=enforcer,
            scheduler=scheduler,
            sweeper=mock_sweeper,
            triage=mock_triage,
        )

        def _one_tick(seconds: int) -> None:
            daemon.shutdown()

        daemon._sleep = _one_tick
        daemon.run()

        assert daemon.ceiling_suspended is False
        assert mock_sweeper.sweep.call_count == 1
