"""Escalation model — a question from an agent that needs user input."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Escalation:
    """A question from an agent that needs user input."""

    agent_name: str
    question: str
    context: str = ""
    options: list[str] = field(default_factory=list)
    priority: str = "normal"   # "blocking" or "normal"
    timestamp: str = ""        # ISO format; populated on first write if blank
    resolved: bool = False
    answer: str = ""           # filled in when resolved

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(tz=timezone.utc).isoformat()

    def to_markdown(self) -> str:
        """Render the escalation as a markdown block."""
        status = "RESOLVED" if self.resolved else "PENDING"
        options_str = ", ".join(self.options) if self.options else ""
        lines = [
            f"### {self.timestamp} — {self.agent_name} — {status}",
            f"**Priority:** {self.priority}",
            f"**Question:** {self.question}",
            f"**Context:** {self.context}",
            f"**Options:** {options_str}",
            f"**Answer:** {self.answer}",
        ]
        return "\n".join(lines)
