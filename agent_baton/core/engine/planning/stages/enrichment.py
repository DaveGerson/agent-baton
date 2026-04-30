"""EnrichmentStage — gates, approvals, team consolidation, bead hints, context.

Owns legacy ``create_plan`` steps 13-19 in the original ordering:

* Step 12+12.a:    ``_step_apply_gates`` — insert QA gates (pytest,
  lint, build) and apply project-config defaults.
* Step 12b+12b-bis: ``_step_apply_approval_gates`` — add approval
  gates on Design/Research at HIGH+ risk; concern-split implement
  phases when the summary names multiple concerns.
* Step 12d:        ``_apply_bead_hints`` — apply BeadAnalyzer
  recommendations (only when bead_hints non-empty).
* Step 13+13b+13c: ``_step_inject_context_files`` — inject context
  files, propagate model preferences, enrich with extracted paths.
* Step 13d:        ``_step_attach_prior_beads`` — scan summary for
  prior task references and attach their outcome beads.

NOTE: The score-check + budget-tier selection (legacy step 10+11) and
team consolidation + plan review (legacy step 12c) move to
``ValidationStage`` so quality enforcement is a single named gate, not
spread across enrichment.  ``ValidationStage`` runs BETWEEN enrichment
and assembly so it has the final phase shape to validate.

Non-``_step_*`` helpers that remain on the legacy class and are called
through ``services.planner``:
  - ``_default_gate``
  - ``_apply_project_config``
  - ``_parse_concerns``
  - ``_split_implement_phase_by_concerns``
  - ``_build_phases_for_names``
  - ``_detect_task_dependency``
  - ``_attach_prior_task_beads``
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices

if TYPE_CHECKING:
    from agent_baton.models.enums import RiskLevel
    from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)


class EnrichmentStage:
    """Stage 5: attach gates, approvals, bead hints, context, prior-task beads."""

    name = "enrichment"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # Step 12+12.a — QA gates + project-config defaults.
        self._apply_gates(
            draft.plan_phases,
            stack_profile=draft.stack_profile,
            gate_scope=draft.gate_scope,
            project_root=draft.project_root,
            services=services,
        )

        # Step 12b+12b-bis — approval gates + concern-split.
        split_phase_ids = self._apply_approval_gates(
            draft.plan_phases,
            risk_level_enum=draft.risk_level_enum,
            task_summary=draft.task_summary,
            resolved_agents=draft.resolved_agents,
            services=services,
        )
        draft.split_phase_ids = split_phase_ids

        # Step 12d — bead hints (conditional).
        if draft.bead_hints:
            draft.plan_phases = self._apply_bead_hints(
                draft.plan_phases, draft.bead_hints, services=services,
            )

        # Step 12c.4 — extract file paths from the task summary.  In the
        # legacy planner this happened inside ``_step_consolidate_team``
        # (now in ValidationStage) and was reused by ``_step_inject_context_files``
        # below.  Stage ordering (Enrichment → Validation) means we must
        # extract here so step 13c can see the paths; ValidationStage
        # re-extracts independently for its own plan-reviewer call.
        # ``_extract_file_paths`` is a pure helper still on the legacy
        # class — safe to call twice in one create_plan.
        draft.extracted_paths = services.planner._extract_file_paths(draft.task_summary)

        # Step 13+13b+13c — context file injection.
        self._inject_context_files(
            draft.plan_phases,
            default_model=draft.default_model,
            extracted_paths=draft.extracted_paths,
            services=services,
        )

        # Step 13d — prior-task bead attachment.
        depends_on_task_id = self._attach_prior_beads(
            draft.plan_phases,
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            services=services,
        )
        draft.depends_on_task_id = depends_on_task_id
        return draft

    # ------------------------------------------------------------------
    # Private methods — inlined from legacy _step_* bodies
    # ------------------------------------------------------------------

    def _apply_gates(
        self,
        plan_phases: list[Any],
        *,
        stack_profile: Any,
        gate_scope: Any,
        project_root: Path | None,
        services: PlannerServices,
    ) -> None:
        """Steps 12 / 12.a — QA gate decoration + project-config overlay.

        Mutates *plan_phases* in place.  Body ported from
        ``_LegacyIntelligentPlanner._step_apply_gates``.
        """
        legacy = services.planner

        # 12. Add QA gates (stack-aware, bd-124f: scoped to changed paths)
        for phase in plan_phases:
            if phase.gate is None:
                # Collect changed source paths from all steps in this phase.
                # Use allowed_paths (sandbox write paths) as the best signal
                # for what files the phase will modify.
                phase_changed: list[str] = []
                for _step in phase.steps:
                    phase_changed.extend(_step.allowed_paths)
                phase.gate = legacy._default_gate(
                    phase.name,
                    stack=stack_profile,
                    changed_paths=phase_changed or None,
                    gate_scope=gate_scope,
                    project_root=project_root,
                )

        # 12.a. Apply project config (baton.yaml) defaults — additive.
        # No-op when no baton.yaml is present in the project.
        try:
            legacy._apply_project_config(plan_phases)
        except Exception:
            logger.warning(
                "Applying project config failed — continuing without it",
                exc_info=True,
            )

    def _apply_approval_gates(
        self,
        plan_phases: list[Any],
        *,
        risk_level_enum: Any,
        task_summary: str,
        resolved_agents: list[str],
        services: PlannerServices,
    ) -> set[int]:
        """Steps 12b / 12b-bis — approval gates and concern-splitting.

        Mutates *plan_phases* in place.  Returns the set of phase ids
        that were split (so step 12c can skip team-consolidation on them).
        Body ported from ``_LegacyIntelligentPlanner._step_apply_approval_gates``.
        """
        from agent_baton.models.enums import RiskLevel  # local import avoids circularity

        legacy = services.planner

        # 12b. Set approval gates on critical phases for HIGH+ risk
        if risk_level_enum in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            for phase in plan_phases:
                if phase.name.lower() in ("design", "research"):
                    phase.approval_required = True
                    phase.approval_description = (
                        f"Review {phase.name.lower()} output before "
                        f"implementation begins. Approve to continue, "
                        f"reject to stop, or approve-with-feedback to "
                        f"add remediation steps."
                    )

        # 12b-bis. Concern-splitting: when the task summary names ≥3 distinct
        # concerns/modules (e.g. "F0.1 ... F0.2 ... F0.3 ... F0.4 ..."), split
        # implement-type phases into one parallel single-agent step per
        # concern.  This runs BEFORE team consolidation so the planner emits
        # parallel steps instead of a single bundled team step.
        # See feedback_planner_parallelization.md.
        _concerns = legacy._parse_concerns(task_summary)
        _split_phase_ids: set[int] = set()
        if _concerns:
            logger.debug(
                "Detected %d concerns in task summary: %s",
                len(_concerns),
                [c[0] for c in _concerns],
            )
            for phase in plan_phases:
                if phase.name.lower() in ("implement", "fix", "draft", "migrate"):
                    legacy._split_implement_phase_by_concerns(
                        phase, _concerns, resolved_agents, task_summary,
                    )
                    _split_phase_ids.add(phase.phase_id)
        return _split_phase_ids

    def _apply_bead_hints(
        self,
        plan_phases: list[Any],
        hints: list[Any],
        *,
        services: PlannerServices,
    ) -> list[Any]:
        """Apply :class:`~agent_baton.models.pattern.PlanStructureHint` objects to phases.

        Three hint types are handled:

        - ``add_context_file``: Append the hinted file to every step's
          ``context_files`` (deduplicated).
        - ``add_review_phase``: Insert a review phase before the first
          non-design, non-research phase (idempotent — skipped if a review
          phase already exists).
        - ``add_approval_gate``: Mark the first non-design phase as
          requiring human approval if it is not already gated.

        Body ported from ``_LegacyIntelligentPlanner._apply_bead_hints``.
        """
        legacy = services.planner

        for hint in hints:
            try:
                if hint.hint_type == "add_context_file":
                    file_path = hint.metadata.get("file", "")
                    if file_path:
                        for phase in plan_phases:
                            for step in phase.steps:
                                if file_path not in step.context_files:
                                    step.context_files.append(file_path)

                elif hint.hint_type == "add_review_phase":
                    # Skip if a review phase already exists.
                    has_review = any(
                        p.name.lower() == "review" for p in plan_phases
                    )
                    if not has_review and plan_phases:
                        # Build a minimal review phase using the last agent.
                        # Use max existing phase_id + 1 to avoid duplicate IDs.
                        last_agent = "code-reviewer"
                        if plan_phases[-1].steps:
                            last_agent = plan_phases[-1].steps[-1].agent_name
                        next_id = max(p.phase_id for p in plan_phases) + 1
                        review_phase = legacy._build_phases_for_names(
                            ["Review"], [last_agent], "Review bead-flagged concerns",
                            start_phase_id=next_id,
                        )
                        plan_phases.extend(review_phase)

                elif hint.hint_type == "add_approval_gate":
                    # Add approval_required to the first non-design phase.
                    for phase in plan_phases:
                        if phase.name.lower() not in ("design", "research", "investigate"):
                            if not phase.approval_required:
                                phase.approval_required = True
                                phase.approval_description = (
                                    "Bead analysis detected decision reversals — "
                                    "review before proceeding. "
                                    "Approve to continue, reject to stop."
                                )
                            break
            except Exception as _hint_exc:
                logger.debug(
                    "_apply_bead_hints: hint %s failed (non-fatal): %s",
                    hint.hint_type, _hint_exc,
                )

        return plan_phases

    def _inject_context_files(
        self,
        plan_phases: list[Any],
        *,
        default_model: str | None,
        extracted_paths: list[str],
        services: PlannerServices,
    ) -> None:
        """Steps 13 / 13b / 13c — context files, model inheritance, richness.

        Mutates *plan_phases* in place.  Body ported from
        ``_LegacyIntelligentPlanner._step_inject_context_files``.
        """
        registry = services.registry

        # 13. Populate context_files — every agent should read CLAUDE.md
        for phase in plan_phases:
            for step in phase.steps:
                if not step.context_files:
                    step.context_files = ["CLAUDE.md"]

        # 13b. Model inheritance — inherit model preference from agent definition.
        # Priority: agent definition model > explicit default_model > "sonnet".
        for phase in plan_phases:
            for step in phase.steps:
                agent_def = registry.get(step.agent_name)
                if agent_def and agent_def.model:
                    step.model = agent_def.model
                elif default_model:
                    step.model = default_model
                # Also propagate to team members
                for member in step.team:
                    member_def = registry.get(member.agent_name)
                    if member_def and member_def.model:
                        member.model = member_def.model
                    elif default_model:
                        member.model = default_model

        # 13c. Context richness — append extracted file paths (from 12c.4)
        # to every step's context_files (deduplicated).
        if extracted_paths:
            for phase in plan_phases:
                for step in phase.steps:
                    existing = set(step.context_files)
                    for path in extracted_paths:
                        if path not in existing:
                            step.context_files.append(path)
                            existing.add(path)

    def _attach_prior_beads(
        self,
        plan_phases: list[Any],
        *,
        task_id: str,
        task_summary: str,
        services: PlannerServices,
    ) -> str | None:
        """E7 dependency detection (step 13d).

        Returns the detected ``depends_on_task_id`` (or ``None``) and,
        when present, attaches the prior task's outcome beads to
        *plan_phases* in-place.  Body ported from
        ``_LegacyIntelligentPlanner._step_attach_prior_beads``.
        """
        legacy = services.planner
        bead_store = services.bead_store

        depends_on_task_id: str | None = None
        if bead_store is not None:
            depends_on_task_id = legacy._detect_task_dependency(task_summary)
            if depends_on_task_id is not None:
                logger.info(
                    "E7 dependency detected: task_id=%s depends on prior task %s",
                    task_id,
                    depends_on_task_id,
                )
                legacy._attach_prior_task_beads(
                    plan_phases, depends_on_task_id
                )
        return depends_on_task_id
