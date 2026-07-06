"""Tests for the opt-in LLM plan review (BATON_PLAN_REVIEW env var).

Covers three scenarios:
1. Flag unset → HeadlessClaude is never constructed.
2. Flag set to a recognised model alias → review runs with the correct model.
3. Flag set to an unrecognised value → warning logged, HeadlessClaude not constructed.

These tests target ``IntelligentPlanner._review_plan_with_llm`` in isolation
from ``FallbackClassifier``'s *separate* ``TalentAgentClassifier`` ->
``HeadlessClaude`` probe that the classification stage runs unconditionally
(regardless of ``BATON_PLAN_REVIEW``) to decide between LLM-backed and
keyword-heuristic classification. The planner's ``task_classifier`` is
pinned to the deterministic ``KeywordClassifier`` -- the same pattern used
by ``tests/e2e/test_manager_mode_planning.py`` -- so the single
``HeadlessClaude`` mock installed in each test below observes only the
post-pipeline review call under test, not the unrelated classification
probe.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def planner():
    """Build a fresh IntelligentPlanner for each test.

    ``task_classifier=KeywordClassifier()`` bypasses ``FallbackClassifier``'s
    default ``TalentAgentClassifier``, which would otherwise construct its
    own ``HeadlessClaude`` instance during classification and confound the
    ``HeadlessClaude`` mocks these tests install to observe the (unrelated)
    ``BATON_PLAN_REVIEW`` review step.
    """
    from agent_baton.core.engine.classifier import KeywordClassifier
    from agent_baton.core.engine.planning.planner import IntelligentPlanner
    return IntelligentPlanner(task_classifier=KeywordClassifier())


class TestPlanReview:

    def test_review_flag_unset_no_headless_constructed(self, planner, monkeypatch):
        """When BATON_PLAN_REVIEW is unset, HeadlessClaude must never be instantiated."""
        monkeypatch.delenv("BATON_PLAN_REVIEW", raising=False)

        with patch(
            "agent_baton.core.runtime.headless.HeadlessClaude",
            side_effect=RuntimeError("HeadlessClaude must not be called"),
        ) as mock_hc:
            plan = planner.create_plan("Add a button")

        assert plan is not None
        mock_hc.assert_not_called()

    def test_review_flag_sonnet_invokes_review_with_correct_model(
        self, planner, monkeypatch
    ):
        """When BATON_PLAN_REVIEW=sonnet, run_sync must be called with model='sonnet'."""
        monkeypatch.setenv("BATON_PLAN_REVIEW", "sonnet")

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = '{"verdict": "PASS", "notes": "Plan is well-structured"}'

        mock_instance = MagicMock()
        mock_instance.is_available = True
        mock_instance.run_sync.return_value = mock_result

        mock_hc_cls = MagicMock(return_value=mock_instance)

        with patch(
            "agent_baton.core.runtime.headless.HeadlessClaude", mock_hc_cls
        ):
            plan = planner.create_plan("Add an endpoint")

        assert plan is not None
        mock_instance.run_sync.assert_called_once()
        call_kwargs = mock_instance.run_sync.call_args
        # The model kwarg must be "sonnet" (not hardcoded "opus" or anything else).
        assert call_kwargs.kwargs.get("model") == "sonnet" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "sonnet"
        ), (
            f"Expected run_sync to be called with model='sonnet', "
            f"got args={call_kwargs.args!r} kwargs={call_kwargs.kwargs!r}"
        )

    def test_review_flag_garbage_warns_and_skips(self, planner, monkeypatch, caplog):
        """When BATON_PLAN_REVIEW=garbage, a warning is logged and review is skipped."""
        monkeypatch.setenv("BATON_PLAN_REVIEW", "garbage")

        with patch(
            "agent_baton.core.runtime.headless.HeadlessClaude",
            side_effect=RuntimeError("HeadlessClaude must not be called"),
        ) as mock_hc:
            with caplog.at_level(logging.WARNING):
                plan = planner.create_plan("Add a button")

        assert plan is not None
        mock_hc.assert_not_called()

        # Confirm a warning was emitted containing both the env-var name and the bad value.
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        matching = [
            m for m in warning_messages
            if "BATON_PLAN_REVIEW" in str(m) and "garbage" in str(m)
        ]
        assert matching, (
            f"Expected a warning mentioning 'BATON_PLAN_REVIEW' and 'garbage'. "
            f"Captured warning messages: {warning_messages!r}"
        )
