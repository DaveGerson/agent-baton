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
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices


class EnrichmentStage:
    """Stage 5: attach gates, approvals, bead hints, context, prior-task beads."""

    name = "enrichment"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 12+12.a — QA gates + project-config defaults.
        legacy._step_apply_gates(
            draft.plan_phases,
            stack_profile=draft.stack_profile,
            gate_scope=draft.gate_scope,
            project_root=draft.project_root,
        )

        # Step 12b+12b-bis — approval gates + concern-split.
        split_phase_ids = legacy._step_apply_approval_gates(
            draft.plan_phases,
            risk_level_enum=draft.risk_level_enum,
            task_summary=draft.task_summary,
            resolved_agents=draft.resolved_agents,
        )
        draft.split_phase_ids = split_phase_ids

        # Step 12d — bead hints (conditional).
        if draft.bead_hints:
            draft.plan_phases = legacy._apply_bead_hints(
                draft.plan_phases, draft.bead_hints,
            )

        # Step 13+13b+13c — context file injection.
        legacy._step_inject_context_files(
            draft.plan_phases,
            default_model=draft.default_model,
            extracted_paths=draft.extracted_paths,
        )

        # Step 13d — prior-task bead attachment.
        depends_on_task_id = legacy._step_attach_prior_beads(
            draft.plan_phases,
            task_id=draft.task_id,
            task_summary=draft.task_summary,
        )
        draft.depends_on_task_id = depends_on_task_id
        return draft
