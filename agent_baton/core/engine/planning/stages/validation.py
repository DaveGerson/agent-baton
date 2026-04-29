"""ValidationStage — score check, budget tier, plan review (HARD GATE).

Owns legacy ``create_plan`` step 10+11 (score check + budget tier) and
step 12c (team consolidation + plan reviewer pass).

**Quality fix #2 — hard gate**: the legacy ``PlanReviewer`` skipped
light-complexity plans entirely (``plan_reviewer.py:222``) and treated
its findings as advisory.  This stage computes a list of *defects* on
top of the reviewer result and exposes them on the draft.  Under
``BATON_PLANNER_HARD_GATE`` the stage raises ``PlanQualityError``
when any defect is critical; without the env var it just records and
warns, preserving legacy behavior so the new gate can bake in
production before flipping the default.

Defects detected here (independent of what the reviewer surfaces):

1. **review_skipped** — legacy reviewer's "skipped-light" early-return
   was hit on a plan that was actually not light.  Critical because
   it means the only quality gate silently no-op'd.
2. **empty_plan** — plan has zero phases.  Critical.
3. **empty_phase** — at least one phase has zero steps.  Critical.
4. **agent_phase_mismatch** — a step's agent role is in
   ``PHASE_BLOCKED_ROLES`` for the phase it landed in (the
   architect-on-Implement defect family bd-0e36 / bd-1974).
   Critical.
5. **reviewer_warning** — the reviewer surfaced any string starting
   with ``[critical]``.  Critical.

Order is preserved: score check + budget tier before consolidation,
because consolidation reads ``budget_tier`` to size team estimates.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_roles import PHASE_BLOCKED_ROLES
from agent_baton.core.engine.planning.services import PlannerServices

logger = logging.getLogger(__name__)


class PlanQualityError(RuntimeError):
    """Raised by ValidationStage in hard-gate mode when a critical defect is found."""


@dataclass
class PlanDefect:
    """A single defect surfaced by ValidationStage."""

    code: str
    severity: str  # "critical" | "warning" | "info"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


class ValidationStage:
    """Stage 6: score check, budget tier, plan review with defect detection.

    Defects are recorded on ``draft.score_warnings`` (any severity) and
    on the new ``draft.plan_defects`` attribute (full list).  Critical
    defects raise ``PlanQualityError`` when ``BATON_PLANNER_HARD_GATE``
    is truthy.
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
        # PlanReviewer internally and stores the result on
        # ``legacy._last_review_result``.
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

        # Compute defects from the assembled plan + reviewer result.
        defects = self._detect_defects(draft)
        draft.plan_defects = defects  # type: ignore[attr-defined]
        for d in defects:
            if d.severity in ("critical", "warning"):
                draft.score_warnings.append(str(d))

        critical = [d for d in defects if d.severity == "critical"]
        if critical:
            logger.warning(
                "planner.validation: %d critical defect(s) on task %s: %s",
                len(critical), draft.task_id,
                "; ".join(d.code for d in critical),
            )
            if self._hard_gate_enabled():
                raise PlanQualityError(
                    f"Plan {draft.task_id} blocked by ValidationStage: "
                    + "; ".join(str(d) for d in critical[:5])
                )
        return draft

    # ------------------------------------------------------------------

    def _hard_gate_enabled(self) -> bool:
        return os.environ.get(self._HARD_GATE_ENV, "").lower() in self._TRUTHY

    def _detect_defects(self, draft: PlanDraft) -> list[PlanDefect]:
        """Inspect the assembled draft and return the list of defects."""
        defects: list[PlanDefect] = []

        # 1. review_skipped: reviewer's light-complexity early return
        #    on a plan that isn't actually light.
        review = draft.review_result
        if review is not None:
            source = getattr(review, "source", "")
            if source == "skipped-light" and draft.inferred_complexity != "light":
                defects.append(PlanDefect(
                    code="review_skipped",
                    severity="critical",
                    message=(
                        f"PlanReviewer skipped a {draft.inferred_complexity!r} "
                        f"plan via the light-complexity early return — "
                        f"quality gate effectively bypassed."
                    ),
                ))
            # 5. reviewer_warning: any "[critical]" prefix in warnings.
            for w in getattr(review, "warnings", None) or []:
                if isinstance(w, str) and w.lower().startswith("[critical]"):
                    defects.append(PlanDefect(
                        code="reviewer_warning",
                        severity="critical",
                        message=w,
                    ))

        # 2. empty_plan
        if not draft.plan_phases:
            defects.append(PlanDefect(
                code="empty_plan",
                severity="critical",
                message="Plan has zero phases.",
            ))
            return defects

        for phase in draft.plan_phases:
            # 3. empty_phase
            if not phase.steps:
                defects.append(PlanDefect(
                    code="empty_phase",
                    severity="critical",
                    message=f"Phase {phase.name!r} has zero steps.",
                ))
                continue

            # 4. agent_phase_mismatch — bd-0e36 / bd-1974 family.
            phase_key = (phase.name or "").lower().split(":")[0].strip()
            # Strip "Phase N: " prefix to get the canonical phase noun.
            phase_key = phase_key.split()[-1] if phase_key else ""
            blocked = PHASE_BLOCKED_ROLES.get(phase_key, set())
            if blocked:
                for step in phase.steps:
                    base = (step.agent_name or "").split("--")[0]
                    if base in blocked:
                        defects.append(PlanDefect(
                            code="agent_phase_mismatch",
                            severity="critical",
                            message=(
                                f"Step {step.step_id} routes "
                                f"{base!r} into the blocked-list phase "
                                f"{phase.name!r}."
                            ),
                        ))

        return defects
