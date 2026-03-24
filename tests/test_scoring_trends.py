"""Tests for PerformanceScorer.detect_trends() enhancement."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.improve.scoring import PerformanceScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(name: str, retries: int = 0) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name, model="sonnet", steps=1, retries=retries,
        gate_results=[], estimated_tokens=1000, duration_seconds=1.0,
    )


def _task(task_id: str, agents: list[AgentUsageRecord]) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id, timestamp="2026-03-01T10:00:00",
        agents_used=agents, total_agents=len(agents),
        risk_level="LOW", sequencing_mode="phased_delivery",
        gates_passed=0, gates_failed=0, outcome="SHIP", notes="",
    )


def _setup(tmp_path: Path) -> tuple[UsageLogger, PerformanceScorer]:
    log_file = tmp_path / "usage.jsonl"
    logger = UsageLogger(log_file)
    retro_engine = RetrospectiveEngine(tmp_path / "retros")
    scorer = PerformanceScorer(logger, retro_engine)
    return logger, scorer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDetectTrends:
    def test_stable_when_insufficient_data(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        assert scorer.detect_trends("arch") == "stable"

    def test_improving_trend(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        # Interleave failures early, successes late within a 10-point window
        # Pattern: 0,0,0,0,0, 1,1,1,1,1 => clear upward slope
        for i in range(5):
            logger.log(_task(f"t{i}", [_agent("arch", retries=2)]))   # 0
        for i in range(5, 10):
            logger.log(_task(f"t{i}", [_agent("arch", retries=0)]))   # 1
        assert scorer.detect_trends("arch", window=10) == "improving"

    def test_degrading_trend(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        # Pattern: 1,1,1,1,1, 0,0,0,0,0 => clear downward slope
        for i in range(5):
            logger.log(_task(f"t{i}", [_agent("arch", retries=0)]))   # 1
        for i in range(5, 10):
            logger.log(_task(f"t{i}", [_agent("arch", retries=2)]))   # 0
        assert scorer.detect_trends("arch", window=10) == "degrading"

    def test_stable_when_consistent(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        # All successes — flat line
        for i in range(10):
            logger.log(_task(f"t{i}", [_agent("arch", retries=0)]))
        assert scorer.detect_trends("arch") == "stable"

    def test_window_parameter(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        # Old failures followed by recent successes
        for i in range(20):
            logger.log(_task(f"t{i}", [_agent("arch", retries=2)]))
        for i in range(20, 25):
            logger.log(_task(f"t{i}", [_agent("arch", retries=0)]))
        # With window=5, only the last 5 (all successes) should show stable
        assert scorer.detect_trends("arch", window=5) == "stable"

    def test_unknown_agent_returns_stable(self, tmp_path: Path):
        logger, scorer = _setup(tmp_path)
        logger.log(_task("t1", [_agent("arch")]))
        assert scorer.detect_trends("ghost") == "stable"
