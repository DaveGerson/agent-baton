"""ExecutionContext — factory for correctly-wired execution components.

Guarantees that EventBus, ExecutionEngine, and EventPersistence are all
connected to the same bus instance, preventing silent event loss.

Design note
-----------
``ExecutionEngine.__init__`` already auto-wires an ``EventPersistence``
subscriber when a bus is supplied (see ``core/engine/executor.py``).
``ExecutionContext.build`` therefore does NOT create a second persistence
instance — doing so would subscribe the same event stream twice and write
duplicate JSONL lines.  Instead it passes the shared bus to the engine and
surfaces the engine's internal persistence reference via the ``persistence``
field so callers can read events back without needing a separate object.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.runtime.launcher import AgentLauncher


@dataclass
class ExecutionContext:
    """Pre-wired execution components ready for use by TaskWorker."""

    engine: ExecutionEngine
    bus: EventBus
    launcher: AgentLauncher
    persistence: EventPersistence | None = None

    @classmethod
    def build(
        cls,
        *,
        launcher: AgentLauncher,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
        persist_events: bool = True,
    ) -> ExecutionContext:
        """Build a correctly-wired execution context.

        ``ExecutionEngine`` auto-wires ``EventPersistence`` as a bus subscriber
        when a bus is provided, so this factory delegates persistence setup to
        the engine rather than creating a duplicate subscriber.

        Args:
            launcher: Agent launcher implementation.
            team_context_root: Root directory for state files.
            bus: EventBus instance (created if not provided).
            persist_events: When True, the bus is passed to the engine so it
                auto-wires event persistence.  When False, the engine is
                constructed without a bus and no events are persisted.
        """
        bus = bus or EventBus()
        engine_bus = bus if persist_events else None
        engine = ExecutionEngine(
            team_context_root=team_context_root,
            bus=engine_bus,
        )
        # Surface the engine's internal persistence reference so callers can
        # replay events without constructing a parallel reader.
        persistence: EventPersistence | None = getattr(
            engine, "_event_persistence", None
        )
        return cls(
            engine=engine,
            bus=bus,
            launcher=launcher,
            persistence=persistence,
        )
