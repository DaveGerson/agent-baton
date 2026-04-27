"""Data model for the release readiness dashboard (R3.2).

``ReleaseReadinessReport`` is a pure, serialisable snapshot produced by
``ReleaseReadinessChecker.compute()``.  It has no persistence of its own —
it is computed on demand and either printed to stdout or serialised to JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReleaseReadinessReport:
    """Aggregated release health snapshot.

    Attributes:
        release_id:          The release being evaluated.
        computed_at:         ISO-8601 UTC timestamp of computation.
        status:              One of READY | RISKY | BLOCKED.
        score:               0-100 health score (higher is better).
        open_warnings:       Open beads of type ``warning``.
        open_critical_beads: Open beads with severity ``critical``.
        failed_gates_7d:     Failed gate_results within the window.
        incomplete_plans:    Plans linked to this release that are not done.
        slo_breaches_7d:     SLO measurement breaches within the window.
        escalations:         Open escalation rows (soft-skipped if table missing).
        breakdown:           Per-category dict of top items for operator review.
    """

    release_id: str
    computed_at: str
    status: str
    score: int
    open_warnings: int
    open_critical_beads: int
    failed_gates_7d: int
    incomplete_plans: int
    slo_breaches_7d: int
    escalations: int
    breakdown: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON output."""
        return {
            "release_id": self.release_id,
            "computed_at": self.computed_at,
            "status": self.status,
            "score": self.score,
            "open_warnings": self.open_warnings,
            "open_critical_beads": self.open_critical_beads,
            "failed_gates_7d": self.failed_gates_7d,
            "incomplete_plans": self.incomplete_plans,
            "slo_breaches_7d": self.slo_breaches_7d,
            "escalations": self.escalations,
            "breakdown": self.breakdown,
        }
