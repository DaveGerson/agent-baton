"""Data model for learned orchestration patterns."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanStructureHint:
    """A structural recommendation for plan construction derived from bead analysis.

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    ``BeadAnalyzer`` produces these hints by mining historical bead data for
    recurring patterns (warning frequency, discovery clustering, decision
    reversals).  The planner applies hints during phase construction to
    proactively add review phases, pre-load context files, or insert approval
    gates before risky transitions.

    Attributes:
        hint_type: What structural change to apply.  One of:
            ``"add_review_phase"`` — insert a review phase before the next phase;
            ``"add_context_file"`` — pre-load a file into step context_files;
            ``"add_approval_gate"`` — require human approval on a phase.
        reason: Human-readable explanation of why this hint was generated.
        evidence_bead_ids: Bead IDs that contributed to this hint.
        metadata: Extra key/value data (e.g. ``{"file": "src/auth.py"}``
            for ``add_context_file`` hints).
    """

    hint_type: str  # "add_review_phase" | "add_context_file" | "add_approval_gate"
    reason: str
    evidence_bead_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a plain dict."""
        return {
            "hint_type": self.hint_type,
            "reason": self.reason,
            "evidence_bead_ids": self.evidence_bead_ids,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStructureHint":
        """Deserialise from a plain dict with backward-compatible defaults."""
        return cls(
            hint_type=data.get("hint_type", ""),
            reason=data.get("reason", ""),
            evidence_bead_ids=data.get("evidence_bead_ids", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class TeamPattern:
    """A recurring team composition pattern derived from usage logs.

    Tracks which agent combinations work well together as teams,
    enabling team-level learning separate from solo-agent patterns.
    The ``PatternLearner`` groups ``TaskUsageRecord`` entries by
    canonical agent tuple and computes effectiveness metrics.

    Attributes:
        pattern_id: Unique identifier, e.g. ``"team-arch-sec-001"``.
        agents: Canonical sorted list of agent names in the team.
        task_types: Task types where this team was used.
        success_rate: Fraction of tasks with outcome ``"SHIP"``.
        sample_size: Number of tasks where this team composition appeared.
        avg_token_cost: Mean estimated tokens per task for this team.
        confidence: Calibrated confidence score in ``[0.0, 1.0]``.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last refresh timestamp.
    """

    pattern_id: str
    agents: list[str]
    task_types: list[str] = field(default_factory=list)
    success_rate: float = 0.0
    sample_size: int = 0
    avg_token_cost: int = 0
    confidence: float = 0.0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "agents": self.agents,
            "task_types": self.task_types,
            "success_rate": self.success_rate,
            "sample_size": self.sample_size,
            "avg_token_cost": self.avg_token_cost,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeamPattern:
        return cls(
            pattern_id=data.get("pattern_id", ""),
            agents=data.get("agents", []),
            task_types=data.get("task_types", []),
            success_rate=float(data.get("success_rate", 0.0)),
            sample_size=int(data.get("sample_size", 0)),
            avg_token_cost=int(data.get("avg_token_cost", 0)),
            confidence=float(data.get("confidence", 0.0)),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class LearnedPattern:
    """A recurring pattern distilled from completed task usage logs.

    Attributes:
        pattern_id: Unique identifier, e.g. "phased_delivery-001".
        task_type: Inferred task category, e.g. "new-api-endpoint", "bug-fix".
            Derived from sequencing_mode until explicit tagging is available.
        stack: Technology stack qualifier, e.g. "python/fastapi". None means
            the pattern applies across all stacks.
        recommended_template: Human-readable phase template name or description
            of the workflow that worked best.
        recommended_agents: Ordered list of agent names in the most successful
            combination observed.
        confidence: Score in [0.0, 1.0] based on sample size and success rate.
            Formula: min(1.0, (sample_size / 15) * success_rate).
        sample_size: Number of task records that contributed to this pattern.
        success_rate: Fraction of tasks in this group with outcome=="SHIP".
        avg_token_cost: Mean estimated_tokens across successful tasks.
        evidence: task_ids that contributed to this pattern.
        created_at: ISO timestamp when the pattern was first generated.
        updated_at: ISO timestamp of the most recent refresh.
    """

    pattern_id: str
    task_type: str
    stack: str | None
    recommended_template: str
    recommended_agents: list[str]
    confidence: float
    sample_size: int
    success_rate: float
    avg_token_cost: int
    evidence: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "task_type": self.task_type,
            "stack": self.stack,
            "recommended_template": self.recommended_template,
            "recommended_agents": list(self.recommended_agents),
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "success_rate": self.success_rate,
            "avg_token_cost": self.avg_token_cost,
            "evidence": list(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LearnedPattern:
        return cls(
            pattern_id=data["pattern_id"],
            task_type=data["task_type"],
            stack=data.get("stack"),
            recommended_template=data.get("recommended_template", ""),
            recommended_agents=list(data.get("recommended_agents", [])),
            confidence=float(data.get("confidence", 0.0)),
            sample_size=int(data.get("sample_size", 0)),
            success_rate=float(data.get("success_rate", 0.0)),
            avg_token_cost=int(data.get("avg_token_cost", 0)),
            evidence=list(data.get("evidence", [])),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
