"""Engine-level exception types.

Exceptions raised by the execution engine when machine-enforceable safety
invariants are violated.

Exception classes defined here:

- :class:`ExecutionVetoed` — auditor VETO blocked phase advance.
- :class:`InvalidApprovalState` — ``record_approval_result`` called in an
  invalid state (reason codes: ``not_approval_pending``, ``phase_mismatch``,
  ``no_approval_requested``, ``self_approval_rejected``).
- :class:`ComplianceWriteError` — compliance audit log write failed when
  ``BATON_COMPLIANCE_FAIL_CLOSED=1``.
- :class:`ExecutionStateInconsistency` — state reload failed after a plan
  amendment in the approve-with-feedback path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_baton.core.govern.compliance import AuditorVerdict

ApprovalStateReason = Literal[
    "not_approval_pending",
    "phase_mismatch",
    "no_approval_requested",
    "self_approval_rejected",
]


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
    """Raised when ``record_approval_result`` is called in an invalid state.

    The ``reason`` field is the machine-readable code the API maps to an HTTP
    status.  The frontend should branch on ``reason`` for localised UI copy.

    Reason codes:

    - ``not_approval_pending`` — execution is not in ``approval_pending``
      status; approve/reject arrived too early or too late.
    - ``phase_mismatch`` — the supplied ``phase_id`` does not match the phase
      currently awaiting approval.
    - ``no_approval_requested`` — the current phase does not have
      ``approval_required=True``.
    - ``self_approval_rejected`` — the actor is the same user as the
      requester and the deployment is in ``team`` approval mode.

    Attributes:
        reason: One of the four reason codes above.
        phase_id: Phase ID supplied by the caller (may be ``None``).
        current_status: Execution status at the time of the call.
        actor: Identity string of the user attempting the action.
        requester: Identity string of the user who requested approval.
    """

    def __init__(
        self,
        *,
        reason: ApprovalStateReason,
        phase_id: int | None = None,
        current_status: str = "",
        actor: str = "",
        requester: str = "",
    ) -> None:
        self.reason = reason
        self.phase_id = phase_id
        self.current_status = current_status
        self.actor = actor
        self.requester = requester
        super().__init__(
            f"Invalid approval state: {reason}"
            + (f" (phase_id={phase_id})" if phase_id is not None else "")
            + (f", status={current_status}" if current_status else "")
        )


class ComplianceWriteError(RuntimeError):
    """Raised when a compliance audit log write fails and fail-closed mode is on.

    Only raised when ``BATON_COMPLIANCE_FAIL_CLOSED=1``.  When unset or ``0``
    the failure is logged and a bead warning is emitted instead.

    Attributes:
        audit_path: Path (or identifier) of the audit record that could not
            be written (may be an empty string if unavailable).
        original_error: The underlying exception that caused the write failure.
    """

    def __init__(
        self,
        message: str = "",
        *,
        audit_path: str = "",
        original_error: BaseException | None = None,
    ) -> None:
        self.audit_path = audit_path
        self.original_error = original_error
        super().__init__(message or "Compliance audit write failed.")


class ExecutionStateInconsistency(RuntimeError):
    """Raised when execution state cannot be reloaded after a plan amendment.

    This indicates a data-integrity problem on the server: the engine saved
    an amended plan but the subsequent reload returned ``None`` or raised.
    It is always a 500-class error.

    Attributes:
        task_id: Task ID of the affected execution.
        operation: Description of the operation that triggered the reload
            (e.g. ``"approve-with-feedback"``).
    """

    def __init__(
        self,
        message: str = "",
        *,
        task_id: str = "",
        operation: str = "",
    ) -> None:
        self.task_id = task_id
        self.operation = operation
        super().__init__(message or f"Execution state inconsistency (task={task_id}, op={operation}).")
