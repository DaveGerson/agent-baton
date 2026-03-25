"""Mission log model — human-readable records of agent dispatches.

The mission log is appended to by the orchestrator after each agent
completes (or fails) its assignment.  It provides a chronological
narrative of the task execution, complementing the machine-readable
``ExecutionState`` used by the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_baton.models.enums import FailureClass


@dataclass
class MissionLogEntry:
    """A single timestamped record of an agent's dispatch outcome.

    Written to the mission log after each agent completes, fails,
    retries, or escalates.  The ``to_markdown()`` method renders the
    entry for the human-readable ``mission-log.md`` file.

    Attributes:
        agent_name: Name of the dispatched agent.
        status: Outcome code — ``"COMPLETE"``, ``"FAILED"``,
            ``"RETRIED"``, or ``"ESCALATED"``.
        assignment: Description of the task the agent was given.
        result: Free-text summary of what the agent accomplished.
        files: Filesystem paths the agent created or modified.
        decisions: Architectural or implementation decisions the agent made.
        issues: Problems encountered during execution.
        handoff: Context passed to the next agent in the pipeline.
        commit_hash: Git commit SHA for the agent's work, if committed.
        failure_class: Categorization of the failure mode, if applicable.
        timestamp: When the entry was recorded.
    """

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
    timestamp: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(timespec="seconds"))

    def to_markdown(self) -> str:
        """Render this entry as a markdown block for ``mission-log.md``.

        Returns:
            A multi-line markdown string with a heading, assignment details,
            and any decisions, issues, or handoff notes.
        """
        lines = [
            f"### {self.timestamp} — {self.agent_name} — {self.status}",
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
