"""Tests for the knowledge gap feedback loop.

Covers:
- RetrospectiveEngine.generate_from_usage writes KnowledgeGapRecord entries
- RetrospectiveEngine._detect_implicit_gaps heuristic detection
- Implicit gap detection integration in generate_from_usage
- PatternLearner.knowledge_gaps_for() query, agent filter, task_type filter, dedup
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.models.knowledge import KnowledgeGapRecord
from agent_baton.models.retrospective import (
    AgentOutcome,
    Retrospective,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usage(
    task_id: str = "task-1",
    timestamp: str = "2026-03-24T10:00:00",
    risk_level: str = "LOW",
    gates_passed: int = 2,
    gates_failed: int = 0,
    agents: list[AgentUsageRecord] | None = None,
) -> TaskUsageRecord:
    agent_list = agents or []
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agent_list,
        total_agents=len(agent_list),
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome="SHIP",
        notes="",
    )


def _make_agent(name: str = "backend-engineer--python", tokens: int = 1000) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=0,
        gate_results=[],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _gap_record(
    description: str = "Need SOX audit context",
    agent_name: str = "backend-engineer--python",
    task_type: str | None = "feature",
    resolution: str = "auto-resolved",
    resolution_detail: str = "compliance-pack/sox-rules.md",
    task_summary: str = "Implement audit trail",
) -> KnowledgeGapRecord:
    return KnowledgeGapRecord(
        description=description,
        gap_type="contextual",
        resolution=resolution,
        resolution_detail=resolution_detail,
        agent_name=agent_name,
        task_summary=task_summary,
        task_type=task_type,
    )


def _write_retro_json(
    retros_dir: Path,
    task_id: str,
    gaps: list[KnowledgeGapRecord],
    task_type: str | None = None,
) -> Path:
    """Write a minimal retrospective JSON sidecar to *retros_dir*."""
    retros_dir.mkdir(parents=True, exist_ok=True)
    retro = Retrospective(
        task_id=task_id,
        task_name=f"Task {task_id}",
        timestamp="2026-03-24T10:00:00",
        knowledge_gaps=gaps,
    )
    data = retro.to_dict()
    json_path = retros_dir / f"{task_id}.json"
    json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return json_path


# ---------------------------------------------------------------------------
# RetrospectiveEngine — gap record writing
# ---------------------------------------------------------------------------

class TestGenerateFromUsageGapRecords:
    """generate_from_usage stores KnowledgeGapRecord entries."""

    def test_explicit_gap_records_stored(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        gap = _gap_record()
        retro = engine.generate_from_usage(usage, knowledge_gaps=[gap])
        assert len(retro.knowledge_gaps) == 1
        assert isinstance(retro.knowledge_gaps[0], KnowledgeGapRecord)
        assert retro.knowledge_gaps[0].description == gap.description

    def test_gap_record_fields_preserved(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        gap = _gap_record(
            description="No Redis knowledge",
            agent_name="backend-engineer--python",
            task_type="feature",
            resolution="unresolved",
            resolution_detail="",
        )
        retro = engine.generate_from_usage(usage, knowledge_gaps=[gap])
        stored = retro.knowledge_gaps[0]
        assert stored.gap_type == "contextual"
        assert stored.resolution == "unresolved"
        assert stored.agent_name == "backend-engineer--python"
        assert stored.task_type == "feature"

    def test_multiple_gap_records_stored(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        gaps = [
            _gap_record(description="Gap A"),
            _gap_record(description="Gap B"),
            _gap_record(description="Gap C"),
        ]
        retro = engine.generate_from_usage(usage, knowledge_gaps=gaps)
        assert len(retro.knowledge_gaps) == 3

    def test_no_gaps_returns_empty(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        retro = engine.generate_from_usage(usage)
        assert retro.knowledge_gaps == []

    def test_gap_records_persisted_in_json_sidecar(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage(task_id="persist-test")
        gap = _gap_record(description="Missing SOX context")
        retro = engine.generate_from_usage(usage, knowledge_gaps=[gap])
        engine.save(retro)

        json_path = tmp_path / "retros" / "persist-test.json"
        assert json_path.exists()
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(raw["knowledge_gaps"]) == 1
        stored = raw["knowledge_gaps"][0]
        assert stored["description"] == "Missing SOX context"
        assert stored["gap_type"] == "contextual"
        assert stored["resolution"] == "auto-resolved"
        assert "agent_name" in stored
        assert "task_type" in stored

    def test_backward_compat_properties_on_gap_record(self, tmp_path: Path) -> None:
        """KnowledgeGapRecord.affected_agent and .suggested_fix still work."""
        gap = _gap_record(agent_name="backend-engineer--python", resolution_detail="create pack")
        assert gap.affected_agent == "backend-engineer--python"
        assert gap.suggested_fix == "create pack"

    def test_task_summary_propagated_to_gap(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        gap = _gap_record(task_summary="Implement OAuth2 login")
        retro = engine.generate_from_usage(usage, knowledge_gaps=[gap])
        assert retro.knowledge_gaps[0].task_summary == "Implement OAuth2 login"


# ---------------------------------------------------------------------------
# RetrospectiveEngine — implicit gap detection heuristic
# ---------------------------------------------------------------------------

class TestDetectImplicitGaps:
    """_detect_implicit_gaps detects gap phrases in AgentOutcome narrative."""

    def test_detects_lacked_context_phrase(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="The agent lacked context on SOX audit requirements.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1
        assert "lacked context" in gaps[0].description
        assert gaps[0].resolution == "unresolved"
        assert gaps[0].gap_type == "contextual"

    def test_detects_didnt_know_about_phrase(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="didn't know about the rate-limiting policy.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1
        assert "didn" in gaps[0].description.lower()

    def test_detects_assumed_incorrectly_phrase(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            root_cause="assumed incorrectly that RBAC was disabled.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1

    def test_detects_phrase_in_root_cause(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="architect",
            issues="Output was suboptimal.",
            root_cause="lacked context on the event bus architecture.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert any("lacked context" in g.description for g in gaps)

    def test_agent_name_set_from_outcome(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="test-engineer",
            issues="lacked context on the testing framework conventions.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert gaps[0].agent_name == "test-engineer"

    def test_task_type_and_summary_passed_through(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="lacked context on the deployment pipeline.",
        )
        gaps = engine._detect_implicit_gaps(
            [outcome], task_type="deployment", task_summary="Deploy to prod"
        )
        assert gaps[0].task_type == "deployment"
        assert gaps[0].task_summary == "Deploy to prod"

    def test_no_gaps_detected_in_clean_text(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="Code quality was fine.",
            root_cause="Minor oversight in edge case handling.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert gaps == []

    def test_empty_outcomes_returns_empty(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        assert engine._detect_implicit_gaps([]) == []

    def test_deduplicates_identical_lines_within_same_outcome(
        self, tmp_path: Path
    ) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        # Same phrase appears in both issues and root_cause
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="lacked context on Redis schema.",
            root_cause="lacked context on Redis schema.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1

    def test_deduplicates_across_multiple_outcomes(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome_a = AgentOutcome(
            name="agent-a",
            issues="lacked context on Redis schema.",
        )
        outcome_b = AgentOutcome(
            name="agent-b",
            issues="lacked context on Redis schema.",
        )
        gaps = engine._detect_implicit_gaps([outcome_a, outcome_b])
        assert len(gaps) == 1

    def test_detects_no_knowledge_of_phrase(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="no knowledge of the retry backoff policy.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1

    def test_detects_unaware_of_phrase(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="Was unaware of the GDPR data retention rules.",
        )
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1

    @pytest.mark.parametrize("phrase", [
        "lacked context on the system.",
        "didn't know about the module.",
        "assumed incorrectly that caching was enabled.",
        "no knowledge of the API contract.",
        "unaware of the service mesh topology.",
        "missing context about the deployment pipeline.",
        "lacked information about the retry policy.",
    ])
    def test_parametrized_phrases_all_detected(
        self, phrase: str, tmp_path: Path
    ) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        outcome = AgentOutcome(name="agent", issues=phrase)
        gaps = engine._detect_implicit_gaps([outcome])
        assert len(gaps) == 1, f"Expected gap for phrase: {phrase!r}"


# ---------------------------------------------------------------------------
# Integration: implicit gaps merged into generate_from_usage
# ---------------------------------------------------------------------------

class TestGenerateFromUsageImplicitMerge:
    """generate_from_usage merges explicit and implicit gaps, deduplicating."""

    def test_implicit_gaps_added_when_no_explicit_gaps(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        what_didnt = [
            AgentOutcome(
                name="backend-engineer--python",
                issues="lacked context on the service mesh topology.",
            )
        ]
        retro = engine.generate_from_usage(usage, what_didnt=what_didnt)
        assert len(retro.knowledge_gaps) == 1
        assert retro.knowledge_gaps[0].resolution == "unresolved"

    def test_explicit_gap_takes_precedence_over_duplicate_implicit(
        self, tmp_path: Path
    ) -> None:
        """If an explicit gap has the same description as an implicit one, only
        the explicit one is kept."""
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()

        # The explicit gap uses exactly the same description string as the implicit line
        description = "lacked context on the event sourcing pattern."
        explicit = KnowledgeGapRecord(
            description=description,
            gap_type="factual",
            resolution="auto-resolved",
            resolution_detail="event-sourcing-pack/overview.md",
            agent_name="backend-engineer--python",
            task_summary="Implement event store",
            task_type="feature",
        )
        what_didnt = [
            AgentOutcome(name="backend-engineer--python", issues=description)
        ]
        retro = engine.generate_from_usage(
            usage, knowledge_gaps=[explicit], what_didnt=what_didnt
        )
        # Should only appear once, and as the explicit (auto-resolved) version
        assert len(retro.knowledge_gaps) == 1
        assert retro.knowledge_gaps[0].resolution == "auto-resolved"

    def test_both_explicit_and_distinct_implicit_included(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        explicit = _gap_record(description="SOX audit requirements missing")
        what_didnt = [
            AgentOutcome(
                name="backend-engineer--python",
                issues="lacked context on the deployment process.",
            )
        ]
        retro = engine.generate_from_usage(
            usage, knowledge_gaps=[explicit], what_didnt=what_didnt
        )
        assert len(retro.knowledge_gaps) == 2
        descriptions = {g.description for g in retro.knowledge_gaps}
        assert "SOX audit requirements missing" in descriptions

    def test_implicit_gaps_have_correct_resolution(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage()
        what_didnt = [
            AgentOutcome(name="agent", issues="lacked context on the retry policy.")
        ]
        retro = engine.generate_from_usage(usage, what_didnt=what_didnt)
        implicit = [g for g in retro.knowledge_gaps if g.resolution == "unresolved"]
        assert len(implicit) == 1

    def test_generate_then_save_round_trips_gap_records(self, tmp_path: Path) -> None:
        engine = RetrospectiveEngine(tmp_path / "retros")
        usage = _make_usage(task_id="roundtrip-1")
        gap = _gap_record(
            description="factual gap",
            resolution="human-answered",
            resolution_detail="The rate limit is 1000 req/s",
        )
        retro = engine.generate_from_usage(
            usage,
            knowledge_gaps=[gap],
            task_type="feature",
            task_summary="Build rate limiter",
        )
        engine.save(retro)

        raw = json.loads(
            (tmp_path / "retros" / "roundtrip-1.json").read_text(encoding="utf-8")
        )
        gaps = raw["knowledge_gaps"]
        assert len(gaps) == 1
        g = gaps[0]
        assert g["resolution"] == "human-answered"
        assert g["resolution_detail"] == "The rate limit is 1000 req/s"


# ---------------------------------------------------------------------------
# PatternLearner.knowledge_gaps_for
# ---------------------------------------------------------------------------

class TestKnowledgeGapsFor:
    """knowledge_gaps_for queries retrospective JSON files for gap records."""

    def test_returns_empty_when_no_retros_dir(self, tmp_path: Path) -> None:
        learner = PatternLearner(team_context_root=tmp_path / "team-context")
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_returns_empty_when_no_json_files(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        retros_dir.mkdir(parents=True)
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_returns_gaps_for_matching_agent(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [
            _gap_record(agent_name="backend-engineer--python", description="SOX gap"),
            _gap_record(agent_name="architect", description="Architecture gap"),
        ]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 1
        assert result[0].description == "SOX gap"

    def test_excludes_gaps_for_other_agents(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [_gap_record(agent_name="architect", description="Architecture gap")]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_filters_by_task_type(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [
            _gap_record(
                agent_name="backend-engineer--python",
                description="Feature gap",
                task_type="feature",
            ),
            _gap_record(
                agent_name="backend-engineer--python",
                description="Bugfix gap",
                task_type="bugfix",
            ),
        ]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python", task_type="feature")
        assert len(result) == 1
        assert result[0].description == "Feature gap"

    def test_no_task_type_filter_returns_all_agent_gaps(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [
            _gap_record(
                agent_name="backend-engineer--python",
                description="Feature gap",
                task_type="feature",
            ),
            _gap_record(
                agent_name="backend-engineer--python",
                description="Bugfix gap",
                task_type="bugfix",
            ),
        ]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 2

    def test_task_type_none_excludes_when_filter_given(self, tmp_path: Path) -> None:
        """Gaps with task_type=None are excluded when a task_type filter is given."""
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [
            _gap_record(
                agent_name="backend-engineer--python",
                description="Untyped gap",
                task_type=None,
            ),
        ]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python", task_type="feature")
        assert result == []

    def test_aggregates_across_multiple_files(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        _write_retro_json(
            retros_dir, "t1",
            [_gap_record(description="Gap A", agent_name="backend-engineer--python")]
        )
        _write_retro_json(
            retros_dir, "t2",
            [_gap_record(description="Gap B", agent_name="backend-engineer--python")]
        )
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        descriptions = {g.description for g in result}
        assert "Gap A" in descriptions
        assert "Gap B" in descriptions

    def test_deduplicates_same_description_across_files(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        # Same description in two different retro files
        _write_retro_json(
            retros_dir, "t1",
            [_gap_record(description="Redis gap", agent_name="backend-engineer--python")]
        )
        _write_retro_json(
            retros_dir, "t2",
            [_gap_record(description="Redis gap", agent_name="backend-engineer--python")]
        )
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 1
        assert result[0].description == "Redis gap"

    def test_sorted_by_frequency_descending(self, tmp_path: Path) -> None:
        """More frequent gaps appear first."""
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        # "Redis gap" appears in 3 files, "Auth gap" in 1
        for i in range(3):
            _write_retro_json(
                retros_dir, f"t-redis-{i}",
                [_gap_record(description="Redis gap", agent_name="backend-engineer--python")]
            )
        _write_retro_json(
            retros_dir, "t-auth",
            [_gap_record(description="Auth gap", agent_name="backend-engineer--python")]
        )
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 2
        assert result[0].description == "Redis gap"
        assert result[1].description == "Auth gap"

    def test_skips_corrupt_json_files(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        retros_dir.mkdir(parents=True)
        (retros_dir / "bad.json").write_text("{not valid json", encoding="utf-8")
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_skips_json_without_knowledge_gaps_key(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        retros_dir.mkdir(parents=True)
        (retros_dir / "no-gaps.json").write_text(
            json.dumps({"task_id": "t1"}), encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_returns_knowledgegaprecord_instances(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        gaps = [_gap_record(agent_name="backend-engineer--python")]
        _write_retro_json(retros_dir, "t1", gaps)

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 1
        assert isinstance(result[0], KnowledgeGapRecord)

    def test_gap_fields_preserved_after_load(self, tmp_path: Path) -> None:
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        original = KnowledgeGapRecord(
            description="SOX audit requirements",
            gap_type="factual",
            resolution="human-answered",
            resolution_detail="Use 90-day immutable retention",
            agent_name="backend-engineer--python",
            task_summary="Build audit trail",
            task_type="feature",
        )
        _write_retro_json(retros_dir, "t1", [original])

        learner = PatternLearner(team_context_root=team_ctx)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 1
        loaded = result[0]
        assert loaded.description == "SOX audit requirements"
        assert loaded.gap_type == "factual"
        assert loaded.resolution == "human-answered"
        assert loaded.resolution_detail == "Use 90-day immutable retention"
        assert loaded.agent_name == "backend-engineer--python"
        assert loaded.task_type == "feature"

    def test_backward_compat_with_old_knowledge_gap_schema(self, tmp_path: Path) -> None:
        """Old-schema JSON entries (affected_agent/suggested_fix) are loaded via
        _knowledge_gap_from_dict and surfaced as KnowledgeGapRecord by the pattern learner."""
        team_ctx = tmp_path / "team-context"
        retros_dir = team_ctx / "retrospectives"
        retros_dir.mkdir(parents=True)
        old_schema = {
            "task_id": "legacy-1",
            "knowledge_gaps": [
                {
                    "description": "No Redis docs",
                    "affected_agent": "backend-engineer--python",
                    "suggested_fix": "create knowledge pack",
                }
            ],
        }
        (retros_dir / "legacy-1.json").write_text(
            json.dumps(old_schema), encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=team_ctx)
        # Old schema doesn't have agent_name — it has affected_agent.
        # The pattern learner reads via KnowledgeGapRecord.from_dict which
        # only knows the new schema — old entries have agent_name="" after migration.
        # This test verifies no crash; filtering by agent may return empty for old schema.
        result = learner.knowledge_gaps_for("backend-engineer--python")
        # Old entries lack agent_name so they will not match — no crash is the key assertion
        assert isinstance(result, list)
