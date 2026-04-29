"""PlanDraft — the mutable working state that flows through the pipeline.

Replaces the private ``_CreatePlanState`` dataclass that lived inside
``planner.py``.  Every stage reads/writes fields here; nothing lives on
``IntelligentPlanner`` self-state during plan construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_baton.core.engine.classifier import (
        ClassificationResult,
        TaskClassification,
    )
    from agent_baton.core.engine.foresight import ForesightInsight
    from agent_baton.core.engine.knowledge_resolver import StackProfile
    from agent_baton.core.engine.plan_reviewer import PlanReviewResult
    from agent_baton.core.engine.strategies import LearnedPattern
    from agent_baton.core.govern.policy import PolicyViolation
    from agent_baton.core.improve.retrospective import RetrospectiveFeedback
    from agent_baton.models.execution import (
        GateScope,
        PlanPhase,
        RiskLevel,
    )


@dataclass
class PlanDraft:
    """Working state for a single ``create_plan`` call.

    Each ``_step_*`` method on the legacy planner wrote into a private
    ``_CreatePlanState`` dataclass.  ``PlanDraft`` is the public version
    of that — every pipeline stage reads from and writes to it.

    Field grouping mirrors the order they are populated by stages:
    inputs first, then classification, then decomposition, etc.
    """

    # --- Inputs (snapshotted on entry; never mutated by stages) ---
    task_summary: str = ""
    task_type: str | None = None
    complexity: str | None = None
    project_root: Path | None = None
    agents: list[str] | None = None
    phases: list[dict] | None = None
    explicit_knowledge_packs: list[str] | None = None
    explicit_knowledge_docs: list[str] | None = None
    intervention_level: str = "low"
    default_model: str | None = None
    gate_scope: "GateScope" = "focused"

    # --- Observability ---
    otel_exporter: Any = None
    otel_started_at: datetime | None = None

    # --- ClassificationStage outputs ---
    task_id: str = ""
    stack_profile: "StackProfile | None" = None
    classified_phases: list[str] | None = None
    inferred_type: str = ""
    inferred_complexity: str = "medium"
    classification: "ClassificationResult | None" = None
    task_classification: "TaskClassification | None" = None
    keyword_risk_level: str = ""
    risk_level: str = ""
    risk_level_enum: "RiskLevel | None" = None
    git_strategy: str = ""

    # --- DecompositionStage outputs ---
    pattern: "LearnedPattern | None" = None
    bead_hints: list = field(default_factory=list)
    subtask_data: list[dict] | None = None
    structured_phase_spec: list[dict] | None = None
    plan_phases: list["PlanPhase"] = field(default_factory=list)
    concerns: list = field(default_factory=list)
    split_phase_ids: set[int] = field(default_factory=set)

    # --- RoutingStage outputs ---
    resolved_agents: list[str] = field(default_factory=list)
    pre_routing_agents: list[str] = field(default_factory=list)
    agent_route_map: dict[str, str] = field(default_factory=dict)
    retro_feedback: "RetrospectiveFeedback | None" = None

    # --- EnrichmentStage outputs ---
    resolver: Any = None
    ranker: Any = None
    max_knowledge_per_step: int = 8
    foresight_insights: list["ForesightInsight"] = field(default_factory=list)
    extracted_paths: list[str] = field(default_factory=list)
    depends_on_task_id: str | None = None

    # --- ValidationStage outputs ---
    score_warnings: list[str] = field(default_factory=list)
    routing_notes: list[str] = field(default_factory=list)
    policy_violations: list["PolicyViolation"] = field(default_factory=list)
    review_result: "PlanReviewResult | None" = None
    budget_tier: str = "standard"

    # --- AssemblyStage outputs ---
    classification_signals: str | None = None
    classification_confidence: float | None = None

    @classmethod
    def from_inputs(
        cls,
        task_summary: str,
        *,
        task_type: str | None = None,
        complexity: str | None = None,
        project_root: Path | None = None,
        agents: list[str] | None = None,
        phases: list[dict] | None = None,
        explicit_knowledge_packs: list[str] | None = None,
        explicit_knowledge_docs: list[str] | None = None,
        intervention_level: str = "low",
        default_model: str | None = None,
        gate_scope: "GateScope" = "focused",
    ) -> "PlanDraft":
        """Build a fresh draft from create_plan kwargs."""
        return cls(
            task_summary=task_summary,
            task_type=task_type,
            complexity=complexity,
            project_root=project_root,
            agents=agents,
            phases=phases,
            explicit_knowledge_packs=explicit_knowledge_packs,
            explicit_knowledge_docs=explicit_knowledge_docs,
            intervention_level=intervention_level,
            default_model=default_model,
            gate_scope=gate_scope,
        )
