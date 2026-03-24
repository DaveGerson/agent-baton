"""Dependency injection for the Agent Baton API.

This module owns the module-level singleton instances of every core class
used by the route handlers.  Singletons are initialised lazily on first use
so that importing this module at startup does not trigger file-system I/O or
registry loading.

Usage pattern::

    # app factory calls this once before accepting traffic:
    init_dependencies(team_context_root=Path(".claude/team-context"))

    # route handlers declare dependencies with FastAPI's Depends():
    @router.get("/plans")
    def list_plans(planner: IntelligentPlanner = Depends(get_planner)):
        ...

DECISION: We use a single shared ``EventBus`` instance wired into every
component that accepts one.  This means all events flow through one bus,
enabling the SSE stream and webhook layer to observe all engine activity
regardless of which component emitted the event.

DECISION: Singletons are stored as module-level ``_private`` variables
rather than in a class or dict so that FastAPI's dependency system can call
the provider functions directly without an intermediate container object.
This keeps the dependency declarations in route files as readable as possible.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.events.bus import EventBus
from agent_baton.core.observe.dashboard import DashboardGenerator
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.runtime.decisions import DecisionManager

# ---------------------------------------------------------------------------
# Module-level singletons (None until init_dependencies() is called)
# ---------------------------------------------------------------------------

_team_context_root: Path | None = None

_bus: EventBus | None = None
_engine: ExecutionEngine | None = None
_planner: IntelligentPlanner | None = None
_registry: AgentRegistry | None = None
_decision_manager: DecisionManager | None = None
_dashboard: DashboardGenerator | None = None
_usage_logger: UsageLogger | None = None
_trace_recorder: TraceRecorder | None = None


# ---------------------------------------------------------------------------
# Initialisation — called once by the app factory
# ---------------------------------------------------------------------------

def init_dependencies(
    team_context_root: Path,
    bus: EventBus | None = None,
) -> None:
    """Initialise all dependency singletons.

    Must be called by :func:`agent_baton.api.server.create_app` before the
    application starts serving requests.  Calling it multiple times is safe;
    subsequent calls replace the existing singletons (useful in tests).

    Args:
        team_context_root: Absolute path to the team-context directory.
            Every core class that reads or writes state uses this root.
        bus: Optional pre-constructed ``EventBus``.  When ``None`` a new bus
            is created and shared across all components.
    """
    global _team_context_root
    global _bus
    global _engine
    global _planner
    global _registry
    global _decision_manager
    global _dashboard
    global _usage_logger
    global _trace_recorder

    _team_context_root = team_context_root

    # Shared event bus — either the one supplied by the caller or a fresh one.
    _bus = bus if bus is not None else EventBus()

    # Core observe helpers — these have no bus dependency.
    _usage_logger = UsageLogger(log_path=team_context_root / "usage-log.jsonl")
    _trace_recorder = TraceRecorder(team_context_root=team_context_root)

    # Engine — wires the shared bus so it emits task/phase events.
    _engine = ExecutionEngine(
        team_context_root=team_context_root,
        bus=_bus,
    )

    # Planner — reads agents from disk; scoped to the team-context root.
    _planner = IntelligentPlanner(team_context_root=team_context_root)

    # Registry — load agents eagerly so the first /agents request is fast.
    _registry = AgentRegistry()
    _registry.load_default_paths()

    # Decision manager — uses the shared bus to publish decision events.
    _decision_manager = DecisionManager(
        decisions_dir=team_context_root / "decisions",
        bus=_bus,
    )

    # Dashboard — wraps the usage logger we already created.
    _dashboard = DashboardGenerator(usage_logger=_usage_logger)


# ---------------------------------------------------------------------------
# FastAPI dependency providers
# ---------------------------------------------------------------------------

def get_bus() -> EventBus:
    """Return the shared :class:`~agent_baton.core.events.bus.EventBus` instance.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _bus is None:
        raise RuntimeError(
            "EventBus not initialised. Call init_dependencies() before serving requests."
        )
    return _bus


def get_engine() -> ExecutionEngine:
    """Return the shared :class:`~agent_baton.core.engine.executor.ExecutionEngine`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _engine is None:
        raise RuntimeError(
            "ExecutionEngine not initialised. Call init_dependencies() before serving requests."
        )
    return _engine


def get_planner() -> IntelligentPlanner:
    """Return the shared :class:`~agent_baton.core.engine.planner.IntelligentPlanner`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _planner is None:
        raise RuntimeError(
            "IntelligentPlanner not initialised. Call init_dependencies() before serving requests."
        )
    return _planner


def get_registry() -> AgentRegistry:
    """Return the shared :class:`~agent_baton.core.orchestration.registry.AgentRegistry`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _registry is None:
        raise RuntimeError(
            "AgentRegistry not initialised. Call init_dependencies() before serving requests."
        )
    return _registry


def get_decision_manager() -> DecisionManager:
    """Return the shared :class:`~agent_baton.core.runtime.decisions.DecisionManager`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _decision_manager is None:
        raise RuntimeError(
            "DecisionManager not initialised. Call init_dependencies() before serving requests."
        )
    return _decision_manager


def get_dashboard() -> DashboardGenerator:
    """Return the shared :class:`~agent_baton.core.observe.dashboard.DashboardGenerator`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _dashboard is None:
        raise RuntimeError(
            "DashboardGenerator not initialised. Call init_dependencies() before serving requests."
        )
    return _dashboard


def get_usage_logger() -> UsageLogger:
    """Return the shared :class:`~agent_baton.core.observe.usage.UsageLogger`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _usage_logger is None:
        raise RuntimeError(
            "UsageLogger not initialised. Call init_dependencies() before serving requests."
        )
    return _usage_logger


def get_trace_recorder() -> TraceRecorder:
    """Return the shared :class:`~agent_baton.core.observe.trace.TraceRecorder`.

    Raises:
        RuntimeError: If :func:`init_dependencies` has not been called.
    """
    if _trace_recorder is None:
        raise RuntimeError(
            "TraceRecorder not initialised. Call init_dependencies() before serving requests."
        )
    return _trace_recorder
