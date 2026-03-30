"""Tests for team collaboration models and their integration points.

Covers:
- TeamCompositionRecord (models.retrospective) — serialisation
- ConflictRecord (models.retrospective) — serialisation
- TeamPattern (models.pattern) — serialisation
- Retrospective backward-compat, round-trip, and to_markdown() team sections
- PatternLearner.analyze_team_patterns / refresh_team_patterns /
  load_team_patterns / get_team_cost_estimate
- RetrospectiveEngine.generate_from_usage team_compositions + conflicts passthrough
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.retrospective import (
    ConflictRecord,
    Retrospective,
    TeamCompositionRecord,
)
from agent_baton.models.pattern import TeamPattern
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.observe.retrospective import RetrospectiveEngine


# ---------------------------------------------------------------------------
# Helper factories (match the project-wide convention)
# ---------------------------------------------------------------------------

def _agent(
    name: str = "architect",
    retries: int = 0,
    gate_results: list[str] | None = None,
    estimated_tokens: int = 1000,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=retries,
        gate_results=gate_results or [],
        estimated_tokens=estimated_tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str = "task-001",
    sequencing_mode: str = "phased_delivery",
    outcome: str = "SHIP",
    agents: list[AgentUsageRecord] | None = None,
    timestamp: str = "2026-03-01T10:00:00",
) -> TaskUsageRecord:
    agent_list = agents if agents is not None else []
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agent_list,
        total_agents=len(agent_list),
        risk_level="LOW",
        sequencing_mode=sequencing_mode,
        gates_passed=2,
        gates_failed=0,
        outcome=outcome,
        notes="",
    )


def _write_tasks(log_path: Path, tasks: list[TaskUsageRecord]) -> None:
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)


@pytest.fixture
def tmp_context(tmp_path: Path) -> Path:
    return tmp_path / "team-context"


# ---------------------------------------------------------------------------
# TestTeamCompositionRecordSerialization
# ---------------------------------------------------------------------------

class TestTeamCompositionRecordSerialization:
    def _sample(self) -> TeamCompositionRecord:
        return TeamCompositionRecord(
            step_id="step-3",
            agents=["architect", "security-reviewer"],
            roles={"architect": "lead", "security-reviewer": "reviewer"},
            outcome="success",
            task_type="feature",
            token_cost=4200,
        )

    def test_roundtrip_is_identity(self):
        rec = self._sample()
        assert TeamCompositionRecord.from_dict(rec.to_dict()) == rec

    def test_to_dict_contains_all_fields(self):
        rec = self._sample()
        d = rec.to_dict()
        assert d["step_id"] == "step-3"
        assert d["agents"] == ["architect", "security-reviewer"]
        assert d["roles"] == {"architect": "lead", "security-reviewer": "reviewer"}
        assert d["outcome"] == "success"
        assert d["task_type"] == "feature"
        assert d["token_cost"] == 4200

    def test_from_dict_defaults_for_optional_fields(self):
        rec = TeamCompositionRecord.from_dict({"step_id": "s1", "agents": ["a", "b"]})
        assert rec.roles == {}
        assert rec.outcome == "success"
        assert rec.task_type is None
        assert rec.token_cost == 0

    def test_task_type_none_survives_roundtrip(self):
        rec = TeamCompositionRecord(step_id="s1", agents=["a", "b"], task_type=None)
        restored = TeamCompositionRecord.from_dict(rec.to_dict())
        assert restored.task_type is None

    @pytest.mark.parametrize("outcome", ["success", "failure"])
    def test_outcome_values(self, outcome: str):
        rec = TeamCompositionRecord.from_dict({
            "step_id": "s1", "agents": ["a"], "outcome": outcome,
        })
        assert rec.outcome == outcome

    def test_from_dict_missing_agents_defaults_to_empty_list(self):
        rec = TeamCompositionRecord.from_dict({"step_id": "s1"})
        assert rec.agents == []


# ---------------------------------------------------------------------------
# TestConflictRecordSerialization
# ---------------------------------------------------------------------------

class TestConflictRecordSerialization:
    def _sample(self) -> ConflictRecord:
        return ConflictRecord(
            conflict_id="c-001",
            step_id="step-5",
            agents=["backend-engineer", "security-reviewer"],
            positions={
                "backend-engineer": "Inline auth is fine",
                "security-reviewer": "Auth must use middleware",
            },
            evidence={
                "security-reviewer": "OWASP A02:2021",
            },
            severity="high",
            resolution="human_decision",
            resolution_detail="Use middleware pattern per security guidance",
            resolved_by="human",
        )

    def test_roundtrip_is_identity(self):
        rec = self._sample()
        assert ConflictRecord.from_dict(rec.to_dict()) == rec

    def test_to_dict_contains_all_fields(self):
        rec = self._sample()
        d = rec.to_dict()
        assert d["conflict_id"] == "c-001"
        assert d["step_id"] == "step-5"
        assert d["agents"] == ["backend-engineer", "security-reviewer"]
        assert d["severity"] == "high"
        assert d["resolution"] == "human_decision"
        assert d["resolved_by"] == "human"
        assert "Use middleware" in d["resolution_detail"]

    def test_from_dict_defaults(self):
        rec = ConflictRecord.from_dict({
            "conflict_id": "c-002",
            "step_id": "s1",
            "agents": ["a", "b"],
        })
        assert rec.positions == {}
        assert rec.evidence == {}
        assert rec.severity == "medium"
        assert rec.resolution == "unresolved"
        assert rec.resolution_detail == ""
        assert rec.resolved_by == "unresolved"

    @pytest.mark.parametrize("severity", ["low", "medium", "high"])
    def test_severity_values(self, severity: str):
        rec = ConflictRecord.from_dict({
            "conflict_id": "x", "step_id": "s", "agents": [],
            "severity": severity,
        })
        assert rec.severity == severity

    @pytest.mark.parametrize("resolution", ["human_decision", "auto_merged", "unresolved"])
    def test_resolution_values(self, resolution: str):
        rec = ConflictRecord.from_dict({
            "conflict_id": "x", "step_id": "s", "agents": [],
            "resolution": resolution,
        })
        assert rec.resolution == resolution

    def test_from_dict_missing_agents_defaults_to_empty_list(self):
        rec = ConflictRecord.from_dict({"conflict_id": "x", "step_id": "s"})
        assert rec.agents == []


# ---------------------------------------------------------------------------
# TestTeamPatternSerialization
# ---------------------------------------------------------------------------

class TestTeamPatternSerialization:
    def _sample(self) -> TeamPattern:
        return TeamPattern(
            pattern_id="team-arch-sec-001",
            agents=["architect", "security-reviewer"],
            task_types=["feature", "refactor"],
            success_rate=0.9,
            sample_size=10,
            avg_token_cost=5000,
            confidence=0.6,
            created_at="2026-03-01T00:00:00Z",
            updated_at="2026-03-15T00:00:00Z",
        )

    def test_roundtrip_is_identity(self):
        p = self._sample()
        assert TeamPattern.from_dict(p.to_dict()) == p

    def test_to_dict_contains_all_fields(self):
        p = self._sample()
        d = p.to_dict()
        assert d["pattern_id"] == "team-arch-sec-001"
        assert d["agents"] == ["architect", "security-reviewer"]
        assert d["task_types"] == ["feature", "refactor"]
        assert d["success_rate"] == pytest.approx(0.9)
        assert d["sample_size"] == 10
        assert d["avg_token_cost"] == 5000
        assert d["confidence"] == pytest.approx(0.6)
        assert d["created_at"] == "2026-03-01T00:00:00Z"
        assert d["updated_at"] == "2026-03-15T00:00:00Z"

    def test_from_dict_defaults_for_optional_fields(self):
        p = TeamPattern.from_dict({"pattern_id": "t-001", "agents": ["a", "b"]})
        assert p.task_types == []
        assert p.success_rate == pytest.approx(0.0)
        assert p.sample_size == 0
        assert p.avg_token_cost == 0
        assert p.confidence == pytest.approx(0.0)
        assert p.created_at == ""
        assert p.updated_at == ""

    def test_numeric_fields_coerced_correctly(self):
        p = TeamPattern.from_dict({
            "pattern_id": "x", "agents": [],
            "success_rate": "0.75",   # string → float
            "sample_size": "8",       # string → int
            "avg_token_cost": "3000", # string → int
            "confidence": "0.5",      # string → float
        })
        assert p.success_rate == pytest.approx(0.75)
        assert p.sample_size == 8
        assert p.avg_token_cost == 3000
        assert p.confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# TestRetrospectiveTeamFields
# ---------------------------------------------------------------------------

class TestRetrospectiveTeamFields:
    def _make_team_comp(self) -> TeamCompositionRecord:
        return TeamCompositionRecord(
            step_id="step-1",
            agents=["architect", "backend-engineer"],
            roles={"architect": "lead", "backend-engineer": "implementer"},
            outcome="success",
            task_type="feature",
            token_cost=3000,
        )

    def _make_conflict(self) -> ConflictRecord:
        return ConflictRecord(
            conflict_id="c-001",
            step_id="step-2",
            agents=["backend-engineer", "security-reviewer"],
            positions={
                "backend-engineer": "Use JWT",
                "security-reviewer": "Use sessions",
            },
            severity="medium",
            resolution="human_decision",
            resolution_detail="Chose JWT per team standard",
            resolved_by="human",
        )

    # -- backward compatibility -------------------------------------------

    def test_from_dict_without_team_fields_returns_empty_lists(self):
        """Retrospectives written before team collaboration fields were added
        must deserialize cleanly with empty lists for the new fields."""
        data = {
            "task_id": "old-task",
            "task_name": "Legacy Task",
            "timestamp": "2025-01-01T00:00:00",
        }
        retro = Retrospective.from_dict(data)
        assert retro.team_compositions == []
        assert retro.conflicts == []

    def test_from_dict_with_explicit_empty_lists(self):
        data = {
            "task_id": "t1", "task_name": "T", "timestamp": "2026-01-01",
            "team_compositions": [],
            "conflicts": [],
        }
        retro = Retrospective.from_dict(data)
        assert retro.team_compositions == []
        assert retro.conflicts == []

    # -- round-trip with populated data -----------------------------------

    def test_roundtrip_with_team_compositions(self):
        comp = self._make_team_comp()
        retro = Retrospective(
            task_id="rt-1", task_name="RT", timestamp="2026-03-01",
            team_compositions=[comp],
        )
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.team_compositions) == 1
        assert restored.team_compositions[0] == comp

    def test_roundtrip_with_conflicts(self):
        conflict = self._make_conflict()
        retro = Retrospective(
            task_id="rt-2", task_name="RT", timestamp="2026-03-01",
            conflicts=[conflict],
        )
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.conflicts) == 1
        assert restored.conflicts[0] == conflict

    def test_full_roundtrip_preserves_both_fields(self):
        retro = Retrospective(
            task_id="full-rt", task_name="Full RT", timestamp="2026-03-01",
            team_compositions=[self._make_team_comp()],
            conflicts=[self._make_conflict()],
        )
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.team_compositions) == 1
        assert len(restored.conflicts) == 1
        assert restored.team_compositions[0].step_id == "step-1"
        assert restored.conflicts[0].conflict_id == "c-001"

    # -- to_markdown sections ---------------------------------------------

    def test_markdown_includes_team_compositions_section_when_populated(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            team_compositions=[self._make_team_comp()],
        )
        md = retro.to_markdown()
        assert "## Team Compositions" in md

    def test_markdown_includes_conflicts_section_when_populated(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            conflicts=[self._make_conflict()],
        )
        md = retro.to_markdown()
        assert "## Conflicts" in md

    def test_markdown_omits_team_sections_when_empty(self):
        retro = Retrospective(task_id="t1", task_name="T", timestamp="2026-03-01")
        md = retro.to_markdown()
        assert "## Team Compositions" not in md
        assert "## Conflicts" not in md

    @pytest.mark.parametrize("expected_fragment", [
        "step-1",                             # step_id
        "architect",                          # agent name in team
        "backend-engineer",                   # second agent
        "success",                            # outcome
        "architect: lead",                    # role entry
        "3,000",                              # token_cost formatted
    ])
    def test_markdown_team_composition_content(self, expected_fragment: str):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            team_compositions=[self._make_team_comp()],
        )
        assert expected_fragment in retro.to_markdown()

    @pytest.mark.parametrize("expected_fragment", [
        "step-2",                             # step_id
        "MEDIUM",                             # severity uppercased
        "human_decision",                     # resolution
        "Use JWT",                            # position content
        "Use sessions",                       # other agent's position
        "Chose JWT per team standard",        # resolution_detail
    ])
    def test_markdown_conflict_content(self, expected_fragment: str):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-01",
            conflicts=[self._make_conflict()],
        )
        assert expected_fragment in retro.to_markdown()


# ---------------------------------------------------------------------------
# TestPatternLearnerTeamPatterns
# ---------------------------------------------------------------------------

class TestPatternLearnerTeamPatterns:
    def _make_learner(
        self, tmp_context: Path, tasks: list[TaskUsageRecord]
    ) -> PatternLearner:
        log_path = tmp_context / "usage-log.jsonl"
        _write_tasks(log_path, tasks)
        return PatternLearner(team_context_root=tmp_context)

    # -- analyze_team_patterns: empty / missing log ----------------------

    def test_returns_empty_when_log_missing(self, tmp_context: Path):
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.analyze_team_patterns() == []

    def test_returns_empty_when_log_empty(self, tmp_context: Path):
        log_path = tmp_context / "usage-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.analyze_team_patterns() == []

    # -- solo agents are excluded -----------------------------------------

    def test_solo_agent_tasks_excluded_from_team_patterns(self, tmp_context: Path):
        # 10 tasks each with a single agent — no team, should produce nothing
        tasks = [
            _task(f"t{i}", outcome="SHIP", agents=[_agent("architect")])
            for i in range(10)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert patterns == []

    def test_mixed_solo_and_team_only_teams_counted(self, tmp_context: Path):
        solo_tasks = [
            _task(f"s{i}", outcome="SHIP", agents=[_agent("architect")])
            for i in range(10)
        ]
        team_tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("architect"), _agent("backend-engineer")])
            for i in range(4)
        ]
        learner = self._make_learner(tmp_context, solo_tasks + team_tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert set(patterns[0].agents) == {"architect", "backend-engineer"}

    # -- grouping and success_rate ----------------------------------------

    def test_team_pattern_extracted_above_threshold(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("architect"), _agent("security-reviewer")])
            for i in range(6)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        p = patterns[0]
        assert set(p.agents) == {"architect", "security-reviewer"}
        assert p.sample_size == 6
        assert p.success_rate == pytest.approx(1.0)

    def test_success_rate_computed_correctly(self, tmp_context: Path):
        tasks = (
            [_task(f"s{i}", outcome="SHIP",
                   agents=[_agent("a"), _agent("b")])
             for i in range(6)]
            + [_task(f"f{i}", outcome="REVISE",
                     agents=[_agent("a"), _agent("b")])
               for i in range(4)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].success_rate == pytest.approx(0.6)

    def test_agent_combo_is_canonical_sorted(self, tmp_context: Path):
        # Tasks list agents in different orders — canonical key must still group them
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("z-agent"), _agent("a-agent")])
            for i in range(4)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].agents == sorted(patterns[0].agents)

    # -- below min_sample_size ---------------------------------------------

    @pytest.mark.parametrize("n", [1, 2])
    def test_below_min_sample_returns_empty(self, tmp_context: Path, n: int):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(n)
        ]
        learner = self._make_learner(tmp_context, tasks)
        assert learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0) == []

    def test_exactly_at_min_sample_size_included(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(3)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1

    # -- confidence formula -----------------------------------------------

    def test_confidence_formula_matches_solo_pattern_formula(self, tmp_context: Path):
        # 9 tasks, all SHIP → confidence = min(1.0, (9/15)*1.0) = 0.6
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        expected = min(1.0, (9 / 15) * 1.0)
        assert patterns[0].confidence == pytest.approx(expected, abs=0.001)

    def test_confidence_capped_at_one(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(30)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].confidence <= 1.0

    def test_below_min_confidence_excluded(self, tmp_context: Path):
        # 4 tasks, 2 SHIP → success_rate=0.5, confidence=(4/15)*0.5=0.133 → excluded at 0.5
        tasks = (
            [_task(f"s{i}", outcome="SHIP",
                   agents=[_agent("a"), _agent("b")])
             for i in range(2)]
            + [_task(f"f{i}", outcome="REVISE",
                     agents=[_agent("a"), _agent("b")])
               for i in range(2)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.5)
        assert patterns == []

    # -- avg_token_cost ---------------------------------------------------

    def test_avg_token_cost_from_successful_tasks(self, tmp_context: Path):
        # 5 SHIP tasks at 1000 tokens each, 2 REVISE at 9000 each
        tasks = (
            [_task(f"s{i}", outcome="SHIP",
                   agents=[_agent("a", estimated_tokens=1000),
                            _agent("b", estimated_tokens=0)])
             for i in range(5)]
            + [_task(f"f{i}", outcome="REVISE",
                     agents=[_agent("a", estimated_tokens=9000),
                              _agent("b", estimated_tokens=0)])
               for i in range(2)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].avg_token_cost == 1000

    def test_avg_token_cost_falls_back_to_all_when_no_successes(self, tmp_context: Path):
        tasks = [
            _task(f"f{i}", outcome="REVISE",
                  agents=[_agent("a", estimated_tokens=2000),
                           _agent("b", estimated_tokens=0)])
            for i in range(4)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].avg_token_cost == 2000

    # -- task_types collected ---------------------------------------------

    def test_task_types_collected_from_group(self, tmp_context: Path):
        tasks = (
            [_task(f"a{i}", sequencing_mode="feature", outcome="SHIP",
                   agents=[_agent("a"), _agent("b")])
             for i in range(3)]
            + [_task(f"b{i}", sequencing_mode="bugfix", outcome="SHIP",
                     agents=[_agent("a"), _agent("b")])
               for i in range(3)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) == 1
        assert "feature" in patterns[0].task_types
        assert "bugfix" in patterns[0].task_types

    # -- sorted by confidence descending ----------------------------------

    def test_patterns_sorted_by_confidence_descending(self, tmp_context: Path):
        # team a+b: 6 SHIP → high confidence
        # team c+d: 6 tasks, 3 SHIP → lower confidence
        tasks = (
            [_task(f"ab{i}", outcome="SHIP",
                   agents=[_agent("a"), _agent("b")])
             for i in range(6)]
            + [_task(f"cds{i}", outcome="SHIP",
                     agents=[_agent("c"), _agent("d")])
               for i in range(3)]
            + [_task(f"cdf{i}", outcome="REVISE",
                     agents=[_agent("c"), _agent("d")])
               for i in range(3)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        assert len(patterns) >= 2
        confidences = [p.confidence for p in patterns]
        assert confidences == sorted(confidences, reverse=True)

    # -- different team combos produce separate patterns ------------------

    def test_distinct_teams_produce_separate_patterns(self, tmp_context: Path):
        tasks = (
            [_task(f"ab{i}", outcome="SHIP",
                   agents=[_agent("a"), _agent("b")])
             for i in range(4)]
            + [_task(f"cd{i}", outcome="SHIP",
                     agents=[_agent("c"), _agent("d")])
               for i in range(4)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze_team_patterns(min_sample_size=3, min_confidence=0.0)
        agent_sets = [frozenset(p.agents) for p in patterns]
        assert frozenset({"a", "b"}) in agent_sets
        assert frozenset({"c", "d"}) in agent_sets

    # -- refresh_team_patterns --------------------------------------------

    def test_refresh_team_patterns_writes_json_file(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.5)
        assert (tmp_context / "team-patterns.json").exists()

    def test_refresh_team_patterns_returns_patterns(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.5)
        assert len(patterns) == 1

    def test_refresh_team_patterns_writes_valid_json(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.0)
        raw = json.loads((tmp_context / "team-patterns.json").read_text())
        assert isinstance(raw, list)
        assert len(raw) == 1
        assert "pattern_id" in raw[0]

    def test_refresh_empty_result_writes_empty_array(self, tmp_context: Path):
        # No tasks → empty patterns file
        log_path = tmp_context / "usage-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        learner.refresh_team_patterns()
        raw = json.loads((tmp_context / "team-patterns.json").read_text())
        assert raw == []

    # -- load_team_patterns -----------------------------------------------

    def test_load_team_patterns_reads_back_written_data(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a"), _agent("b")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        written = learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.0)
        loaded = learner.load_team_patterns()
        assert len(loaded) == len(written)
        assert loaded[0].pattern_id == written[0].pattern_id
        assert loaded[0].agents == written[0].agents

    def test_load_team_patterns_returns_empty_when_file_missing(self, tmp_context: Path):
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_team_patterns() == []

    def test_load_team_patterns_returns_empty_for_invalid_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "team-patterns.json").write_text("NOT_JSON", encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_team_patterns() == []

    def test_load_team_patterns_returns_empty_for_non_list_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "team-patterns.json").write_text(
            '{"pattern_id": "x"}', encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_team_patterns() == []

    def test_load_team_patterns_skips_non_dict_items(self, tmp_context: Path):
        # TeamPattern.from_dict has all-optional fields (no required keys raise),
        # so the only items skipped are those that fail the isinstance(item, dict)
        # guard (e.g. bare strings or numbers in the JSON array).
        good = TeamPattern(
            pattern_id="team-ok-001", agents=["a", "b"],
            success_rate=0.9, sample_size=5, confidence=0.6,
        )
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "team-patterns.json").write_text(
            json.dumps([good.to_dict(), "not-a-dict", 42]),
            encoding="utf-8",
        )
        learner = PatternLearner(team_context_root=tmp_context)
        loaded = learner.load_team_patterns()
        # Only the dict item survives; string and int are skipped by isinstance guard
        assert len(loaded) == 1
        assert loaded[0].pattern_id == "team-ok-001"

    # -- get_team_cost_estimate -------------------------------------------

    def test_get_team_cost_estimate_returns_cost_for_matching_team(
        self, tmp_context: Path
    ):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a", estimated_tokens=2000),
                           _agent("b", estimated_tokens=1000)])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.0)
        cost = learner.get_team_cost_estimate(["a", "b"])
        assert cost == 3000

    def test_get_team_cost_estimate_order_invariant(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("alpha"), _agent("beta")])
            for i in range(9)
        ]
        learner = self._make_learner(tmp_context, tasks)
        learner.refresh_team_patterns(min_sample_size=3, min_confidence=0.0)
        cost_ab = learner.get_team_cost_estimate(["alpha", "beta"])
        cost_ba = learner.get_team_cost_estimate(["beta", "alpha"])
        assert cost_ab is not None
        assert cost_ab == cost_ba

    def test_get_team_cost_estimate_returns_none_for_unknown_team(
        self, tmp_context: Path
    ):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "team-patterns.json").write_text("[]", encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.get_team_cost_estimate(["a", "b"]) is None

    def test_get_team_cost_estimate_returns_none_when_file_missing(
        self, tmp_context: Path
    ):
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.get_team_cost_estimate(["a", "b"]) is None

    def test_get_team_cost_estimate_partial_match_returns_none(
        self, tmp_context: Path
    ):
        # Only pattern is for ["a", "b"] — asking for ["a", "c"] must return None
        pattern = TeamPattern(
            pattern_id="t-001", agents=["a", "b"],
            avg_token_cost=5000, confidence=0.8, sample_size=5,
        )
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "team-patterns.json").write_text(
            json.dumps([pattern.to_dict()]), encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.get_team_cost_estimate(["a", "c"]) is None


# ---------------------------------------------------------------------------
# TestRetrospectiveEngineTeamFields
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineTeamFields:
    def _usage(self, task_id: str = "task-1") -> TaskUsageRecord:
        return TaskUsageRecord(
            task_id=task_id,
            timestamp="2026-03-01T10:00:00",
            agents_used=[_agent("arch"), _agent("be")],
            total_agents=2,
            risk_level="LOW",
            sequencing_mode="phased_delivery",
            gates_passed=2,
            gates_failed=0,
            outcome="SHIP",
            notes="",
        )

    def test_team_compositions_passed_through_to_retrospective(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        comp = TeamCompositionRecord(
            step_id="s1", agents=["arch", "be"],
            outcome="success", task_type="feature", token_cost=2000,
        )
        retro = engine.generate_from_usage(
            self._usage(), team_compositions=[comp]
        )
        assert len(retro.team_compositions) == 1
        assert retro.team_compositions[0].step_id == "s1"
        assert retro.team_compositions[0].token_cost == 2000

    def test_conflicts_passed_through_to_retrospective(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        conflict = ConflictRecord(
            conflict_id="c-001",
            step_id="s2",
            agents=["arch", "security-reviewer"],
            severity="high",
            resolution="human_decision",
            resolved_by="human",
        )
        retro = engine.generate_from_usage(
            self._usage(), conflicts=[conflict]
        )
        assert len(retro.conflicts) == 1
        assert retro.conflicts[0].conflict_id == "c-001"
        assert retro.conflicts[0].severity == "high"

    def test_both_fields_passed_through_together(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        comp = TeamCompositionRecord(step_id="s1", agents=["a", "b"])
        conflict = ConflictRecord(
            conflict_id="c-001", step_id="s1", agents=["a", "b"]
        )
        retro = engine.generate_from_usage(
            self._usage(),
            team_compositions=[comp],
            conflicts=[conflict],
        )
        assert len(retro.team_compositions) == 1
        assert len(retro.conflicts) == 1

    def test_defaults_to_empty_lists_when_not_provided(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = engine.generate_from_usage(self._usage())
        assert retro.team_compositions == []
        assert retro.conflicts == []

    def test_none_params_default_to_empty_lists(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = engine.generate_from_usage(
            self._usage(),
            team_compositions=None,
            conflicts=None,
        )
        assert retro.team_compositions == []
        assert retro.conflicts == []

    def test_save_and_reload_preserves_team_fields(self, tmp_path: Path):
        """Round-trip through RetrospectiveEngine.save() → JSON sidecar → from_dict()."""
        engine = RetrospectiveEngine(tmp_path / "retros")
        comp = TeamCompositionRecord(
            step_id="s1", agents=["arch", "be"],
            roles={"arch": "lead"}, outcome="success", token_cost=1500,
        )
        conflict = ConflictRecord(
            conflict_id="c-01", step_id="s1", agents=["arch", "reviewer"],
            severity="low", resolution="auto_merged",
        )
        retro = engine.generate_from_usage(
            self._usage("save-test"),
            team_compositions=[comp],
            conflicts=[conflict],
        )
        engine.save(retro)

        json_path = tmp_path / "retros" / "save-test.json"
        assert json_path.exists()
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        restored = Retrospective.from_dict(raw)

        assert len(restored.team_compositions) == 1
        assert restored.team_compositions[0].step_id == "s1"
        assert restored.team_compositions[0].token_cost == 1500
        assert len(restored.conflicts) == 1
        assert restored.conflicts[0].conflict_id == "c-01"

    def test_multiple_compositions_and_conflicts_all_preserved(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        comps = [
            TeamCompositionRecord(step_id=f"s{i}", agents=["a", "b"])
            for i in range(3)
        ]
        conflicts = [
            ConflictRecord(conflict_id=f"c-{i}", step_id=f"s{i}", agents=["a", "b"])
            for i in range(2)
        ]
        retro = engine.generate_from_usage(
            self._usage(),
            team_compositions=comps,
            conflicts=conflicts,
        )
        assert len(retro.team_compositions) == 3
        assert len(retro.conflicts) == 2
