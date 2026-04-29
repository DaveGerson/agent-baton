"""RiskStage — knowledge resolver setup + sensitivity classification + risk.

Owns legacy ``create_plan`` steps 8-9 in the original ordering:

* Step 6.5: ``_step_setup_knowledge`` — instantiate the knowledge
  resolver/ranker (when a ``KnowledgeRegistry`` is wired) and decide
  the per-step attachment cap.
* Step 7+8+8b: ``_step_classify_data`` — run the data sensitivity
  classifier, merge keyword + structural risk signals, and derive the
  git strategy.

These two run together because ``_step_classify_data`` consumes the
resolved roster and the resolver setup is cheap pre-classification work
that happens at the same point in the legacy ordering.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.risk_signals import RISK_ORDINAL
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.models.enums import RiskLevel

if TYPE_CHECKING:
    from agent_baton.core.govern.classifier import ClassificationResult

logger = logging.getLogger(__name__)


def _select_git_strategy(risk: RiskLevel) -> Any:
    """Thin local forwarder — delegates to the module-level helper in
    ``_legacy_planner`` to avoid duplicating the mapping logic."""
    from agent_baton.core.engine.planning._legacy_planner import _select_git_strategy as _impl
    return _impl(risk)


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
            )
        )
        draft.classification = classification
        draft.risk_level = risk_level
        draft.risk_level_enum = risk_level_enum
        draft.git_strategy = git_strategy
        return draft

    # ------------------------------------------------------------------
    # Private helpers (ported from _LegacyIntelligentPlanner)
    # ------------------------------------------------------------------

    def _setup_knowledge(
        self, services: PlannerServices
    ) -> tuple[Any, Any, int]:
        """Step 6.5 — knowledge resolver/ranker construction.

        Returns ``(resolver, ranker, max_knowledge_per_step)``.
        Body is semantically identical to
        ``_LegacyIntelligentPlanner._step_setup_knowledge``.
        """
        _resolver = None
        _ranker = None
        _max_knowledge_per_step: int = 8
        if services.knowledge_registry is not None:
            import os as _os
            from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
            from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
            from agent_baton.core.intel.knowledge_ranker import KnowledgeRanker
            # Wire F0.4 lifecycle telemetry (bd-a313).  Resolver records a
            # KnowledgeUsed row per attachment whenever ``task_id``/``step_id``
            # are passed to ``resolve()``.  Construction is best-effort.
            try:
                _telemetry = KnowledgeTelemetryStore()
            except Exception:
                _telemetry = None
            _resolver = KnowledgeResolver(
                services.knowledge_registry,
                agent_registry=services.registry,
                rag_available=services.planner._detect_rag(),
                step_token_budget=32_000,
                doc_token_cap=8_000,
                telemetry=_telemetry,
            )
            # bd-0184: effectiveness-aware ranking.  Best-effort — ranker failure
            # never degrades planning; it simply returns the input unchanged.
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
    ) -> tuple["ClassificationResult | None", str, RiskLevel, str]:
        """Steps 7 / 8 / 8b — DataClassifier dispatch, risk merging, and
        git-strategy selection.

        Returns ``(classification, risk_level, risk_level_enum, git_strategy)``.
        ``services.planner._last_classification`` is set as a side effect
        (matching legacy behaviour).
        Body is semantically identical to
        ``_LegacyIntelligentPlanner._step_classify_data``.
        """
        # 7. Classify task sensitivity (DataClassifier if available)
        classification: "ClassificationResult | None" = None
        if services.data_classifier is not None:
            try:
                classification = services.data_classifier.classify(task_summary)
                services.planner._last_classification = classification
            except Exception:
                pass

        # 8. Risk — combines DataClassifier output with keyword/structural signals.
        # The classifier's risk level is the floor; keyword/structural signals can
        # raise it further but cannot lower it below what the classifier detected.
        keyword_risk_level = services.planner._assess_risk(task_summary, resolved_agents)
        if classification is not None:
            # Take the higher of the two assessments
            classifier_ordinal = RISK_ORDINAL[classification.risk_level]
            keyword_ordinal = RISK_ORDINAL[RiskLevel(keyword_risk_level)]
            if classifier_ordinal > keyword_ordinal:
                risk_level = classification.risk_level.value
            else:
                risk_level = keyword_risk_level
        else:
            risk_level = keyword_risk_level
        risk_level_enum = RiskLevel(risk_level)

        logger.info(
            "Risk classification: task_id=%s risk=%s (keyword=%s classifier=%s) git_strategy=%s",
            task_id,
            risk_level,
            keyword_risk_level,
            classification.risk_level.value if classification else "n/a",
            _select_git_strategy(risk_level_enum).value,
        )

        # 8b. Git strategy — derived from risk
        git_strategy = _select_git_strategy(risk_level_enum).value
        return classification, risk_level, risk_level_enum, git_strategy
