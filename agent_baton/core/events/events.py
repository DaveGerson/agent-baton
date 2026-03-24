"""Domain event types for the execution runtime.

Each function creates a properly-typed :class:`Event` with the correct topic
and payload structure.  Using factory functions (rather than subclasses) keeps
the serialisation format uniform — every event is just an ``Event`` with a
different ``topic`` string.
"""
from __future__ import annotations

from agent_baton.models.events import Event


# ── Step lifecycle ──────────────────────────────────────────────────────────

def step_dispatched(
    task_id: str,
    step_id: str,
    agent_name: str,
    model: str = "sonnet",
    sequence: int = 0,
) -> Event:
    """An agent has been dispatched for a plan step."""
    return Event.create(
        topic="step.dispatched",
        task_id=task_id,
        sequence=sequence,
        payload={
            "step_id": step_id,
            "agent_name": agent_name,
            "model": model,
        },
    )


def step_completed(
    task_id: str,
    step_id: str,
    agent_name: str,
    outcome: str = "",
    files_changed: list[str] | None = None,
    commit_hash: str = "",
    duration_seconds: float = 0.0,
    estimated_tokens: int = 0,
    sequence: int = 0,
) -> Event:
    """An agent finished its step successfully."""
    return Event.create(
        topic="step.completed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "step_id": step_id,
            "agent_name": agent_name,
            "outcome": outcome,
            "files_changed": files_changed or [],
            "commit_hash": commit_hash,
            "duration_seconds": duration_seconds,
            "estimated_tokens": estimated_tokens,
        },
    )


def step_failed(
    task_id: str,
    step_id: str,
    agent_name: str,
    error: str = "",
    duration_seconds: float = 0.0,
    sequence: int = 0,
) -> Event:
    """An agent's step failed."""
    return Event.create(
        topic="step.failed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "step_id": step_id,
            "agent_name": agent_name,
            "error": error,
            "duration_seconds": duration_seconds,
        },
    )


# ── Gates ───────────────────────────────────────────────────────────────────

def gate_required(
    task_id: str,
    phase_id: int,
    gate_type: str,
    command: str = "",
    sequence: int = 0,
) -> Event:
    """A QA gate needs to be run."""
    return Event.create(
        topic="gate.required",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "gate_type": gate_type,
            "command": command,
        },
    )


def gate_passed(
    task_id: str,
    phase_id: int,
    gate_type: str,
    output: str = "",
    sequence: int = 0,
) -> Event:
    """A QA gate passed."""
    return Event.create(
        topic="gate.passed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "gate_type": gate_type,
            "output": output,
        },
    )


def gate_failed(
    task_id: str,
    phase_id: int,
    gate_type: str,
    output: str = "",
    sequence: int = 0,
) -> Event:
    """A QA gate failed."""
    return Event.create(
        topic="gate.failed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "gate_type": gate_type,
            "output": output,
        },
    )


# ── Human decisions ─────────────────────────────────────────────────────────

def human_decision_needed(
    task_id: str,
    request_id: str,
    decision_type: str,
    summary: str,
    options: list[str] | None = None,
    context_files: list[str] | None = None,
    sequence: int = 0,
) -> Event:
    """The execution needs a human decision to proceed."""
    return Event.create(
        topic="human.decision_needed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "request_id": request_id,
            "decision_type": decision_type,
            "summary": summary,
            "options": options or [],
            "context_files": context_files or [],
        },
    )


def human_decision_resolved(
    task_id: str,
    request_id: str,
    chosen_option: str,
    rationale: str = "",
    resolved_by: str = "human",
    sequence: int = 0,
) -> Event:
    """A human decision has been resolved."""
    return Event.create(
        topic="human.decision_resolved",
        task_id=task_id,
        sequence=sequence,
        payload={
            "request_id": request_id,
            "chosen_option": chosen_option,
            "rationale": rationale,
            "resolved_by": resolved_by,
        },
    )


# ── Task lifecycle ──────────────────────────────────────────────────────────

def task_started(
    task_id: str,
    task_summary: str = "",
    risk_level: str = "LOW",
    total_steps: int = 0,
    sequence: int = 0,
) -> Event:
    """An execution task has started."""
    return Event.create(
        topic="task.started",
        task_id=task_id,
        sequence=sequence,
        payload={
            "task_summary": task_summary,
            "risk_level": risk_level,
            "total_steps": total_steps,
        },
    )


def task_completed(
    task_id: str,
    steps_completed: int = 0,
    gates_passed: int = 0,
    elapsed_seconds: float = 0.0,
    sequence: int = 0,
) -> Event:
    """An execution task completed successfully."""
    return Event.create(
        topic="task.completed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "steps_completed": steps_completed,
            "gates_passed": gates_passed,
            "elapsed_seconds": elapsed_seconds,
        },
    )


def task_failed(
    task_id: str,
    reason: str = "",
    failed_step_id: str = "",
    sequence: int = 0,
) -> Event:
    """An execution task failed."""
    return Event.create(
        topic="task.failed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "reason": reason,
            "failed_step_id": failed_step_id,
        },
    )


# ── Phase lifecycle ─────────────────────────────────────────────────────────

def phase_started(
    task_id: str,
    phase_id: int,
    phase_name: str = "",
    step_count: int = 0,
    sequence: int = 0,
) -> Event:
    """A plan phase has started."""
    return Event.create(
        topic="phase.started",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "phase_name": phase_name,
            "step_count": step_count,
        },
    )


def phase_completed(
    task_id: str,
    phase_id: int,
    phase_name: str = "",
    sequence: int = 0,
) -> Event:
    """A plan phase completed."""
    return Event.create(
        topic="phase.completed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "phase_name": phase_name,
        },
    )


# ── Approvals ──────────────────────────────────────────────────────────────

def approval_required(
    task_id: str,
    phase_id: int,
    phase_name: str = "",
    description: str = "",
    sequence: int = 0,
) -> Event:
    """Execution paused for human approval."""
    return Event.create(
        topic="approval.required",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "phase_name": phase_name,
            "description": description,
        },
    )


def approval_resolved(
    task_id: str,
    phase_id: int,
    result: str,
    feedback: str = "",
    sequence: int = 0,
) -> Event:
    """Human approval decision recorded."""
    return Event.create(
        topic="approval.resolved",
        task_id=task_id,
        sequence=sequence,
        payload={
            "phase_id": phase_id,
            "result": result,
            "feedback": feedback,
        },
    )


# ── Plan amendments ────────────────────────────────────────────────────────

def plan_amended(
    task_id: str,
    amendment_id: str,
    description: str,
    trigger: str = "manual",
    phases_added: list[int] | None = None,
    steps_added: list[str] | None = None,
    sequence: int = 0,
) -> Event:
    """Plan was amended during execution."""
    return Event.create(
        topic="plan.amended",
        task_id=task_id,
        sequence=sequence,
        payload={
            "amendment_id": amendment_id,
            "description": description,
            "trigger": trigger,
            "phases_added": phases_added or [],
            "steps_added": steps_added or [],
        },
    )


# ── Team steps ─────────────────────────────────────────────────────────────

def team_member_completed(
    task_id: str,
    step_id: str,
    member_id: str,
    agent_name: str,
    outcome: str = "",
    sequence: int = 0,
) -> Event:
    """A team member finished their work."""
    return Event.create(
        topic="team.member_completed",
        task_id=task_id,
        sequence=sequence,
        payload={
            "step_id": step_id,
            "member_id": member_id,
            "agent_name": agent_name,
            "outcome": outcome,
        },
    )
