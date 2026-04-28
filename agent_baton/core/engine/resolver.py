"""ActionResolver — stateless evaluator that maps ExecutionState to a
ResolverDecision intent.

The resolver does NOT mutate state, does NOT perform I/O, does NOT publish
events, and does NOT construct fully-built ExecutionAction objects.  It
inspects state and returns a small frozen :class:`ResolverDecision`
describing what should happen next.  The caller
(``ExecutionEngine._drive_resolver_loop``) is responsible for state
mutation, persistence, event publication, and constructing the final
:class:`ExecutionAction`.

Importing rules:
  - This module imports from ``agent_baton.models.*`` and stdlib only.
  - This module MUST NOT import from ``agent_baton.core.engine.executor``.

Step 2.2C scope (005b refactor):
  - ``ResolverDecision`` + ``DecisionKind`` are introduced here.
  - ``ActionResolver.determine_next`` translates each branch of
    ``ExecutionEngine._determine_action`` into a ``ResolverDecision``.
  - Engine wire-up (``_drive_resolver_loop``) is Step 2.3, not done here.

The resolver MUST NOT recurse on transitive phase advance.  When all
steps in a phase are complete and the phase is ready to advance, the
resolver returns ``EMPTY_PHASE_ADVANCE`` / ``PHASE_ADVANCE_OK`` and
trusts the engine to mutate state and re-invoke ``determine_next``.
See :file:`docs/internal/005b-phase2-design.md` §2.7.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from agent_baton.models.execution import (
    ExecutionState,
    PlanStep,
)


# Pure helpers shared with ExecutionEngine live in _executor_helpers.
# Imported with aliases to preserve the resolver's _-prefixed call-site names.
from agent_baton.core.engine._executor_helpers import (
    find_step as _find_step,
    effective_timeout as _effective_timeout,
    gate_passed_for_phase as _gate_passed,
    approval_passed_for_phase as _approval_passed,
    feedback_resolved_for_phase as _feedback_resolved,
)


def _elapsed_seconds(started_at: str) -> float:
    """Return elapsed wall-clock seconds since *started_at* (ISO string).

    Returns 0.0 when *started_at* is empty or unparseable, mirroring the
    existing helper in ``executor.py:201``.
    """
    if not started_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.now(tz=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0.0, (now - start).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# === DecisionKind / ResolverDecision ========================================


class DecisionKind(Enum):
    """The kind of intent the resolver is signalling to the engine.

    See ``docs/internal/005b-phase2-design.md`` §2.6 for the canonical list
    and Q3 for the mapping back to :class:`ActionType`.
    """

    TERMINAL_COMPLETE = "terminal_complete"
    TERMINAL_FAILED = "terminal_failed"
    APPROVAL_PENDING = "approval_pending"
    FEEDBACK_PENDING = "feedback_pending"
    GATE_PENDING = "gate_pending"
    GATE_FAILED = "gate_failed"
    PAUSED_TAKEOVER = "paused_takeover"
    BUDGET_EXCEEDED = "budget_exceeded"
    NO_PHASES_LEFT = "no_phases_left"
    EMPTY_PHASE_GATE = "empty_phase_gate"
    EMPTY_PHASE_ADVANCE = "empty_phase_advance"
    STEP_FAILED_IN_PHASE = "step_failed_in_phase"
    DISPATCH = "dispatch"
    TEAM_DISPATCH = "team_dispatch"
    INTERACT = "interact"
    INTERACT_CONTINUE = "interact_continue"
    TIMEOUT = "timeout"
    WAIT = "wait"
    PHASE_NEEDS_APPROVAL = "phase_needs_approval"
    PHASE_NEEDS_FEEDBACK = "phase_needs_feedback"
    PHASE_NEEDS_GATE = "phase_needs_gate"
    PHASE_ADVANCE_OK = "phase_advance_ok"


@dataclass(frozen=True)
class ResolverDecision:
    """A small, immutable intent object.

    The resolver returns these.  The engine translates them into final
    :class:`ExecutionAction` objects, applies state mutations, runs heavy
    builders, publishes events, and persists.

    Attributes:
        kind: Which decision branch was taken.
        phase_id: Phase context for the decision (or ``None`` when N/A).
        step_id: Step context (DISPATCH, INTERACT, TIMEOUT, WAIT-on-step).
        failed_step_ids: IDs of failed steps in the current phase
            (``STEP_FAILED_IN_PHASE`` only).
        fail_count: Gate fail-count for the current phase
            (``GATE_FAILED`` / ``TERMINAL_FAILED`` after retries).
        message: Human-readable message for the engine to forward verbatim
            into the final ExecutionAction.message.
        summary: Short summary forwarded into ExecutionAction.summary.
    """

    kind: DecisionKind
    phase_id: int | None = None
    step_id: str | None = None
    failed_step_ids: tuple[str, ...] = ()
    fail_count: int = 0
    message: str = ""
    summary: str = ""


# === ActionResolver =========================================================


class ActionResolver:
    """Stateless evaluator: ``ExecutionState`` -> ``ResolverDecision``.

    No state held between calls.  ``max_gate_retries`` is the only datum the
    resolver needs — it compares gate fail counts against the cap to choose
    between ``GATE_FAILED`` (retry) and ``TERMINAL_FAILED`` (give up).
    See design §3.1.
    """

    def __init__(self, *, max_gate_retries: int = 3) -> None:
        self._max_gate_retries = max_gate_retries

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def determine_next(self, state: ExecutionState) -> ResolverDecision:
        """Compute the next ``ResolverDecision`` for *state*.

        Translates each branch of the legacy ``_determine_action`` method
        (``executor.py:4866-5322``) into a small intent object.
        """
        # ── Terminal states: report immediately ─────────────────────────────
        if state.status == "complete":
            msg = f"Task {state.task_id} is already complete."
            return ResolverDecision(
                kind=DecisionKind.TERMINAL_COMPLETE,
                message=msg,
                summary=f"Task {state.task_id} completed.",
            )

        if state.status == "failed":
            rejected = next(
                (
                    a
                    for a in reversed(state.approval_results)
                    if a.result == "reject"
                ),
                None,
            )
            if rejected is not None:
                msg = (
                    f"Phase {rejected.phase_id} approval was rejected. "
                    "To continue: amend the plan with 'baton execute amend', "
                    "or finalize with 'baton execute complete'."
                )
            else:
                failed_ids = sorted(state.failed_step_ids)
                joined = ", ".join(failed_ids) or "gate"
                msg = f"Execution failed. Failed step(s): {joined}"
            return ResolverDecision(
                kind=DecisionKind.TERMINAL_FAILED,
                message=msg,
                summary=msg,
            )

        # ── approval_pending: waiting on human approval ────────────────────
        if state.status == "approval_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.approval_required:
                return ResolverDecision(
                    kind=DecisionKind.APPROVAL_PENDING,
                    phase_id=phase_obj.phase_id,
                )

        # ── feedback_pending: waiting on user answers ──────────────────────
        if state.status == "feedback_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.feedback_questions:
                return ResolverDecision(
                    kind=DecisionKind.FEEDBACK_PENDING,
                    phase_id=phase_obj.phase_id,
                )

        # ── gate_pending: gate requested, result not recorded yet ──────────
        if state.status == "gate_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                return ResolverDecision(
                    kind=DecisionKind.GATE_PENDING,
                    phase_id=phase_obj.phase_id,
                )

        # ── gate_failed: retry-or-terminate decision ───────────────────────
        if state.status == "gate_failed":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                fail_count = sum(
                    1
                    for gr in state.gate_results
                    if gr.phase_id == phase_obj.phase_id and not gr.passed
                )
                if fail_count >= self._max_gate_retries:
                    msg = (
                        f"Gate '{phase_obj.gate.gate_type}' for phase "
                        f"{phase_obj.phase_id} failed {fail_count} time(s) "
                        f"(max_gate_retries={self._max_gate_retries}). "
                        "Execution terminated."
                    )
                    return ResolverDecision(
                        kind=DecisionKind.TERMINAL_FAILED,
                        phase_id=phase_obj.phase_id,
                        fail_count=fail_count,
                        message=msg,
                        summary=msg,
                    )
                return ResolverDecision(
                    kind=DecisionKind.GATE_FAILED,
                    phase_id=phase_obj.phase_id,
                    fail_count=fail_count,
                )

        # ── paused-takeover: developer is hands-on inside the worktree ─────
        if state.status == "paused-takeover":
            takeover_records = getattr(state, "takeover_records", []) or []
            active = next(
                (r for r in reversed(takeover_records) if not r.get("resumed_at")),
                None,
            )
            step_hint = active.get("step_id", "unknown") if active else "unknown"
            return ResolverDecision(
                kind=DecisionKind.PAUSED_TAKEOVER,
                step_id=step_hint,
            )

        # ── budget_exceeded: token cap hit ─────────────────────────────────
        if state.status == "budget_exceeded":
            total = sum(r.estimated_tokens for r in state.step_results)
            return ResolverDecision(
                kind=DecisionKind.BUDGET_EXCEEDED,
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

        # ── No more phases: all done ───────────────────────────────────────
        if state.current_phase >= len(state.plan.phases):
            return ResolverDecision(
                kind=DecisionKind.NO_PHASES_LEFT,
                message=f"All phases of task {state.task_id} are complete.",
                summary=f"Task {state.task_id} completed successfully.",
            )

        phase_obj = state.current_phase_obj
        if phase_obj is None:
            # Defensive: current_phase < len but current_phase_obj is None.
            return ResolverDecision(
                kind=DecisionKind.NO_PHASES_LEFT,
                message="No more phases.",
                summary=f"Task {state.task_id} completed.",
            )

        steps = phase_obj.steps

        # ── Empty phase: jump to gate or advance ───────────────────────────
        if not steps:
            if phase_obj.gate and not _gate_passed(state, phase_obj.phase_id):
                return ResolverDecision(
                    kind=DecisionKind.EMPTY_PHASE_GATE,
                    phase_id=phase_obj.phase_id,
                )
            # Engine will advance; resolver does NOT recurse.
            return ResolverDecision(
                kind=DecisionKind.EMPTY_PHASE_ADVANCE,
                phase_id=phase_obj.phase_id,
            )

        # ── Any failed step in this phase short-circuits to FAILED ─────────
        failed_in_phase = [
            s.step_id for s in steps if s.step_id in state.failed_step_ids
        ]
        if failed_in_phase:
            first = failed_in_phase[0]
            msg = f"Step {first} failed."
            return ResolverDecision(
                kind=DecisionKind.STEP_FAILED_IN_PHASE,
                phase_id=phase_obj.phase_id,
                step_id=first,
                failed_step_ids=tuple(failed_in_phase),
                message=msg,
                summary=msg,
            )

        # ── Find the next dispatchable step ────────────────────────────────
        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        interacting_ids = {
            r.step_id
            for r in state.step_results
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
            if step.depends_on and not all(
                dep in completed for dep in step.depends_on
            ):
                continue
            next_step = step
            break

        if next_step is not None:
            if next_step.team:
                return ResolverDecision(
                    kind=DecisionKind.TEAM_DISPATCH,
                    phase_id=phase_obj.phase_id,
                    step_id=next_step.step_id,
                )
            return ResolverDecision(
                kind=DecisionKind.DISPATCH,
                phase_id=phase_obj.phase_id,
                step_id=next_step.step_id,
            )

        # ── Interactive: agent responded, awaiting human input ─────────────
        for result in state.step_results:
            if result.status == "interacting":
                plan_step = _find_step(state, result.step_id)
                if plan_step is not None:
                    return ResolverDecision(
                        kind=DecisionKind.INTERACT,
                        phase_id=phase_obj.phase_id,
                        step_id=result.step_id,
                    )

        # ── Interactive: human input given, agent needs continuation ───────
        for result in state.step_results:
            if result.status == "interact_dispatched":
                plan_step = _find_step(state, result.step_id)
                if plan_step is not None:
                    return ResolverDecision(
                        kind=DecisionKind.INTERACT_CONTINUE,
                        phase_id=phase_obj.phase_id,
                        step_id=result.step_id,
                    )

        # ── Timeout enforcement on dispatched in-flight steps ──────────────
        for result in state.step_results:
            if result.status != "dispatched":
                continue
            plan_step = _find_step(state, result.step_id)
            if plan_step is None:
                continue
            timeout_s = _effective_timeout(plan_step)
            if timeout_s <= 0:
                continue
            elapsed = _elapsed_seconds(result.step_started_at or state.started_at)
            if elapsed > timeout_s:
                msg = f"Step {result.step_id} timed out after {timeout_s}s."
                return ResolverDecision(
                    kind=DecisionKind.TIMEOUT,
                    phase_id=phase_obj.phase_id,
                    step_id=result.step_id,
                    message=msg,
                    summary=msg,
                )

        # ── Pending in-flight steps: WAIT ──────────────────────────────────
        pending = (
            {s.step_id for s in steps}
            - completed
            - state.failed_step_ids
            - state.interrupted_step_ids
        )
        if pending:
            return ResolverDecision(
                kind=DecisionKind.WAIT,
                phase_id=phase_obj.phase_id,
                message=(
                    "Waiting for in-flight steps to complete before proceeding."
                ),
                summary=(
                    f"Steps in flight or blocked: {', '.join(sorted(pending))}"
                ),
            )

        # ── All steps complete: approval > feedback > gate > advance ───────
        if (
            phase_obj.approval_required
            and not _approval_passed(state, phase_obj.phase_id)
        ):
            return ResolverDecision(
                kind=DecisionKind.PHASE_NEEDS_APPROVAL,
                phase_id=phase_obj.phase_id,
            )

        if (
            phase_obj.feedback_questions
            and not _feedback_resolved(state, phase_obj.phase_id)
        ):
            return ResolverDecision(
                kind=DecisionKind.PHASE_NEEDS_FEEDBACK,
                phase_id=phase_obj.phase_id,
            )

        if phase_obj.gate and not _gate_passed(state, phase_obj.phase_id):
            return ResolverDecision(
                kind=DecisionKind.PHASE_NEEDS_GATE,
                phase_id=phase_obj.phase_id,
            )

        # Engine will mutate state and re-invoke determine_next.
        return ResolverDecision(
            kind=DecisionKind.PHASE_ADVANCE_OK,
            phase_id=phase_obj.phase_id,
        )
