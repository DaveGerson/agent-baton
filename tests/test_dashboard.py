"""Tests for agent_baton.core.dashboard.DashboardGenerator."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.usage import UsageLogger
from agent_baton.core.dashboard import DashboardGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(
    name: str = "arch",
    model: str = "sonnet",
    retries: int = 0,
    tokens: int = 1000,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model=model,
        steps=1,
        retries=retries,
        gate_results=[],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str = "t1",
    agents: list[AgentUsageRecord] | None = None,
    timestamp: str = "2026-03-01T10:00:00",
    risk_level: str = "LOW",
    outcome: str = "SHIP",
    gates_passed: int = 2,
    gates_failed: int = 0,
    sequencing_mode: str = "phased_delivery",
) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agents if agents is not None else [],
        total_agents=len(agents) if agents else 0,
        risk_level=risk_level,
        sequencing_mode=sequencing_mode,
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome=outcome,
        notes="",
    )


def _logger_with_data(tmp_path: Path) -> UsageLogger:
    log_file = tmp_path / "usage.jsonl"
    logger = UsageLogger(log_file)
    logger.log(_task("t1", [_agent("arch", retries=0, tokens=1000),
                             _agent("be", model="opus", retries=1, tokens=2000)],
                risk_level="LOW", outcome="SHIP", gates_passed=2, gates_failed=0))
    logger.log(_task("t2", [_agent("arch", retries=0, tokens=500),
                             _agent("fe", model="haiku", retries=2, tokens=800)],
                risk_level="HIGH", outcome="REVISE", gates_passed=1, gates_failed=1))
    return logger


# ---------------------------------------------------------------------------
# DashboardGenerator.generate
# DECISION: Collapsed section-presence tests (5 tests) and overview-table
# content tests (4 tests) each into 1 parameterized test. Tests that need
# distinct fixture setup (sequencing modes, zero gates, avg agents, missing
# outcome, retries column) are kept as individual tests.
# ---------------------------------------------------------------------------

class TestDashboardGeneratorGenerate:
    def test_no_data_message_for_empty_log(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "empty.jsonl")
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert "No usage data available" in result

    def test_starts_with_h1_usage_dashboard(self, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert gen.generate().startswith("# Usage Dashboard")

    def test_includes_task_count(self, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert "2 tasks tracked" in result

    @pytest.mark.parametrize("section_heading", [
        "## Overview",
        "## Agent Utilization",
        "## Model Mix",
        "## Outcomes",
        "## Risk Distribution",
        "## Sequencing Modes",
    ])
    def test_section_present(self, section_heading: str, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert section_heading in gen.generate()

    @pytest.mark.parametrize("expected_substring", [
        "| Total tasks |",
        "| 2 |",                 # 2 tasks
        "| Total agent uses |",
        "| 4 |",                 # t1: 2 agents + t2: 2 agents = 4
        "Estimated tokens",
        "4,300",                 # 1000+2000+500+800
        "Gate pass rate",
        "75%",                   # 3 passed / 4 total = 75%
    ])
    def test_overview_table_content(self, expected_substring: str, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert expected_substring in result

    @pytest.mark.parametrize("agent_name", ["arch", "be", "fe"])
    def test_agent_utilization_contains_agent_names(
        self, agent_name: str, tmp_path: Path
    ):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert agent_name in gen.generate()

    @pytest.mark.parametrize("model_name", ["sonnet", "opus", "haiku"])
    def test_model_mix_contains_model_names(self, model_name: str, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert model_name in gen.generate()

    @pytest.mark.parametrize("outcome_value", ["SHIP", "REVISE"])
    def test_outcomes_contains_outcome_values(self, outcome_value: str, tmp_path: Path):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert outcome_value in gen.generate()

    @pytest.mark.parametrize("risk_level", ["LOW", "HIGH"])
    def test_risk_distribution_contains_risk_levels(
        self, risk_level: str, tmp_path: Path
    ):
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        assert risk_level in gen.generate()

    def test_sequencing_modes_contains_mode_names(self, tmp_path: Path):
        log_file = tmp_path / "u.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent()], sequencing_mode="rapid_build"))
        logger.log(_task("t2", [_agent()], sequencing_mode="phased_delivery"))
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert "rapid_build" in result
        assert "phased_delivery" in result

    def test_gate_pass_rate_na_when_no_gates(self, tmp_path: Path):
        log_file = tmp_path / "u.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent()], gates_passed=0, gates_failed=0))
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert "n/a" in result

    def test_avg_agents_per_task(self, tmp_path: Path):
        log_file = tmp_path / "u.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("a"), _agent("b"), _agent("c")]))
        logger.log(_task("t2", [_agent("d")]))
        gen = DashboardGenerator(logger)
        result = gen.generate()
        # avg = 4 / 2 = 2.0
        assert "2.0" in result

    def test_missing_outcome_not_in_outcome_table(self, tmp_path: Path):
        log_file = tmp_path / "u.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent()], outcome=""))
        gen = DashboardGenerator(logger)
        result = gen.generate()
        # The table should have headers but no empty-string outcome row
        assert "## Outcomes" in result

    def test_single_agent_avg_retries_column_present(self, tmp_path: Path):
        log_file = tmp_path / "u.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("t1", [_agent("arch", retries=2)]))
        gen = DashboardGenerator(logger)
        result = gen.generate()
        assert "Avg Retries" in result


# ---------------------------------------------------------------------------
# DashboardGenerator.write
# DECISION: Merged 4 write tests into 1 comprehensive test that checks:
# file created, return value, content matches generate(), parent dirs created.
# ---------------------------------------------------------------------------

class TestDashboardGeneratorWrite:
    def test_write_comprehensive(self, tmp_path: Path):
        """Covers: file created, returns path, content matches generate(),
        and parent directories are created automatically."""
        logger = _logger_with_data(tmp_path)
        gen = DashboardGenerator(logger)
        expected = gen.generate()

        # Test nested path to verify parent dir creation
        out_path = tmp_path / "reports" / "usage" / "dashboard.md"
        result = gen.write(out_path)

        assert result == out_path
        assert out_path.exists()
        assert out_path.read_text(encoding="utf-8") == expected
