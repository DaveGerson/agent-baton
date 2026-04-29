"""ClassificationStage — initialize state + classify task.

Owns legacy ``create_plan`` steps 1-2 in the original ordering:

* Step 1+2+2b: ``_step_initialize_state`` — task_id, stack detection,
  structured-description parsing of the inputs.
* Step 3:     ``_step_classify_task`` — task_type, complexity,
  resolved_agents (initial pass), classified_phases.

The logic formerly bridged through ``services.planner._step_initialize_state``
and ``services.planner._step_classify_task`` is now owned by this stage.
Helpers that are not ``_step_*`` (``_parse_structured_description``,
``_infer_task_type``, ``_generate_task_id``) still live on the legacy class
and are called through ``services.planner``; later commits will pull them out.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.orchestration.router import is_reviewer_agent

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.structured_spec import enrich_phase_titles

if TYPE_CHECKING:
    from agent_baton.core.orchestration.router import StackProfile

logger = logging.getLogger(__name__)

# Default agents by task type when no pattern is found (mirrors legacy constant).
_DEFAULT_AGENTS: dict[str, list[str]] = {
    "new-feature": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "bug-fix": ["backend-engineer", "test-engineer"],
    "refactor": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "data-analysis": ["architect", "data-analyst"],
    "documentation": ["architect", "talent-builder", "code-reviewer"],
    "migration": ["architect", "backend-engineer", "test-engineer", "code-reviewer", "auditor"],
    "test": ["test-engineer"],
    # E3 — fallback for unknown/generic tasks: default four-phase roster
    "generic": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
}


class ClassificationStage:
    """Stage 1: figure out what this task is."""

    name = "classification"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # Step 1+2+2b — task_id, stack profile, structured-description
        # parsing.  ``phases`` and ``agents`` may be mutated when the
        # summary contains an explicit phase spec, so we read both back.
        task_id, stack_profile, phases_after, agents_after = self._initialize_state(
            task_summary=draft.task_summary,
            project_root=draft.project_root,
            phases=draft.phases,
            agents=draft.agents,
            services=services,
        )
        draft.task_id = task_id
        draft.stack_profile = stack_profile
        draft.phases = phases_after
        draft.agents = agents_after

        # QUALITY FIX #1 — enrich phase titles parsed from a structured
        # spec.  The legacy parser detects "Phase 1: Authentication" but
        # produces a phase named just "Phase 1", losing the title.
        # ``enrich_phase_titles`` replaces those generic names with
        # "Phase 1: Authentication" so the operator can correlate baton
        # phases with their spec phases — addressing one root cause of
        # the plan-explosion incident
        # (docs/internal/competitive-audit/INCIDENT-plan-explosion.md).
        if draft.phases:
            draft.phases = enrich_phase_titles(draft.phases, draft.task_summary)

        # Step 3 — classify task: infer task_type, complexity, agents,
        # phases (Haiku classifier when available, keyword fallback
        # otherwise).
        inferred_type, inferred_complexity, resolved_agents, classified_phases = (
            self._classify_task(
                task_summary=draft.task_summary,
                task_type=draft.task_type,
                complexity=draft.complexity,
                project_root=draft.project_root,
                agents=draft.agents,
                phases=draft.phases,
                services=services,
            )
        )
        draft.inferred_type = inferred_type
        draft.inferred_complexity = inferred_complexity
        draft.resolved_agents = resolved_agents
        draft.classified_phases = classified_phases
        return draft

    # ------------------------------------------------------------------
    # Private helpers — ported from _LegacyIntelligentPlanner._step_*
    # ------------------------------------------------------------------

    def _initialize_state(
        self,
        *,
        task_summary: str,
        project_root: Path | None,
        phases: list[dict] | None,
        agents: list[str] | None,
        services: PlannerServices,
    ) -> "tuple[str, StackProfile | None, list[dict] | None, list[str] | None]":
        """Steps 1 / 2 / 2b — task id, stack detection, structured parse.

        Returns ``(task_id, stack_profile, phases, agents)``.  *phases*
        and *agents* are returned because step 2b may overwrite them
        from the structured-description parse.
        """
        # 1. Task ID
        task_id = services.planner._generate_task_id(task_summary)

        # 2. Detect stack (best effort) — needed before agent resolution
        stack_profile = None
        if project_root is not None:
            try:
                stack_profile = services.router.detect_stack(project_root)
            except Exception:
                pass

        # 2b. Parse structured descriptions — extract phases and agent hints
        # before falling through to the classifier/keyword path.
        parsed_phases, parsed_agents = services.planner._parse_structured_description(
            task_summary
        )
        if parsed_phases is not None:
            phases = parsed_phases
        if parsed_agents is not None and agents is None:
            agents = parsed_agents
        return task_id, stack_profile, phases, agents

    def _classify_task(
        self,
        *,
        task_summary: str,
        task_type: str | None,
        complexity: str | None,
        project_root: Path | None,
        agents: list[str] | None,
        phases: list[dict] | None,
        services: PlannerServices,
    ) -> "tuple[str, str, list[str], list[str] | None]":
        """Step 3 — task classification (auto path or explicit-override path).

        Returns ``(inferred_type, inferred_complexity, resolved_agents,
        classified_phases)``.  ``services.planner._last_task_classification``
        is set as a side effect on the auto-classify branch (tests read it).
        """
        # 3. Classify — determines task_type, complexity, agents, and phases.
        # Explicit overrides take precedence over the classifier.
        # When complexity is explicitly provided, the caller is overriding
        # classification — use the keyword path so phases are scaled to
        # match the explicit complexity rather than the classifier's guess.
        classified_phases: list[str] | None = None
        if task_type is None and agents is None and phases is None and complexity is None:
            task_cls = services.task_classifier.classify(
                task_summary, services.registry, project_root
            )
            services.planner._last_task_classification = task_cls
            inferred_type = task_cls.task_type
            inferred_complexity = task_cls.complexity
            resolved_agents = list(task_cls.agents)
            classified_phases = list(task_cls.phases)
            logger.debug(
                "Task classified: type=%s complexity=%s agents=%s phases=%s source=%s",
                inferred_type,
                inferred_complexity,
                resolved_agents,
                classified_phases,
                task_cls.source,
            )
        else:
            inferred_type = task_type or services.planner._infer_task_type(task_summary)
            inferred_complexity = complexity or "medium"
            classified_phases = None  # let downstream logic handle phases
            # 5. Agent selection (legacy path for explicit overrides)
            if agents is None:
                resolved_agents = list(_DEFAULT_AGENTS.get(inferred_type, []))
            else:
                resolved_agents = list(agents)
                # Warn when an explicit override includes reviewer-class agents
                # — they may still appear in review/gate phases, but the
                # implement-phase team-step will filter them out (see
                # _consolidate_team_step).  Surface this so users aren't
                # surprised when the auditor doesn't show up as an implementer.
                _override_reviewers = [
                    a for a in resolved_agents if is_reviewer_agent(a)
                ]
                if _override_reviewers:
                    logger.warning(
                        "--agents override includes reviewer-class agent(s) %s; "
                        "they will be excluded from implement-phase team steps "
                        "(reviewers belong in review/gate phases only)",
                        _override_reviewers,
                    )
            logger.debug(
                "Task classification (override path): type=%s complexity=%s agents=%s",
                inferred_type,
                inferred_complexity,
                resolved_agents,
            )
        return inferred_type, inferred_complexity, resolved_agents, classified_phases
