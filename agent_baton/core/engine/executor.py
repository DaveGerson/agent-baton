"""Execution engine -- state machine that drives orchestrated task execution.

This module contains the ``ExecutionEngine``, the central component of the
orchestration system.  The engine implements the ``ExecutionDriver`` protocol
and is called repeatedly by the driving session (Claude CLI or async
``TaskWorker``).  Each call either advances the internal state machine or
returns an action for the caller to perform (DISPATCH, GATE, APPROVAL,
COMPLETE, FAILED, or WAIT).

State is persisted to disk after every transition to enable crash recovery
via ``engine.resume()``.  The engine supports both legacy file-based
persistence and a SQLite storage backend, with automatic dual-write during
the transition period.

Key design decisions:

- The engine is synchronous and stateless between calls.  The async runtime
  layer (``TaskWorker``) wraps it for concurrent dispatch.
- Event ownership is split: the engine publishes task-level and phase-level
  events; step-level events are published by ``TaskWorker`` to avoid
  duplication.
- Knowledge gap detection (``KNOWLEDGE_GAP:`` signals in agent output) is
  handled inline during ``record_step_result()``, with escalation routed
  through the escalation matrix in ``knowledge_gap.py``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from agent_baton.models.execution import (
    ActionType,
    ApprovalResult,
    ExecutionAction,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    SynthesisSpec,
    TeamStepResult,
)
from agent_baton.models.events import Event
from agent_baton.models.knowledge import KnowledgeGapSignal, ResolvedDecision
from agent_baton.models.retrospective import ConflictRecord, TeamCompositionRecord
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.knowledge_gap import determine_escalation, parse_knowledge_gap
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.events.projections import TaskView, project_task_view
from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.models.trace import TaskTrace, TraceEvent
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.context_profiler import ContextProfiler


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return the current UTC time as a seconds-precision ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _elapsed_seconds(started_at: str) -> float:
    """Return elapsed wall-clock seconds since started_at (ISO string)."""
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.now(tz=timezone.utc)
        # Make start timezone-aware if it isn't already.
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0.0, (now - start).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# TaskViewSubscriber — materialized view maintained as events fire
# ---------------------------------------------------------------------------

_HIGH_RISK_LEVELS: frozenset[str] = frozenset({"HIGH", "CRITICAL"})


class TaskViewSubscriber:
    """EventBus subscriber that maintains a materialized TaskView on disk.

    Each time an event is published, the subscriber re-projects the full
    task view from the bus history and writes it to *view_path* as JSON.
    This keeps ``task-view.json`` current without requiring on-demand
    computation.
    """

    def __init__(self, task_id: str, bus: "EventBus", view_path: Path) -> None:
        self._task_id = task_id
        self._bus = bus
        self._view_path = view_path

    def __call__(self, event: "Event") -> None:  # noqa: F821
        """Called synchronously by EventBus for every published event."""
        if event.task_id != self._task_id:
            return
        try:
            all_events = self._bus.replay(self._task_id)
            view = project_task_view(all_events, task_id=self._task_id)
            self._write(view)
        except Exception as exc:  # pragma: no cover
            _log.warning("TaskViewSubscriber: failed to update task-view.json: %s", exc)

    def _write(self, view: TaskView) -> None:
        """Serialise *view* to JSON and write atomically to *view_path*."""
        self._view_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "task_id": view.task_id,
            "status": view.status,
            "started_at": view.started_at,
            "completed_at": view.completed_at,
            "risk_level": view.risk_level,
            "total_steps": view.total_steps,
            "steps_completed": view.steps_completed,
            "steps_failed": view.steps_failed,
            "steps_dispatched": view.steps_dispatched,
            "gates_passed": view.gates_passed,
            "gates_failed": view.gates_failed,
            "elapsed_seconds": view.elapsed_seconds,
            "last_event_seq": view.last_event_seq,
            "pending_decisions": view.pending_decisions,
            "phases": {
                str(pid): {
                    "phase_id": ph.phase_id,
                    "phase_name": ph.phase_name,
                    "status": ph.status,
                    "started_at": ph.started_at,
                    "completed_at": ph.completed_at,
                    "gate_status": ph.gate_status,
                    "gate_output": ph.gate_output,
                    "steps": {
                        sid: {
                            "step_id": sv.step_id,
                            "agent_name": sv.agent_name,
                            "status": sv.status,
                            "dispatched_at": sv.dispatched_at,
                            "completed_at": sv.completed_at,
                            "duration_seconds": sv.duration_seconds,
                            "outcome": sv.outcome,
                            "error": sv.error,
                            "files_changed": sv.files_changed,
                            "commit_hash": sv.commit_hash,
                        }
                        for sid, sv in ph.steps.items()
                    },
                }
                for pid, ph in view.phases.items()
            },
        }
        tmp = self._view_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._view_path)


# ---------------------------------------------------------------------------
# ExecutionEngine
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """State machine that drives orchestrated task execution.

    The engine implements the ``ExecutionDriver`` protocol and is the single
    source of truth for plan state.  It is designed to be called repeatedly
    by the driving session (Claude CLI or ``TaskWorker``).  Each call either
    advances the internal state machine or returns an action for the caller
    to perform.

    State is persisted to disk after every transition to enable crash
    recovery via ``resume()``.  The engine supports both legacy file-based
    persistence and a SQLite storage backend with automatic dual-write.

    Typical lifecycle::

        engine = ExecutionEngine(team_context_root=Path(".claude/team-context"))
        action = engine.start(plan)           # ActionType.DISPATCH

        while True:
            if action.action_type == ActionType.DISPATCH.value:
                # caller spawns agent, then:
                engine.record_step_result(action.step_id, action.agent_name, ...)
                action = engine.next_action()
            elif action.action_type == ActionType.GATE.value:
                # caller runs gate check, then:
                engine.record_gate_result(action.phase_id, passed=True)
                action = engine.next_action()
            elif action.action_type == ActionType.COMPLETE.value:
                summary = engine.complete()
                break
            elif action.action_type == ActionType.FAILED.value:
                break

    Attributes:
        _root: Resolved path to the team-context directory where state,
            traces, usage logs, and retrospectives are stored.
        _storage: Optional SQLite storage backend; when set, the engine
            routes persistence through it with file-based fallback.
        _bus: Optional EventBus for domain event publication.
        _knowledge_resolver: Optional resolver for runtime knowledge gap
            auto-resolution.  When None, gaps fall through to best-effort
            or queue-for-gate.
        _trace: In-memory trace object populated during execution, written
            to disk on ``complete()``.
    """

    _DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")

    def __init__(
        self,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
        task_id: str | None = None,
        storage=None,  # SqliteStorage | FileStorage | None
        knowledge_resolver=None,  # KnowledgeResolver | None
    ) -> None:
        self._root = (team_context_root or self._DEFAULT_CONTEXT_ROOT).resolve()
        self._task_id = task_id
        self._storage = storage  # May be None (legacy file mode)
        self._bus = bus

        # KnowledgeResolver for runtime gap auto-resolution.  Callers (CLI and
        # tests) set this at construction time.  When None, gaps fall through to
        # best-effort / queue-for-gate.
        self._knowledge_resolver = knowledge_resolver

        if storage is not None:
            # StorageBackend mode — primary I/O goes through the storage
            # backend.  We still create a file persistence object for
            # dual-write fallback so that file-based readers (scanner,
            # list/switch) stay current during the SQLite transition.
            self._persistence = StatePersistence(self._root, task_id=task_id)
            self._usage_logger = None
            self._telemetry = None
            self._retro_engine = None
            # Wire EventPersistence even in storage mode so domain events are
            # durably written to JSONL files alongside the SQLite/file state.
            # Events are namespaced under the task directory when task_id is
            # provided (mirrors the legacy-mode naming convention).
            if self._bus is not None:
                if task_id:
                    events_dir = self._root / "executions" / task_id / "events"
                else:
                    events_dir = self._root / "events"
                self._event_persistence = EventPersistence(events_dir=events_dir)
                self._bus.subscribe("*", self._persist_event)
            else:
                self._event_persistence = None
        else:
            # Legacy file mode — existing behavior unchanged.
            self._persistence = StatePersistence(self._root, task_id=task_id)
            # Namespace events under the task directory when task_id is provided.
            if task_id:
                events_dir = self._root / "executions" / task_id / "events"
            else:
                events_dir = self._root / "events"
            # If bus provided, auto-wire persistence as a subscriber.
            if self._bus is not None:
                self._event_persistence = EventPersistence(
                    events_dir=events_dir
                )
                self._bus.subscribe("*", self._persist_event)
            else:
                self._event_persistence = None
            self._usage_logger = UsageLogger(
                log_path=self._root / "usage-log.jsonl"
            )
            self._telemetry = AgentTelemetry(
                log_path=self._root / "telemetry.jsonl"
            )
            self._retro_engine = RetrospectiveEngine(
                retrospectives_dir=self._root / "retrospectives"
            )

        self._tracer = TraceRecorder(team_context_root=self._root)

        # Wire telemetry as a catch-all EventBus subscriber so every domain
        # event is captured in the telemetry log.
        if self._bus is not None:
            self._bus.subscribe("*", self._on_event_for_telemetry)

        # In-memory trace object, populated during start() / resume().
        self._trace = None

        # TaskViewSubscriber — wired lazily in start() once task_id is known.
        self._task_view_subscriber: TaskViewSubscriber | None = None

    # ── Storage routing helpers ──────────────────────────────────────────────

    def _save_execution(self, state: ExecutionState) -> None:
        """Persist execution state via storage backend or legacy file."""
        if self._storage is not None:
            try:
                self._storage.save_execution(state)
            except Exception as e:
                _log.warning(
                    "SQLite save failed, falling back to file persistence: %s", e
                )
                if self._persistence is not None:
                    self._persistence.save(state)
                return
            # Dual-write: keep file-based readers current during transition
            # (only when a file persistence layer exists alongside the backend).
            # TODO(T4): remove file dual-write once all readers use SQLite.
            if self._persistence is not None:
                try:
                    self._persistence.save(state)
                except Exception as e:
                    _log.warning(
                        "File persistence dual-write failed (non-fatal): %s", e
                    )
        else:
            self._persistence.save(state)

    def _load_execution(self) -> ExecutionState | None:
        """Load execution state via storage backend or legacy file.

        When a specific ``task_id`` was requested (either explicitly or via
        ``active-task-id.txt``), this method validates that the loaded state
        actually belongs to that task.  If the file-based fallback returns a
        state for a *different* task (i.e. a stale ``execution-state.json``
        from a previous run), it is discarded and ``None`` is returned so
        the caller can fail gracefully rather than silently resuming the
        wrong execution.
        """
        if self._storage is not None:
            try:
                task_id = self._task_id
                if task_id:
                    return self._storage.load_execution(task_id)
                active = self._storage.get_active_task()
                if active:
                    return self._storage.load_execution(active)
                return None
            except Exception as e:
                _log.warning(
                    "SQLite load failed, falling back to file persistence: %s", e
                )
                if self._persistence is not None:
                    state = self._persistence.load()
                    # Discard if the file belongs to a different task so we
                    # never resume the wrong execution via a stale file.
                    if state is not None and self._task_id and state.task_id != self._task_id:
                        _log.warning(
                            "File state task_id %r does not match requested %r — "
                            "discarding stale file state",
                            state.task_id,
                            self._task_id,
                        )
                        return None
                    return state
                return None
        else:
            state = self._persistence.load()
            # In file mode the persistence path is already namespaced to
            # self._task_id (when provided), but a stale legacy flat file
            # could have been written for a different task.  Guard against
            # returning wrong-task state.
            if state is not None and self._task_id and state.task_id != self._task_id:
                _log.warning(
                    "File state task_id %r does not match requested %r — "
                    "discarding stale file state",
                    state.task_id,
                    self._task_id,
                )
                return None
            return state

    def _log_usage(self, record: TaskUsageRecord) -> None:
        """Log a TaskUsageRecord via storage backend or legacy logger."""
        if self._storage is not None:
            try:
                self._storage.log_usage(record)
            except Exception as e:
                _log.warning(
                    "SQLite usage log failed, falling back to file logger: %s", e
                )
                if self._usage_logger is not None:
                    self._usage_logger.log(record)
        else:
            if self._usage_logger is not None:
                self._usage_logger.log(record)

    def _log_telemetry_event(self, tel_event: TelemetryEvent) -> None:
        """Log a telemetry event via storage backend or legacy logger."""
        if self._storage is not None:
            try:
                self._storage.log_telemetry({
                    "timestamp": tel_event.timestamp,
                    "agent_name": tel_event.agent_name,
                    "event_type": tel_event.event_type,
                    "tool_name": getattr(tel_event, "tool_name", ""),
                    "file_path": getattr(tel_event, "file_path", ""),
                    "duration_ms": getattr(tel_event, "duration_ms", 0),
                    "details": getattr(tel_event, "details", ""),
                    "task_id": self._task_id or "",
                })
            except Exception as e:
                _log.warning(
                    "SQLite telemetry log failed, falling back to file logger: %s", e
                )
                if self._telemetry is not None:
                    try:
                        self._telemetry.log_event(tel_event)
                    except Exception as fe:
                        _log.warning("File telemetry fallback also failed: %s", fe)
        else:
            if self._telemetry is not None:
                try:
                    self._telemetry.log_event(tel_event)
                except Exception as e:
                    _log.warning("File telemetry log failed: %s", e)

    def _save_retro(self, retro) -> "Path | None":
        """Persist a retrospective via storage backend or legacy engine."""
        if self._storage is not None:
            try:
                self._storage.save_retrospective(retro)
            except Exception as e:
                _log.warning(
                    "SQLite retrospective save failed, falling back to file engine: %s",
                    e,
                )
                if self._retro_engine is not None:
                    return self._retro_engine.save(retro)
            return None
        else:
            if self._retro_engine is not None:
                return self._retro_engine.save(retro)
            return None

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, plan: MachinePlan) -> ExecutionAction:
        """Initialize execution from a *plan*.

        - Creates :class:`ExecutionState`
        - Starts a trace via :class:`TraceRecorder`
        - Saves state to disk
        - Returns the first action (DISPATCH for the first step, or COMPLETE
          if the plan has no phases/steps)
        """
        if not plan.phases:
            raise ValueError(
                "Plan has no phases. Check your planner output — "
                "a valid plan must have at least one phase with one step."
            )

        # Track the task_id for subsequent load/save calls.
        self._task_id = plan.task_id
        if self._storage is not None:
            try:
                self._storage.set_active_task(plan.task_id)
            except Exception as exc:
                _log.warning("Failed to set active task in storage: %s", exc)
        # Keep file-based active-task-id.txt in sync during the SQLite transition.
        # TODO(T4): remove active-task-id.txt dual-write once all readers use SQLite.
        if self._persistence is not None:
            try:
                # Update persistence's task_id then write the active marker.
                self._persistence._task_id = plan.task_id
                self._persistence.set_active()
            except Exception:
                pass

        # Wire the materialized-view subscriber now that we know the task_id.
        # One subscriber per engine instance; replace any previous one.
        if self._bus is not None:
            if self._task_id:
                view_dir = self._root / "executions" / self._task_id
            else:
                view_dir = self._root
            view_path = view_dir / "task-view.json"
            self._task_view_subscriber = TaskViewSubscriber(
                task_id=plan.task_id,
                bus=self._bus,
                view_path=view_path,
            )
            self._bus.subscribe("*", self._task_view_subscriber)

        # ── Risk-level pre-flight approval ───────────────────────────────────
        # For HIGH/CRITICAL plans, ensure the user explicitly approves before
        # any agents are dispatched — unless Phase 1 already carries an
        # approval gate (planner-added checkpoints are sufficient).
        initial_status = "running"
        if plan.risk_level.upper() in _HIGH_RISK_LEVELS and plan.phases:
            first_phase = plan.phases[0]
            if not first_phase.approval_required:
                first_phase.approval_required = True
                first_phase.approval_description = (
                    f"This plan is classified as **{plan.risk_level}** risk. "
                    "Please review the plan summary and confirm you want to "
                    "proceed before any agents are dispatched.\n\n"
                    f"**Task**: {plan.task_summary}\n"
                    f"**Phases**: {len(plan.phases)}\n"
                    f"**Total steps**: {plan.total_steps}"
                )
                initial_status = "approval_pending"

        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            current_phase=0,
            current_step_index=0,
            status=initial_status,
        )

        # Initialise trace (in-memory; committed to disk on complete()).
        self._trace = self._tracer.start_trace(
            task_id=plan.task_id,
            plan_snapshot=plan.to_dict(),
        )

        self._log_telemetry_event(TelemetryEvent(
            timestamp=_utcnow(),
            agent_name="engine",
            event_type="execution.started",
            details=f"task_id={plan.task_id} risk={plan.risk_level}",
        ))

        self._publish(evt.task_started(
            task_id=plan.task_id,
            task_summary=plan.task_summary,
            risk_level=plan.risk_level,
            total_steps=plan.total_steps,
        ))
        if plan.phases:
            first_phase = plan.phases[0]
            self._publish(evt.phase_started(
                task_id=plan.task_id,
                phase_id=first_phase.phase_id,
                phase_name=first_phase.name,
                step_count=len(first_phase.steps),
            ))

        self._save_execution(state)
        # Track the new task_id so _load_execution() can find it by ID.
        self._task_id = state.task_id
        # In storage mode, also mark this as the active task so any engine
        # instance without an explicit task_id (e.g. from init_dependencies)
        # can still load the state via get_active_task().
        if self._storage is not None:
            try:
                self._storage.set_active_task(state.task_id)
            except Exception:
                pass
        elif self._persistence is not None:
            # TODO(T4): remove active-task-id.txt dual-write once all readers use SQLite.
            self._persistence.set_active()
        return self._determine_action(state)

    def next_action(self) -> ExecutionAction:
        """Determine and return the next action based on current state.

        Logic (in priority order):

        1. Load state from disk.
        2. If status is already *failed* or *complete*, return the
           corresponding terminal action immediately.
        3. If status is *gate_pending*, return a GATE action for the current
           phase.
        4. Walk the current phase:
           a. If a step failed → return FAILED.
           b. If all steps are complete and there is a gate → return GATE.
           c. If all steps are complete and gate passed (or no gate) →
              advance to next phase.
           d. If steps remain → return DISPATCH for the next pending step.
        5. If all phases are exhausted → return COMPLETE.
        6. Save state before returning any mutable action.
        """
        state = self._load_execution()
        if state is None:
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message="No active execution state found. Call start() first.",
                summary="No execution state on disk.",
            )

        action = self._determine_action(state)
        self._save_execution(state)
        return action

    def next_actions(self) -> list[ExecutionAction]:
        """Return ALL currently dispatchable actions for parallel execution.

        Unlike :meth:`next_action` which returns a single action, this method
        returns every step whose dependencies are satisfied and that has not
        yet been dispatched, completed, or failed.  The caller can spawn all
        returned agents in parallel.

        Returns an empty list if no steps are dispatchable (caller should
        check :meth:`next_action` for WAIT / GATE / COMPLETE / FAILED).
        """
        state = self._load_execution()
        if state is None:
            return []

        if state.status in ("complete", "failed", "gate_pending", "approval_pending"):
            return []

        if state.current_phase >= len(state.plan.phases):
            return []

        phase_obj = state.current_phase_obj
        if phase_obj is None or not phase_obj.steps:
            return []

        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        occupied = completed | state.failed_step_ids | dispatched | state.interrupted_step_ids

        actions: list[ExecutionAction] = []
        for step in phase_obj.steps:
            if step.step_id in occupied:
                continue
            if step.depends_on and not all(
                dep in completed for dep in step.depends_on
            ):
                continue
            actions.append(self._dispatch_action(step, state))

        return actions

    @staticmethod
    def _extract_deviations(outcome: str) -> list[str]:
        """Extract deviation notes from agent outcome text.

        Looks for ``## Deviation`` or ``## Deviations`` section headers
        (levels 1-3) and collects the content until the next heading or
        end of text.  Multiple Deviation sections are each returned as a
        separate entry.

        Returns:
            List of deviation strings; empty list if none found.
        """
        lines = outcome.split("\n")
        in_deviation = False
        current: list[str] = []
        deviations: list[str] = []
        for line in lines:
            if re.match(r"^#{1,3}\s+[Dd]eviation", line):
                if current:
                    deviations.append("\n".join(current).strip())
                    current = []
                in_deviation = True
                continue
            if in_deviation:
                if re.match(r"^#{1,3}\s+", line) and not re.match(
                    r"^#{1,3}\s+[Dd]eviation", line
                ):
                    deviations.append("\n".join(current).strip())
                    current = []
                    in_deviation = False
                else:
                    current.append(line)
        if in_deviation and current:
            deviations.append("\n".join(current).strip())
        return [d for d in deviations if d]

    def record_step_result(
        self,
        step_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
        commit_hash: str = "",
        estimated_tokens: int = 0,
        duration_seconds: float = 0.0,
        error: str = "",
    ) -> None:
        """Record the result of a step execution.

        - Creates :class:`StepResult` and appends to state.
        - Emits trace events (``agent_complete`` or ``agent_failed``).
        - Saves state to disk.
        """
        _VALID_STEP_STATUSES = {"complete", "failed", "dispatched", "interrupted"}
        if status not in _VALID_STEP_STATUSES:
            raise ValueError(
                f"Invalid step status '{status}'. Must be one of: {_VALID_STEP_STATUSES}"
            )

        state = self._load_execution()
        if state is None:
            raise RuntimeError(
                "record_step_result() called with no active execution state."
            )

        result = StepResult(
            step_id=step_id,
            agent_name=agent_name,
            status=status,
            outcome=outcome,
            files_changed=files_changed or [],
            commit_hash=commit_hash,
            estimated_tokens=estimated_tokens,
            duration_seconds=duration_seconds,
            error=error,
            completed_at=_utcnow(),
            deviations=self._extract_deviations(outcome),
        )
        state.step_results.append(result)

        # ── Knowledge gap protocol ──────────────────────────────────────────
        # Inspect the outcome for a KNOWLEDGE_GAP signal emitted by the agent.
        # Only process when status is "complete" or "interrupted" — a "failed"
        # step is handled by the failure path; "dispatched" has no outcome yet.
        if status in ("complete", "interrupted") and outcome:
            self._handle_knowledge_gap(
                outcome=outcome,
                step_id=step_id,
                agent_name=agent_name,
                state=state,
            )

        # Determine phase + step index for trace context.
        phase_idx, step_idx = self._locate_step(state, step_id)
        if phase_idx == -1:
            valid_ids = [
                s.step_id
                for p in state.plan.phases
                for s in p.steps
            ]
            raise ValueError(
                f"Step '{step_id}' not found in plan. "
                f"Valid step IDs: {', '.join(valid_ids)}"
            )

        # Emit trace event.
        if self._trace is not None:
            event_type = "agent_complete" if status == "complete" else "agent_failed"
            self._tracer.record_event(
                self._trace,
                event_type,
                agent_name=agent_name,
                phase=phase_idx + 1,
                step=step_idx + 1,
                details={
                    "step_id": step_id,
                    "outcome": outcome,
                    "commit_hash": commit_hash,
                    "files_changed": files_changed or [],
                    "error": error,
                },
                duration_seconds=duration_seconds if duration_seconds else None,
            )

        # Log telemetry event for this step.
        tel_event_type = (
            "step.completed" if status == "complete" else "step.failed"
        )
        duration_ms = int(duration_seconds * 1000)
        file_path = files_changed[0] if files_changed else ""
        self._log_telemetry_event(TelemetryEvent(
            timestamp=_utcnow(),
            agent_name=agent_name,
            event_type=tel_event_type,
            duration_ms=duration_ms,
            file_path=file_path,
            details=f"step_id={step_id} outcome={outcome}" + (
                f" error={error}" if error else ""
            ),
        ))

        # Check token budget and warn when exceeded.
        warning = self._check_token_budget(state)
        if warning:
            _log.warning("Budget warning: %s", warning)
            result.deviations.append(f"TOKEN_BUDGET_WARNING: {warning}")

        self._save_execution(state)

    def mark_dispatched(self, step_id: str, agent_name: str) -> None:
        """Record that a step has been dispatched (in-flight, not yet complete).

        This allows the engine to track which steps are currently running
        so it can correctly determine what to dispatch next in parallel
        execution scenarios.
        """
        self.record_step_result(
            step_id=step_id,
            agent_name=agent_name,
            status="dispatched",
        )

    def record_gate_result(
        self,
        phase_id: int,
        passed: bool,
        output: str = "",
    ) -> None:
        """Record the result of a QA gate check.

        - Creates :class:`GateResult` and appends to state.
        - Emits a ``gate_result`` trace event.
        - If *failed*: sets state status to ``failed``.
        - If *passed*: advances the phase pointer and resets step index.
        - Saves state.
        """
        state = self._load_execution()
        if state is None:
            raise RuntimeError(
                "record_gate_result() called with no active execution state."
            )

        phase_obj = state.current_phase_obj
        gate_type = phase_obj.gate.gate_type if (phase_obj and phase_obj.gate) else "unknown"

        gate_result = GateResult(
            phase_id=phase_id,
            gate_type=gate_type,
            passed=passed,
            output=output,
            checked_at=_utcnow(),
        )
        state.gate_results.append(gate_result)

        # Emit trace event.
        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "gate_result",
                agent_name=None,
                phase=phase_id + 1,
                step=0,
                details={
                    "gate_type": gate_type,
                    "result": "PASS" if passed else "FAIL",
                    "output": output,
                },
            )

        # Log telemetry event for this gate.
        self._log_telemetry_event(TelemetryEvent(
            timestamp=_utcnow(),
            agent_name="engine",
            event_type="gate.passed" if passed else "gate.failed",
            details=f"phase_id={phase_id} gate_type={gate_type}",
        ))

        if not passed:
            self._publish(evt.gate_failed(
                task_id=state.task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                output=output,
            ))
            state.status = "failed"
        else:
            self._publish(evt.gate_passed(
                task_id=state.task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                output=output,
            ))
            # Advance to next phase.  current_phase is a 0-based index into
            # plan.phases, whereas phase_id is a 1-based identifier — so we
            # must increment the index, not derive it from phase_id.
            state.current_phase += 1
            state.current_step_index = 0
            state.status = "running"

        self._save_execution(state)

    def complete(self) -> str:
        """Finalise execution.

        - Sets state to ``complete``.
        - Completes the trace via :class:`TraceRecorder`.
        - Writes a :class:`TaskUsageRecord` via :class:`UsageLogger`.
        - Generates and writes a retrospective via :class:`RetrospectiveEngine`.
        - Returns a human-readable completion summary string.
        """
        state = self._load_execution()
        if state is None:
            return "No active execution state found."

        state.status = "complete"
        state.completed_at = _utcnow()
        self._save_execution(state)

        # Finalise trace.
        # In CLI mode each call creates a fresh engine instance, so self._trace
        # is None.  Reconstruct a trace from the persisted ExecutionState so
        # that baton trace always returns data after baton execute complete.
        trace_path: Path | None = None
        finished_trace = None
        if self._trace is None:
            self._trace = self._reconstruct_trace_from_state(state)
        if self._trace is not None:
            finished_trace = self._trace  # keep reference before complete_trace mutates it
            trace_path = self._tracer.complete_trace(finished_trace, outcome="SHIP")
            self._trace = None

        # Persist trace to SQLite if storage backend is available.
        if self._storage is not None and finished_trace is not None:
            try:
                self._storage.save_trace(finished_trace)
            except Exception as exc:
                _log.warning(
                    "SQLite trace save failed (non-fatal): %s", exc
                )

        # Build and log usage record.
        usage_record = self._build_usage_record(state)
        self._log_usage(usage_record)

        # Build and save retrospective with rich qualitative data.
        retro_data = self._build_retrospective_data(state)
        # generate_from_usage produces the model object but does not persist.
        # Reuse self._retro_engine in file mode; create a transient one for
        # storage mode (persist is handled by _save_retro).
        _gen_engine = self._retro_engine or RetrospectiveEngine(
            retrospectives_dir=self._root / "retrospectives"
        )
        retro = _gen_engine.generate_from_usage(
            usage=usage_record,
            task_name=retro_data.get("task_name", state.plan.task_summary),
            what_worked=retro_data.get("what_worked"),
            what_didnt=retro_data.get("what_didnt"),
            knowledge_gaps=retro_data.get("knowledge_gaps"),
            roster_recommendations=retro_data.get("roster_recommendations"),
            sequencing_notes=retro_data.get("sequencing_notes"),
            team_compositions=retro_data.get("team_compositions"),
            conflicts=retro_data.get("conflicts"),
        )
        retro_path = self._save_retro(retro)

        # Trigger improvement loop (best-effort, non-blocking).
        # The loop has built-in guards: circuit breaker, trigger thresholds,
        # and data-volume checks.  It no-ops if there isn't enough new data.
        try:
            from agent_baton.core.improve.loop import ImprovementLoop
            loop = ImprovementLoop(improvements_dir=self._root / "improvements")
            loop.run_cycle()
        except Exception as exc:
            _log.debug("Post-completion improvement cycle skipped: %s", exc)

        # Compute context efficiency profile (best-effort, non-blocking).
        # Saved to <team_context_root>/context-profiles/<task_id>.json.
        context_profile_path: Path | None = None
        try:
            profiler = ContextProfiler(team_context_root=self._root)
            profile = profiler.profile_task(state.task_id)
            if profile is not None:
                context_profile_path = profiler.save_profile(profile)
        except Exception as exc:
            _log.debug("Context profiling skipped (non-fatal): %s", exc)

        # Compose summary string.
        steps_done = len(state.completed_step_ids)
        gates_passed = sum(1 for g in state.gate_results if g.passed)
        elapsed = _elapsed_seconds(state.started_at)

        self._publish(evt.task_completed(
            task_id=state.task_id,
            steps_completed=steps_done,
            gates_passed=gates_passed,
            elapsed_seconds=elapsed,
        ))

        self._log_telemetry_event(TelemetryEvent(
            timestamp=_utcnow(),
            agent_name="engine",
            event_type="execution.completed",
            duration_ms=int(elapsed * 1000),
            details=(
                f"task_id={state.task_id} steps={steps_done}"
                f" gates_passed={gates_passed}"
            ),
        ))

        summary_lines = [
            f"Task {state.task_id} completed.",
            f"Steps: {steps_done}/{state.plan.total_steps}",
            f"Gates passed: {gates_passed}",
            f"Elapsed: {int(elapsed)}s",
        ]
        if trace_path:
            summary_lines.append(f"Trace: {trace_path}")
        summary_lines.append(f"Retrospective: {retro_path}")
        if context_profile_path:
            summary_lines.append(f"Context profile: {context_profile_path}")
        return "\n".join(summary_lines)

    def status(self) -> dict:
        """Return current execution status as a dict.

        Keys: ``task_id``, ``status``, ``current_phase``, ``total_phases``,
        ``steps_complete``, ``steps_total``, ``gates_passed``,
        ``gates_failed``, ``elapsed_seconds``, ``step_results``,
        ``step_plan``, ``gate_results``.
        """
        state = self._load_execution()
        if state is None:
            return {"status": "no_active_execution"}

        gates_passed = sum(1 for g in state.gate_results if g.passed)
        gates_failed = sum(1 for g in state.gate_results if not g.passed)

        # Build step_plan: all steps across all phases, preserving order
        step_plan = [
            {"step_id": step.step_id, "agent_name": step.agent_name,
             "task_description": step.task_description}
            for phase in state.plan.phases
            for step in phase.steps
        ]

        return {
            "task_id": state.task_id,
            "status": state.status,
            "current_phase": state.current_phase,
            "total_phases": len(state.plan.phases),
            "steps_complete": len(state.completed_step_ids),
            "steps_total": state.plan.total_steps,
            "gates_passed": gates_passed,
            "gates_failed": gates_failed,
            "elapsed_seconds": _elapsed_seconds(state.started_at),
            "step_results": [r.to_dict() for r in state.step_results],
            "step_plan": step_plan,
            "gate_results": [g.to_dict() for g in state.gate_results],
        }

    def resume(self) -> ExecutionAction:
        """Resume from a saved state (crash recovery).

        Resolution order when a specific ``task_id`` is known:

        1. Primary: load via ``_load_execution()`` (storage backend or
           namespaced file).
        2. SQLite fallback: if the primary load returns ``None`` *and* we
           have a storage backend with the requested task, reconstruct the
           state directly from SQLite.  This handles the case where
           ``execution-state.json`` was overwritten by a concurrent run or
           e2e test but ``baton.db`` still holds the correct state.

        - Loads state from disk.
        - Determines where execution left off.
        - Returns the appropriate next action.
        """
        state = self._load_execution()

        # If file-based load came up empty but we have a task_id and a storage
        # backend, try reconstructing directly from SQLite before giving up.
        if state is None and self._task_id and self._storage is not None:
            _log.info(
                "Primary load returned no state for task %r; "
                "attempting SQLite reconstruction",
                self._task_id,
            )
            try:
                state = self._storage.load_execution(self._task_id)
                if state is not None:
                    _log.info(
                        "Reconstructed execution state for task %r from SQLite",
                        self._task_id,
                    )
            except Exception as exc:
                _log.warning(
                    "SQLite reconstruction for task %r failed: %s",
                    self._task_id,
                    exc,
                )

        if state is None:
            task_hint = f" (task {self._task_id!r})" if self._task_id else ""
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message=f"No execution state found{task_hint}. Cannot resume.",
                summary="No execution state on disk.",
            )

        # Reconnect the in-memory trace if one exists on disk; otherwise
        # start a fresh trace continuation so subsequent events are recorded.
        if self._trace is None:
            existing = self._tracer.load_trace(state.task_id)
            if existing is not None:
                self._trace = existing
            else:
                self._trace = self._tracer.start_trace(
                    task_id=state.task_id,
                    plan_snapshot=state.plan.to_dict(),
                )

        return self._determine_action(state)

    def recover_dispatched_steps(self) -> int:
        """Clear stale dispatched-step markers for crash recovery.

        After a daemon crash, steps in ``dispatched`` status have no running
        agent process.  This method removes their ``StepResult`` entries so
        the engine will re-dispatch them on the next ``next_action()`` call.

        Returns the number of recovered (re-dispatchable) steps.
        """
        state = self._load_execution()
        if state is None:
            return 0

        original_count = len(state.step_results)
        state.step_results = [
            r for r in state.step_results if r.status != "dispatched"
        ]
        recovered = original_count - len(state.step_results)

        if recovered > 0:
            self._save_execution(state)

        return recovered

    # ── Approval, amendment, and team APIs ─────────────────────────────────

    def record_approval_result(
        self,
        phase_id: int,
        result: str,
        feedback: str = "",
    ) -> None:
        """Record a human approval decision for a phase.

        Args:
            phase_id: The phase_id requiring approval.
            result: One of ``"approve"``, ``"reject"``,
                ``"approve-with-feedback"``.
            feedback: Free-text feedback (used when result is
                ``"approve-with-feedback"`` to trigger a plan amendment).
        """
        _VALID_RESULTS = {"approve", "reject", "approve-with-feedback"}
        if result not in _VALID_RESULTS:
            raise ValueError(
                f"Invalid approval result '{result}'. Must be one of: {_VALID_RESULTS}"
            )

        state = self._load_execution()
        if state is None:
            raise RuntimeError(
                "record_approval_result() called with no active execution state."
            )

        approval = ApprovalResult(
            phase_id=phase_id,
            result=result,
            feedback=feedback,
        )
        state.approval_results.append(approval)

        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "approval_result",
                agent_name=None,
                phase=phase_id,
                step=0,
                details={"result": result, "feedback": feedback},
            )

        if result == "reject":
            state.status = "failed"
        elif result == "approve":
            state.status = "running"
        elif result == "approve-with-feedback":
            # Insert a remediation phase after the current phase.
            # Save state first so amend_plan sees the approval result.
            self._save_execution(state)
            self._amend_from_feedback(state, phase_id, feedback)
            # Reload state — amend_plan saved its own copy with the
            # amendment applied.  We must pick up those changes.
            state = self._load_execution() or state
            state.status = "running"

        self._save_execution(state)

    def amend_plan(
        self,
        description: str,
        new_phases: list[PlanPhase] | None = None,
        insert_after_phase: int | None = None,
        add_steps_to_phase: int | None = None,
        new_steps: list[PlanStep] | None = None,
        trigger: str = "manual",
        trigger_phase_id: int = 0,
        feedback: str = "",
    ) -> PlanAmendment:
        """Amend the running plan by adding phases or steps.

        The plan inside ``ExecutionState`` is mutated in place.  An audit
        record (:class:`PlanAmendment`) is appended to ``state.amendments``.

        Args:
            description: Human-readable explanation of the amendment.
            new_phases: New :class:`PlanPhase` objects to insert.
            insert_after_phase: Insert *new_phases* after this phase_id.
                If ``None``, appends after the current phase.
            add_steps_to_phase: Phase_id to add *new_steps* to.
            new_steps: New :class:`PlanStep` objects for an existing phase.
            trigger: What caused this amendment.
            trigger_phase_id: Which phase triggered it.
            feedback: Reviewer feedback text.

        Returns:
            The :class:`PlanAmendment` record.
        """
        state = self._load_execution()
        if state is None:
            raise RuntimeError(
                "amend_plan() called with no active execution state."
            )

        amendment = PlanAmendment(
            amendment_id=f"amend-{len(state.amendments) + 1}",
            trigger=trigger,
            trigger_phase_id=trigger_phase_id,
            description=description,
            feedback=feedback,
        )

        if new_phases:
            # Determine insertion index.
            if insert_after_phase is not None:
                insert_idx = next(
                    (i + 1 for i, p in enumerate(state.plan.phases)
                     if p.phase_id == insert_after_phase),
                    len(state.plan.phases),
                )
            else:
                # Default: insert after the current phase.
                insert_idx = state.current_phase + 1

            for i, phase in enumerate(new_phases):
                state.plan.phases.insert(insert_idx + i, phase)
                amendment.phases_added.append(phase.phase_id)

            self._renumber_phases(state)

        if new_steps and add_steps_to_phase is not None:
            target = next(
                (p for p in state.plan.phases if p.phase_id == add_steps_to_phase),
                None,
            )
            if target is not None:
                for step in new_steps:
                    target.steps.append(step)
                    amendment.steps_added.append(step.step_id)

        state.amendments.append(amendment)

        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "replan",
                agent_name=None,
                phase=trigger_phase_id,
                step=0,
                details={
                    "amendment_id": amendment.amendment_id,
                    "description": description,
                    "phases_added": amendment.phases_added,
                    "steps_added": amendment.steps_added,
                },
            )

        self._save_execution(state)
        return amendment

    def record_team_member_result(
        self,
        step_id: str,
        member_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
    ) -> None:
        """Record the result of a single team member within a team step.

        When all members have completed, the parent step is automatically
        marked as complete.  If any member fails, the parent step fails.
        """
        state = self._load_execution()
        if state is None:
            raise RuntimeError(
                "record_team_member_result() called with no active execution state."
            )

        # Find or create the parent StepResult for this team step.
        parent = state.get_step_result(step_id)
        if parent is None:
            parent = StepResult(
                step_id=step_id, agent_name="team", status="dispatched",
            )
            state.step_results.append(parent)

        member_result = TeamStepResult(
            member_id=member_id,
            agent_name=agent_name,
            status=status,
            outcome=outcome,
            files_changed=files_changed or [],
        )
        parent.member_results.append(member_result)

        # Check if all team members are done.
        plan_step = self._find_step(state, step_id)
        if plan_step and plan_step.team:
            all_member_ids = {m.member_id for m in plan_step.team}
            completed_ids = {
                m.member_id for m in parent.member_results
                if m.status == "complete"
            }
            failed_ids = {
                m.member_id for m in parent.member_results
                if m.status == "failed"
            }

            if failed_ids:
                # Check conflict_handling strategy before failing.
                spec = plan_step.synthesis
                if spec and spec.conflict_handling == "fail":
                    conflict = self._detect_team_conflict(
                        plan_step, parent.member_results
                    )
                    if conflict:
                        parent.error = (
                            f"Conflict detected: {conflict.resolution_detail}"
                        )
                parent.status = "failed"
                parent.error = parent.error or (
                    f"Team member(s) failed: {', '.join(sorted(failed_ids))}"
                )
                parent.completed_at = _utcnow()
            elif completed_ids >= all_member_ids:
                spec = plan_step.synthesis
                conflict = self._detect_team_conflict(
                    plan_step, parent.member_results
                )

                # If conflict detected and escalation requested, pause
                # for human review instead of auto-completing.
                if conflict and spec and spec.conflict_handling == "escalate":
                    state.status = "approval_pending"
                    parent.status = "dispatched"  # keep step open
                    parent.deviations.append(
                        f"Conflict escalated: {conflict.conflict_id}"
                    )
                    self._save_execution(state)
                    return

                # Apply synthesis strategy.
                self._apply_synthesis(plan_step, parent)
                parent.completed_at = _utcnow()

        # Check token budget and warn when exceeded.
        warning = self._check_token_budget(state)
        if warning:
            _log.warning("Budget warning: %s", warning)
            parent.deviations.append(f"TOKEN_BUDGET_WARNING: {warning}")

        self._save_execution(state)

    # ── Team synthesis and conflict detection ────────────────────────────────

    def _apply_synthesis(
        self, plan_step: PlanStep, parent: StepResult
    ) -> None:
        """Apply the configured synthesis strategy to team member results.

        Updates ``parent.outcome`` and ``parent.files_changed`` in place.

        Strategies:
        - ``concatenate`` (default): Join outcomes with ``"; "``, collect
          all files_changed.
        - ``merge_files``: Same as concatenate but deduplicate files_changed.
        - ``agent_synthesis``: Same as concatenate for now — the synthesis
          agent dispatch is deferred to Phase 3.3 (INTERACT action type)
          which requires invariant changes.  This branch sets a marker in
          ``parent.deviations`` indicating synthesis was requested.
        """
        spec = plan_step.synthesis
        strategy = spec.strategy if spec else "concatenate"

        # Build base outcome and files from members.
        outcomes = [
            m.outcome for m in parent.member_results if m.outcome
        ]
        all_files = [
            f for m in parent.member_results for f in m.files_changed
        ]

        if strategy == "merge_files":
            # Deduplicate files while preserving order.
            seen: set[str] = set()
            deduped: list[str] = []
            for f in all_files:
                if f not in seen:
                    seen.add(f)
                    deduped.append(f)
            parent.files_changed = deduped
        elif strategy == "agent_synthesis":
            # Mark for future synthesis agent dispatch.
            parent.deviations.append(
                f"synthesis_requested: agent={spec.synthesis_agent if spec else 'code-reviewer'}"
            )
            parent.files_changed = all_files
        else:
            # concatenate (default)
            parent.files_changed = all_files

        parent.outcome = "; ".join(outcomes)
        parent.status = "complete"

    def _detect_team_conflict(
        self,
        plan_step: PlanStep,
        member_results: list[TeamStepResult],
    ) -> ConflictRecord | None:
        """Detect conflicts between team member outputs.

        A conflict is detected when two or more members modified the same
        file.  This is a heuristic — overlapping files suggest potentially
        conflicting changes that may need human review.

        Returns a :class:`ConflictRecord` if conflict found, else ``None``.
        """
        if len(member_results) < 2:
            return None

        # Build file → list of members who touched it.
        file_owners: dict[str, list[str]] = {}
        for m in member_results:
            for f in m.files_changed:
                file_owners.setdefault(f, []).append(m.agent_name)

        # Find files touched by multiple members.
        conflicting_files = {
            f: agents for f, agents in file_owners.items()
            if len(agents) > 1
        }

        if not conflicting_files:
            return None

        # Build positions from outcomes.
        positions = {
            m.agent_name: m.outcome
            for m in member_results
            if m.agent_name in {a for agents in conflicting_files.values() for a in agents}
        }

        # Build evidence from file overlap.
        evidence = {
            agent: ", ".join(
                f for f, agents in conflicting_files.items()
                if agent in agents
            )
            for agent in positions
        }

        import hashlib
        conflict_id = hashlib.sha256(
            f"{plan_step.step_id}:{sorted(positions.keys())}".encode()
        ).hexdigest()[:12]

        return ConflictRecord(
            conflict_id=f"conflict-{conflict_id}",
            step_id=plan_step.step_id,
            agents=sorted(positions.keys()),
            positions=positions,
            evidence=evidence,
            severity="medium",
            resolution="unresolved",
        )

    def _check_token_budget(self, state: ExecutionState) -> str | None:
        """Return a warning string if cumulative tokens exceed the plan's budget tier threshold.

        Compares the sum of ``estimated_tokens`` across all completed step
        results against the threshold for the plan's ``budget_tier``.  Returns
        ``None`` when within budget.

        Thresholds by tier:
        - ``lean``: 50,000 tokens
        - ``standard``: 500,000 tokens
        - ``full``: 2,000,000 tokens
        """
        total = sum(r.estimated_tokens for r in state.step_results)
        thresholds: dict[str, int] = {
            "lean": 50_000,
            "standard": 500_000,
            "full": 2_000_000,
        }
        limit = thresholds.get(state.plan.budget_tier, 500_000)
        if total > limit:
            return (
                f"Token budget exceeded: {total:,} tokens used, "
                f"{state.plan.budget_tier} tier limit is {limit:,}"
            )
        return None

    # ── Internal helpers ────────────────────────────────────────────────────

    # Event ownership: Engine publishes task-level and phase-level events.
    # Step-level events (step.dispatched, step.completed, step.failed) are
    # published by the runtime layer (TaskWorker) to avoid duplication.

    def _persist_event(self, event: Event) -> None:
        """EventBus subscriber that appends *event* to all active persistence stores.

        Writes to the JSONL flat-file log (``EventPersistence.append``) and,
        when a SQLite storage backend is configured, also to the ``events``
        table via ``storage.append_event()``.  Both writes are best-effort;
        a failure in either path logs a warning and does not crash execution.

        Wraps :meth:`EventPersistence.append` (which returns a ``Path``) so
        that the method signature matches the ``EventHandler`` type alias
        (``Callable[[Event], None]``).
        """
        if self._event_persistence is not None:
            self._event_persistence.append(event)
        # Write to SQLite events table so the events table is populated for
        # CLI-driven executions (not just async TaskWorker runs).
        if self._storage is not None:
            try:
                self._storage.append_event(event)
            except Exception as exc:
                _log.warning(
                    "SQLite append_event failed (non-fatal): %s", exc
                )

    def _on_event_for_telemetry(self, event: Event) -> None:
        """EventBus subscriber that mirrors every domain event to telemetry.

        Called synchronously by the bus during publish().  Wrapped in
        try/except so a logging failure never crashes execution.
        """
        agent_name = event.payload.get("agent_name") or "engine"
        self._log_telemetry_event(TelemetryEvent(
            timestamp=event.timestamp,
            agent_name=agent_name,
            event_type=event.topic,
            details=f"task_id={event.task_id} seq={event.sequence}",
        ))

    def _publish(self, event: Event) -> None:
        """Publish an event if a bus is configured."""
        if self._bus is not None:
            self._bus.publish(event)

    # Backward-compatible shims — tests may call these directly.
    def _save_state(self, state: ExecutionState) -> "Path | None":
        """Persist state; routes to storage backend or legacy file."""
        self._save_execution(state)
        if self._persistence is not None:
            return self._persistence.path
        return None

    def _load_state(self) -> ExecutionState | None:
        """Load state; routes to storage backend or legacy file."""
        return self._load_execution()

    def _reconstruct_trace_from_state(self, state: ExecutionState) -> TaskTrace:
        """Reconstruct an in-memory :class:`TaskTrace` from persisted state.

        Called by :meth:`complete` when ``self._trace`` is ``None`` — the
        typical situation when ``baton execute complete`` is invoked as a
        separate CLI call that creates a fresh engine instance.

        The reconstructed trace contains one event per step result and one
        event per gate result, ordered by their ``completed_at`` timestamps.
        This gives ``baton trace`` useful data even though the in-memory
        trace was never populated during this process lifetime.
        """
        trace = TaskTrace(
            task_id=state.task_id,
            plan_snapshot=state.plan.to_dict(),
            events=[],
            started_at=state.started_at or _utcnow(),
            completed_at=None,
            outcome=None,
        )

        # Build a timestamp → phase/step index look-up from step results.
        for result in state.step_results:
            if result.status not in ("complete", "failed"):
                # Skip dispatched/interrupted — they have no final outcome yet.
                continue
            phase_idx, step_idx = self._locate_step(state, result.step_id)
            event_type = (
                "agent_complete" if result.status == "complete" else "agent_failed"
            )
            trace.events.append(TraceEvent(
                timestamp=result.completed_at or _utcnow(),
                event_type=event_type,
                agent_name=result.agent_name,
                phase=phase_idx + 1,
                step=step_idx + 1,
                details={
                    "step_id": result.step_id,
                    "outcome": result.outcome,
                    "commit_hash": result.commit_hash,
                    "files_changed": result.files_changed,
                    "error": result.error,
                },
                duration_seconds=(
                    result.duration_seconds if result.duration_seconds else None
                ),
            ))

        # Append gate result events.
        for gate in state.gate_results:
            phase_obj = state.plan.phases[gate.phase_id] if (
                0 <= gate.phase_id < len(state.plan.phases)
            ) else None
            gate_type = (
                phase_obj.gate.gate_type
                if (phase_obj and phase_obj.gate)
                else "unknown"
            )
            trace.events.append(TraceEvent(
                timestamp=gate.checked_at or _utcnow(),
                event_type="gate_result",
                agent_name=None,
                phase=gate.phase_id + 1,
                step=0,
                details={
                    "gate_type": gate_type,
                    "result": "PASS" if gate.passed else "FAIL",
                    "output": gate.output,
                },
            ))

        # Sort events by timestamp so the timeline is chronological.
        trace.events.sort(key=lambda e: e.timestamp)

        return trace

    def _build_usage_record(self, state: ExecutionState) -> TaskUsageRecord:
        """Convert *state* into a :class:`TaskUsageRecord` for the usage logger."""
        # Aggregate per-agent metrics from step results.
        agent_map: dict[str, AgentUsageRecord] = {}

        for result in state.step_results:
            name = result.agent_name
            if name not in agent_map:
                # Determine model from the plan step if available.
                model = _model_for_step(state.plan, result.step_id)
                agent_map[name] = AgentUsageRecord(
                    name=name,
                    model=model,
                    steps=0,
                    retries=0,
                    gate_results=[],
                    estimated_tokens=0,
                    duration_seconds=0.0,
                )
            rec = agent_map[name]
            rec.steps += 1
            # Use the caller-supplied token count when available; fall back to a
            # heuristic derived from the plan step's task description length.
            # 1 token ≈ 4 characters (consistent with ContextProfiler / KnowledgeRegistry).
            token_count = result.estimated_tokens
            if token_count == 0:
                token_count = _estimate_tokens_for_step(state.plan, result.step_id)
            rec.estimated_tokens += token_count
            rec.duration_seconds += result.duration_seconds
            rec.retries += result.retries

        # Attach gate results to agents — associate gates with the agents in
        # the corresponding phase (best-effort; use gate PASS/FAIL strings).
        for gate in state.gate_results:
            gate_str = "PASS" if gate.passed else "FAIL"
            phase_agents = _agents_in_phase(state.plan, gate.phase_id)
            for agent_name in phase_agents:
                if agent_name in agent_map:
                    agent_map[agent_name].gate_results.append(gate_str)

        gates_passed = sum(1 for g in state.gate_results if g.passed)
        gates_failed = sum(1 for g in state.gate_results if not g.passed)
        outcome = "SHIP" if state.status == "complete" else (
            "BLOCK" if state.status == "failed" else ""
        )

        return TaskUsageRecord(
            task_id=state.task_id,
            timestamp=state.completed_at or _utcnow(),
            agents_used=list(agent_map.values()),
            total_agents=len(agent_map),
            risk_level=state.plan.risk_level,
            sequencing_mode=state.plan.execution_mode,
            gates_passed=gates_passed,
            gates_failed=gates_failed,
            outcome=outcome,
        )

    def _build_retrospective_data(self, state: ExecutionState) -> dict:
        """Build a rich data dict for the retrospective from execution state.

        Extracts per-agent outcomes, knowledge gap signals, sequencing
        observations, and roster recommendations from the step results,
        gate results, and plan structure — turning raw execution data into
        actionable learning feedback.
        """
        from agent_baton.models.retrospective import (
            AgentOutcome,
            RosterRecommendation,
            SequencingNote,
        )
        from agent_baton.models.knowledge import KnowledgeGapRecord

        what_worked: list[AgentOutcome] = []
        what_didnt: list[AgentOutcome] = []
        knowledge_gaps: list[KnowledgeGapRecord] = []
        sequencing_notes: list[SequencingNote] = []
        roster_recs: list[RosterRecommendation] = []

        # ── Per-agent outcomes from step results ──────────────────────────
        agent_steps: dict[str, list] = {}
        for result in state.step_results:
            agent_steps.setdefault(result.agent_name, []).append(result)

        for agent_name, results in agent_steps.items():
            successes = [r for r in results if r.status == "complete"]
            failures = [r for r in results if r.status == "failed"]
            total_retries = sum(r.retries for r in results)
            files_changed = []
            for r in successes:
                files_changed.extend(r.files_changed)

            if successes and not failures:
                worked_detail = f"Completed {len(successes)} step(s)"
                if files_changed:
                    worked_detail += f", changed {len(files_changed)} file(s)"
                if total_retries == 0:
                    worked_detail += " — first-pass success"
                what_worked.append(AgentOutcome(
                    name=agent_name,
                    worked_well=worked_detail,
                ))
            elif failures:
                fail = failures[-1]  # most recent failure
                what_didnt.append(AgentOutcome(
                    name=agent_name,
                    issues=fail.error or f"Failed at step {fail.step_id}",
                    root_cause=fail.error[:200] if fail.error else "",
                ))
                # Signal a knowledge gap if the agent failed with retries
                if total_retries > 0 or len(failures) > 1:
                    knowledge_gaps.append(KnowledgeGapRecord(
                        description=(
                            f"{agent_name} struggled: "
                            f"{len(failures)} failure(s), "
                            f"{total_retries} retry(ies)"
                        ),
                        gap_type="contextual",
                        resolution="unresolved",
                        resolution_detail="review agent prompt or add knowledge pack",
                        agent_name=agent_name,
                        task_summary=state.plan.task_summary,
                    ))
                # Recommend improvement if retry rate is high
                if total_retries >= 2:
                    roster_recs.append(RosterRecommendation(
                        action="improve",
                        target=agent_name,
                        reason=(
                            f"High retry rate ({total_retries}) suggests "
                            f"prompt or knowledge gap"
                        ),
                    ))

        # ── Deviation notes → sequencing notes ────────────────────────────
        # Agents can signal plan misfit via a Deviation section in their outcome.
        # These feed the retrospective learning loop to improve future plans.
        for result in state.step_results:
            if result.deviations:
                for dev in result.deviations:
                    sequencing_notes.append(SequencingNote(
                        phase="deviation",
                        observation=f"Agent {result.agent_name} deviated: {dev}",
                    ))

        # ── Gate outcomes → sequencing notes ──────────────────────────────
        for gate_result in state.gate_results:
            phase = next(
                (p for p in state.plan.phases
                 if p.phase_id == gate_result.phase_id),
                None,
            )
            phase_name = phase.name if phase else f"Phase {gate_result.phase_id}"
            gate_type = gate_result.gate_type

            if gate_result.passed:
                sequencing_notes.append(SequencingNote(
                    phase=phase_name,
                    observation=f"Gate '{gate_type}' passed",
                    keep=True,
                ))
            else:
                sequencing_notes.append(SequencingNote(
                    phase=phase_name,
                    observation=(
                        f"Gate '{gate_type}' FAILED"
                        + (f": {gate_result.output[:100]}"
                           if gate_result.output else "")
                    ),
                    keep=True,
                ))

        # ── Token efficiency signal ───────────────────────────────────────
        total_tokens = sum(
            r.estimated_tokens for r in state.step_results
        )
        total_steps = state.plan.total_steps
        if total_steps > 0 and total_tokens > 0:
            avg_per_step = total_tokens // total_steps
            if avg_per_step > 50000:
                knowledge_gaps.append(KnowledgeGapRecord(
                    description=(
                        f"High token usage: ~{avg_per_step:,} tokens/step "
                        f"({total_tokens:,} total). May indicate agents "
                        f"exploring too broadly."
                    ),
                    gap_type="contextual",
                    resolution="unresolved",
                    resolution_detail="add context_files to reduce search scope",
                    agent_name="",
                    task_summary=state.plan.task_summary,
                ))

        # ── Pending gaps (unresolved KnowledgeGapSignal entries) ─────────
        for signal in state.pending_gaps:
            knowledge_gaps.append(KnowledgeGapRecord(
                description=signal.description,
                gap_type=signal.gap_type,
                resolution="unresolved",
                resolution_detail=signal.partial_outcome or "",
                agent_name=signal.agent_name,
                task_summary=state.plan.task_summary,
            ))

        # ── Resolved decisions (human-answered gaps) ──────────────────────
        for decision in state.resolved_decisions:
            knowledge_gaps.append(KnowledgeGapRecord(
                description=decision.gap_description,
                gap_type="factual",
                resolution="human-answered",
                resolution_detail=decision.resolution,
                agent_name="",
                task_summary=state.plan.task_summary,
            ))

        # ── Team composition tracking ─────────────────────────────────────
        team_compositions: list[TeamCompositionRecord] = []
        conflicts: list[ConflictRecord] = []

        for phase in state.plan.phases:
            for step in phase.steps:
                if not step.team:
                    continue
                result = state.get_step_result(step.step_id)
                if result is None:
                    continue

                agents = sorted(m.agent_name for m in step.team)
                roles = {m.agent_name: m.role for m in step.team}
                outcome = "success" if result.status == "complete" else "failure"

                team_compositions.append(TeamCompositionRecord(
                    step_id=step.step_id,
                    agents=agents,
                    roles=roles,
                    outcome=outcome,
                    task_type=state.plan.task_type,
                    token_cost=result.estimated_tokens,
                ))

                # Detect and record conflicts from team results.
                if result.member_results and len(result.member_results) >= 2:
                    conflict = self._detect_team_conflict(
                        step, result.member_results
                    )
                    if conflict:
                        # Mark as auto_merged if step completed successfully.
                        if result.status == "complete":
                            conflict.resolution = "auto_merged"
                            conflict.resolved_by = "synthesis_agent"
                        conflicts.append(conflict)

        gates_passed = len([g for g in state.gate_results if g.passed])
        gates_failed = len([g for g in state.gate_results if not g.passed])
        agent_count = len({r.agent_name for r in state.step_results})

        return {
            "task_name": state.plan.task_summary,
            "task_id": state.task_id,
            "status": state.status,
            "gates_passed": gates_passed,
            "gates_failed": gates_failed,
            "agent_count": agent_count,
            "what_worked": what_worked,
            "what_didnt": what_didnt,
            "knowledge_gaps": knowledge_gaps,
            "roster_recommendations": roster_recs,
            "sequencing_notes": sequencing_notes,
            "team_compositions": team_compositions,
            "conflicts": conflicts,
        }

    # ── State machine logic ─────────────────────────────────────────────────

    def _determine_action(self, state: ExecutionState) -> ExecutionAction:
        """Core state machine — inspect *state* and return the next action.

        This method is the single source of truth for what comes next.
        It does NOT mutate *state* itself; callers are responsible for saving.
        """
        # Terminal states — report immediately.
        if state.status == "complete":
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message=f"Task {state.task_id} is already complete.",
                summary=f"Task {state.task_id} completed.",
            )
        if state.status == "failed":
            # Check if the failure was caused by an approval rejection rather
            # than a step failure — the message should reflect the distinction.
            rejected_approval = next(
                (a for a in reversed(state.approval_results) if a.result == "reject"),
                None,
            )
            if rejected_approval is not None:
                msg = (
                    f"Phase {rejected_approval.phase_id} approval was rejected. "
                    "To continue: amend the plan with 'baton execute amend', "
                    "or finalize with 'baton execute complete'."
                )
            else:
                failed_ids = list(state.failed_step_ids)
                msg = f"Execution failed. Failed step(s): {', '.join(failed_ids) or 'gate'}"
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message=msg,
                summary=msg,
            )

        # approval_pending — waiting for human approval before proceeding.
        if state.status == "approval_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.approval_required:
                return self._approval_action(state, phase_obj)

        # gate_pending — a gate was requested but result not yet recorded.
        if state.status == "gate_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                return ExecutionAction(
                    action_type=ActionType.GATE,
                    message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )

        # No more phases — all done.
        if state.current_phase >= len(state.plan.phases):
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message=f"All phases of task {state.task_id} are complete.",
                summary=f"Task {state.task_id} completed successfully.",
            )

        phase_obj = state.current_phase_obj
        if phase_obj is None:
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message="No more phases.",
                summary=f"Task {state.task_id} completed.",
            )

        steps = phase_obj.steps

        # If phase has no steps, go straight to gate or next phase.
        if not steps:
            if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
                state.status = "gate_pending"
                return ExecutionAction(
                    action_type=ActionType.GATE,
                    message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )
            # Advance past empty phase with no gate (or gate already done).
            self._publish(evt.phase_completed(
                task_id=state.task_id,
                phase_id=phase_obj.phase_id,
                phase_name=phase_obj.name,
            ))
            state.current_phase += 1
            state.current_step_index = 0
            if state.current_phase < len(state.plan.phases):
                next_phase = state.plan.phases[state.current_phase]
                self._publish(evt.phase_started(
                    task_id=state.task_id,
                    phase_id=next_phase.phase_id,
                    phase_name=next_phase.name,
                    step_count=len(next_phase.steps),
                ))
            return self._determine_action(state)

        # Check for any failed steps in this phase.
        for step in steps:
            if step.step_id in state.failed_step_ids:
                state.status = "failed"
                msg = f"Step {step.step_id} failed."
                return ExecutionAction(
                    action_type=ActionType.FAILED,
                    message=msg,
                    summary=msg,
                )

        # Find the next dispatchable step — must not be completed, failed,
        # dispatched, or interrupted (interrupted steps have been superseded
        # by a re-dispatch step inserted via the knowledge-gap amend flow).
        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        occupied = completed | state.failed_step_ids | dispatched | state.interrupted_step_ids

        next_step: PlanStep | None = None
        for step in steps:
            if step.step_id in occupied:
                continue
            # Check dependency satisfaction: all depends_on must be completed.
            if step.depends_on and not all(
                dep in completed for dep in step.depends_on
            ):
                continue
            next_step = step
            break

        if next_step is not None:
            # There is still work to do in this phase.
            if next_step.team:
                return self._team_dispatch_action(next_step, state)
            return self._dispatch_action(next_step, state)

        # If no step is dispatchable but some are still pending (dispatched or
        # blocked by dependencies), return WAIT.
        # Interrupted steps are excluded: they have been superseded by amended
        # re-dispatch steps and must not hold the phase in a WAIT loop.
        pending = (
            {s.step_id for s in steps}
            - completed
            - state.failed_step_ids
            - state.interrupted_step_ids
        )
        if pending:
            return ExecutionAction(
                action_type=ActionType.WAIT,
                message="Waiting for in-flight steps to complete before proceeding.",
                summary=f"Steps in flight or blocked: {', '.join(sorted(pending))}",
            )

        # All steps in this phase are complete.
        # Check approval requirement BEFORE gate.
        if (phase_obj.approval_required
                and not self._approval_passed_for_phase(state, phase_obj.phase_id)):
            state.status = "approval_pending"
            return self._approval_action(state, phase_obj)

        if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
            state.status = "gate_pending"
            return ExecutionAction(
                action_type=ActionType.GATE,
                message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                gate_type=phase_obj.gate.gate_type,
                gate_command=phase_obj.gate.command,
                phase_id=phase_obj.phase_id,
            )

        # Gate passed (or no gate) — move to next phase.
        self._publish(evt.phase_completed(
            task_id=state.task_id,
            phase_id=phase_obj.phase_id,
            phase_name=phase_obj.name,
        ))
        state.current_phase += 1
        state.current_step_index = 0
        state.status = "running"
        if state.current_phase < len(state.plan.phases):
            next_phase = state.plan.phases[state.current_phase]
            self._publish(evt.phase_started(
                task_id=state.task_id,
                phase_id=next_phase.phase_id,
                phase_name=next_phase.name,
                step_count=len(next_phase.steps),
            ))
        return self._determine_action(state)

    def _dispatch_action(self, step: PlanStep, state: ExecutionState) -> ExecutionAction:
        """Build a DISPATCH action for *step*."""
        dispatcher = PromptDispatcher()

        # Find the most recent completed step (different step_id) for handoff.
        handoff = ""
        for result in reversed(state.step_results):
            if result.step_id != step.step_id and result.status == "complete" and result.outcome:
                handoff = result.outcome
                break

        # Append resolved decisions to the handoff so the agent does not re-litigate
        # knowledge gaps that have already been answered.
        handoff = _append_resolved_decisions(handoff, state.resolved_decisions)

        prompt = dispatcher.build_delegation_prompt(
            step,
            shared_context=state.plan.shared_context,
            handoff_from=handoff,
            task_summary=state.plan.task_summary,
            task_type=state.plan.task_type or "",
        )
        enforcement = PromptDispatcher.build_path_enforcement(step)
        return ExecutionAction(
            action_type=ActionType.DISPATCH,
            message=f"Dispatch agent '{step.agent_name}' for step {step.step_id}.",
            agent_name=step.agent_name,
            agent_model=step.model,
            delegation_prompt=prompt,
            step_id=step.step_id,
            path_enforcement=enforcement or "",
        )

    @staticmethod
    def _gate_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
        """Return True if a passing gate result exists for *phase_id*."""
        for g in state.gate_results:
            if g.phase_id == phase_id and g.passed:
                return True
        return False

    @staticmethod
    def _approval_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
        """Return True if an approval result (approve or approve-with-feedback) exists."""
        for a in state.approval_results:
            if a.phase_id == phase_id and a.result in ("approve", "approve-with-feedback"):
                return True
        return False

    def _approval_action(
        self, state: ExecutionState, phase_obj: PlanPhase,
    ) -> ExecutionAction:
        """Build an APPROVAL action for a phase requiring human review."""
        context = phase_obj.approval_description or self._build_approval_context(
            state, phase_obj,
        )
        return ExecutionAction(
            action_type=ActionType.APPROVAL,
            message=(
                f"Phase {phase_obj.phase_id} ({phase_obj.name}) "
                f"requires approval before proceeding."
            ),
            phase_id=phase_obj.phase_id,
            approval_context=context,
            approval_options=["approve", "reject", "approve-with-feedback"],
        )

    @staticmethod
    def _build_approval_context(
        state: ExecutionState, phase_obj: PlanPhase,
    ) -> str:
        """Build a markdown summary of phase output for the human reviewer.

        Also surfaces any pending knowledge gaps so the reviewer can answer
        them before execution continues.
        """
        lines = [
            f"## Phase {phase_obj.phase_id}: {phase_obj.name} — Review Summary",
            "",
        ]
        # Gather step results for this phase.
        phase_step_ids = {s.step_id for s in phase_obj.steps}
        for result in state.step_results:
            if result.step_id in phase_step_ids and result.status == "complete":
                lines.append(f"### Step {result.step_id}: {result.agent_name}")
                if result.outcome:
                    lines.append(result.outcome)
                if result.files_changed:
                    lines.append(f"**Files changed**: {', '.join(result.files_changed)}")
                lines.append("")

        # Surface pending knowledge gaps for human resolution.
        if state.pending_gaps:
            lines.append("## Pending Knowledge Gaps")
            lines.append(
                "The following gaps were flagged by agents and require your input "
                "before execution can continue:"
            )
            lines.append("")
            for gap in state.pending_gaps:
                lines.append(
                    f"- **Step {gap.step_id}** ({gap.agent_name}, "
                    f"confidence={gap.confidence}, type={gap.gap_type}): "
                    f"{gap.description}"
                )
            lines.append("")

        return "\n".join(lines)

    def _team_dispatch_action(
        self, step: PlanStep, state: ExecutionState,
    ) -> ExecutionAction:
        """Build a DISPATCH action with parallel_actions for each team member."""
        dispatcher = PromptDispatcher()

        # Build team overview for context.
        team_overview = ", ".join(
            f"{m.agent_name} ({m.role})" for m in step.team
        )

        # Find completed member IDs (if any members already recorded).
        parent = state.get_step_result(step.step_id)
        completed_members = set()
        if parent:
            completed_members = {
                m.member_id for m in parent.member_results
                if m.status == "complete"
            }

        member_actions: list[ExecutionAction] = []
        for member in step.team:
            if member.member_id in completed_members:
                continue
            # Check member-level dependencies.
            if member.depends_on and not all(
                dep in completed_members for dep in member.depends_on
            ):
                continue

            prompt = dispatcher.build_team_delegation_prompt(
                step=step,
                member=member,
                shared_context=state.plan.shared_context,
                task_summary=state.plan.task_summary,
                team_overview=team_overview,
            )
            member_actions.append(ExecutionAction(
                action_type=ActionType.DISPATCH,
                message=f"Team member '{member.agent_name}' ({member.role}) for step {step.step_id}.",
                agent_name=member.agent_name,
                agent_model=member.model,
                delegation_prompt=prompt,
                step_id=member.member_id,
            ))

        if not member_actions:
            # All dispatchable members are blocked — WAIT.
            return ExecutionAction(
                action_type=ActionType.WAIT,
                message=f"Waiting for team members in step {step.step_id}.",
                summary=f"Team step {step.step_id} has members in flight.",
            )

        # Return the first member action with the rest as parallel_actions.
        first = member_actions[0]
        if len(member_actions) > 1:
            first.parallel_actions = member_actions[1:]
        return first

    def _amend_from_feedback(
        self, state: ExecutionState, phase_id: int, feedback: str,
    ) -> None:
        """Insert a remediation phase based on approval feedback.

        Creates a new phase with a single step assigned to the most
        appropriate agent, inserted after the current phase.
        """
        # Determine which agent should handle remediation.
        phase_obj = state.current_phase_obj
        if phase_obj and phase_obj.steps:
            agent = phase_obj.steps[0].agent_name
        else:
            agent = "backend-engineer"

        # Build a new phase_id (will be renumbered by amend_plan).
        new_phase = PlanPhase(
            phase_id=0,  # placeholder — renumbered in amend_plan
            name="Remediation",
            steps=[PlanStep(
                step_id="0.1",  # placeholder
                agent_name=agent,
                task_description=f"Address feedback from phase {phase_id} review: {feedback}",
            )],
        )
        self.amend_plan(
            description=f"Remediation from approval feedback on phase {phase_id}",
            new_phases=[new_phase],
            trigger="approval_feedback",
            trigger_phase_id=phase_id,
            feedback=feedback,
        )

    @staticmethod
    def _find_step(state: ExecutionState, step_id: str) -> PlanStep | None:
        """Locate a PlanStep by step_id in the plan."""
        for phase in state.plan.phases:
            for step in phase.steps:
                if step.step_id == step_id:
                    return step
        return None

    @staticmethod
    def _renumber_phases(state: ExecutionState) -> None:
        """Re-assign sequential phase_id values (1-based) after insertion.

        Also updates step_ids to match new phase numbering, and fixes
        references in gate_results and approval_results.
        """
        old_to_new: dict[int, int] = {}
        for idx, phase in enumerate(state.plan.phases):
            new_id = idx + 1
            old_to_new[phase.phase_id] = new_id
            phase.phase_id = new_id
            # Renumber step_ids within this phase.
            for si, step in enumerate(phase.steps):
                step.step_id = f"{new_id}.{si + 1}"
                # Renumber team member IDs if present.
                for mi, member in enumerate(step.team):
                    member.member_id = f"{new_id}.{si + 1}.{chr(97 + mi)}"

        # Update phase_id references in gate and approval results.
        for gr in state.gate_results:
            gr.phase_id = old_to_new.get(gr.phase_id, gr.phase_id)
        for ar in state.approval_results:
            ar.phase_id = old_to_new.get(ar.phase_id, ar.phase_id)

    @staticmethod
    def _locate_step(state: ExecutionState, step_id: str) -> tuple[int, int]:
        """Return (phase_index, step_index) for *step_id* in the plan.

        Returns (-1, -1) if not found.
        """
        for pi, phase in enumerate(state.plan.phases):
            for si, step in enumerate(phase.steps):
                if step.step_id == step_id:
                    return pi, si
        return -1, -1

    def _handle_knowledge_gap(
        self,
        outcome: str,
        step_id: str,
        agent_name: str,
        state: ExecutionState,
    ) -> None:
        """Inspect *outcome* for a KNOWLEDGE_GAP signal and take the appropriate action.

        This is the core of the runtime knowledge acquisition protocol.
        Called from :meth:`record_step_result` after the StepResult is
        appended but before the state is saved.

        Three outcomes are possible based on the escalation matrix:

        * ``auto-resolve``: The resolver found matching knowledge.  A
          ``ResolvedDecision`` is recorded so re-dispatches carry the answer.
        * ``best-effort``: LOW risk + low intervention + no match.  Log and
          continue — the caller proceeds with best-effort work.
        * ``queue-for-gate``: The gap is appended to
          ``state.pending_gaps`` so it surfaces at the next human review gate.

        When a ``KnowledgeResolver`` is available on the engine (set by the
        caller), it is used for auto-resolution.  Otherwise the auto-resolve
        path logs a warning and falls back to ``queue-for-gate`` — the
        engine should not fail silently if the resolver was expected.
        """
        signal = parse_knowledge_gap(outcome, step_id=step_id, agent_name=agent_name)
        if signal is None:
            return

        logger.debug(
            "KNOWLEDGE_GAP detected in step %s (%s): %r [confidence=%s, type=%s]",
            step_id, agent_name, signal.description, signal.confidence, signal.gap_type,
        )

        # Attempt auto-resolution via resolver if available.
        resolver = getattr(self, "_knowledge_resolver", None)
        resolution_found = False
        resolved_detail = ""
        attachments: list = []

        if resolver is not None:
            try:
                attachments = resolver.resolve(
                    agent_name=agent_name,
                    task_description=signal.description,
                )
                if attachments:
                    resolution_found = True
                    resolved_detail = "auto-resolved via " + ", ".join(
                        f"{a.pack_name or 'unknown'}/{a.document_name}"
                        for a in attachments
                    )
            except Exception:
                logger.warning(
                    "KnowledgeResolver.resolve() raised for gap in step %s — "
                    "treating as no match",
                    step_id, exc_info=True,
                )

        risk_level = state.plan.risk_level
        intervention_level = getattr(state.plan, "intervention_level", "low")
        action = determine_escalation(
            signal,
            risk_level=risk_level,
            intervention_level=intervention_level,
            resolution_found=resolution_found,
        )

        logger.info(
            "Knowledge gap escalation for step %s: %s (risk=%s, intervention=%s, match=%s)",
            step_id, action, risk_level, intervention_level, resolution_found,
        )

        if action == "auto-resolve":
            # Record a ResolvedDecision so future re-dispatches carry the answer.
            decision = ResolvedDecision(
                gap_description=signal.description,
                resolution=resolved_detail or "auto-resolved (no detail)",
                step_id=step_id,
                timestamp=_utcnow(),
            )
            state.resolved_decisions.append(decision)
            logger.info(
                "Auto-resolved knowledge gap for step %s: %r",
                step_id, signal.description,
            )

            # Amend the plan: insert a re-dispatch step for the same agent
            # immediately after the interrupted step.  The interrupted step
            # result is already in state.step_results (status="interrupted"),
            # so _determine_action will skip it via interrupted_step_ids.
            #
            # We mutate state directly here instead of calling amend_plan()
            # because record_step_result() has not yet flushed state to disk —
            # amend_plan() would load a stale snapshot and lose the interrupted
            # step result.  The amendment audit record is still created so the
            # trace and amendment log remain complete.
            interrupted_plan_step = self._find_step(state, step_id)
            if interrupted_plan_step is not None:
                # Locate the containing phase.
                containing_phase: PlanPhase | None = None
                for phase in state.plan.phases:
                    if any(s.step_id == step_id for s in phase.steps):
                        containing_phase = phase
                        break

                if containing_phase is not None:
                    new_step_id = (
                        f"{containing_phase.phase_id}.{len(containing_phase.steps) + 1}"
                    )
                    # Note: the handoff context (partial outcome + resolved decisions) is
                    # injected at dispatch time by _dispatch_action → _append_resolved_decisions.
                    # state.resolved_decisions already contains the decision we just recorded.
                    re_dispatch_step = PlanStep(
                        step_id=new_step_id,
                        agent_name=interrupted_plan_step.agent_name,
                        task_description=(
                            interrupted_plan_step.task_description
                            + "\n\nContinue from partial progress."
                        ),
                        model=interrupted_plan_step.model,
                        knowledge=list(attachments) if attachments else [],
                        context_files=list(interrupted_plan_step.context_files),
                        allowed_paths=list(interrupted_plan_step.allowed_paths),
                        blocked_paths=list(interrupted_plan_step.blocked_paths),
                    )
                    containing_phase.steps.append(re_dispatch_step)

                    # Record the amendment for audit / trace.
                    amendment = PlanAmendment(
                        amendment_id=f"amend-{len(state.amendments) + 1}",
                        trigger="knowledge_gap",
                        trigger_phase_id=containing_phase.phase_id,
                        description=(
                            f"Re-dispatch after auto-resolved gap in step {step_id}: "
                            f"{signal.description!r}"
                        ),
                        steps_added=[new_step_id],
                    )
                    state.amendments.append(amendment)

                    if self._trace is not None:
                        self._tracer.record_event(
                            self._trace,
                            "replan",
                            agent_name=None,
                            phase=containing_phase.phase_id,
                            step=0,
                            details={
                                "amendment_id": amendment.amendment_id,
                                "description": amendment.description,
                                "phases_added": [],
                                "steps_added": [new_step_id],
                            },
                        )

                    logger.info(
                        "Amended plan: inserted re-dispatch step %s after "
                        "interrupted step %s for agent %s",
                        new_step_id, step_id, interrupted_plan_step.agent_name,
                    )

        elif action == "best-effort":
            # Log and continue — nothing added to state.
            logger.info(
                "Best-effort knowledge gap for step %s: %r (proceeding without resolution)",
                step_id, signal.description,
            )

        else:  # queue-for-gate
            # Surface at the next human review gate.
            state.pending_gaps.append(signal)
            logger.info(
                "Queued knowledge gap for step %s for human review: %r",
                step_id, signal.description,
            )


# ---------------------------------------------------------------------------
# Private utilities (module-level to keep the class lean)
# ---------------------------------------------------------------------------

def _append_resolved_decisions(
    handoff: str,
    resolved_decisions: list[ResolvedDecision],
) -> str:
    """Append a 'Resolved Decisions' section to a handoff string.

    When *resolved_decisions* is non-empty, the section is appended so the
    re-dispatched agent sees final answers and does not re-litigate them.

    Returns the original *handoff* unchanged if there are no decisions.
    """
    if not resolved_decisions:
        return handoff

    lines = []
    if handoff:
        lines.append(handoff)
        lines.append("")

    lines.append("## Resolved Decisions (final — do not revisit)")
    for decision in resolved_decisions:
        lines.append(f'- "{decision.gap_description}": {decision.resolution}')

    return "\n".join(lines)


def _build_delegation_prompt(step: PlanStep, plan: MachinePlan) -> str:
    """Build a minimal delegation prompt for a plan step.

    This is a lightweight fallback used internally when the full
    ``PromptDispatcher`` is not needed (e.g., for trace reconstruction).
    For actual agent dispatch, ``PromptDispatcher.build_delegation_prompt``
    is used instead.
    """
    lines = [
        f"# Agent Task: {step.step_id}",
        "",
        f"**Task**: {step.task_description}",
    ]
    if plan.shared_context:
        lines += ["", "## Shared Context", plan.shared_context]
    if step.context_files:
        lines += ["", "## Read these files first"]
        for cf in step.context_files:
            lines.append(f"- {cf}")
    if step.deliverables:
        lines += ["", "## Deliverables"]
        for d in step.deliverables:
            lines.append(f"- {d}")
    if step.allowed_paths:
        lines += ["", f"**Allowed paths**: {', '.join(step.allowed_paths)}"]
    if step.blocked_paths:
        lines += [f"**Blocked paths**: {', '.join(step.blocked_paths)}"]
    lines += [
        "",
        "Read `CLAUDE.md` for project conventions. Shared context is provided above.",
    ]
    return "\n".join(lines)


def _model_for_step(plan: MachinePlan, step_id: str) -> str:
    """Look up the model declared for *step_id* in *plan*.

    Falls back to ``"sonnet"`` if the step is not found.
    """
    for phase in plan.phases:
        for step in phase.steps:
            if step.step_id == step_id:
                return step.model
    return "sonnet"


# Chars-per-token constant (1 token ≈ 4 chars) used throughout the codebase.
_CHARS_PER_TOKEN = 4


def _estimate_tokens_for_step(plan: MachinePlan, step_id: str) -> int:
    """Estimate the token cost for *step_id* from its plan step content.

    Uses the task description, deliverables, and shared context as a proxy for
    the delegation prompt that was actually sent to the agent.  This heuristic
    is intentionally conservative — real prompt tokens will be higher because
    the dispatcher injects additional boilerplate and handoff context — but it
    is far more useful than leaving ``estimated_tokens`` at 0.

    Returns 0 if *step_id* is not found in the plan.
    """
    for phase in plan.phases:
        for step in phase.steps:
            if step.step_id == step_id:
                content = step.task_description
                if plan.shared_context:
                    content += plan.shared_context
                for deliverable in step.deliverables:
                    content += deliverable
                return max(1, len(content) // _CHARS_PER_TOKEN)
    return 0


def _agents_in_phase(plan: MachinePlan, phase_id: int) -> list[str]:
    """Return unique agent names for steps in *phase_id*.

    Used by ``_build_usage_record`` to associate gate results with the
    agents that participated in the corresponding phase.
    """
    seen: set[str] = set()
    result: list[str] = []
    for phase in plan.phases:
        if phase.phase_id == phase_id:
            for step in phase.steps:
                if step.agent_name not in seen:
                    result.append(step.agent_name)
                    seen.add(step.agent_name)
    return result
