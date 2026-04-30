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
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.context import (
    attach_prior_task_beads,
    detect_task_dependency,
)
from agent_baton.core.engine.planning.utils.gates import (
    apply_project_config,
    default_gate,
)
from agent_baton.core.engine.planning.utils.phase_builder import (
    build_phases_for_names,
    split_implement_phase_by_concerns,
)
from agent_baton.core.engine.planning.utils.text_parsers import (
    extract_file_paths,
    parse_concerns,
)

from agent_baton.models.enums import RiskLevel

if TYPE_CHECKING:
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
            isolation_overrides=draft.isolation_overrides,
        )

        # Step 12b+12b-bis — approval gates + concern-split.
        split_phase_ids = self._apply_approval_gates(
            draft.plan_phases,
            risk_level_enum=draft.risk_level_enum,
            task_summary=draft.task_summary,
            resolved_agents=draft.resolved_agents,
        )
        draft.split_phase_ids = split_phase_ids

        # Step 12d — bead hints (conditional).
        if draft.bead_hints:
            draft.plan_phases = self._apply_bead_hints(
                draft.plan_phases, draft.bead_hints, services=services,
            )

        # Step 12c.4 — extract file paths from the task summary.  Done here
        # so step 13c can see the paths; ValidationStage re-extracts
        # independently for its own plan-reviewer call.
        draft.extracted_paths = extract_file_paths(draft.task_summary)

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

        # Post-enrichment safety net: HIGH/CRITICAL tasks must always have a
        # terminal Review phase, regardless of whether complexity cap truncated
        # code-reviewer from the roster before phase construction.
        self._ensure_review_phase(draft, services)

        return draft

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _apply_gates(
        self,
        plan_phases: list[Any],
        *,
        stack_profile: Any,
        gate_scope: Any,
        project_root: Path | None,
        services: PlannerServices,
        isolation_overrides: dict[str, str],
    ) -> None:
        """Steps 12 / 12.a — QA gate decoration + project-config overlay."""
        for phase in plan_phases:
            if phase.gate is None:
                phase_changed: list[str] = []
                for _step in phase.steps:
                    phase_changed.extend(_step.allowed_paths)
                phase.gate = default_gate(
                    phase.name,
                    stack=stack_profile,
                    changed_paths=phase_changed or None,
                    gate_scope=gate_scope,
                    project_root=project_root,
                )

        try:
            apply_project_config(
                plan_phases, services.project_config, isolation_overrides,
            )
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
    ) -> set[int]:
        """Steps 12b / 12b-bis — approval gates and concern-splitting."""
        from agent_baton.models.enums import RiskLevel

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

        _concerns = parse_concerns(task_summary)
        _split_phase_ids: set[int] = set()
        if _concerns:
            logger.debug(
                "Detected %d concerns in task summary: %s",
                len(_concerns),
                [c[0] for c in _concerns],
            )
            for phase in plan_phases:
                if phase.name.lower() in ("implement", "fix", "draft", "migrate"):
                    split_implement_phase_by_concerns(
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
        """Apply BeadAnalyzer hint objects to phases."""
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
                    has_review = any(
                        p.name.lower() == "review" for p in plan_phases
                    )
                    if not has_review and plan_phases:
                        last_agent = "code-reviewer"
                        if plan_phases[-1].steps:
                            last_agent = plan_phases[-1].steps[-1].agent_name
                        next_id = max(p.phase_id for p in plan_phases) + 1
                        review_phase = build_phases_for_names(
                            ["Review"], [last_agent], "Review bead-flagged concerns",
                            services.registry,
                            start_phase_id=next_id,
                        )
                        plan_phases.extend(review_phase)

                elif hint.hint_type == "add_approval_gate":
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
        """Steps 13 / 13b / 13c — context files, model inheritance, richness."""
        registry = services.registry

        for phase in plan_phases:
            for step in phase.steps:
                if not step.context_files:
                    step.context_files = ["CLAUDE.md"]

        for phase in plan_phases:
            for step in phase.steps:
                agent_def = registry.get(step.agent_name)
                if agent_def and agent_def.model:
                    step.model = agent_def.model
                elif default_model:
                    step.model = default_model
                for member in step.team:
                    member_def = registry.get(member.agent_name)
                    if member_def and member_def.model:
                        member.model = member_def.model
                    elif default_model:
                        member.model = default_model

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
        """E7 dependency detection (step 13d)."""
        bead_store = services.bead_store

        depends_on_task_id: str | None = None
        if bead_store is not None:
            depends_on_task_id = detect_task_dependency(task_summary, bead_store)
            if depends_on_task_id is not None:
                logger.info(
                    "E7 dependency detected: task_id=%s depends on prior task %s",
                    task_id,
                    depends_on_task_id,
                )
                attach_prior_task_beads(
                    plan_phases, depends_on_task_id, bead_store
                )
        return depends_on_task_id

    def _ensure_review_phase(
        self,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> None:
        """Inject a terminal Review phase for HIGH/CRITICAL tasks if absent.

        The complexity cap can truncate code-reviewer from the roster before
        phase construction runs, which silently drops the Review phase.  This
        post-enrichment check is the safety net: if the risk level warrants a
        review checkpoint but none exists, one is appended unconditionally.
        """
        if draft.risk_level_enum not in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return

        if any(p.name.lower() == "review" for p in draft.plan_phases):
            return

        max_id = max((p.phase_id for p in draft.plan_phases), default=0)
        injected = build_phases_for_names(
            ["Review"],
            ["code-reviewer"],
            draft.task_summary,
            services.registry,
            start_phase_id=max_id + 1,
        )
        draft.plan_phases.extend(injected)

        note = (
            f"[enrichment] Injected Review phase (phase_id={max_id + 1}) "
            f"for {draft.risk_level_enum.value}-risk task — "
            "code-reviewer was absent from roster due to complexity cap."
        )
        logger.info(note)
        draft.routing_notes.append(note)
