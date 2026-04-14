"""Tests for agent_baton.core.improve.triggers.TriggerEvaluator
and agent_baton.models.improvement.TriggerConfig.
"""
from __future__ import annotations

import json
import os
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


def _setup(
    tmp_path: Path,
    tasks: list[TaskUsageRecord],
    config: TriggerConfig | None = None,
) -> TriggerEvaluator:
    tc_dir = tmp_path / "team-context"
    log_path = tc_dir / "usage-log.jsonl"
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)
    return TriggerEvaluator(config=config, team_context_root=tc_dir)


# ---------------------------------------------------------------------------
# TriggerConfig defaults
# ---------------------------------------------------------------------------

class TestTriggerConfigDefaults:
    def test_default_min_tasks_is_3(self):
        cfg = TriggerConfig()
        assert cfg.min_tasks_before_analysis == 3

    def test_default_interval_is_3(self):
        cfg = TriggerConfig()
        assert cfg.analysis_interval_tasks == 3

    def test_from_dict_uses_new_defaults(self):
        cfg = TriggerConfig.from_dict({})
        assert cfg.min_tasks_before_analysis == 3
        assert cfg.analysis_interval_tasks == 3

    def test_from_dict_respects_explicit_values(self):
        cfg = TriggerConfig.from_dict(
            {"min_tasks_before_analysis": 7, "analysis_interval_tasks": 2}
        )
        assert cfg.min_tasks_before_analysis == 7
        assert cfg.analysis_interval_tasks == 2

    def test_round_trip(self):
        cfg = TriggerConfig(min_tasks_before_analysis=5, analysis_interval_tasks=4)
        restored = TriggerConfig.from_dict(cfg.to_dict())
        assert restored.min_tasks_before_analysis == 5
        assert restored.analysis_interval_tasks == 4


# ---------------------------------------------------------------------------
# TriggerConfig.from_env
# ---------------------------------------------------------------------------

class TestTriggerConfigFromEnv:
    def test_defaults_when_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("BATON_MIN_TASKS", raising=False)
        monkeypatch.delenv("BATON_ANALYSIS_INTERVAL", raising=False)
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 3
        assert cfg.analysis_interval_tasks == 3

    def test_reads_min_tasks_from_env(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "7")
        monkeypatch.delenv("BATON_ANALYSIS_INTERVAL", raising=False)
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 7
        assert cfg.analysis_interval_tasks == 3  # default unchanged

    def test_reads_interval_from_env(self, monkeypatch):
        monkeypatch.delenv("BATON_MIN_TASKS", raising=False)
        monkeypatch.setenv("BATON_ANALYSIS_INTERVAL", "10")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 3  # default unchanged
        assert cfg.analysis_interval_tasks == 10

    def test_reads_both_env_vars(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "5")
        monkeypatch.setenv("BATON_ANALYSIS_INTERVAL", "2")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 5
        assert cfg.analysis_interval_tasks == 2

    def test_ignores_non_numeric_env_var(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "not-a-number")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 3

    def test_ignores_zero_env_var(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "0")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 3

    def test_ignores_negative_env_var(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "-5")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 3

    def test_strips_whitespace_from_env_var(self, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "  8  ")
        cfg = TriggerConfig.from_env()
        assert cfg.min_tasks_before_analysis == 8


# ---------------------------------------------------------------------------
# TriggerEvaluator config resolution — overrides file
# ---------------------------------------------------------------------------

class TestTriggerEvaluatorConfigResolution:
    def test_uses_explicit_config_over_everything(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "99")
        tc_dir = tmp_path / "team-context"
        explicit = TriggerConfig(min_tasks_before_analysis=1, analysis_interval_tasks=1)
        ev = TriggerEvaluator(config=explicit, team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 1

    def test_reads_trigger_config_from_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BATON_MIN_TASKS", raising=False)
        monkeypatch.delenv("BATON_ANALYSIS_INTERVAL", raising=False)
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)
        overrides = {
            "trigger_config": {
                "min_tasks_before_analysis": 2,
                "analysis_interval_tasks": 2,
            }
        }
        (tc_dir / "learned-overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )
        ev = TriggerEvaluator(team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 2
        assert ev._config.analysis_interval_tasks == 2

    def test_overrides_file_partial_keys_leave_env_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "6")
        monkeypatch.delenv("BATON_ANALYSIS_INTERVAL", raising=False)
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)
        # Only override the interval — min_tasks should stay at env value (6)
        overrides = {"trigger_config": {"analysis_interval_tasks": 4}}
        (tc_dir / "learned-overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )
        ev = TriggerEvaluator(team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 6
        assert ev._config.analysis_interval_tasks == 4

    def test_missing_overrides_file_uses_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BATON_MIN_TASKS", "5")
        tc_dir = tmp_path / "team-context"
        ev = TriggerEvaluator(team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 5

    def test_malformed_overrides_file_falls_back_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BATON_MIN_TASKS", raising=False)
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)
        (tc_dir / "learned-overrides.json").write_text("not valid json", encoding="utf-8")
        ev = TriggerEvaluator(team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 3

    def test_overrides_file_without_trigger_config_key_ignored(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BATON_MIN_TASKS", raising=False)
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)
        # File exists but has no trigger_config block
        (tc_dir / "learned-overrides.json").write_text(
            json.dumps({"flavor_map": {}}), encoding="utf-8"
        )
        ev = TriggerEvaluator(team_context_root=tc_dir)
        assert ev._config.min_tasks_before_analysis == 3


# ---------------------------------------------------------------------------
# reset_watermark
# ---------------------------------------------------------------------------

class TestResetWatermark:
    def test_reset_forces_reanalysis(self, tmp_path):
        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        tasks = [_task(f"t{i}") for i in range(3)]
        evaluator = _setup(tmp_path, tasks, config)
        evaluator.mark_analyzed()
        assert evaluator.should_analyze() is False

        evaluator.reset_watermark()
        assert evaluator.should_analyze() is True

    def test_reset_writes_zero_to_state_file(self, tmp_path):
        tc_dir = tmp_path / "team-context"
        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        tasks = [_task(f"t{i}") for i in range(5)]
        evaluator = _setup(tmp_path, tasks, config)
        evaluator.mark_analyzed()

        evaluator.reset_watermark()

        state = json.loads((tc_dir / "improvement-trigger-state.json").read_text())
        assert state["last_analyzed_count"] == 0

    def test_reset_without_prior_state_file_is_safe(self, tmp_path):
        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        evaluator.reset_watermark()  # must not raise
        state = json.loads((tc_dir / "improvement-trigger-state.json").read_text())
        assert state["last_analyzed_count"] == 0


# ---------------------------------------------------------------------------
# should_analyze — core threshold logic (now with default=3)
# ---------------------------------------------------------------------------

class TestShouldAnalyze:
    def test_false_when_no_data(self, tmp_path: Path):
        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        assert evaluator.should_analyze() is False

    def test_false_when_below_minimum_tasks(self, tmp_path: Path):
        # 2 tasks, default min=3
        tasks = [_task(f"t{i}") for i in range(2)]
        evaluator = _setup(tmp_path, tasks)
        assert evaluator.should_analyze() is False

    def test_true_at_exactly_minimum_tasks(self, tmp_path: Path):
        # 3 tasks with default min=3 and interval=3 — watermark=0, so new=3 >= interval
        tasks = [_task(f"t{i}") for i in range(3)]
        evaluator = _setup(tmp_path, tasks)
        assert evaluator.should_analyze() is True

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

    def test_false_when_interval_not_yet_reached_after_mark(self, tmp_path: Path):
        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=5)
        tasks = [_task(f"t{i}") for i in range(3)]
        evaluator = _setup(tmp_path, tasks, config)
        evaluator.mark_analyzed()

        # Add only 2 new tasks — below interval of 5
        tc_dir = tmp_path / "team-context"
        logger = UsageLogger(tc_dir / "usage-log.jsonl")
        for i in range(3, 5):
            logger.log(_task(f"t{i}"))

        assert evaluator.should_analyze() is False

    def test_env_var_min_tasks_respected(self, tmp_path: Path, monkeypatch):
        # Set both: min_tasks=2, interval=2 so that 2 tasks satisfies both conditions.
        monkeypatch.setenv("BATON_MIN_TASKS", "2")
        monkeypatch.setenv("BATON_ANALYSIS_INTERVAL", "2")
        tasks = [_task(f"t{i}") for i in range(2)]
        tc_dir = tmp_path / "team-context"
        log_path = tc_dir / "usage-log.jsonl"
        logger = UsageLogger(log_path)
        for t in tasks:
            logger.log(t)
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
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


# ---------------------------------------------------------------------------
# ImprovementLoop integration — force flag bypasses threshold
# ---------------------------------------------------------------------------

class TestImprovementLoopForce:
    def test_force_bypasses_threshold(self, tmp_path: Path):
        """ImprovementLoop.run_cycle(force=True) must not return a SKIPPED report
        due to insufficient data, even with zero tasks in the log."""
        from unittest.mock import MagicMock, patch
        from agent_baton.core.improve.loop import ImprovementLoop
        from agent_baton.core.improve.triggers import TriggerEvaluator

        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        # No data — should_analyze() will be False
        assert evaluator.should_analyze() is False

        loop = ImprovementLoop(
            trigger_evaluator=evaluator,
            improvements_dir=tmp_path / "improvements",
        )

        with patch.object(loop._recommender, "analyze", return_value=[]), \
             patch.object(loop._rollbacks, "circuit_breaker_tripped", return_value=False), \
             patch.object(loop, "_spawn_maintainer"), \
             patch.object(loop, "_apply_central_signals"):
            report = loop.run_cycle(force=True)

        assert report.skipped is False

    def test_run_without_force_skips_when_no_data(self, tmp_path: Path):
        """Without --force and no data, the cycle must be skipped."""
        from agent_baton.core.improve.loop import ImprovementLoop
        from agent_baton.core.improve.triggers import TriggerEvaluator
        from unittest.mock import patch

        tc_dir = tmp_path / "team-context"
        evaluator = TriggerEvaluator(team_context_root=tc_dir)
        loop = ImprovementLoop(
            trigger_evaluator=evaluator,
            improvements_dir=tmp_path / "improvements",
        )

        with patch.object(loop._rollbacks, "circuit_breaker_tripped", return_value=False):
            report = loop.run_cycle(force=False)

        assert report.skipped is True
        assert "Not enough new data" in report.reason
