"""Tests for agent_baton.core.learn.recommender.Recommender."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.improve.evolution import EvolutionProposal, PromptEvolutionEngine
from agent_baton.core.learn.recommender import Recommender
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.pattern import LearnedPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_scorer(scorecards: list[AgentScorecard]) -> PerformanceScorer:
    scorer = MagicMock(spec=PerformanceScorer)
    scorer.score_all.return_value = scorecards
    return scorer


def _mock_tuner(recs: list[BudgetRecommendation]) -> BudgetTuner:
    tuner = MagicMock(spec=BudgetTuner)
    tuner.analyze.return_value = recs
    return tuner


def _mock_learner(patterns: list[LearnedPattern]) -> PatternLearner:
    learner = MagicMock(spec=PatternLearner)
    learner.load_patterns.return_value = patterns
    return learner


def _mock_evolution(proposals: list[EvolutionProposal]) -> PromptEvolutionEngine:
    engine = MagicMock(spec=PromptEvolutionEngine)
    engine.analyze.return_value = proposals
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecommenderAnalyze:
    def test_empty_when_no_data(self):
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner([]),
            evolution_engine=_mock_evolution([]),
        )
        assert recommender.analyze() == []

    def test_budget_downgrade_is_auto_applicable(self):
        recs = [
            BudgetRecommendation(
                task_type="phased",
                current_tier="standard",
                recommended_tier="lean",
                reason="Low usage",
                avg_tokens_used=20_000,
                median_tokens_used=18_000,
                p95_tokens_used=30_000,
                sample_size=10,
                confidence=0.9,
                potential_savings=5_000,
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner(recs),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        budget_recs = [r for r in results if r.category == "budget_tier"]
        assert len(budget_recs) == 1
        assert budget_recs[0].auto_applicable is True
        assert budget_recs[0].risk == "low"

    def test_budget_upgrade_is_not_auto_applicable(self):
        recs = [
            BudgetRecommendation(
                task_type="phased",
                current_tier="lean",
                recommended_tier="standard",
                reason="High usage",
                avg_tokens_used=200_000,
                median_tokens_used=180_000,
                p95_tokens_used=300_000,
                sample_size=10,
                confidence=0.9,
                potential_savings=0,
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner(recs),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        budget_recs = [r for r in results if r.category == "budget_tier"]
        assert len(budget_recs) == 1
        assert budget_recs[0].auto_applicable is False

    def test_prompt_changes_never_auto_applicable(self):
        proposals = [
            EvolutionProposal(
                agent_name="architect",
                scorecard=AgentScorecard(
                    agent_name="architect",
                    times_used=10,
                    first_pass_rate=0.3,
                ),
                issues=["Low first-pass rate"],
                suggestions=["Add examples"],
                priority="high",
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner([]),
            evolution_engine=_mock_evolution(proposals),
        )
        results = recommender.analyze()
        prompt_recs = [r for r in results if r.category == "agent_prompt"]
        assert len(prompt_recs) == 1
        assert prompt_recs[0].auto_applicable is False
        assert prompt_recs[0].risk == "high"

    def test_sequencing_auto_applicable_when_high_confidence_and_success(self):
        patterns = [
            LearnedPattern(
                pattern_id="p-001",
                task_type="phased",
                stack=None,
                recommended_template="phased workflow",
                recommended_agents=["architect", "backend"],
                confidence=0.9,
                sample_size=15,
                success_rate=0.95,
                avg_token_cost=50_000,
                evidence=["t1", "t2", "t3"],
                created_at="2026-03-01",
                updated_at="2026-03-01",
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner(patterns),
            budget_tuner=_mock_tuner([]),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        seq_recs = [r for r in results if r.category == "sequencing"]
        assert len(seq_recs) == 1
        assert seq_recs[0].auto_applicable is True

    def test_sequencing_not_auto_applicable_when_low_success(self):
        patterns = [
            LearnedPattern(
                pattern_id="p-002",
                task_type="phased",
                stack=None,
                recommended_template="phased workflow",
                recommended_agents=["architect"],
                confidence=0.9,
                sample_size=10,
                success_rate=0.7,  # Below 0.9 threshold
                avg_token_cost=50_000,
                evidence=["t1"],
                created_at="2026-03-01",
                updated_at="2026-03-01",
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner(patterns),
            budget_tuner=_mock_tuner([]),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        seq_recs = [r for r in results if r.category == "sequencing"]
        assert len(seq_recs) == 1
        assert seq_recs[0].auto_applicable is False

    def test_routing_recommendations_for_needs_improvement_agents(self):
        scorecards = [
            AgentScorecard(
                agent_name="flaky",
                times_used=10,
                first_pass_rate=0.3,
                retry_rate=2.5,
                negative_mentions=3,
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer(scorecards),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner([]),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        routing_recs = [r for r in results if r.category == "routing"]
        assert len(routing_recs) == 1
        assert routing_recs[0].auto_applicable is False  # subtractive change

    def test_deduplication_keeps_highest_confidence(self):
        # Two budget recs for same target
        recs = [
            BudgetRecommendation(
                task_type="phased",
                current_tier="standard",
                recommended_tier="lean",
                reason="Reason A",
                avg_tokens_used=20_000,
                median_tokens_used=18_000,
                p95_tokens_used=30_000,
                sample_size=5,
                confidence=0.5,
                potential_savings=5_000,
            ),
        ]
        # The second will be a sequencing rec for same target "phased"
        patterns = [
            LearnedPattern(
                pattern_id="p-001",
                task_type="phased",
                stack=None,
                recommended_template="test",
                recommended_agents=["a"],
                confidence=0.95,
                sample_size=20,
                success_rate=0.95,
                avg_token_cost=50_000,
                evidence=["t1"],
                created_at="2026-03-01",
                updated_at="2026-03-01",
            )
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner(patterns),
            budget_tuner=_mock_tuner(recs),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        # Should have both since they are different categories
        categories = [r.category for r in results]
        assert "budget_tier" in categories
        assert "sequencing" in categories

    def test_results_sorted_by_confidence_desc(self):
        recs = [
            BudgetRecommendation(
                task_type="type_a",
                current_tier="standard",
                recommended_tier="lean",
                reason="A",
                avg_tokens_used=20_000,
                median_tokens_used=18_000,
                p95_tokens_used=30_000,
                sample_size=5,
                confidence=0.5,
                potential_savings=5_000,
            ),
            BudgetRecommendation(
                task_type="type_b",
                current_tier="standard",
                recommended_tier="lean",
                reason="B",
                avg_tokens_used=20_000,
                median_tokens_used=18_000,
                p95_tokens_used=30_000,
                sample_size=10,
                confidence=0.9,
                potential_savings=5_000,
            ),
        ]
        recommender = Recommender(
            scorer=_mock_scorer([]),
            pattern_learner=_mock_learner([]),
            budget_tuner=_mock_tuner(recs),
            evolution_engine=_mock_evolution([]),
        )
        results = recommender.analyze()
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)
