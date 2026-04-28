"""Audit sub-package — read-only verification of execution artifacts.

This package hosts post-hoc auditors that verify execution outcomes
against the constraints declared in the plan.  Auditors are read-only
by contract: they NEVER mutate execution state, plans, git history, or
any other operator-owned artifact.  They produce reports that operators
(or CI) can inspect and act on.

Modules:
    dispatch_verifier: Verifies that a dispatched step's filesystem
        and git footprint matches its declared ``allowed_paths`` and
        assigned branch (if any).  Powers ``baton execute verify-dispatch``
        and ``baton execute audit-isolation``.
"""

from agent_baton.core.audit.dispatch_verifier import (
    AuditReport,
    DispatchVerifier,
    VerificationResult,
)

__all__ = [
    "AuditReport",
    "DispatchVerifier",
    "VerificationResult",
]
