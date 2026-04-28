"""Tests for Wave 6.2 Part B — FindingTriage (bd-be76).

Covers:
- Bead is always filed (regardless of confidence)
- High confidence + correct kind + headroom → auto-fix dispatched
- Low confidence → bead only, no auto-fix
- Budget headroom exhausted → bead only, no auto-fix
- kind not in AUTO_FIX_KINDS → bead only, no auto-fix
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.govern.budget import BudgetEnforcer
from agent_baton.core.immune.daemon import ImmuneConfig
from agent_baton.core.immune.scheduler import SweepTarget
from agent_baton.core.immune.sweeper import SweepFinding
from agent_baton.core.immune.triage import FindingTriage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    confidence: float = 0.90,
    kind: str = "stale-comment",
    auto_fix_directive: str = "Remove stale comment at L10",
    path: str = "/project/src/module.py",
) -> SweepFinding:
    target = SweepTarget(
        path=Path(path),
        kind=kind,
        last_swept_at="2024-01-01T00:00:00Z",
        priority=1.0,
    )
    return SweepFinding(
        target=target,
        confidence=confidence,
        description="Test finding: stale comment",
        affected_lines=[10],
        auto_fix_directive=auto_fix_directive,
        kind=kind,
    )


def _make_bead_store() -> MagicMock:
    store = MagicMock()
    store.write.return_value = "bd-test01"
    return store


def _make_launcher() -> MagicMock:
    launcher = MagicMock()
    launcher.launch.return_value = None
    return launcher


def _make_config(
    auto_fix: bool = True,
    auto_fix_threshold: float = 0.85,
) -> ImmuneConfig:
    return ImmuneConfig(
        enabled=True,
        auto_fix=auto_fix,
        auto_fix_threshold=auto_fix_threshold,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFindingTriageBeadAlways:
    def test_bead_filed_for_high_confidence(self, tmp_path: Path) -> None:
        """A bead is filed for a high-confidence finding."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=_make_launcher(),
        )
        triage.handle(_make_finding(confidence=0.95))
        bead_store.write.assert_called_once()

    def test_bead_filed_for_low_confidence(self, tmp_path: Path) -> None:
        """A bead is ALWAYS filed, even for low-confidence findings."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=launcher,
        )
        triage.handle(_make_finding(confidence=0.30))
        bead_store.write.assert_called_once()
        launcher.launch.assert_not_called()

    def test_bead_id_returned(self, tmp_path: Path) -> None:
        """handle() returns the filed bead_id."""
        bead_store = _make_bead_store()
        bead_store.write.return_value = "bd-abc123"
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=_make_launcher(),
        )
        bead_id = triage.handle(_make_finding(confidence=0.60))
        assert bead_id == "bd-abc123"


class TestAutoFixGating:
    def test_high_confidence_correct_kind_dispatches_autofix(
        self, tmp_path: Path
    ) -> None:
        """confidence >= 0.85 + kind in AUTO_FIX_KINDS + headroom → auto-fix."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=launcher,
        )
        triage.handle(_make_finding(confidence=0.92, kind="stale-comment"))
        launcher.launch.assert_called_once()
        call_kwargs = launcher.launch.call_args[1]
        assert call_kwargs.get("agent_name") == "self-heal-haiku"

    def test_low_confidence_no_autofix(self, tmp_path: Path) -> None:
        """confidence < threshold → auto-fix NOT dispatched."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(auto_fix_threshold=0.85),
            launcher=launcher,
        )
        triage.handle(_make_finding(confidence=0.50, kind="stale-comment"))
        launcher.launch.assert_not_called()

    def test_kind_not_in_allowlist_no_autofix(self, tmp_path: Path) -> None:
        """kind NOT in AUTO_FIX_KINDS → auto-fix NOT dispatched."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=launcher,
        )
        # "untested-edges" is not in AUTO_FIX_KINDS
        triage.handle(_make_finding(confidence=0.95, kind="untested-edges", auto_fix_directive=""))
        launcher.launch.assert_not_called()

    def test_budget_exhausted_no_autofix(self, tmp_path: Path) -> None:
        """Daily cap exhausted → auto-fix NOT dispatched."""
        bead_store = _make_bead_store()
        # Tiny cap so headroom check fails immediately.
        budget = BudgetEnforcer(immune_daily_cap_usd=0.0001)
        # Pre-exhaust the budget.
        budget.record_immune_spend("/f.py", "stale-comment", 1_000_000, 1_000_000)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=launcher,
        )
        triage.handle(_make_finding(confidence=0.95, kind="stale-comment"))
        launcher.launch.assert_not_called()

    def test_auto_fix_disabled_in_config(self, tmp_path: Path) -> None:
        """config.auto_fix=False → auto-fix NOT dispatched even at high confidence."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(auto_fix=False),
            launcher=launcher,
        )
        triage.handle(_make_finding(confidence=0.99, kind="stale-comment"))
        launcher.launch.assert_not_called()

    def test_missing_auto_fix_directive_no_dispatch(self, tmp_path: Path) -> None:
        """Empty auto_fix_directive → auto-fix NOT dispatched even if all gates pass."""
        bead_store = _make_bead_store()
        budget = BudgetEnforcer(immune_daily_cap_usd=5.0)
        launcher = _make_launcher()
        triage = FindingTriage(
            bead_store=bead_store,
            budget=budget,
            config=_make_config(),
            launcher=launcher,
        )
        triage.handle(
            _make_finding(confidence=0.95, kind="stale-comment", auto_fix_directive="")
        )
        launcher.launch.assert_not_called()
