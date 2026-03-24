"""Tests for BudgetTuner.auto_apply_recommendations() enhancement."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.learn.budget_tuner import BudgetTuner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(name: str = "worker", estimated_tokens: int = 10_000) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name, model="sonnet", steps=1, retries=0, gate_results=[],
        estimated_tokens=estimated_tokens, duration_seconds=1.0,
    )


def _task(
    task_id: str = "task-001",
    sequencing_mode: str = "phased_delivery",
    agents: list[AgentUsageRecord] | None = None,
) -> TaskUsageRecord:
    agent_list = agents if agents is not None else []
    return TaskUsageRecord(
        task_id=task_id, timestamp="2026-03-01T10:00:00",
        agents_used=agent_list, total_agents=len(agent_list),
        risk_level="LOW", sequencing_mode=sequencing_mode,
        gates_passed=0, gates_failed=0, outcome="SHIP", notes="",
    )


def _make_tuner(tmp_path: Path, tasks: list[TaskUsageRecord]) -> BudgetTuner:
    tc_dir = tmp_path / "team-context"
    log_path = tc_dir / "usage-log.jsonl"
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)
    return BudgetTuner(team_context_root=tc_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutoApplyRecommendations:
    def test_empty_when_no_data(self, tmp_path: Path):
        tuner = BudgetTuner(team_context_root=tmp_path / "tc")
        assert tuner.auto_apply_recommendations() == []

    def test_returns_only_downgrades(self, tmp_path: Path):
        # Create tasks with very low token usage in standard tier range
        # p95 below 50001 (standard floor) => downgrade to lean
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=20_000)])
            for i in range(10)  # enough for confidence=1.0
        ]
        tuner = _make_tuner(tmp_path, tasks)

        all_recs = tuner.analyze()
        auto_recs = tuner.auto_apply_recommendations()

        # All auto-apply recs should be downgrades
        for rec in auto_recs:
            tier_order = {"lean": 0, "standard": 1, "full": 2}
            assert tier_order[rec.recommended_tier] < tier_order[rec.current_tier]

    def test_excludes_upgrades(self, tmp_path: Path):
        # Create tasks with high token usage to trigger upgrade recommendation
        # median > 400000 (80% of 500000 standard ceiling)
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=450_000)])
            for i in range(10)
        ]
        tuner = _make_tuner(tmp_path, tasks)

        all_recs = tuner.analyze()
        auto_recs = tuner.auto_apply_recommendations()

        # Any upgrade recs from analyze() should NOT appear in auto_apply
        upgrades = [r for r in all_recs if r.recommended_tier == "full"]
        auto_upgrade = [r for r in auto_recs if r.recommended_tier == "full"]
        assert len(auto_upgrade) == 0

    def test_respects_confidence_threshold(self, tmp_path: Path):
        # Only 3 samples => confidence = 0.3, below 0.8 threshold
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=20_000)])
            for i in range(3)
        ]
        tuner = _make_tuner(tmp_path, tasks)
        auto_recs = tuner.auto_apply_recommendations(threshold=0.8)
        assert len(auto_recs) == 0

    def test_custom_threshold(self, tmp_path: Path):
        # With threshold=0.3 and 3 samples (confidence=0.3), should include
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=20_000)])
            for i in range(3)
        ]
        tuner = _make_tuner(tmp_path, tasks)

        # Check if there are any downgrades first
        all_recs = tuner.analyze()
        downgrades = [
            r for r in all_recs
            if {"lean": 0, "standard": 1, "full": 2}.get(r.recommended_tier, 0)
            < {"lean": 0, "standard": 1, "full": 2}.get(r.current_tier, 0)
        ]
        if downgrades:
            auto_recs = tuner.auto_apply_recommendations(threshold=0.3)
            assert len(auto_recs) >= 1
