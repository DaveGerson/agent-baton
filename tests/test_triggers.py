"""Tests for agent_baton.core.improve.triggers.TriggerEvaluator."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.models.improvement import TriggerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(
    name: str = "worker",
    retries: int = 0,
    tokens: int = 10_000,
    gate_results: list[str] | None = None,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=retries,
        gate_results=gate_results or [],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str = "task-001",
    agents: list[AgentUsageRecord] | None = None,
    risk_level: str = "LOW",
    gates_passed: int = 0,
    gates_failed: int = 0,
) -> TaskUsageRecord:
    agent_list = agents if agents is not None else [_agent()]
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-03-01T10:00:00",
        agents_used=agent_list,
        total_agents=len(agent_list),
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome="SHIP",
        notes="",
    )


def _setup(tmp_path: Path, tasks: list[TaskUsageRecord], config: TriggerConfig | None = None) -> TriggerEvaluator:
    tc_dir = tmp_path / "team-context"
    log_path = tc_dir / "usage-log.jsonl"
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)
    return TriggerEvaluator(config=config, team_context_root=tc_dir)


# ---------------------------------------------------------------------------
# should_analyze
# ---------------------------------------------------------------------------

class TestShouldAnalyze:
    def test_false_when_no_data(self, tmp_path: Path):
        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        assert evaluator.should_analyze() is False

    def test_false_when_below_minimum_tasks(self, tmp_path: Path):
        tasks = [_task(f"t{i}") for i in range(5)]  # below default 10
        evaluator = _setup(tmp_path, tasks)
        assert evaluator.should_analyze() is False

    def test_true_when_enough_new_tasks(self, tmp_path: Path):
        config = TriggerConfig(min_tasks_before_analysis=5, analysis_interval_tasks=3)
        tasks = [_task(f"t{i}") for i in range(5)]
        evaluator = _setup(tmp_path, tasks, config)
        assert evaluator.should_analyze() is True

    def test_false_after_mark_analyzed(self, tmp_path: Path):
        config = TriggerConfig(min_tasks_before_analysis=5, analysis_interval_tasks=3)
        tasks = [_task(f"t{i}") for i in range(5)]
        evaluator = _setup(tmp_path, tasks, config)
        evaluator.mark_analyzed()
        assert evaluator.should_analyze() is False

    def test_true_again_after_new_tasks(self, tmp_path: Path):
        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=2)
        tasks = [_task(f"t{i}") for i in range(3)]
        evaluator = _setup(tmp_path, tasks, config)
        evaluator.mark_analyzed()

        # Add more tasks
        tc_dir = tmp_path / "team-context"
        logger = UsageLogger(tc_dir / "usage-log.jsonl")
        for i in range(3, 5):
            logger.log(_task(f"t{i}"))

        assert evaluator.should_analyze() is True


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------

class TestDetectAnomalies:
    def test_no_anomalies_when_healthy(self, tmp_path: Path):
        tasks = [_task(f"t{i}", agents=[_agent(retries=0)]) for i in range(5)]
        evaluator = _setup(tmp_path, tasks)
        anomalies = evaluator.detect_anomalies()
        # Filter out budget overrun since token amounts are small
        non_budget = [a for a in anomalies if a.anomaly_type != "budget_overrun"]
        assert len(non_budget) == 0

    def test_detects_high_failure_rate(self, tmp_path: Path):
        # 4 out of 5 tasks with retries = 80% failure rate (>30% threshold)
        tasks = [
            _task(f"t{i}", agents=[_agent(name="flaky", retries=2)])
            for i in range(4)
        ] + [_task("t4", agents=[_agent(name="flaky", retries=0)])]
        evaluator = _setup(tmp_path, tasks)
        anomalies = evaluator.detect_anomalies()
        failure_anomalies = [a for a in anomalies if a.anomaly_type == "high_failure_rate"]
        assert len(failure_anomalies) == 1
        assert failure_anomalies[0].agent_name == "flaky"

    def test_detects_retry_spike(self, tmp_path: Path):
        # avg retries > 2.0 across 3 tasks
        tasks = [
            _task(f"t{i}", agents=[_agent(name="slow", retries=3)])
            for i in range(3)
        ]
        evaluator = _setup(tmp_path, tasks)
        anomalies = evaluator.detect_anomalies()
        retry_anomalies = [a for a in anomalies if a.anomaly_type == "retry_spike"]
        assert len(retry_anomalies) == 1
        assert retry_anomalies[0].agent_name == "slow"

    def test_detects_gate_failure_rate(self, tmp_path: Path):
        # 3 gate failures out of 4 total = 75% (>20% threshold)
        tasks = [
            _task(f"t{i}", gates_passed=0, gates_failed=1) for i in range(3)
        ] + [_task("t3", gates_passed=1, gates_failed=0)]
        evaluator = _setup(tmp_path, tasks)
        anomalies = evaluator.detect_anomalies()
        gate_anomalies = [a for a in anomalies if a.anomaly_type == "high_gate_failure_rate"]
        assert len(gate_anomalies) == 1

    def test_empty_when_no_data(self, tmp_path: Path):
        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        assert evaluator.detect_anomalies() == []

    def test_skips_agents_below_minimum_sample(self, tmp_path: Path):
        # Only 2 uses — below minimum of 3
        tasks = [
            _task(f"t{i}", agents=[_agent(name="rare", retries=2)])
            for i in range(2)
        ]
        evaluator = _setup(tmp_path, tasks)
        anomalies = evaluator.detect_anomalies()
        failure_anomalies = [a for a in anomalies if a.anomaly_type == "high_failure_rate"]
        assert len(failure_anomalies) == 0
