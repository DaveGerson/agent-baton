"""Engine-level exception types.

Exceptions raised by the execution engine when machine-enforceable safety
invariants are violated.

Classes:
    ExecutionVetoed: Raised when execution is blocked by an auditor VETO.
    ExecutionStateInconsistency: Raised when a save+amend cycle produces a
        reload failure, indicating the persisted state is unreadable or was
        written by a different backend.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.govern.compliance import AuditorVerdict


class ExecutionStateInconsistency(RuntimeError):
    """Raised when execution state cannot be reloaded after a save+amend cycle.

    This indicates that the storage backend wrote state successfully but
    a subsequent load returned ``None``, creating an irreconcilable split
    between the in-memory state and what is durably persisted on disk.
    Callers should not fall back to the stale in-memory state — doing so
    would allow the amendment audit record to disagree with the persisted
    plan.

    Attributes:
        task_id: The task whose state could not be reloaded.
        context: Free-text description of the operation that triggered the
            inconsistency (e.g. ``"record_approval_result:approve-with-feedback"``).
    """

    def __init__(self, *, task_id: str, context: str) -> None:
        self.task_id = task_id
        self.context = context
        super().__init__(
            f"Execution state for task '{task_id}' could not be reloaded after "
            f"a save+amend cycle during '{context}'. The storage backend "
            f"returned None, indicating the persisted state is corrupt or "
            f"inaccessible. Do not fall back to the stale in-memory state."
        )


class ExecutionVetoed(RuntimeError):
    """Raised when execution attempts to advance past a VETO'd HIGH/CRITICAL phase.

    The auditor's compliance verdict was ``VETO`` and the operator did not
    supply ``--force`` to override the block.  The executor halts before
    advancing to the next phase.

    Attributes:
        phase_id: ID of the phase whose advance was blocked.
        verdict: The blocking ``AuditorVerdict`` (always ``VETO``).
        rationale: Free-text rationale extracted from the auditor's report.
    """

    def __init__(
        self,
        *,
        phase_id: int | str,
        verdict: "AuditorVerdict",
        rationale: str = "",
    ) -> None:
        self.phase_id = phase_id
        self.verdict = verdict
        self.rationale = rationale
        message = (
            f"Phase {phase_id} blocked by auditor verdict {verdict.value}. "
            f"Use --force --justification \"...\" to override."
        )
        if rationale:
            message = f"{message}\nAuditor rationale: {rationale}"
        super().__init__(message)


class InvalidApprovalState(RuntimeError):
    """Raised when ``record_approval_result`` is called against an invalid state.

    Hole 1 fix.  Covers the four pre-conditions an approval recording must
    satisfy:

    1. ``ExecutionState.status`` is ``"approval_pending"`` — calls made
       mid-execution would otherwise re-flip status to ``"running"`` and
       silently mask whatever phase the engine was actually in.
    2. The supplied ``phase_id`` matches ``state.current_phase_obj.phase_id`` —
       audit rows must be filed against the phase that actually requested
       approval, not an unrelated one.
    3. The current phase has ``approval_required=True`` — an approval
       cannot be recorded for a phase that never asked for one.
    4. In ``BATON_APPROVAL_MODE=team`` the recording actor must differ from
       whoever requested the approval (no self-approval).

    The ``reason`` attribute is a short machine-readable tag from this class
    (``REASON_*`` constants) so that callers (e.g. the API layer) can map
    specific failures to HTTP status codes without parsing the message.
    """

    REASON_NOT_PENDING: str = "not_approval_pending"
    REASON_PHASE_MISMATCH: str = "phase_mismatch"
    REASON_NO_APPROVAL_REQUESTED: str = "no_approval_requested"
    REASON_SELF_APPROVAL: str = "self_approval_rejected"

    def __init__(
        self,
        *,
        reason: str,
        message: str,
        phase_id: int | str | None = None,
        current_status: str | None = None,
        actor: str | None = None,
        requester: str | None = None,
    ) -> None:
        self.reason = reason
        self.phase_id = phase_id
        self.current_status = current_status
        self.actor = actor
        self.requester = requester
        super().__init__(message)
