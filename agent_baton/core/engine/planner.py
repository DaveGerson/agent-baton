"""Backward-compat shim — see :mod:`agent_baton.core.engine.planning`.

The IntelligentPlanner monolith that used to live in this file has moved
to a pipeline of stages under ``agent_baton.core.engine.planning``.  This
module exists so external code that still does ::

    from agent_baton.core.engine.planner import IntelligentPlanner

keeps working.

All symbols are re-exported from the new locations in utils/ and rules/.
"""
from __future__ import annotations

# --- Public class: pipeline-based planner ---
from agent_baton.core.engine.planning.planner import IntelligentPlanner

# --- GateScope and gate helpers ---
from agent_baton.core.engine.planning.utils.gates import (
    GateScope,
    _test_files_for_changes,
    _coverage_package_for_changes,
)

# --- Phase-builder private helpers ---
from agent_baton.core.engine.planning.utils.phase_builder import (
    _derive_expected_outcome,
    _step_type_for_agent,
)

# --- Text-parser constants ---
from agent_baton.core.engine.planning.utils.text_parsers import (
    _TASK_TYPE_KEYWORDS,
    _AGENT_ALIASES,
)

# --- Rules constants ---
from agent_baton.core.engine.planning.rules.default_agents import _DEFAULT_AGENTS
from agent_baton.core.engine.planning.rules.phase_templates import _PHASE_NAMES
from agent_baton.core.engine.planning.rules.templates import (
    _AGENT_DELIVERABLES,
    _STEP_TEMPLATES,
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
