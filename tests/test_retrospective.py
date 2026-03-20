"""Tests for agent_baton.models.retrospective and agent_baton.core.retrospective."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.retrospective import RetrospectiveEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(name: str = "arch", retries: int = 0, tokens: int = 1000) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=retries,
        gate_results=[],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _usage(
    task_id: str = "task-1",
    timestamp: str = "2026-03-01T10:00:00",
    agents: list[AgentUsageRecord] | None = None,
    risk_level: str = "LOW",
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
        outcome="SHIP",
        notes="",
    )


def _full_retro(task_id: str = "task-1") -> Retrospective:
    return Retrospective(
        task_id=task_id,
        task_name="Full Retro Task",
        timestamp="2026-03-01T10:00:00",
        agent_count=2,
        retry_count=1,
        gates_passed=3,
        gates_failed=1,
        risk_level="MEDIUM",
        duration_estimate="2h",
        estimated_tokens=5000,
        what_worked=[AgentOutcome(name="architect", worked_well="Designed cleanly")],
        what_didnt=[AgentOutcome(name="backend", issues="Missed edge case",
                                 root_cause="Unclear spec")],
        knowledge_gaps=[KnowledgeGap(description="No Redis knowledge",
                                     affected_agent="backend",
                                     suggested_fix="create knowledge pack")],
        roster_recommendations=[
            RosterRecommendation(action="create", target="redis-specialist",
                                 reason="Needed for caching tasks")
        ],
        sequencing_notes=[SequencingNote(phase="2", observation="Gate was redundant",
                                         keep=False)],
    )


# ---------------------------------------------------------------------------
# Retrospective.to_markdown
# ---------------------------------------------------------------------------

class TestRetrospectiveToMarkdown:
    def test_title_and_task_id_present(self):
        retro = _full_retro("abc-123")
        md = retro.to_markdown()
        assert "# Retrospective: Full Retro Task" in md
        assert "abc-123" in md

    def test_timestamp_present(self):
        retro = _full_retro()
        assert "2026-03-01T10:00:00" in retro.to_markdown()

    def test_metrics_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## Metrics" in md
        assert "Agents: 2" in md
        assert "Retries: 1" in md

    def test_what_worked_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## What Worked" in md
        assert "architect" in md
        assert "Designed cleanly" in md

    def test_what_didnt_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## What Didn't" in md
        assert "backend" in md
        assert "Missed edge case" in md
        assert "root cause: Unclear spec" in md

    def test_knowledge_gaps_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## Knowledge Gaps Exposed" in md
        assert "No Redis knowledge" in md
        assert "fix: create knowledge pack" in md

    def test_roster_recommendations_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## Roster Recommendations" in md
        assert "Create:" in md
        assert "redis-specialist" in md
        assert "Needed for caching tasks" in md

    def test_sequencing_notes_section_present(self):
        retro = _full_retro()
        md = retro.to_markdown()
        assert "## Sequencing Notes" in md
        assert "Phase 2" in md
        assert "Gate was redundant" in md
        assert "consider removing" in md

    def test_omits_empty_what_worked_section(self):
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert "## What Worked" not in retro.to_markdown()

    def test_omits_empty_what_didnt_section(self):
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert "## What Didn't" not in retro.to_markdown()

    def test_omits_empty_knowledge_gaps_section(self):
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert "## Knowledge Gaps" not in retro.to_markdown()

    def test_omits_empty_roster_recommendations_section(self):
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert "## Roster Recommendations" not in retro.to_markdown()

    def test_omits_empty_sequencing_notes_section(self):
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert "## Sequencing Notes" not in retro.to_markdown()

    def test_metrics_duration_na_when_empty(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-01-01T00:00:00",
            duration_estimate="",
        )
        assert "N/A" in retro.to_markdown()

    def test_gate_fraction_in_metrics(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-01-01T00:00:00",
            gates_passed=3, gates_failed=1,
        )
        md = retro.to_markdown()
        # gates_passed / (gates_passed + gates_failed) -> 3/4
        assert "3/4" in md

    def test_keep_tag_shows_keep_for_keep_true(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-01-01",
            sequencing_notes=[SequencingNote(phase="1", observation="Good gate", keep=True)]
        )
        assert "(keep)" in retro.to_markdown()


# ---------------------------------------------------------------------------
# RetrospectiveEngine.save / load
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineSaveLoad:
    def test_save_creates_file(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = _full_retro("save-test")
        path = engine.save(retro)
        assert path.exists()

    def test_save_returns_correct_path(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        path = engine.save(_full_retro("rt-1"))
        assert path.name == "rt-1.md"

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "deep" / "retros")
        engine.save(_full_retro("x"))
        assert (tmp_path / "deep" / "retros").is_dir()

    def test_save_content_is_markdown(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        path = engine.save(_full_retro("md-check"))
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# Retrospective:")

    def test_load_returns_content_for_existing(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("load-me"))
        content = engine.load("load-me")
        assert content is not None
        assert "# Retrospective:" in content

    def test_load_returns_none_for_missing(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        assert engine.load("nonexistent-task") is None

    def test_save_sanitises_slashes_in_task_id(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = Retrospective(
            task_id="my/task/id", task_name="T", timestamp="2026-01-01"
        )
        path = engine.save(retro)
        assert "/" not in path.name
        assert path.name == "my-task-id.md"

    def test_load_handles_slash_in_task_id(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = Retrospective(
            task_id="a/b", task_name="T", timestamp="2026-01-01"
        )
        engine.save(retro)
        assert engine.load("a/b") is not None


# ---------------------------------------------------------------------------
# RetrospectiveEngine.list_retrospectives / list_recent
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineList:
    def test_list_returns_empty_when_dir_missing(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "no-such-dir")
        assert engine.list_retrospectives() == []

    def test_list_returns_all_md_files(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("alpha", "beta", "gamma"):
            engine.save(Retrospective(task_id=tid, task_name=tid, timestamp="2026-01-01"))
        paths = engine.list_retrospectives()
        assert len(paths) == 3

    def test_list_sorted_alphabetically(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("c", "a", "b"):
            engine.save(Retrospective(task_id=tid, task_name=tid, timestamp="2026-01-01"))
        names = [p.stem for p in engine.list_retrospectives()]
        assert names == sorted(names)

    def test_list_recent_returns_last_n(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("t1", "t2", "t3", "t4", "t5"):
            engine.save(Retrospective(task_id=tid, task_name=tid, timestamp="2026-01-01"))
        recent = engine.list_recent(3)
        assert len(recent) == 3
        # list_recent returns the LAST N from sorted list
        stems = [p.stem for p in recent]
        assert "t3" in stems or "t4" in stems or "t5" in stems

    def test_list_recent_returns_all_when_fewer_than_n(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(Retrospective(task_id="only", task_name="T", timestamp="2026-01-01"))
        assert len(engine.list_recent(10)) == 1

    def test_list_retrospectives_ignores_non_md_files(self, tmp_path: Path):
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        (retros_dir / "notes.txt").write_text("ignore me")
        engine = RetrospectiveEngine(retros_dir)
        assert engine.list_retrospectives() == []


# ---------------------------------------------------------------------------
# RetrospectiveEngine.search
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineSearch:
    def test_search_finds_matching_keyword(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = _full_retro("searchable")
        engine.save(retro)
        results = engine.search("redis-specialist")
        assert len(results) == 1

    def test_search_is_case_insensitive(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("ci"))
        results = engine.search("REDIS-SPECIALIST")
        assert len(results) == 1

    def test_search_returns_empty_for_no_match(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("no-match"))
        assert engine.search("xyzzy-not-in-there") == []

    def test_search_across_multiple_files(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        # Both retros contain "architect" in what_worked
        for tid in ("r1", "r2", "r3"):
            engine.save(_full_retro(tid))
        results = engine.search("architect")
        assert len(results) == 3

    def test_search_returns_empty_when_no_retros(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        assert engine.search("anything") == []


# ---------------------------------------------------------------------------
# RetrospectiveEngine.generate_from_usage
# ---------------------------------------------------------------------------

class TestGenerateFromUsage:
    def test_populates_agent_count(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(agents=[_agent("a"), _agent("b"), _agent("c")])
        retro = engine.generate_from_usage(usage)
        assert retro.agent_count == 3

    def test_populates_retry_count(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(agents=[_agent("a", retries=2), _agent("b", retries=1)])
        retro = engine.generate_from_usage(usage)
        assert retro.retry_count == 3

    def test_populates_gates_passed_and_failed(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(gates_passed=4, gates_failed=1)
        retro = engine.generate_from_usage(usage)
        assert retro.gates_passed == 4
        assert retro.gates_failed == 1

    def test_populates_risk_level(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(risk_level="HIGH")
        retro = engine.generate_from_usage(usage)
        assert retro.risk_level == "HIGH"

    def test_populates_estimated_tokens(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(agents=[_agent("a", tokens=2000), _agent("b", tokens=3000)])
        retro = engine.generate_from_usage(usage)
        assert retro.estimated_tokens == 5000

    def test_uses_task_name_parameter(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage("my-task")
        retro = engine.generate_from_usage(usage, task_name="My Custom Task Name")
        assert retro.task_name == "My Custom Task Name"

    def test_falls_back_to_task_id_when_no_name(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage("fallback-id")
        retro = engine.generate_from_usage(usage, task_name="")
        assert retro.task_name == "fallback-id"

    def test_passes_through_what_worked(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage()
        worked = [AgentOutcome(name="arch", worked_well="Great")]
        retro = engine.generate_from_usage(usage, what_worked=worked)
        assert len(retro.what_worked) == 1
        assert retro.what_worked[0].name == "arch"

    def test_passes_through_knowledge_gaps(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage()
        gaps = [KnowledgeGap(description="Missing Redis", suggested_fix="create pack")]
        retro = engine.generate_from_usage(usage, knowledge_gaps=gaps)
        assert len(retro.knowledge_gaps) == 1

    def test_preserves_task_id_and_timestamp(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage("my-id", timestamp="2026-06-01T00:00:00")
        retro = engine.generate_from_usage(usage)
        assert retro.task_id == "my-id"
        assert retro.timestamp == "2026-06-01T00:00:00"


# ---------------------------------------------------------------------------
# RetrospectiveEngine.extract_recommendations
# ---------------------------------------------------------------------------

class TestExtractRecommendations:
    def test_extracts_create_recommendation(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("r1"))
        recs = engine.extract_recommendations()
        assert any(r.action == "create" and r.target == "redis-specialist" for r in recs)

    def test_returns_empty_when_no_retros(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        assert engine.extract_recommendations() == []

    def test_aggregates_across_multiple_retros(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("r1", "r2"):
            engine.save(_full_retro(tid))
        recs = engine.extract_recommendations()
        # Both retros have the same "create redis-specialist" recommendation
        assert len(recs) == 2

    def test_extracts_improve_recommendation(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-01-01",
            roster_recommendations=[
                RosterRecommendation(action="improve", target="backend-engineer", reason="")
            ],
        )
        engine.save(retro)
        recs = engine.extract_recommendations()
        assert any(r.action == "improve" and r.target == "backend-engineer" for r in recs)

    def test_skips_malformed_recommendation_lines(self, tmp_path: Path):
        """Lines in the Roster Recommendations section that don't parse cleanly are skipped."""
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        # Write a retro with a malformed recommendation line
        (retros_dir / "bad.md").write_text(
            "# Retrospective: Bad\n\n## Roster Recommendations\n- not a bold action line\n",
            encoding="utf-8",
        )
        engine = RetrospectiveEngine(retros_dir)
        # Should not raise and should return empty (no parseable recs)
        recs = engine.extract_recommendations()
        assert recs == []
