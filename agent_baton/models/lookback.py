"""Data models for the ``baton lookback`` historical failure analysis tool.

These models carry the output of :class:`agent_baton.core.improve.lookback.LookbackAnalyzer`
and are serialised to JSON or rendered as markdown by the CLI command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FailureClassification:
    """A single classified failure mode found in an execution.

    Attributes:
        category: High-level failure bucket.  One of:
            ``PLAN_MISMATCH``, ``GATE_FAIL``, ``AGENT_ERROR``,
            ``SCOPE_OVERRUN``, ``ENV_FAILURE``, ``CONTEXT_EXHAUST``.
        subcategory: More specific label, e.g. ``"GATE_FAIL_TEST"``,
            ``"AGENT_ERROR_TRANSIENT"``.
        confidence: Classifier confidence, 0.0 – 1.0.
        affected_steps: Step IDs that contributed evidence for this class.
        affected_agents: Agent names involved.
        evidence: Human-readable strings drawn from raw data fields.
        recommended_action: Short description of the suggested fix.
    """

    category: str
    subcategory: str
    confidence: float
    affected_steps: list[str] = field(default_factory=list)
    affected_agents: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subcategory": self.subcategory,
            "confidence": self.confidence,
            "affected_steps": self.affected_steps,
            "affected_agents": self.affected_agents,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action,
        }


@dataclass
class RecurringPattern:
    """A failure pattern observed across multiple executions.

    Attributes:
        pattern_type: One of ``"agent_task_mismatch"``, ``"missing_gate"``,
            ``"scope_creep"``, ``"env_dep"``.
        description: Human-readable description of the pattern.
        frequency: How many distinct task_ids exhibited this pattern.
        total_occurrences: Total number of individual failure events.
        failure_rate: Fraction of analyzed executions that showed this
            pattern (0.0 – 1.0).
        affected_agents: Agent names most frequently involved.
        affected_task_types: Task type labels (from ``MachinePlan.task_type``).
        evidence_task_ids: Task IDs that contributed evidence.
        recommended_action: Short description of the suggested fix.
    """

    pattern_type: str
    description: str
    frequency: int
    total_occurrences: int
    failure_rate: float
    affected_agents: list[str] = field(default_factory=list)
    affected_task_types: list[str] = field(default_factory=list)
    evidence_task_ids: list[str] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "frequency": self.frequency,
            "total_occurrences": self.total_occurrences,
            "failure_rate": self.failure_rate,
            "affected_agents": self.affected_agents,
            "affected_task_types": self.affected_task_types,
            "evidence_task_ids": self.evidence_task_ids,
            "recommended_action": self.recommended_action,
        }


@dataclass
class LookbackRecommendation:
    """An actionable recommendation derived from the analysis.

    Attributes:
        action: What kind of change is recommended.  One of:
            ``"add_override"``, ``"add_gate"``, ``"split_task"``,
            ``"change_agent"``, ``"add_knowledge_pack"``.
        target: The agent name, gate type, or task type this applies to.
        detail: Full description of what should be changed and why.
        confidence: How certain the analyzer is (0.0 – 1.0).
        auto_applicable: True when the fix can be applied without human
            review (e.g. adding a learned-override entry).
        evidence_task_ids: Task IDs that motivated this recommendation.
    """

    action: str
    target: str
    detail: str
    confidence: float
    auto_applicable: bool
    evidence_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "detail": self.detail,
            "confidence": self.confidence,
            "auto_applicable": self.auto_applicable,
            "evidence_task_ids": self.evidence_task_ids,
        }


@dataclass
class LookbackReport:
    """Top-level output of a ``baton lookback`` run.

    Attributes:
        task_id: Non-None when the report covers a single task.
        query_range: (since, until) ISO 8601 strings when the report
            covers a date range, else None.
        executions_analyzed: How many executions were examined.
        failures_found: How many had at least one classified failure.
        classifications: Per-task or aggregate failure classifications.
        recurring_patterns: Cross-task patterns when ``analyze_range``
            was called.
        recommendations: Actionable suggestions derived from the analysis.
        token_waste_estimate: Rough token count attributed to avoidable
            failures (sum of ``estimated_tokens`` for failed steps).
        generated_at: ISO 8601 timestamp when the report was produced.
    """

    task_id: str | None
    query_range: tuple[str, str] | None
    executions_analyzed: int
    failures_found: int
    classifications: list[FailureClassification] = field(default_factory=list)
    recurring_patterns: list[RecurringPattern] = field(default_factory=list)
    recommendations: list[LookbackRecommendation] = field(default_factory=list)
    token_waste_estimate: int = 0
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "query_range": list(self.query_range) if self.query_range else None,
            "executions_analyzed": self.executions_analyzed,
            "failures_found": self.failures_found,
            "classifications": [c.to_dict() for c in self.classifications],
            "recurring_patterns": [p.to_dict() for p in self.recurring_patterns],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "token_waste_estimate": self.token_waste_estimate,
            "generated_at": self.generated_at,
        }
