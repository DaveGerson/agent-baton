"""Trace models — data structures for structured task execution recording."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TraceEvent:
    """A single timestamped event in a task trace DAG."""

    timestamp: str          # ISO 8601 format
    event_type: str         # "agent_start", "agent_complete", "gate_check",
                            # "gate_result", "escalation", "replan",
                            # "file_read", "file_write", "decision"
    agent_name: str | None
    phase: int
    step: int
    details: dict = field(default_factory=dict)
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "agent_name": self.agent_name,
            "phase": self.phase,
            "step": self.step,
            "details": self.details,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TraceEvent:
        return cls(
            timestamp=data.get("timestamp", ""),
            event_type=data.get("event_type", ""),
            agent_name=data.get("agent_name", None),
            phase=data.get("phase", 0),
            step=data.get("step", 0),
            details=data.get("details") or {},
            duration_seconds=data.get("duration_seconds", None),
        )


@dataclass
class TaskTrace:
    """A complete structured trace for a single orchestrated task."""

    task_id: str
    plan_snapshot: dict = field(default_factory=dict)
    events: list[TraceEvent] = field(default_factory=list)
    started_at: str = ""
    completed_at: str | None = None
    outcome: str | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "plan_snapshot": self.plan_snapshot,
            "events": [e.to_dict() for e in self.events],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskTrace:
        raw_events = data.get("events") or []
        return cls(
            task_id=data.get("task_id", ""),
            plan_snapshot=data.get("plan_snapshot") or {},
            events=[TraceEvent.from_dict(e) for e in raw_events],
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", None),
            outcome=data.get("outcome", None),
        )
