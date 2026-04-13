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
    """Create an event indicating an agent has been dispatched for a plan step.

    Published by the executor when it spawns a subagent via the Agent tool.
    Consumed by :class:`EventPersistence` (for durable logging) and
    projections (to update :class:`StepView` status to ``"dispatched"``).

    Args:
        task_id: The execution task identifier.
        step_id: The plan step being worked on.
        agent_name: Resolved agent name (may be flavored, e.g.
            ``"backend-engineer--python"``).
        model: The model used for the agent session.
        sequence: Event sequence number.  Defaults to 0, which tells
            the bus to auto-assign the next monotonic value.

    Returns:
        An :class:`Event` with topic ``"step.dispatched"``.
    """
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
    """Create an event indicating an agent finished its step successfully.

    Published by the executor after ``baton execute record --status complete``.
    Consumed by projections (updates :class:`StepView` to ``"completed"``),
    persistence, the mission log writer, and the usage tracker.

    Args:
        task_id: The execution task identifier.
        step_id: The plan step that was completed.
        agent_name: The agent that performed the work.
        outcome: Human-readable summary of what the agent accomplished.
        files_changed: List of file paths modified by the agent.
        commit_hash: Git commit hash for the agent's work, if committed.
        duration_seconds: Wall-clock time the agent spent on the step.
        estimated_tokens: Approximate token usage for the agent session.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"step.completed"``.
    """
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
    """Create an event indicating an agent's step failed.

    Published by the executor after ``baton execute record --status failed``.
    Consumed by projections (updates :class:`StepView` to ``"failed"``)
    and the retrospective system for failure analysis.

    Args:
        task_id: The execution task identifier.
        step_id: The plan step that failed.
        agent_name: The agent that attempted the work.
        error: Description of the failure.
        duration_seconds: Wall-clock time before the failure.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"step.failed"``.
    """
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


# ── Bead memory ─────────────────────────────────────────────────────────────
# Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

def bead_created(
    task_id: str,
    bead_id: str,
    bead_type: str,
    agent_name: str,
    step_id: str = "",
    source: str = "agent-signal",
    sequence: int = 0,
) -> Event:
    """Create an event indicating a new bead has been written to the store.

    Published by the executor after ``BeadStore.write()`` succeeds during
    ``record_step_result()``.  Consumed by projections and observability
    tooling that want to track memory growth without querying SQLite directly.

    Args:
        task_id: The execution task identifier.
        bead_id: The short hash ID of the new bead (e.g. ``"bd-a1b2"``).
        bead_type: Type of bead — ``"discovery"``, ``"decision"``, ``"warning"``,
            ``"outcome"``, or ``"planning"``.
        agent_name: The agent that produced the bead.
        step_id: The plan step where the bead was created.
        source: How the bead was created — ``"agent-signal"``,
            ``"planning-capture"``, ``"retrospective"``, or ``"manual"``.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"bead.created"``.
    """
    return Event.create(
        topic="bead.created",
        task_id=task_id,
        sequence=sequence,
        payload={
            "bead_id": bead_id,
            "bead_type": bead_type,
            "agent_name": agent_name,
            "step_id": step_id,
            "source": source,
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
    """Create an event indicating a QA gate needs to be executed.

    Published by the executor when a phase's gate check is due.  The
    orchestrator reads this event to run the gate command (typically
    ``pytest`` or a custom validation script) and then publishes either
    ``gate.passed`` or ``gate.failed``.

    Args:
        task_id: The execution task identifier.
        phase_id: Numeric ID of the phase requiring the gate.
        gate_type: Type of gate (e.g. ``"test"``, ``"lint"``, ``"review"``).
        command: Shell command to execute for the gate check.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"gate.required"``.
    """
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
    """Create an event indicating a QA gate passed successfully.

    Published by the executor after ``baton execute gate --result pass``.
    Consumed by projections (updates :class:`PhaseView` gate status and
    increments ``TaskView.gates_passed``).

    Args:
        task_id: The execution task identifier.
        phase_id: Numeric ID of the phase whose gate passed.
        gate_type: Type of gate (e.g. ``"test"``).
        output: Captured output from the gate command.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"gate.passed"``.
    """
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
    """Create an event indicating a QA gate failed.

    Published by the executor after ``baton execute gate --result fail``.
    Consumed by projections (sets :class:`PhaseView` gate status to
    ``"failed"`` and increments ``TaskView.gates_failed``).  The
    orchestrator may decide to halt execution or re-dispatch agents
    based on this event.

    Args:
        task_id: The execution task identifier.
        phase_id: Numeric ID of the phase whose gate failed.
        gate_type: Type of gate (e.g. ``"test"``).
        output: Captured output from the gate command showing failures.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"gate.failed"``.
    """
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
    """Create an event indicating execution is blocked on a human decision.

    Published by the executor when an APPROVAL action or a runtime
    decision point is reached.  The orchestrator presents the decision
    context to the user and waits for input before publishing
    ``human.decision_resolved``.

    Projections add the *request_id* to ``TaskView.pending_decisions``,
    which is cleared when the corresponding resolved event arrives.

    Args:
        task_id: The execution task identifier.
        request_id: Unique identifier for this decision request, used
            to correlate with the resolution event.
        decision_type: Category of decision (e.g. ``"approval"``,
            ``"risk_escalation"``, ``"plan_amendment"``).
        summary: Human-readable description of what needs to be decided.
        options: List of available choices (e.g.
            ``["approve", "reject", "approve-with-feedback"]``).
        context_files: File paths relevant to the decision.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"human.decision_needed"``.
    """
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
    """Create an event indicating a human decision has been resolved.

    Published after the user responds to a decision request.  Projections
    remove the *request_id* from ``TaskView.pending_decisions``, allowing
    execution to continue.

    Args:
        task_id: The execution task identifier.
        request_id: Matches the ``request_id`` from the corresponding
            ``human.decision_needed`` event.
        chosen_option: The option selected by the user.
        rationale: Optional explanation for the decision.
        resolved_by: Who resolved it (default ``"human"``).
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"human.decision_resolved"``.
    """
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
    """Create an event indicating an execution task has started.

    Published by ``baton execute start``.  This is typically the first
    event in a task's stream and initialises the :class:`TaskView` with
    status ``"running"``, the risk level, and the expected step count.

    Args:
        task_id: The execution task identifier.
        task_summary: Brief description of the task being executed.
        risk_level: Risk classification (``"LOW"``, ``"MEDIUM"``,
            ``"HIGH"``, ``"CRITICAL"``).
        total_steps: Total number of steps in the execution plan.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"task.started"``.
    """
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
    """Create an event indicating an execution task completed successfully.

    Published by ``baton execute complete``.  This is typically the last
    event in a successful task's stream.  Projections set the
    :class:`TaskView` status to ``"completed"`` and record the total
    elapsed time.

    Args:
        task_id: The execution task identifier.
        steps_completed: Number of steps that completed successfully.
        gates_passed: Number of QA gates that passed.
        elapsed_seconds: Total wall-clock time for the execution.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"task.completed"``.
    """
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
    """Create an event indicating an execution task failed.

    Published when the executor determines the task cannot continue
    (e.g. a critical step failed, or a gate failed with no recovery
    path).  Projections set the :class:`TaskView` status to ``"failed"``.

    Args:
        task_id: The execution task identifier.
        reason: Human-readable explanation of the failure.
        failed_step_id: The step that caused the task to fail, if
            applicable.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"task.failed"``.
    """
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
    """Create an event indicating a plan phase has started.

    Published by the executor when it begins processing a new phase.
    Projections create or update a :class:`PhaseView` with status
    ``"running"`` and track the current phase ID so subsequent step
    events are placed in the correct phase.

    Args:
        task_id: The execution task identifier.
        phase_id: Numeric ID of the phase (1-indexed, from the plan).
        phase_name: Human-readable phase name.
        step_count: Number of steps in this phase.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"phase.started"``.
    """
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
    """Create an event indicating a plan phase completed.

    Published after all steps in the phase are done and the phase's gate
    (if any) has passed.  Projections set the :class:`PhaseView` status
    to ``"completed"``.

    Args:
        task_id: The execution task identifier.
        phase_id: Numeric ID of the completed phase.
        phase_name: Human-readable phase name.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"phase.completed"``.
    """
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
    """Create an event indicating execution is paused for human approval.

    Published when the executor encounters an APPROVAL action in the plan.
    The orchestrator presents the approval context to the user and waits
    for ``baton execute approve`` before proceeding.

    Args:
        task_id: The execution task identifier.
        phase_id: The phase awaiting approval.
        phase_name: Human-readable phase name.
        description: Context for what is being approved.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"approval.required"``.
    """
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
    """Create an event recording a human approval decision.

    Published after ``baton execute approve``.  The *result* determines
    how execution proceeds: ``"approve"`` continues normally,
    ``"reject"`` halts the task, and ``"approve-with-feedback"`` inserts
    a remediation phase before continuing.

    Args:
        task_id: The execution task identifier.
        phase_id: The phase that was approved or rejected.
        result: Decision outcome (``"approve"``, ``"reject"``, or
            ``"approve-with-feedback"``).
        feedback: Optional user feedback, typically used when the result
            is ``"approve-with-feedback"`` to guide remediation.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"approval.resolved"``.
    """
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
    """Create an event indicating the execution plan was amended mid-flight.

    Published after ``baton execute amend``.  Amendments allow the
    orchestrator to add phases or steps to a running plan without
    restarting the entire execution.  This event records the amendment
    for traceability in the event stream.

    Args:
        task_id: The execution task identifier.
        amendment_id: Unique identifier for this amendment.
        description: Human-readable description of what was changed.
        trigger: What caused the amendment (``"manual"``,
            ``"gate_failure"``, ``"approval_feedback"``).
        phases_added: IDs of newly added phases, if any.
        steps_added: IDs of newly added steps, if any.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"plan.amended"``.
    """
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
    """Create an event indicating a team member finished their part of a step.

    Published by ``baton execute team-record`` for team steps where
    multiple agents collaborate on the same step.  Each member records
    their completion individually; the step is considered complete only
    when all members have reported.

    Args:
        task_id: The execution task identifier.
        step_id: The team step being worked on.
        member_id: Unique identifier for this team member within the step.
        agent_name: The agent that performed the member's work.
        outcome: Summary of what this member accomplished.
        sequence: Event sequence number (0 = auto-assign).

    Returns:
        An :class:`Event` with topic ``"team.member_completed"``.
    """
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
