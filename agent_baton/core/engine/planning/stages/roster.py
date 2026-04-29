"""RosterStage — assemble the agent roster.

Owns legacy ``create_plan`` steps 4 through 7 in the original ordering:

* Step 4+4b: ``_step_apply_pattern`` — match a learned pattern and
  collect bead hints from the BeadAnalyzer.
* Step 5b:   ``_step_apply_retro`` — drop/prefer agents based on
  closed-loop retrospective feedback.
* Step 5c:   ``_step_decompose_subtasks`` — detect compound tasks
  and split the roster across them.
* Step 5d+5d-cap: ``_step_expand_concerns`` — add specialists for
  cross-concern signals; cap the roster by complexity tier.
* Step 5e+6+6a: ``_step_route_agents`` — route base agent names to
  stack-flavored variants (e.g. ``backend-engineer-python``).

The order is preserved because each step's input depends on the
previous step's output (resolved_agents is mutated repeatedly).
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices


class RosterStage:
    """Stage 2: settle on the agent roster for this plan."""

    name = "roster"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 4+4b — pattern lookup + bead hints.
        pattern, resolved_agents, bead_hints = legacy._step_apply_pattern(
            task_summary=draft.task_summary,
            inferred_type=draft.inferred_type,
            stack_profile=draft.stack_profile,
            resolved_agents=draft.resolved_agents,
            agents=draft.agents,
            phases=draft.phases,
        )
        draft.pattern = pattern
        draft.resolved_agents = resolved_agents
        draft.bead_hints = bead_hints

        # Step 5b — retrospective feedback.
        draft.resolved_agents = legacy._step_apply_retro(draft.resolved_agents)

        # Step 5c — compound task decomposition.
        subtask_data, resolved_agents = legacy._step_decompose_subtasks(
            task_summary=draft.task_summary,
            phases=draft.phases,
            agents=draft.agents,
            resolved_agents=draft.resolved_agents,
        )
        draft.subtask_data = subtask_data
        draft.resolved_agents = resolved_agents

        # Step 5d+5d-cap — cross-concern expansion + agent cap.
        draft.resolved_agents = legacy._step_expand_concerns(
            task_summary=draft.task_summary,
            inferred_complexity=draft.inferred_complexity,
            agents=draft.agents,
            resolved_agents=draft.resolved_agents,
            subtask_data=draft.subtask_data,
        )

        # Step 5e+6+6a — agent routing to stack-flavored variants.
        resolved_agents, agent_route_map = legacy._step_route_agents(
            resolved_agents=draft.resolved_agents,
            project_root=draft.project_root,
        )
        draft.resolved_agents = resolved_agents
        draft.agent_route_map = agent_route_map
        return draft
