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
   ``PHASE_BLOCKED_ROLES`` for the phase it landed in.  Critical.
5. **reviewer_warning** — the reviewer surfaced any string starting
   with ``[critical]``.  Critical.

Order is preserved: score check + budget tier before consolidation,
because consolidation reads ``budget_tier`` to size team estimates.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_roles import PHASE_BLOCKED_ROLES
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.phase_builder import (
    consolidate_team_step,
    is_team_phase,
)
from agent_baton.core.engine.planning.utils.risk_and_policy import (
    classify_to_preset_key,
    select_budget_tier,
    validate_agents_against_policy,
)
from agent_baton.core.engine.planning.utils.roster_logic import check_agent_scores
from agent_baton.core.engine.planning.utils.text_parsers import extract_file_paths

if TYPE_CHECKING:
    from agent_baton.core.govern.classifier import ClassificationResult
    from agent_baton.models.execution import MachinePlan, PlanPhase

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
        # Step 10+11+11b — score check, budget tier, policy validation.
        # _check_scores writes score_warnings and policy_violations onto draft
        # as side effects and returns only the budget tier.
        budget_tier = self._check_scores(draft=draft, services=services)
        draft.budget_tier = budget_tier

        # Step 12c+12c.4+12c.5 — team consolidation, file-path
        # extraction, plan reviewer pass.
        extracted_paths, review_result = self._consolidate_team(
            draft=draft, services=services
        )
        draft.extracted_paths = extracted_paths
        draft.review_result = review_result

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
    # Private helpers
    # ------------------------------------------------------------------

    def _check_scores(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> str:
        """Steps 10 / 11 / 11b — score warnings, budget tier, policy check.

        Writes ``draft.score_warnings`` and ``draft.policy_violations`` as
        side effects.  Returns the selected budget tier string.
        """
        plan_phases = draft.plan_phases
        resolved_agents = draft.resolved_agents
        inferred_type = draft.inferred_type
        classification: "ClassificationResult | None" = draft.classification

        # 10. Score check — warn about low-health agents
        score_warnings = check_agent_scores(
            resolved_agents, services.scorer, services.bead_store
        )
        draft.score_warnings.extend(score_warnings)

        # 11. Budget tier
        budget_tier = select_budget_tier(
            inferred_type, len(resolved_agents), services.budget_tuner
        )

        # 11b. Policy validation
        if services.policy_engine is not None:
            try:
                preset_name = classify_to_preset_key(classification)
                policy_set = services.policy_engine.load_preset(preset_name)
                if policy_set is not None:
                    draft.policy_violations = validate_agents_against_policy(
                        resolved_agents, policy_set, plan_phases, services.policy_engine
                    )
            except Exception:
                pass

        return budget_tier

    def _consolidate_team(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> tuple[list[str], object | None]:
        """Steps 12c / 12c.4 / 12c.5 — team consolidation, file-path
        extraction, and plan reviewer pass.

        Mutates ``draft.plan_phases`` in place.
        Returns ``(extracted_paths, review_result)`` tuple.
        """
        from agent_baton.models.execution import MachinePlan

        plan_phases = draft.plan_phases
        task_id = draft.task_id
        task_summary = draft.task_summary
        risk_level = draft.risk_level
        inferred_type = draft.inferred_type
        inferred_complexity = draft.inferred_complexity
        split_phase_ids = draft.split_phase_ids

        plan_reviewer = services.plan_reviewer

        # 12c. Consolidate multi-agent Implement/Fix phases into team steps.
        for phase in plan_phases:
            if phase.phase_id in split_phase_ids:
                continue
            if is_team_phase(phase, task_summary):
                phase.steps = [consolidate_team_step(phase)]

        # 12c.4. Extract file paths
        extracted_paths = extract_file_paths(task_summary)

        # 12c.5. Plan structure review
        review_result = None
        try:
            review_result = plan_reviewer.review(
                plan=MachinePlan(
                    task_id=task_id,
                    task_summary=task_summary,
                    risk_level=risk_level,
                    budget_tier="standard",
                    phases=plan_phases,
                    task_type=inferred_type,
                    complexity=inferred_complexity,
                ),
                task_summary=task_summary,
                file_paths=extracted_paths,
                complexity=inferred_complexity,
            )
            if review_result.splits_applied > 0:
                logger.info(
                    "Plan review applied %d split(s) (source=%s)",
                    review_result.splits_applied,
                    review_result.source,
                )
        except Exception:
            logger.debug(
                "Plan review failed — skipping", exc_info=True,
            )

        return extracted_paths, review_result

    # ------------------------------------------------------------------

    def _hard_gate_enabled(self) -> bool:
        return os.environ.get(self._HARD_GATE_ENV, "").lower() in self._TRUTHY

    def _detect_defects(self, draft: PlanDraft) -> list[PlanDefect]:
        """Inspect the assembled draft and return the list of defects."""
        defects: list[PlanDefect] = []

        # 1. review_skipped
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
            # 5. reviewer_warning
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

            # 4. agent_phase_mismatch
            phase_key = (phase.name or "").lower().split(":")[0].strip()
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
