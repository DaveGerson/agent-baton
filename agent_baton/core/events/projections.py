"""Materialized views derived from event streams.

Projections consume a list of :class:`Event` objects (from the bus history
or from disk replay via :class:`EventPersistence`) and produce summary
dataclasses useful for dashboards, CLI status output, and runtime
decision-making.

The projection model follows the CQRS / event-sourcing pattern:

1. The event stream is the source of truth (append-only).
2. Projections are **derived, read-only views** -- they fold events into
   convenient summary structures but never mutate events or persist state.
3. Projections can be rebuilt from scratch at any time by replaying the
   full event stream, making them crash-safe by construction.

The primary entry point is :func:`project_task_view`, which takes a list
of events and returns a :class:`TaskView` containing nested
:class:`PhaseView` and :class:`StepView` structures.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.events import Event


@dataclass
class StepView:
    """Materialized view of a single step's current state.

    Built by folding ``step.dispatched``, ``step.completed``, and
    ``step.failed`` events.  The ``status`` field progresses through:
    ``pending`` -> ``dispatched`` -> ``completed`` | ``failed``.

    Attributes:
        step_id: Unique identifier for this step within the plan.
        agent_name: The agent assigned to this step (may be flavored).
        status: Current lifecycle state.
        dispatched_at: ISO timestamp when the agent was spawned.
        completed_at: ISO timestamp when the step finished.
        duration_seconds: Wall-clock time for the step.
        outcome: Summary of what the agent accomplished (on success).
        error: Error description (on failure).
        files_changed: Paths modified by the agent.
        commit_hash: Git commit hash for the agent's work.
    """

    step_id: str
    agent_name: str = ""
    status: str = "pending"  # pending, dispatched, completed, failed
    dispatched_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    outcome: str = ""
    error: str = ""
    files_changed: list[str] = field(default_factory=list)
    commit_hash: str = ""


@dataclass
class PhaseView:
    """Materialized view of a phase's current state.

    Built by folding ``phase.started``, ``phase.completed``,
    ``gate.required``, ``gate.passed``, and ``gate.failed`` events.
    Contains a dictionary of nested :class:`StepView` instances for all
    steps that belong to this phase.

    The ``status`` field progresses through:
    ``pending`` -> ``running`` -> ``gate_pending`` -> ``completed`` | ``failed``.

    Attributes:
        phase_id: Numeric phase identifier (1-indexed, from the plan).
        phase_name: Human-readable phase name.
        status: Current lifecycle state of the phase.
        started_at: ISO timestamp when the phase began.
        completed_at: ISO timestamp when the phase finished.
        steps: Dictionary mapping step IDs to their :class:`StepView`.
        gate_status: Status of the phase's QA gate (empty string when
            no gate has been requested yet).
        gate_output: Captured output from the gate command.
    """

    phase_id: int
    phase_name: str = ""
    status: str = "pending"  # pending, running, gate_pending, completed, failed
    started_at: str = ""
    completed_at: str = ""
    steps: dict[str, StepView] = field(default_factory=dict)
    gate_status: str = ""  # "", "required", "passed", "failed"
    gate_output: str = ""


@dataclass
class TaskView:
    """Complete materialized view of a task's execution state.

    The top-level projection produced by :func:`project_task_view`.
    Contains aggregate counters derived from nested phase/step views,
    plus task-level metadata from ``task.started`` and ``task.completed``
    events.

    Used by:
        - The ``baton status`` CLI command to display execution progress.
        - The dashboard endpoint to render real-time task status.
        - The executor to determine what action to take next.

    Attributes:
        task_id: The execution task identifier.
        status: Task lifecycle state (``"unknown"``, ``"running"``,
            ``"completed"``, ``"failed"``).
        started_at: ISO timestamp from the ``task.started`` event.
        completed_at: ISO timestamp from the ``task.completed`` event.
        risk_level: Risk classification assigned at plan time.
        total_steps: Expected step count from the plan.
        steps_completed: Count of steps with ``"completed"`` status.
        steps_failed: Count of steps with ``"failed"`` status.
        steps_dispatched: Count of steps with ``"dispatched"`` status
            (in-flight, not yet finished).
        gates_passed: Number of QA gates that passed.
        gates_failed: Number of QA gates that failed.
        elapsed_seconds: Total wall-clock time (from ``task.completed``).
        phases: Dictionary mapping phase IDs to :class:`PhaseView`.
        pending_decisions: List of unresolved human decision request IDs.
        last_event_seq: Highest event sequence number seen, useful for
            incremental replay.
    """

    task_id: str
    status: str = "unknown"  # started, running, completed, failed
    started_at: str = ""
    completed_at: str = ""
    risk_level: str = ""
    total_steps: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    steps_dispatched: int = 0
    gates_passed: int = 0
    gates_failed: int = 0
    elapsed_seconds: float = 0.0
    phases: dict[int, PhaseView] = field(default_factory=dict)
    pending_decisions: list[str] = field(default_factory=list)
    last_event_seq: int = 0


def project_task_view(events: list[Event], task_id: str = "") -> TaskView:
    """Build a :class:`TaskView` by folding a sequence of events.

    This is the primary projection function.  It processes events in order,
    applying each one to mutate the view state.  After all events are
    processed, aggregate counters (``steps_completed``, ``steps_failed``,
    ``steps_dispatched``) are derived by iterating over the nested phase
    and step views.

    The function is idempotent: calling it with the same events always
    produces the same view.  It can be used for both full replays (from
    the beginning of a task) and incremental updates (by passing only
    new events to a view that was previously built).

    Args:
        events: Ordered list of events to process.  Events whose
            ``task_id`` does not match *task_id* are silently skipped.
        task_id: The task to build the view for.  When empty, the task
            ID is inferred from the first event in the list.

    Returns:
        A fully populated :class:`TaskView` reflecting the cumulative
        state of all processed events.
    """
    if not events:
        return TaskView(task_id=task_id or "unknown")

    tid = task_id or events[0].task_id
    view = TaskView(task_id=tid)
    # Track the most recently started phase so steps are placed correctly.
    current_phase_id: int = 0

    for event in events:
        if event.task_id != tid:
            continue
        view.last_event_seq = max(view.last_event_seq, event.sequence)
        if event.topic == "phase.started":
            current_phase_id = event.payload.get("phase_id", 0)
        _apply_event(view, event, current_phase_id)

    # Derive aggregate counts.
    for phase in view.phases.values():
        for step in phase.steps.values():
            if step.status == "completed":
                view.steps_completed += 1
            elif step.status == "failed":
                view.steps_failed += 1
            elif step.status == "dispatched":
                view.steps_dispatched += 1

    return view


def _apply_event(view: TaskView, event: Event, current_phase_id: int = 0) -> None:
    """Apply a single event to the task view.  Mutates *view* in place.

    Dispatches on ``event.topic`` to update the appropriate part of the
    view.  Handles all domain event topics: task lifecycle, phase
    lifecycle, step lifecycle, gates, human decisions, and approvals.

    Steps are placed into the phase identified by *current_phase_id*
    (tracked by the caller based on ``phase.started`` events).  If a
    step already exists in a different phase (from a prior dispatch),
    it is updated in place rather than duplicated.

    Args:
        view: The task view to mutate.
        event: The event to apply.
        current_phase_id: The phase ID from the most recent
            ``phase.started`` event, used to associate new steps
            with the correct phase.
    """
    topic = event.topic
    p = event.payload

    if topic == "task.started":
        view.status = "running"
        view.started_at = event.timestamp
        view.risk_level = p.get("risk_level", "")
        view.total_steps = p.get("total_steps", 0)

    elif topic == "task.completed":
        view.status = "completed"
        view.completed_at = event.timestamp
        view.elapsed_seconds = p.get("elapsed_seconds", 0.0)

    elif topic == "task.failed":
        view.status = "failed"

    elif topic == "phase.started":
        phase_id = p.get("phase_id", 0)
        phase = view.phases.setdefault(phase_id, PhaseView(phase_id=phase_id))
        phase.status = "running"
        phase.started_at = event.timestamp
        phase.phase_name = p.get("phase_name", "")

    elif topic == "phase.completed":
        phase_id = p.get("phase_id", 0)
        phase = view.phases.setdefault(phase_id, PhaseView(phase_id=phase_id))
        phase.status = "completed"
        phase.completed_at = event.timestamp

    elif topic == "step.dispatched":
        step_id = p.get("step_id", "")
        sv = StepView(
            step_id=step_id,
            agent_name=p.get("agent_name", ""),
            status="dispatched",
            dispatched_at=event.timestamp,
        )
        # Place step in the current phase.
        _upsert_step(view, sv, current_phase_id)

    elif topic == "step.completed":
        step_id = p.get("step_id", "")
        sv = _find_step(view, step_id) or StepView(step_id=step_id)
        sv.status = "completed"
        sv.completed_at = event.timestamp
        sv.agent_name = p.get("agent_name", sv.agent_name)
        sv.outcome = p.get("outcome", "")
        sv.files_changed = p.get("files_changed", [])
        sv.commit_hash = p.get("commit_hash", "")
        sv.duration_seconds = p.get("duration_seconds", 0.0)
        _upsert_step(view, sv, current_phase_id)

    elif topic == "step.failed":
        step_id = p.get("step_id", "")
        sv = _find_step(view, step_id) or StepView(step_id=step_id)
        sv.status = "failed"
        sv.agent_name = p.get("agent_name", sv.agent_name)
        sv.error = p.get("error", "")
        sv.duration_seconds = p.get("duration_seconds", 0.0)
        _upsert_step(view, sv, current_phase_id)

    elif topic == "gate.required":
        phase_id = p.get("phase_id", 0)
        phase = view.phases.setdefault(phase_id, PhaseView(phase_id=phase_id))
        phase.gate_status = "required"
        phase.status = "gate_pending"

    elif topic == "gate.passed":
        phase_id = p.get("phase_id", 0)
        phase = view.phases.setdefault(phase_id, PhaseView(phase_id=phase_id))
        phase.gate_status = "passed"
        phase.gate_output = p.get("output", "")
        view.gates_passed += 1

    elif topic == "gate.failed":
        phase_id = p.get("phase_id", 0)
        phase = view.phases.setdefault(phase_id, PhaseView(phase_id=phase_id))
        phase.gate_status = "failed"
        phase.gate_output = p.get("output", "")
        view.gates_failed += 1

    elif topic == "human.decision_needed":
        request_id = p.get("request_id", "")
        if request_id and request_id not in view.pending_decisions:
            view.pending_decisions.append(request_id)

    elif topic == "human.decision_resolved":
        request_id = p.get("request_id", "")
        if request_id in view.pending_decisions:
            view.pending_decisions.remove(request_id)


def _find_step(view: TaskView, step_id: str) -> StepView | None:
    """Find a StepView by step_id across all phases."""
    for phase in view.phases.values():
        if step_id in phase.steps:
            return phase.steps[step_id]
    return None


def _upsert_step(view: TaskView, sv: StepView, current_phase_id: int = 0) -> None:
    """Insert or update a step in the task view.

    Searches all phases for an existing step with the same ``step_id``.
    If found, the step is replaced in its current phase (preserving
    phase association even if the current phase has changed).  If not
    found, the step is inserted into *current_phase_id*, creating the
    phase if necessary.

    This approach ensures that a step's ``dispatched`` and ``completed``
    events are always correlated in the same phase, even if they arrive
    across phase boundaries.

    Args:
        view: The task view to mutate.
        sv: The step view to insert or update.
        current_phase_id: Phase to use for new steps.
    """
    for phase in view.phases.values():
        if sv.step_id in phase.steps:
            phase.steps[sv.step_id] = sv
            return
    # New step — insert into the current active phase.
    phase = view.phases.setdefault(current_phase_id, PhaseView(phase_id=current_phase_id))
    phase.steps[sv.step_id] = sv
