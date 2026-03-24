"""Core sub-package — orchestration engine and supporting subsystems.

Architecture layers (dependency flows downward):

    models          Foundation data structures (no internal deps)
    events/observe  Infrastructure: event bus, tracing, metrics
    govern          Policy enforcement, validation, compliance
    engine          Execution core: planner, executor, dispatcher, gates
    runtime         Async execution: worker, supervisor, launchers

Core vs Peripheral subsystems
==============================
CORE — required for any plan to run; circular dependencies here break
every orchestrated task:

  engine/       ExecutionEngine, IntelligentPlanner, PromptDispatcher,
                GateRunner, ExecutionDriver, StatePersistence
  runtime/      TaskWorker, WorkerSupervisor, AgentLauncher
  events/       EventBus
  orchestration/ AgentRegistry, AgentRouter, ContextManager

PERIPHERAL — optional capabilities that enhance but do not gate execution:

  govern/       Classifier, ComplianceChecker, EscalationManager,
                PolicyEngine, Validator
  observe/      Tracer, UsageTracker, Dashboard, Retrospective,
                Telemetry, ContextProfiler
  improve/      EvolutionEngine, AgentScorer, VCSAdapter
  learn/        PatternLearner, BudgetTuner
  distribute/   AsyncDispatcher, PackageManager, SharingManager,
                TransferManager, IncidentManager

Peripheral subsystems depend on core, never the reverse.
"""
from __future__ import annotations

from agent_baton.core.orchestration import AgentRegistry, AgentRouter, ContextManager

# Execution engine
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.gates import GateRunner
from agent_baton.core.engine.protocols import ExecutionDriver
from agent_baton.core.engine.persistence import StatePersistence

# Runtime
from agent_baton.core.runtime.launcher import AgentLauncher
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.core.runtime.supervisor import WorkerSupervisor

# Events
from agent_baton.core.events.bus import EventBus

__all__ = [
    # Orchestration
    "AgentRegistry",
    "AgentRouter",
    "ContextManager",
    # Execution engine
    "ExecutionEngine",
    "IntelligentPlanner",
    "PromptDispatcher",
    "GateRunner",
    "ExecutionDriver",
    "StatePersistence",
    # Runtime
    "AgentLauncher",
    "TaskWorker",
    "WorkerSupervisor",
    # Events
    "EventBus",
]
