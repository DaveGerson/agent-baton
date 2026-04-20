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
- Event ownership: the engine publishes task-level, phase-level, and
  step-level events.  ``TaskWorker`` also publishes step-level events via
  its own path (headless/async execution); both paths call ``_publish``,
  which is a no-op when no bus is configured, so there is no double-emit
  risk — each path owns its own engine instance.
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
    FeedbackQuestion,
    FeedbackResult,
    GateResult,
    InteractionTurn,
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
from agent_baton.core.govern.compliance import ComplianceEntry, ComplianceReportGenerator


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


def _cli_actor() -> str:
    """Return a best-effort identity string for the current CLI user.

    Produces ``"$USER@$HOSTNAME"`` when both environment variables are
    available, falls back to ``"$USER"`` or ``"unknown"`` if not.
    Used to populate the A2 ``actor`` field on gate and approval results.
    """
    import os
    import socket
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""
    return f"{user}@{hostname}" if hostname else user


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
# NOTE: As of 2026-04-13 the task-view.json file written by this subscriber
# is not consumed by any production subsystem (CLI commands, API routes, or
# the PMO UI all read from different sources).  The subscriber is kept because
# it is exercised by tests (test_executor.py TestTaskViewSubscriber and
# test_events.py TestTaskViewSubscriber) and the file may be useful for
# external tooling or future dashboards.
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
        policy_engine=None,  # PolicyEngine | None
        enforce_token_budget: bool = True,
        token_budget: int | None = None,
        max_gate_retries: int = 3,
    ) -> None:
        self._root = (team_context_root or self._DEFAULT_CONTEXT_ROOT).resolve()
        self._task_id = task_id
        self._storage = storage  # May be None (legacy file mode)
        self._bus = bus
        # Maximum number of times a gate may fail before the engine
        # automatically transitions to "failed" instead of issuing another
        # retryable GATE action.  Guards against infinite retry loops when
        # gate failures are recorded programmatically (e.g. headless / API).
        # Operators can still call fail_gate() at any time to force a terminal
        # failure before the cap is reached.
        self._max_gate_retries: int = max_gate_retries

        # KnowledgeResolver for runtime gap auto-resolution.  Callers (CLI and
        # tests) set this at construction time.  When None, gaps fall through to
        # best-effort / queue-for-gate.
        self._knowledge_resolver = knowledge_resolver

        # PolicyEngine for pre-dispatch enforcement.  When set, block-severity
        # violations inject an APPROVAL action instead of proceeding.
        self._policy_engine = policy_engine

        # Session-level set of step IDs that received human unblock for a
        # policy violation.  Populated by record_policy_approval(); checked
        # in _dispatch_action() to skip the policy gate on re-dispatch.
        self._policy_approved_steps: set[str] = set()

        # Token budget enforcement (B1).
        # When enforce_token_budget is True and the cumulative token count
        # exceeds the plan tier threshold (or the explicit token_budget cap),
        # _determine_action() will set state.status = "budget_exceeded" and
        # return a COMPLETE action rather than dispatching new steps.
        # In-flight work is never aborted — only NEW dispatches are blocked.
        self._enforce_token_budget: bool = enforce_token_budget
        # Explicit per-session token cap (overrides the tier threshold).
        # 0 / None → use the plan's budget_tier threshold.
        self._token_budget: int | None = token_budget or None

        # Compliance audit log — JSONL file written best-effort.  Path is
        # resolved after _root is known; initialized to None until first write.
        self._compliance_log_path: Path | None = None

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

        # Resolve compliance log path now that _root is known.
        self._compliance_log_path = self._root / "compliance-audit.jsonl"

        # ── Bead memory store (schema v4, Inspired by beads-ai/beads-cli) ───
        # Initialised from the same db_path as _storage.  Silently None when
        # the storage backend is unavailable or uses an older schema — all
        # bead operations degrade gracefully.
        self._bead_store = None
        if storage is not None:
            try:
                from agent_baton.core.engine.bead_store import BeadStore
                self._bead_store = BeadStore(storage.db_path)
            except Exception as _bead_init_exc:
                _log.debug(
                    "BeadStore init skipped (non-fatal): %s", _bead_init_exc
                )

    # ── Storage routing helpers ──────────────────────────────────────────────

    def _save_execution(self, state: ExecutionState) -> None:
        """Persist execution state via storage backend or legacy file.

        When SQLite fails for any reason, the fallback writes *state* as-is
        to the JSON file.  *state* is always the post-mutation object — the
        caller mutates state before calling this method, never after.

        When the two backends diverge (SQLite fails but file succeeds), a
        WARNING is emitted with the task_id, status, and per-step status
        summary so the split-brain is visible in logs without DB inspection.
        """
        if self._storage is not None:
            try:
                self._storage.save_execution(state)
            except Exception as e:
                step_summary = ", ".join(
                    f"{r.step_id}={r.status}" for r in state.step_results
                ) or "(no steps)"
                _log.warning(
                    "SQLite save failed for task %r (status=%r, steps=[%s]); "
                    "falling back to file persistence — SQLite and file state "
                    "may diverge. Error: %s",
                    state.task_id,
                    state.status,
                    step_summary,
                    e,
                )
                if self._persistence is not None:
                    self._persistence.save(state)
                return
            # Dual-write: keep file-based persistence in sync for resilience.
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

    def _require_execution(self, caller: str) -> ExecutionState:
        """Load execution state or raise with a diagnostic message.

        Use this in any public method that requires an active execution.
        ``caller`` is the method name shown in the error message.
        """
        state = self._load_execution()
        if state is None:
            task_hint = self._task_id or "(no task_id)"
            raise RuntimeError(
                f"{caller}() called but no execution state found for "
                f"task '{task_hint}'. The active task pointer may reference "
                f"an execution that was never started or was cleaned up.\n"
                f"Recovery: run 'baton execute start' to begin a new "
                f"execution, or 'baton execute list' to find existing ones."
            )
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

    # ── Split-brain reconciliation helpers ──────────────────────────────────
    # Step status advancement order: dispatched < interrupted < failed < complete.
    # Used by _reconcile_states to pick the more-advanced result when SQLite and
    # the file backend disagree after a failed write.

    _STEP_STATUS_RANK: dict[str, int] = {
        "dispatched":  1,
        "interrupted": 2,
        "failed":      3,
        "complete":    4,
    }

    @classmethod
    def _step_status_rank(cls, status: str) -> int:
        """Return lifecycle rank for a step status; unknown statuses rank 0."""
        return cls._STEP_STATUS_RANK.get(status, 0)

    def _reconcile_states(
        self,
        primary: "ExecutionState",
        secondary: "ExecutionState",
        primary_label: str = "primary",
        secondary_label: str = "secondary",
    ) -> "ExecutionState":
        """Return a reconciled execution state from two potentially divergent backends.

        Bi-directional reconciliation strategy (per-step):

        1. If both results carry a non-empty ``updated_at`` timestamp, the
           result with the **newer** timestamp wins, regardless of which
           backend it came from.  This handles the reverse split-brain case
           where SQLite has a step recorded *after* the last file dual-write
           succeeded.

        2. If either result has an empty/None ``updated_at`` (pre-v12 data),
           fall back to status-rank ordering so older databases continue to
           work correctly.

        3. In both cases, status is **never downgraded** — if timestamps say
           a stale record is newer but its status is lower rank, we keep the
           higher-rank status.

        4. Steps present only in *secondary* (not in primary at all) are
           added to the reconciled result list — this handles the case where
           SQLite recorded a step that the file backend never saw.

        Does NOT mutate either input state.  Returns *primary* unchanged when
        no corrections are needed.

        Args:
            primary: State from the normal ``_load_execution`` path (SQLite).
            secondary: State from the alternate backend (file).
            primary_label: Label for log messages.
            secondary_label: Label for log messages.

        Returns:
            The reconciled ``ExecutionState``.
        """
        import copy

        primary_by_step: dict[str, "StepResult"] = {
            r.step_id: r for r in primary.step_results
        }
        secondary_by_step: dict[str, "StepResult"] = {
            r.step_id: r for r in secondary.step_results
        }

        corrections: list[str] = []
        reconciled_results = list(primary.step_results)

        # ── Pass 1: resolve steps present in both backends ───────────────────
        for idx, primary_result in enumerate(reconciled_results):
            sec_result = secondary_by_step.get(primary_result.step_id)
            if sec_result is None:
                continue

            pri_ts = primary_result.updated_at or ""
            sec_ts = sec_result.updated_at or ""

            if pri_ts and sec_ts:
                # Both have timestamps — newer write wins (bi-directional).
                try:
                    pri_dt = datetime.fromisoformat(pri_ts)
                    sec_dt = datetime.fromisoformat(sec_ts)
                except ValueError:
                    # Unparseable timestamp: fall through to rank-based logic.
                    pri_dt = sec_dt = None

                if pri_dt is not None and sec_dt is not None and sec_dt > pri_dt:
                    # Secondary is newer — but never downgrade status.
                    if self._step_status_rank(sec_result.status) >= self._step_status_rank(
                        primary_result.status
                    ):
                        corrections.append(
                            f"step {primary_result.step_id}: "
                            f"{primary_label}={primary_result.status!r}"
                            f"@{pri_ts} -> "
                            f"{secondary_label}={sec_result.status!r}"
                            f"@{sec_ts} (newer timestamp)"
                        )
                        reconciled_results[idx] = sec_result
                    continue
                # Primary is newer or equal — keep primary as-is.
                continue

            # ── Fallback: no timestamps on one or both sides — use rank ───────
            if self._step_status_rank(sec_result.status) > self._step_status_rank(
                primary_result.status
            ):
                corrections.append(
                    f"step {primary_result.step_id}: "
                    f"{primary_label}={primary_result.status!r} -> "
                    f"{secondary_label}={sec_result.status!r} (status-rank fallback)"
                )
                reconciled_results[idx] = sec_result

        # ── Pass 2: add steps present only in secondary ──────────────────────
        for step_id, sec_result in secondary_by_step.items():
            if step_id not in primary_by_step:
                corrections.append(
                    f"step {step_id}: only in {secondary_label} "
                    f"(status={sec_result.status!r}) — added to reconciled state"
                )
                reconciled_results.append(sec_result)

        if not corrections:
            return primary

        _log.warning(
            "Persistence split-brain detected for task %r during resume — "
            "reconciling %s and %s backends. Corrections: %s. "
            "Check logs for earlier write-failure warnings.",
            primary.task_id,
            primary_label,
            secondary_label,
            "; ".join(corrections),
        )

        reconciled = copy.copy(primary)
        reconciled.step_results = reconciled_results
        return reconciled

    # ── Compliance audit helpers ─────────────────────────────────────────────

    def _write_compliance_entry(self, entry: dict) -> None:
        """Append a compliance audit entry as a JSONL line.

        This is best-effort: any I/O failure is logged and silently swallowed
        so that compliance write failures never block execution.

        ``entry`` should include at minimum: ``timestamp``, ``event_type``,
        ``task_id``, ``plan_id``, ``step_id``, and ``agent_name``.
        """
        if self._compliance_log_path is None:
            return
        try:
            self._compliance_log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with self._compliance_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:
            _log.warning("Compliance audit write failed (non-fatal): %s", exc)

    def _compliance_dispatch(
        self,
        state: "ExecutionState",
        step_id: str,
        agent_name: str,
        policy_context: str = "",
    ) -> None:
        """Write a compliance entry for an agent dispatch event."""
        self._write_compliance_entry({
            "timestamp": _utcnow(),
            "event_type": "agent_dispatch",
            "task_id": state.task_id,
            "plan_id": state.plan.task_id,
            "step_id": step_id,
            "agent_name": agent_name,
            "risk_level": state.plan.risk_level,
            "policy_context": policy_context,
        })

    def _compliance_policy_event(
        self,
        state: "ExecutionState",
        step_id: str,
        agent_name: str,
        violations: list,
        action_taken: str,
    ) -> None:
        """Write a compliance entry for a policy violation event."""
        self._write_compliance_entry({
            "timestamp": _utcnow(),
            "event_type": "policy_violation",
            "task_id": state.task_id,
            "plan_id": state.plan.task_id,
            "step_id": step_id,
            "agent_name": agent_name,
            "risk_level": state.plan.risk_level,
            "violations": [
                {
                    "rule_name": v.rule.name,
                    "severity": v.rule.severity,
                    "rule_type": v.rule.rule_type,
                    "details": v.details,
                }
                for v in violations
            ],
            "action_taken": action_taken,
        })

    def _compliance_gate(
        self,
        state: "ExecutionState",
        phase_id: int,
        gate_type: str,
        passed: bool,
        output: str = "",
    ) -> None:
        """Write a compliance entry for a gate evaluation result."""
        self._write_compliance_entry({
            "timestamp": _utcnow(),
            "event_type": "gate_result",
            "task_id": state.task_id,
            "plan_id": state.plan.task_id,
            "step_id": "",
            "agent_name": "engine",
            "risk_level": state.plan.risk_level,
            "phase_id": phase_id,
            "gate_type": gate_type,
            "passed": passed,
            "output_snippet": output[:500] if output else "",
        })

    # ── Policy enforcement helpers ───────────────────────────────────────────

    def _check_policy_block(
        self,
        state: "ExecutionState",
        step: "PlanStep",
    ) -> "ExecutionAction | None":
        """Check *step* against the active policy preset.

        Returns an APPROVAL action if any ``severity='block'`` rule is
        violated, or ``None`` when the step is clear to dispatch.

        The check is lightweight: it uses the plan's ``risk_level`` to
        derive the preset key (matching the planner's mapping) and evaluates
        only ``path_block`` and ``tool_restrict`` rules against the step's
        ``allowed_paths``.  ``require_agent`` / ``require_gate`` are
        structural plan-level concerns already handled by the planner.

        Compliance entries are written for all violations (block + warn) so
        the audit trail is complete.
        """
        if self._policy_engine is None:
            return None
        if step.step_id in self._policy_approved_steps:
            # Human already unblocked this step.
            return None

        try:
            preset_name = _risk_level_to_preset(state.plan.risk_level)
            policy_set = self._policy_engine.load_preset(preset_name)
            if policy_set is None:
                return None

            violations = self._policy_engine.evaluate(
                policy_set,
                step.agent_name,
                list(step.allowed_paths or []),
                [],  # tools not tracked at dispatch time
            )

            # Filter to per-step rule types only; require_* are plan-level.
            violations = [
                v for v in violations
                if v.rule.rule_type in ("path_block", "tool_restrict")
            ]

            if not violations:
                return None

            block_violations = [v for v in violations if v.rule.severity == "block"]
            warn_violations = [v for v in violations if v.rule.severity != "block"]

            # Always write compliance entries for every violation.
            all_violations = block_violations + warn_violations
            if all_violations:
                action_taken = "block_approval" if block_violations else "warn"
                self._compliance_policy_event(
                    state, step.step_id, step.agent_name,
                    all_violations, action_taken,
                )

            if not block_violations:
                # Warn-only violations: log and continue.
                for v in warn_violations:
                    _log.warning(
                        "Policy warn [%s] for step %s / agent %s: %s",
                        v.rule.name, step.step_id, step.agent_name, v.details,
                    )
                return None

            # Hard block — inject APPROVAL for human unblock.
            context = _build_policy_approval_context(
                step, block_violations, warn_violations, policy_set.name,
            )
            _log.info(
                "Policy block on step %s / agent %s — injecting APPROVAL "
                "(%d block violation(s)): %s",
                step.step_id,
                step.agent_name,
                len(block_violations),
                "; ".join(v.rule.name for v in block_violations),
            )
            return ExecutionAction(
                action_type=ActionType.APPROVAL,
                message=(
                    f"Policy block: step {step.step_id} ({step.agent_name}) "
                    f"violates {len(block_violations)} block-severity rule(s). "
                    "Approve to override and proceed, or reject to fail the step."
                ),
                # phase_id is not meaningful here; use sentinel -1 so CLI can
                # distinguish policy approvals from phase-level approvals.
                phase_id=-1,
                approval_context=context,
                approval_options=["approve", "reject"],
                # Embed step_id in summary so record_policy_approval() can
                # route correctly without a schema change.
                summary=step.step_id,
            )
        except Exception as exc:
            _log.warning(
                "Policy check for step %s failed (non-fatal): %s",
                step.step_id, exc,
            )
            return None

    def record_policy_approval(
        self,
        step_id: str,
        result: str,
    ) -> None:
        """Record a human decision on a policy-block APPROVAL for *step_id*.

        Call this when the orchestrator receives an APPROVAL action with
        ``phase_id == -1`` (the policy-block sentinel) and the human
        makes a decision.

        Args:
            step_id: The step ID that was blocked.
            result: ``"approve"`` to unblock the step, ``"reject"`` to fail it.
        """
        if result == "approve":
            self._policy_approved_steps.add(step_id)
            _log.info("Policy unblock recorded for step %s.", step_id)
            state = self._load_execution()
            if state is not None:
                self._compliance_policy_event(
                    state, step_id, "",
                    [], "human_unblock",
                )
        elif result == "reject":
            state = self._load_execution()
            if state is not None:
                state.failed_step_ids.add(step_id)
                state.status = "failed"
                self._save_execution(state)
                self._compliance_policy_event(
                    state, step_id, "",
                    [], "human_reject",
                )
            _log.info("Policy rejection recorded for step %s — step failed.", step_id)
        else:
            raise ValueError(
                f"Invalid policy approval result '{result}'. Must be 'approve' or 'reject'."
            )

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
        # Update file-based persistence's task_id so save() targets the right
        # directory, but do NOT set_active_task() yet — the execution row does
        # not exist until _save_execution() below.  Setting active before save
        # creates a dangling reference that causes "no active execution state"
        # errors if anything fails between here and save.
        if self._persistence is not None:
            self._persistence._task_id = plan.task_id

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
            self._publish(evt.phase_pre_start(
                task_id=plan.task_id,
                phase_id=first_phase.phase_id,
                phase_name=first_phase.name,
                step_count=len(first_phase.steps),
            ))
            self._publish(evt.phase_started(
                task_id=plan.task_id,
                phase_id=first_phase.phase_id,
                phase_name=first_phase.name,
                step_count=len(first_phase.steps),
            ))

        self._save_execution(state)
        # Track the new task_id so _load_execution() can find it by ID.
        self._task_id = state.task_id
        # NOW mark as active — the execution row exists, so the active
        # pointer won't dangle.  Write to both backends for resilience.
        if self._storage is not None:
            try:
                self._storage.set_active_task(state.task_id)
            except Exception:
                pass
        if self._persistence is not None:
            try:
                self._persistence.set_active()
            except Exception:
                _log.warning(
                    "Failed to write active-task-id.txt for task %s",
                    state.task_id,
                    exc_info=True,
                )
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
            task_hint = self._task_id or "(no task_id)"
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message=(
                    f"No execution state found for task '{task_hint}'. "
                    f"Run 'baton execute start' to begin, or "
                    f"'baton execute list' to find existing executions."
                ),
                summary=f"No execution state for '{task_hint}'.",
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

        if state.status in (
            "complete", "failed", "gate_pending", "gate_failed",
            "approval_pending", "budget_exceeded",
        ):
            return []

        if state.current_phase >= len(state.plan.phases):
            return []

        phase_obj = state.current_phase_obj
        if phase_obj is None or not phase_obj.steps:
            return []

        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        interacting_ids = {
            r.step_id for r in state.step_results
            if r.status in ("interacting", "interact_dispatched")
        }
        occupied = (
            completed
            | state.failed_step_ids
            | dispatched
            | state.interrupted_step_ids
            | interacting_ids
        )

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
        session_id: str = "",
        step_started_at: str = "",
    ) -> None:
        """Record the result of a step execution.

        - Creates :class:`StepResult` and appends to state.
        - Emits trace events (``agent_complete`` or ``agent_failed``).
        - Saves state to disk.
        """
        _VALID_STEP_STATUSES = {
            "complete", "failed", "dispatched", "interrupted",
            "interacting", "interact_dispatched",
        }
        if status not in _VALID_STEP_STATUSES:
            raise ValueError(
                f"Invalid step status '{status}'. Must be one of: {_VALID_STEP_STATUSES}"
            )

        state = self._require_execution("record_step_result")

        # ── Interacting status: multi-turn interaction protocol ────────────────
        # When an interactive step reports status="interacting", we append the
        # agent turn to the existing StepResult rather than creating a new one.
        # The execution status stays "running" — other steps keep flowing.
        if status == "interacting":
            existing = state.get_step_result(step_id)
            plan_step = self._find_step(state, step_id)
            max_turns = plan_step.max_turns if plan_step else 10

            if existing is None:
                # First interacting record: create a new StepResult.
                existing = StepResult(
                    step_id=step_id,
                    agent_name=agent_name,
                    status="interacting",
                    outcome=outcome,
                    files_changed=files_changed or [],
                    commit_hash=commit_hash,
                    estimated_tokens=estimated_tokens,
                    duration_seconds=duration_seconds,
                    error=error,
                    completed_at="",
                )
                state.step_results.append(existing)
            else:
                # Update mutable fields from the new agent response.
                existing.agent_name = agent_name
                existing.outcome = outcome

            # Count existing agent turns to determine current turn number.
            agent_turns = [t for t in existing.interaction_history if t.role == "agent"]
            turn_number = len(agent_turns) + 1

            # Check for INTERACT_COMPLETE signal before appending.
            clean_outcome = outcome
            if "\nINTERACT_COMPLETE" in outcome or outcome.strip() == "INTERACT_COMPLETE":
                clean_outcome = outcome.replace("INTERACT_COMPLETE", "").strip()
                existing.outcome = clean_outcome
                existing.status = "complete"
                existing.completed_at = _utcnow()
                existing.updated_at = _utcnow()
                existing.deviations = self._extract_deviations(clean_outcome)
                existing.interaction_history.append(InteractionTurn(
                    role="agent",
                    content=clean_outcome,
                    turn_number=turn_number,
                ))
                self._save_execution(state)
                return

            # Auto-complete when max_turns is exhausted (turn_count = agent + human pairs).
            total_turns = len(existing.interaction_history)
            if total_turns >= max_turns * 2:
                existing.outcome = (
                    clean_outcome
                    + "\n\n[Auto-completed: max_turns reached]"
                )
                existing.status = "complete"
                existing.completed_at = _utcnow()
                existing.updated_at = _utcnow()
                existing.deviations = self._extract_deviations(clean_outcome)
                existing.interaction_history.append(InteractionTurn(
                    role="agent",
                    content=clean_outcome,
                    turn_number=turn_number,
                ))
                _log.warning(
                    "Step %s auto-completed: max_turns (%d) reached.", step_id, max_turns
                )
                self._save_execution(state)
                return

            # Normal interacting turn — append and stay in "interacting".
            existing.interaction_history.append(InteractionTurn(
                role="agent",
                content=clean_outcome,
                turn_number=turn_number,
            ))
            existing.status = "interacting"
            self._save_execution(state)
            return

        # ── Token estimation fallback ──────────────────────────────────────────
        # When the caller supplies estimated_tokens=0 (the default) and the
        # step has actually completed (not just been dispatched), derive a
        # conservative estimate from the plan step's task description.
        # This ensures the DB row is never left with a zero that permanently
        # suppresses usage reporting and budget-tuner learning.
        # "dispatched" steps have no outcome yet; skip the fallback for them.
        effective_tokens = estimated_tokens
        if effective_tokens == 0 and status not in ("dispatched",) and state.plan is not None:
            effective_tokens = _estimate_tokens_for_step(state.plan, step_id)

        # ── Real token accounting via session JSONL scanner ───────────────────
        # When the caller supplies a session_id and step_started_at, scan the
        # Claude Code session JSONL for actual token usage in this step's window.
        # Falls back to char/4 heuristic (effective_tokens) when unavailable.
        real_input = 0
        real_cache_read = 0
        real_cache_creation = 0
        real_output = 0
        real_model = ""
        _sid = session_id or ""
        _started = step_started_at or ""
        if _sid and _started and status not in ("dispatched",):
            try:
                from agent_baton.core.observe.jsonl_scanner import scan_session
                _scan = scan_session(_sid, _started)
                if _scan.turns_scanned > 0:
                    real_input = _scan.input_tokens
                    real_cache_read = _scan.cache_read_tokens
                    real_cache_creation = _scan.cache_creation_tokens
                    real_output = _scan.output_tokens
                    real_model = _scan.model_id
                    # Override heuristic with real total when available.
                    effective_tokens = real_input + real_cache_read + real_output
            except Exception as _scan_exc:  # noqa: BLE001
                _log.debug("JSONL scanner failed (non-fatal): %s", _scan_exc)

        result = StepResult(
            step_id=step_id,
            agent_name=agent_name,
            status=status,
            outcome=outcome,
            files_changed=files_changed or [],
            commit_hash=commit_hash,
            estimated_tokens=effective_tokens,
            duration_seconds=duration_seconds,
            error=error,
            completed_at=_utcnow(),
            deviations=self._extract_deviations(outcome),
            updated_at=_utcnow(),
            input_tokens=real_input,
            cache_read_tokens=real_cache_read,
            cache_creation_tokens=real_cache_creation,
            output_tokens=real_output,
            model_id=real_model,
            session_id=_sid,
            step_started_at=_started,
        )
        # Replace any existing result for this step_id (e.g. a prior
        # "dispatched" row written by mark_dispatched) instead of appending.
        # Appending a duplicate step_id causes save_execution's DELETE+INSERT
        # loop to fail with a UNIQUE constraint on (task_id, step_id).
        existing_idx = next(
            (i for i, r in enumerate(state.step_results) if r.step_id == step_id),
            None,
        )
        if existing_idx is not None:
            state.step_results[existing_idx] = result
        else:
            state.step_results.append(result)

        # ── Propagate step_type from PlanStep onto StepResult ─────────────────
        # Allows analytics/queries against step_results to filter by type
        # (e.g. "how many tokens did consulting steps save?") without joining
        # back to plan_steps.
        _plan_step = self._find_step(state, step_id)
        if _plan_step is not None:
            result.step_type = _plan_step.step_type

        # ── Flag detection protocol ──────────────────────────────────────────
        # Must run BEFORE _handle_knowledge_gap so flags take precedence.
        # Skipped for automation steps: stdout is command output, not agent text.
        if (
            status in ("complete", "interrupted")
            and outcome
            and (_plan_step is None or _plan_step.step_type != "automation")
        ):
            flag_handled = self._handle_flags(
                outcome=outcome,
                step_id=step_id,
                agent_name=agent_name,
                state=state,
            )
            if flag_handled:
                # Flag inserted a consultation step — skip knowledge gap
                # processing.  The gap (if any) will re-surface via the
                # specialist's output if the consultation can't resolve.
                self._save_state(state)
                return

        # ── Consultation result handling ──────────────────────────────────────
        # When a consulting step completes, check for resolution markers.
        # If a resolution or Tier-2 escalation was handled, skip the
        # knowledge-gap handler to avoid spurious gap processing.
        consultation_handled = False
        if status == "complete" and outcome:
            if _plan_step is not None and _plan_step.step_type == "consulting":
                consultation_handled = self._handle_consultation_result(
                    outcome=outcome,
                    step_id=step_id,
                    agent_name=agent_name,
                    state=state,
                )
                if consultation_handled:
                    self._save_state(state)
                    return

        # ── Knowledge gap protocol ──────────────────────────────────────────
        # Inspect the outcome for a KNOWLEDGE_GAP signal emitted by the agent.
        # Only process when status is "complete" or "interrupted" — a "failed"
        # step is handled by the failure path; "dispatched" has no outcome yet.
        # Skipped for automation steps: stdout is command output, not agent text.
        if (
            status in ("complete", "interrupted")
            and outcome
            and (_plan_step is None or _plan_step.step_type != "automation")
        ):
            self._handle_knowledge_gap(
                outcome=outcome,
                step_id=step_id,
                agent_name=agent_name,
                state=state,
            )

        # ── Bead signal protocol ──────────────────────────────────────────────
        # Extract BEAD_DISCOVERY / BEAD_DECISION / BEAD_WARNING signals from
        # the agent outcome and persist them to the bead store.  Guarded by
        # self._bead_store so this block is a strict no-op when beads are
        # unavailable (older schema, no storage backend, init failure).
        # Skipped for automation steps: command stdout won't contain bead signals.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if (
            status in ("complete", "interrupted")
            and outcome
            and self._bead_store
            and (_plan_step is None or _plan_step.step_type != "automation")
        ):
            try:
                from agent_baton.core.engine.bead_signal import parse_bead_signals
                _bead_count = len(
                    self._bead_store.query(task_id=state.task_id, limit=10000)
                )
                beads = parse_bead_signals(
                    outcome,
                    step_id=step_id,
                    agent_name=agent_name,
                    task_id=state.task_id,
                    bead_count=_bead_count,
                )
                for bead in beads:
                    self._bead_store.write(bead)
                    # Publish to event bus so EventPersistence captures
                    # bead creation in the learn pipeline's event log.
                    if self._bus is not None:
                        from agent_baton.core.events.events import bead_created
                        self._bus.publish(bead_created(
                            task_id=state.task_id,
                            bead_id=bead.bead_id,
                            bead_type=bead.bead_type,
                            agent_name=agent_name,
                            step_id=step_id,
                        ))
                if beads:
                    _log.debug(
                        "Bead store: wrote %d bead(s) from step %s (%s)",
                        len(beads), step_id, agent_name,
                    )
            except Exception as _bead_exc:
                _log.debug("Bead signal extraction failed (non-fatal): %s", _bead_exc)

        # ── Bead feedback protocol (F12 — Quality Scoring) ────────────────────
        # Parse BEAD_FEEDBACK signals from the outcome and apply quality score
        # adjustments to the referenced beads.  This is a tiebreaker in
        # BeadSelector ranking: useful beads surface more, misleading beads decay.
        # Skipped for automation steps: command stdout won't contain bead signals.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if (
            status in ("complete", "interrupted")
            and outcome
            and self._bead_store
            and (_plan_step is None or _plan_step.step_type != "automation")
        ):
            try:
                from agent_baton.core.engine.bead_signal import parse_bead_feedback
                feedback_items = parse_bead_feedback(outcome)
                for _fb_bead_id, _fb_delta in feedback_items:
                    self._bead_store.update_quality_score(_fb_bead_id, _fb_delta)
                if feedback_items:
                    _log.debug(
                        "Bead feedback: applied %d quality adjustment(s) from step %s",
                        len(feedback_items), step_id,
                    )
            except Exception as _fb_exc:
                _log.debug("Bead feedback processing failed (non-fatal): %s", _fb_exc)

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

        # ── Domain event publication ──────────────────────────────────────────
        # Publish step-level domain events to the event bus so that CLI-driven
        # execution (which does not go through TaskWorker) still emits these
        # events to projections, EventPersistence, and the PMO dashboard.
        # TaskWorker publishes the same events in its own path; this call is
        # only reached via the CLI path (mark_dispatched / record_step_result
        # directly), so there is no duplication.
        if status == "dispatched":
            self._publish(evt.step_dispatched(
                task_id=state.task_id,
                step_id=step_id,
                agent_name=agent_name,
            ))
        elif status == "complete":
            self._publish(evt.step_completed(
                task_id=state.task_id,
                step_id=step_id,
                agent_name=agent_name,
                outcome=outcome,
                files_changed=files_changed or [],
                commit_hash=commit_hash,
                duration_seconds=duration_seconds,
                estimated_tokens=estimated_tokens,
            ))
        elif status == "failed":
            self._publish(evt.step_failed(
                task_id=state.task_id,
                step_id=step_id,
                agent_name=agent_name,
                error=error,
                duration_seconds=duration_seconds,
            ))

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
        command: str = "",
        exit_code: int | None = None,
        decision_source: str = "human",
        actor: str = "",
    ) -> None:
        """Record the result of a QA gate check.

        - Creates :class:`GateResult` and appends to state.
        - Emits a ``gate_result`` trace event.
        - If *failed*: sets state status to ``failed``.
        - If *passed*: advances the phase pointer and resets step index.
        - Saves state.

        Args:
            phase_id: Phase whose gate was checked.
            passed: Whether the gate check succeeded.
            output: Command stdout/stderr or reviewer notes.
            command: The shell command that was executed (A6).
            exit_code: Subprocess exit code; ``None`` for manual gates (A6).
            decision_source: How the gate was decided — ``"human"``,
                ``"daemon_auto"``, ``"api"``, or ``"policy_auto"`` (A2).
            actor: Best-available identity string (A2).
        """
        state = self._require_execution("record_gate_result")

        phase_obj = state.current_phase_obj
        gate_type = phase_obj.gate.gate_type if (phase_obj and phase_obj.gate) else "unknown"
        # Derive command from the plan gate if not supplied by caller (A6).
        if not command and phase_obj and phase_obj.gate and phase_obj.gate.command:
            command = phase_obj.gate.command
        # Populate actor from environment when not supplied (A2).
        if not actor:
            actor = _cli_actor()

        gate_result = GateResult(
            phase_id=phase_id,
            gate_type=gate_type,
            passed=passed,
            output=output,
            checked_at=_utcnow(),
            command=command,
            exit_code=exit_code,
            decision_source=decision_source,
            actor=actor,
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

        # ── Compliance audit: record gate result ─────────────────────────────
        self._compliance_gate(state, phase_id, gate_type, passed, output)

        if not passed:
            self._publish(evt.gate_failed(
                task_id=state.task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                output=output,
            ))
            state.status = "gate_failed"
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

    def reset_gate_failed(self, phase_id: int) -> None:
        """Reset a ``gate_failed`` status back to ``gate_pending`` for retry.

        Removes the most recent failed :class:`GateResult` for *phase_id* from
        the state so the engine will re-issue the GATE action on the next call
        to :meth:`next_action`.  The execution status is reset to
        ``"gate_pending"`` so the gate is presented again to the caller.

        Called by ``baton execute retry-gate --phase-id N``.

        Raises:
            RuntimeError: If no active execution is found.
            ValueError: If the execution is not in ``gate_failed`` status, or
                if no failed gate result exists for *phase_id*.
        """
        state = self._require_execution("reset_gate_failed")
        if state.status != "gate_failed":
            raise ValueError(
                f"reset_gate_failed() requires status 'gate_failed', "
                f"got '{state.status}'. "
                "Use 'baton execute retry-gate' only after a gate has failed."
            )
        # Remove the most recent failed gate result for this phase so the gate
        # is treated as pending again.
        before = len(state.gate_results)
        state.gate_results = [
            r for r in state.gate_results
            if not (r.phase_id == phase_id and not r.passed)
        ]
        if len(state.gate_results) == before:
            raise ValueError(
                f"No failed gate result found for phase_id={phase_id}. "
                "Check 'baton execute status' for the correct phase ID."
            )
        state.status = "gate_pending"
        self._save_execution(state)

    def fail_gate(self, phase_id: int) -> None:
        """Explicitly transition ``gate_failed`` to ``failed``.

        Used when the operator decides not to retry a failed gate and wants to
        terminate the execution.  Called by ``baton execute fail --phase-id N``.

        Raises:
            RuntimeError: If no active execution is found.
            ValueError: If the execution is not in ``gate_failed`` status.
        """
        state = self._require_execution("fail_gate")
        if state.status != "gate_failed":
            raise ValueError(
                f"fail_gate() requires status 'gate_failed', got '{state.status}'. "
                "Use 'baton execute fail' only after a gate has failed."
            )
        state.status = "failed"
        self._save_execution(state)

    # ── Compliance report helpers ────────────────────────────────────────────

    @staticmethod
    def _should_generate_compliance_report(state: "ExecutionState") -> bool:
        """Return True when a compliance report should be produced.

        A report is warranted for HIGH or CRITICAL risk plans.  LOW and MEDIUM
        plans are skipped to keep the audit trail lean.
        """
        return state.plan.risk_level.upper() in _HIGH_RISK_LEVELS

    @staticmethod
    def _should_consolidate(state: "ExecutionState") -> bool:
        """Return True when commit consolidation should be attempted.

        Consolidation is warranted when:
        - At least one step recorded a commit hash (there are commits to
          consolidate).
        - No steps failed (a clean execution is required; partial failures
          produce an unpredictable commit set).
        - The plan's git_strategy is not ``"none"`` (consolidation is only
          meaningful for strategies that actually create commits).
        """
        has_commits = any(
            sr.commit_hash
            for sr in state.step_results
            if sr.status == "complete"
        )
        has_failures = bool(state.failed_step_ids)
        git_strategy = getattr(state.plan, "git_strategy", "commit-per-agent")
        return has_commits and not has_failures and git_strategy != "none"

    def _build_compliance_entries(
        self, state: "ExecutionState"
    ) -> list[ComplianceEntry]:
        """Assemble one ``ComplianceEntry`` per completed step.

        Gate results are associated with the agents in each phase using a
        best-effort lookup (mirrors the pattern in ``_build_usage_record``).
        """
        # Build a phase_id → gate-result string map.
        phase_gate: dict[int, str] = {}
        for gate in state.gate_results:
            phase_gate[gate.phase_id] = "PASS" if gate.passed else "FAIL"

        # Build a step_id → phase_id reverse-lookup from the plan.
        step_to_phase: dict[str, int] = {}
        for phase in state.plan.phases:
            for step in phase.steps:
                step_to_phase[step.step_id] = phase.phase_id

        entries: list[ComplianceEntry] = []
        for result in state.step_results:
            phase_id = step_to_phase.get(result.step_id, -1)
            gate_result = phase_gate.get(phase_id, "")
            if result.status != "complete":
                action = "failed"
            elif result.files_changed:
                action = "modified"
            else:
                action = "reviewed"
            entries.append(ComplianceEntry(
                agent_name=result.agent_name,
                action=action,
                files=list(result.files_changed),
                commit_hash=result.commit_hash,
                gate_result=gate_result,
                notes=result.outcome[:200] if result.outcome else "",
            ))
        return entries

    def complete(self) -> str:
        """Finalise execution.

        - Sets state to ``complete``.
        - Completes the trace via :class:`TraceRecorder`.
        - Writes a :class:`TaskUsageRecord` via :class:`UsageLogger`.
        - Generates and writes a retrospective via :class:`RetrospectiveEngine`.
        - Generates and saves a compliance report for HIGH/CRITICAL plans.
        - Returns a human-readable completion summary string.
        """
        state = self._load_execution()
        if state is None:
            task_hint = self._task_id or "(no task_id)"
            return (
                f"No execution state found for task '{task_hint}'. "
                f"Run 'baton execute list' to find existing executions."
            )

        self._publish(evt.task_completing(
            task_id=state.task_id,
            steps_completed=len(state.completed_step_ids),
            steps_failed=len(state.failed_step_ids),
        ))
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

        # Consolidate agent commits onto the feature branch (best-effort).
        # Must run after trace recording but before retrospective so that
        # the consolidation_result is persisted and visible to the PMO API.
        if self._should_consolidate(state):
            try:
                from agent_baton.core.engine.consolidator import CommitConsolidator
                _consolidator = CommitConsolidator(working_directory=self._root.parent)
                _consolidation_result = _consolidator.consolidate(state)
                state.consolidation_result = _consolidation_result
                self._save_execution(state)
                if _consolidation_result.status == "success":
                    from agent_baton.models.events import Event
                    self._publish(Event.create(
                        topic="task.consolidated",
                        task_id=state.task_id,
                        payload={
                            "final_head": _consolidation_result.final_head,
                            "files_changed": len(_consolidation_result.files_changed),
                            "commits_rebased": len(_consolidation_result.rebased_commits),
                        },
                    ))
                elif _consolidation_result.status == "conflict":
                    from agent_baton.models.events import Event
                    self._publish(Event.create(
                        topic="task.consolidation_conflict",
                        task_id=state.task_id,
                        payload={
                            "conflict_step_id": _consolidation_result.conflict_step_id,
                            "conflict_files": _consolidation_result.conflict_files,
                        },
                    ))
            except Exception as _consolidation_exc:
                _log.warning(
                    "Commit consolidation skipped (non-fatal): %s", _consolidation_exc
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

        # Generate compliance report for HIGH/CRITICAL risk plans (best-effort).
        # Stored alongside traces and retrospectives in the execution directory.
        compliance_report_path: Path | None = None
        try:
            if self._should_generate_compliance_report(state):
                _compliance_gen = ComplianceReportGenerator(
                    reports_dir=self._root / "compliance-reports"
                )
                _entries = self._build_compliance_entries(state)
                _preset = _risk_level_to_preset(state.plan.risk_level)
                _report = _compliance_gen.generate(
                    task_id=state.task_id,
                    task_description=state.plan.task_summary,
                    risk_level=state.plan.risk_level,
                    classification=_preset,
                    entries=_entries,
                    usage=usage_record,
                )
                compliance_report_path = _compliance_gen.save(_report)
                _log.debug(
                    "Compliance report written: %s (risk=%s)",
                    compliance_report_path, state.plan.risk_level,
                )
        except Exception as exc:
            _log.warning("Compliance report generation skipped (non-fatal): %s", exc)

        # Close planning beads that agents never close (planning beads are
        # created by the planner itself, not dispatched, so no agent emits a
        # closing signal for them). Without this they leak forever and
        # pollute BeadStore.ready() queries.
        self._close_open_beads_at_terminal(state, succeeded=True)

        # F6 — Memory Decay: archive old closed beads for the finished task.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if self._bead_store is not None:
            try:
                from agent_baton.core.engine.bead_decay import decay_beads
                _archived = decay_beads(
                    self._bead_store,
                    ttl_hours=168,  # 7 days default
                    task_id=state.task_id,
                )
                if _archived:
                    _log.debug(
                        "Bead decay: archived %d bead(s) for task %s",
                        _archived, state.task_id,
                    )
            except Exception as _decay_exc:
                _log.debug("Bead decay skipped (non-fatal): %s", _decay_exc)

        # Trigger improvement loop (best-effort, non-blocking).
        # The loop has built-in guards: circuit breaker, trigger thresholds,
        # and data-volume checks.  It no-ops if there isn't enough new data.
        try:
            from agent_baton.core.improve.loop import ImprovementLoop
            loop = ImprovementLoop(improvements_dir=self._root / "improvements")
            loop.run_cycle()
        except Exception as exc:
            _log.debug("Post-completion improvement cycle skipped: %s", exc)

        # Detect learning signals from the completed execution (best-effort).
        # Writes/updates LearningIssue records in baton.db; auto-applies safe
        # fixes that have crossed their occurrence threshold.
        try:
            from agent_baton.core.learn.engine import LearningEngine
            LearningEngine(team_context_root=self._root).detect(state)
        except Exception as exc:
            _log.debug("Post-completion learning detection skipped: %s", exc)

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
        if compliance_report_path:
            summary_lines.append(f"Compliance report: {compliance_report_path}")
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
        3. Reconciliation: when both SQLite and the file backend are available
           and both return a state, compare per-step statuses and promote any
           step that is more advanced in the secondary backend.  This corrects
           split-brain divergence caused by a prior SQLite write failure that
           left SQLite stale while the file fallback captured the correct state.

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

        # Reconciliation: when we have both a SQLite backend and a file
        # persistence layer, load the alternate source and check whether any
        # step result is more advanced there.  This heals split-brain state
        # that arises when SQLite fails mid-write and the file fallback captures
        # the correct (more-advanced) status while SQLite remains stale.
        if (
            state is not None
            and self._storage is not None
            and self._persistence is not None
            and self._task_id
        ):
            try:
                sqlite_state = self._storage.load_execution(self._task_id)
                file_state = self._persistence.load()
                # Only reconcile when both backends have state for the same task.
                if (
                    sqlite_state is not None
                    and file_state is not None
                    and file_state.task_id == self._task_id
                ):
                    # Primary is SQLite (loaded by _load_execution); secondary
                    # is the file.  Promote any step that is more advanced in
                    # the file backend.
                    state = self._reconcile_states(
                        primary=sqlite_state,
                        secondary=file_state,
                        primary_label="SQLite",
                        secondary_label="file",
                    )
            except Exception as exc:
                _log.warning(
                    "Resume reconciliation check failed for task %r (non-fatal): %s",
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

        self.recover_dispatched_steps()

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
        decision_source: str = "human",
        actor: str = "",
        rationale: str = "",
    ) -> None:
        """Record a human approval decision for a phase.

        Args:
            phase_id: The phase_id requiring approval.
            result: One of ``"approve"``, ``"reject"``,
                ``"approve-with-feedback"``.
            feedback: Free-text feedback (used when result is
                ``"approve-with-feedback"`` to trigger a plan amendment).
            decision_source: How the approval was decided — ``"human"``,
                ``"daemon_auto"``, ``"api"``, or ``"policy_auto"`` (A2).
            actor: Best-available identity string (A2).
            rationale: Optional structured rationale for the decision (A2).
        """
        _VALID_RESULTS = {"approve", "reject", "approve-with-feedback"}
        if result not in _VALID_RESULTS:
            raise ValueError(
                f"Invalid approval result '{result}'. Must be one of: {_VALID_RESULTS}"
            )

        state = self._require_execution("record_approval_result")
        # Populate actor from environment when not supplied (A2).
        if not actor:
            actor = _cli_actor()

        approval = ApprovalResult(
            phase_id=phase_id,
            result=result,
            feedback=feedback,
            decision_source=decision_source,
            actor=actor,
            rationale=rationale,
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
        state = self._require_execution("amend_plan")

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
        state = self._require_execution("record_team_member_result")

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

        # ── Bead signal protocol (team dispatch path) ────────────────────────
        # Mirror the same bead signal extraction done in record_step_result so
        # that BEAD_DISCOVERY / BEAD_DECISION / BEAD_WARNING signals emitted
        # by team-member agents are captured.  Only process when the member
        # reached a terminal success state (complete) — failed/dispatched
        # members have no useful outcome to mine.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if status in ("complete", "interrupted") and outcome and self._bead_store:
            try:
                from agent_baton.core.engine.bead_signal import parse_bead_signals
                _bead_count = len(
                    self._bead_store.query(task_id=state.task_id, limit=10000)
                )
                _member_beads = parse_bead_signals(
                    outcome,
                    step_id=member_id,
                    agent_name=agent_name,
                    task_id=state.task_id,
                    bead_count=_bead_count,
                )
                for _mb in _member_beads:
                    self._bead_store.write(_mb)
                    if self._bus is not None:
                        from agent_baton.core.events.events import bead_created
                        self._bus.publish(bead_created(
                            task_id=state.task_id,
                            bead_id=_mb.bead_id,
                            bead_type=_mb.bead_type,
                            agent_name=agent_name,
                            step_id=member_id,
                        ))
                if _member_beads:
                    _log.debug(
                        "Bead store: wrote %d bead(s) from team member %s (%s)",
                        len(_member_beads), member_id, agent_name,
                    )
            except Exception as _bead_exc:
                _log.debug(
                    "Bead signal extraction failed for team member %s (non-fatal): %s",
                    member_id, _bead_exc,
                )

        # ── Bead feedback protocol (team dispatch path, F12) ─────────────────
        # Apply BEAD_FEEDBACK quality adjustments from team member outcomes.
        if status in ("complete", "interrupted") and outcome and self._bead_store:
            try:
                from agent_baton.core.engine.bead_signal import parse_bead_feedback
                _fb_items = parse_bead_feedback(outcome)
                for _fb_bead_id, _fb_delta in _fb_items:
                    self._bead_store.update_quality_score(_fb_bead_id, _fb_delta)
                if _fb_items:
                    _log.debug(
                        "Bead feedback: applied %d quality adjustment(s) from "
                        "team member %s",
                        len(_fb_items), member_id,
                    )
            except Exception as _fb_exc:
                _log.debug(
                    "Bead feedback processing failed for team member %s (non-fatal): %s",
                    member_id, _fb_exc,
                )

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
        """Return a warning string if cumulative tokens exceed the budget limit.

        Compares the sum of ``estimated_tokens`` across all completed step
        results against the effective limit (explicit ``_token_budget`` cap,
        or the per-tier threshold).  Returns ``None`` when within budget.

        When ``_enforce_token_budget`` is True *and* the budget is exceeded,
        this method also sets ``state.status = "budget_exceeded"`` so that
        :meth:`_determine_action` will stop dispatching new steps.  In-flight
        work (steps already dispatched) is never aborted.

        Tier thresholds (used when no explicit cap is set):
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
        if self._token_budget is not None and self._token_budget > 0:
            limit = self._token_budget
        else:
            limit = thresholds.get(state.plan.budget_tier, 500_000)

        if total > limit:
            warning = (
                f"Token budget exceeded: {total:,} tokens used, "
                f"limit is {limit:,}"
            )
            if self._enforce_token_budget and state.status not in (
                "complete", "failed", "budget_exceeded"
            ):
                state.status = "budget_exceeded"
                _log.warning(
                    "Budget enforced: setting status=budget_exceeded. %s. "
                    "No new dispatches will be issued. "
                    "Use 'baton execute resume-budget' to clear.",
                    warning,
                )
                # Publish domain event so daemon webhooks can fire.
                if self._bus is not None:
                    try:
                        from agent_baton.core.events.events import budget_exceeded as _budget_evt
                        self._bus.publish(_budget_evt(
                            task_id=state.task_id,
                            tokens_used=total,
                            tokens_limit=limit,
                        ))
                    except Exception as _be_exc:
                        _log.debug("budget.exceeded event publish failed (non-fatal): %s", _be_exc)
            return warning
        return None

    def resume_budget(self) -> None:
        """Clear a ``budget_exceeded`` status so execution can continue.

        Resets ``state.status`` back to ``"running"`` and persists the change.
        Intended to be called after the operator has reviewed the situation
        and explicitly chooses to allow further token spend (e.g. after
        adjusting the budget cap or upgrading the budget tier).

        Raises:
            ValueError: If the current execution is not in ``budget_exceeded``
                status.
        """
        state = self._require_execution("resume_budget")
        if state.status != "budget_exceeded":
            raise ValueError(
                f"resume_budget() requires status 'budget_exceeded', "
                f"got '{state.status}'. "
                "Use 'baton execute status' to check current state."
            )
        state.status = "running"
        self._save_execution(state)
        _log.info("Budget status cleared — execution resumed for task %s.", state.task_id)

    # ── Internal helpers ────────────────────────────────────────────────────

    # Event ownership: Engine publishes task-level, phase-level, and
    # step-level events (step.dispatched, step.completed, step.failed).
    # TaskWorker also emits step-level events via its own engine instance;
    # because each path holds a separate engine object, there is no
    # double-publish risk.

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

    def _close_open_beads_at_terminal(
        self, state: ExecutionState, *, succeeded: bool
    ) -> None:
        """Close beads still open when a task reaches a terminal state.

        On success, only planning beads (step_id == "planning") are closed.
        These are created by the planner itself rather than by dispatched
        agents, so nothing else ever closes them and they leak into future
        BeadStore.ready() queries. Agent-level beads are left alone so the
        normal signal flow and decay TTL can govern their lifecycle.

        On failure, every still-open bead for the task is closed. Without
        this, failed tasks leave open beads behind that the decay routine
        (which only archives closed beads) can never clean up.

        Errors here are logged and swallowed — a bookkeeping failure must
        not mask the real completion/failure result returned to the caller.
        """
        if self._bead_store is None:
            return
        try:
            open_beads = self._bead_store.query(
                task_id=state.task_id, status="open", limit=10000,
            )
            if not open_beads:
                return
            if succeeded:
                targets = [b for b in open_beads if b.step_id == "planning"]
                summary = "Plan execution completed"
            else:
                targets = open_beads
                summary = "Task failed before bead was closed"
            for bead in targets:
                self._bead_store.close(bead.bead_id, summary=summary)
        except Exception as exc:
            _log.debug(
                "Terminal bead closure skipped (non-fatal): %s", exc,
            )

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

        # ── Routing mismatch detection ─────────────────────────────────
        # When the plan records a detected_stack, check whether any flavored
        # agent disagrees with it.  E.g. backend-engineer--node on a Python
        # project signals a router misroute that should feed back into
        # the learning pipeline.
        _FLAVOR_LANGUAGE: dict[str, str] = {
            "python": "python",
            "node": "javascript",
            "react": "javascript",
            "dotnet": "csharp",
        }
        detected_stack = state.plan.detected_stack
        if detected_stack:
            primary_lang = detected_stack.split("/")[0]
            for result in state.step_results:
                if "--" not in result.agent_name:
                    continue
                _, flavor = result.agent_name.split("--", 1)
                flavor_lang = _FLAVOR_LANGUAGE.get(flavor)
                if flavor_lang and flavor_lang != primary_lang:
                    correct_flavor = {v: k for k, v in _FLAVOR_LANGUAGE.items()}.get(
                        primary_lang, primary_lang
                    )
                    base_name = result.agent_name.split("--")[0]
                    roster_recs.append(RosterRecommendation(
                        action="prefer",
                        target=f"{base_name}--{correct_flavor}",
                        reason=(
                            f"Routing mismatch: {result.agent_name} was used "
                            f"but project stack is {detected_stack}; "
                            f"prefer {base_name}--{correct_flavor}"
                        ),
                    ))

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

        # feedback_pending — waiting for user answers to feedback questions.
        if state.status == "feedback_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.feedback_questions:
                return self._feedback_action(state, phase_obj)

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

        # gate_failed — a gate ran and failed.  Count how many times this
        # gate has already failed for the current phase.  If the failure count
        # has reached _max_gate_retries, auto-terminate with FAILED so the
        # engine never loops forever in headless / API mode.  Otherwise
        # re-issue the GATE action so the caller can retry manually.
        # Use 'baton execute retry-gate' to reset and re-run, or
        # 'baton execute fail' to permanently fail at any point.
        if state.status == "gate_failed":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                fail_count = sum(
                    1
                    for gr in state.gate_results
                    if gr.phase_id == phase_obj.phase_id and not gr.passed
                )
                if fail_count >= self._max_gate_retries:
                    state.status = "failed"
                    self._save_execution(state)
                    msg = (
                        f"Gate '{phase_obj.gate.gate_type}' for phase "
                        f"{phase_obj.phase_id} failed {fail_count} time(s) "
                        f"(max_gate_retries={self._max_gate_retries}). "
                        "Execution terminated."
                    )
                    return ExecutionAction(
                        action_type=ActionType.FAILED,
                        message=msg,
                        summary=msg,
                    )
                return ExecutionAction(
                    action_type=ActionType.GATE,
                    message=(
                        f"Gate '{phase_obj.gate.gate_type}' for phase "
                        f"{phase_obj.phase_id} failed "
                        f"({fail_count}/{self._max_gate_retries} attempts). "
                        "Retry with 'baton execute retry-gate --phase-id "
                        f"{phase_obj.phase_id}', or permanently fail with "
                        f"'baton execute fail --phase-id {phase_obj.phase_id}'."
                    ),
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )

        # budget_exceeded — token budget was reached; block new dispatches.
        # In-flight steps already running are allowed to complete.  The operator
        # can clear this status with 'baton execute resume-budget' to allow
        # further spend (e.g. after reviewing costs or raising the cap).
        if state.status == "budget_exceeded":
            total = sum(r.estimated_tokens for r in state.step_results)
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message=(
                    f"Task {state.task_id} stopped: token budget exceeded "
                    f"({total:,} tokens used). "
                    "Run 'baton execute resume-budget' to allow further spend, "
                    "or 'baton execute complete' to finalize as-is."
                ),
                summary=(
                    f"Budget exceeded at {total:,} tokens. "
                    "Execution paused — no data lost."
                ),
            )

        # F11 — Conflict Detection: warn when unresolved bead conflicts exist.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        # This is a non-blocking warning — execution continues but the conflict
        # is surfaced as a log warning and a domain event so operators are aware.
        if self._bead_store is not None:
            try:
                if self._bead_store.has_unresolved_conflicts(state.task_id):
                    _log.warning(
                        "Bead conflict: unresolved contradicting beads detected "
                        "for task %s — review with `baton beads list --tag conflict:unresolved`",
                        state.task_id,
                    )
                    if self._bus is not None:
                        from agent_baton.core.events.events import bead_conflict
                        self._bus.publish(bead_conflict(task_id=state.task_id))
            except Exception as _cf_exc:
                _log.debug("Bead conflict check failed (non-fatal): %s", _cf_exc)

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
                self._publish(evt.phase_pre_start(
                    task_id=state.task_id,
                    phase_id=next_phase.phase_id,
                    phase_name=next_phase.name,
                    step_count=len(next_phase.steps),
                ))
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
                # Close any still-open beads (planning + agent-level) so decay
                # can archive them; otherwise failed tasks leak open beads.
                self._close_open_beads_at_terminal(state, succeeded=False)
                msg = f"Step {step.step_id} failed."
                return ExecutionAction(
                    action_type=ActionType.FAILED,
                    message=msg,
                    summary=msg,
                )

        # Find the next dispatchable step — must not be completed, failed,
        # dispatched, or interrupted (interrupted steps have been superseded
        # by a re-dispatch step inserted via the knowledge-gap amend flow).
        # Interactive steps in "interacting" or "interact_dispatched" are
        # treated as in-flight (parallel-safe): other steps keep flowing.
        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        interacting_ids = {
            r.step_id for r in state.step_results
            if r.status in ("interacting", "interact_dispatched")
        }
        occupied = (
            completed
            | state.failed_step_ids
            | dispatched
            | state.interrupted_step_ids
            | interacting_ids
        )

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

        # After exhausting normal dispatch candidates, check whether any step
        # is waiting for human input (interacting).  Return an INTERACT action
        # so the orchestrator can surface it — but only when there is no other
        # pending work being dispatched.
        for result in state.step_results:
            if result.status == "interacting":
                plan_step = self._find_step(state, result.step_id)
                if plan_step is not None:
                    return self._interact_action(plan_step, result, state)

        # Check for interact_dispatched steps that need a continuation DISPATCH.
        for result in state.step_results:
            if result.status == "interact_dispatched":
                plan_step = self._find_step(state, result.step_id)
                if plan_step is not None:
                    return self._dispatch_action(plan_step, state)

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
        # Check approval requirement BEFORE feedback and gate.
        if (phase_obj.approval_required
                and not self._approval_passed_for_phase(state, phase_obj.phase_id)):
            state.status = "approval_pending"
            return self._approval_action(state, phase_obj)

        # Check feedback questions AFTER approval but BEFORE gate.
        if (phase_obj.feedback_questions
                and not self._feedback_resolved_for_phase(state, phase_obj.phase_id)):
            state.status = "feedback_pending"
            return self._feedback_action(state, phase_obj)

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
            self._publish(evt.phase_pre_start(
                task_id=state.task_id,
                phase_id=next_phase.phase_id,
                phase_name=next_phase.name,
                step_count=len(next_phase.steps),
            ))
            self._publish(evt.phase_started(
                task_id=state.task_id,
                phase_id=next_phase.phase_id,
                phase_name=next_phase.name,
                step_count=len(next_phase.steps),
            ))
        return self._determine_action(state)

    def _dispatch_action(self, step: PlanStep, state: ExecutionState) -> ExecutionAction:
        """Build a DISPATCH action for *step*.

        Before building the prompt, runs a policy check against the active
        guardrail preset.  If any ``severity='block'`` rule is violated and
        the step has not already been human-approved, an APPROVAL action is
        returned instead of DISPATCH so the orchestrator can surface the
        violation to the human.

        On a clean policy check (or after human unblock), a compliance audit
        entry is written recording the dispatch event.

        Routing by step_type:
        - ``automation``: skip policy check and LLM; return command directly.
        - ``consulting``: lightweight consultation prompt.
        - ``task``: minimal bespoke-skill prompt.
        - everything else: full delegation prompt (existing behaviour).
        """
        # ── Automation: bypass policy check and LLM dispatch ─────────────────
        # Automation steps are deterministic shell commands — no token cost,
        # no agent model, no prompt.  Return the action immediately so the
        # caller (CLI orchestrator or TaskWorker) can run the command directly.
        if step.step_type == "automation":
            return ExecutionAction(
                action_type=ActionType.DISPATCH,
                step_id=step.step_id,
                step_type="automation",
                command=step.command,
                message=f"Execute automation step {step.step_id}.",
            )

        # ── Policy pre-dispatch check ────────────────────────────────────────
        policy_action = self._check_policy_block(state, step)
        if policy_action is not None:
            return policy_action

        dispatcher = PromptDispatcher()

        # ── Interactive step continuation detection ──────────────────────────
        # When a step is interactive and has an existing result in
        # "interact_dispatched" status, build a continuation prompt that
        # includes the accumulated interaction history instead of a fresh
        # delegation prompt.  Also reset the step status to "dispatched" so
        # _determine_action treats it as in-flight.
        existing_result = state.get_step_result(step.step_id)
        is_continuation = (
            step.interactive
            and existing_result is not None
            and existing_result.status == "interact_dispatched"
        )
        if is_continuation:
            # Pyright cannot narrow through a boolean flag — assert explicitly
            # so the type checker knows existing_result is non-None here.
            assert existing_result is not None, (
                f"is_continuation is True but existing_result is None for "
                f"step '{step.step_id}' — this is a logic error"
            )
            existing_result.status = "dispatched"
            prompt = dispatcher.build_continuation_prompt(
                step,
                existing_result.interaction_history,
                shared_context=state.plan.shared_context,
                task_summary=state.plan.task_summary,
            )
            enforcement = PromptDispatcher.build_path_enforcement(step)
            self._save_execution(state)
            return ExecutionAction(
                action_type=ActionType.DISPATCH,
                message=(
                    f"Dispatch agent '{step.agent_name}' for step {step.step_id} "
                    f"(interactive continuation, turn "
                    f"{len(existing_result.interaction_history) + 1})."
                ),
                agent_name=step.agent_name,
                agent_model=step.model,
                delegation_prompt=prompt,
                step_id=step.step_id,
                step_type=step.step_type,
                command=step.command,
                path_enforcement=enforcement or "",
                interactive=True,
                interact_max_turns=step.max_turns,
            )

        # Find the most recent completed step (different step_id) for handoff.
        handoff = ""
        for result in reversed(state.step_results):
            if result.step_id != step.step_id and result.status == "complete" and result.outcome:
                handoff = result.outcome
                break

        # Append resolved decisions to the handoff so the agent does not re-litigate
        # knowledge gaps that have already been answered.
        handoff = _append_resolved_decisions(handoff, state.resolved_decisions)

        # F3 — Forward Relay: select relevant beads to inject into the prompt.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        prior_beads = []
        if self._bead_store is not None:
            try:
                from agent_baton.core.engine.bead_selector import BeadSelector
                selector = BeadSelector()
                prior_beads = selector.select(
                    self._bead_store,
                    step,
                    state.plan,
                    token_budget=4096,
                    max_beads=5,
                )
                # Increment retrieval_count for each selected bead (F12).
                for _pb in prior_beads:
                    self._bead_store.increment_retrieval_count(_pb.bead_id)
            except Exception as _sel_exc:
                _log.debug("BeadSelector failed (non-fatal): %s", _sel_exc)
                prior_beads = []

        # ── Route prompt builder by step_type ───────────────────────────────
        # consulting → lightweight consultation prompt (no shared context chain)
        # task       → minimal bespoke-skill prompt (no context overhead)
        # everything else → full delegation prompt with knowledge dedup
        if step.step_type == "consulting":
            prompt = dispatcher.build_consultation_prompt(
                step,
                task_summary=state.plan.task_summary,
                prior_beads=prior_beads or None,
            )
        elif step.step_type == "task":
            prompt = dispatcher.build_task_prompt(
                step,
                task_summary=state.plan.task_summary,
            )
        else:
            prompt = dispatcher.build_delegation_prompt(
                step,
                shared_context=state.plan.shared_context,
                handoff_from=handoff,
                task_summary=state.plan.task_summary,
                task_type=state.plan.task_type or "",
                prior_beads=prior_beads or None,
                delivered_knowledge=state.delivered_knowledge,
            )
            # Persist the updated delivered_knowledge map so subsequent
            # dispatches in this run know which docs are already inlined.
            if self._persistence is not None:
                self._persistence.save(state)
        enforcement = PromptDispatcher.build_path_enforcement(step)

        # ── Compliance audit: record dispatch event ──────────────────────────
        preset_name = _risk_level_to_preset(state.plan.risk_level)
        self._compliance_dispatch(
            state,
            step_id=step.step_id,
            agent_name=step.agent_name,
            policy_context=preset_name,
        )

        msg = f"Dispatch agent '{step.agent_name}' for step {step.step_id}."
        if step.interactive:
            msg += f" (interactive, max {step.max_turns} turns)"
        return ExecutionAction(
            action_type=ActionType.DISPATCH,
            message=msg,
            agent_name=step.agent_name,
            agent_model=step.model,
            delegation_prompt=prompt,
            step_id=step.step_id,
            step_type=step.step_type,
            command=step.command,
            path_enforcement=enforcement or "",
            interactive=step.interactive,
            interact_max_turns=step.max_turns if step.interactive else 10,
        )

    def _interact_action(
        self,
        step: PlanStep,
        result: StepResult,
        state: ExecutionState,
    ) -> ExecutionAction:
        """Build an INTERACT action for a step in ``interacting`` status.

        Called by :meth:`_determine_action` when a step has responded but is
        waiting for human input to continue.

        Args:
            step: The :class:`PlanStep` that is interacting.
            result: The :class:`StepResult` with the accumulated history.
            state: Current execution state.

        Returns:
            An :class:`ExecutionAction` with ``action_type=INTERACT``.
        """
        agent_turns = [t for t in result.interaction_history if t.role == "agent"]
        turn_number = len(agent_turns)
        # Latest agent output is the result.outcome field.
        latest_output = result.outcome or ""

        return ExecutionAction(
            action_type=ActionType.INTERACT,
            message=f"Interactive step {step.step_id} awaiting input (turn {turn_number}/{step.max_turns}).",
            interact_prompt=latest_output,
            interact_step_id=step.step_id,
            interact_agent_name=step.agent_name,
            interact_turn=turn_number,
            interact_max_turns=step.max_turns,
        )

    def provide_interact_input(
        self,
        step_id: str,
        input_text: str,
        source: str = "human",
    ) -> None:
        """Record human input for an interactive step and set it to ``interact_dispatched``.

        Called by ``baton execute interact --step-id X --input "..."`` to
        record the human's response to the agent's latest output.  After this
        call the next ``_determine_action()`` will find the step in
        ``interact_dispatched`` status and return a DISPATCH continuation.

        Args:
            step_id: The step ID that is currently in ``interacting`` status.
            input_text: Human-provided text to send as the next turn.
            source: Origin of this input turn.  One of ``"human"`` (default,
                typed by a person), ``"auto-agent"`` (generated by Tier 2
                agent-to-agent dialogue), or ``"webhook"`` (external webhook).

        Raises:
            RuntimeError: If no active execution state exists.
            ValueError: If the step is not in ``interacting`` status.
        """
        state = self._require_execution("provide_interact_input")

        result = state.get_step_result(step_id)
        if result is None or result.status != "interacting":
            raise ValueError(
                f"Step '{step_id}' is not in 'interacting' status "
                f"(current status: {result.status if result else 'not found'})."
            )

        # Compute human turn number.
        human_turns = [t for t in result.interaction_history if t.role == "human"]
        turn_number = len(human_turns) + 1

        result.interaction_history.append(InteractionTurn(
            role="human",
            content=input_text,
            turn_number=turn_number,
            source=source,
        ))
        result.status = "interact_dispatched"
        self._save_execution(state)
        _log.info(
            "Interaction input recorded for step %s (human turn %d, source=%s).",
            step_id, turn_number, source,
        )

    def complete_interaction(self, step_id: str) -> None:
        """Promote an ``interacting`` step to ``complete`` using its last agent output.

        Called by ``baton execute interact --step-id X --done`` when the human
        decides the interaction is finished without the agent signalling
        ``INTERACT_COMPLETE``.

        Args:
            step_id: The step ID that is currently in ``interacting`` status.

        Raises:
            RuntimeError: If no active execution state exists.
            ValueError: If the step is not in ``interacting`` status.
        """
        state = self._require_execution("complete_interaction")

        result = state.get_step_result(step_id)
        if result is None or result.status != "interacting":
            raise ValueError(
                f"Step '{step_id}' is not in 'interacting' status "
                f"(current status: {result.status if result else 'not found'})."
            )

        result.status = "complete"
        result.completed_at = _utcnow()
        if not result.deviations:
            result.deviations = self._extract_deviations(result.outcome)
        self._save_execution(state)
        _log.info("Interaction completed (human --done) for step %s.", step_id)

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

    @staticmethod
    def _feedback_resolved_for_phase(state: ExecutionState, phase_id: int) -> bool:
        """Return True if all feedback questions for *phase_id* have been answered."""
        phase_obj = state.current_phase_obj
        if phase_obj is None:
            return True
        question_ids = {q.question_id for q in phase_obj.feedback_questions}
        answered_ids = {
            r.question_id for r in state.feedback_results
            if r.phase_id == phase_id
        }
        return question_ids <= answered_ids

    def _feedback_action(
        self, state: ExecutionState, phase_obj: PlanPhase,
    ) -> ExecutionAction:
        """Build a FEEDBACK action presenting multiple-choice questions."""
        # Filter to only unanswered questions.
        answered_ids = {
            r.question_id for r in state.feedback_results
            if r.phase_id == phase_obj.phase_id
        }
        unanswered = [
            q for q in phase_obj.feedback_questions
            if q.question_id not in answered_ids
        ]
        context = self._build_feedback_context(state, phase_obj)
        return ExecutionAction(
            action_type=ActionType.FEEDBACK,
            message=(
                f"Phase {phase_obj.phase_id} ({phase_obj.name}) "
                f"has {len(unanswered)} feedback question(s) requiring your input."
            ),
            phase_id=phase_obj.phase_id,
            feedback_questions=unanswered,
            feedback_context=context,
        )

    @staticmethod
    def _build_feedback_context(
        state: ExecutionState, phase_obj: PlanPhase,
    ) -> str:
        """Build a markdown summary of prior work for the feedback reviewer."""
        lines = [
            f"## Phase {phase_obj.phase_id}: {phase_obj.name} — Feedback",
            "",
            "The following work has been completed. Please answer the "
            "questions below to steer the next set of changes.",
            "",
        ]
        phase_step_ids = {s.step_id for s in phase_obj.steps}
        for result in state.step_results:
            if result.step_id in phase_step_ids and result.status == "complete":
                lines.append(f"### Step {result.step_id}: {result.agent_name}")
                if result.outcome:
                    lines.append(result.outcome)
                if result.files_changed:
                    lines.append(f"**Files changed**: {', '.join(result.files_changed)}")
                lines.append("")
        return "\n".join(lines)

    def record_feedback_result(
        self,
        phase_id: int,
        question_id: str,
        chosen_index: int,
    ) -> None:
        """Record a user's answer to a feedback question and amend the plan.

        Looks up the chosen option's mapped agent and prompt template,
        inserts a new step into the *next* phase (or creates a new phase)
        that will be dispatched on the next ``next_action()`` call.

        Args:
            phase_id: The phase presenting the feedback gate.
            question_id: Which question was answered.
            chosen_index: Zero-based index into the question's options list.
        """
        state = self._require_execution("record_feedback_result")

        # Find the question definition on the phase.
        phase_obj = None
        for p in state.plan.phases:
            if p.phase_id == phase_id:
                phase_obj = p
                break
        if phase_obj is None:
            raise ValueError(f"Phase {phase_id} not found in plan.")

        question: FeedbackQuestion | None = None
        for q in phase_obj.feedback_questions:
            if q.question_id == question_id:
                question = q
                break
        if question is None:
            raise ValueError(
                f"Feedback question '{question_id}' not found on phase {phase_id}."
            )

        if chosen_index < 0 or chosen_index >= len(question.options):
            raise ValueError(
                f"chosen_index {chosen_index} out of range for question "
                f"'{question_id}' with {len(question.options)} options."
            )

        chosen_option = question.options[chosen_index]
        agent_name = (
            question.option_agents[chosen_index]
            if chosen_index < len(question.option_agents)
            else "backend-engineer"
        )
        prompt_template = (
            question.option_prompts[chosen_index]
            if chosen_index < len(question.option_prompts)
            else chosen_option
        )
        # Expand {task} placeholder with plan task summary.
        prompt = prompt_template.replace("{task}", state.plan.task_summary)

        # Create a dispatch step via plan amendment.
        new_step = PlanStep(
            step_id="0.1",  # placeholder — renumbered by amend_plan
            agent_name=agent_name,
            task_description=prompt,
        )
        # Use a unique negative placeholder phase_id to avoid collision
        # with existing phase_ids during renumbering.
        new_phase = PlanPhase(
            phase_id=-9999,  # placeholder — renumbered by amend_plan
            name=f"Feedback-Dispatch ({question_id})",
            steps=[new_step],
        )
        # Save the feedback result first so amend_plan sees it.
        fb_result = FeedbackResult(
            phase_id=phase_id,
            question_id=question_id,
            chosen_option=chosen_option,
            chosen_index=chosen_index,
        )
        state.feedback_results.append(fb_result)
        self._save_execution(state)

        amendment = self.amend_plan(
            description=(
                f"Feedback dispatch for question '{question_id}' on phase {phase_id}: "
                f"user chose '{chosen_option}'"
            ),
            new_phases=[new_phase],
            trigger="feedback",
            trigger_phase_id=phase_id,
            feedback=chosen_option,
        )

        # Reload to pick up the amendment's renumbered state (including
        # updated phase_ids on feedback_results).
        state = self._load_execution() or state

        # Find the feedback result in the reloaded state (in-memory
        # fb_result reference is stale after amend_plan reload + save).
        reloaded_fb = next(
            (r for r in state.feedback_results
             if r.question_id == question_id),
            None,
        )
        if reloaded_fb is not None:
            # Record the dispatched step_id.
            if amendment.phases_added:
                for p in state.plan.phases:
                    if p.phase_id in amendment.phases_added and p.steps:
                        reloaded_fb.dispatched_step_id = p.steps[0].step_id
                        break
            elif amendment.steps_added:
                reloaded_fb.dispatched_step_id = amendment.steps_added[0]

        # Find the current phase_id (after renumbering) for the phase
        # that holds the feedback questions.
        current_phase_obj = state.current_phase_obj
        current_pid = current_phase_obj.phase_id if current_phase_obj else phase_id

        # Check if all feedback questions are now resolved.
        if self._feedback_resolved_for_phase(state, current_pid):
            state.status = "running"
        self._save_execution(state)

        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "feedback_result",
                agent_name=None,
                phase=phase_id,
                step=0,
                details={
                    "question_id": question_id,
                    "chosen_option": chosen_option,
                    "chosen_index": chosen_index,
                    "dispatched_step_id": fb_result.dispatched_step_id,
                },
            )

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

            # F3 Forward Relay: inject prior beads into team member prompts
            _team_beads: list = []
            if self._bead_store:
                try:
                    from agent_baton.core.engine.bead_selector import BeadSelector as _TBS
                    _team_beads = _TBS().select(
                        self._bead_store, step, state.plan,
                    )
                except Exception:
                    pass
            prompt = dispatcher.build_team_delegation_prompt(
                step=step,
                member=member,
                shared_context=state.plan.shared_context,
                task_summary=state.plan.task_summary,
                team_overview=team_overview,
                prior_beads=_team_beads or None,
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

        # Update phase_id references in gate, approval, and feedback results.
        for gr in state.gate_results:
            gr.phase_id = old_to_new.get(gr.phase_id, gr.phase_id)
        for ar in state.approval_results:
            ar.phase_id = old_to_new.get(ar.phase_id, ar.phase_id)
        for fr in state.feedback_results:
            fr.phase_id = old_to_new.get(fr.phase_id, fr.phase_id)

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

    def _handle_flags(
        self,
        outcome: str,
        step_id: str,
        agent_name: str,
        state: ExecutionState,
    ) -> bool:
        """Detect a DESIGN_CHOICE: or CONFLICT: flag in *outcome* and handle it.

        When a flag is found the original step is marked ``"interrupted"``, a
        new ``consulting`` step is inserted into the same phase, and a
        :class:`PlanAmendment` is recorded.

        Returns ``True`` if a flag was found and handled, ``False`` otherwise.
        The caller should return early and skip the knowledge-gap handler when
        this method returns ``True``.

        Anti-loop guard: consulting steps are exempt — a consultant that
        cannot resolve emits ``KNOWLEDGE_GAP:`` (Tier 3) rather than
        re-entering Tier 1.
        """
        from agent_baton.core.engine.flags import (
            parse_design_flag,
            parse_conflict_flag,
            _FLAG_ROUTING,
            _FLAG_ROUTING_DEFAULT,
        )

        # Parse flag — design-choice takes precedence over conflict.
        flag = parse_design_flag(outcome, step_id=step_id, agent_name=agent_name)
        if flag is None:
            flag = parse_conflict_flag(outcome, step_id=step_id, agent_name=agent_name)
        if flag is None:
            return False

        # Anti-loop guard: consulting steps must not spawn more consulting steps.
        plan_step = self._find_step(state, step_id)
        if plan_step is None or plan_step.step_type == "consulting":
            return False

        # Attach the full outcome so to_consultation_description() has context.
        flag.partial_outcome = outcome

        # Route to the appropriate specialist.
        specialist = _FLAG_ROUTING.get(flag.flag_type, _FLAG_ROUTING_DEFAULT)

        # Locate the containing phase BEFORE mutating any state.
        containing_phase: PlanPhase | None = None
        for phase in state.plan.phases:
            if any(s.step_id == step_id for s in phase.steps):
                containing_phase = phase
                break

        if containing_phase is None:
            logger.warning(
                "_handle_flags: could not locate phase for step %s — skipping flag insertion",
                step_id,
            )
            return False

        # Mark the current step as interrupted in step_results — only after
        # confirming the phase exists so we don't orphan an interrupted step.
        for sr in state.step_results:
            if sr.step_id == step_id:
                sr.status = "interrupted"
                break

        # Generate the new consulting step id.
        new_step_id = f"{containing_phase.phase_id}.{len(containing_phase.steps) + 1}"

        # Build the consulting PlanStep.
        consulting_step = PlanStep(
            step_id=new_step_id,
            agent_name=specialist,
            task_description=flag.to_consultation_description(),
            step_type="consulting",
            context_files=list(plan_step.context_files),
        )
        containing_phase.steps.append(consulting_step)

        # Record the plan amendment.
        amendment = PlanAmendment(
            amendment_id=f"amend-{len(state.amendments) + 1}",
            trigger=f"flag:{flag.flag_type}",
            trigger_phase_id=containing_phase.phase_id,
            description=(
                f"Consulting {specialist} on {flag.flag_type} in step {step_id}"
            ),
            steps_added=[new_step_id],
            metadata={
                "original_step_id": step_id,
                "consulting_step_id": new_step_id,
            },
        )
        state.amendments.append(amendment)

        logger.info(
            "Flag escalation: %r in step %s (%s) — inserted consulting step %s "
            "for specialist %s (amendment %s)",
            flag.flag_type, step_id, agent_name, new_step_id,
            specialist, amendment.amendment_id,
        )
        return True

    def _handle_consultation_result(
        self,
        outcome: str,
        step_id: str,
        agent_name: str,
        state: ExecutionState,
    ) -> bool:
        """Process the specialist's output from a consulting step.

        Three resolution paths:

        * ``FLAG_RESOLVED: <decision>`` — Tier 1 resolved.  Record a
          :class:`ResolvedDecision`, find the original interrupted step via
          the amendment's ``original_step_id`` metadata, and insert a
          re-dispatch step for that agent.

        * ``ESCALATE_TO_INTERACT:`` — Tier 2 promotion.  Set the consulting
          :class:`PlanStep` to ``interactive=True`` and the :class:`StepResult`
          status to ``"interacting"`` so the next ``_determine_action()`` cycle
          returns an INTERACT action.

        * Neither marker — the consulting step completed normally.  The
          knowledge-gap handler (called next by ``record_step_result``) will
          process any ``KNOWLEDGE_GAP:`` in the output for Tier 3 escalation.

        Returns ``True`` if a resolution or escalation was handled (caller
        should skip knowledge-gap processing), ``False`` otherwise.
        """
        from agent_baton.core.engine.flags import (
            parse_flag_resolution,
            has_escalate_to_interact,
        )

        resolution = parse_flag_resolution(outcome)
        if resolution is not None:
            # ── Tier 1 resolved ────────────────────────────────────────────
            # Find the original interrupted step via the amendment metadata.
            original_step_id = ""
            for amendment in reversed(state.amendments):
                if step_id in amendment.steps_added:
                    original_step_id = amendment.metadata.get("original_step_id", "")
                    break

            # Record the resolved decision so re-dispatch carries the answer.
            # ResolvedDecision reuses gap_description for the flag description
            # (infrastructure reuse; avoids model changes).
            decision = ResolvedDecision(
                gap_description=f"Flag resolution for step {original_step_id or step_id}",
                resolution=resolution,
                step_id=step_id,
                timestamp=_utcnow(),
            )
            state.resolved_decisions.append(decision)

            # Insert re-dispatch step for the original agent.
            if original_step_id:
                original_plan_step = self._find_step(state, original_step_id)
                if original_plan_step is not None:
                    containing_phase: PlanPhase | None = None
                    for phase in state.plan.phases:
                        if any(s.step_id == step_id for s in phase.steps):
                            containing_phase = phase
                            break

                    if containing_phase is not None:
                        new_step_id = (
                            f"{containing_phase.phase_id}.{len(containing_phase.steps) + 1}"
                        )
                        re_dispatch_step = PlanStep(
                            step_id=new_step_id,
                            agent_name=original_plan_step.agent_name,
                            task_description=(
                                original_plan_step.task_description
                                + "\n\nContinue from partial progress."
                            ),
                            model=original_plan_step.model,
                            step_type=original_plan_step.step_type,
                            context_files=list(original_plan_step.context_files),
                            allowed_paths=list(original_plan_step.allowed_paths),
                            blocked_paths=list(original_plan_step.blocked_paths),
                        )
                        containing_phase.steps.append(re_dispatch_step)

                        redispatch_amendment = PlanAmendment(
                            amendment_id=f"amend-{len(state.amendments) + 1}",
                            trigger=f"flag:resolved",
                            trigger_phase_id=containing_phase.phase_id,
                            description=(
                                f"Re-dispatch {original_plan_step.agent_name} after "
                                f"flag resolved by {agent_name} (step {step_id})"
                            ),
                            steps_added=[new_step_id],
                            metadata={
                                "original_step_id": original_step_id,
                                "consulting_step_id": step_id,
                                "resolution": resolution,
                            },
                        )
                        state.amendments.append(redispatch_amendment)

                        logger.info(
                            "Flag resolved by %s (step %s): %r — "
                            "re-dispatching %s as step %s",
                            agent_name, step_id, resolution,
                            original_plan_step.agent_name, new_step_id,
                        )
            return True

        if has_escalate_to_interact(outcome):
            # ── Tier 2 promotion ────────────────────────────────────────────
            # Flip the consulting PlanStep to interactive mode so
            # _determine_action() returns INTERACT on the next cycle.
            consulting_plan_step = self._find_step(state, step_id)
            if consulting_plan_step is not None:
                consulting_plan_step.interactive = True

            # Update the StepResult status to "interacting".
            for sr in state.step_results:
                if sr.step_id == step_id:
                    sr.status = "interacting"
                    sr.interaction_history.append(
                        InteractionTurn(
                            role="agent",
                            content=outcome,
                            source="agent",
                            turn_number=1,
                        )
                    )
                    break

            logger.info(
                "ESCALATE_TO_INTERACT from consulting step %s (%s) — "
                "promoting to Tier 2 agent-to-agent INTERACT",
                step_id, agent_name,
            )
            return True

        # Neither FLAG_RESOLVED nor ESCALATE_TO_INTERACT — consulting step
        # completed normally.  The knowledge-gap handler that runs next will
        # catch any KNOWLEDGE_GAP: for Tier 3 escalation.
        return False

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
            bead_store=self._bead_store,
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


# ---------------------------------------------------------------------------
# Module-level helpers for policy enforcement
# ---------------------------------------------------------------------------

_RISK_TO_PRESET: dict[str, str] = {
    "LOW": "standard_dev",
    "MEDIUM": "standard_dev",
    "HIGH": "regulated",
    "CRITICAL": "regulated",
}


def _risk_level_to_preset(risk_level: str) -> str:
    """Map a plan's risk_level string to a PolicyEngine preset key.

    This is a coarse mapping used at dispatch time so the executor does
    not need to re-run the classifier.  The planner's guardrail_preset
    is not stored on ``MachinePlan``, so risk_level is the best proxy
    available without a schema change.

    Falls back to ``"standard_dev"`` for unknown values.
    """
    return _RISK_TO_PRESET.get(risk_level.upper(), "standard_dev")


def _build_policy_approval_context(
    step: "PlanStep",
    block_violations: list,
    warn_violations: list,
    preset_name: str,
) -> str:
    """Build the approval_context string for a policy-block APPROVAL action.

    The text is shown to the human reviewer so they can make an informed
    decision about whether to override the policy block.
    """
    lines = [
        f"## Policy Block — Step {step.step_id}: {step.agent_name}",
        "",
        f"**Guardrail preset**: `{preset_name}`",
        f"**Agent**: `{step.agent_name}`",
        f"**Task**: {step.task_description}",
        "",
        "### Block-severity violations",
        "",
    ]
    for v in block_violations:
        lines.append(
            f"- **{v.rule.name}** (`{v.rule.rule_type}`): {v.details}"
        )
    if warn_violations:
        lines += ["", "### Warn-severity violations (advisory)", ""]
        for v in warn_violations:
            lines.append(
                f"- **{v.rule.name}** (`{v.rule.rule_type}`): {v.details}"
            )
    lines += [
        "",
        "---",
        "**Approve** to override the block and dispatch the agent.",
        "**Reject** to mark this step as failed.",
    ]
    return "\n".join(lines)
