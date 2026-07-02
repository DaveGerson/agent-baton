"""ValidationStage — score check, budget tier, plan review (HARD GATE).

Owns legacy ``create_plan`` step 10+11 (score check + budget tier) and
step 12c (team consolidation + plan reviewer pass).

**Quality fix #2 — hard gate**: the legacy ``PlanReviewer`` skipped
light-complexity plans entirely (``plan_reviewer.py:222``) and treated
its findings as advisory.  This stage computes a list of *defects* on
top of the reviewer result and exposes them on the draft.  Critical
defects raise ``PlanQualityError`` by default.  ``BATON_DEV_MODE=1``
and ``BATON_PLANNER_WARN_ONLY=1`` make the gate warn-only for local
experimentation; truthy ``BATON_PLANNER_HARD_GATE`` is the legacy
explicit override and blocks even when dev/warn-only mode is set.

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
import re
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
from agent_baton.core.orchestration.router import REVIEWER_AGENTS, is_reviewer_agent
from agent_baton.models.enums import RiskLevel

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
    on the new ``draft.plan_defects`` attribute (full list).
    Critical defects raise ``PlanQualityError`` by default.  Set
    ``BATON_DEV_MODE=1`` or ``BATON_PLANNER_WARN_ONLY=1`` to keep local
    experimentation warn-only.  A truthy ``BATON_PLANNER_HARD_GATE`` remains
    a supported legacy explicit opt-in flag and overrides warn-only/dev mode.
    """

    name = "validation"
    _HARD_GATE_ENV = "BATON_PLANNER_HARD_GATE"
    _DEV_MODE_ENV = "BATON_DEV_MODE"
    _WARN_ONLY_ENV = "BATON_PLANNER_WARN_ONLY"
    _TRUTHY = frozenset({"1", "true", "yes", "on"})
    _IMPLEMENT_PHASE_KEYS = frozenset({
        "implement",
        "implementation",
        "fix",
        "build",
        "develop",
        "development",
    })
    _REVIEWER_BASES = REVIEWER_AGENTS - {"auditor"}
    _COMPLIANCE_TERMS = frozenset({
        "audit",
        "auditable",
        "compliant",
        "compliance",
        "dss",
        "gdpr",
        "hipaa",
        "pci",
        "regulated",
        "regulation",
        "regulatory",
        "sox",
    })

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
            if self._quality_gate_blocks():
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
        """Return the legacy explicit hard-gate flag state.

        Plan-quality blocking now defaults on; callers should use
        ``_quality_gate_blocks`` for the effective policy.
        """
        return self._env_truthy(self._HARD_GATE_ENV)

    def _quality_gate_blocks(self) -> bool:
        return self._hard_gate_enabled() or not self._warn_only_enabled()

    def _warn_only_enabled(self) -> bool:
        return (
            self._env_truthy(self._DEV_MODE_ENV)
            or self._env_truthy(self._WARN_ONLY_ENV)
        )

    def _env_truthy(self, name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in self._TRUTHY

    def _with_remediation(self, message: str, remediation: str) -> str:
        if re.search(r"\bremediation\s*:", message, flags=re.IGNORECASE):
            return message
        separator = " " if message.rstrip().endswith((".", "!", "?")) else ". "
        return f"{message.rstrip()}{separator}{remediation}"

    def _detect_defects(self, draft: PlanDraft) -> list[PlanDefect]:
        """Inspect the assembled draft and return the list of defects."""
        defects: list[PlanDefect] = []
        agent_bases = self._agent_bases(draft)

        # 1. review_skipped
        review = draft.review_result
        if review is not None:
            source = getattr(review, "source", "")
            if source == "skipped-light" and draft.inferred_complexity != "light":
                defects.append(PlanDefect(
                    code="review_skipped",
                    severity="critical",
                    message=(
                        f"task_id={draft.task_id} source=skipped-light "
                        f"complexity={draft.inferred_complexity}. "
                        "PlanReviewer took the light-complexity early return on "
                        "a non-light plan, bypassing structural review. "
                        "Remediation: rerun review with the correct complexity "
                        "or add an explicit Review phase before validation."
                    ),
                ))
            # 5. reviewer_warning
            for w in getattr(review, "warnings", None) or []:
                if isinstance(w, str) and w.lower().startswith("[critical]"):
                    defects.append(PlanDefect(
                        code="reviewer_warning",
                        severity="critical",
                        message=self._with_remediation(
                            w,
                            "Remediation: update the plan to address the "
                            "reviewer warning, or add explicit Review/Audit "
                            "coverage that resolves it before validation.",
                        ),
                    ))

        # 2. empty_plan
        if not draft.plan_phases:
            defects.append(PlanDefect(
                code="empty_plan",
                severity="critical",
                message=(
                    f"task_id={draft.task_id} phase_count=0. "
                    "Plan has no executable phases or steps. "
                    "Remediation: add at least one phase with at least one "
                    "concrete step before validation."
                ),
            ))
            return defects

        if (
            self._review_required(draft, agent_bases)
            and not self._has_review_coverage(draft)
        ):
            defects.append(PlanDefect(
                code="review_missing",
                severity="critical",
                message=(
                    f"task_id={draft.task_id} risk={self._risk_value(draft)} "
                    f"agents={sorted(agent_bases)}. "
                    "High-risk or reviewer-routed plans require Review coverage. "
                    "Remediation: add a terminal Review phase with code-reviewer "
                    "or security-reviewer steps."
                ),
            ))

        if (
            self._audit_required(draft, agent_bases)
            and not self._has_audit_coverage(draft)
        ):
            defects.append(PlanDefect(
                code="audit_missing",
                severity="critical",
                message=(
                    f"task_id={draft.task_id} agents={sorted(agent_bases)}. "
                    "Compliance or auditor-routed plans require Audit coverage. "
                    "Remediation: add a terminal Audit phase with an auditor step."
                ),
            ))

        for phase in draft.plan_phases:
            # 3. empty_phase
            if not phase.steps:
                defects.append(PlanDefect(
                    code="empty_phase",
                    severity="critical",
                    message=(
                        f"task_id={draft.task_id} phase_id={phase.phase_id} "
                        f"phase={phase.name!r} step_count=0. "
                        "Phase has no executable steps. "
                        "Remediation: add at least one step to this phase or "
                        "remove the empty phase."
                    ),
                ))
                continue

            # 4. agent_phase_mismatch
            phase_key = self._phase_key(phase.name)
            blocked = PHASE_BLOCKED_ROLES.get(phase_key, set())
            if blocked:
                for step in phase.steps:
                    base = (step.agent_name or "").split("--")[0]
                    if base in blocked:
                        defects.append(PlanDefect(
                            code="agent_phase_mismatch",
                            severity="critical",
                            message=(
                                f"task_id={draft.task_id} phase_id={phase.phase_id} "
                                f"phase={phase.name!r} step_id={step.step_id} "
                                f"agent={base!r}. Agent is blocked from this "
                                "phase type. Remediation: route the step to an "
                                "allowed implementer for this phase or move the "
                                "agent to a dedicated Review/Audit phase."
                            ),
                        ))
            if phase_key in self._IMPLEMENT_PHASE_KEYS:
                for step in phase.steps:
                    for base in self._step_agent_bases(step):
                        if is_reviewer_agent(base):
                            target_phase = (
                                "Audit" if base == "auditor" else "Review"
                            )
                            defects.append(PlanDefect(
                                code="agent_phase_mismatch",
                                severity="critical",
                                message=(
                                    f"task_id={draft.task_id} phase_id={phase.phase_id} "
                                    f"phase={phase.name!r} step_id={step.step_id} "
                                    f"agent={base!r}. Reviewer-class agents are "
                                    "blocked from implementation phases. "
                                    f"Remediation: move {base!r} to a dedicated "
                                    f"{target_phase} phase and assign implementation "
                                    "work to an engineering agent."
                                ),
                            ))

        return defects

    def _review_required(self, draft: PlanDraft, agent_bases: set[str]) -> bool:
        return (
            self._risk_value(draft) in {"HIGH", "CRITICAL"}
            or bool(agent_bases & self._REVIEWER_BASES)
        )

    def _audit_required(self, draft: PlanDraft, agent_bases: set[str]) -> bool:
        if "auditor" in agent_bases:
            return True
        summary = (draft.task_summary or "").lower()
        return any(
            re.search(rf"\b{re.escape(term)}\b", summary)
            for term in self._COMPLIANCE_TERMS
        )

    def _has_review_coverage(self, draft: PlanDraft) -> bool:
        for phase in draft.plan_phases:
            if self._phase_key(phase.name) != "review":
                continue
            for step in phase.steps:
                if self._step_agent_bases(step) & self._REVIEWER_BASES:
                    return True
        return False

    def _has_audit_coverage(self, draft: PlanDraft) -> bool:
        for phase in draft.plan_phases:
            if self._phase_key(phase.name) != "audit":
                continue
            for step in phase.steps:
                if "auditor" in self._step_agent_bases(step):
                    return True
        return False

    def _risk_value(self, draft: PlanDraft) -> str:
        risk = getattr(draft, "risk_level_enum", None)
        if isinstance(risk, RiskLevel):
            return risk.value
        if risk:
            return str(risk).upper()
        return str(getattr(draft, "risk_level", "") or "").upper()

    def _phase_key(self, name: str) -> str:
        raw = (name or "").lower().split(":")[0].strip()
        key = raw.split()[-1] if raw else ""
        if key == "implementation":
            return "implement"
        if key == "development":
            return "develop"
        return key

    def _agent_bases(self, draft: PlanDraft) -> set[str]:
        bases = {
            (agent or "").split("--")[0]
            for agent in getattr(draft, "resolved_agents", []) or []
            if agent
        }
        for phase in draft.plan_phases:
            for step in phase.steps:
                bases.update(self._step_agent_bases(step))
        bases.discard("")
        return bases

    def _step_agent_bases(self, step: object) -> set[str]:
        bases: set[str] = set()
        agent_name = getattr(step, "agent_name", "")
        if agent_name:
            bases.add(agent_name.split("--")[0])
        for member in getattr(step, "team", []) or []:
            member_name = getattr(member, "agent_name", "")
            if member_name:
                bases.add(member_name.split("--")[0])
            for nested in getattr(member, "sub_team", []) or []:
                nested_name = getattr(nested, "agent_name", "")
                if nested_name:
                    bases.add(nested_name.split("--")[0])
        return bases
