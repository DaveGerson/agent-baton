"""PhaseManager — phase-boundary evaluator + crisp phase-advance mutator.

Pure-by-default. The single mutating method is ``advance_phase``, which
only touches state.current_phase / state.current_step_index (and optionally
state.status). All other phase-boundary side effects (bead synthesis, VETO
enforcement, event publication, audit writes) remain on ExecutionEngine.

Importing rules:
  - Imports from agent_baton.models.* and agent_baton.core.engine._executor_helpers only.
  - MUST NOT import from agent_baton.core.engine.executor.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent_baton.models.execution import ExecutionState
from agent_baton.core.engine._executor_helpers import (
    is_phase_complete,
    gate_passed_for_phase,
    approval_passed_for_phase,
    feedback_resolved_for_phase,
)


@dataclass(frozen=True)
class ApprovalGateOutcome:
    """Result of evaluating a phase's approval gate.

    Attributes:
        required: ``True`` when the phase has ``approval_required=True``.
        satisfied: ``True`` when a passing approval result exists for the
            phase (result ``"approve"`` or ``"approve-with-feedback"``).
        rejected: ``True`` when at least one approval result for this
            phase has ``result == "reject"``.
    """

    required: bool
    satisfied: bool
    rejected: bool


@dataclass(frozen=True)
class FeedbackGateOutcome:
    """Result of evaluating a phase's feedback gate.

    Attributes:
        required: ``True`` when the phase has at least one feedback question.
        satisfied: ``True`` when all feedback questions have been answered.
        pending_question_ids: Tuple of question IDs that have not yet been
            answered.  Empty when ``satisfied`` is ``True``.

    Note (bd-f4e3 — fixed in commit ``bb83587``):
        Earlier in 005b, ``feedback_resolved_for_phase`` read from
        ``state.current_phase_obj`` rather than scanning by ``phase_id``.
        That bug is fixed: the helper now resolves the phase by its
        ``phase_id`` field, so ``satisfied`` and ``pending_question_ids``
        accurately reflect the queried phase regardless of
        ``state.current_phase``.
    """

    required: bool
    satisfied: bool
    pending_question_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateOutcome:
    """Result of evaluating a phase's QA gate.

    Attributes:
        required: ``True`` when the phase has a non-``None`` gate.
        satisfied: ``True`` when a passing gate result exists for the phase.
        fail_count: Number of ``GateResult`` rows for this phase where
            ``passed=False``.
    """

    required: bool
    satisfied: bool
    fail_count: int = 0


class PhaseManager:
    """Phase-boundary evaluator and crisp phase-advance mutator.

    PhaseManager is stateless — all evaluation methods are pure reads over
    ``ExecutionState`` and the underlying helpers in ``_executor_helpers``.
    The single mutating method, ``advance_phase``, performs only the minimal
    field bumps on ``state`` required to move execution to the next phase.
    All heavy I/O (event publication, bead synthesis, VETO enforcement, audit
    rows) remains on ``ExecutionEngine``.

    Usage::

        pm = PhaseManager()
        if pm.is_phase_complete(state, phase_id):
            gate = pm.evaluate_phase_gate(state, phase_id)
            if gate.required and not gate.satisfied:
                ...
            pm.advance_phase(state, set_status_running=True)

    BEAD_DECISION: PhaseManager has zero collaborators.  Every method is
    either pure or a crisp single-field mutator.  Injecting a bead store /
    event bus / policy engine would re-import the heavy I/O slice that
    belongs on ExecutionEngine.
    """

    def __init__(self) -> None:
        # No collaborators.  Zero-arg singleton.
        pass

    # ------------------------------------------------------------------
    # Pure read-only evaluation methods
    # ------------------------------------------------------------------

    def is_phase_complete(self, state: ExecutionState, phase_id: int) -> bool:
        """Return True when all steps in *phase_id* have terminal results.

        Delegates to the module-level helper :func:`is_phase_complete` in
        ``_executor_helpers``.

        Args:
            state: Current execution state (not mutated).
            phase_id: The phase to evaluate.

        Returns:
            ``True`` when every step has a terminal result
            (complete / failed / interrupted).  ``False`` when the phase
            is not found or any step is still in-flight.
        """
        return is_phase_complete(state, phase_id)

    def evaluate_phase_approval_gate(
        self,
        state: ExecutionState,
        phase_id: int,
    ) -> ApprovalGateOutcome:
        """Evaluate whether the approval gate for *phase_id* is satisfied.

        Args:
            state: Current execution state (not mutated).
            phase_id: The phase to evaluate.

        Returns:
            :class:`ApprovalGateOutcome` with:

            - ``required``: ``True`` iff the phase has
              ``approval_required=True``.
            - ``satisfied``: ``True`` iff at least one approval result for
              the phase carries ``"approve"`` or
              ``"approve-with-feedback"``.
            - ``rejected``: ``True`` iff at least one approval result for
              the phase carries ``"reject"``.
        """
        phase = next(
            (p for p in state.plan.phases if p.phase_id == phase_id),
            None,
        )
        required = phase is not None and phase.approval_required
        satisfied = approval_passed_for_phase(state, phase_id)
        rejected = any(
            a.phase_id == phase_id and a.result == "reject"
            for a in state.approval_results
        )
        return ApprovalGateOutcome(
            required=required,
            satisfied=satisfied,
            rejected=rejected,
        )

    def evaluate_phase_feedback_gate(
        self,
        state: ExecutionState,
        phase_id: int,
    ) -> FeedbackGateOutcome:
        """Evaluate whether all feedback questions for *phase_id* are answered.

        bd-f4e3 was fixed in ``bb83587``:
        :func:`feedback_resolved_for_phase` now resolves the phase by its
        ``phase_id`` field instead of dereferencing
        ``state.current_phase_obj``, so this method returns the correct
        answer for any ``phase_id`` regardless of
        ``state.current_phase``.

        Args:
            state: Current execution state (not mutated).
            phase_id: The phase to evaluate.

        Returns:
            :class:`FeedbackGateOutcome` with:

            - ``required``: ``True`` iff the phase has at least one
              feedback question.
            - ``satisfied``: ``True`` iff all questions have answers.
            - ``pending_question_ids``: Tuple of unanswered question IDs.
        """
        phase = next(
            (p for p in state.plan.phases if p.phase_id == phase_id),
            None,
        )
        if phase is None or not phase.feedback_questions:
            return FeedbackGateOutcome(
                required=False,
                satisfied=True,
                pending_question_ids=(),
            )

        required = True
        satisfied = feedback_resolved_for_phase(state, phase_id)

        # Compute pending question IDs by inspecting the phase resolved
        # by phase_id (bd-f4e3 fixed in bb83587).
        answered_ids = {
            r.question_id
            for r in state.feedback_results
            if r.phase_id == phase_id
        }
        pending = tuple(
            q.question_id
            for q in phase.feedback_questions
            if q.question_id not in answered_ids
        )
        return FeedbackGateOutcome(
            required=required,
            satisfied=satisfied,
            pending_question_ids=pending,
        )

    def evaluate_phase_gate(
        self,
        state: ExecutionState,
        phase_id: int,
    ) -> GateOutcome:
        """Evaluate whether the QA gate for *phase_id* is satisfied.

        Args:
            state: Current execution state (not mutated).
            phase_id: The phase to evaluate.

        Returns:
            :class:`GateOutcome` with:

            - ``required``: ``True`` iff the phase has a non-``None``
              gate.
            - ``satisfied``: ``True`` iff a passing gate result exists.
            - ``fail_count``: Number of gate result rows for this phase
              where ``passed=False``.
        """
        phase = next(
            (p for p in state.plan.phases if p.phase_id == phase_id),
            None,
        )
        required = phase is not None and phase.gate is not None
        satisfied = gate_passed_for_phase(state, phase_id)
        fail_count = sum(
            1
            for g in state.gate_results
            if g.phase_id == phase_id and not g.passed
        )
        return GateOutcome(
            required=required,
            satisfied=satisfied,
            fail_count=fail_count,
        )

    # ------------------------------------------------------------------
    # Single crisp mutator
    # ------------------------------------------------------------------

    def advance_phase(
        self,
        state: ExecutionState,
        *,
        set_status_running: bool = False,
    ) -> None:
        """Bump ``state.current_phase`` and reset ``state.current_step_index``.

        This is the only mutating method on PhaseManager.  It performs the
        minimal field updates needed to move the execution state to the next
        phase.  All surrounding side effects (event publication, bead
        synthesis, VETO enforcement) remain on ``ExecutionEngine``.

        Args:
            state: The execution state to mutate.
            set_status_running: When ``True``, also set
                ``state.status = "running"``.  Pass ``True`` for the
                ``PHASE_ADVANCE_OK`` arm (all-steps-complete path) and
                ``False`` for the ``EMPTY_PHASE_ADVANCE`` arm (no-steps
                path, which must not flip status).
        """
        state.current_phase += 1
        state.current_step_index = 0
        if set_status_running:
            state.status = "running"
