"""Shared fixtures for tests/engine/planning -- hermetic classifier isolation.

``IntelligentPlanner()`` constructed with no explicit ``task_classifier``
defaults to ``FallbackClassifier`` (agent_baton/core/engine/classifier.py),
which tries a *real* Sonnet call via ``HeadlessClaude`` (``claude --print``)
whenever the ``claude`` binary happens to be on ``PATH`` --
``_talent_agent_available()`` only checks binary presence, it does not
require ``ANTHROPIC_API_KEY`` or any other opt-in signal.

In a sandbox where the ``claude`` CLI is reachable (this repo's own dev
containers included -- we run *inside* Claude Code), any test in this
directory that builds ``IntelligentPlanner()`` without pinning a
deterministic classifier silently makes a live, network-dependent LLM call
instead of exercising the documented "mock mode by default ... without API
keys" contract (see ``test_planner_smoke.py``'s module docstring). The LLM
is free to recommend any agent in the registry for any phase, including
picking a reviewer-class agent (e.g. ``security-reviewer``) for an
Implement-type phase -- something ``ValidationStage``'s
``agent_phase_mismatch`` check correctly rejects. The result is a plan
that is sometimes valid and sometimes not for byte-identical input,
observed as intermittent ``PlanQualityError`` failures uncorrelated with
any code change (bd: phase-5 gate repair).

This autouse fixture forces the ``TalentAgentClassifier`` probe unavailable
so ``FallbackClassifier`` always falls through to the deterministic
``KeywordClassifier`` -- unless a test opts into real integration coverage
via ``BATON_PLANNER_INTEGRATION=1`` (the flag ``test_planner_smoke.py``
already documents for that purpose), in which case this fixture is a no-op
and the live path is exercised exactly as before.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

_INTEGRATION = os.environ.get("BATON_PLANNER_INTEGRATION", "").lower() in {"1", "true", "yes"}


@pytest.fixture(autouse=True)
def _hermetic_task_classifier(monkeypatch: Any) -> None:
    """Prevent live ``claude`` CLI classification calls in this test suite.

    Applies to every test collected under ``tests/engine/planning/``
    (autouse) unless ``BATON_PLANNER_INTEGRATION=1`` requests real
    end-to-end coverage.
    """
    if _INTEGRATION:
        return
    import agent_baton.core.engine.classifier as classifier_mod

    monkeypatch.setattr(
        classifier_mod, "_talent_agent_available", lambda: (False, None)
    )
