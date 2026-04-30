"""PlannerServices — DI container for optional planner dependencies.

The legacy ``IntelligentPlanner`` carried 12+ optional collaborators on
``self`` (registry, scorer, retro engine, knowledge registry, etc.).
Stages need a subset of these; we collect them in one frozen container
that is passed alongside the draft.

Design choices:

* All fields default to ``None`` so a minimal planner can be constructed
  for tests.  Stages must check before use.
* Required services (registry, router, scorer, etc.) are populated by
  ``IntelligentPlanner.__init__``.  Optional services (classifier,
  policy_engine, retro_engine, knowledge_registry, bead_store) are
  ``None`` when the caller did not supply them.
* The container is frozen — stages never reassign services mid-run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_baton.core.config import ProjectConfig
    from agent_baton.core.engine.classifier import TaskClassifier
    from agent_baton.core.engine.foresight import ForesightEngine
    from agent_baton.core.engine.plan_reviewer import PlanReviewer
    from agent_baton.core.govern.classifier import DataClassifier
    from agent_baton.core.govern.policy import PolicyEngine
    from agent_baton.core.improve.retrospective import RetrospectiveEngine
    from agent_baton.core.knowledge.registry import KnowledgeRegistry
    from agent_baton.core.optimize.budget_tuner import BudgetTuner
    from agent_baton.core.optimize.pattern_learner import PatternLearner
    from agent_baton.core.optimize.scorer import PerformanceScorer
    from agent_baton.core.routing.agent_registry import AgentRegistry
    from agent_baton.core.routing.agent_router import AgentRouter


@dataclass(frozen=True)
class PlannerServices:
    """Container for the collaborators a stage may need.

    Stages access services by attribute and must tolerate ``None`` for
    optional ones.  The container is constructed once per
    ``IntelligentPlanner`` instance (not per ``create_plan`` call).
    """

    # --- Always populated ---
    registry: "AgentRegistry"
    router: "AgentRouter"
    scorer: "PerformanceScorer"
    pattern_learner: "PatternLearner"
    budget_tuner: "BudgetTuner"
    task_classifier: "TaskClassifier"
    foresight_engine: "ForesightEngine"
    plan_reviewer: "PlanReviewer"
    project_config: "ProjectConfig"

    # --- Optional ---
    data_classifier: "DataClassifier | None" = None
    policy_engine: "PolicyEngine | None" = None
    retro_engine: "RetrospectiveEngine | None" = None
    knowledge_registry: "KnowledgeRegistry | None" = None
    bead_store: Any = None
    team_context_root: Path | None = None
