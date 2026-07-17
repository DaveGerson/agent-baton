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
5. **review_missing** - high-risk or reviewer-routed plans are missing
   a Review phase with reviewer coverage.  Critical.
6. **audit_missing** - regulated, compliance, policy-auditor, or
   auditor-routed plans are missing an Audit phase with auditor coverage.
   Critical.
7. **reviewer_warning** — the reviewer surfaced any string starting
   with ``[critical]``.  Critical.

Order is preserved: score check + budget tier before consolidation,
because consolidation reads ``budget_tier`` to size team estimates.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_roles import (
    IMPLEMENT_PHASE_NAMES,
    PHASE_BLOCKED_ROLES,
)
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.phase_builder import (
    consolidate_team_step,
    is_team_phase,
)
from agent_baton.core.engine.planning.rules.risk_signals import RISK_ORDINAL
from agent_baton.core.engine.planning.scope_contract import diagnose_step_scope
from agent_baton.core.engine.planning.utils.risk_and_policy import (
    audit_coverage_requirement,
    assess_risk,
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


# Heavy-task-only shallow-decomposition signals (see
# ValidationStage._detect_shallow_decomposition). Two independent tiers,
# deliberately kept apart because they carry very different confidence:
#
# 1. _PLACEHOLDER_MARKER_RE -- a small, curated set of literal placeholder
#    markers that are vanishingly unlikely to appear in genuine task
#    content ("tbd", "todo", "placeholder", "lorem ipsum"). Unambiguous
#    -- flagged critical (blocking).
# 2. _BARE_TEMPLATE_SUFFIX_RE -- the literal bare fallback
#    ``phase_builder.step_description`` emits when neither an
#    "expert"-tier agent definition nor a STEP_TEMPLATES entry applies
#    for a given agent/phase pair: ``f"{verb}: {scoped} (as
#    {agent_name})"``. This IS a real signal of "the repository-grounded
#    decomposition pipeline had nothing to work with" -- but
#    STEP_TEMPLATES's per-agent/per-phase coverage has real, currently-
#    legitimate gaps (e.g. frontend-engineer on a Test-type phase), so a
#    real, otherwise-fine plan can legitimately hit it. Flagged warning
#    (surfaced as a diagnostic, non-blocking) rather than critical for
#    that reason -- see _detect_shallow_decomposition.
_PLACEHOLDER_MARKER_RE = re.compile(
    r"\btbd\b|\btodo\b|\bplaceholder\b|\blorem ipsum\b", re.IGNORECASE,
)
_BARE_TEMPLATE_SUFFIX_RE = re.compile(r"\(as [\w\-]+\)\s*$")


def _collect_member_agent_names(member: object) -> list[str]:
    """Collect ``agent_name`` from a TeamMember and its full ``sub_team`` tree.

    ``TeamMember.sub_team`` (models/execution.py) is self-referential and
    unbounded, so this recurses to arbitrary depth.  Used by both
    ``ValidationStage._step_agent_bases`` (coverage/mismatch checks) and
    ``_resolved_agents_from_plan`` so the two never disagree about which
    agents a team step contains — a depth-1-only walk let depth-2+ auditors
    trigger false ``audit_missing`` blocks and depth-2+ reviewers evade the
    reviewer-in-implement check.
    """
    names: list[str] = []
    name = str(getattr(member, "agent_name", "") or "")
    if name:
        names.append(name)
    for nested in getattr(member, "sub_team", []) or []:
        names.extend(_collect_member_agent_names(nested))
    return names


class PlanQualityError(RuntimeError):
    """Raised by ValidationStage when the effective quality gate blocks."""

    def __init__(
        self,
        message: str,
        *,
        defects: list["PlanDefect"] | None = None,
    ) -> None:
        super().__init__(message)
        self.defects = list(defects or [])


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
    # Derived from the canonical implement-phase table so the reviewer-in-
    # implement check stays in lockstep with routing (which folds
    # implementation/development variants via ``_phase_key``).  Deriving here
    # keeps the documentation archetype's ``Draft`` phase in scope instead of
    # silently dropping it (phase_roles.IMPLEMENT_PHASE_NAMES includes "draft").
    _IMPLEMENT_PHASE_KEYS = IMPLEMENT_PHASE_NAMES
    _REVIEWER_BASES = REVIEWER_AGENTS - {"auditor"}
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

        # Reconcile the roster with what actually landed in phases, now
        # that every agent-dropping mutation (RosterStage's subtask-union,
        # EnrichmentStage's concern-split, and this stage's own
        # reviewer-filtering team consolidation) has had its turn.
        # ``_agent_bases`` below folds ``resolved_agents`` into its
        # review/audit-coverage evidence, so a candidate that was never
        # actually assigned a step must not still masquerade as "routed".
        self._prune_unused_resolved_agents(draft)

        # Compute defects from the assembled plan + reviewer result.
        defects = self._detect_defects(draft)
        draft.plan_defects = defects  # type: ignore[attr-defined]
        self._apply_quality_gate(
            task_id=draft.task_id,
            defects=defects,
            score_warnings=draft.score_warnings,
        )
        return draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_quality_gate(
        self,
        *,
        task_id: str,
        defects: list[PlanDefect],
        score_warnings: list[str] | None = None,
    ) -> None:
        if score_warnings is not None:
            for defect in defects:
                if defect.severity in ("critical", "warning"):
                    score_warnings.append(str(defect))

        critical = [d for d in defects if d.severity == "critical"]
        if critical:
            logger.warning(
                "planner.validation: %d critical defect(s) on task %s: %s",
                len(critical), task_id,
                "; ".join(d.code for d in critical),
            )
            if self._quality_gate_blocks():
                raise PlanQualityError(
                    f"Plan {task_id} blocked by ValidationStage: "
                    + "; ".join(str(d) for d in critical[:5]),
                    defects=critical[:5],
                )

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
            except Exception as exc:
                # Non-fatal: a policy-engine failure must not abort planning,
                # but swallowing it silently voids the policy-driven
                # audit-requirement branch (risk_and_policy.py:139-149).  Make
                # it visible so the missing audit gate is traceable.
                logger.warning(
                    "planner.validation: policy validation failed for task %s "
                    "— policy-driven audit requirement not evaluated: %s",
                    draft.task_id, exc, exc_info=True,
                )
                draft.score_warnings.append(
                    f"policy_validation_failed: {exc}"
                )

        return budget_tier

    @staticmethod
    def _remap_depends_on(plan_phases: list, remap: dict[str, str]) -> None:
        """Rewrite ``step.depends_on`` entries per *remap*, de-duplicating.

        Used after team-step consolidation folds several step_ids into
        one survivor -- every downstream ``depends_on`` pointing at one of
        the folded ids must follow it to the survivor, not dangle.
        """
        for phase in plan_phases:
            for step in phase.steps:
                if not step.depends_on:
                    continue
                new_deps: list[str] = []
                for dep in step.depends_on:
                    mapped = remap.get(dep, dep)
                    if mapped != step.step_id and mapped not in new_deps:
                        new_deps.append(mapped)
                step.depends_on = new_deps

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
                original_step_ids = [s.step_id for s in phase.steps]
                consolidated = consolidate_team_step(phase)
                phase.steps = [consolidated]
                # Folding N steps into one collapses their step_ids into a
                # single new one (phase_builder.py's
                # ``consolidate_team_step`` -- the originals survive only
                # as nested ``TeamMember`` entries, not top-level steps).
                # A later phase's step may have declared ``depends_on`` a
                # specific one of those now-gone ids (e.g. the
                # "investigative" archetype's Verify step depends on the
                # Fix phase's implementer step) -- left unrewritten, that
                # reference dangles and MachinePlan's plan-graph invariant
                # check rejects the whole plan. Point every such reference
                # at the surviving consolidated step_id instead.
                remap = {
                    sid: consolidated.step_id
                    for sid in original_step_ids
                    if sid != consolidated.step_id
                }
                if remap:
                    self._remap_depends_on(plan_phases, remap)

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
        explicit_phase_agent_pairs = self._explicit_phase_agent_pairs(draft)

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

        audit_requirement = self._audit_requirement(draft, agent_bases)
        if audit_requirement and not self._has_audit_coverage(draft):
            defects.append(PlanDefect(
                code="audit_missing",
                severity="critical",
                message=(
                    f"task_id={draft.task_id} agents={sorted(agent_bases)} "
                    f"{audit_requirement}. "
                    "Compliance or auditor-routed plans require Audit coverage. "
                    "Remediation: add the auditor agent to the roster and a "
                    "terminal Audit phase with an auditor step. Headless/forge "
                    "plans (validate_assembled_plan) are NOT auto-remediated the "
                    "way the interactive RiskStage safety-roster path is — "
                    "regenerate the plan with the auditor included so the "
                    "auditor is guaranteed on both paths (parity of outcome)."
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
                    if base in blocked and (phase.name, base) not in explicit_phase_agent_pairs:
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

        defects.extend(self._detect_shallow_decomposition(draft))

        return defects

    @staticmethod
    def _explicit_phase_agent_pairs(draft: PlanDraft) -> set[tuple[str, str]]:
        """(phase_name, agent_base) pairs the *caller* explicitly requested
        via ``phases=[{"name": ..., "agents": [...]}]``.

        ``PHASE_BLOCKED_ROLES`` (agent_phase_mismatch #4) exists to catch
        the planner's own auto-routing mistakes (bd-0e36: architect
        auto-landing on Implement). It must not veto a caller's explicit,
        deliberate choice — RosterStage / EnrichmentStage already honour
        ``phases is not None`` as "trust the caller" for concern-splitting
        and roster expansion (see TestEnrichmentStageExplicitPhasesGuard);
        this stage needs the same carve-out or it silently re-rejects the
        very override those stages just agreed to preserve.

        Scoped to the exact (phase name, agent) pairs the caller wrote —
        not a blanket "phases is not None" bypass — so a dict with an
        empty ``agents`` list (auto-routed via
        ``phase_builder.assign_agents_to_phases`` fallback) still gets the
        full auto-routing check.
        """
        raw_phases = draft.phases
        if not raw_phases:
            return set()
        pairs: set[tuple[str, str]] = set()
        for entry in raw_phases:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            for agent in entry.get("agents", []) or []:
                pairs.add((name, str(agent).split("--")[0]))
        return pairs

    def _detect_shallow_decomposition(self, draft: PlanDraft) -> list[PlanDefect]:
        """Heavy-task-only: flag generic placeholder language and empty
        deliverables/write-scope.

        ``planning.utils.repo_grounding`` grounds heavy-task steps in
        concrete repository evidence when it's available; when it isn't
        (or a step's grounding produced nothing), the pre-existing
        generic-template fallback in ``phase_builder`` fires unchanged.
        That fallback is fine for light/medium tasks -- it's the wrong
        outcome for a task the pipeline itself classified as heavy. This
        surfaces that outcome as a blocking defect instead of silently
        shipping a plan with N steps of "Implement: <same summary> (as
        backend-engineer)" as their entire brief. Never runs for light or
        medium plans -- template-only descriptions are the expected,
        acceptable behavior there.
        """
        defects: list[PlanDefect] = []
        if draft.inferred_complexity != "heavy":
            return defects

        for phase in draft.plan_phases:
            phase_key = self._phase_key(phase.name)
            is_implement_phase = phase_key in self._IMPLEMENT_PHASE_KEYS
            for step in phase.steps:
                desc = step.task_description or ""
                if _PLACEHOLDER_MARKER_RE.search(desc):
                    defects.append(PlanDefect(
                        code="generic_placeholder",
                        severity="critical",
                        message=(
                            f"task_id={draft.task_id} phase_id={phase.phase_id} "
                            f"step_id={step.step_id} agent={step.agent_name!r}. "
                            "Heavy-task step description contains a literal "
                            f"placeholder marker: {desc[:160]!r}. Remediation: "
                            "replace the placeholder with the actual concrete "
                            "work package before this plan is dispatched."
                        ),
                    ))
                elif _BARE_TEMPLATE_SUFFIX_RE.search(desc):
                    defects.append(PlanDefect(
                        code="bare_agent_template",
                        # Warning, not critical -- see module-level
                        # _BARE_TEMPLATE_SUFFIX_RE docstring: STEP_TEMPLATES
                        # coverage gaps make this a real, currently-
                        # legitimate outcome for some agent/phase pairs, so
                        # it's a diagnostic signal, not a blocking one.
                        severity="warning",
                        message=(
                            f"task_id={draft.task_id} phase_id={phase.phase_id} "
                            f"step_id={step.step_id} agent={step.agent_name!r}. "
                            "Heavy-task step description reads as a generic "
                            f"template rather than a concrete work package: "
                            f"{desc[:160]!r}. Remediation: ground the step in "
                            "concrete repository artifacts (files, symbols, "
                            "tests) — see planning.utils.repo_grounding — or "
                            "supply an explicit phases/agents override with "
                            "real task detail."
                        ),
                    ))

                if is_implement_phase and not step.deliverables:
                    defects.append(PlanDefect(
                        code="empty_deliverables",
                        # Warning, not critical: unlike generic_placeholder
                        # (an unambiguous quality bug), an empty deliverable
                        # list can be entirely legitimate for a heavy task
                        # with no project_root to ground against (dry-run
                        # planning, a brand-new repo, CLI callers that don't
                        # pass --project-root). Still surfaced as a
                        # diagnostic (score_warnings / plan_diagnostics) so
                        # it's visible, just not blocking.
                        severity="warning",
                        message=(
                            f"task_id={draft.task_id} phase_id={phase.phase_id} "
                            f"step_id={step.step_id} agent={step.agent_name!r}. "
                            "Heavy-task implementation step has no "
                            "deliverables. Remediation: populate concrete, "
                            "file- or artifact-anchored deliverables for this "
                            "step."
                        ),
                    ))

                diag = diagnose_step_scope(step.step_id, step.step_type, step.allowed_paths)
                if diag is not None and diag.code == "write_scope_missing":
                    defects.append(PlanDefect(
                        code="empty_scope",
                        # Warning, not critical -- see empty_deliverables
                        # above: ambiguous write scope is common and
                        # legitimate whenever no project_root was supplied
                        # to ground against, so this stays a surfaced
                        # diagnostic rather than a blocking gate.
                        severity="warning",
                        message=self._with_remediation(
                            f"task_id={draft.task_id} {diag.message}",
                            "Remediation: populate step.allowed_paths from "
                            "repository evidence (deliverables, context "
                            "files, or confirmed repo topology) — see "
                            "planning.utils.repo_grounding / "
                            "planning.scope_contract.derive_allowed_paths.",
                        ),
                    ))

        return defects

    def _review_required(self, draft: PlanDraft, agent_bases: set[str]) -> bool:
        return (
            self._risk_value(draft) in {"HIGH", "CRITICAL"}
            or bool(agent_bases & self._REVIEWER_BASES)
        )

    def _audit_required(self, draft: PlanDraft, agent_bases: set[str]) -> bool:
        return self._audit_requirement(draft, agent_bases) is not None

    def _audit_requirement(self, draft: PlanDraft, agent_bases: set[str]) -> str | None:
        if "auditor" in agent_bases:
            return "requirement=auditor_routed"
        return audit_coverage_requirement(
            draft.task_summary,
            getattr(draft, "classification", None),
            getattr(draft, "policy_violations", None),
        )

    def _has_review_coverage(self, draft: PlanDraft) -> bool:
        """A reviewer-class step outside an implement-type phase counts.

        The canonical case is a phase literally named "Review", but
        several archetypes route a reviewer into a differently-named
        terminal phase in substance -- e.g. the "investigation"
        archetype's "Verify" phase, or an explicit ``--agents`` roster
        applied wholesale across compound-task subtask phases ("Test",
        "Document", ...). Excluding only ``_IMPLEMENT_PHASE_KEYS`` (rather
        than requiring an allow-listed name) keeps this in sync with the
        *other* half of the same rule: a reviewer-class agent inside an
        implement-type phase is independently flagged as
        ``agent_phase_mismatch`` above and is filtered out of
        implement/fix/draft/migrate team-steps by
        ``consolidate_team_step`` before this runs, so it can never
        double as coverage by accident.
        """
        for phase in draft.plan_phases:
            if self._phase_key(phase.name) in self._IMPLEMENT_PHASE_KEYS:
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

    def _prune_unused_resolved_agents(self, draft: PlanDraft) -> None:
        """Drop ``resolved_agents`` entries that never landed in any step.

        Several upstream stages narrow a multi-candidate roster down to
        fewer agents than ``resolved_agents`` still carries: RosterStage's
        subtask-union (each subtask only uses its own slice),
        EnrichmentStage's concern-split (one best-fit agent per concern),
        and this stage's own team consolidation (``_consolidate_team``
        filters reviewer-class agents out of Implement/Fix team-steps).
        Left un-reconciled, the unpicked candidates become phantom roster
        members: ``_agent_bases`` folds ``resolved_agents`` into its
        review/audit-coverage evidence, so an agent that was never
        actually assigned any work still trips a "needs a Review/Audit
        phase" false positive (review_missing/audit_missing). Only ever
        removes -- never adds -- so it cannot mask an agent genuinely
        still needed by the roster.
        """
        resolved_agents = getattr(draft, "resolved_agents", None)
        if not resolved_agents:
            return
        used_bases: set[str] = set()
        for phase in draft.plan_phases:
            for step in phase.steps:
                used_bases.update(self._step_agent_bases(step))
        pruned = [a for a in resolved_agents if (a or "").split("--")[0] in used_bases]
        if pruned == resolved_agents:
            return
        dropped = [a for a in resolved_agents if a not in pruned]
        draft.routing_notes.append(
            f"[validation] Pruned unused roster candidate(s) {dropped} — "
            "never assigned to a phase step."
        )
        draft.resolved_agents = pruned

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
        # "team" is the consolidated-team-step sentinel (phase_builder.py
        # ``consolidate_team_step``), never a real agent -- the actual
        # members live in ``step.team`` and are collected below. Other
        # call sites (planner.py's own agent-name walks) already exclude
        # it; this one didn't, so a consolidated team step silently
        # leaked a fake "team" base name into every downstream
        # review/audit-coverage check.
        if agent_name and agent_name != "team":
            bases.add(agent_name.split("--")[0])
        for member in getattr(step, "team", []) or []:
            for name in _collect_member_agent_names(member):
                bases.add(name.split("--")[0])
        return bases


def _resolved_agents_from_plan(plan: "MachinePlan") -> list[str]:
    agents: list[str] = []

    def _append(agent_name: str) -> None:
        if agent_name and agent_name != "team":
            agents.append(agent_name)

    for step in plan.all_steps:
        _append(step.agent_name)
        for member in getattr(step, "team", []) or []:
            for name in _collect_member_agent_names(member):
                _append(name)

    return list(dict.fromkeys(agents))


def _classification_from_plan(plan: "MachinePlan") -> object | None:
    if not plan.classification_signals:
        return None
    try:
        payload = json.loads(plan.classification_signals)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    risk_level = str(payload.get("risk_level") or plan.risk_level or "LOW").upper()
    try:
        risk_enum = RiskLevel(risk_level)
    except ValueError:
        risk_enum = RiskLevel.LOW

    confidence = "high" if (plan.classification_confidence or 0.0) >= 1.0 else "medium"
    return SimpleNamespace(
        signals_found=list(payload.get("signals") or []),
        risk_level=risk_enum,
        guardrail_preset=str(payload.get("guardrail_preset") or "Standard Development"),
        explanation=str(payload.get("explanation") or ""),
        confidence=confidence,
    )


def _classification_payload(classification: object) -> dict[str, object]:
    risk = getattr(classification, "risk_level", RiskLevel.LOW)
    if isinstance(risk, RiskLevel):
        risk_value = risk.value
    else:
        risk_value = str(risk or RiskLevel.LOW.value).upper()
    return {
        "signals": list(getattr(classification, "signals_found", []) or []),
        "risk_level": risk_value,
        "guardrail_preset": str(
            getattr(classification, "guardrail_preset", "Standard Development")
            or "Standard Development"
        ),
        "explanation": str(getattr(classification, "explanation", "") or ""),
    }


def _classification_confidence_value(classification: object) -> float:
    return 1.0 if getattr(classification, "confidence", "") == "high" else 0.5


def _coerce_risk_level(value: object, default: RiskLevel = RiskLevel.LOW) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    try:
        return RiskLevel(str(value or "").upper())
    except ValueError:
        return default


def _sync_plan_validation(plan: "MachinePlan", draft: PlanDraft) -> None:
    plan.risk_level = draft.risk_level
    plan.phases = list(draft.plan_phases)
    plan.budget_tier = draft.budget_tier
    existing = dict(getattr(plan, "plan_diagnostics", {}) or {})
    existing["validation_warning_count"] = max(
        int(existing.get("validation_warning_count", 0)),
        len(draft.score_warnings),
    )
    plan.plan_diagnostics = existing


def validate_assembled_plan(
    plan: "MachinePlan",
    *,
    services: PlannerServices,
    project_root: Path | None = None,
) -> "MachinePlan":
    """Apply ValidationStage semantics to an already-assembled MachinePlan."""
    draft = PlanDraft.from_inputs(
        plan.task_summary,
        task_type=plan.task_type,
        complexity=plan.complexity,
        project_root=project_root,
        explicit_knowledge_packs=list(plan.explicit_knowledge_packs or []),
        explicit_knowledge_docs=list(plan.explicit_knowledge_docs or []),
        intervention_level=plan.intervention_level,
    )
    draft.task_id = plan.task_id
    draft.plan_phases = list(plan.phases)
    draft.resolved_agents = _resolved_agents_from_plan(plan)
    draft.inferred_type = plan.task_type or ""
    draft.inferred_complexity = plan.complexity or "medium"
    draft.risk_level = str(plan.risk_level or "")
    draft.git_strategy = plan.git_strategy
    draft.classification = _classification_from_plan(plan)
    if draft.classification is None and services.data_classifier is not None:
        try:
            draft.classification = services.data_classifier.classify(plan.task_summary)
        except Exception:
            draft.classification = None
        else:
            plan.classification_signals = json.dumps(
                _classification_payload(draft.classification)
            )
            plan.classification_confidence = _classification_confidence_value(
                draft.classification
            )

    risk_candidates = [
        _coerce_risk_level(plan.risk_level),
        _coerce_risk_level(assess_risk(plan.task_summary, draft.resolved_agents)),
    ]
    if draft.classification is not None:
        risk_candidates.append(
            _coerce_risk_level(getattr(draft.classification, "risk_level", None))
        )
    draft.risk_level_enum = max(
        risk_candidates,
        key=lambda risk: RISK_ORDINAL[risk],
    )
    draft.risk_level = draft.risk_level_enum.value

    stage = ValidationStage()
    try:
        stage.run(draft, services)
    except PlanQualityError:
        _sync_plan_validation(plan, draft)
        raise
    _sync_plan_validation(plan, draft)
    return plan
