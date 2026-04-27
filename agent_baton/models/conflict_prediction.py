"""Data models for plan-time file conflict prediction (R3.7).

Predicts file-level conflicts between parallel-eligible steps in a
plan *before* execution, so the operator can spot likely merge friction
ahead of time.  These models are ephemeral (computed on demand) and
therefore have no storage layer — they only support JSON
serialization for CLI output and possible future integrations.

See ``agent_baton.core.release.conflict_predictor.ConflictPredictor``
for the analyzer that produces ``PlanConflictReport``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# Allowed conflict types.  Kept as a module-level constant rather than
# an Enum so the values flow through ``to_dict``/``from_dict`` without
# extra coercion and stay friendly to JSON consumers.
CONFLICT_TYPES: tuple[str, ...] = ("write_write", "read_write")


@dataclass
class FileConflict:
    """A predicted file-level conflict between two parallel-eligible steps.

    Attributes:
        step_a_id: First step's ``step_id`` (e.g. ``"1.1"``).
        step_b_id: Second step's ``step_id``.  Ordering is stable
            (``step_a_id <= step_b_id``) so duplicates are easy to dedupe.
        file_path: Path or path-glob the two steps both touch.
        conflict_type: Either ``"write_write"`` (both steps write the
            same path) or ``"read_write"`` (one writes, the other reads).
        confidence: Heuristic strength of the prediction in ``[0.0, 1.0]``.
            Higher means the conflict is more likely to materialize.
        reason: Short human-readable explanation suitable for CLI output.
    """

    step_a_id: str
    step_b_id: str
    file_path: str
    conflict_type: str
    confidence: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.conflict_type not in CONFLICT_TYPES:
            raise ValueError(
                f"conflict_type must be one of {CONFLICT_TYPES}, "
                f"got {self.conflict_type!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )

    def to_dict(self) -> dict:
        return {
            "step_a_id": self.step_a_id,
            "step_b_id": self.step_b_id,
            "file_path": self.file_path,
            "conflict_type": self.conflict_type,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FileConflict:
        return cls(
            step_a_id=data["step_a_id"],
            step_b_id=data["step_b_id"],
            file_path=data["file_path"],
            conflict_type=data["conflict_type"],
            confidence=float(data["confidence"]),
            reason=data.get("reason", ""),
        )


@dataclass
class PlanConflictReport:
    """A conflict-prediction summary for one plan.

    Attributes:
        plan_id: ``MachinePlan.task_id`` of the analyzed plan.
        computed_at: ISO 8601 UTC timestamp when prediction ran.
        conflicts: All detected conflicts, sorted by confidence descending.
        total_steps_analyzed: Number of steps inspected across all phases.
        parallel_groups_analyzed: Number of phases with two or more
            parallel-eligible steps.
    """

    plan_id: str
    computed_at: str = ""
    conflicts: list[FileConflict] = field(default_factory=list)
    total_steps_analyzed: int = 0
    parallel_groups_analyzed: int = 0

    def __post_init__(self) -> None:
        if not self.computed_at:
            self.computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "computed_at": self.computed_at,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "total_steps_analyzed": self.total_steps_analyzed,
            "parallel_groups_analyzed": self.parallel_groups_analyzed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanConflictReport:
        return cls(
            plan_id=data["plan_id"],
            computed_at=data.get("computed_at", ""),
            conflicts=[FileConflict.from_dict(c) for c in data.get("conflicts", [])],
            total_steps_analyzed=int(data.get("total_steps_analyzed", 0)),
            parallel_groups_analyzed=int(data.get("parallel_groups_analyzed", 0)),
        )
