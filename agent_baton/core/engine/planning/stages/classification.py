"""ClassificationStage — initialize state + classify task.

Owns legacy ``create_plan`` steps 1-2 in the original ordering:

* Step 1+2+2b: ``_step_initialize_state`` — task_id, stack detection,
  structured-description parsing of the inputs.
* Step 3:     ``_step_classify_task`` — task_type, complexity,
  resolved_agents (initial pass), classified_phases.

This commit delegates to the legacy ``_step_*`` methods via
``services.planner``.  A follow-up commit ports the bodies in-place
and removes those legacy methods.
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices


class ClassificationStage:
    """Stage 1: figure out what this task is."""

    name = "classification"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 1+2+2b — task_id, stack profile, structured-description
        # parsing.  ``phases`` and ``agents`` may be mutated when the
        # summary contains an explicit phase spec, so we read both back.
        task_id, stack_profile, phases_after, agents_after = legacy._step_initialize_state(
            task_summary=draft.task_summary,
            project_root=draft.project_root,
            phases=draft.phases,
            agents=draft.agents,
        )
        draft.task_id = task_id
        draft.stack_profile = stack_profile
        draft.phases = phases_after
        draft.agents = agents_after

        # Step 3 — classify task: infer task_type, complexity, agents,
        # phases (Haiku classifier when available, keyword fallback
        # otherwise).
        inferred_type, inferred_complexity, resolved_agents, classified_phases = (
            legacy._step_classify_task(
                task_summary=draft.task_summary,
                task_type=draft.task_type,
                complexity=draft.complexity,
                project_root=draft.project_root,
                agents=draft.agents,
                phases=draft.phases,
            )
        )
        draft.inferred_type = inferred_type
        draft.inferred_complexity = inferred_complexity
        draft.resolved_agents = resolved_agents
        draft.classified_phases = classified_phases
        return draft
