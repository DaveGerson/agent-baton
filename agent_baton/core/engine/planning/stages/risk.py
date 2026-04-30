"""RiskStage — knowledge resolver setup + sensitivity classification + risk.

Owns legacy ``create_plan`` steps 8-9 in the original ordering:

* Step 6.5: ``_step_setup_knowledge`` — instantiate the knowledge
  resolver/ranker (when a ``KnowledgeRegistry`` is wired) and decide
  the per-step attachment cap.
* Step 7+8+8b: ``_step_classify_data`` — run the data sensitivity
  classifier, merge keyword + structural risk signals, and derive the
  git strategy.
* Step 8c: ``_ensure_safety_roster`` — post-risk safety injection.
  HIGH/CRITICAL tasks must always carry ``code-reviewer``; tasks that
  mention compliance, regulated-domain, or audit keywords must also
  carry ``auditor``.  This runs AFTER risk classification so the
  complexity cap set in RosterStage (stage 2) cannot silently drop
  safety-critical agents.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.risk_signals import RISK_ORDINAL
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.risk_and_policy import (
    assess_risk,
    select_git_strategy,
)
from agent_baton.models.enums import RiskLevel

if TYPE_CHECKING:
    from agent_baton.core.govern.classifier import ClassificationResult

logger = logging.getLogger(__name__)

# Keywords that require the auditor agent regardless of complexity cap.
_AUDIT_KEYWORDS: frozenset[str] = frozenset({
    "compliance", "compliant", "regulated", "regulation", "audit", "auditable",
    "gdpr", "hipaa", "sox", "pci", "dss",
})


class RiskStage:
    """Stage 3: knowledge setup + risk and sensitivity classification."""

    name = "risk"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # Step 6.5 — knowledge resolver setup (graceful no-op when
        # no KnowledgeRegistry is wired).
        resolver, ranker, max_knowledge_per_step = self._setup_knowledge(services)
        draft.resolver = resolver
        draft.ranker = ranker
        draft.max_knowledge_per_step = max_knowledge_per_step

        # Step 7+8+8b — sensitivity, risk, git strategy.
        classification, risk_level, risk_level_enum, git_strategy = (
            self._classify_data(
                task_id=draft.task_id,
                task_summary=draft.task_summary,
                resolved_agents=draft.resolved_agents,
                services=services,
                task_classification=draft.task_classification,
            )
        )
        draft.classification = classification
        draft.risk_level = risk_level
        draft.risk_level_enum = risk_level_enum
        draft.git_strategy = git_strategy

        # Step 8c — post-risk safety roster injection.
        # Must run after risk_level_enum is set so we can inspect the final
        # risk level rather than the pre-cap complexity estimate.
        self._ensure_safety_roster(draft)
        return draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _setup_knowledge(
        self, services: PlannerServices
    ) -> tuple[Any, Any, int]:
        """Step 6.5 — knowledge resolver/ranker construction.

        Returns ``(resolver, ranker, max_knowledge_per_step)``.
        """
        _resolver = None
        _ranker = None
        _max_knowledge_per_step: int = 8
        if services.knowledge_registry is not None:
            import os as _os
            from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
            from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
            from agent_baton.core.intel.knowledge_ranker import KnowledgeRanker
            from agent_baton.core.engine.planning.utils.risk_and_policy import detect_rag
            try:
                _telemetry = KnowledgeTelemetryStore()
            except Exception:
                _telemetry = None
            _resolver = KnowledgeResolver(
                services.knowledge_registry,
                agent_registry=services.registry,
                rag_available=detect_rag(),
                step_token_budget=32_000,
                doc_token_cap=8_000,
                telemetry=_telemetry,
            )
            try:
                _ranker = KnowledgeRanker()
            except Exception:
                _ranker = None
            try:
                _max_knowledge_per_step = int(
                    _os.environ.get("BATON_MAX_KNOWLEDGE_PER_STEP", "8")
                )
            except (ValueError, TypeError):
                _max_knowledge_per_step = 8
        return _resolver, _ranker, _max_knowledge_per_step

    def _classify_data(
        self,
        *,
        task_id: str,
        task_summary: str,
        resolved_agents: list[str],
        services: PlannerServices,
        task_classification: Any = None,
    ) -> tuple["ClassificationResult | None", str, RiskLevel, str]:
        """Steps 7 / 8 / 8b — DataClassifier dispatch, risk merging, and
        git-strategy selection.

        Returns ``(classification, risk_level, risk_level_enum, git_strategy)``.
        """
        # 7. Classify task sensitivity (DataClassifier if available)
        classification: "ClassificationResult | None" = None
        if services.data_classifier is not None:
            try:
                classification = services.data_classifier.classify(task_summary)
            except Exception:
                pass

        # 8. Risk — combines DataClassifier output with keyword/structural
        #    signals AND the CLI classifier's risk hint (if available).
        keyword_risk_level = assess_risk(task_summary, resolved_agents)
        risk_level = keyword_risk_level

        if classification is not None:
            classifier_ordinal = RISK_ORDINAL[classification.risk_level]
            keyword_ordinal = RISK_ORDINAL[RiskLevel(keyword_risk_level)]
            if classifier_ordinal > keyword_ordinal:
                risk_level = classification.risk_level.value

        # TalentAgentClassifier attaches a risk hint from its AI review.
        # Use max(current, hint) so AI judgment can escalate but not lower.
        cli_risk_hint = getattr(
            services.task_classifier, "_last_cli_risk_hint", None
        )
        if cli_risk_hint is None:
            cli_risk_hint = getattr(task_classification, "_cli_risk_hint", None)
        if cli_risk_hint and cli_risk_hint in ("LOW", "MEDIUM", "HIGH"):
            hint_ordinal = RISK_ORDINAL[RiskLevel(cli_risk_hint)]
            current_ordinal = RISK_ORDINAL[RiskLevel(risk_level)]
            if hint_ordinal > current_ordinal:
                logger.info(
                    "CLI risk hint escalates %s → %s",
                    risk_level, cli_risk_hint,
                )
                risk_level = cli_risk_hint

        risk_level_enum = RiskLevel(risk_level)

        logger.info(
            "Risk classification: task_id=%s risk=%s (keyword=%s classifier=%s) git_strategy=%s",
            task_id,
            risk_level,
            keyword_risk_level,
            classification.risk_level.value if classification else "n/a",
            select_git_strategy(risk_level_enum).value,
        )

        # 8b. Git strategy — derived from risk
        git_strategy = select_git_strategy(risk_level_enum).value
        return classification, risk_level, risk_level_enum, git_strategy

    def _ensure_safety_roster(self, draft: PlanDraft) -> None:
        """Step 8c — post-risk safety roster injection.

        The complexity cap in RosterStage (stage 2) runs before risk
        classification and can silently drop safety-critical agents.  This
        method re-injects them after the final risk level is known so that
        HIGH/CRITICAL plans always carry the minimum review and audit
        coverage required.

        Rules
        -----
        * HIGH or CRITICAL risk  → ensure ``code-reviewer`` is present.
        * Task summary contains an audit/compliance keyword → ensure
          ``auditor`` is present (applies at any risk level, but is most
          impactful when the cap has already removed it).

        Injected agents are appended rather than prepended so the existing
        ordering (architect → backend-engineer → …) is preserved.  A
        ``routing_notes`` entry is added for each injection so the phase
        builder knows to create a Review phase.
        """
        risk = draft.risk_level_enum
        if risk is None:
            return

        task_lower = draft.task_summary.lower()
        needs_reviewer = risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        needs_auditor = any(kw in task_lower for kw in _AUDIT_KEYWORDS)

        # Strip stack-flavor suffixes for membership checks (e.g.
        # "backend-engineer--python" → "backend-engineer").
        def _base(name: str) -> str:
            return name.split("--")[0]

        current_bases = {_base(a) for a in draft.resolved_agents}

        injected_reviewer = False
        injected_auditor = False

        if needs_reviewer and "code-reviewer" not in current_bases:
            draft.resolved_agents.append("code-reviewer")
            draft.routing_notes.append(
                f"safety-roster: injected code-reviewer (risk={risk.value})"
            )
            logger.info(
                "Safety roster: injected code-reviewer — risk=%s task_id=%s",
                risk.value,
                draft.task_id,
            )
            injected_reviewer = True

        if needs_auditor and "auditor" not in current_bases:
            draft.resolved_agents.append("auditor")
            draft.routing_notes.append(
                "safety-roster: injected auditor (compliance/audit keyword detected)"
            )
            logger.info(
                "Safety roster: injected auditor — compliance keyword detected task_id=%s",
                draft.task_id,
            )
            injected_auditor = True

        injected_any = injected_reviewer or injected_auditor

        # When agents were injected, guarantee that review-type phases exist in
        # classified_phases so each injected safety agent has a home.
        #
        # Background: ClassificationStage may produce a medium-complexity phase
        # list (e.g. ["Design", "Implement", "Test"]) that strips Review before
        # risk is known.  Without this correction the injected agents are
        # force-assigned to the Implement team step and then silently filtered
        # by the phase-builder's reviewer-exclusion guard.
        #
        # Assignment algorithm constraint: assign_agents_to_phases() places at
        # most ONE primary agent per phase in Pass 1.  If both code-reviewer and
        # auditor are on the roster they need SEPARATE review-type phase slots —
        # one takes "Review" and the other takes "Audit".
        if injected_any and draft.classified_phases is not None:
            new_phases = list(draft.classified_phases)
            added: list[str] = []

            if injected_reviewer and "Review" not in new_phases:
                new_phases.append("Review")
                added.append("Review")

            if injected_auditor:
                # auditor needs its own phase slot distinct from "Review"
                # (which code-reviewer claims).  Use "Audit" — it maps to the
                # review ideal-roles table and is not blocked anywhere.
                reviewer_in_roster = (
                    "code-reviewer" in current_bases or injected_reviewer
                )
                if reviewer_in_roster and "Audit" not in new_phases:
                    new_phases.append("Audit")
                    added.append("Audit")
                elif "Review" not in new_phases:
                    # No reviewer competing — auditor can take Review directly.
                    new_phases.append("Review")
                    added.append("Review")

            if added:
                draft.classified_phases = new_phases
                draft.routing_notes.append(
                    f"safety-roster: appended {added} phase(s) to classified_phases "
                    "to host injected safety agent(s)"
                )
                logger.info(
                    "Safety roster: appended %s phase(s) to classified_phases task_id=%s",
                    added,
                    draft.task_id,
                )
