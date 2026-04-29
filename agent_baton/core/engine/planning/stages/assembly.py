"""AssemblyStage — build the final MachinePlan + emit telemetry.

Owns legacy ``create_plan`` steps 20-21 in the original ordering:

* Step 14+16: ``_step_build_shared_context`` — assemble the
  ``MachinePlan``, compute team cost estimates, attach
  ``shared_context``.
* Step F4/O1.4: ``_step_emit_telemetry`` — F4 planning decision
  capture + optional OTel JSONL span.

The assembled ``MachinePlan`` is stored on ``draft.assembled_plan`` for
the pipeline runner to return via ``extract_plan(draft)``.

Non-``_step_*`` helpers used here:
* ``services.planner._build_shared_context(plan)`` — builds the shared
  context string from the assembled plan.
* ``services.planner._capture_planning_bead(...)`` — persists the
  planning bead when bead_store is set.

Both remain on the legacy class per the porting contract.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.models.execution import MachinePlan

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AssemblyStage:
    """Stage 7: build MachinePlan and emit telemetry."""

    name = "assembly"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        machine_plan = self._build_shared_context(draft, services)
        self._emit_telemetry(machine_plan, draft, services)
        draft.assembled_plan = machine_plan
        return draft

    # ------------------------------------------------------------------
    # Private: ported from _step_build_shared_context (lines 1939-2023)
    # ------------------------------------------------------------------

    def _build_shared_context(
        self,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> MachinePlan:
        """Assemble the MachinePlan and attach shared_context.

        Ported from ``_LegacyIntelligentPlanner._step_build_shared_context``
        (lines 1939-2023).  Introspection write
        ``_last_team_cost_estimates`` is stored on ``services.planner``
        to preserve the public ``_last_*`` contract.
        """
        legacy = services.planner

        # A3 — derive classification_signals (JSON) and
        # classification_confidence from the DataClassifier result when
        # available.
        _classification_signals: str | None = None
        _classification_confidence: float | None = None
        if draft.classification is not None:
            _classification_signals = json.dumps(
                {
                    "signals": draft.classification.signals_found,
                    "risk_level": draft.classification.risk_level.value,
                    "guardrail_preset": draft.classification.guardrail_preset,
                    "explanation": draft.classification.explanation,
                }
            )
            # ClassificationResult.confidence is "high" | "low" (string).
            # Map to float so callers can order/threshold numerically.
            _classification_confidence = (
                1.0 if draft.classification.confidence == "high" else 0.5
            )

        # ``_last_task_classification`` is set as a side effect of
        # ClassificationStage → ``_step_classify_task``; it lives on the
        # legacy planner instance until that stage is also ported.
        _last_task_cls = legacy._last_task_classification if legacy is not None else None

        tmp_plan = MachinePlan(
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            risk_level=draft.risk_level,
            budget_tier=draft.budget_tier,
            git_strategy=draft.git_strategy,
            phases=draft.plan_phases,
            pattern_source=draft.pattern.pattern_id if draft.pattern else None,
            task_type=draft.inferred_type,
            explicit_knowledge_packs=list(draft.explicit_knowledge_packs or []),
            explicit_knowledge_docs=list(draft.explicit_knowledge_docs or []),
            intervention_level=draft.intervention_level,
            complexity=draft.inferred_complexity,
            classification_source=(
                _last_task_cls.source if _last_task_cls else "cli-override"
            ),
            detected_stack=(
                f"{draft.stack_profile.language}/{draft.stack_profile.framework}"
                if draft.stack_profile and draft.stack_profile.framework
                else (draft.stack_profile.language if draft.stack_profile else None)
            ),
            # ``_last_foresight_insights`` is set as a side effect of
            # DecompositionStage → ``_step_apply_foresight``; also lives on
            # the legacy planner instance until that stage is ported.
            foresight_insights=list(
                legacy._last_foresight_insights if legacy is not None else []
            ),
            depends_on_task=draft.depends_on_task_id,
            classification_signals=_classification_signals,
            classification_confidence=_classification_confidence,
        )

        # Step 16 — team cost estimation: look up historical cost data for
        # team steps.
        team_cost_estimates: dict[str, int] = {}
        for phase in tmp_plan.phases:
            for step in phase.steps:
                if step.team and len(step.team) >= 2:
                    agents = [m.agent_name for m in step.team]
                    estimate = services.pattern_learner.get_team_cost_estimate(agents)
                    if estimate is not None:
                        team_cost_estimates[step.step_id] = estimate

        # Preserve the introspection attribute on the legacy planner so
        # existing callers of ``planner._last_team_cost_estimates`` keep
        # working.
        if legacy is not None:
            legacy._last_team_cost_estimates = team_cost_estimates

        # Attach shared context string (non-_step_* helper stays legacy).
        shared_context = (
            legacy._build_shared_context(tmp_plan)
            if legacy is not None
            else ""
        )
        tmp_plan.shared_context = shared_context
        return tmp_plan

    # ------------------------------------------------------------------
    # Private: ported from _step_emit_telemetry (lines 2025-2085)
    # ------------------------------------------------------------------

    def _emit_telemetry(
        self,
        machine_plan: MachinePlan,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> None:
        """Emit planning bead + OTel span — pure observability side-effects.

        Ported from ``_LegacyIntelligentPlanner._step_emit_telemetry``
        (lines 2025-2085).  Failures must not crash plan construction.
        """
        # F4 — Planning Decision Capture: persist key planner decisions
        # as beads.  Inspired by Steve Yegge's Beads agent memory system
        # (beads-ai/beads-cli).  ``_capture_planning_bead`` is a
        # non-_step_* helper that remains on the legacy class.
        if services.bead_store is not None and services.planner is not None:
            try:
                services.planner._capture_planning_bead(
                    task_id=draft.task_id,
                    content=(
                        f"Plan created for: {draft.task_summary}. "
                        f"Type={draft.inferred_type}, "
                        f"complexity={draft.inferred_complexity}, "
                        f"risk={draft.risk_level}, "
                        f"agents={draft.resolved_agents}, "
                        f"phases={[p.name for p in draft.plan_phases]}, "
                        f"budget_tier={draft.budget_tier}, "
                        f"git_strategy={draft.git_strategy}."
                    ),
                    tags=["planning", "plan-complete", draft.inferred_type],
                )
            except Exception:
                pass

        # O1.4 — emit OTel span when the exporter is enabled.
        if draft.otel_exporter is not None and draft.otel_started_at is not None:
            try:
                draft.otel_exporter.record_span(
                    name="plan.create",
                    kind="INTERNAL",
                    attributes={
                        "task_id": draft.task_id,
                        "task_type": draft.inferred_type,
                        "complexity": draft.inferred_complexity,
                        "risk_level": str(draft.risk_level),
                        "agent_count": len(draft.resolved_agents),
                        "phase_count": len(draft.plan_phases),
                    },
                    started_at=draft.otel_started_at,
                    ended_at=datetime.now(timezone.utc),
                )
            except Exception:
                # Observability must never crash the planner.
                logger.debug("OTel span emission failed", exc_info=True)

    # ------------------------------------------------------------------
    # Public: called by IntelligentPlanner.create_plan after pipeline.run
    # ------------------------------------------------------------------

    @staticmethod
    def extract_plan(draft: PlanDraft) -> MachinePlan:
        """Return the MachinePlan that was built into the draft."""
        if draft.assembled_plan is None:
            raise RuntimeError(
                "AssemblyStage did not run — no MachinePlan on draft"
            )
        return draft.assembled_plan
