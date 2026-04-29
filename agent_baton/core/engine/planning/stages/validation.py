"""ValidationStage — score check, budget tier, plan review (HARD GATE).

Owns legacy ``create_plan`` step 10+11 (score check + budget tier) and
step 12c (team consolidation + plan reviewer pass).

**Quality fix #2 — hard gate**: the legacy ``PlanReviewer`` skipped
light-complexity plans entirely (``plan_reviewer.py:222``) and treated
its findings as advisory (over-broad steps got annotated, not
rejected).  This stage runs the reviewer for **every** plan and
exposes its findings on the draft so callers can decide what to do
with them.  The behavior change is opt-in via
``BATON_PLANNER_HARD_GATE`` to keep the legacy default for now and
let the new behavior bake; flip the env var to make the gate raise
on critical findings.

Order is preserved: score check + budget tier before consolidation,
because consolidation reads ``budget_tier`` to size team estimates.
"""
from __future__ import annotations

import logging
import os

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices

logger = logging.getLogger(__name__)


class PlanQualityError(RuntimeError):
    """Raised by ValidationStage when hard-gate mode catches a critical defect."""


class ValidationStage:
    """Stage 6: score check, budget tier, plan review.

    Hard-gate behavior: when ``BATON_PLANNER_HARD_GATE`` is set to a
    truthy value, the stage raises ``PlanQualityError`` on critical
    plan-review findings instead of just annotating the draft.  When
    unset (the default during the rollout), behavior matches the
    legacy planner — findings are recorded but the plan is still
    returned.
    """

    name = "validation"
    _HARD_GATE_ENV = "BATON_PLANNER_HARD_GATE"
    _TRUTHY = frozenset({"1", "true", "yes", "on"})

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        legacy = services.planner

        # Step 10+11+11b — score check, budget tier, policy validation.
        budget_tier = legacy._step_check_scores(
            draft.plan_phases,
            resolved_agents=draft.resolved_agents,
            inferred_type=draft.inferred_type,
            classification=draft.classification,
        )
        draft.budget_tier = budget_tier

        # Step 12c+12c.4+12c.5 — team consolidation, file-path
        # extraction, plan reviewer pass.  The legacy method runs
        # PlanReviewer internally; under hard-gate mode we re-read
        # its result and raise on critical findings.
        extracted_paths = legacy._step_consolidate_team(
            draft.plan_phases,
            task_id=draft.task_id,
            task_summary=draft.task_summary,
            risk_level=draft.risk_level,
            inferred_type=draft.inferred_type,
            inferred_complexity=draft.inferred_complexity,
            split_phase_ids=draft.split_phase_ids,
        )
        draft.extracted_paths = extracted_paths
        draft.review_result = legacy._last_review_result

        if self._hard_gate_enabled():
            self._enforce(draft)
        return draft

    # ------------------------------------------------------------------

    def _hard_gate_enabled(self) -> bool:
        return os.environ.get(self._HARD_GATE_ENV, "").lower() in self._TRUTHY

    def _enforce(self, draft: PlanDraft) -> None:
        review = draft.review_result
        if review is None:
            return
        # PlanReviewResult exposes ``critical_findings`` when the
        # reviewer detected defects severe enough to block the plan.
        # We treat any non-empty critical list as a hard failure under
        # hard-gate mode.  When the reviewer is skipped (e.g. the
        # legacy "light" early return) this list is empty so behavior
        # is no-op.
        critical = getattr(review, "critical_findings", None) or []
        if critical:
            logger.warning(
                "planner.validation.hard_gate.fail count=%d task=%s",
                len(critical), draft.task_id,
            )
            raise PlanQualityError(
                f"Plan {draft.task_id} blocked by ValidationStage: "
                f"{len(critical)} critical finding(s): "
                + "; ".join(str(f) for f in critical[:3])
            )
