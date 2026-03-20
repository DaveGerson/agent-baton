"""Data models for task retrospectives."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentOutcome:
    """Outcome record for a single agent within a retrospective."""
    name: str
    worked_well: str = ""
    issues: str = ""
    root_cause: str = ""


@dataclass
class KnowledgeGap:
    """A gap in agent knowledge exposed during a task."""
    description: str
    affected_agent: str = ""
    suggested_fix: str = ""  # "create knowledge pack", "update agent prompt", etc.


@dataclass
class RosterRecommendation:
    """A recommendation about the agent roster from a retrospective."""
    action: str  # "create", "improve", "remove"
    target: str  # agent name or knowledge pack
    reason: str = ""


@dataclass
class SequencingNote:
    """A note about the effectiveness of task sequencing."""
    phase: str
    observation: str  # e.g., "gate caught issue X", "gate was unnecessary"
    keep: bool = True


@dataclass
class Retrospective:
    """Structured retrospective for an orchestrated task."""
    task_id: str
    task_name: str
    timestamp: str  # ISO format

    # Metrics
    agent_count: int = 0
    retry_count: int = 0
    gates_passed: int = 0
    gates_failed: int = 0
    risk_level: str = "LOW"
    duration_estimate: str = ""
    estimated_tokens: int = 0

    # Qualitative
    what_worked: list[AgentOutcome] = field(default_factory=list)
    what_didnt: list[AgentOutcome] = field(default_factory=list)
    knowledge_gaps: list[KnowledgeGap] = field(default_factory=list)
    roster_recommendations: list[RosterRecommendation] = field(default_factory=list)
    sequencing_notes: list[SequencingNote] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the retrospective as markdown."""
        lines = [
            f"# Retrospective: {self.task_name}",
            "",
            f"**Task ID:** {self.task_id}",
            f"**Date:** {self.timestamp}",
            "",
            "## Metrics",
            f"- Agents: {self.agent_count}, Retries: {self.retry_count}, "
            f"Gates: {self.gates_passed}/{self.gates_passed + self.gates_failed}",
            f"- Risk: {self.risk_level}, Duration: {self.duration_estimate or 'N/A'}, "
            f"Estimated tokens: {self.estimated_tokens:,}",
            "",
        ]

        if self.what_worked:
            lines.append("## What Worked")
            for outcome in self.what_worked:
                lines.append(f"- **{outcome.name}**: {outcome.worked_well}")
            lines.append("")

        if self.what_didnt:
            lines.append("## What Didn't")
            for outcome in self.what_didnt:
                detail = outcome.issues
                if outcome.root_cause:
                    detail += f" (root cause: {outcome.root_cause})"
                lines.append(f"- **{outcome.name}**: {detail}")
            lines.append("")

        if self.knowledge_gaps:
            lines.append("## Knowledge Gaps Exposed")
            for gap in self.knowledge_gaps:
                line = f"- {gap.description}"
                if gap.suggested_fix:
                    line += f" — *fix: {gap.suggested_fix}*"
                lines.append(line)
            lines.append("")

        if self.roster_recommendations:
            lines.append("## Roster Recommendations")
            for rec in self.roster_recommendations:
                lines.append(f"- **{rec.action.capitalize()}:** {rec.target}")
                if rec.reason:
                    lines.append(f"  {rec.reason}")
            lines.append("")

        if self.sequencing_notes:
            lines.append("## Sequencing Notes")
            for note in self.sequencing_notes:
                keep_tag = "keep" if note.keep else "consider removing"
                lines.append(f"- Phase {note.phase}: {note.observation} ({keep_tag})")
            lines.append("")

        return "\n".join(lines)
