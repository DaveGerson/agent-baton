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
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.default_agents import (
    DEFAULT_AGENTS,
    MAX_AGENTS_BY_COMPLEXITY,
    MIN_PATTERN_CONFIDENCE,
)
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.roster_logic import (
    apply_retro_feedback,
    expand_agents_for_concerns,
    route_agents,
)
from agent_baton.core.engine.planning.utils.text_parsers import (
    infer_task_type,
    parse_concerns,
    parse_subtasks,
)

if TYPE_CHECKING:
    from agent_baton.core.engine.knowledge_resolver import StackProfile
    from agent_baton.models.pattern import LearnedPattern

logger = logging.getLogger(__name__)


class RosterStage:
    """Stage 2: settle on the agent roster for this plan."""

    name = "roster"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # Early-return when both agents AND phases were explicitly supplied.
        # The caller has fully specified the plan shape; we skip the
        # expensive pattern/retro/subtask/concern expansion and only do
        # agent routing (step 5e) so stack-flavored variants are resolved.
        if draft.agents is not None and draft.phases is not None:
            logger.debug(
                "RosterStage: explicit agents+phases supplied — skipping "
                "pattern/retro/decompose/expand, running routing only"
            )
            resolved_agents, agent_route_map = self._route_agents(
                resolved_agents=draft.resolved_agents,
                project_root=draft.project_root,
                routing_notes=draft.routing_notes,
                services=services,
            )
            draft.resolved_agents = resolved_agents
            draft.agent_route_map = agent_route_map
            return draft

        # Step 4+4b — pattern lookup + bead hints.
        pattern, resolved_agents, bead_hints = self._apply_pattern(
            task_summary=draft.task_summary,
            inferred_type=draft.inferred_type,
            stack_profile=draft.stack_profile,
            resolved_agents=draft.resolved_agents,
            agents=draft.agents,
            phases=draft.phases,
            task_classification=draft.task_classification,
            services=services,
        )
        draft.pattern = pattern
        draft.resolved_agents = resolved_agents
        draft.bead_hints = bead_hints

        # Step 5b — retrospective feedback.
        draft.resolved_agents, draft.retro_feedback = self._apply_retro(
            draft.resolved_agents,
            routing_notes=draft.routing_notes,
            services=services,
        )

        # Step 5c — compound task decomposition.
        subtask_data, resolved_agents = self._decompose_subtasks(
            task_summary=draft.task_summary,
            phases=draft.phases,
            agents=draft.agents,
            resolved_agents=draft.resolved_agents,
        )
        draft.subtask_data = subtask_data
        draft.resolved_agents = resolved_agents

        # Step 5d+5d-cap — cross-concern expansion + agent cap.
        draft.resolved_agents = self._expand_concerns(
            task_summary=draft.task_summary,
            inferred_complexity=draft.inferred_complexity,
            agents=draft.agents,
            resolved_agents=draft.resolved_agents,
            subtask_data=draft.subtask_data,
        )

        # Step 5e+6+6a — agent routing to stack-flavored variants.
        resolved_agents, agent_route_map = self._route_agents(
            resolved_agents=draft.resolved_agents,
            project_root=draft.project_root,
            routing_notes=draft.routing_notes,
            services=services,
        )
        draft.resolved_agents = resolved_agents
        draft.agent_route_map = agent_route_map
        return draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_pattern(
        self,
        *,
        task_summary: str,
        inferred_type: str,
        stack_profile: "StackProfile | None",
        resolved_agents: list[str],
        agents: list[str] | None,
        phases: list[dict] | None,
        task_classification: object | None,
        services: PlannerServices,
    ) -> "tuple[LearnedPattern | None, list[str], list]":
        """Steps 4 / 4b — pattern lookup and BeadAnalyzer bead hints."""
        pattern: "LearnedPattern | None" = None
        if not task_classification and not agents and not phases:
            try:
                stack_key = (
                    f"{stack_profile.language}/{stack_profile.framework}"
                    if stack_profile and stack_profile.framework
                    else (stack_profile.language if stack_profile else None)
                )
                candidates = services.pattern_learner.get_patterns_for_task(
                    inferred_type, stack=stack_key
                )
                for cand in candidates:
                    if cand.confidence >= MIN_PATTERN_CONFIDENCE:
                        pattern = cand
                        resolved_agents = list(pattern.recommended_agents)
                        break
            except Exception:
                pass

        _bead_hints: list = []
        if services.bead_store is not None:
            try:
                from agent_baton.core.learn.bead_analyzer import BeadAnalyzer
                _bead_hints = BeadAnalyzer().analyze(
                    services.bead_store, task_description=task_summary
                )
            except Exception:
                _bead_hints = []
        return pattern, resolved_agents, _bead_hints

    def _apply_retro(
        self,
        resolved_agents: list[str],
        *,
        routing_notes: list[str],
        services: PlannerServices,
    ) -> "tuple[list[str], object | None]":
        """Step 5b — retrospective feedback application.

        Returns ``(resolved_agents, retro_feedback)``.
        """
        retro_feedback = None
        if services.retro_engine is not None:
            try:
                retro_feedback = services.retro_engine.load_recent_feedback()
            except Exception:
                pass

        if retro_feedback is not None and retro_feedback.has_feedback():
            resolved_agents = apply_retro_feedback(
                resolved_agents, retro_feedback, routing_notes
            )
        return resolved_agents, retro_feedback

    def _decompose_subtasks(
        self,
        *,
        task_summary: str,
        phases: list[dict] | None,
        agents: list[str] | None,
        resolved_agents: list[str],
    ) -> tuple[list[dict] | None, list[str]]:
        """Step 5c — compound task decomposition."""
        _subtask_data: list[dict] | None = None
        if phases is None:
            subtasks = parse_subtasks(task_summary)
            if len(subtasks) >= 2:
                _subtask_data = []
                _explicit_agents = list(agents) if agents is not None else None
                for sub_idx, sub_text in subtasks:
                    st_type = infer_task_type(sub_text)
                    if _explicit_agents is not None:
                        st_agents = list(_explicit_agents)
                    else:
                        st_agents = list(DEFAULT_AGENTS.get(st_type, ["backend-engineer"]))
                        st_agents = expand_agents_for_concerns(
                            st_agents, sub_text
                        )
                    _subtask_data.append({
                        "index": sub_idx,
                        "text": sub_text,
                        "task_type": st_type,
                        "agents": st_agents,
                    })
                union_agents: list[str] = []
                for st in _subtask_data:
                    for a in st["agents"]:
                        if a not in union_agents:
                            union_agents.append(a)
                resolved_agents = union_agents
        return _subtask_data, resolved_agents

    def _expand_concerns(
        self,
        *,
        task_summary: str,
        inferred_complexity: str,
        agents: list[str] | None,
        resolved_agents: list[str],
        subtask_data: list[dict] | None,
    ) -> list[str]:
        """Steps 5d / 5d-cap — cross-concern expansion + complexity cap."""
        # Skip concern expansion when the caller supplied an explicit agents
        # list.  Expanding would add specialists (e.g. security-reviewer for
        # auth keywords) that then displace the caller's chosen agent from
        # the phase they implicitly care about.  Subtask decomposition already
        # handles its own per-subtask expansion before reaching this point,
        # so the subtask_data guard is still required for the non-explicit path.
        if agents is None and subtask_data is None:
            resolved_agents = expand_agents_for_concerns(
                resolved_agents, task_summary,
            )

        if agents is None:
            _agent_cap = MAX_AGENTS_BY_COMPLEXITY.get(inferred_complexity, 5)
            _early_concerns = parse_concerns(task_summary)
            if _early_concerns and len(_early_concerns) > _agent_cap:
                _agent_cap = len(_early_concerns)
                logger.debug(
                    "Concern-split detected (%d concerns) — raised agent cap "
                    "to %d to keep one specialist per concern (bd-076c).",
                    len(_early_concerns),
                    _agent_cap,
                )
            if len(resolved_agents) > _agent_cap:
                resolved_agents = resolved_agents[:_agent_cap]
        return resolved_agents

    def _route_agents(
        self,
        *,
        resolved_agents: list[str],
        project_root: Path | None,
        routing_notes: list[str],
        services: PlannerServices,
    ) -> tuple[list[str], dict[str, str]]:
        """Steps 5e / 6 / 6a — pre-routing snapshot + routing + route map."""
        _pre_routing_agents = list(resolved_agents)

        resolved_agents = route_agents(
            resolved_agents, project_root, services.router, routing_notes
        )

        _agent_route_map = dict(zip(_pre_routing_agents, resolved_agents))
        logger.debug(
            "Agent routing complete: %s",
            _agent_route_map if _agent_route_map else resolved_agents,
        )
        return resolved_agents, _agent_route_map
