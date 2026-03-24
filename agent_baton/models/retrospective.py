"""Data models for task retrospectives."""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.knowledge import KnowledgeGapRecord


@dataclass
class AgentOutcome:
    """Outcome record for a single agent within a retrospective."""
    name: str
    worked_well: str = ""
    issues: str = ""
    root_cause: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "worked_well": self.worked_well,
            "issues": self.issues,
            "root_cause": self.root_cause,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentOutcome:
        return cls(
            name=data["name"],
            worked_well=data.get("worked_well", ""),
            issues=data.get("issues", ""),
            root_cause=data.get("root_cause", ""),
        )


@dataclass
class KnowledgeGap:
    """A gap in agent knowledge exposed during a task."""
    description: str
    affected_agent: str = ""
    suggested_fix: str = ""  # "create knowledge pack", "update agent prompt", etc.

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "affected_agent": self.affected_agent,
            "suggested_fix": self.suggested_fix,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeGap:
        return cls(
            description=data["description"],
            affected_agent=data.get("affected_agent", ""),
            suggested_fix=data.get("suggested_fix", ""),
        )


@dataclass
class RosterRecommendation:
    """A recommendation about the agent roster from a retrospective."""
    action: str  # "create", "improve", "remove"
    target: str  # agent name or knowledge pack
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RosterRecommendation:
        return cls(
            action=data["action"],
            target=data["target"],
            reason=data.get("reason", ""),
        )


@dataclass
class SequencingNote:
    """A note about the effectiveness of task sequencing."""
    phase: str
    observation: str  # e.g., "gate caught issue X", "gate was unnecessary"
    keep: bool = True

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "observation": self.observation,
            "keep": self.keep,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SequencingNote:
        return cls(
            phase=data["phase"],
            observation=data["observation"],
            keep=bool(data.get("keep", True)),
        )


def _knowledge_gap_from_dict(data: dict) -> KnowledgeGapRecord:
    """Deserialise a knowledge gap entry with backward-compat for the old KnowledgeGap schema.

    Old schema had: description, affected_agent, suggested_fix
    New schema has: description, gap_type, resolution, resolution_detail,
                    agent_name, task_summary, task_type
    """
    # Old schema detection: presence of 'affected_agent' or 'suggested_fix'
    if "affected_agent" in data or "suggested_fix" in data:
        return KnowledgeGapRecord(
            description=data["description"],
            gap_type="factual",             # reasonable default for old records
            resolution="unresolved",
            resolution_detail=data.get("suggested_fix", ""),
            agent_name=data.get("affected_agent", ""),
            task_summary="",
            task_type=None,
        )
    return KnowledgeGapRecord.from_dict(data)


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
    knowledge_gaps: list[KnowledgeGapRecord] = field(default_factory=list)
    roster_recommendations: list[RosterRecommendation] = field(default_factory=list)
    sequencing_notes: list[SequencingNote] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON persistence."""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "timestamp": self.timestamp,
            "agent_count": self.agent_count,
            "retry_count": self.retry_count,
            "gates_passed": self.gates_passed,
            "gates_failed": self.gates_failed,
            "risk_level": self.risk_level,
            "duration_estimate": self.duration_estimate,
            "estimated_tokens": self.estimated_tokens,
            "what_worked": [o.to_dict() for o in self.what_worked],
            "what_didnt": [o.to_dict() for o in self.what_didnt],
            "knowledge_gaps": [g.to_dict() for g in self.knowledge_gaps],
            "roster_recommendations": [r.to_dict() for r in self.roster_recommendations],
            "sequencing_notes": [n.to_dict() for n in self.sequencing_notes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Retrospective:
        """Deserialise from a plain dict (e.g. loaded from JSON)."""
        return cls(
            task_id=data["task_id"],
            task_name=data.get("task_name", data["task_id"]),
            timestamp=data.get("timestamp", ""),
            agent_count=int(data.get("agent_count", 0)),
            retry_count=int(data.get("retry_count", 0)),
            gates_passed=int(data.get("gates_passed", 0)),
            gates_failed=int(data.get("gates_failed", 0)),
            risk_level=data.get("risk_level", "LOW"),
            duration_estimate=data.get("duration_estimate", ""),
            estimated_tokens=int(data.get("estimated_tokens", 0)),
            what_worked=[AgentOutcome.from_dict(o) for o in data.get("what_worked", [])],
            what_didnt=[AgentOutcome.from_dict(o) for o in data.get("what_didnt", [])],
            knowledge_gaps=[
                _knowledge_gap_from_dict(g) for g in data.get("knowledge_gaps", [])
            ],
            roster_recommendations=[
                RosterRecommendation.from_dict(r)
                for r in data.get("roster_recommendations", [])
            ],
            sequencing_notes=[
                SequencingNote.from_dict(n) for n in data.get("sequencing_notes", [])
            ],
        )

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
                # Support both KnowledgeGapRecord (new) and KnowledgeGap (old schema)
                if hasattr(gap, "agent_name") and gap.agent_name:
                    line += f" (agent: {gap.agent_name})"
                if hasattr(gap, "resolution"):
                    line += f" — *{gap.resolution}*"
                    if gap.resolution_detail:
                        line += f": {gap.resolution_detail}"
                elif hasattr(gap, "suggested_fix") and gap.suggested_fix:
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
