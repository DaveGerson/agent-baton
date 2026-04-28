"""Engine-level exception types.

Exceptions raised by the execution engine when machine-enforceable safety
invariants are violated.

Currently only :class:`ExecutionVetoed` is defined; additional engine-side
exception types should be co-located here as they are added.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.govern.compliance import AuditorVerdict


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
