"""Auto-enrichment pipeline for SpecDraft entities (007 Phase I).

``enrich(title, body)`` produces an ``EnrichmentData`` record containing:

- Risk classification via ``DataClassifier`` (pack-aware when packs are
  loaded; falls back gracefully when no packs are present).
- Required reviewers derived from the active policy preset's
  ``require_agent`` rules.
- Cost forecast via ``cost_estimator.forecast_plan`` on a minimal stub
  ``MachinePlan`` built from ``_DEFAULT_ROSTER_BY_RISK``.  When
  ``central.db`` exists, a history-calibrated ``CostForecaster`` is used
  and ``cost_confidence`` is set to ``"high"`` when ``sample_size > 0``.

``_run_enrichment(spec_id, store)`` is a best-effort worker that
swallows and logs exceptions so it can be safely dispatched via
``loop.run_in_executor``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.models.spec_draft import EnrichmentData

if TYPE_CHECKING:
    from agent_baton.core.federate.spec_draft_store import SpecDraftStore

logger = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"

# Default agent roster per risk level used to build the stub plan.
# Keeps the stub plan minimal so cost estimates are fast and predictable.
_DEFAULT_ROSTER_BY_RISK: dict[str, list[str]] = {
    "LOW":      ["developer"],
    "MEDIUM":   ["developer", "code-reviewer"],
    "HIGH":     ["developer", "code-reviewer", "auditor"],
    "CRITICAL": ["developer", "code-reviewer", "auditor", "security-reviewer"],
}


def enrich(title: str, body: str) -> EnrichmentData:
    """Produce enrichment data for a spec draft.

    Runs synchronously; callers that want non-blocking behaviour should
    dispatch via ``loop.run_in_executor(None, lambda: enrich(title, body))``.

    Args:
        title: Spec draft title.
        body: Spec draft body (markdown).

    Returns:
        A populated :class:`~agent_baton.models.spec_draft.EnrichmentData`.
    """
    task_text = f"{title}\n{body}"

    # --- Step 1: classify risk ------------------------------------------------
    try:
        from agent_baton.core.govern.packs import load_packs, make_classifier_for_packs, register_pack_policies
        project_root = Path.cwd()
        packs = load_packs(project_root)
        register_pack_policies(packs)
        classifier = make_classifier_for_packs(packs)
    except Exception:  # noqa: BLE001 — tolerate missing packs gracefully
        from agent_baton.core.govern.classifier import DataClassifier
        classifier = DataClassifier()

    try:
        cls_result = classifier.classify(task_text)
    except Exception:  # noqa: BLE001
        from agent_baton.core.govern.classifier import ClassificationResult
        from agent_baton.models.enums import RiskLevel
        cls_result = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset="Standard Development",
        )

    risk_level = cls_result.risk_level.value if hasattr(cls_result.risk_level, "value") else str(cls_result.risk_level)
    guardrail_preset = cls_result.guardrail_preset
    signals_found = list(cls_result.signals_found)
    confidence = cls_result.confidence

    # --- Step 2: required reviewers from policy preset -----------------------
    required_reviewers: list[str] = []
    try:
        from agent_baton.core.engine.planning.utils.risk_and_policy import classify_to_preset_key
        from agent_baton.core.govern.policy import PolicyEngine
        preset_key = classify_to_preset_key(cls_result)
        engine = PolicyEngine()
        policy_set = engine.load_preset(preset_key)
        if policy_set is not None:
            for rule in policy_set.rules:
                if rule.rule_type == "require_agent" and rule.pattern:
                    if rule.pattern not in required_reviewers:
                        required_reviewers.append(rule.pattern)
    except Exception:  # noqa: BLE001
        logger.debug("Could not derive required_reviewers from policy", exc_info=True)

    # --- Step 3: cost forecast ------------------------------------------------
    est_usd_low = 0.0
    est_usd_mid = 0.0
    est_usd_high = 0.0
    cost_confidence = "default"
    breakdown: list[dict] = []

    try:
        from agent_baton.core.engine.cost_estimator import forecast_plan as _forecast_plan
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        agents = _DEFAULT_ROSTER_BY_RISK.get(risk_level, ["developer"])
        steps = [
            PlanStep(
                step_id=f"1.{i + 1}",
                agent_name=agent,
                task_description=title,
                model="sonnet",
            )
            for i, agent in enumerate(agents)
        ]
        phase = PlanPhase(phase_id=1, name="Execution", steps=steps)
        stub_plan = MachinePlan(
            task_id="spec-enrich-stub",
            task_summary=title,
            risk_level=risk_level,
            phases=[phase],
        )
        fc = _forecast_plan(stub_plan)
        # cost_estimator.forecast_plan returns CostForecast dataclass
        mid = fc.total_cost_usd
        est_usd_low = round(0.75 * mid, 6)
        est_usd_mid = round(mid, 6)
        est_usd_high = round(1.25 * mid, 6)

        # Per-agent breakdown
        breakdown = [
            {"agent_name": step.agent_name, "model": "sonnet",
             "est_steps": 1, "est_tokens": tokens, "est_usd": round(cost_usd, 6)}
            for (_, tokens), cost_usd in zip(
                fc.per_step_tokens,
                [
                    (t / 1_000_000.0) * fc.total_cost_usd / max(fc.total_tokens, 1) * 1_000_000
                    for _, t in fc.per_step_tokens
                ],
            )
        ]

        # Attempt history-calibrated upgrade
        if _CENTRAL_DB_DEFAULT.exists():
            try:
                from agent_baton.core.observe.cost_forecaster import CostForecaster
                from agent_baton.models.execution import MachinePlan as _MP

                # Build a richer plan with the same stub for the history-aware forecaster
                h_fc = CostForecaster(str(_CENTRAL_DB_DEFAULT)).forecast(stub_plan)
                if h_fc.sample_size > 0:
                    cost_confidence = "high"
                    est_usd_low = h_fc.est_usd_low
                    est_usd_mid = h_fc.est_usd_mid
                    est_usd_high = h_fc.est_usd_high
                    breakdown = list(h_fc.breakdown)
            except Exception:  # noqa: BLE001
                logger.debug("History-calibrated cost forecast failed; using defaults", exc_info=True)

    except Exception:  # noqa: BLE001
        logger.debug("Cost forecast failed; returning zeros", exc_info=True)

    return EnrichmentData(
        risk_level=risk_level,
        guardrail_preset=guardrail_preset,
        required_reviewers=required_reviewers,
        signals_found=signals_found,
        confidence=confidence,
        est_usd_low=est_usd_low,
        est_usd_mid=est_usd_mid,
        est_usd_high=est_usd_high,
        cost_confidence=cost_confidence,
        breakdown=breakdown,
    )


def _run_enrichment(spec_id: str, store: "SpecDraftStore") -> None:
    """Best-effort worker: enrich *spec_id* and persist results.

    Swallows and logs all exceptions so it is safe to dispatch via
    ``asyncio.get_event_loop().run_in_executor``.

    Args:
        spec_id: ID of the SpecDraft to enrich.
        store: A ``SpecDraftStore`` instance to read/write the draft.
    """
    try:
        draft = store.get(spec_id)
        if draft is None:
            logger.warning("_run_enrichment: spec_draft %r not found", spec_id)
            return
        enrichment_data = enrich(draft.title, draft.body)
        store.update_enrichment(spec_id, enrichment_data)
    except Exception:  # noqa: BLE001
        logger.warning("_run_enrichment: failed for %r", spec_id, exc_info=True)
