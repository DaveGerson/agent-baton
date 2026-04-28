"""Tests for agent_baton.core.predict.accept (Wave 6.2 Part C, bd-03b0).

Covers:
- test_accept_handoff_to_wave_5_3
- Handoff when worktree_handle is None (graceful no-op)
- SpeculativePipeliner.build_handoff called with correct args
- HandoffProtocol returned with expected fields
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.predict.accept import handoff_to_pipeliner, _DEFAULT_DIRECTIVE
from agent_baton.core.predict.classifier import IntentClassification, IntentKind
from agent_baton.core.predict.speculator import Speculation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_classification(
    intent: IntentKind = IntentKind.ADD_FEATURE,
    summary: str = "implement login feature",
    directive_kind: str = "implement",
) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=0.85,
        scope=[Path("src/login.py")],
        summary=summary,
        speculation_directive={
            "kind": directive_kind,
            "prompt": "Implement JWT login in src/login.py",
            "estimated_files_changed": 2,
        },
    )


def _make_worktree_handle(tmp_path: Path) -> MagicMock:
    handle = MagicMock()
    handle.path = tmp_path / "spec-wt"
    handle.path.mkdir(parents=True, exist_ok=True)
    handle.branch = "worktree/predict-abc12345/speculate"
    return handle


def _make_pipeliner(handoff_result: Any = None) -> MagicMock:
    """Build a mock SpeculativePipeliner whose build_handoff returns handoff_result."""
    pipeliner = MagicMock()
    pipeliner._speculations = {}

    # build_handoff returns the mock HandoffProtocol.
    mock_handoff = MagicMock() if handoff_result is None else handoff_result
    mock_handoff.spec_id = "abc12345"
    mock_handoff.target_model = "claude-sonnet"
    mock_handoff.worktree_path = "/some/worktree"
    mock_handoff.prompt = "handoff prompt"
    pipeliner.build_handoff.return_value = mock_handoff

    return pipeliner


# ---------------------------------------------------------------------------
# test_accept_handoff_to_wave_5_3
# ---------------------------------------------------------------------------


class TestHandoffToPipeliner:
    def test_delegates_to_build_handoff(self, tmp_path: Path) -> None:
        """handoff_to_pipeliner must call pipeliner.build_handoff with correct args."""
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="abc12345",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        result = handoff_to_pipeliner(spec, pipeliner, target_model="claude-sonnet")

        assert result is not None
        pipeliner.build_handoff.assert_called_once()
        call_kwargs = pipeliner.build_handoff.call_args
        # The spec_id should be passed as the first positional arg.
        assert call_kwargs[0][0] == "abc12345"
        # target_model should be claude-sonnet.
        assert call_kwargs[1].get("target_model") == "claude-sonnet" or \
               "claude-sonnet" in str(call_kwargs)

    def test_default_directive_passed(self, tmp_path: Path) -> None:
        """The default directive string is forwarded to the handoff description."""
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="def99999",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        handoff_to_pipeliner(spec, pipeliner)

        # build_handoff must have been called with next_step_description containing
        # the default directive.
        call_kwargs = pipeliner.build_handoff.call_args[1]
        desc = call_kwargs.get("next_step_description", "")
        assert _DEFAULT_DIRECTIVE in desc

    def test_classifier_prompt_appended_to_description(self, tmp_path: Path) -> None:
        """The classifier's directive prompt is appended to the description."""
        handle = _make_worktree_handle(tmp_path)
        classification = _make_classification()
        spec = Speculation(
            spec_id="fed11111",
            intent=classification,
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        handoff_to_pipeliner(spec, pipeliner)

        call_kwargs = pipeliner.build_handoff.call_args[1]
        desc = call_kwargs.get("next_step_description", "")
        # The classifier prompt should appear in the description.
        assert "JWT" in desc

    def test_returns_none_when_no_worktree_handle(self) -> None:
        """handoff_to_pipeliner returns None when worktree_handle is None."""
        spec = Speculation(
            spec_id="nohandle1",
            intent=_make_classification(),
            worktree_handle=None,
            status="accepted",
        )
        pipeliner = _make_pipeliner()

        result = handoff_to_pipeliner(spec, pipeliner)

        assert result is None
        # build_handoff must NOT have been called.
        pipeliner.build_handoff.assert_not_called()

    def test_returns_none_when_build_handoff_returns_none(self, tmp_path: Path) -> None:
        """When pipeliner.build_handoff returns None, handoff_to_pipeliner returns None.

        This happens when the worktree has uncommitted edits (safety guard).
        """
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="nullhand1",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = MagicMock()
        pipeliner._speculations = {}
        pipeliner.build_handoff.return_value = None   # simulates uncommitted edits

        result = handoff_to_pipeliner(spec, pipeliner)

        assert result is None

    def test_injects_speculation_record_into_pipeliner(self, tmp_path: Path) -> None:
        """handoff_to_pipeliner injects a SpeculationRecord into the pipeliner."""
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="inject01",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        handoff_to_pipeliner(spec, pipeliner)

        # The spec_id must have been injected into pipeliner._speculations.
        assert "inject01" in pipeliner._speculations

    def test_custom_directive_used(self, tmp_path: Path) -> None:
        """A custom directive string overrides the default."""
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="custom01",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        custom = "my custom directive"
        handoff_to_pipeliner(spec, pipeliner, directive=custom)

        call_kwargs = pipeliner.build_handoff.call_args[1]
        desc = call_kwargs.get("next_step_description", "")
        assert custom in desc

    def test_target_model_forwarded(self, tmp_path: Path) -> None:
        """The target_model parameter is forwarded to build_handoff."""
        handle = _make_worktree_handle(tmp_path)
        spec = Speculation(
            spec_id="model001",
            intent=_make_classification(),
            worktree_handle=handle,
            status="accepted",
            started_at="2026-04-28T00:00:00+00:00",
        )
        pipeliner = _make_pipeliner()

        handoff_to_pipeliner(spec, pipeliner, target_model="claude-opus")

        call_kwargs = pipeliner.build_handoff.call_args[1]
        assert call_kwargs.get("target_model") == "claude-opus"


# ---------------------------------------------------------------------------
# Budget enforcer predict methods (integrated here as they relate to accept)
# ---------------------------------------------------------------------------


class TestBudgetEnforcerPredict:
    def test_allow_speculation_predict_under_cap(self) -> None:
        """allow_speculation_predict returns True when under the daily cap."""
        from agent_baton.core.govern.budget import BudgetEnforcer
        budget = BudgetEnforcer()
        allowed, reason = budget.allow_speculation_predict()
        assert allowed is True
        assert reason == ""

    def test_allow_speculation_predict_over_cap(self) -> None:
        """allow_speculation_predict returns False when cap exhausted."""
        from agent_baton.core.govern.budget import BudgetEnforcer
        budget = BudgetEnforcer()
        budget._ensure_predict_state()
        # Force spend over cap.
        from unittest.mock import patch as _patch
        with _patch(
            "agent_baton.core.govern.budget._today_str",
            return_value="2026-04-28",
        ):
            budget._predict_daily_cap = 2.00
            budget._predict_daily_spend["2026-04-28"] = 3.00
            allowed, reason = budget.allow_speculation_predict()
            assert allowed is False
            assert "cap exhausted" in reason

    def test_record_predict_outcome_auto_disable(self) -> None:
        """Auto-disable fires after 50 rejections."""
        from agent_baton.core.govern.budget import BudgetEnforcer
        budget = BudgetEnforcer()
        for _ in range(50):
            budget.record_predict_outcome("spec-x", accepted=False)
        assert budget.predict_is_disabled()

    def test_record_predict_outcome_no_disable_above_threshold(self) -> None:
        """No auto-disable when accept rate >= 20%."""
        from agent_baton.core.govern.budget import BudgetEnforcer
        budget = BudgetEnforcer()
        # 40 reject + 10 accept = 20% → exactly at limit, no disable.
        for _ in range(40):
            budget.record_predict_outcome("spec-x", accepted=False)
        for _ in range(10):
            budget.record_predict_outcome("spec-x", accepted=True)
        assert not budget.predict_is_disabled()

    def test_predict_daily_spend_accumulates(self) -> None:
        """predict_daily_spend accumulates token costs."""
        from agent_baton.core.govern.budget import BudgetEnforcer
        budget = BudgetEnforcer()
        cost = budget.record_speculation_spend_predict("spec-1", 10_000, 2_000)
        assert cost > 0.0
        assert budget.predict_daily_spend() == pytest.approx(cost)
