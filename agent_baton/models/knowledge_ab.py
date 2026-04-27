"""Data models for knowledge A/B testing experiments (K2.4).

Tracks two variant paths for a single knowledge document and records
per-dispatch assignments so ``KnowledgeABService`` can compute winner
statistics without touching agent dispatch logic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KnowledgeABExperiment:
    """A registered A/B test for a single knowledge document.

    Attributes:
        experiment_id: Unique experiment identifier (UUID4).
        knowledge_id:  Canonical pack/doc id being tested
                       (e.g. ``"security/owasp.md"``).
        variant_a_path: Relative filesystem path to the A variant.
        variant_b_path: Relative filesystem path to the B variant.
        split_ratio:   Fraction of assignments routed to variant A
                       (0.0–1.0, default 0.5).
        status:        ``"active"`` or ``"stopped"``.
        started_at:    ISO 8601 creation timestamp.
        stopped_at:    ISO 8601 stop timestamp, or ``""`` while active.
    """

    experiment_id: str
    knowledge_id: str
    variant_a_path: str
    variant_b_path: str
    split_ratio: float
    status: str
    started_at: str
    stopped_at: str

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "knowledge_id": self.knowledge_id,
            "variant_a_path": self.variant_a_path,
            "variant_b_path": self.variant_b_path,
            "split_ratio": self.split_ratio,
            "status": self.status,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


@dataclass
class KnowledgeABAssignment:
    """A single per-dispatch variant assignment.

    Attributes:
        assignment_id: Auto-increment PK from SQLite.
        experiment_id: Parent experiment.
        task_id:       Execution task being assigned.
        step_id:       Plan step within the task (``""`` for task-level).
        variant:       ``"a"`` or ``"b"``.
        assigned_at:   ISO 8601 assignment timestamp.
        outcome:       ``"success"``, ``"failure"``, or ``""`` (pending).
    """

    assignment_id: int
    experiment_id: str
    task_id: str
    step_id: str
    variant: str
    assigned_at: str
    outcome: str

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "experiment_id": self.experiment_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "variant": self.variant,
            "assigned_at": self.assigned_at,
            "outcome": self.outcome,
        }
