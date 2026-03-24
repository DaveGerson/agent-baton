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
from agent_baton.core.observe.retrospective import RetrospectiveEngine


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
# DECISION: Collapsed 8 individual "contains X" and section-presence tests
# into 2 parameterized tests (one for field values, one for section headings).
# Kept distinct tests for: empty-section omission (5 cases → 1 parameterized),
# metrics edge cases (duration N/A, gate fraction, keep tag) as separate tests
# since they each need a distinct Retrospective fixture.
# ---------------------------------------------------------------------------

class TestRetrospectiveToMarkdown:
    @pytest.mark.parametrize("expected_substring", [
        "# Retrospective: Full Retro Task",  # title
        "abc-123",                           # task_id
        "2026-03-01T10:00:00",               # timestamp
        "Agents: 2",                         # metrics
        "Retries: 1",                        # metrics
        "architect",                         # what_worked agent
        "Designed cleanly",                  # what_worked detail
        "backend",                           # what_didnt agent
        "Missed edge case",                  # what_didnt issue
        "root cause: Unclear spec",          # what_didnt root_cause
        "No Redis knowledge",                # knowledge gap description
        "fix: create knowledge pack",        # knowledge gap fix
        "Create:",                           # roster recommendation action
        "redis-specialist",                  # roster recommendation target
        "Needed for caching tasks",          # roster recommendation reason
        "Phase 2",                           # sequencing note phase
        "Gate was redundant",                # sequencing note observation
        "consider removing",                 # keep=False tag
    ])
    def test_markdown_contains(self, expected_substring: str) -> None:
        retro = _full_retro("abc-123")
        md = retro.to_markdown()
        assert expected_substring in md

    @pytest.mark.parametrize("section_heading", [
        "## Metrics",
        "## What Worked",
        "## What Didn't",
        "## Knowledge Gaps Exposed",
        "## Roster Recommendations",
        "## Sequencing Notes",
    ])
    def test_sections_present(self, section_heading: str) -> None:
        md = _full_retro().to_markdown()
        assert section_heading in md

    @pytest.mark.parametrize("absent_section", [
        "## What Worked",
        "## What Didn't",
        "## Knowledge Gaps",
        "## Roster Recommendations",
        "## Sequencing Notes",
    ])
    def test_omits_empty_sections(self, absent_section: str) -> None:
        retro = Retrospective(
            task_id="t1", task_name="Minimal", timestamp="2026-01-01T00:00:00"
        )
        assert absent_section not in retro.to_markdown()

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
# DECISION: Merged trivial save tests (creates_file, returns_correct_path,
# creates_parent_dirs, content_is_markdown) into 1 comprehensive test.
# load tests kept where they exercise distinct logic (full restore, missing
# file, slash-in-id handling). load_returns_content_for_existing merged into
# save comprehensive test.
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineSaveLoad:
    def test_save_comprehensive(self, tmp_path: Path):
        """Covers: file created, correct path name, parent dirs created,
        content starts with markdown header, load returns that content."""
        engine = RetrospectiveEngine(tmp_path / "deep" / "retros")
        path = engine.save(_full_retro("rt-1"))
        assert path.exists()
        assert path.name == "rt-1.md"
        assert (tmp_path / "deep" / "retros").is_dir()
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# Retrospective:")

        loaded = engine.load("rt-1")
        assert loaded is not None
        assert "# Retrospective:" in loaded

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

    def test_list_recent_and_boundary(self, tmp_path: Path):
        """Covers: list_recent returns last N, and returns all when fewer than N."""
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("t1", "t2", "t3", "t4", "t5"):
            engine.save(Retrospective(task_id=tid, task_name=tid, timestamp="2026-01-01"))
        recent = engine.list_recent(3)
        assert len(recent) == 3
        # list_recent returns the LAST N from sorted list
        stems = [p.stem for p in recent]
        assert "t3" in stems or "t4" in stems or "t5" in stems

        # Fewer items than requested → returns all
        engine2 = RetrospectiveEngine(tmp_path / "retros2")
        engine2.save(Retrospective(task_id="only", task_name="T", timestamp="2026-01-01"))
        assert len(engine2.list_recent(10)) == 1

    def test_list_retrospectives_ignores_non_md_files(self, tmp_path: Path):
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        (retros_dir / "notes.txt").write_text("ignore me")
        engine = RetrospectiveEngine(retros_dir)
        assert engine.list_retrospectives() == []


# ---------------------------------------------------------------------------
# RetrospectiveEngine.search
# DECISION: Consolidated 5 search tests into 3 — merged the case-insensitive
# test with the basic match test (they share the same fixture), and merged
# no-retros and no-match into a single parametrized test.
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineSearch:
    def test_search_finds_matching_keyword_case_insensitive(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("searchable"))
        # Exact match
        assert len(engine.search("redis-specialist")) == 1
        # Case-insensitive
        assert len(engine.search("REDIS-SPECIALIST")) == 1

    @pytest.mark.parametrize("keyword", [
        "xyzzy-not-in-there",  # no match in populated dir
    ])
    def test_search_returns_empty_for_no_match(self, keyword: str, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_full_retro("no-match"))
        assert engine.search(keyword) == []

    def test_search_returns_empty_when_no_retros(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        assert engine.search("anything") == []

    def test_search_across_multiple_files(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        # Both retros contain "architect" in what_worked
        for tid in ("r1", "r2", "r3"):
            engine.save(_full_retro(tid))
        results = engine.search("architect")
        assert len(results) == 3


# ---------------------------------------------------------------------------
# RetrospectiveEngine.generate_from_usage
# DECISION: Collapsed 11 individual field-population tests into 3 parameterized
# groups: scalar fields (agent_count, retry_count, risk_level, estimated_tokens,
# task_id, timestamp), gate fields (gates_passed, gates_failed), and pass-through
# list fields (what_worked, knowledge_gaps). task_name (custom + fallback) kept
# separate since it has two distinct inputs.
# ---------------------------------------------------------------------------

class TestGenerateFromUsage:
    def test_populates_scalar_fields(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(
            "my-id",
            timestamp="2026-06-01T00:00:00",
            agents=[_agent("a", retries=2), _agent("b", retries=1), _agent("c")],
            risk_level="HIGH",
        )
        # agents=[a(2), b(1), c(0)] → agent_count=3, retry_count=3
        retro = engine.generate_from_usage(usage)
        assert retro.agent_count == 3
        assert retro.retry_count == 3
        assert retro.risk_level == "HIGH"
        assert retro.task_id == "my-id"
        assert retro.timestamp == "2026-06-01T00:00:00"

    def test_populates_gates_and_tokens(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage(
            gates_passed=4, gates_failed=1,
            agents=[_agent("a", tokens=2000), _agent("b", tokens=3000)],
        )
        retro = engine.generate_from_usage(usage)
        assert retro.gates_passed == 4
        assert retro.gates_failed == 1
        assert retro.estimated_tokens == 5000

    @pytest.mark.parametrize("task_name,expected_name", [
        ("My Custom Task Name", "My Custom Task Name"),
        ("", "task-1"),  # falls back to task_id when name is blank
    ])
    def test_task_name_and_fallback(
        self, task_name: str, expected_name: str, tmp_path: Path
    ) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage("task-1")
        retro = engine.generate_from_usage(usage, task_name=task_name)
        assert retro.task_name == expected_name

    def test_passes_through_list_fields(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _usage()
        worked = [AgentOutcome(name="arch", worked_well="Great")]
        gaps = [KnowledgeGap(description="Missing Redis", suggested_fix="create pack")]
        retro = engine.generate_from_usage(usage, what_worked=worked, knowledge_gaps=gaps)
        assert len(retro.what_worked) == 1
        assert retro.what_worked[0].name == "arch"
        assert len(retro.knowledge_gaps) == 1


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
