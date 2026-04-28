"""Intel subsystem — agent-facing intelligence helpers.

Currently houses :class:`ContextHarvester` (Wave 2.2), which writes a
compact per-(agent_name, domain) learning row after every successful
step so that subsequent dispatches can prepend a "Prior Context" block
to the delegation prompt.

This module is intentionally lightweight: every public entry point is
best-effort and swallows exceptions so that intel work can never block
or break the execution path.
"""
from agent_baton.core.intel.context_harvester import ContextHarvester

__all__ = ["ContextHarvester"]
