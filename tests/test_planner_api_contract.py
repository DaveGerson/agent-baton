"""Canary tests that pin the public API surface of IntelligentPlanner.

These tests MUST pass both before and after the 005b Phase 1 refactor.
If any of these tests break, the refactor has violated the API contract.

Per 005b-phase1-design.md §7 step 1.
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# Import contract
# ---------------------------------------------------------------------------

def test_module_level_constants_importable() -> None:
    """The test suite imports these three constants from planner.py directly."""
    from agent_baton.core.engine.planner import (  # noqa: F401
        _DEFAULT_AGENTS,
        _PHASE_NAMES,
        _TASK_TYPE_KEYWORDS,
    )


def test_intelligent_planner_importable_from_package() -> None:
    """IntelligentPlanner must be re-exported from the engine package."""
    from agent_baton.core.engine import IntelligentPlanner  # noqa: F401


# ---------------------------------------------------------------------------
# Constructor signature (frozen per §1.2)
# ---------------------------------------------------------------------------

def test_init_signature_matches_contract() -> None:
    """Param names, ordering, and defaults must be exactly as specified in §1.2."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    sig = inspect.signature(IntelligentPlanner.__init__)
    params = list(sig.parameters.items())

    # Skip 'self'
    params = [(n, p) for n, p in params if n != "self"]

    expected_names_in_order = [
        "team_context_root",
        "classifier",
        "policy_engine",
        "retro_engine",
        "knowledge_registry",
        "task_classifier",
        "bead_store",
        "project_config",
        # emit_beads was removed during the pipeline refactor (bd-9de9 intent
        # was to opt-out of planning-bead emission, but the refactored planner
        # no longer emits beads from __init__ — the param was never added).
    ]
    actual_names_in_order = [n for n, _ in params]
    assert actual_names_in_order == expected_names_in_order, (
        f"Constructor parameter order changed.\n"
        f"  expected: {expected_names_in_order}\n"
        f"  actual:   {actual_names_in_order}"
    )

    # All eight parameters must have defaults (all are optional)
    for name, param in params:
        assert param.default is not inspect.Parameter.empty, (
            f"Parameter '{name}' must have a default value"
        )

    # Return annotation must be None
    assert sig.return_annotation in (None, "None", inspect.Parameter.empty)


# ---------------------------------------------------------------------------
# create_plan signature (frozen per §1.3)
# ---------------------------------------------------------------------------

def test_create_plan_signature_matches_contract() -> None:
    """create_plan must accept exactly the params specified in §1.3."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    sig = inspect.signature(IntelligentPlanner.create_plan)
    params = list(sig.parameters.items())

    # First param (after self) is positional-or-keyword
    positional = [(n, p) for n, p in params if n != "self" and
                  p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.POSITIONAL_ONLY)]
    assert len(positional) == 1, "create_plan must have exactly one positional param (task_summary)"
    assert positional[0][0] == "task_summary"

    # Keyword-only params
    kw_only = {n: p for n, p in params if p.kind == inspect.Parameter.KEYWORD_ONLY}
    expected_kw = {
        "task_type",
        "complexity",
        "project_root",
        "agents",
        "phases",
        "explicit_knowledge_packs",
        "explicit_knowledge_docs",
        "intervention_level",
        "default_model",
        "gate_scope",
    }
    assert set(kw_only.keys()) == expected_kw, (
        f"Keyword-only param mismatch.\n"
        f"  expected: {sorted(expected_kw)}\n"
        f"  actual:   {sorted(kw_only.keys())}"
    )

    # Check specific defaults
    assert kw_only["intervention_level"].default == "low"
    assert kw_only["gate_scope"].default == "focused"


# ---------------------------------------------------------------------------
# _last_* introspection attributes (§1.5)
# ---------------------------------------------------------------------------

EXPECTED_LAST_ATTRS = [
    "_last_pattern_used",
    "_last_score_warnings",
    "_last_routing_notes",
    "_last_retro_feedback",
    "_last_classification",
    "_last_policy_violations",
    "_last_task_classification",
    "_last_foresight_insights",
    "_last_review_result",
    "_last_team_cost_estimates",
]


def test_last_attrs_present_after_create_plan() -> None:
    """All _last_* attributes must exist on the planner instance after create_plan."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    planner = IntelligentPlanner()
    planner.create_plan("Add a simple health-check endpoint")

    for attr in EXPECTED_LAST_ATTRS:
        assert hasattr(planner, attr), (
            f"IntelligentPlanner is missing attribute '{attr}' after create_plan"
        )


def test_last_attrs_present_before_create_plan() -> None:
    """Most _last_* attributes are initialized in __init__ (except _last_team_cost_estimates)."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    planner = IntelligentPlanner()
    # These should be set in __init__
    init_time_attrs = [a for a in EXPECTED_LAST_ATTRS if a != "_last_team_cost_estimates"]
    for attr in init_time_attrs:
        assert hasattr(planner, attr), (
            f"IntelligentPlanner is missing attribute '{attr}' after __init__"
        )
