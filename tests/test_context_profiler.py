"""Tests for AgentContextProfile, TaskContextProfile, and ContextProfiler."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.context_profile import AgentContextProfile, TaskContextProfile
from agent_baton.models.trace import TaskTrace, TraceEvent
from agent_baton.core.observe.context_profiler import ContextProfiler
from agent_baton.core.observe.trace import TraceRecorder


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _make_trace(
    task_id: str = "test-task",
    events: list[TraceEvent] | None = None,
) -> TaskTrace:
    return TaskTrace(
        task_id=task_id,
        plan_snapshot={},
        events=events or [],
        started_at="2026-03-20T14:30:00+00:00",
        completed_at="2026-03-20T14:35:00+00:00",
        outcome="SHIP",
    )


def _file_read_event(
    agent: str,
    path: str,
    phase: int = 1,
    step: int = 1,
) -> TraceEvent:
    return TraceEvent(
        timestamp="2026-03-20T14:30:01+00:00",
        event_type="file_read",
        agent_name=agent,
        phase=phase,
        step=step,
        details={"path": path},
    )


def _file_write_event(
    agent: str,
    path: str,
    phase: int = 1,
    step: int = 2,
) -> TraceEvent:
    return TraceEvent(
        timestamp="2026-03-20T14:30:05+00:00",
        event_type="file_write",
        agent_name=agent,
        phase=phase,
        step=step,
        details={"path": path},
    )


def _non_file_event(agent: str | None = "architect") -> TraceEvent:
    return TraceEvent(
        timestamp="2026-03-20T14:30:00+00:00",
        event_type="agent_start",
        agent_name=agent,
        phase=1,
        step=0,
        details={},
    )


def _write_trace_to_disk(
    tmp_path: Path,
    task_id: str,
    events: list[TraceEvent],
) -> None:
    """Create a trace on disk via TraceRecorder using tmp_path as context root."""
    recorder = TraceRecorder(tmp_path)
    trace = recorder.start_trace(task_id)
    for event in events:
        trace.events.append(event)
    recorder.complete_trace(trace)


# ---------------------------------------------------------------------------
# AgentContextProfile — model fields and serialisation
# ---------------------------------------------------------------------------

class TestAgentContextProfileModel:
    def test_required_field_stored(self) -> None:
        ap = AgentContextProfile(agent_name="backend-engineer")
        assert ap.agent_name == "backend-engineer"

    def test_optional_fields_default(self) -> None:
        ap = AgentContextProfile(agent_name="architect")
        assert ap.files_read == []
        assert ap.files_written == []
        assert ap.files_referenced == []
        assert ap.context_tokens_estimate == 0
        assert ap.output_tokens_estimate == 0
        assert ap.efficiency_score == 0.0

    def test_to_dict_contains_all_keys(self) -> None:
        ap = AgentContextProfile(agent_name="backend-engineer")
        d = ap.to_dict()
        for key in (
            "agent_name", "files_read", "files_written", "files_referenced",
            "context_tokens_estimate", "output_tokens_estimate", "efficiency_score",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_values_match(self) -> None:
        ap = AgentContextProfile(
            agent_name="test-agent",
            files_read=["a.py", "b.py"],
            files_written=["c.py"],
            files_referenced=["a.py"],
            context_tokens_estimate=500,
            output_tokens_estimate=200,
            efficiency_score=0.5,
        )
        d = ap.to_dict()
        assert d["agent_name"] == "test-agent"
        assert d["files_read"] == ["a.py", "b.py"]
        assert d["files_written"] == ["c.py"]
        assert d["files_referenced"] == ["a.py"]
        assert d["context_tokens_estimate"] == 500
        assert d["output_tokens_estimate"] == 200
        assert d["efficiency_score"] == 0.5

    def test_from_dict_roundtrip(self) -> None:
        ap = AgentContextProfile(
            agent_name="arch",
            files_read=["x.py"],
            files_written=["y.py"],
            efficiency_score=0.75,
        )
        restored = AgentContextProfile.from_dict(ap.to_dict())
        assert restored.agent_name == ap.agent_name
        assert restored.files_read == ap.files_read
        assert restored.files_written == ap.files_written
        assert restored.efficiency_score == ap.efficiency_score

    def test_from_dict_handles_missing_optional_keys(self) -> None:
        ap = AgentContextProfile.from_dict({"agent_name": "x"})
        assert ap.files_read == []
        assert ap.efficiency_score == 0.0

    def test_from_dict_handles_null_list_fields(self) -> None:
        ap = AgentContextProfile.from_dict(
            {"agent_name": "x", "files_read": None, "files_written": None}
        )
        assert ap.files_read == []
        assert ap.files_written == []

    def test_to_dict_lists_are_copies(self) -> None:
        """Mutating the returned dict should not affect the dataclass."""
        ap = AgentContextProfile(agent_name="a", files_read=["f.py"])
        d = ap.to_dict()
        d["files_read"].append("extra.py")
        assert len(ap.files_read) == 1


# ---------------------------------------------------------------------------
# TaskContextProfile — model fields and serialisation
# ---------------------------------------------------------------------------

class TestTaskContextProfileModel:
    def test_required_field_stored(self) -> None:
        tp = TaskContextProfile(task_id="my-task")
        assert tp.task_id == "my-task"

    def test_optional_fields_default(self) -> None:
        tp = TaskContextProfile(task_id="t")
        assert tp.agent_profiles == []
        assert tp.total_files_read == 0
        assert tp.unique_files_read == 0
        assert tp.redundant_reads == 0
        assert tp.redundancy_rate == 0.0
        assert tp.created_at == ""

    def test_to_dict_contains_all_keys(self) -> None:
        tp = TaskContextProfile(task_id="t")
        d = tp.to_dict()
        for key in (
            "task_id", "agent_profiles", "total_files_read",
            "unique_files_read", "redundant_reads", "redundancy_rate", "created_at",
        ):
            assert key in d, f"Missing key: {key}"

    def test_agent_profiles_serialised_as_list_of_dicts(self) -> None:
        tp = TaskContextProfile(
            task_id="t",
            agent_profiles=[
                AgentContextProfile(agent_name="arch"),
                AgentContextProfile(agent_name="backend-engineer"),
            ],
        )
        d = tp.to_dict()
        assert isinstance(d["agent_profiles"], list)
        assert len(d["agent_profiles"]) == 2
        assert d["agent_profiles"][0]["agent_name"] == "arch"

    def test_from_dict_roundtrip(self) -> None:
        tp = TaskContextProfile(
            task_id="roundtrip",
            agent_profiles=[AgentContextProfile(agent_name="arch", efficiency_score=0.5)],
            total_files_read=10,
            unique_files_read=8,
            redundant_reads=2,
            redundancy_rate=0.2,
            created_at="2026-03-20T14:00:00+00:00",
        )
        restored = TaskContextProfile.from_dict(tp.to_dict())
        assert restored.task_id == tp.task_id
        assert restored.total_files_read == tp.total_files_read
        assert restored.unique_files_read == tp.unique_files_read
        assert restored.redundant_reads == tp.redundant_reads
        assert restored.redundancy_rate == tp.redundancy_rate
        assert restored.created_at == tp.created_at
        assert len(restored.agent_profiles) == 1
        assert restored.agent_profiles[0].agent_name == "arch"

    def test_from_dict_handles_missing_keys(self) -> None:
        tp = TaskContextProfile.from_dict({"task_id": "minimal"})
        assert tp.agent_profiles == []
        assert tp.total_files_read == 0

    def test_from_dict_handles_null_agent_profiles(self) -> None:
        tp = TaskContextProfile.from_dict({"task_id": "t", "agent_profiles": None})
        assert tp.agent_profiles == []

    def test_json_serialisable(self) -> None:
        tp = TaskContextProfile(
            task_id="json-test",
            agent_profiles=[AgentContextProfile(agent_name="arch")],
        )
        json_str = json.dumps(tp.to_dict())
        restored = TaskContextProfile.from_dict(json.loads(json_str))
        assert restored.task_id == "json-test"


# ---------------------------------------------------------------------------
# ContextProfiler — profile_task
# ---------------------------------------------------------------------------

class TestProfileTask:
    def test_returns_none_when_no_trace(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        result = profiler.profile_task("nonexistent-task")
        assert result is None

    def test_returns_task_context_profile(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "basic-task", [
            _file_read_event("arch", "design.md"),
            _file_write_event("arch", "plan.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("basic-task")
        assert isinstance(profile, TaskContextProfile)

    def test_task_id_preserved(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "my-task-id", [
            _file_read_event("arch", "design.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("my-task-id")
        assert profile is not None
        assert profile.task_id == "my-task-id"

    def test_created_at_is_populated(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "ts-task", [
            _file_read_event("arch", "f.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("ts-task")
        assert profile is not None
        assert profile.created_at != ""

    def test_collects_files_read_per_agent(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "reads-task", [
            _file_read_event("backend-engineer", "src/app.py"),
            _file_read_event("backend-engineer", "src/models.py"),
            _file_write_event("backend-engineer", "src/app.py"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("reads-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert ap.agent_name == "backend-engineer"
        assert "src/app.py" in ap.files_read
        assert "src/models.py" in ap.files_read

    def test_collects_files_written_per_agent(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "writes-task", [
            _file_read_event("arch", "spec.md"),
            _file_write_event("arch", "output.md"),
            _file_write_event("arch", "report.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("writes-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert "output.md" in ap.files_written
        assert "report.md" in ap.files_written

    def test_multiple_agents_profiled_separately(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "multi-agent-task", [
            _file_read_event("architect", "design.md"),
            _file_write_event("architect", "plan.md"),
            _file_read_event("backend-engineer", "plan.md"),
            _file_write_event("backend-engineer", "src/main.py"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("multi-agent-task")
        assert profile is not None
        agent_names = {ap.agent_name for ap in profile.agent_profiles}
        assert "architect" in agent_names
        assert "backend-engineer" in agent_names

    def test_efficiency_score_computed_correctly(self, tmp_path: Path) -> None:
        # 2 reads, 1 write → efficiency = 1/2 = 0.5
        _write_trace_to_disk(tmp_path, "eff-task", [
            _file_read_event("arch", "a.md"),
            _file_read_event("arch", "b.md"),
            _file_write_event("arch", "c.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("eff-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert abs(ap.efficiency_score - 0.5) < 0.0001

    def test_efficiency_score_with_zero_reads(self, tmp_path: Path) -> None:
        # 0 reads, 1 write → efficiency = 1/max(0,1) = 1.0
        _write_trace_to_disk(tmp_path, "no-reads-task", [
            _file_write_event("arch", "output.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("no-reads-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert ap.efficiency_score == 1.0

    def test_efficiency_score_with_zero_writes(self, tmp_path: Path) -> None:
        # 3 reads, 0 writes → efficiency = 0/3 = 0.0
        _write_trace_to_disk(tmp_path, "no-writes-task", [
            _file_read_event("arch", "a.md"),
            _file_read_event("arch", "b.md"),
            _file_read_event("arch", "c.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("no-writes-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert ap.efficiency_score == 0.0

    def test_total_files_read_counts_all_events(self, tmp_path: Path) -> None:
        # Both agents each read the same file → total = 2, unique = 1
        _write_trace_to_disk(tmp_path, "total-task", [
            _file_read_event("arch", "shared.md"),
            _file_read_event("backend-engineer", "shared.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("total-task")
        assert profile is not None
        assert profile.total_files_read == 2

    def test_unique_files_read_deduplicates(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "unique-task", [
            _file_read_event("arch", "shared.md"),
            _file_read_event("backend-engineer", "shared.md"),
            _file_read_event("backend-engineer", "other.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("unique-task")
        assert profile is not None
        assert profile.unique_files_read == 2  # shared.md + other.md

    def test_redundant_reads_computed(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "redundant-task", [
            _file_read_event("arch", "shared.md"),
            _file_read_event("backend-engineer", "shared.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("redundant-task")
        assert profile is not None
        assert profile.redundant_reads == 1  # total(2) - unique(1)

    def test_redundancy_rate_computed(self, tmp_path: Path) -> None:
        # 2 total reads, 1 unique → rate = 0.5
        _write_trace_to_disk(tmp_path, "rate-task", [
            _file_read_event("arch", "shared.md"),
            _file_read_event("backend-engineer", "shared.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("rate-task")
        assert profile is not None
        assert abs(profile.redundancy_rate - 0.5) < 0.0001

    def test_no_redundancy_when_all_reads_unique(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "no-redundancy-task", [
            _file_read_event("arch", "a.md"),
            _file_read_event("backend-engineer", "b.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("no-redundancy-task")
        assert profile is not None
        assert profile.redundant_reads == 0
        assert profile.redundancy_rate == 0.0

    def test_files_referenced_is_read_write_intersection(self, tmp_path: Path) -> None:
        # arch reads a.md and b.md, writes a.md → referenced = {a.md}
        _write_trace_to_disk(tmp_path, "ref-task", [
            _file_read_event("arch", "a.md"),
            _file_read_event("arch", "b.md"),
            _file_write_event("arch", "a.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("ref-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert ap.files_referenced == ["a.md"]
        assert "b.md" not in ap.files_referenced

    def test_non_file_events_ignored(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "non-file-task", [
            _non_file_event("arch"),
            _file_read_event("arch", "real.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("non-file-task")
        assert profile is not None
        # Only one agent profile, with one read.
        arch_profiles = [ap for ap in profile.agent_profiles if ap.agent_name == "arch"]
        assert len(arch_profiles) == 1
        assert arch_profiles[0].files_read == ["real.md"]

    def test_file_events_without_path_ignored(self, tmp_path: Path) -> None:
        """file_read/file_write events with no 'path' in details are skipped."""
        event_no_path = TraceEvent(
            timestamp="2026-03-20T14:30:01+00:00",
            event_type="file_read",
            agent_name="arch",
            phase=1,
            step=1,
            details={},  # no 'path' key
        )
        _write_trace_to_disk(tmp_path, "no-path-task", [event_no_path])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("no-path-task")
        assert profile is not None
        assert profile.total_files_read == 0

    def test_token_estimates_positive_when_reads_exist(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "tokens-task", [
            _file_read_event("arch", "a.md"),
            _file_write_event("arch", "b.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("tokens-task")
        assert profile is not None
        ap = profile.agent_profiles[0]
        assert ap.context_tokens_estimate > 0
        assert ap.output_tokens_estimate > 0


# ---------------------------------------------------------------------------
# ContextProfiler — trace with no file events
# ---------------------------------------------------------------------------

class TestNoFileEvents:
    def test_profile_with_no_file_events(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "no-files-task", [
            _non_file_event("arch"),
            _non_file_event("backend-engineer"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("no-files-task")
        assert profile is not None
        assert profile.total_files_read == 0
        assert profile.unique_files_read == 0
        assert profile.redundant_reads == 0
        assert profile.redundancy_rate == 0.0
        # No agent profiles since neither agent had file activity.
        assert profile.agent_profiles == []

    def test_empty_trace_produces_empty_profile(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "empty-trace", [])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("empty-trace")
        assert profile is not None
        assert profile.agent_profiles == []
        assert profile.total_files_read == 0


# ---------------------------------------------------------------------------
# ContextProfiler — single agent
# ---------------------------------------------------------------------------

class TestSingleAgent:
    def test_single_agent_no_redundancy(self, tmp_path: Path) -> None:
        _write_trace_to_disk(tmp_path, "solo-task", [
            _file_read_event("arch", "a.md"),
            _file_read_event("arch", "b.md"),
            _file_write_event("arch", "c.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("solo-task")
        assert profile is not None
        assert len(profile.agent_profiles) == 1
        assert profile.redundant_reads == 0

    def test_single_agent_self_redundancy(self, tmp_path: Path) -> None:
        """Agent reading the same file twice counts as redundant."""
        _write_trace_to_disk(tmp_path, "self-redundant", [
            _file_read_event("arch", "a.md"),
            _file_read_event("arch", "a.md"),  # duplicate read
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("self-redundant")
        assert profile is not None
        assert profile.total_files_read == 2
        assert profile.unique_files_read == 1
        assert profile.redundant_reads == 1


# ---------------------------------------------------------------------------
# ContextProfiler — save_profile / load_profile
# ---------------------------------------------------------------------------

class TestSaveLoadProfile:
    def test_save_returns_path(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp = TaskContextProfile(task_id="save-test", created_at="2026-03-20T00:00:00+00:00")
        path = profiler.save_profile(tp)
        assert isinstance(path, Path)

    def test_save_writes_json_file(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp = TaskContextProfile(task_id="write-test")
        path = profiler.save_profile(tp)
        assert path.exists()
        assert path.suffix == ".json"

    def test_save_path_is_under_profiles_dir(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp = TaskContextProfile(task_id="dir-test")
        path = profiler.save_profile(tp)
        assert path.parent == tmp_path / "context-profiles"

    def test_save_creates_profiles_dir_automatically(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path / "deep" / "root")
        tp = TaskContextProfile(task_id="mkdir-test")
        path = profiler.save_profile(tp)
        assert path.exists()

    def test_load_returns_task_context_profile(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp = TaskContextProfile(task_id="load-me", total_files_read=5)
        profiler.save_profile(tp)
        loaded = profiler.load_profile("load-me")
        assert isinstance(loaded, TaskContextProfile)

    def test_load_restores_all_fields(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp = TaskContextProfile(
            task_id="full-restore",
            agent_profiles=[
                AgentContextProfile(
                    agent_name="arch",
                    files_read=["a.md"],
                    files_written=["b.md"],
                    efficiency_score=0.75,
                )
            ],
            total_files_read=3,
            unique_files_read=2,
            redundant_reads=1,
            redundancy_rate=0.333,
            created_at="2026-03-20T14:00:00+00:00",
        )
        profiler.save_profile(tp)
        loaded = profiler.load_profile("full-restore")
        assert loaded is not None
        assert loaded.task_id == "full-restore"
        assert loaded.total_files_read == 3
        assert loaded.unique_files_read == 2
        assert loaded.redundant_reads == 1
        assert abs(loaded.redundancy_rate - 0.333) < 0.0001
        assert loaded.created_at == "2026-03-20T14:00:00+00:00"
        assert len(loaded.agent_profiles) == 1
        assert loaded.agent_profiles[0].agent_name == "arch"
        assert loaded.agent_profiles[0].efficiency_score == 0.75

    def test_load_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        result = profiler.load_profile("does-not-exist")
        assert result is None

    def test_load_returns_none_for_malformed_json(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.profiles_dir.mkdir(parents=True, exist_ok=True)
        (profiler.profiles_dir / "bad.json").write_text("NOT JSON", encoding="utf-8")
        result = profiler.load_profile("bad")
        assert result is None

    def test_save_overwrites_existing_profile(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        tp1 = TaskContextProfile(task_id="overwrite", total_files_read=1)
        tp2 = TaskContextProfile(task_id="overwrite", total_files_read=99)
        profiler.save_profile(tp1)
        profiler.save_profile(tp2)
        loaded = profiler.load_profile("overwrite")
        assert loaded is not None
        assert loaded.total_files_read == 99

    def test_roundtrip_through_profile_task(self, tmp_path: Path) -> None:
        """End-to-end: write trace → profile → save → load."""
        _write_trace_to_disk(tmp_path, "e2e-task", [
            _file_read_event("arch", "spec.md"),
            _file_write_event("arch", "plan.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("e2e-task")
        assert profile is not None
        profiler.save_profile(profile)

        loaded = profiler.load_profile("e2e-task")
        assert loaded is not None
        assert loaded.task_id == "e2e-task"
        assert loaded.total_files_read == 1
        assert len(loaded.agent_profiles) == 1
        assert loaded.agent_profiles[0].agent_name == "arch"


# ---------------------------------------------------------------------------
# ContextProfiler — list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_returns_empty_when_no_dir(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path / "nonexistent")
        assert profiler.list_profiles() == []

    def test_returns_paths(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(task_id="p1"))
        paths = profiler.list_profiles()
        assert len(paths) == 1
        assert paths[0].suffix == ".json"

    def test_count_limits_results(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        for i in range(5):
            profiler.save_profile(TaskContextProfile(task_id=f"task-{i}"))
        assert len(profiler.list_profiles(count=3)) == 3

    def test_count_larger_than_available_returns_all(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        for i in range(3):
            profiler.save_profile(TaskContextProfile(task_id=f"task-{i}"))
        assert len(profiler.list_profiles(count=100)) == 3


# ---------------------------------------------------------------------------
# ContextProfiler — agent_summary
# ---------------------------------------------------------------------------

class TestAgentSummary:
    def test_returns_zero_stats_when_no_profiles(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        summary = profiler.agent_summary("unknown-agent")
        assert summary["times_seen"] == 0
        assert summary["avg_files_read"] == 0.0
        assert summary["avg_efficiency"] == 0.0
        assert summary["most_read_files"] == {}
        assert summary["low_efficiency_tasks"] == []

    def test_times_seen_counts_appearances(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        for i in range(3):
            profiler.save_profile(TaskContextProfile(
                task_id=f"task-{i}",
                agent_profiles=[AgentContextProfile(agent_name="arch", efficiency_score=0.5)],
            ))
        summary = profiler.agent_summary("arch")
        assert summary["times_seen"] == 3

    def test_avg_files_read_computed(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="task-a",
            agent_profiles=[AgentContextProfile(
                agent_name="arch", files_read=["a.md", "b.md"],
            )],
        ))
        profiler.save_profile(TaskContextProfile(
            task_id="task-b",
            agent_profiles=[AgentContextProfile(
                agent_name="arch", files_read=["c.md"],
            )],
        ))
        summary = profiler.agent_summary("arch")
        # (2 + 1) / 2 = 1.5
        assert summary["avg_files_read"] == 1.5

    def test_avg_efficiency_computed(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="task-x",
            agent_profiles=[AgentContextProfile(agent_name="be", efficiency_score=0.4)],
        ))
        profiler.save_profile(TaskContextProfile(
            task_id="task-y",
            agent_profiles=[AgentContextProfile(agent_name="be", efficiency_score=0.6)],
        ))
        summary = profiler.agent_summary("be")
        assert abs(summary["avg_efficiency"] - 0.5) < 0.0001

    def test_most_read_files_returned(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        for i in range(3):
            profiler.save_profile(TaskContextProfile(
                task_id=f"task-{i}",
                agent_profiles=[AgentContextProfile(
                    agent_name="arch",
                    files_read=["popular.md", f"unique-{i}.md"],
                )],
            ))
        summary = profiler.agent_summary("arch")
        # popular.md appears 3 times; each unique-N.md appears once.
        assert "popular.md" in summary["most_read_files"]
        assert summary["most_read_files"]["popular.md"] == 3

    def test_most_read_files_limited_to_five(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        files = [f"file-{i}.md" for i in range(10)]
        profiler.save_profile(TaskContextProfile(
            task_id="many-files",
            agent_profiles=[AgentContextProfile(agent_name="arch", files_read=files)],
        ))
        summary = profiler.agent_summary("arch")
        assert len(summary["most_read_files"]) <= 5

    def test_low_efficiency_tasks_flagged(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="low-eff",
            agent_profiles=[AgentContextProfile(agent_name="arch", efficiency_score=0.1)],
        ))
        profiler.save_profile(TaskContextProfile(
            task_id="high-eff",
            agent_profiles=[AgentContextProfile(agent_name="arch", efficiency_score=0.8)],
        ))
        summary = profiler.agent_summary("arch")
        assert "low-eff" in summary["low_efficiency_tasks"]
        assert "high-eff" not in summary["low_efficiency_tasks"]

    def test_other_agents_not_counted(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="mixed",
            agent_profiles=[
                AgentContextProfile(agent_name="arch", efficiency_score=0.5),
                AgentContextProfile(agent_name="backend-engineer", efficiency_score=0.7),
            ],
        ))
        summary = profiler.agent_summary("arch")
        assert summary["times_seen"] == 1
        # avg_efficiency should only include arch's score
        assert abs(summary["avg_efficiency"] - 0.5) < 0.0001


# ---------------------------------------------------------------------------
# ContextProfiler — generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_no_profiles_returns_no_profiles_message(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        report = profiler.generate_report()
        assert "No profiles found" in report

    def test_report_is_markdown(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(task_id="my-task"))
        report = profiler.generate_report()
        assert report.startswith("#")

    def test_report_contains_task_id(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(task_id="special-task-id"))
        report = profiler.generate_report()
        assert "special-task-id" in report

    def test_report_contains_redundancy_info(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="t1",
            total_files_read=10,
            unique_files_read=8,
            redundant_reads=2,
            redundancy_rate=0.2,
        ))
        report = profiler.generate_report()
        assert "20.0%" in report or "20%" in report

    def test_report_flags_low_efficiency_agents(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="flagged-task",
            agent_profiles=[AgentContextProfile(
                agent_name="broad-reader",
                efficiency_score=0.1,
            )],
        ))
        report = profiler.generate_report()
        assert "broad-reader" in report
        # Should appear in the flagged section.
        assert "0.1" in report or "Flagged" in report

    def test_report_contains_no_flagged_agents_message_when_clean(
        self, tmp_path: Path
    ) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="clean-task",
            agent_profiles=[AgentContextProfile(
                agent_name="focused",
                efficiency_score=0.8,
            )],
        ))
        report = profiler.generate_report()
        assert "No agents flagged" in report

    def test_report_contains_overall_statistics(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        profiler.save_profile(TaskContextProfile(
            task_id="stats-task",
            agent_profiles=[AgentContextProfile(agent_name="arch", efficiency_score=0.6)],
        ))
        report = profiler.generate_report()
        assert "Overall" in report or "statistics" in report.lower()

    def test_report_handles_multiple_tasks(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path)
        for i in range(3):
            profiler.save_profile(TaskContextProfile(
                task_id=f"batch-task-{i}",
                agent_profiles=[AgentContextProfile(agent_name="arch")],
            ))
        report = profiler.generate_report()
        assert "batch-task-0" in report
        assert "batch-task-1" in report
        assert "batch-task-2" in report


# ---------------------------------------------------------------------------
# ContextProfiler — edge cases and integration
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_system_events_grouped_under_system_agent(self, tmp_path: Path) -> None:
        """Events with agent_name=None are grouped under '__system__'."""
        event = TraceEvent(
            timestamp="2026-03-20T14:30:00+00:00",
            event_type="file_read",
            agent_name=None,
            phase=0,
            step=0,
            details={"path": "system.lock"},
        )
        _write_trace_to_disk(tmp_path, "sys-task", [event])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("sys-task")
        assert profile is not None
        agent_names = {ap.agent_name for ap in profile.agent_profiles}
        assert "__system__" in agent_names

    def test_many_agents_with_heavy_redundancy(self, tmp_path: Path) -> None:
        """5 agents all reading the same 3 files produces redundancy_rate = 0.8."""
        events: list[TraceEvent] = []
        agents = ["a", "b", "c", "d", "e"]
        for agent in agents:
            for f in ["common1.md", "common2.md", "common3.md"]:
                events.append(_file_read_event(agent, f))
        _write_trace_to_disk(tmp_path, "heavy-redundancy", events)
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("heavy-redundancy")
        assert profile is not None
        # total = 15, unique = 3, redundant = 12, rate = 12/15 = 0.8
        assert profile.total_files_read == 15
        assert profile.unique_files_read == 3
        assert profile.redundant_reads == 12
        assert abs(profile.redundancy_rate - 0.8) < 0.0001

    def test_profile_task_then_agent_summary_integration(self, tmp_path: Path) -> None:
        """profile_task → save_profile → agent_summary round-trip."""
        _write_trace_to_disk(tmp_path, "integration-task", [
            _file_read_event("arch", "design.md"),
            _file_read_event("arch", "spec.md"),
            _file_write_event("arch", "plan.md"),
        ])
        profiler = ContextProfiler(tmp_path)
        profile = profiler.profile_task("integration-task")
        assert profile is not None
        profiler.save_profile(profile)

        summary = profiler.agent_summary("arch")
        assert summary["times_seen"] == 1
        assert summary["avg_files_read"] == 2.0
        # efficiency = 1 write / 2 reads = 0.5
        assert abs(summary["avg_efficiency"] - 0.5) < 0.0001

    def test_load_profile_nonexistent_dir(self, tmp_path: Path) -> None:
        profiler = ContextProfiler(tmp_path / "no-such-dir")
        assert profiler.load_profile("anything") is None
        assert profiler.list_profiles() == []
