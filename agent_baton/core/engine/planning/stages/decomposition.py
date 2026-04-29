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

import logging
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_templates import PHASE_NAMES as _PHASE_NAMES
from agent_baton.core.engine.planning.services import PlannerServices

if TYPE_CHECKING:
    from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)


class DecompositionStage:
    """Stage 4: build the phase list, attach knowledge, apply foresight."""

    name = "decomposition"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # If a structured phase spec was detected by ClassificationStage,
        # it has already been folded into ``draft.phases`` (the explicit
        # override path).  ``_build_phases`` honors explicit phases first,
        # so the structured spec wins automatically.

        # Step 9+9b — build phase list.
        draft.plan_phases = self._build_phases(
            draft=draft,
            services=services,
        )

        # Step 9.5+9.6 — resolve knowledge attachments per step.
        self._resolve_knowledge(
            plan_phases=draft.plan_phases,
            draft=draft,
            services=services,
        )

        # Step 9.7+9.8 — foresight (may rebuild plan_phases).
        draft.plan_phases = self._apply_foresight(
            plan_phases=draft.plan_phases,
            draft=draft,
            services=services,
        )
        return draft

    # ------------------------------------------------------------------
    # Private helpers — ported bodies of the three legacy _step_* methods
    # ------------------------------------------------------------------

    def _build_phases(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> list[PlanPhase]:
        """Steps 9 / 9b — phase construction and enrichment.

        Port of ``_LegacyIntelligentPlanner._step_build_phases``.
        Non-``_step_*`` helpers (_build_compound_phases, _default_phases,
        _apply_pattern, _build_phases_for_names, _phases_from_dicts,
        _enrich_phases, _assign_agents_to_phases) stay on the legacy object
        and are called via ``services.planner``.
        """
        legacy = services.planner
        task_id = draft.task_id
        task_summary = draft.task_summary
        inferred_type = draft.inferred_type
        inferred_complexity = draft.inferred_complexity
        complexity = draft.complexity
        resolved_agents = draft.resolved_agents
        phases = draft.phases
        classified_phases = draft.classified_phases
        pattern = draft.pattern
        subtask_data = draft.subtask_data
        agent_route_map = draft.agent_route_map

        # 9. Build phases
        if subtask_data is not None:
            # Compound task — each sub-task becomes its own phase
            plan_phases = legacy._build_compound_phases(
                subtask_data, agent_route_map,
            )
        elif phases is not None:
            plan_phases = legacy._phases_from_dicts(phases, resolved_agents, task_summary)
        elif classified_phases is not None:
            # Use classifier-provided phase names
            plan_phases = legacy._build_phases_for_names(
                classified_phases, resolved_agents, task_summary
            )
        elif pattern is not None:
            plan_phases = legacy._apply_pattern(pattern, inferred_type, task_summary)
            # Apply routed agent names to pattern-derived phases
            plan_phases = legacy._assign_agents_to_phases(plan_phases, resolved_agents, task_summary)
        elif complexity is not None:
            # Explicit complexity override — scale phases to match.
            # Use KeywordClassifier phase scaling so light/heavy produces
            # the right number of phases even in the legacy path.
            from agent_baton.core.engine.classifier import KeywordClassifier as _KC
            complexity_phases = _KC()._select_phases(inferred_type, inferred_complexity, _PHASE_NAMES)
            plan_phases = legacy._build_phases_for_names(complexity_phases, resolved_agents, task_summary)
        else:
            plan_phases = legacy._default_phases(inferred_type, resolved_agents, task_summary)

        logger.info(
            "Plan phases selected for task_id=%s: %s",
            task_id,
            [(p.name, [s.agent_name for s in p.steps]) for p in plan_phases],
        )

        # 9b. Enrich steps with cross-phase context and default deliverables
        plan_phases = legacy._enrich_phases(plan_phases, task_summary=task_summary)
        return plan_phases

    def _resolve_knowledge(
        self,
        *,
        plan_phases: list[PlanPhase],
        draft: PlanDraft,
        services: PlannerServices,
    ) -> None:
        """Steps 9.5 + 9.6 — knowledge resolution and gap-suggested attachments.

        Port of ``_LegacyIntelligentPlanner._step_resolve_knowledge``.
        Mutates *plan_phases* steps in place by setting ``step.knowledge``.
        """
        resolver = draft.resolver
        ranker = draft.ranker
        max_knowledge_per_step = draft.max_knowledge_per_step
        inferred_type = draft.inferred_type
        risk_level = draft.risk_level
        explicit_knowledge_packs = draft.explicit_knowledge_packs
        explicit_knowledge_docs = draft.explicit_knowledge_docs

        # 9.5. Resolve knowledge attachments for each step.
        # Runs after phase building so step.agent_name and task_description are final.
        # explicit_knowledge_packs/docs come from create_plan args (CLI --knowledge flags).
        # bd-0184: after resolving, rank by historical effectiveness and cap count.
        if resolver is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        resolved = resolver.resolve(
                            agent_name=step.agent_name,
                            task_description=step.task_description,
                            task_type=inferred_type,
                            risk_level=risk_level,
                            explicit_packs=explicit_knowledge_packs or [],
                            explicit_docs=explicit_knowledge_docs or [],
                        )
                        if ranker is not None:
                            resolved = ranker.rank(resolved)
                        step.knowledge = resolved[:max_knowledge_per_step]
                    except Exception:
                        logger.debug(
                            "Knowledge resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

        # 9.6. Gap-suggested attachments — query pattern learner for prior gaps
        # matching each step's agent + task type. Only runs when both resolver
        # and pattern learner are available.
        pattern_learner = services.pattern_learner
        if resolver is not None and pattern_learner is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        prior_gaps = pattern_learner.knowledge_gaps_for(
                            step.agent_name, inferred_type
                        )
                        for gap in prior_gaps:
                            matches = resolver.resolve(
                                agent_name=step.agent_name,
                                task_description=gap.description,
                            )
                            existing_paths = {a.path for a in step.knowledge if a.path}
                            for match in matches:
                                if match.path and match.path in existing_paths:
                                    continue
                                match.source = "gap-suggested"
                                step.knowledge.append(match)
                                if match.path:
                                    existing_paths.add(match.path)
                    except Exception:
                        logger.debug(
                            "Gap-suggested resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

    def _apply_foresight(
        self,
        *,
        plan_phases: list[PlanPhase],
        draft: PlanDraft,
        services: PlannerServices,
    ) -> list[PlanPhase]:
        """Steps 9.7 + 9.8 — foresight insertion and post-foresight
        knowledge resolution for inserted steps.

        Port of ``_LegacyIntelligentPlanner._step_apply_foresight``.
        Foresight may rebuild *plan_phases*, so this returns the new list.
        Writes ``services.planner._last_foresight_insights`` as a side effect
        (matching legacy behaviour so AssemblyStage can read it back).
        Also sets ``draft.foresight_insights`` for pipeline consumers.
        """
        task_summary = draft.task_summary
        risk_level = draft.risk_level
        resolved_agents = draft.resolved_agents
        resolver = draft.resolver
        ranker = draft.ranker
        max_knowledge_per_step = draft.max_knowledge_per_step
        inferred_type = draft.inferred_type
        explicit_knowledge_packs = draft.explicit_knowledge_packs
        explicit_knowledge_docs = draft.explicit_knowledge_docs

        foresight_engine = services.foresight_engine

        # 9.7. Foresight analysis — proactively insert preparatory steps
        # for predicted capability gaps, prerequisites, and edge cases.
        foresight_insights: list = []
        try:
            plan_phases, foresight_insights = foresight_engine.analyze(
                plan_phases,
                task_summary,
                risk_level=risk_level,
                existing_agents=resolved_agents,
            )
        except Exception:
            logger.debug(
                "Foresight analysis failed — skipping",
                exc_info=True,
            )

        # Write back introspection state on the legacy planner instance so
        # downstream bridge stages (AssemblyStage → _step_build_shared_context,
        # explain_plan, etc.) can still read ``_last_foresight_insights``.
        services.planner._last_foresight_insights = foresight_insights
        # Also store on the draft for future pipeline-native consumers.
        draft.foresight_insights = foresight_insights

        # 9.8. Resolve knowledge for foresight-inserted steps.
        # Foresight steps are inserted after the initial knowledge resolution
        # pass (9.5), so they need their own resolution pass.
        # bd-0184: also rank + cap foresight-step attachments.
        if resolver is not None and foresight_insights:
            foresight_step_ids: set[str] = set()
            for ins in foresight_insights:
                foresight_step_ids.update(ins.inserted_step_ids)
            for phase in plan_phases:
                for step in phase.steps:
                    if step.step_id in foresight_step_ids:
                        try:
                            resolved = resolver.resolve(
                                agent_name=step.agent_name,
                                task_description=step.task_description,
                                task_type=inferred_type,
                                risk_level=risk_level,
                                explicit_packs=explicit_knowledge_packs or [],
                                explicit_docs=explicit_knowledge_docs or [],
                            )
                            if ranker is not None:
                                resolved = ranker.rank(resolved)
                            step.knowledge = resolved[:max_knowledge_per_step]
                        except Exception:
                            logger.debug(
                                "Knowledge resolution failed for foresight step %s — skipping",
                                step.step_id,
                                exc_info=True,
                            )
        return plan_phases
