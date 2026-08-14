"""Guardrail tests for the budget-tier auto-apply path (F066).

``BudgetTuner.auto_apply_recommendations`` documents a cost-safety guardrail:
budget *upgrades* are never applied without human sign-off, and low-confidence
recommendations are never applied at all.  ``ImprovementLoop.run_cycle``
likewise classifies every recommendation into ``auto_applied`` or ``escalated``.

These tests pin the *observable* consequence of that guardrail: a recommendation
the improvement loop reports as ``escalated`` must not change the budget tier
that the planner subsequently selects.  Anything the loop escalates is, by
definition, awaiting human review — the planner must keep using its own default.

The mirror case is pinned too: a high-confidence *downgrade* is a safe,
auto-applicable change and must still be honoured by the planner.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.planning.utils.risk_and_policy import select_budget_tier
from agent_baton.core.improve.loop import ImprovementLoop
from agent_baton.core.improve.scoring import PerformanceScorer
from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.learn.recommender import Recommender
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.improvement import ImprovementConfig
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord

TASK_TYPE = "guardrail_probe"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(estimated_tokens: int) -> AgentUsageRecord:
    return AgentUsageRecord(
        name="worker",
        model="sonnet",
        steps=1,
        retries=0,
        gate_results=[],
        estimated_tokens=estimated_tokens,
        duration_seconds=1.0,
    )


def _task(task_id: str, tokens: int, mode: str = TASK_TYPE) -> TaskUsageRecord:
    agents = [_agent(tokens)]
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-03-01T10:00:00",
        agents_used=agents,
        total_agents=len(agents),
        risk_level="LOW",
        sequencing_mode=mode,
        gates_passed=0,
        gates_failed=0,
        outcome="SHIP",
        notes="",
    )


@pytest.fixture()
def team_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated team-context root, with CentralStore pinned inside tmp_path."""
    import agent_baton.core.storage.central as central_mod

    monkeypatch.setattr(
        central_mod, "_CENTRAL_DB_DEFAULT", tmp_path / "central" / "central.db"
    )
    tc = tmp_path / "team-context"
    tc.mkdir(parents=True, exist_ok=True)
    return tc


def _write_usage_log(tc: Path, tokens: int, count: int) -> None:
    logger = UsageLogger(tc / "usage-log.jsonl")
    for i in range(count):
        logger.log(_task(f"t{i}", tokens))


def _build_loop(tc: Path, improvements_dir: Path) -> ImprovementLoop:
    triggers = MagicMock(spec=TriggerEvaluator)
    triggers.should_analyze.return_value = True
    triggers.detect_anomalies.return_value = []

    scorer = MagicMock(spec=PerformanceScorer)
    scorer.score_all.return_value = []

    recommender = Recommender(
        scorer=scorer,
        pattern_learner=PatternLearner(team_context_root=tc),
        budget_tuner=BudgetTuner(team_context_root=tc),
    )

    return ImprovementLoop(
        trigger_evaluator=triggers,
        recommender=recommender,
        scorer=scorer,
        config=ImprovementConfig(),
        improvements_dir=improvements_dir,
        maintainer_spawner=MagicMock(),
    )


def _budget_rec_ids(report, task_type: str) -> list[str]:
    return [
        r["rec_id"]
        for r in report.recommendations
        if r.get("category") == "budget_tier" and r.get("target") == task_type
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEscalatedUpgradeDoesNotReachPlanner:
    """A low-confidence upgrade the loop escalates must not raise the tier."""

    def test_low_confidence_upgrade_is_escalated_and_not_honoured(
        self, team_context: Path, tmp_path: Path
    ):
        # 3 records * 45,000 tokens => median 45,000.
        # median lands in the "lean" tier but exceeds 80% of its ceiling
        # (40,000) => UPGRADE lean -> standard.  sample_size 3 => confidence 0.3.
        _write_usage_log(team_context, tokens=45_000, count=3)

        loop = _build_loop(team_context, tmp_path / "improvements")
        report = loop.run_cycle(force=True)

        rec_ids = _budget_rec_ids(report, TASK_TYPE)
        assert rec_ids, "expected a budget_tier recommendation for the probe task type"
        rec = next(
            r for r in report.recommendations if r["rec_id"] == rec_ids[0]
        )
        assert rec["proposed_change"]["from"] == "lean"
        assert rec["proposed_change"]["to"] == "standard"
        assert rec["confidence"] == pytest.approx(0.3)

        # The loop reports the recommendation as NOT applied.
        assert rec_ids[0] in report.escalated
        assert rec_ids[0] not in report.auto_applied

        # Therefore the planner's read path must not have applied it either.
        tier = select_budget_tier(
            TASK_TYPE,
            agent_count=1,
            budget_tuner=BudgetTuner(team_context_root=team_context),
        )
        assert tier == "lean", (
            "an escalated (never-applied) budget upgrade silently raised the "
            f"planner's tier to {tier!r}"
        )

    def test_planner_select_budget_tier_ignores_escalated_upgrade(
        self, team_context: Path, tmp_path: Path
    ):
        """Same guarantee, exercised through IntelligentPlanner itself."""
        _write_usage_log(team_context, tokens=45_000, count=3)

        loop = _build_loop(team_context, tmp_path / "improvements")
        loop.run_cycle(force=True)

        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner(team_context_root=team_context)
        assert planner._select_budget_tier(TASK_TYPE, agent_count=1) == "lean"

    def test_high_confidence_upgrade_is_still_not_honoured(
        self, team_context: Path, tmp_path: Path
    ):
        """Upgrades are never auto-applied, regardless of confidence."""
        # 10 records => confidence 1.0, still an upgrade lean -> standard.
        _write_usage_log(team_context, tokens=45_000, count=10)

        loop = _build_loop(team_context, tmp_path / "improvements")
        report = loop.run_cycle(force=True)

        rec_ids = _budget_rec_ids(report, TASK_TYPE)
        assert rec_ids
        assert rec_ids[0] in report.escalated
        assert rec_ids[0] not in report.auto_applied

        tier = select_budget_tier(
            TASK_TYPE,
            agent_count=1,
            budget_tuner=BudgetTuner(team_context_root=team_context),
        )
        assert tier == "lean"


class TestSafeDowngradeIsHonoured:
    """Mirror case: a high-confidence downgrade is safe and must be applied."""

    def test_high_confidence_downgrade_overrides_agent_count_default(
        self, team_context: Path
    ):
        rec = BudgetRecommendation(
            task_type=TASK_TYPE,
            current_tier="full",
            recommended_tier="lean",
            reason="p95 usage sits well below the full-tier floor",
            avg_tokens_used=20_000,
            median_tokens_used=20_000,
            p95_tokens_used=30_000,
            sample_size=9,
            confidence=0.9,
            potential_savings=100_000,
        )
        (team_context / "budget-recommendations.json").write_text(
            json.dumps([rec.to_dict()], indent=2) + "\n", encoding="utf-8"
        )

        # agent_count=8 would default to "full"; the downgrade must win.
        tier = select_budget_tier(
            TASK_TYPE,
            agent_count=8,
            budget_tuner=BudgetTuner(team_context_root=team_context),
        )
        assert tier == "lean"

    def test_no_recommendation_falls_back_to_agent_count_default(
        self, team_context: Path
    ):
        tuner = BudgetTuner(team_context_root=team_context)
        assert select_budget_tier(TASK_TYPE, agent_count=1, budget_tuner=tuner) == "lean"
        assert (
            select_budget_tier(TASK_TYPE, agent_count=4, budget_tuner=tuner)
            == "standard"
        )
        assert select_budget_tier(TASK_TYPE, agent_count=9, budget_tuner=tuner) == "full"
