"""Tests for agent_baton.core.scoring.PerformanceScorer and AgentScorecard."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
)
from agent_baton.core.usage import UsageLogger
from agent_baton.core.retrospective import RetrospectiveEngine
from agent_baton.core.scoring import AgentScorecard, PerformanceScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(
    name: str,
    retries: int = 0,
    gate_results: list[str] | None = None,
    model: str = "sonnet",
    tokens: int = 1000,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model=model,
        steps=1,
        retries=retries,
        gate_results=gate_results if gate_results is not None else [],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str,
    agents: list[AgentUsageRecord],
    timestamp: str = "2026-03-01T10:00:00",
    risk_level: str = "LOW",
    outcome: str = "SHIP",
    gates_passed: int = 0,
    gates_failed: int = 0,
) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agents,
        total_agents=len(agents),
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome=outcome,
        notes="",
    )


def _setup_scorer(tmp_path: Path) -> tuple[UsageLogger, RetrospectiveEngine, PerformanceScorer]:
    log_file = tmp_path / "usage.jsonl"
    retros_dir = tmp_path / "retros"
    logger = UsageLogger(log_file)
    retro_engine = RetrospectiveEngine(retros_dir)
    scorer = PerformanceScorer(logger, retro_engine)
    return logger, retro_engine, scorer


# ---------------------------------------------------------------------------
# AgentScorecard.health property
# ---------------------------------------------------------------------------

class TestAgentScorecardHealth:
    def test_unused_for_zero_uses(self):
        sc = AgentScorecard(agent_name="ghost", times_used=0)
        assert sc.health == "unused"

    def test_strong_for_high_first_pass_and_no_negatives(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=5,
            first_pass_rate=0.9,
            negative_mentions=0,
        )
        assert sc.health == "strong"

    def test_strong_threshold_is_0_8(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=5,
            first_pass_rate=0.8,
            negative_mentions=0,
        )
        assert sc.health == "strong"

    def test_adequate_when_first_pass_below_0_8(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=5,
            first_pass_rate=0.6,
            negative_mentions=0,
        )
        assert sc.health == "adequate"

    def test_adequate_when_high_pass_but_has_negative_mentions(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=5,
            first_pass_rate=0.9,
            negative_mentions=1,
        )
        # Not "strong" because of negative mention, but first_pass >= 0.5
        assert sc.health == "adequate"

    def test_needs_improvement_for_low_first_pass(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=5,
            first_pass_rate=0.3,
            negative_mentions=0,
        )
        assert sc.health == "needs-improvement"

    def test_needs_improvement_threshold_is_below_0_5(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=10,
            first_pass_rate=0.4,
        )
        assert sc.health == "needs-improvement"

    def test_adequate_at_exactly_0_5_first_pass(self):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=2,
            first_pass_rate=0.5,
        )
        assert sc.health == "adequate"


# ---------------------------------------------------------------------------
# PerformanceScorer.score_agent
# ---------------------------------------------------------------------------

class TestScoreAgent:
    def test_returns_empty_scorecard_for_unknown_agent(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        sc = scorer.score_agent("ghost")
        assert sc.times_used == 0
        assert sc.first_pass_rate == 0.0
        assert sc.gate_pass_rate is None

    def test_times_used_correct(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        logger.log(_task("t2", [_agent("arch"), _agent("be")]))
        sc = scorer.score_agent("arch")
        assert sc.times_used == 2

    def test_first_pass_rate_all_zero_retries(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=0)]))
        sc = scorer.score_agent("arch")
        assert sc.first_pass_rate == 1.0

    def test_first_pass_rate_mixed_retries(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=1)]))
        logger.log(_task("t3", [_agent("arch", retries=0)]))
        sc = scorer.score_agent("arch")
        assert sc.first_pass_rate == pytest.approx(2 / 3)

    def test_retry_rate(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=3)]))
        logger.log(_task("t2", [_agent("arch", retries=1)]))
        sc = scorer.score_agent("arch")
        assert sc.retry_rate == 2.0

    def test_gate_pass_rate(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", gate_results=["PASS", "PASS", "FAIL"])]))
        sc = scorer.score_agent("arch")
        assert sc.gate_pass_rate == pytest.approx(2 / 3)

    def test_gate_pass_rate_none_when_no_gates(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", gate_results=[])]))
        sc = scorer.score_agent("arch")
        assert sc.gate_pass_rate is None

    def test_total_estimated_tokens(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", tokens=1000)]))
        logger.log(_task("t2", [_agent("arch", tokens=2000)]))
        sc = scorer.score_agent("arch")
        assert sc.total_estimated_tokens == 3000

    def test_avg_tokens_per_use(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", tokens=1000)]))
        logger.log(_task("t2", [_agent("arch", tokens=3000)]))
        sc = scorer.score_agent("arch")
        assert sc.avg_tokens_per_use == 2000

    def test_models_used(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", model="sonnet")]))
        logger.log(_task("t2", [_agent("arch", model="opus")]))
        logger.log(_task("t3", [_agent("arch", model="sonnet")]))
        sc = scorer.score_agent("arch")
        assert sc.models_used["sonnet"] == 2
        assert sc.models_used["opus"] == 1


# ---------------------------------------------------------------------------
# PerformanceScorer — retrospective signals
# ---------------------------------------------------------------------------

class TestScoreAgentRetroSignals:
    def test_positive_mentions_from_what_worked(self, tmp_path: Path):
        logger, retro_engine, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            what_worked=[AgentOutcome(name="arch", worked_well="Did great")],
        )
        retro_engine.save(retro)
        sc = scorer.score_agent("arch")
        assert sc.positive_mentions >= 1

    def test_negative_mentions_from_what_didnt(self, tmp_path: Path):
        logger, retro_engine, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            what_didnt=[AgentOutcome(name="arch", issues="Missed edge case")],
        )
        retro_engine.save(retro)
        sc = scorer.score_agent("arch")
        assert sc.negative_mentions >= 1

    def test_knowledge_gaps_cited(self, tmp_path: Path):
        logger, retro_engine, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            knowledge_gaps=[KnowledgeGap(description="arch lacks Redis knowledge",
                                          affected_agent="arch")],
        )
        retro_engine.save(retro)
        sc = scorer.score_agent("arch")
        assert sc.knowledge_gaps_cited >= 1


# ---------------------------------------------------------------------------
# PerformanceScorer.score_all
# ---------------------------------------------------------------------------

class TestScoreAll:
    def test_returns_scorecards_for_all_agents(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch"), _agent("be")]))
        scorecards = scorer.score_all()
        names = {sc.agent_name for sc in scorecards}
        assert "arch" in names
        assert "be" in names

    def test_returns_empty_when_no_usage_data(self, tmp_path: Path):
        _, _, scorer = _setup_scorer(tmp_path)
        assert scorer.score_all() == []

    def test_excludes_agents_with_zero_uses(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        scorecards = scorer.score_all()
        assert all(sc.times_used > 0 for sc in scorecards)

    def test_sorted_by_agent_name(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("zed"), _agent("alpha"), _agent("mango")]))
        names = [sc.agent_name for sc in scorer.score_all()]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# PerformanceScorer.generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_no_data_message_when_empty(self, tmp_path: Path):
        _, _, scorer = _setup_scorer(tmp_path)
        report = scorer.generate_report()
        assert "No usage data available" in report

    def test_starts_with_h1(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        assert scorer.generate_report().startswith("# Agent Performance Scorecards")

    def test_includes_agent_section(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        report = scorer.generate_report()
        assert "arch" in report

    def test_groups_by_health_status(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        # arch: all zero retries -> first_pass_rate=1.0 -> "strong"
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        report = scorer.generate_report()
        assert "Strong" in report

    def test_total_uses_count_in_report(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch"), _agent("be")]))
        logger.log(_task("t2", [_agent("arch")]))
        report = scorer.generate_report()
        assert "3 total agent uses" in report


# ---------------------------------------------------------------------------
# PerformanceScorer.write_report
# ---------------------------------------------------------------------------

class TestWriteReport:
    def test_creates_file_on_disk(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "scorecards.md"
        result = scorer.write_report(out_path)
        assert result.exists()

    def test_returns_the_output_path(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "scorecards.md"
        result = scorer.write_report(out_path)
        assert result == out_path

    def test_file_content_is_markdown(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "scorecards.md"
        scorer.write_report(out_path)
        content = out_path.read_text(encoding="utf-8")
        assert content.startswith("# Agent Performance Scorecards")

    def test_creates_parent_dirs(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "reports" / "scorecards.md"
        scorer.write_report(out_path)
        assert out_path.exists()
