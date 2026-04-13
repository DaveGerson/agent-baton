"""Data models for the learning automation system.

LearningEvidence and LearningIssue are the core data structures used by
the LearningLedger to track recurring signals, proposed fixes, and their
resolution outcomes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Valid literals
# ---------------------------------------------------------------------------

VALID_ISSUE_TYPES: frozenset[str] = frozenset({
    "routing_mismatch",
    "agent_degradation",
    "knowledge_gap",
    "roster_bloat",
    "gate_mismatch",
    "pattern_drift",
    "prompt_evolution",
})

VALID_STATUSES: frozenset[str] = frozenset({
    "open",
    "investigating",
    "proposed",
    "applied",
    "resolved",
    "wontfix",
})

VALID_SEVERITIES: frozenset[str] = frozenset({
    "low",
    "medium",
    "high",
    "critical",
})


# ---------------------------------------------------------------------------
# LearningEvidence
# ---------------------------------------------------------------------------


@dataclass
class LearningEvidence:
    """A single piece of evidence contributing to a LearningIssue.

    Attributes:
        timestamp: ISO 8601 timestamp when the signal was observed.
        source_task_id: Execution task_id that produced this signal.
        detail: Human-readable description of what was observed.
        data: Structured payload (agent names, scores, gate commands, etc.).
    """

    timestamp: str
    source_task_id: str
    detail: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "source_task_id": self.source_task_id,
            "detail": self.detail,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LearningEvidence:
        return cls(
            timestamp=data.get("timestamp", ""),
            source_task_id=data.get("source_task_id", ""),
            detail=data.get("detail", ""),
            data=data.get("data", {}),
        )


# ---------------------------------------------------------------------------
# LearningIssue
# ---------------------------------------------------------------------------


@dataclass
class LearningIssue:
    """Structured record of a recurring pattern that may need remediation.

    Issues are keyed by ``(issue_type, target)`` for deduplication.  Each
    time the same signal recurs, ``occurrence_count`` is incremented and a
    new ``LearningEvidence`` entry is appended.

    Attributes:
        issue_id: UUID identifying this issue record.
        issue_type: Category of the issue — one of VALID_ISSUE_TYPES.
        severity: Impact rating — one of VALID_SEVERITIES.
        status: Lifecycle state — one of VALID_STATUSES.
        title: Human-readable summary of the issue.
        target: Subject of the issue (agent name, flavor key, knowledge pack, etc.).
        evidence: Chronological list of evidence entries.
        first_seen: ISO timestamp of the first observation.
        last_seen: ISO timestamp of the most recent observation.
        occurrence_count: Total number of times this signal has been observed.
        proposed_fix: Description of the recommended remediation, if any.
        resolution: Description of how the issue was resolved, if resolved.
        resolution_type: How it was resolved — ``"auto"``, ``"human"``, or
            ``"interview"``.
        experiment_id: Links to an Experiment record when auto-applied.
    """

    issue_id: str
    issue_type: str
    severity: str
    status: str
    title: str
    target: str
    evidence: list[LearningEvidence] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    occurrence_count: int = 1
    proposed_fix: str | None = None
    resolution: str | None = None
    resolution_type: str | None = None
    experiment_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "status": self.status,
            "title": self.title,
            "target": self.target,
            "evidence": [e.to_dict() for e in self.evidence],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "occurrence_count": self.occurrence_count,
            "proposed_fix": self.proposed_fix,
            "resolution": self.resolution,
            "resolution_type": self.resolution_type,
            "experiment_id": self.experiment_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LearningIssue:
        raw_evidence = data.get("evidence", [])
        # evidence may be stored as a JSON string (from SQLite TEXT column)
        if isinstance(raw_evidence, str):
            try:
                raw_evidence = json.loads(raw_evidence)
            except (ValueError, TypeError):
                raw_evidence = []
        evidence = [LearningEvidence.from_dict(e) for e in raw_evidence]
        return cls(
            issue_id=data["issue_id"],
            issue_type=data.get("issue_type", "routing_mismatch"),
            severity=data.get("severity", "medium"),
            status=data.get("status", "open"),
            title=data.get("title", ""),
            target=data.get("target", ""),
            evidence=evidence,
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
            occurrence_count=int(data.get("occurrence_count", 1)),
            proposed_fix=data.get("proposed_fix"),
            resolution=data.get("resolution"),
            resolution_type=data.get("resolution_type"),
            experiment_id=data.get("experiment_id"),
        )
