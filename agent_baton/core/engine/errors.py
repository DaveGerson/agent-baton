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
