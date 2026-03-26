"""Execution engine sub-package.

Contains the core components that drive orchestrated task execution:

- ``IntelligentPlanner`` -- creates data-driven execution plans informed by
  historical patterns, agent performance scores, and budget recommendations.
- ``ExecutionEngine`` -- state machine that advances plans phase-by-phase,
  returning DISPATCH / GATE / APPROVAL / COMPLETE actions for the caller.
- ``PromptDispatcher`` -- generates delegation prompts following the
  comms-protocols template, including knowledge delivery and path enforcement.
- ``GateRunner`` -- evaluates QA gate results (build, test, lint, spec, review).
- ``StatePersistence`` -- atomic read/write of ``ExecutionState`` to disk,
  supporting namespaced concurrent executions and crash recovery.
- ``KnowledgeResolver`` -- 4-layer pipeline that selects and attaches knowledge
  documents to plan steps with inline/reference delivery decisions.
- ``ExecutionDriver`` -- protocol defining the contract between the async
  worker layer and the synchronous state machine.

The engine is intentionally synchronous and stateless between calls.  The
async runtime layer (``core/runtime/``) wraps the engine for concurrent
dispatch.
"""
from __future__ import annotations

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.gates import GateRunner
from agent_baton.core.engine.protocols import ExecutionDriver
from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver

__all__ = [
    "ExecutionEngine",
    "ExecutionDriver",
    "StatePersistence",
    "IntelligentPlanner",
    "PromptDispatcher",
    "GateRunner",
    "KnowledgeResolver",
]
