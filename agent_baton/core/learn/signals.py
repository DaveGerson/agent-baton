"""Wave 6.2 Part B — LearningSignal: structured signals from immune findings.

Each immune sweep finding becomes a :class:`LearningSignal` that feeds the
closed-loop learning pipeline (Wave 4.1 / Wave 4.2):

- ``deprecated-api`` finding → agent X used deprecated symbol Y →
  updates X's ``agent_context`` to "avoid Y".
- Repeat findings → reduce agent's ``PerformanceScorer`` rating.

Signals are lightweight (no SQLite writes here); consumers ingest them via
the learning pipeline's existing ingestion path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.immune.sweeper import SweepFinding

__all__ = ["LearningSignal"]


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class LearningSignal:
    """A learning signal emitted by immune, self-heal, or other subsystems.

    Consumed by the closed-loop learning pipeline to update agent context
    profiles and performance scorer ratings.

    Attributes:
        source: Origin of this signal, e.g. ``"immune-sweep"``,
            ``"self-heal-success"``.
        target_agent: Name of the agent to update (e.g. ``"backend-engineer"``).
            May be empty when the signal is not agent-specific.
        finding_kind: Sweep kind that produced this signal (e.g.
            ``"deprecated-api"``).
        confidence: Confidence of the underlying finding (0.0–1.0).
        payload: Arbitrary key-value context for the learning consumer.
        created_at: ISO-8601 UTC timestamp of signal creation.
    """

    source: str
    target_agent: str
    finding_kind: str
    confidence: float
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_str)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_immune_finding(cls, finding: "SweepFinding") -> "LearningSignal":
        """Build a :class:`LearningSignal` from an immune :class:`SweepFinding`.

        Maps the sweep kind to the appropriate target agent so the learning
        pipeline can update the right agent's context profile.

        Args:
            finding: The :class:`~agent_baton.core.immune.sweeper.SweepFinding`
                to convert.

        Returns:
            A populated :class:`LearningSignal` for downstream consumption.
        """
        # Best-effort mapping from finding kind to the agent most likely to
        # have introduced the issue.
        _KIND_AGENT_MAP: dict[str, str] = {
            "deprecated-api": "backend-engineer",
            "deprecated-api-trivial": "backend-engineer",
            "doc-drift": "backend-engineer",
            "doc-drift-signature": "backend-engineer",
            "stale-comment": "backend-engineer",
            "untested-edges": "test-engineer",
            "todo-rot": "",  # not agent-specific
        }
        target = _KIND_AGENT_MAP.get(finding.kind, "")

        return cls(
            source="immune-sweep",
            target_agent=target,
            finding_kind=finding.kind,
            confidence=finding.confidence,
            payload={
                "path": str(finding.target.path),
                "description": finding.description,
                "affected_lines": finding.affected_lines,
                "auto_fix_directive": finding.auto_fix_directive,
            },
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return {
            "source": self.source,
            "target_agent": self.target_agent,
            "finding_kind": self.finding_kind,
            "confidence": self.confidence,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LearningSignal":
        """Reconstruct a :class:`LearningSignal` from a serialised dict."""
        return cls(
            source=data.get("source", ""),
            target_agent=data.get("target_agent", ""),
            finding_kind=data.get("finding_kind", ""),
            confidence=float(data.get("confidence", 0.0)),
            payload=data.get("payload", {}),
            created_at=data.get("created_at", _utcnow_str()),
        )
