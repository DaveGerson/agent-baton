"""Context profile models — data structures for agent context efficiency analysis."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentContextProfile:
    """Context efficiency profile for a single agent within a task.

    Measures how effectively an agent used its context window by
    comparing files read versus files actually written or referenced
    in its output.  Used by the ``baton context-profile`` command to
    identify agents that read too many irrelevant files.

    Attributes:
        agent_name: Name of the profiled agent.
        files_read: Files the agent read during execution.
        files_written: Files the agent created or modified.
        files_referenced: Files mentioned in the agent's output but
            not directly written.
        context_tokens_estimate: Estimated tokens consumed by context
            (files read + prompt).
        output_tokens_estimate: Estimated tokens in the agent's output.
        efficiency_score: Ratio of useful output to context consumed
            (higher is better, range 0.0 to 1.0).
    """

    agent_name: str
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    files_referenced: list[str] = field(default_factory=list)
    context_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    efficiency_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "files_read": list(self.files_read),
            "files_written": list(self.files_written),
            "files_referenced": list(self.files_referenced),
            "context_tokens_estimate": self.context_tokens_estimate,
            "output_tokens_estimate": self.output_tokens_estimate,
            "efficiency_score": self.efficiency_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentContextProfile:
        return cls(
            agent_name=data["agent_name"],
            files_read=data.get("files_read") or [],
            files_written=data.get("files_written") or [],
            files_referenced=data.get("files_referenced") or [],
            context_tokens_estimate=data.get("context_tokens_estimate", 0),
            output_tokens_estimate=data.get("output_tokens_estimate", 0),
            efficiency_score=data.get("efficiency_score", 0.0),
        )


@dataclass
class TaskContextProfile:
    """Aggregated context efficiency profile for a complete orchestrated task.

    Combines individual ``AgentContextProfile`` records to compute
    cross-agent redundancy metrics — useful for identifying cases where
    multiple agents read the same files unnecessarily.

    Attributes:
        task_id: Execution identifier this profile belongs to.
        agent_profiles: Per-agent efficiency breakdowns.
        total_files_read: Sum of all file reads across all agents.
        unique_files_read: Number of distinct files read.
        redundant_reads: ``total_files_read - unique_files_read``.
        redundancy_rate: Fraction of reads that were redundant (0.0 to 1.0).
        created_at: ISO 8601 timestamp when this profile was generated.
    """

    task_id: str
    agent_profiles: list[AgentContextProfile] = field(default_factory=list)
    total_files_read: int = 0
    unique_files_read: int = 0
    redundant_reads: int = 0
    redundancy_rate: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_profiles": [p.to_dict() for p in self.agent_profiles],
            "total_files_read": self.total_files_read,
            "unique_files_read": self.unique_files_read,
            "redundant_reads": self.redundant_reads,
            "redundancy_rate": self.redundancy_rate,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskContextProfile:
        raw_profiles = data.get("agent_profiles") or []
        return cls(
            task_id=data["task_id"],
            agent_profiles=[AgentContextProfile.from_dict(p) for p in raw_profiles],
            total_files_read=data.get("total_files_read", 0),
            unique_files_read=data.get("unique_files_read", 0),
            redundant_reads=data.get("redundant_reads", 0),
            redundancy_rate=data.get("redundancy_rate", 0.0),
            created_at=data.get("created_at", ""),
        )
