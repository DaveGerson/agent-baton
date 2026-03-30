"""Data models for task retrospectives.

Retrospectives are generated after each execution completes, capturing
what worked, what failed, knowledge gaps, roster recommendations, and
sequencing observations.  They feed the closed-loop improvement system
and the ``RetrospectiveFeedback`` model consumed by the planner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.knowledge import KnowledgeGapRecord


@dataclass
class AgentOutcome:
    """Outcome record for a single agent within a retrospective.

    Captures qualitative observations about the agent's performance
    during the task.  Used in the "What Worked" and "What Didn't"
    sections of the retrospective markdown.

    Attributes:
        name: Agent name.
        worked_well: Description of positive contributions.
        issues: Problems the agent encountered or caused.
        root_cause: Identified root cause of any issues.
    """

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
    """A gap in agent knowledge exposed during a task (legacy schema).

    Superseded by ``KnowledgeGapRecord`` which adds resolution tracking.
    Retained for backward compatibility with older retrospective files
    that use the ``affected_agent`` / ``suggested_fix`` field names.

    Attributes:
        description: What information was missing.
        affected_agent: Agent that lacked the knowledge.
        suggested_fix: Recommended remediation (e.g. "create knowledge
            pack", "update agent prompt").
    """

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
    """A recommendation about the agent roster from a retrospective.

    Consumed by the planner (via ``RetrospectiveFeedback``) to adjust
    which agents are preferred or excluded in future plans.

    Attributes:
        action: What to do — ``"create"``, ``"improve"``, ``"remove"``,
            ``"prefer"``, or ``"drop"``.
        target: Agent name or knowledge pack the recommendation applies to.
        reason: Why this recommendation was made.
    """

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
    """A note about the effectiveness of task sequencing.

    Records whether a specific phase or gate was valuable, helping
    the planner decide whether to include similar phases in future plans.

    Attributes:
        phase: Phase name or identifier this note refers to.
        observation: What happened (e.g. "gate caught issue X",
            "gate was unnecessary").
        keep: ``True`` if the phase should be retained in future plans.
    """

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


@dataclass
class TeamCompositionRecord:
    """Record of a team composition used during a task execution.

    Captures which agents collaborated as a team within a single step,
    their roles, and the outcome.  Aggregated across retrospectives,
    these records enable team-level learning: identifying which agent
    combinations produce the best results for specific task types.

    Attributes:
        step_id: Plan step where the team was used.
        agents: Sorted list of agent names in the team.
        roles: Mapping of agent name to role (lead/implementer/reviewer).
        outcome: ``"success"`` or ``"failure"`` based on team step result.
        task_type: Inferred task category for cross-task analysis.
        token_cost: Estimated total tokens consumed by the team step.
    """

    step_id: str
    agents: list[str]                           # sorted agent names
    roles: dict[str, str] = field(default_factory=dict)  # agent → role
    outcome: str = "success"                    # "success" | "failure"
    task_type: str | None = None
    token_cost: int = 0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "agents": self.agents,
            "roles": self.roles,
            "outcome": self.outcome,
            "task_type": self.task_type,
            "token_cost": self.token_cost,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeamCompositionRecord:
        return cls(
            step_id=data.get("step_id", ""),
            agents=data.get("agents", []),
            roles=data.get("roles", {}),
            outcome=data.get("outcome", "success"),
            task_type=data.get("task_type"),
            token_cost=data.get("token_cost", 0),
        )


@dataclass
class ConflictRecord:
    """Structured record of a disagreement between agents during execution.

    When agents produce conflicting outputs (e.g. a security reviewer
    flags an issue that an engineer dismisses), the conflict should be
    captured here rather than smoothed over by synthesis.  Conflicts
    are surfaced to the human for judgment and the resolution is
    recorded as a binding decision.

    Attributes:
        conflict_id: Unique identifier for this conflict.
        step_id: Plan step where the conflict occurred.
        agents: Agent names involved in the disagreement.
        positions: Mapping of agent name to their position/recommendation.
        evidence: Mapping of agent name to supporting evidence or citations.
        severity: ``"low"``, ``"medium"``, or ``"high"`` based on impact.
        resolution: How it was resolved — ``"human_decision"``,
            ``"auto_merged"``, or ``"unresolved"``.
        resolution_detail: The chosen resolution and rationale.
        resolved_by: Who resolved it — ``"human"``, ``"synthesis_agent"``,
            or ``"unresolved"``.
    """

    conflict_id: str
    step_id: str
    agents: list[str]
    positions: dict[str, str] = field(default_factory=dict)   # agent → position
    evidence: dict[str, str] = field(default_factory=dict)    # agent → evidence
    severity: str = "medium"                     # low | medium | high
    resolution: str = "unresolved"               # human_decision | auto_merged | unresolved
    resolution_detail: str = ""
    resolved_by: str = "unresolved"              # human | synthesis_agent | unresolved

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "step_id": self.step_id,
            "agents": self.agents,
            "positions": self.positions,
            "evidence": self.evidence,
            "severity": self.severity,
            "resolution": self.resolution,
            "resolution_detail": self.resolution_detail,
            "resolved_by": self.resolved_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ConflictRecord:
        return cls(
            conflict_id=data.get("conflict_id", ""),
            step_id=data.get("step_id", ""),
            agents=data.get("agents", []),
            positions=data.get("positions", {}),
            evidence=data.get("evidence", {}),
            severity=data.get("severity", "medium"),
            resolution=data.get("resolution", "unresolved"),
            resolution_detail=data.get("resolution_detail", ""),
            resolved_by=data.get("resolved_by", "unresolved"),
        )


def _knowledge_gap_from_dict(data: dict) -> KnowledgeGapRecord:
    """Deserialize a knowledge gap entry with backward compatibility.

    Handles both the legacy ``KnowledgeGap`` schema (``affected_agent``,
    ``suggested_fix``) and the current ``KnowledgeGapRecord`` schema.

    Args:
        data: Dict from a persisted retrospective JSON file.

    Returns:
        A ``KnowledgeGapRecord`` instance, with sensible defaults
        applied when reading from the old schema.
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
    """Structured retrospective for a completed orchestrated task.

    Generated by the ``RetrospectiveEngine`` after execution completes.
    Combines quantitative metrics with qualitative analysis of agent
    outcomes, knowledge gaps, and sequencing effectiveness.  Persisted
    as JSON and rendered as markdown via ``to_markdown()``.

    Attributes:
        task_id: Execution identifier.
        task_name: Human-readable task description.
        timestamp: ISO 8601 completion time.
        agent_count: Number of agents dispatched.
        retry_count: Total retries across all steps.
        gates_passed: Number of QA gates that passed.
        gates_failed: Number of QA gates that failed.
        risk_level: Risk tier assigned to the plan.
        duration_estimate: Estimated wall-clock duration.
        estimated_tokens: Total estimated token usage.
        what_worked: Agents that performed well, with details.
        what_didnt: Agents that had issues, with root causes.
        knowledge_gaps: Missing knowledge identified during execution.
        roster_recommendations: Suggestions for roster changes.
        sequencing_notes: Observations about phase ordering effectiveness.
        team_compositions: Team compositions used during execution,
            enabling team-level learning across retrospectives.
        conflicts: Structured disagreements between agents, captured
            for escalation and decision tracking.
    """

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

    # Team collaboration tracking
    team_compositions: list[TeamCompositionRecord] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)

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
            "team_compositions": [t.to_dict() for t in self.team_compositions],
            "conflicts": [c.to_dict() for c in self.conflicts],
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
            team_compositions=[
                TeamCompositionRecord.from_dict(t) for t in data.get("team_compositions", [])
            ],
            conflicts=[
                ConflictRecord.from_dict(c) for c in data.get("conflicts", [])
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

        if self.team_compositions:
            lines.append("## Team Compositions")
            for team in self.team_compositions:
                agents_str = ", ".join(team.agents)
                lines.append(f"- Step {team.step_id}: [{agents_str}] — {team.outcome}")
                if team.roles:
                    role_parts = [f"{a}: {r}" for a, r in team.roles.items()]
                    lines.append(f"  Roles: {', '.join(role_parts)}")
                if team.token_cost:
                    lines.append(f"  Tokens: {team.token_cost:,}")
            lines.append("")

        if self.conflicts:
            lines.append("## Conflicts")
            for conflict in self.conflicts:
                agents_str = " vs ".join(conflict.agents)
                lines.append(
                    f"- [{conflict.severity.upper()}] {agents_str} "
                    f"(step {conflict.step_id}) — {conflict.resolution}"
                )
                for agent, position in conflict.positions.items():
                    lines.append(f"  - **{agent}**: {position}")
                if conflict.resolution_detail:
                    lines.append(f"  Resolution: {conflict.resolution_detail}")
            lines.append("")

        return "\n".join(lines)
