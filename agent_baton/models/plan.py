from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agent_baton.models.enums import FailureClass


@dataclass
class MissionLogEntry:
    """A single entry in the mission log."""
    agent_name: str
    status: str  # "COMPLETE", "FAILED", "RETRIED", "ESCALATED"
    assignment: str = ""
    result: str = ""
    files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    handoff: str = ""
    commit_hash: str = ""
    failure_class: FailureClass | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        lines = [
            f"### {self.timestamp.isoformat()} — {self.agent_name} — {self.status}",
            f"Assignment: {self.assignment}",
        ]
        if self.result:
            lines.append(f"Result: {self.result}")
        if self.files:
            lines.append(f"Files: {', '.join(self.files)}")
        if self.decisions:
            lines.append("Decisions:")
            for d in self.decisions:
                lines.append(f"  - {d}")
        if self.issues:
            lines.append("Issues:")
            for i in self.issues:
                lines.append(f"  - {i}")
        if self.handoff:
            lines.append(f"Handoff: {self.handoff}")
        if self.commit_hash:
            lines.append(f"Commit: {self.commit_hash}")
        if self.failure_class:
            lines.append(f"Failure class: {self.failure_class.value}")
        lines.append("")
        return "\n".join(lines)
