"""AssemblyStage — build the final MachinePlan + emit telemetry.

Owns legacy ``create_plan`` steps 20-21 in the original ordering:

* Step 14+16: ``_step_build_shared_context`` — assemble the
  ``MachinePlan``, compute team cost estimates, attach
  ``shared_context``.
* Step F4/O1.4: ``_step_emit_telemetry`` — F4 planning decision
  capture + optional OTel JSONL span.

The assembled ``MachinePlan`` is stored on ``draft.machine_plan`` for
the pipeline runner to return.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices

if TYPE_CHECKING:
    from agent_baton.models.execution import MachinePlan


class AssemblyStage:
    """Stage 7: build MachinePlan and emit telemetry."""

    name = "assembly"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 14+16 — build MachinePlan + shared_context.
        machine_plan = legacy._step_build_shared_context(
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            inferred_type=draft.inferred_type,
            inferred_complexity=draft.inferred_complexity,
            risk_level=draft.risk_level,
            budget_tier=draft.budget_tier,
            git_strategy=draft.git_strategy,
            plan_phases=draft.plan_phases,
            pattern=draft.pattern,
            explicit_knowledge_packs=draft.explicit_knowledge_packs,
            explicit_knowledge_docs=draft.explicit_knowledge_docs,
            intervention_level=draft.intervention_level,
            stack_profile=draft.stack_profile,
            classification=draft.classification,
            depends_on_task_id=draft.depends_on_task_id,
        )

        # Step F4/O1.4 — telemetry side effects.
        legacy._step_emit_telemetry(
            machine_plan,
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            inferred_type=draft.inferred_type,
            inferred_complexity=draft.inferred_complexity,
            risk_level=draft.risk_level,
            resolved_agents=draft.resolved_agents,
            plan_phases=draft.plan_phases,
            budget_tier=draft.budget_tier,
            git_strategy=draft.git_strategy,
            otel_exporter=draft.otel_exporter,
            otel_started_at=draft.otel_started_at,
        )

        # Stash the assembled plan on the draft for the runner.
        draft.machine_plan = machine_plan  # type: ignore[attr-defined]
        return draft

    @staticmethod
    def extract_plan(draft: PlanDraft) -> "MachinePlan":
        """Return the MachinePlan that was built into the draft."""
        plan = getattr(draft, "machine_plan", None)
        if plan is None:
            raise RuntimeError(
                "AssemblyStage did not run — no MachinePlan on draft"
            )
        return plan
