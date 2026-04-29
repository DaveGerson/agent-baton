"""RiskStage — knowledge resolver setup + sensitivity classification + risk.

Owns legacy ``create_plan`` steps 8-9 in the original ordering:

* Step 6.5: ``_step_setup_knowledge`` — instantiate the knowledge
  resolver/ranker (when a ``KnowledgeRegistry`` is wired) and decide
  the per-step attachment cap.
* Step 7+8+8b: ``_step_classify_data`` — run the data sensitivity
  classifier, merge keyword + structural risk signals, and derive the
  git strategy.

These two run together because ``_step_classify_data`` consumes the
resolved roster and the resolver setup is cheap pre-classification work
that happens at the same point in the legacy ordering.
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices


class RiskStage:
    """Stage 3: knowledge setup + risk and sensitivity classification."""

    name = "risk"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 6.5 — knowledge resolver setup (graceful no-op when
        # no KnowledgeRegistry is wired).
        resolver, ranker, max_knowledge_per_step = legacy._step_setup_knowledge()
        draft.resolver = resolver
        draft.ranker = ranker
        draft.max_knowledge_per_step = max_knowledge_per_step

        # Step 7+8+8b — sensitivity, risk, git strategy.
        classification, risk_level, risk_level_enum, git_strategy = (
            legacy._step_classify_data(
                task_id=draft.task_id,
                task_summary=draft.task_summary,
                resolved_agents=draft.resolved_agents,
            )
        )
        draft.classification = classification
        draft.risk_level = risk_level
        draft.risk_level_enum = risk_level_enum
        draft.git_strategy = git_strategy
        return draft
