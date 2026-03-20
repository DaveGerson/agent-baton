from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class AgentUsageRecord:
    """Record of a single agent's usage within a task."""
    name: str
    model: str = "sonnet"
    steps: int = 1
    retries: int = 0
    gate_results: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AgentUsageRecord:
        return cls(
            name=data["name"],
            model=data.get("model", "sonnet"),
            steps=data.get("steps", 1),
            retries=data.get("retries", 0),
            gate_results=data.get("gate_results", []),
            estimated_tokens=data.get("estimated_tokens", 0),
            duration_seconds=data.get("duration_seconds", 0.0),
        )


@dataclass
class TaskUsageRecord:
    """Record of a full orchestrated task's usage."""
    task_id: str
    timestamp: str  # ISO format
    agents_used: list[AgentUsageRecord] = field(default_factory=list)
    total_agents: int = 0
    risk_level: str = "LOW"
    sequencing_mode: str = "phased_delivery"
    gates_passed: int = 0
    gates_failed: int = 0
    outcome: str = ""  # "SHIP", "SHIP WITH NOTES", "REVISE", "BLOCK"
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict handles nested dataclasses recursively, so agents_used is
        # already a list[dict] at this point — no manual conversion needed.
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskUsageRecord:
        agents_used = [
            AgentUsageRecord.from_dict(a)
            for a in data.get("agents_used", [])
        ]
        return cls(
            task_id=data["task_id"],
            timestamp=data["timestamp"],
            agents_used=agents_used,
            total_agents=data.get("total_agents", 0),
            risk_level=data.get("risk_level", "LOW"),
            sequencing_mode=data.get("sequencing_mode", "phased_delivery"),
            gates_passed=data.get("gates_passed", 0),
            gates_failed=data.get("gates_failed", 0),
            outcome=data.get("outcome", ""),
            notes=data.get("notes", ""),
        )
