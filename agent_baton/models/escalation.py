"""Escalation model — a question from an agent that needs user input."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Escalation:
    """A question from an agent that requires human input before proceeding.

    Escalations are created when an agent encounters ambiguity it cannot
    resolve autonomously.  They are persisted to ``escalations.json`` and
    surfaced via ``baton status`` or the PMO dashboard.

    Attributes:
        agent_name: The agent that raised the escalation.
        question: The specific question needing a human answer.
        context: Background information to help the human decide.
        options: Suggested answer choices, if applicable.
        priority: ``"blocking"`` halts execution; ``"normal"`` is advisory.
        timestamp: ISO 8601 creation time (auto-populated if blank).
        resolved: Whether the human has answered.
        answer: The human's response, set when resolved.
    """

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
