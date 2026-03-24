"""Core sub-package — orchestration engine and supporting subsystems.

Architecture layers (dependency flows downward):

    models          Foundation data structures (no internal deps)
    events/observe  Infrastructure: event bus, tracing, metrics
    govern          Policy enforcement, validation, compliance
    engine          Execution core: planner, executor, dispatcher, gates
    runtime         Async execution: worker, supervisor, launchers
"""
from __future__ import annotations

from agent_baton.core.orchestration import AgentRegistry, AgentRouter, ContextManager

__all__ = [
    "AgentRegistry",
    "AgentRouter",
    "ContextManager",
]
