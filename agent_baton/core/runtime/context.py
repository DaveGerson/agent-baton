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
    """Pre-wired execution components ready for use by TaskWorker.

    The factory method ``build()`` guarantees that all components share the
    same ``EventBus`` instance, preventing silent event loss from mismatched
    bus references.  Callers should always use ``build()`` rather than
    constructing this dataclass directly.

    Attributes:
        engine: The execution engine driving plan state transitions.
        bus: Shared event bus for domain event publication and subscription.
        launcher: Agent launcher implementation (real or dry-run).
        persistence: EventPersistence reference from the engine, surfaced
            so callers can replay events without constructing a parallel
            reader.  May be None if event persistence is disabled.
    """

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
        task_id: str | None = None,
        storage=None,  # StorageBackend | None — explicit override
        use_project_storage: bool = True,
    ) -> ExecutionContext:
        """Build a correctly-wired execution context.

        ``ExecutionEngine`` auto-wires ``EventPersistence`` as a bus subscriber
        when a bus is provided, so this factory delegates persistence setup to
        the engine rather than creating a duplicate subscriber.

        F098 fix: this factory is the daemon / worker-supervisor entry point.
        Before this fix it always built an ``ExecutionEngine`` with no
        storage backend, so a daemon-driven task's usage landed only in
        ``usage-log.jsonl`` while the CLI path (which injects
        ``get_project_storage()``) wrote only to ``baton.db``. The two sinks
        were disjoint — ``baton chargeback``, ``baton aibom``, and
        ``/api/v1/metrics`` (all SQLite readers) silently missed every
        daemon-run task. Wiring the same project storage backend here closes
        that gap so both entry points share one ``usage_records`` table.

        The default is only applied when *task_id* is given. ``executor.py``
        ``start()`` only repairs ``StatePersistence``'s state path for a
        storage-backed engine that was constructed without a task_id
        (``set_task_id()``'s "storage mode" branch) — it otherwise preserves
        the legacy flat ``execution-state.json`` path for the unnamespaced
        single-execution daemon flow. Auto-wiring storage there too would
        silently move that flow onto the namespaced path and break
        ``WorkerSupervisor.status()``, which reads execution state through a
        separate, storage-less ``ExecutionEngine``. Namespaced (task_id-bound)
        runs are unaffected by that legacy path and safely get storage.

        Args:
            launcher: Agent launcher implementation.
            team_context_root: Root directory for state files.
            bus: EventBus instance (created if not provided).
            persist_events: When True, the bus is passed to the engine so it
                auto-wires event persistence.  When False, the engine is
                constructed without a bus and no events are persisted.
            task_id: Optional task ID for namespaced execution state. When
                provided, state files are stored under
                ``<team_context_root>/executions/<task_id>/``, and (unless
                *storage* is overridden) the project's SQLite storage is
                wired in automatically.
            storage: Explicit storage backend to use. When ``None`` (the
                default), ``use_project_storage`` is True, and *task_id* is
                provided, the project's SQLite storage (``get_project_storage``)
                is wired in automatically.
            use_project_storage: Escape hatch for callers (e.g. tests) that
                need the legacy no-storage / JSONL-only engine even when a
                task_id is supplied. Ignored when *storage* is passed
                explicitly.
        """
        bus = bus or EventBus()
        engine_bus = bus if persist_events else None
        if storage is None and use_project_storage and task_id:
            from agent_baton.core.storage import get_project_storage

            resolved_root = (
                team_context_root or ExecutionEngine._DEFAULT_CONTEXT_ROOT
            ).resolve()
            storage = get_project_storage(resolved_root)
        engine = ExecutionEngine(
            team_context_root=team_context_root,
            bus=engine_bus,
            task_id=task_id,
            storage=storage,
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
