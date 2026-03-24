"""Materialized views derived from event streams.

Projections consume a list of :class:`Event` objects (from the bus or
from disk replay) and produce summary structures useful for dashboards,
status queries, and decision-making.

These are read-only views — they never mutate events or persist state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.events import Event


@dataclass
class StepView:
    """Current state of a single step, derived from events."""

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
    """Current state of a phase, derived from events."""

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
    """Complete materialized view of a task's execution, derived from events."""

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
    """Build a :class:`TaskView` from a sequence of events.

    If *task_id* is empty, it is inferred from the first event.
    Events are processed in order; later events overwrite earlier state.
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
    """Apply a single event to the task view.  Mutates *view* in place."""
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

    If the step already exists in a phase, update in place.
    Otherwise insert into *current_phase_id*.
    """
    for phase in view.phases.values():
        if sv.step_id in phase.steps:
            phase.steps[sv.step_id] = sv
            return
    # New step — insert into the current active phase.
    phase = view.phases.setdefault(current_phase_id, PhaseView(phase_id=current_phase_id))
    phase.steps[sv.step_id] = sv
