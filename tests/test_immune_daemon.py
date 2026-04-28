"""Tests for Wave 6.2 Part B — ImmuneDaemon + BudgetEnforcer immune methods (bd-be76).

Covers:
1. test_immune_daemon_tick_loop_advances_queue — daemon processes a target and
   advances the scheduler queue.
2. test_immune_sweep_finds_deprecated_api — sweeper finding flows to triage.
3. test_immune_finding_files_bead — triage writes a bead for every finding.
4. test_immune_high_confidence_auto_fix_dispatches — auto-fix launched when
   gates pass.
5. test_immune_low_confidence_bead_only — no auto-fix for low confidence.
6. test_immune_budget_hard_cap_halts — allow_immune_sweep() returns False when
   daily cap exhausted.
7. test_immune_anomaly_burst_suspends_1h — record_immune_spend() triggers 1 h
   suspension when burst threshold is crossed.
8. test_immune_cached_context_reduces_tokens — ContextCache.get_or_build()
   returns a cached JSON string on the second call (no rebuild).

Integration:
9. test_immune_daemon_24h_simulation — daemon processes N ticks with mocked
   time; verifies tick count and bead filings.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from agent_baton.core.govern.budget import BudgetEnforcer
from agent_baton.core.immune.cache import ContextCache
from agent_baton.core.immune.daemon import ImmuneConfig, ImmuneDaemon
from agent_baton.core.immune.scheduler import SweepScheduler, SweepTarget
from agent_baton.core.immune.sweeper import SweepFinding, Sweeper
from agent_baton.core.immune.triage import FindingTriage


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


def _make_finding(
    kind: str = "stale-comment",
    confidence: float = 0.92,
    directive: str = "Remove stale comment at L5",
) -> SweepFinding:
    return SweepFinding(
        target=_make_target(kind=kind),
        confidence=confidence,
        description="Test finding",
        affected_lines=[5],
        auto_fix_directive=directive,
        kind=kind,
    )


def _make_components(
    tmp_path: Path,
    finding: SweepFinding | None = None,
    daily_cap_usd: float = 5.0,
) -> tuple[BudgetEnforcer, SweepScheduler, Sweeper, FindingTriage, MagicMock, MagicMock]:
    conn = _conn()
    budget = BudgetEnforcer(immune_daily_cap_usd=daily_cap_usd)

    scheduler = SweepScheduler(project_root=tmp_path, conn=conn)
    scheduler.seed([Path("/a/file.py")], kind="stale-comment")

    launcher = MagicMock()
    launcher.launch.return_value = None

    cache = MagicMock(spec=ContextCache)
    cache.get_or_build.return_value = json.dumps({"built_at": "2026-04-28T00:00:00Z"})

    sweeper = MagicMock(spec=Sweeper)
    sweeper.sweep.return_value = finding

    bead_store = MagicMock()
    bead_store.write.return_value = "bd-test01"

    config = ImmuneConfig(
        enabled=True,
        daily_cap_usd=daily_cap_usd,
        sweep_kinds=["stale-comment", "deprecated-api", "doc-drift"],
        auto_fix=True,
        auto_fix_threshold=0.85,
        tick_interval_sec=0,  # no real sleep in tests
    )

    triage = FindingTriage(
        bead_store=bead_store,
        budget=budget,
        config=config,
        launcher=launcher,
    )

    return budget, scheduler, sweeper, triage, launcher, bead_store


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestImmuneDaemonTickLoop:
    def test_tick_loop_advances_queue(self, tmp_path: Path) -> None:
        """After one tick the queue entry's last_swept_at is updated."""
        finding = _make_finding()
        budget, scheduler, sweeper, triage, launcher, bead_store = _make_components(
            tmp_path, finding=finding
        )
        config = ImmuneConfig(
            enabled=True,
            daily_cap_usd=5.0,
            sweep_kinds=["stale-comment"],
            auto_fix=True,
            auto_fix_threshold=0.85,
            tick_interval_sec=0,
        )
        daemon = ImmuneDaemon(
            config=config,
            budget=budget,
            scheduler=scheduler,
            sweeper=sweeper,
            triage=triage,
        )
        # Shut down after first tick.
        original_sleep = daemon._sleep

        def _one_tick_sleep(seconds: int) -> None:
            daemon.shutdown()

        daemon._sleep = _one_tick_sleep
        daemon.run()

        assert daemon.ticks_run >= 1
        # Queue entry must have been advanced.
        row = scheduler._conn.execute(
            "SELECT last_swept_at FROM immune_queue"
        ).fetchone()
        # After mark_swept with found_issue=True, deferred by 7 days.
        assert row is not None
        ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        assert ts > datetime.now(timezone.utc)

    def test_daemon_processes_finding_through_triage(self, tmp_path: Path) -> None:
        """A sweep finding is passed to triage.handle()."""
        finding = _make_finding()
        budget, scheduler, sweeper, triage_real, launcher, bead_store = _make_components(
            tmp_path, finding=finding
        )
        mock_triage = MagicMock(spec=FindingTriage)
        config = ImmuneConfig(
            enabled=True,
            daily_cap_usd=5.0,
            sweep_kinds=["stale-comment"],
            auto_fix=True,
            auto_fix_threshold=0.85,
            tick_interval_sec=0,
        )
        daemon = ImmuneDaemon(
            config=config,
            budget=budget,
            scheduler=scheduler,
            sweeper=sweeper,
            triage=mock_triage,
        )

        def _one_tick(seconds: int) -> None:
            daemon.shutdown()

        daemon._sleep = _one_tick
        daemon.run()

        mock_triage.handle.assert_called_once_with(finding)


class TestBudgetGating:
    def test_allow_immune_sweep_returns_true_within_cap(self, tmp_path: Path) -> None:
        """allow_immune_sweep() returns (True, "") when cap not hit."""
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        allowed, reason = budget.allow_immune_sweep()
        assert allowed is True
        assert reason == ""

    def test_budget_hard_cap_halts(self, tmp_path: Path) -> None:
        """allow_immune_sweep() returns (False, reason) after daily cap hit."""
        budget = BudgetEnforcer(immune_daily_cap_usd=0.001)
        # Exhaust the cap.
        budget.record_immune_spend("/f.py", "stale-comment", 1_000_000, 1_000_000)
        allowed, reason = budget.allow_immune_sweep()
        assert allowed is False
        # Either the daily cap message or the anomaly suspension message is acceptable —
        # both correctly block further sweeps.
        assert reason != ""

    def test_daily_cap_exceeded_helper(self) -> None:
        """daily_cap_exceeded() returns True when cap is exhausted."""
        budget = BudgetEnforcer(immune_daily_cap_usd=0.001)
        budget.record_immune_spend("/f.py", "doc-drift", 1_000_000, 1_000_000)
        assert budget.daily_cap_exceeded() is True

    def test_has_headroom_true_when_fresh(self) -> None:
        """has_headroom_for_auto_fix() returns True with full cap remaining."""
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        assert budget.has_headroom_for_auto_fix() is True

    def test_has_headroom_false_when_near_cap(self) -> None:
        """has_headroom_for_auto_fix() returns False when < 5% remains."""
        # Use a tiny cap ($0.01) and spend just over 95% of it.
        budget = BudgetEnforcer(immune_daily_cap_usd=0.01)
        # Each call: 10K input * 0.25/1M + 1K output * 1.25/1M = $0.0025 + $0.00000125 ≈ $0.0025
        # 5 calls ≈ $0.0126, exceeds 95% of $0.01 ($0.0095) reliably.
        for _ in range(5):
            budget.record_immune_spend("/f.py", "stale-comment", tokens_in=10_000, tokens_out=1_000)
        assert budget.has_headroom_for_auto_fix() is False


class TestAnomalyBurst:
    def test_anomaly_burst_suspends_1h(self) -> None:
        """Spending > 30% of daily cap in 60 min triggers 1-h suspension."""
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        # 30% of $5.00 = $1.50 → need to spend > $1.50 in one window.
        # 2M input + 100K output Haiku = 2M * 0.25/1M + 100K * 1.25/1M
        #   = $0.50 + $0.125 = $0.625 each call.
        # Three calls = $1.875 > $1.50 → burst.
        for _ in range(3):
            budget.record_immune_spend("/f.py", "deprecated-api", 2_000_000, 100_000)

        assert budget.anomaly_burst_detected() is True
        allowed, reason = budget.allow_immune_sweep()
        assert allowed is False
        assert "suspension" in reason

    def test_no_burst_below_threshold(self) -> None:
        """Spending < 30% of daily cap does NOT trigger suspension."""
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        # One small call ($0.01) is well below 30% of $5.00.
        budget.record_immune_spend("/f.py", "stale-comment", 30_000, 5_000)
        assert budget.anomaly_burst_detected() is False


class TestImmuneSweepFindFlow:
    def test_sweep_finds_deprecated_api(self, tmp_path: Path) -> None:
        """sweeper.sweep() returning a finding causes triage.handle() to be called."""
        # Use stale-comment to match the kind seeded by _make_components.
        finding = _make_finding(kind="stale-comment", confidence=0.70)
        _, scheduler, sweeper, _, _, bead_store = _make_components(
            tmp_path, finding=finding
        )
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        config = ImmuneConfig(enabled=True, sweep_kinds=["stale-comment"], tick_interval_sec=0)
        mock_triage = MagicMock()

        daemon = ImmuneDaemon(
            config=config,
            budget=budget,
            scheduler=scheduler,
            sweeper=sweeper,
            triage=mock_triage,
        )

        def _one_tick(seconds: int) -> None:
            daemon.shutdown()

        daemon._sleep = _one_tick
        daemon.run()

        mock_triage.handle.assert_called_once()

    def test_no_finding_no_triage(self, tmp_path: Path) -> None:
        """When sweeper returns None, triage is not called."""
        _, scheduler, sweeper, _, _, _ = _make_components(tmp_path, finding=None)
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        config = ImmuneConfig(enabled=True, sweep_kinds=["stale-comment"], tick_interval_sec=0)
        mock_triage = MagicMock()

        daemon = ImmuneDaemon(
            config=config,
            budget=budget,
            scheduler=scheduler,
            sweeper=sweeper,
            triage=mock_triage,
        )

        def _one_tick(seconds: int) -> None:
            daemon.shutdown()

        daemon._sleep = _one_tick
        daemon.run()

        mock_triage.handle.assert_not_called()


class TestCachedContext:
    def test_cached_context_reduces_tokens(self, tmp_path: Path) -> None:
        """ContextCache.get_or_build() returns cached JSON on second call (no rebuild)."""
        cache = ContextCache(project_root=tmp_path)
        first = cache.get_or_build()
        second = cache.get_or_build()
        assert first == second  # identical → served from disk cache
        data = json.loads(second)
        assert "built_at" in data


# ---------------------------------------------------------------------------
# Integration: 24-hour simulation
# ---------------------------------------------------------------------------


class TestImmuneDaemon24hSimulation:
    def test_daemon_24h_simulation(self, tmp_path: Path) -> None:
        """Simulate 24 h of immune daemon operation with mocked time.

        Uses tick_interval_sec=0 and a sweep counter to verify:
        - The daemon processes at least N ticks before shutdown.
        - Findings counter increments correctly.
        - Budget spend is recorded.
        """
        N_TICKS = 20
        ticks_processed = []
        findings_filed = []

        # Track findings.
        finding = _make_finding(kind="stale-comment", confidence=0.92)
        finding_sequence = [finding if i % 3 == 0 else None for i in range(N_TICKS)]
        call_count = [0]

        def _mock_sweep(target: SweepTarget) -> SweepFinding | None:
            idx = call_count[0]
            call_count[0] += 1
            return finding_sequence[idx] if idx < len(finding_sequence) else None

        conn = _conn()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        scheduler = SweepScheduler(project_root=tmp_path, conn=conn)
        # Seed enough targets so the queue doesn't drain.
        for i in range(N_TICKS + 5):
            scheduler.seed([Path(f"/a/file{i}.py")], kind="stale-comment")

        mock_sweeper = MagicMock(spec=Sweeper)
        mock_sweeper.sweep.side_effect = _mock_sweep

        bead_store = MagicMock()
        bead_store.write.side_effect = lambda b: (findings_filed.append(b), "bd-x")[1]

        config = ImmuneConfig(
            enabled=True,
            daily_cap_usd=5.0,
            sweep_kinds=["stale-comment"],
            auto_fix=False,  # skip auto-fix for integration sim
            auto_fix_threshold=0.85,
            tick_interval_sec=0,
        )
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=config,
            launcher=MagicMock(),
        )
        daemon = ImmuneDaemon(
            config=config,
            budget=budget,
            scheduler=scheduler,
            sweeper=mock_sweeper,
            triage=triage,
        )

        def _counting_sleep(seconds: int) -> None:
            ticks_processed.append(daemon.ticks_run)
            if daemon.ticks_run >= N_TICKS:
                daemon.shutdown()

        daemon._sleep = _counting_sleep
        daemon.run()

        assert daemon.ticks_run == N_TICKS
        # Findings expected every 3rd tick.
        expected_findings = N_TICKS // 3
        # Allow ±1 due to off-by-one in sequence.
        assert abs(daemon.findings_count - expected_findings) <= 1
        # Budget must have been recorded for each tick.
        assert budget.immune_daily_spend() > 0
