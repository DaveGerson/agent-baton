"""Backward-compat shim — see :mod:`agent_baton.core.engine.planning`.

The IntelligentPlanner monolith that used to live in this file has moved
to a pipeline of stages under ``agent_baton.core.engine.planning``.  This
module exists so external code that still does ::

    from agent_baton.core.engine.planner import IntelligentPlanner

keeps working.

The exported ``IntelligentPlanner`` class is the new pipeline-based shell
(see :mod:`agent_baton.core.engine.planning.planner`).  Other module-level
symbols (``_DEFAULT_AGENTS``, ``_TASK_TYPE_KEYWORDS``,
``_derive_expected_outcome`` etc.) are re-exported from the legacy
implementation in :mod:`agent_baton.core.engine.planning._legacy_planner`
while we incrementally port their callers; the legacy module will be
deleted once the port completes.
"""
from __future__ import annotations

# --- Public class: NEW pipeline-based planner ---
from agent_baton.core.engine.planning.planner import IntelligentPlanner

# --- Re-exports of legacy module-level symbols still imported elsewhere ---
# These are read-only constants and pure helpers; they stay in the legacy
# module until the corresponding stage / helper port lands.
from agent_baton.core.engine.planning._legacy_planner import (
    GateScope,
    _AGENT_ALIASES,
    _AGENT_DELIVERABLES,
    _DEFAULT_AGENTS,
    _PHASE_NAMES,
    _STEP_TEMPLATES,
    _TASK_TYPE_KEYWORDS,
    _coverage_package_for_changes,
    _derive_expected_outcome,
    _step_type_for_agent,
    _test_files_for_changes,
)

__all__ = [
    "IntelligentPlanner",
    "GateScope",
    "_AGENT_ALIASES",
    "_AGENT_DELIVERABLES",
    "_DEFAULT_AGENTS",
    "_PHASE_NAMES",
    "_STEP_TEMPLATES",
    "_TASK_TYPE_KEYWORDS",
    "_coverage_package_for_changes",
    "_derive_expected_outcome",
    "_step_type_for_agent",
    "_test_files_for_changes",
]
