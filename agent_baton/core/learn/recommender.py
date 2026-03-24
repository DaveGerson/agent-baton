"""Unified Recommender — runs all analysis engines and produces ranked
Recommendation objects with confidence, risk, and auto_applicable flags.

Categories: agent_prompt, budget_tier, routing, sequencing, gate_config, roster.
Deduplicates by (category, target) and ranks by impact.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agent_baton.core.improve.scoring import PerformanceScorer, AgentScorecard
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.improve.evolution import PromptEvolutionEngine
from agent_baton.models.improvement import Recommendation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Recommender:
    """Aggregate analysis from all engines into a unified recommendation list.

    Guardrail enforcement:
    - Prompt changes: NEVER auto-apply (always escalate to human)
    - Budget changes: auto-apply only DOWNWARD (cheaper tier, never upgrade)
    - Routing changes: auto-apply only if confidence >= 0.9 and additive
    - Sequencing changes: auto-apply only if confidence >= 0.8 and success >= 0.9
    - All other categories: only LOW-risk recommendations auto-apply
    """

    def __init__(
        self,
        scorer: PerformanceScorer | None = None,
        pattern_learner: PatternLearner | None = None,
        budget_tuner: BudgetTuner | None = None,
        evolution_engine: PromptEvolutionEngine | None = None,
    ) -> None:
        self._scorer = scorer or PerformanceScorer()
        self._learner = pattern_learner or PatternLearner()
        self._tuner = budget_tuner or BudgetTuner()
        self._evolution = evolution_engine or PromptEvolutionEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> list[Recommendation]:
        """Run all analysis engines and return a deduplicated, ranked list of
        recommendations.

        Order: highest confidence first, then by risk (low before high).
        """
        recs: list[Recommendation] = []

        recs.extend(self._budget_recommendations())
        recs.extend(self._prompt_recommendations())
        recs.extend(self._sequencing_recommendations())
        recs.extend(self._scoring_recommendations())

        # Deduplicate by (category, target) — keep highest confidence
        seen: dict[tuple[str, str], Recommendation] = {}
        for rec in recs:
            key = (rec.category, rec.target)
            if key not in seen or rec.confidence > seen[key].confidence:
                seen[key] = rec
        deduped = list(seen.values())

        # Rank: confidence desc, then risk asc (low < medium < high)
        _risk_order = {"low": 0, "medium": 1, "high": 2}
        deduped.sort(
            key=lambda r: (-r.confidence, _risk_order.get(r.risk, 1)),
        )
        return deduped

    # ------------------------------------------------------------------
    # Budget recommendations
    # ------------------------------------------------------------------

    def _budget_recommendations(self) -> list[Recommendation]:
        recs: list[Recommendation] = []
        budget_recs = self._tuner.analyze()

        _tier_order = {"lean": 0, "standard": 1, "full": 2}

        for br in budget_recs:
            is_downgrade = (
                _tier_order.get(br.recommended_tier, 0)
                < _tier_order.get(br.current_tier, 0)
            )

            # Guardrail: only auto-apply downgrades
            auto = is_downgrade and br.confidence >= 0.7 and br.confidence > 0
            risk = "low" if is_downgrade else "medium"

            recs.append(Recommendation(
                rec_id=_make_id("budget"),
                category="budget_tier",
                target=br.task_type,
                action="downgrade budget" if is_downgrade else "upgrade budget",
                description=br.reason,
                evidence=[
                    f"avg={br.avg_tokens_used:,}, median={br.median_tokens_used:,}, "
                    f"p95={br.p95_tokens_used:,}, samples={br.sample_size}",
                ],
                confidence=br.confidence,
                risk=risk,
                auto_applicable=auto,
                proposed_change={
                    "type": "budget_tier",
                    "task_type": br.task_type,
                    "from": br.current_tier,
                    "to": br.recommended_tier,
                },
                rollback_spec={
                    "type": "budget_tier",
                    "task_type": br.task_type,
                    "from": br.recommended_tier,
                    "to": br.current_tier,
                },
                created_at=_now_iso(),
            ))
        return recs

    # ------------------------------------------------------------------
    # Prompt recommendations (NEVER auto-apply)
    # ------------------------------------------------------------------

    def _prompt_recommendations(self) -> list[Recommendation]:
        recs: list[Recommendation] = []
        proposals = self._evolution.analyze()

        for proposal in proposals:
            confidence = 0.5 if proposal.priority == "normal" else 0.7
            recs.append(Recommendation(
                rec_id=_make_id("prompt"),
                category="agent_prompt",
                target=proposal.agent_name,
                action="evolve prompt",
                description="; ".join(proposal.issues),
                evidence=[f"suggestion: {s}" for s in proposal.suggestions],
                confidence=confidence,
                risk="high",       # Prompt changes are always high risk
                auto_applicable=False,  # GUARDRAIL: never auto-apply prompt changes
                proposed_change={
                    "type": "prompt_evolution",
                    "agent_name": proposal.agent_name,
                    "suggestions": proposal.suggestions,
                },
                rollback_spec={
                    "type": "prompt_rollback",
                    "agent_name": proposal.agent_name,
                },
                created_at=_now_iso(),
            ))
        return recs

    # ------------------------------------------------------------------
    # Sequencing recommendations
    # ------------------------------------------------------------------

    def _sequencing_recommendations(self) -> list[Recommendation]:
        recs: list[Recommendation] = []
        patterns = self._learner.load_patterns()

        for pattern in patterns:
            if pattern.confidence < 0.7:
                continue

            # Guardrail: auto-apply only if confidence >= 0.8 AND success >= 0.9
            auto = (
                pattern.confidence >= 0.8
                and pattern.success_rate >= 0.9
            )
            risk = "low" if auto else "medium"

            recs.append(Recommendation(
                rec_id=_make_id("seq"),
                category="sequencing",
                target=pattern.task_type,
                action="apply learned sequence",
                description=(
                    f"Pattern {pattern.pattern_id}: {pattern.recommended_template} "
                    f"({pattern.success_rate:.0%} success over {pattern.sample_size} samples)"
                ),
                evidence=pattern.evidence[:5],
                confidence=pattern.confidence,
                risk=risk,
                auto_applicable=auto,
                proposed_change={
                    "type": "sequencing",
                    "task_type": pattern.task_type,
                    "template": pattern.recommended_template,
                    "agents": pattern.recommended_agents,
                },
                rollback_spec={
                    "type": "sequencing_rollback",
                    "task_type": pattern.task_type,
                },
                created_at=_now_iso(),
            ))
        return recs

    # ------------------------------------------------------------------
    # Scoring-based recommendations (agent health)
    # ------------------------------------------------------------------

    def _scoring_recommendations(self) -> list[Recommendation]:
        recs: list[Recommendation] = []
        scorecards = self._scorer.score_all()

        for sc in scorecards:
            if sc.health != "needs-improvement":
                continue

            # Routing recommendation: suggest reducing reliance on this agent
            recs.append(Recommendation(
                rec_id=_make_id("route"),
                category="routing",
                target=sc.agent_name,
                action="reduce routing weight",
                description=(
                    f"Agent {sc.agent_name} has health=needs-improvement "
                    f"(first_pass={sc.first_pass_rate:.0%}, retries={sc.retry_rate:.1f})"
                ),
                evidence=[
                    f"uses={sc.times_used}, neg_mentions={sc.negative_mentions}",
                ],
                confidence=min(1.0, sc.times_used / 10),
                risk="medium",
                # Guardrail: routing changes auto-apply only if confidence >= 0.9
                # and additive (reducing weight is subtractive, so NOT auto-applicable)
                auto_applicable=False,
                proposed_change={
                    "type": "routing",
                    "agent_name": sc.agent_name,
                    "action": "reduce_weight",
                },
                rollback_spec={
                    "type": "routing_rollback",
                    "agent_name": sc.agent_name,
                },
                created_at=_now_iso(),
            ))
        return recs
