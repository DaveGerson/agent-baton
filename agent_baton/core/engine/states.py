"""ExecutionPhaseState — encapsulates the small mutation epilogue tied to
a state.status cluster. Hybrid dispatch with the engine's _apply_resolver_decision:
the dispatch table stays keyed on DecisionKind; the state class is consulted
only for the small mutation tail.

Importing rules:
  - Imports from agent_baton.models.* and agent_baton.core.engine.resolver only.
  - MUST NOT import from agent_baton.core.engine.executor or phase_manager.

Design reference: docs/internal/005b-phase3-design.md §2
Step: 3.3a (005b refactor)
"""

from __future__ import annotations

from typing import Protocol

from agent_baton.core.engine.resolver import DecisionKind, ResolverDecision
from agent_baton.models.execution import ExecutionState


class ExecutionPhaseStateProtocol(Protocol):
    """Encapsulates the small mutation epilogue tied to a state.status cluster.

    Heavy I/O (bead writes, VETO, event publication) is not the state class's
    concern — the engine performs those before/after handle() is called.
    This method's contract: mutate state only as needed for the decision,
    raise on illegal transitions, return None.

    See design §2.4 for the full interface contract.
    """

    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None:
        """Apply the small mutation epilogue for *decision* against *state*.

        Args:
            state: Mutable execution state. Only ``state.status`` (and in
                some arms ``step_results`` items) should be touched.
            decision: The resolver intent. ``decision.kind`` drives dispatch.

        Raises:
            RuntimeError: On an illegal DecisionKind for this state cluster.
        """
        ...


class PlanningState:
    """Handles decisions when state.status == 'pending'.

    Pre-execution. The engine's ``start()`` flips status to ``'running'``
    (or ``'approval_pending'`` for HIGH-risk pre-flight). ``PlanningState``
    exists for completeness — in practice it is only reachable if
    ``next_action()`` is called between plan save and the first status
    transition.

    Most decisions from ``pending`` are handled entirely by the engine's
    heavy builders; the state class is a no-op for all of them.
    """

    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None:
        # All DecisionKinds reachable from 'pending' are transition-initiating
        # decisions: the engine's start() handles the flip out of 'pending'.
        # The state class has no additional mutation to perform.
        # DISPATCH and TEAM_DISPATCH are unlikely from 'pending' but not
        # illegal — engine handles them via heavy builders (bd-068b).
        _ = state
        _ = decision


class ExecutingPhaseState:
    """Handles decisions when state.status == 'running'.

    The big one. Owns the mutation epilogues for:

    - DISPATCH / TEAM_DISPATCH / INTERACT / INTERACT_CONTINUE — no-op
      (heavy builder on engine produces the action; no status flip).
    - WAIT — no-op (engine builds WAIT action directly).
    - NO_PHASES_LEFT — no-op (engine builds COMPLETE action).
    - EMPTY_PHASE_GATE / PHASE_NEEDS_GATE — flip to ``'gate_pending'``.
    - EMPTY_PHASE_ADVANCE / PHASE_ADVANCE_OK — no-op here; engine calls
      ``phase_manager.advance_phase()`` separately.
    - PHASE_NEEDS_APPROVAL — flip to ``'approval_pending'``.
    - PHASE_NEEDS_FEEDBACK — flip to ``'feedback_pending'``.
    - STEP_FAILED_IN_PHASE — flip to ``'failed'``.
    - TERMINAL_FAILED — flip to ``'failed'`` (gate-exhaustion path).
    - TIMEOUT — flip to ``'failed'`` (engine writes bead + step result
      mutation before calling handle()).

    See design §2.3 for the canonical table.
    """

    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None:
        kind = decision.kind

        # ── No-op arms: heavy builder on engine produces the action ──────────
        if kind in (
            DecisionKind.DISPATCH,
            DecisionKind.TEAM_DISPATCH,
            DecisionKind.INTERACT,
            DecisionKind.INTERACT_CONTINUE,
            DecisionKind.WAIT,
            DecisionKind.NO_PHASES_LEFT,
        ):
            return

        # ── Phase-advance arms: engine calls phase_manager.advance_phase() ───
        # State class has no mutation here; PhaseManager owns the bump.
        if kind in (
            DecisionKind.EMPTY_PHASE_ADVANCE,
            DecisionKind.PHASE_ADVANCE_OK,
        ):
            return

        # ── Gate-pending: gate is required and not yet passed ─────────────────
        if kind in (
            DecisionKind.EMPTY_PHASE_GATE,
            DecisionKind.PHASE_NEEDS_GATE,
        ):
            # Non-coupled status flip: no I1/I2/I9 sibling field is paired
            # with gate_pending.  Direct write is intentional and safe.
            state.status = "gate_pending"  # noqa: state-mutation
            return

        # ── Approval-pending: all steps complete, approval required ───────────
        # The I1-coupled status flip + pending_approval_request stamp is
        # owned by the engine's _approval_action via
        # ExecutionState.transition_to_approval_pending. No-op here so the
        # two writes are atomic in the caller.
        if kind == DecisionKind.PHASE_NEEDS_APPROVAL:
            return

        # ── Feedback-pending: all steps complete, questions not answered ──────
        if kind == DecisionKind.PHASE_NEEDS_FEEDBACK:
            # Non-coupled status flip: no sibling field is paired with
            # feedback_pending.  Direct write is intentional and safe.
            state.status = "feedback_pending"  # noqa: state-mutation
            return

        # ── Terminal failure arms ─────────────────────────────────────────────
        # I2: every terminal-failure path funnels through transition_to_failed
        # so completed_at is stamped atomically with the status flip.
        if kind == DecisionKind.STEP_FAILED_IN_PHASE:
            # Engine calls _close_open_beads_at_terminal before handle().
            state.transition_to_failed(reason="step failed in phase")
            return

        if kind == DecisionKind.TERMINAL_FAILED:
            # Gate-exhaustion path: resolver returned TERMINAL_FAILED from a
            # 'running' status context (fail_count >= max_gate_retries while
            # state.status was 'running').  Flip to failed; engine persists.
            state.transition_to_failed(reason="gate exhausted (TERMINAL_FAILED)")
            return

        if kind == DecisionKind.TIMEOUT:
            # Engine writes the timeout bead and mutates the step result
            # (result.status / result.outcome / result.error / result.completed_at)
            # before calling handle(). State class only flips the overall status.
            state.transition_to_failed(reason="step timeout")
            return

        # Anything else from 'running' is a resolver bug — every DecisionKind
        # that can occur in running status is listed above.
        raise RuntimeError(
            f"ExecutingPhaseState received unexpected DecisionKind {kind!r} "
            f"from state.status {state.status!r}"
        )


class AwaitingApprovalState:
    """Handles decisions when state.status is in the blocked-on-input cluster.

    Covers: ``'approval_pending'``, ``'feedback_pending'``, ``'gate_pending'``,
    ``'gate_failed'``, ``'paused'``, ``'paused-takeover'``.

    Most decisions in this cluster are pass-through: the resolver already
    classified them as APPROVAL_PENDING / FEEDBACK_PENDING / GATE_PENDING /
    GATE_FAILED / PAUSED_TAKEOVER, and the engine's heavy builder produces
    the final action. The state class confirms the contract and is a no-op.

    Terminal decisions (TERMINAL_FAILED, BUDGET_EXCEEDED) are valid from
    blocked states (e.g. budget exceeded while paused). TERMINAL_FAILED
    flips status to ``'failed'``; others are no-ops because the engine's
    heavy builder owns the COMPLETE/FAILED action construction.

    DISPATCH-class decisions (DISPATCH, TEAM_DISPATCH, INTERACT,
    INTERACT_CONTINUE) arriving here are resolver bugs: the resolver
    should have detected the blocked status and returned the appropriate
    blocked-state kind. We raise to surface this.

    See design §2.3 for the canonical table.
    """

    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None:
        kind = decision.kind

        # ── Pass-through blocked-state kinds ─────────────────────────────────
        # Engine heavy builder produces the APPROVAL/FEEDBACK/GATE/WAIT action.
        if kind in (
            DecisionKind.APPROVAL_PENDING,
            DecisionKind.FEEDBACK_PENDING,
            DecisionKind.GATE_PENDING,
            DecisionKind.GATE_FAILED,
            DecisionKind.PAUSED_TAKEOVER,
            DecisionKind.WAIT,
            DecisionKind.NO_PHASES_LEFT,
        ):
            return

        # ── Terminal decisions valid from blocked states ───────────────────────
        if kind == DecisionKind.TERMINAL_FAILED:
            # Gate-exhaustion while in gate_failed: resolver escalated to
            # TERMINAL_FAILED.  I2: stamp completed_at atomically.
            state.transition_to_failed(
                reason="terminal failed from blocked state"
            )
            return

        if kind in (
            DecisionKind.TERMINAL_COMPLETE,
            DecisionKind.BUDGET_EXCEEDED,
        ):
            # Pure report; engine builds COMPLETE action. No status flip.
            return

        # ── DISPATCH-class arriving at a blocked state is a resolver bug ──────
        if kind in (
            DecisionKind.DISPATCH,
            DecisionKind.TEAM_DISPATCH,
            DecisionKind.INTERACT,
            DecisionKind.INTERACT_CONTINUE,
        ):
            raise RuntimeError(
                f"AwaitingApprovalState received DISPATCH-class decision "
                f"{kind!r} but state.status is {state.status!r} — resolver "
                f"should have transitioned to running first"
            )

        # ── Catch-all for unexpected kinds from blocked states ─────────────────
        raise RuntimeError(
            f"AwaitingApprovalState received unexpected DecisionKind {kind!r} "
            f"from state.status {state.status!r}"
        )


class TerminalState:
    """Handles decisions when state.status is in the terminal cluster.

    Covers: ``'complete'``, ``'failed'``, ``'budget_exceeded'``.

    Terminal-pass-through decisions (TERMINAL_COMPLETE, TERMINAL_FAILED,
    BUDGET_EXCEEDED, NO_PHASES_LEFT) are no-ops — the engine builds the
    final COMPLETE/FAILED action with no further mutation needed.

    Any decision implying further mutation (DISPATCH, GATE_PENDING, etc.)
    is a resolver bug and raises RuntimeError immediately.

    See design §2.3 and §2.7 for the canonical contract.
    """

    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None:
        kind = decision.kind

        # ── Terminal pass-through kinds ───────────────────────────────────────
        if kind in (
            DecisionKind.TERMINAL_COMPLETE,
            DecisionKind.TERMINAL_FAILED,
            DecisionKind.BUDGET_EXCEEDED,
            DecisionKind.NO_PHASES_LEFT,
        ):
            return

        # Any other kind from a terminal state is a resolver bug.
        raise RuntimeError(
            f"TerminalState received non-terminal DecisionKind {kind!r} "
            f"from state.status {state.status!r} — execution is already terminal"
        )
