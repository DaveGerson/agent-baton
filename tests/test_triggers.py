"""Tests for agent_baton.core.improve.triggers.TriggerEvaluator
and agent_baton.models.improvement.TriggerConfig.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# TriggerEvaluatorWithStorage — reading from StorageBackend
# ---------------------------------------------------------------------------

def _write_tasks(tc_dir: Path, count: int) -> None:
    """Write *count* task records to the JSONL usage log in *tc_dir*."""
    logger = UsageLogger(tc_dir / "usage-log.jsonl")
    for i in range(count):
        logger.log(_task(f"t{i}"))


class TestTriggerEvaluatorWithStorage:
    """Tests for TriggerEvaluator reading from StorageBackend."""

    def test_reads_from_storage_when_provided(self, tmp_path: Path):
        """_read_records returns storage data, not JSONL data."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        # JSONL has 0 records.
        # Storage returns 5 records — evaluator must use storage.
        storage_records = [_task(f"s{i}") for i in range(5)]
        storage = MagicMock()
        storage.read_usage.return_value = storage_records

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        ev = TriggerEvaluator(config=config, team_context_root=tc_dir, storage=storage)

        assert ev.should_analyze() is True
        storage.read_usage.assert_called()

    def test_falls_back_to_jsonl_when_storage_raises(self, tmp_path: Path):
        """When storage.read_usage() raises, fall back to JSONL."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        # JSONL has 5 records.
        _write_tasks(tc_dir, 5)

        storage = MagicMock()
        storage.read_usage.side_effect = Exception("db unavailable")

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        ev = TriggerEvaluator(config=config, team_context_root=tc_dir, storage=storage)

        # Falls back to JSONL — 5 records present so trigger fires.
        assert ev.should_analyze() is True

    def test_stale_watermark_auto_resets(self, tmp_path: Path):
        """Watermark > total records triggers auto-reset to 0."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        # Write a stale watermark of 100 into the state file.
        state_path = tc_dir / "improvement-trigger-state.json"
        state_path.write_text(
            json.dumps({"last_analyzed_count": 100, "last_analyzed_at": "2024-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )

        # Storage returns only 5 records (< watermark of 100).
        storage_records = [_task(f"s{i}") for i in range(5)]
        storage = MagicMock()
        storage.read_usage.return_value = storage_records

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        ev = TriggerEvaluator(config=config, team_context_root=tc_dir, storage=storage)

        # Watermark reset → 5 new tasks >= interval of 3 → should trigger.
        assert ev.should_analyze() is True

        # State file must have been reset to 0.
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["last_analyzed_count"] == 0


# ---------------------------------------------------------------------------
# TriggerEvaluatorIssueSignal — learning issue supplementary trigger
# ---------------------------------------------------------------------------

class TestTriggerEvaluatorIssueSignal:
    """Tests for learning-issue-based trigger enrichment."""

    def _evaluator_with_watermark(
        self,
        tc_dir: Path,
        task_count: int,
        watermark_count: int,
        last_analyzed_at: str,
        config: TriggerConfig,
        **kwargs,
    ) -> TriggerEvaluator:
        """Helper: write tasks to JSONL, persist a state file, return evaluator."""
        _write_tasks(tc_dir, task_count)
        state_path = tc_dir / "improvement-trigger-state.json"
        state_path.write_text(
            json.dumps({
                "last_analyzed_count": watermark_count,
                "last_analyzed_at": last_analyzed_at,
            }),
            encoding="utf-8",
        )
        return TriggerEvaluator(config=config, team_context_root=tc_dir, **kwargs)

    def test_new_issues_trigger_analysis(self, tmp_path: Path):
        """Open issues updated after watermark timestamp trigger analysis."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2024-01-01T00:00:00+00:00"

        # 5 tasks in JSONL, watermark count = 5 (0 new tasks, below interval).
        # One open issue with last_seen AFTER the watermark timestamp.
        issue = MagicMock()
        issue.last_seen = "2025-06-01T10:00:00+00:00"

        ledger = MagicMock()
        ledger.get_open_issues.return_value = [issue]

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, ledger=ledger,
        )

        assert ev.should_analyze() is True

    def test_old_issues_do_not_trigger(self, tmp_path: Path):
        """Issues with last_seen before watermark don't trigger."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2025-06-01T12:00:00+00:00"

        # Issue last_seen is BEFORE the watermark timestamp.
        issue = MagicMock()
        issue.last_seen = "2024-01-01T00:00:00+00:00"

        ledger = MagicMock()
        ledger.get_open_issues.return_value = [issue]

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, ledger=ledger,
        )

        assert ev.should_analyze() is False

    def test_ledger_error_is_non_fatal(self, tmp_path: Path):
        """Ledger error doesn't crash, returns False."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2024-01-01T00:00:00+00:00"

        ledger = MagicMock()
        ledger.get_open_issues.side_effect = RuntimeError("ledger offline")

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, ledger=ledger,
        )

        # Must not raise; no other signal crosses threshold → False.
        assert ev.should_analyze() is False


# ---------------------------------------------------------------------------
# TriggerEvaluatorBeadSignal — bead supplementary trigger
# ---------------------------------------------------------------------------

class TestTriggerEvaluatorBeadSignal:
    """Tests for bead-based trigger enrichment."""

    def _evaluator_with_watermark(
        self,
        tc_dir: Path,
        task_count: int,
        watermark_count: int,
        last_analyzed_at: str,
        config: TriggerConfig,
        **kwargs,
    ) -> TriggerEvaluator:
        """Helper: write tasks to JSONL, persist a state file, return evaluator."""
        _write_tasks(tc_dir, task_count)
        state_path = tc_dir / "improvement-trigger-state.json"
        state_path.write_text(
            json.dumps({
                "last_analyzed_count": watermark_count,
                "last_analyzed_at": last_analyzed_at,
            }),
            encoding="utf-8",
        )
        return TriggerEvaluator(config=config, team_context_root=tc_dir, **kwargs)

    def test_beads_trigger_analysis(self, tmp_path: Path):
        """3+ beads created after watermark trigger analysis."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2024-01-01T00:00:00+00:00"

        # 3 beads all created after the watermark timestamp.
        beads = []
        for _ in range(3):
            b = MagicMock()
            b.created_at = "2025-06-01T10:00:00+00:00"
            beads.append(b)

        bead_store = MagicMock()
        bead_store.query.return_value = beads

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, bead_store=bead_store,
        )

        assert ev.should_analyze() is True

    def test_beads_below_threshold_do_not_trigger(self, tmp_path: Path):
        """Fewer than 3 beads after watermark don't trigger."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2024-01-01T00:00:00+00:00"

        # Only 2 beads created after the watermark timestamp.
        beads = []
        for _ in range(2):
            b = MagicMock()
            b.created_at = "2025-06-01T10:00:00+00:00"
            beads.append(b)

        bead_store = MagicMock()
        bead_store.query.return_value = beads

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, bead_store=bead_store,
        )

        assert ev.should_analyze() is False

    def test_bead_store_error_is_non_fatal(self, tmp_path: Path):
        """bead_store.query() error doesn't crash, returns False."""
        tc_dir = tmp_path / "team-context"
        tc_dir.mkdir(parents=True)

        config = TriggerConfig(min_tasks_before_analysis=3, analysis_interval_tasks=3)
        watermark_ts = "2024-01-01T00:00:00+00:00"

        bead_store = MagicMock()
        bead_store.query.side_effect = OSError("bead store unavailable")

        ev = self._evaluator_with_watermark(
            tc_dir, task_count=5, watermark_count=5,
            last_analyzed_at=watermark_ts, config=config, bead_store=bead_store,
        )

        # Must not raise; no other signal crosses threshold → False.
        assert ev.should_analyze() is False
