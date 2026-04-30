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
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_roles import PHASE_BLOCKED_ROLES
from agent_baton.core.engine.planning.services import PlannerServices

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
        budget_tier = self._check_scores(draft=draft, services=services)
        draft.budget_tier = budget_tier

        # Step 12c+12c.4+12c.5 — team consolidation, file-path
        # extraction, plan reviewer pass.  The native method writes
        # ``services.planner._last_review_result`` as a side effect so
        # ``explain_plan`` can read it back.
        extracted_paths = self._consolidate_team(draft=draft, services=services)
        draft.extracted_paths = extracted_paths
        draft.review_result = services.planner._last_review_result

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
    # Private helpers — ported bodies of the two legacy _step_* methods
    # ------------------------------------------------------------------

    def _check_scores(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> str:
        """Steps 10 / 11 / 11b — score warnings, budget tier, policy check.

        Port of ``_LegacyIntelligentPlanner._step_check_scores``.
        Returns the selected budget tier.  Writes
        ``services.planner._last_policy_violations`` as a side effect
        (matching legacy behaviour so ``explain_plan`` can still read it).
        Score warnings are written to ``services.planner._last_score_warnings``
        via the legacy ``_check_agent_scores`` helper.

        Non-``_step_*`` helpers (``_check_agent_scores``,
        ``_select_budget_tier``, ``_classify_to_preset_key``,
        ``_validate_agents_against_policy``) stay on the legacy object and
        are called through ``services.planner``.
        """
        plan_phases = draft.plan_phases
        resolved_agents = draft.resolved_agents
        inferred_type = draft.inferred_type
        classification: ClassificationResult | None = draft.classification

        legacy = services.planner

        # 10. Score check — warn about low-health agents
        legacy._check_agent_scores(resolved_agents)

        # 11. Budget tier
        budget_tier = legacy._select_budget_tier(inferred_type, len(resolved_agents))

        # 11b. Policy validation — check agent assignments against active policy set.
        # Violations are recorded as warnings; they never hard-block plan creation.
        if services.policy_engine is not None:
            try:
                preset_name = legacy._classify_to_preset_key(classification)
                policy_set = services.policy_engine.load_preset(preset_name)
                if policy_set is not None:
                    legacy._last_policy_violations = (
                        legacy._validate_agents_against_policy(
                            resolved_agents, policy_set, plan_phases
                        )
                    )
                    # Enforce structural require_agent rules by injecting missing
                    # required agents into the plan's shared context as warnings.
                    # (We cannot silently add phases here — the user decides.)
            except Exception:
                pass
        return budget_tier

    def _consolidate_team(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> list[str]:
        """Steps 12c / 12c.4 / 12c.5 — team consolidation, file-path
        extraction, and plan reviewer pass.

        Port of ``_LegacyIntelligentPlanner._step_consolidate_team``.
        Mutates ``draft.plan_phases`` in place.  Returns ``extracted_paths``;
        EnrichmentStage extracts the same paths independently for its
        own use (the helper is pure, double-call is harmless).

        Writes ``services.planner._last_review_result`` as a side effect so
        ``explain_plan`` can read it unchanged.

        Non-``_step_*`` helpers (``_is_team_phase``,
        ``_consolidate_team_step``, ``_extract_file_paths``) stay on the
        legacy object and are called through ``services.planner``.
        """
        from agent_baton.models.execution import MachinePlan

        plan_phases = draft.plan_phases
        task_id = draft.task_id
        task_summary = draft.task_summary
        risk_level = draft.risk_level
        inferred_type = draft.inferred_type
        inferred_complexity = draft.inferred_complexity
        split_phase_ids = draft.split_phase_ids

        legacy = services.planner
        plan_reviewer = services.plan_reviewer

        # 12c. Consolidate multi-agent Implement/Fix phases into team steps.
        # NOTE: After concern-splitting (12b-bis), an implement phase that was
        # split now has N single-agent steps where each step is for a
        # *different concern*.  We must NOT re-consolidate those into a team
        # — they are intentionally parallel-by-concern.
        for phase in plan_phases:
            if phase.phase_id in split_phase_ids:
                continue
            if legacy._is_team_phase(phase, task_summary):
                phase.steps = [legacy._consolidate_team_step(phase)]

        # 12c.4. Extract file paths early — needed by plan reviewer (12c.5)
        # and context richness (13c).
        extracted_paths = legacy._extract_file_paths(task_summary)

        # 12c.5. Plan structure review — detect overly broad single-agent
        # steps and split them into parallel concern-scoped steps.
        # Skips light-complexity plans (nothing to split).  Uses Haiku
        # for medium+ plans, with heuristic fallback when unavailable.
        try:
            legacy._last_review_result = plan_reviewer.review(
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
            if legacy._last_review_result.splits_applied > 0:
                logger.info(
                    "Plan review applied %d split(s) (source=%s)",
                    legacy._last_review_result.splits_applied,
                    legacy._last_review_result.source,
                )
        except Exception:
            logger.debug(
                "Plan review failed — skipping", exc_info=True,
            )
        return extracted_paths

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
