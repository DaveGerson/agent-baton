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
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer


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
    # Decision: 8 individual tests collapsed into one parameterized test.
    # Each tuple is an independent boundary/threshold case for the health
    # classifier.  The "unused" case is included as a tuple so it won't be
    # lost, while the boundary-straddling values (0.8 exactly, 0.5 exactly)
    # are preserved as separate tuples.
    @pytest.mark.parametrize("times_used,first_pass_rate,neg_mentions,expected_health", [
        (0,  0.0, 0, "unused"),
        (5,  0.9, 0, "strong"),           # well above 0.8 threshold
        (5,  0.8, 0, "strong"),           # exactly at 0.8 threshold
        (5,  0.6, 0, "adequate"),         # below 0.8, above 0.5
        (5,  0.9, 1, "adequate"),         # high pass but has negative mention
        (2,  0.5, 0, "adequate"),         # exactly at 0.5 threshold
        (5,  0.3, 0, "needs-improvement"),
        (10, 0.4, 0, "needs-improvement"),# just below 0.5
    ])
    def test_health(self, times_used, first_pass_rate, neg_mentions, expected_health):
        sc = AgentScorecard(
            agent_name="arch",
            times_used=times_used,
            first_pass_rate=first_pass_rate,
            negative_mentions=neg_mentions,
        )
        assert sc.health == expected_health


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
        assert scorer.score_agent("arch").times_used == 2

    # Decision: 5 metric tests consolidated — they all call score_agent on a
    # controlled dataset and assert a single computed field.  Keeping them
    # as parametrize tuples means each metric can fail independently.
    @pytest.mark.parametrize("setup,metric,expected", [
        ("all_zero_retries",  "first_pass_rate", 1.0),
        ("mixed_retries",     "first_pass_rate", 2 / 3),
        ("retries_3_and_1",   "retry_rate",      2.0),
        ("tokens_1k_2k",      "total_estimated_tokens", 3000),
        ("tokens_1k_3k",      "avg_tokens_per_use",     2000),
    ])
    def test_numeric_metrics(self, tmp_path: Path, setup, metric, expected):
        logger, _, scorer = _setup_scorer(tmp_path / setup)
        if setup == "all_zero_retries":
            logger.log(_task("t1", [_agent("arch", retries=0)]))
            logger.log(_task("t2", [_agent("arch", retries=0)]))
        elif setup == "mixed_retries":
            logger.log(_task("t1", [_agent("arch", retries=0)]))
            logger.log(_task("t2", [_agent("arch", retries=1)]))
            logger.log(_task("t3", [_agent("arch", retries=0)]))
        elif setup == "retries_3_and_1":
            logger.log(_task("t1", [_agent("arch", retries=3)]))
            logger.log(_task("t2", [_agent("arch", retries=1)]))
        elif setup == "tokens_1k_2k":
            logger.log(_task("t1", [_agent("arch", tokens=1000)]))
            logger.log(_task("t2", [_agent("arch", tokens=2000)]))
        elif setup == "tokens_1k_3k":
            logger.log(_task("t1", [_agent("arch", tokens=1000)]))
            logger.log(_task("t2", [_agent("arch", tokens=3000)]))
        sc = scorer.score_agent("arch")
        assert getattr(sc, metric) == pytest.approx(expected)

    @pytest.mark.parametrize("gate_results,expected_rate", [
        (["PASS", "PASS", "FAIL"], pytest.approx(2 / 3)),
        ([],                       None),
    ])
    def test_gate_pass_rate(self, tmp_path: Path, gate_results, expected_rate):
        logger, _, scorer = _setup_scorer(tmp_path / str(len(gate_results)))
        logger.log(_task("t1", [_agent("arch", gate_results=gate_results)]))
        assert scorer.score_agent("arch").gate_pass_rate == expected_rate

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
        assert scorer.score_agent("arch").positive_mentions >= 1

    def test_negative_mentions_from_what_didnt(self, tmp_path: Path):
        logger, retro_engine, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            what_didnt=[AgentOutcome(name="arch", issues="Missed edge case")],
        )
        retro_engine.save(retro)
        assert scorer.score_agent("arch").negative_mentions >= 1

    def test_knowledge_gaps_cited(self, tmp_path: Path):
        logger, retro_engine, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            knowledge_gaps=[KnowledgeGap(description="arch lacks Redis knowledge",
                                          affected_agent="arch")],
        )
        retro_engine.save(retro)
        assert scorer.score_agent("arch").knowledge_gaps_cited >= 1


# ---------------------------------------------------------------------------
# PerformanceScorer.score_all
# ---------------------------------------------------------------------------

class TestScoreAll:
    def test_returns_scorecards_for_all_agents(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch"), _agent("be")]))
        names = {sc.agent_name for sc in scorer.score_all()}
        assert "arch" in names
        assert "be" in names

    def test_returns_empty_when_no_usage_data(self, tmp_path: Path):
        _, _, scorer = _setup_scorer(tmp_path)
        assert scorer.score_all() == []

    def test_excludes_agents_with_zero_uses(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        assert all(sc.times_used > 0 for sc in scorer.score_all())

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
        assert "No usage data available" in scorer.generate_report()

    # Decision: 4 report-content checks collapsed — all use the same setup and
    # assert distinct substrings.  Fails independently because each substring
    # is checked separately.
    def test_report_content(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch"), _agent("be")]))
        report = scorer.generate_report()
        assert report.startswith("# Agent Performance Scorecards")
        assert "arch" in report
        assert "Strong" in report          # health group heading
        assert "3 total agent uses" in report


# ---------------------------------------------------------------------------
# PerformanceScorer.write_report
# ---------------------------------------------------------------------------

class TestWriteReport:
    # Decision: 4 write_report tests merged into 2.  File existence, return
    # value, and content correctness are all checked in one test because they
    # are consequences of the same single call.  Parent-dir creation is kept
    # separate because it exercises a distinct code path.
    def test_write_report_creates_file_with_correct_content(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "scorecards.md"
        result = scorer.write_report(out_path)
        assert result == out_path
        assert result.exists()
        assert result.read_text(encoding="utf-8").startswith("# Agent Performance Scorecards")

    def test_write_report_creates_parent_dirs(self, tmp_path: Path):
        logger, _, scorer = _setup_scorer(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        out_path = tmp_path / "reports" / "scorecards.md"
        scorer.write_report(out_path)
        assert out_path.exists()


# ---------------------------------------------------------------------------
# FIX-7: PerformanceScorer reads retros from SQLite when storage is provided
# ---------------------------------------------------------------------------

class TestPerformanceScorerSQLiteStorage:
    """When a storage backend is passed, retros come from it, not the filesystem.

    This exercises the path that was broken in SQLite-mode projects where
    retrospectives are only written to the DB.
    """

    def _make_retro(
        self,
        task_id: str,
        agent_name: str,
        *,
        worked: bool = False,
        didnt: bool = False,
        gap: bool = False,
    ) -> Retrospective:
        return Retrospective(
            task_id=task_id,
            task_name="Test task",
            timestamp="2026-03-01T00:00:00",
            what_worked=(
                [AgentOutcome(name=agent_name, worked_well="Nailed it")]
                if worked
                else []
            ),
            what_didnt=(
                [AgentOutcome(name=agent_name, issues="Stumbled")]
                if didnt
                else []
            ),
            knowledge_gaps=(
                [KnowledgeGap(description=f"{agent_name} lacked context")]
                if gap
                else []
            ),
        )

    def _storage_with_retros(self, retros: list[Retrospective]):
        """Build a minimal mock storage backend that serves the given retros."""
        from unittest.mock import MagicMock
        storage = MagicMock()
        task_ids = [r.task_id for r in retros]
        storage.list_retrospective_ids.return_value = task_ids
        retro_map = {r.task_id: r for r in retros}
        storage.load_retrospective.side_effect = lambda tid: retro_map.get(tid)
        return storage

    def test_positive_mentions_read_from_storage_backend(self, tmp_path: Path) -> None:
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch")]))

        retro = self._make_retro("t1", "arch", worked=True)
        storage = self._storage_with_retros([retro])
        scorer = PerformanceScorer(usage_logger=logger, storage=storage)

        sc = scorer.score_agent("arch")
        assert sc.positive_mentions >= 1

    def test_negative_mentions_read_from_storage_backend(self, tmp_path: Path) -> None:
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch")]))

        retro = self._make_retro("t1", "arch", didnt=True)
        storage = self._storage_with_retros([retro])
        scorer = PerformanceScorer(usage_logger=logger, storage=storage)

        sc = scorer.score_agent("arch")
        assert sc.negative_mentions >= 1

    def test_knowledge_gaps_read_from_storage_backend(self, tmp_path: Path) -> None:
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch")]))

        retro = self._make_retro("t1", "arch", gap=True)
        storage = self._storage_with_retros([retro])
        scorer = PerformanceScorer(usage_logger=logger, storage=storage)

        sc = scorer.score_agent("arch")
        assert sc.knowledge_gaps_cited >= 1

    def test_filesystem_retros_ignored_when_storage_provided(self, tmp_path: Path) -> None:
        """Storage backend takes priority; on-disk retro files are not read."""
        log_file = tmp_path / "usage.jsonl"
        retros_dir = tmp_path / "retros"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch")]))

        # Write a filesystem retro claiming arch worked well.
        retro_engine = RetrospectiveEngine(retros_dir)
        retro_fs = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            what_worked=[AgentOutcome(name="arch", worked_well="filesystem retro")],
        )
        retro_fs_path = retros_dir / "t1.md"
        retros_dir.mkdir(parents=True, exist_ok=True)
        retro_fs_path.write_text(retro_engine.save(retro_fs).read_text(encoding="utf-8"), encoding="utf-8")

        # Storage returns NO retros — so positive_mentions should be 0.
        storage = self._storage_with_retros([])
        scorer = PerformanceScorer(
            usage_logger=logger,
            retro_engine=retro_engine,
            storage=storage,
        )

        sc = scorer.score_agent("arch")
        assert sc.positive_mentions == 0, (
            "Filesystem retros must not be read when storage backend is configured"
        )

    def test_storage_exception_falls_through_gracefully(self, tmp_path: Path) -> None:
        """If list_retrospective_ids raises, scorer degrades to zero qualitative signals."""
        from unittest.mock import MagicMock
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch")]))

        storage = MagicMock()
        storage.list_retrospective_ids.side_effect = RuntimeError("db locked")
        scorer = PerformanceScorer(usage_logger=logger, storage=storage)

        # Should not raise; qualitative signals default to 0.
        sc = scorer.score_agent("arch")
        assert sc.times_used == 1
        assert sc.positive_mentions == 0
        assert sc.negative_mentions == 0
