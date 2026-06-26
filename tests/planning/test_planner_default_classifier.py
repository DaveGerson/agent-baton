"""Regression tests for the planner's default task-classifier wiring.

Covers:
- The default ``_task_classifier`` is ``FallbackClassifier`` (LLM-backed),
  NOT a bare ``KeywordClassifier``.
- The ``CLIValidatedClassifier`` dangling-import regression does not recur:
  ``FallbackClassifier`` is importable and constructible without errors.
- When ``FallbackClassifier`` construction is forcibly broken, the planner
  degrades gracefully to ``KeywordClassifier`` AND emits a ``WARNING`` log
  (not silent, not an INFO).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from agent_baton.core.engine.classifier import FallbackClassifier, KeywordClassifier


# ---------------------------------------------------------------------------
# 1. Default classifier is FallbackClassifier
# ---------------------------------------------------------------------------

class TestPlannerDefaultClassifier:
    def test_default_task_classifier_is_fallback_not_keyword(self):
        """The planner's default _task_classifier must be FallbackClassifier.

        This guards against the CLIValidatedClassifier dangling-import bug
        where a missing class caused a silent ImportError and silent degradation
        to KeywordClassifier.
        """
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        assert isinstance(planner._task_classifier, FallbackClassifier), (
            f"Expected FallbackClassifier but got {type(planner._task_classifier).__name__}. "
            "The planner may have silently degraded to KeywordClassifier due to "
            "a bad import in the default-classifier branch."
        )

    def test_default_task_classifier_is_not_bare_keyword_classifier(self):
        """The planner must NOT use KeywordClassifier as its primary/default."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        assert not isinstance(planner._task_classifier, KeywordClassifier), (
            "Planner defaulted to KeywordClassifier (pure heuristics). "
            "FallbackClassifier should be used instead so LLM-backed "
            "classification is attempted first."
        )

    def test_explicit_task_classifier_is_honoured(self):
        """An explicitly injected classifier must be used as-is."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        kw = KeywordClassifier()
        planner = IntelligentPlanner(task_classifier=kw)
        assert planner._task_classifier is kw


# ---------------------------------------------------------------------------
# 2. FallbackClassifier is importable and constructible (no dangling imports)
# ---------------------------------------------------------------------------

class TestFallbackClassifierImportable:
    def test_fallback_classifier_can_be_imported(self):
        """FallbackClassifier must be importable from the canonical path."""
        from agent_baton.core.engine.classifier import FallbackClassifier  # noqa: F401

    def test_fallback_classifier_can_be_constructed(self):
        """FallbackClassifier() must construct without raising."""
        from agent_baton.core.engine.classifier import FallbackClassifier

        classifier = FallbackClassifier()
        assert classifier is not None

    def test_no_clivalidated_classifier_in_planner_default_branch(self):
        """Guard: the planner source must not reference the non-existent
        CLIValidatedClassifier in its default-wiring branch."""
        import inspect
        from agent_baton.core.engine.planning import planner as planner_module

        source = inspect.getsource(planner_module)
        assert "CLIValidatedClassifier" not in source, (
            "planner.py still references CLIValidatedClassifier which does not "
            "exist in the codebase. Remove or replace it."
        )


# ---------------------------------------------------------------------------
# 3. Graceful degradation to KeywordClassifier emits WARNING
# ---------------------------------------------------------------------------

class TestPlannerGracefulDegradation:
    def test_fallback_construction_failure_uses_keyword_classifier(self, caplog):
        """When FallbackClassifier cannot be constructed, planner falls back to
        KeywordClassifier and must emit a WARNING-level log (not silent).

        Patch at the source module (agent_baton.core.engine.classifier) because
        the planner imports FallbackClassifier lazily inside the try/except block
        via ``from agent_baton.core.engine.classifier import FallbackClassifier``.
        Python's ``from X import Y`` binds from sys.modules[X].Y at call time, so
        patching the source attribute is the correct interception point.
        """
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        # Force FallbackClassifier to raise during construction by patching at source
        with patch(
            "agent_baton.core.engine.classifier.FallbackClassifier",
            side_effect=RuntimeError("simulated construction failure"),
        ):
            with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.planning.planner"):
                planner = IntelligentPlanner()

        assert isinstance(planner._task_classifier, KeywordClassifier), (
            "When FallbackClassifier fails to construct, planner should fall "
            "back to KeywordClassifier."
        )
        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert warning_messages, (
            "No WARNING was emitted when the planner degraded to KeywordClassifier. "
            "Silent degradation must not occur — operators need visibility."
        )

    def test_degradation_warning_mentions_keyword_or_fallback(self, caplog):
        """The degradation WARNING must provide enough context to be actionable."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        with patch(
            "agent_baton.core.engine.classifier.FallbackClassifier",
            side_effect=RuntimeError("simulated construction failure"),
        ):
            with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.planning.planner"):
                IntelligentPlanner()

        warning_text = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ).lower()
        # The warning should mention what happened (keyword/fallback) or how to fix
        assert any(
            keyword in warning_text
            for keyword in ("keyword", "fallback", "classifier", "degrad")
        ), (
            f"Warning message lacks actionable context: {warning_text!r}"
        )
