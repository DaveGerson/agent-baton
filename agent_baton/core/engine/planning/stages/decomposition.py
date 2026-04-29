"""DecompositionStage — build phases, attach knowledge, apply foresight.

Owns legacy ``create_plan`` steps 10-12 in the original ordering:

* Step 9+9b:    ``_step_build_phases`` — pick the phase strategy
  (compound / explicit / classifier / pattern / complexity / default)
  and build the ``PlanPhase`` list.
* Step 9.5+9.6: ``_step_resolve_knowledge`` — attach knowledge
  documents to each step.
* Step 9.7+9.8: ``_step_apply_foresight`` — insert preventive steps
  for HIGH+ risk plans; re-resolve knowledge for inserted steps.

This stage is also where the **structured-spec quality fix** lives:
when the task summary contains an explicit "Phase 1: ... / Phase 2:
..." structure, the parser should recognize it and the planner should
honor those phase boundaries rather than flattening or exploding them.
The fix is wired by passing ``draft.structured_phase_spec`` (populated
by the parser called from ClassificationStage's structured-description
parse) into the phase builder.  When present, it overrides the
heuristic phase selection.
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices


class DecompositionStage:
    """Stage 4: build the phase list, attach knowledge, apply foresight."""

    name = "decomposition"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # If a structured phase spec was detected by ClassificationStage,
        # it has already been folded into ``draft.phases`` (the explicit
        # override path).  ``_step_build_phases`` honors explicit
        # phases first, so the structured spec wins automatically.

        # Step 9+9b — build phase list.
        plan_phases = legacy._step_build_phases(
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            inferred_type=draft.inferred_type,
            inferred_complexity=draft.inferred_complexity,
            complexity=draft.complexity,
            resolved_agents=draft.resolved_agents,
            phases=draft.phases,
            classified_phases=draft.classified_phases,
            pattern=draft.pattern,
            subtask_data=draft.subtask_data,
            agent_route_map=draft.agent_route_map,
        )
        draft.plan_phases = plan_phases

        # Step 9.5+9.6 — resolve knowledge attachments per step.
        legacy._step_resolve_knowledge(
            draft.plan_phases,
            resolver=draft.resolver,
            ranker=draft.ranker,
            max_knowledge_per_step=draft.max_knowledge_per_step,
            inferred_type=draft.inferred_type,
            risk_level=draft.risk_level,
            explicit_knowledge_packs=draft.explicit_knowledge_packs,
            explicit_knowledge_docs=draft.explicit_knowledge_docs,
        )

        # Step 9.7+9.8 — foresight (may rebuild plan_phases).
        draft.plan_phases = legacy._step_apply_foresight(
            draft.plan_phases,
            task_summary=draft.task_summary,
            risk_level=draft.risk_level,
            resolved_agents=draft.resolved_agents,
            resolver=draft.resolver,
            ranker=draft.ranker,
            max_knowledge_per_step=draft.max_knowledge_per_step,
            inferred_type=draft.inferred_type,
            explicit_knowledge_packs=draft.explicit_knowledge_packs,
            explicit_knowledge_docs=draft.explicit_knowledge_docs,
        )
        return draft
