"""Tests for agent_baton.models.usage and agent_baton.core.usage."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(
    name: str = "architect",
    model: str = "sonnet",
    steps: int = 1,
    retries: int = 0,
    gate_results: list[str] | None = None,
    estimated_tokens: int = 1000,
    duration_seconds: float = 2.5,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model=model,
        steps=steps,
        retries=retries,
        gate_results=gate_results if gate_results is not None else [],
        estimated_tokens=estimated_tokens,
        duration_seconds=duration_seconds,
    )


def _task(
    task_id: str = "task-001",
    timestamp: str = "2026-03-01T10:00:00",
    agents: list[AgentUsageRecord] | None = None,
    risk_level: str = "LOW",
    outcome: str = "SHIP",
    gates_passed: int = 2,
    gates_failed: int = 0,
) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agents if agents is not None else [],
        total_agents=len(agents) if agents else 0,
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome=outcome,
        notes="",
    )


# ---------------------------------------------------------------------------
# AgentUsageRecord — serialization
# ---------------------------------------------------------------------------

class TestAgentUsageRecordRoundtrip:
    def test_roundtrip_is_identity(self):
        original = _agent(name="router", model="haiku", steps=1, retries=2,
                          gate_results=["PASS", "PASS", "FAIL"],
                          estimated_tokens=750, duration_seconds=3.3)
        assert AgentUsageRecord.from_dict(original.to_dict()) == original

    def test_from_dict_uses_defaults_for_missing_keys(self):
        rec = AgentUsageRecord.from_dict({"name": "new-agent"})
        assert rec.model == "sonnet"
        assert rec.steps == 1
        assert rec.retries == 0
        assert rec.gate_results == []
        assert rec.estimated_tokens == 0
        assert rec.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# TaskUsageRecord — serialization
# ---------------------------------------------------------------------------

class TestTaskUsageRecordRoundtrip:
    def test_roundtrip_is_identity(self):
        task = _task(
            task_id="rt-1",
            timestamp="2026-01-01T00:00:00",
            agents=[_agent("x", retries=2, gate_results=["PASS", "FAIL"])],
            risk_level="MEDIUM",
            outcome="SHIP WITH NOTES",
            gates_passed=3,
            gates_failed=1,
        )
        assert TaskUsageRecord.from_dict(task.to_dict()) == task

    def test_from_dict_restores_nested_agents(self):
        task = _task(agents=[
            _agent("arch", retries=0, gate_results=["PASS"]),
            _agent("be", retries=1, gate_results=["FAIL"]),
        ])
        restored = TaskUsageRecord.from_dict(task.to_dict())
        assert len(restored.agents_used) == 2
        assert restored.agents_used[0].name == "arch"
        assert restored.agents_used[1].retries == 1

    def test_from_dict_defaults_for_missing_optional_keys(self):
        task = TaskUsageRecord.from_dict({
            "task_id": "min",
            "timestamp": "2026-01-01T00:00:00",
        })
        assert task.agents_used == []
        assert task.total_agents == 0
        assert task.risk_level == "LOW"
        assert task.sequencing_mode == "phased_delivery"
        assert task.outcome == ""


# ---------------------------------------------------------------------------
# UsageLogger.log and read_all
# ---------------------------------------------------------------------------

class TestUsageLoggerLog:
    def test_log_creates_file_including_parent_dirs(self, tmp_path: Path):
        log_file = tmp_path / "deep" / "nested" / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task())
        assert log_file.exists()

    def test_log_appends_one_line_per_record(self, tmp_path: Path):
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        for i in range(3):
            logger.log(_task(f"t{i}"))
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3

    def test_read_all_returns_empty_for_missing_or_empty_file(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "nonexistent.jsonl")
        assert logger.read_all() == []

        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("")
        assert UsageLogger(empty_file).read_all() == []

    def test_read_all_restores_written_records(self, tmp_path: Path):
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        t1 = _task("t1", agents=[_agent("arch")])
        t2 = _task("t2", agents=[_agent("be"), _agent("fe")])
        logger.log(t1)
        logger.log(t2)
        records = logger.read_all()
        assert len(records) == 2
        assert records[0].task_id == "t1"
        assert records[1].task_id == "t2"
        assert len(records[1].agents_used) == 2

    def test_read_all_skips_malformed_and_blank_lines(self, tmp_path: Path):
        log_file = tmp_path / "usage.jsonl"
        logger = UsageLogger(log_file)
        logger.log(_task("good"))
        with log_file.open("a") as f:
            f.write("NOT_JSON\n\n\n")
        logger.log(_task("also-good"))
        records = logger.read_all()
        assert len(records) == 2
        assert {r.task_id for r in records} == {"good", "also-good"}

    def test_read_all_skips_json_missing_required_key(self, tmp_path: Path):
        log_file = tmp_path / "usage.jsonl"
        log_file.write_text('{"timestamp":"2026-01-01T00:00:00"}\n', encoding="utf-8")
        assert UsageLogger(log_file).read_all() == []


# ---------------------------------------------------------------------------
# UsageLogger.read_recent
# ---------------------------------------------------------------------------

class TestUsageLoggerReadRecent:
    def test_returns_last_n_records(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        for i in range(5):
            logger.log(_task(f"t{i}"))
        recent = logger.read_recent(3)
        assert len(recent) == 3
        assert [r.task_id for r in recent] == ["t2", "t3", "t4"]

    def test_returns_all_when_count_exceeds_total(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        for i in range(3):
            logger.log(_task(f"t{i}"))
        assert len(logger.read_recent(10)) == 3

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        assert UsageLogger(tmp_path / "missing.jsonl").read_recent(5) == []


# ---------------------------------------------------------------------------
# UsageLogger.summary
# ---------------------------------------------------------------------------

class TestUsageLoggerSummary:
    def test_summary_zeros_for_empty_log(self, tmp_path: Path):
        s = UsageLogger(tmp_path / "u.jsonl").summary()
        assert s["total_tasks"] == 0
        assert s["total_agents_used"] == 0
        assert s["total_estimated_tokens"] == 0
        assert s["avg_agents_per_task"] == 0.0
        assert s["avg_retries_per_task"] == 0.0
        assert s["outcome_counts"] == {}
        assert s["risk_level_counts"] == {}
        assert s["agent_frequency"] == {}

    @pytest.mark.parametrize("setup,field,expected", [
        (
            lambda l: [l.log(_task(f"t{i}")) for i in range(4)],
            "total_tasks", 4,
        ),
        (
            lambda l: (
                l.log(_task("t1", agents=[_agent("a", estimated_tokens=1000),
                                          _agent("b", estimated_tokens=2000)])),
            ),
            "total_estimated_tokens", 3000,
        ),
        (
            lambda l: (
                l.log(_task("t1", agents=[_agent("a"), _agent("b")])),
                l.log(_task("t2", agents=[_agent("c")])),
            ),
            "avg_agents_per_task", 1.5,
        ),
        (
            lambda l: (
                l.log(_task("t1", agents=[_agent("a", retries=2), _agent("b", retries=0)])),
                l.log(_task("t2", agents=[_agent("c", retries=2)])),
            ),
            "avg_retries_per_task", 2.0,
        ),
    ])
    def test_summary_numeric_field(self, tmp_path: Path, setup, field, expected):
        logger = UsageLogger(tmp_path / "u.jsonl")
        setup(logger)
        assert logger.summary()[field] == expected

    def test_summary_agent_frequency(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch"), _agent("be")]))
        logger.log(_task("t2", agents=[_agent("arch")]))
        freq = logger.summary()["agent_frequency"]
        assert freq["arch"] == 2
        assert freq["be"] == 1

    def test_summary_outcome_counts(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", outcome="SHIP"))
        logger.log(_task("t2", outcome="SHIP"))
        logger.log(_task("t3", outcome="REVISE"))
        oc = logger.summary()["outcome_counts"]
        assert oc["SHIP"] == 2
        assert oc["REVISE"] == 1

    def test_summary_risk_level_counts(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", risk_level="LOW"))
        logger.log(_task("t2", risk_level="HIGH"))
        logger.log(_task("t3", risk_level="LOW"))
        rc = logger.summary()["risk_level_counts"]
        assert rc["LOW"] == 2
        assert rc["HIGH"] == 1

    def test_summary_ignores_empty_outcome(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", outcome=""))
        assert "" not in logger.summary()["outcome_counts"]


# ---------------------------------------------------------------------------
# UsageLogger.agent_stats
# ---------------------------------------------------------------------------

class TestUsageLoggerAgentStats:
    def test_unknown_agent_returns_zeros(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch")]))
        stats = logger.agent_stats("ghost")
        assert stats["times_used"] == 0
        assert stats["total_retries"] == 0
        assert stats["avg_retries"] == 0.0
        assert stats["gate_pass_rate"] is None
        assert stats["models_used"] == {}

    def test_times_used_counts_across_tasks(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch"), _agent("be")]))
        logger.log(_task("t2", agents=[_agent("arch")]))
        assert logger.agent_stats("arch")["times_used"] == 2

    def test_total_and_avg_retries(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch", retries=2)]))
        logger.log(_task("t2", agents=[_agent("arch", retries=0)]))
        stats = logger.agent_stats("arch")
        assert stats["total_retries"] == 2
        assert stats["avg_retries"] == 1.0

    def test_gate_pass_rate_computed_correctly(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[
            _agent("arch", gate_results=["PASS", "PASS", "FAIL"])
        ]))
        stats = logger.agent_stats("arch")
        assert stats["gate_pass_rate"] == pytest.approx(2 / 3)

    def test_gate_pass_rate_none_when_no_gates(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch", gate_results=[])]))
        assert logger.agent_stats("arch")["gate_pass_rate"] is None

    def test_models_used_aggregated(self, tmp_path: Path):
        logger = UsageLogger(tmp_path / "u.jsonl")
        logger.log(_task("t1", agents=[_agent("arch", model="sonnet")]))
        logger.log(_task("t2", agents=[_agent("arch", model="opus")]))
        logger.log(_task("t3", agents=[_agent("arch", model="sonnet")]))
        models = logger.agent_stats("arch")["models_used"]
        assert models["sonnet"] == 2
        assert models["opus"] == 1
