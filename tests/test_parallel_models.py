"""Unit tests for agent_baton.models.parallel — ExecutionRecord and ResourceLimits."""
from __future__ import annotations

import pytest

from agent_baton.models.parallel import ExecutionRecord, ResourceLimits


# ---------------------------------------------------------------------------
# ExecutionRecord
# ---------------------------------------------------------------------------

class TestExecutionRecord:
    def _sample(self) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id="2026-01-15-build-feature",
            project_path="/home/user/project",
            status="running",
            plan_summary="Build a new authentication feature",
            worker_pid=12345,
            started_at="2026-01-15T10:00:00",
            updated_at="2026-01-15T10:30:00",
            risk_level="MEDIUM",
            budget_tier="standard",
            steps_total=5,
            steps_complete=2,
            git_branch="feature/auth",
            tokens_estimated=50000,
        )

    def test_roundtrip_is_identity(self) -> None:
        rec = self._sample()
        restored = ExecutionRecord.from_dict(rec.to_dict())
        assert restored.execution_id == rec.execution_id
        assert restored.project_path == rec.project_path
        assert restored.status == rec.status
        assert restored.plan_summary == rec.plan_summary
        assert restored.worker_pid == rec.worker_pid
        assert restored.started_at == rec.started_at
        assert restored.updated_at == rec.updated_at
        assert restored.risk_level == rec.risk_level
        assert restored.budget_tier == rec.budget_tier
        assert restored.steps_total == rec.steps_total
        assert restored.steps_complete == rec.steps_complete
        assert restored.git_branch == rec.git_branch
        assert restored.tokens_estimated == rec.tokens_estimated

    def test_to_dict_contains_all_keys(self) -> None:
        rec = self._sample()
        d = rec.to_dict()
        expected_keys = {
            "execution_id", "project_path", "status", "plan_summary",
            "worker_pid", "started_at", "updated_at", "risk_level",
            "budget_tier", "steps_total", "steps_complete", "git_branch",
            "tokens_estimated",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict_handles_missing_optional_fields(self) -> None:
        data = {"execution_id": "task-001"}
        rec = ExecutionRecord.from_dict(data)
        assert rec.execution_id == "task-001"
        assert rec.project_path == ""
        assert rec.status == "running"
        assert rec.plan_summary == ""
        assert rec.worker_pid == 0
        assert rec.started_at == ""
        assert rec.updated_at == ""
        assert rec.risk_level == "LOW"
        assert rec.budget_tier == "lean"
        assert rec.steps_total == 0
        assert rec.steps_complete == 0
        assert rec.git_branch == ""
        assert rec.tokens_estimated == 0

    def test_default_values_are_correct(self) -> None:
        rec = ExecutionRecord(execution_id="my-task")
        assert rec.status == "running"
        assert rec.risk_level == "LOW"
        assert rec.budget_tier == "lean"
        assert rec.worker_pid == 0
        assert rec.steps_total == 0
        assert rec.steps_complete == 0
        assert rec.tokens_estimated == 0

    def test_from_dict_coerces_numeric_strings(self) -> None:
        """from_dict must coerce string values to int for numeric fields."""
        data = {
            "execution_id": "t",
            "worker_pid": "999",
            "steps_total": "10",
            "steps_complete": "3",
            "tokens_estimated": "12345",
        }
        rec = ExecutionRecord.from_dict(data)
        assert rec.worker_pid == 999
        assert rec.steps_total == 10
        assert rec.steps_complete == 3
        assert rec.tokens_estimated == 12345

    @pytest.mark.parametrize("status", ["running", "complete", "failed", "gate_pending", "approval_pending"])
    def test_status_roundtrips_for_all_known_values(self, status: str) -> None:
        rec = ExecutionRecord(execution_id="t", status=status)
        assert ExecutionRecord.from_dict(rec.to_dict()).status == status


# ---------------------------------------------------------------------------
# ResourceLimits
# ---------------------------------------------------------------------------

class TestResourceLimits:
    def _sample(self) -> ResourceLimits:
        return ResourceLimits(
            max_concurrent_executions=5,
            max_concurrent_agents=12,
            max_tokens_per_minute=10000,
            max_concurrent_per_project=3,
        )

    def test_roundtrip_is_identity(self) -> None:
        limits = self._sample()
        restored = ResourceLimits.from_dict(limits.to_dict())
        assert restored.max_concurrent_executions == limits.max_concurrent_executions
        assert restored.max_concurrent_agents == limits.max_concurrent_agents
        assert restored.max_tokens_per_minute == limits.max_tokens_per_minute
        assert restored.max_concurrent_per_project == limits.max_concurrent_per_project

    def test_to_dict_contains_all_keys(self) -> None:
        limits = self._sample()
        d = limits.to_dict()
        expected_keys = {
            "max_concurrent_executions",
            "max_concurrent_agents",
            "max_tokens_per_minute",
            "max_concurrent_per_project",
        }
        assert set(d.keys()) == expected_keys

    def test_default_values_are_correct(self) -> None:
        limits = ResourceLimits()
        assert limits.max_concurrent_executions == 3
        assert limits.max_concurrent_agents == 8
        assert limits.max_tokens_per_minute == 0
        assert limits.max_concurrent_per_project == 2

    def test_from_dict_uses_defaults_for_missing_fields(self) -> None:
        limits = ResourceLimits.from_dict({})
        assert limits.max_concurrent_executions == 3
        assert limits.max_concurrent_agents == 8
        assert limits.max_tokens_per_minute == 0
        assert limits.max_concurrent_per_project == 2

    def test_from_dict_coerces_string_values(self) -> None:
        """from_dict must coerce string values to int."""
        data = {
            "max_concurrent_executions": "5",
            "max_concurrent_agents": "12",
            "max_tokens_per_minute": "10000",
            "max_concurrent_per_project": "3",
        }
        limits = ResourceLimits.from_dict(data)
        assert limits.max_concurrent_executions == 5
        assert limits.max_concurrent_agents == 12
        assert limits.max_tokens_per_minute == 10000
        assert limits.max_concurrent_per_project == 3

    def test_zero_tokens_per_minute_means_unlimited(self) -> None:
        """max_tokens_per_minute=0 is the documented 'unlimited' sentinel."""
        limits = ResourceLimits(max_tokens_per_minute=0)
        assert limits.to_dict()["max_tokens_per_minute"] == 0
        restored = ResourceLimits.from_dict(limits.to_dict())
        assert restored.max_tokens_per_minute == 0
