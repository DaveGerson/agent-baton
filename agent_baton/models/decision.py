"""Decision model — human decision requests and resolutions during execution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class DecisionRequest:
    """A request for human input during execution."""

    request_id: str
    task_id: str
    decision_type: str       # "gate_approval", "escalation", "plan_review"
    summary: str             # human-readable context
    options: list[str] = field(default_factory=list)  # e.g. ["approve", "reject", "modify"]
    deadline: str | None = None  # ISO 8601 timeout (optional)
    context_files: list[str] = field(default_factory=list)
    created_at: str = ""
    status: str = "pending"  # "pending", "resolved", "expired"

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @classmethod
    def create(
        cls,
        task_id: str,
        decision_type: str,
        summary: str,
        options: list[str] | None = None,
        **kwargs: object,
    ) -> DecisionRequest:
        """Factory method — auto-generates a request_id."""
        return cls(
            request_id=uuid.uuid4().hex[:12],
            task_id=task_id,
            decision_type=decision_type,
            summary=summary,
            options=options or ["approve", "reject"],
            **kwargs,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "decision_type": self.decision_type,
            "summary": self.summary,
            "options": self.options,
            "deadline": self.deadline,
            "context_files": self.context_files,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DecisionRequest:
        return cls(
            request_id=data.get("request_id", ""),
            task_id=data.get("task_id", ""),
            decision_type=data.get("decision_type", ""),
            summary=data.get("summary", ""),
            options=data.get("options", []),
            deadline=data.get("deadline"),
            context_files=data.get("context_files", []),
            created_at=data.get("created_at", ""),
            status=data.get("status", "pending"),
        )


@dataclass
class DecisionResolution:
    """Resolution of a decision request."""

    request_id: str
    chosen_option: str
    rationale: str | None = None
    resolved_by: str = "human"  # "human", "timeout_default", "auto_policy"
    resolved_at: str = ""

    def __post_init__(self) -> None:
        if not self.resolved_at:
            self.resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "chosen_option": self.chosen_option,
            "rationale": self.rationale,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DecisionResolution:
        return cls(
            request_id=data.get("request_id", ""),
            chosen_option=data.get("chosen_option", ""),
            rationale=data.get("rationale"),
            resolved_by=data.get("resolved_by", "human"),
            resolved_at=data.get("resolved_at", ""),
        )
