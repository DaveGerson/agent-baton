"""Tests for agent_baton.models.pattern and agent_baton.core.learn.pattern_learner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.pattern import LearnedPattern
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.learn.pattern_learner import PatternLearner


# ---------------------------------------------------------------------------
# Fixture / helper factories
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
        gate_results=gate_results if gate_results is not None else [],
        estimated_tokens=estimated_tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str = "task-001",
    sequencing_mode: str = "phased_delivery",
    outcome: str = "SHIP",
    agents: list[AgentUsageRecord] | None = None,
    timestamp: str = "2026-03-01T10:00:00",
    gates_passed: int = 2,
    gates_failed: int = 0,
) -> TaskUsageRecord:
    agent_list = agents if agents is not None else []
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agent_list,
        total_agents=len(agent_list),
        risk_level="LOW",
        sequencing_mode=sequencing_mode,
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome=outcome,
        notes="",
    )


def _write_tasks(log_path: Path, tasks: list[TaskUsageRecord]) -> None:
    """Write a list of tasks to a JSONL log file."""
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)


@pytest.fixture
def tmp_context(tmp_path: Path) -> Path:
    """Returns a temporary team-context directory (not yet created on disk)."""
    return tmp_path / "team-context"


# ---------------------------------------------------------------------------
# LearnedPattern — serialisation round-trip
# DECISION: Removed test_to_dict_contains_all_fields and
# test_from_dict_restores_all_fields (both subsumed by test_roundtrip_is_identity).
# Removed test_stack_none_serialises_as_null (trivial single-field check).
# Removed test_from_dict_uses_defaults_for_optional_keys (trivial defaults).
# Kept test_recommended_agents_is_a_copy because it tests mutation isolation,
# a non-trivial invariant that roundtrip doesn't cover.
# ---------------------------------------------------------------------------

class TestLearnedPatternSerialisation:
    def test_roundtrip_is_identity(self):
        p = LearnedPattern(
            pattern_id="rt-001",
            task_type="refactor",
            stack="node/express",
            recommended_template="refactor workflow with 3 agent(s)",
            recommended_agents=["architect", "be", "reviewer"],
            confidence=0.9,
            sample_size=15,
            success_rate=1.0,
            avg_token_cost=8000,
            evidence=["e1"],
            created_at="2026-03-20T00:00:00Z",
            updated_at="2026-03-20T00:00:00Z",
        )
        assert LearnedPattern.from_dict(p.to_dict()) == p

    def test_recommended_agents_is_a_copy(self):
        agents = ["a", "b"]
        p = LearnedPattern(
            pattern_id="x", task_type="x", stack=None,
            recommended_template="", recommended_agents=agents,
            confidence=0.0, sample_size=0, success_rate=0.0,
            avg_token_cost=0,
        )
        d = p.to_dict()
        d["recommended_agents"].append("c")
        # Original field must be unaffected
        assert p.recommended_agents == ["a", "b"]


# ---------------------------------------------------------------------------
# PatternLearner.analyze — basic extraction
# ---------------------------------------------------------------------------

class TestPatternLearnerAnalyze:
    def _make_learner(self, tmp_context: Path, tasks: list[TaskUsageRecord]) -> PatternLearner:
        log_path = tmp_context / "usage-log.jsonl"
        _write_tasks(log_path, tasks)
        return PatternLearner(team_context_root=tmp_context)

    def test_returns_empty_when_log_missing(self, tmp_context: Path):
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.analyze() == []

    def test_returns_empty_when_log_empty(self, tmp_context: Path):
        log_path = tmp_context / "usage-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.analyze() == []

    def test_single_mode_above_threshold(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="phased_delivery", outcome="SHIP",
                  agents=[_agent("arch"), _agent("be")])
            for i in range(10)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.5)
        assert len(patterns) == 1
        p = patterns[0]
        assert p.task_type == "phased_delivery"
        assert p.sample_size == 10
        assert p.success_rate == pytest.approx(1.0)
        assert p.confidence > 0.5

    def test_recommended_agents_match_most_common_combo(self, tmp_context: Path):
        # 7 tasks use arch+be, 3 use arch+fe — arch+be should win
        tasks = (
            [
                _task(f"t{i}", outcome="SHIP",
                      agents=[_agent("arch"), _agent("be")])
                for i in range(7)
            ]
            + [
                _task(f"u{i}", outcome="SHIP",
                      agents=[_agent("arch"), _agent("fe")])
                for i in range(3)
            ]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        # Most common combo is arch + be
        assert set(patterns[0].recommended_agents) == {"arch", "be"}

    def test_success_rate_computed_correctly(self, tmp_context: Path):
        tasks = [
            _task(f"s{i}", outcome="SHIP") for i in range(8)
        ] + [
            _task(f"f{i}", outcome="REVISE") for i in range(2)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].success_rate == pytest.approx(0.8)

    def test_avg_token_cost_from_successful_tasks(self, tmp_context: Path):
        # 6 SHIP tasks with 1000 tokens each, 2 REVISE tasks with 9000 each
        tasks = (
            [_task(f"s{i}", outcome="SHIP",
                   agents=[_agent("a", estimated_tokens=1000)])
             for i in range(6)]
            + [_task(f"f{i}", outcome="REVISE",
                     agents=[_agent("a", estimated_tokens=9000)])
               for i in range(2)]
        )
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        # avg over SHIP tasks only: 1000
        assert patterns[0].avg_token_cost == 1000

    def test_avg_token_cost_falls_back_to_all_when_no_successes(self, tmp_context: Path):
        tasks = [
            _task(f"f{i}", outcome="REVISE",
                  agents=[_agent("a", estimated_tokens=2000)])
            for i in range(6)
        ]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].avg_token_cost == 2000

    def test_evidence_contains_all_task_ids(self, tmp_context: Path):
        ids = [f"ev-{i}" for i in range(6)]
        tasks = [_task(tid, outcome="SHIP") for tid in ids]
        learner = self._make_learner(tmp_context, tasks)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert set(patterns[0].evidence) == set(ids)

    def test_multiple_modes_produces_multiple_patterns(self, tmp_context: Path):
        mode_a = [_task(f"a{i}", sequencing_mode="mode_a", outcome="SHIP")
                  for i in range(6)]
        mode_b = [_task(f"b{i}", sequencing_mode="mode_b", outcome="SHIP")
                  for i in range(6)]
        learner = self._make_learner(tmp_context, mode_a + mode_b)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        task_types = {p.task_type for p in patterns}
        assert "mode_a" in task_types
        assert "mode_b" in task_types

    def test_patterns_sorted_by_confidence_descending(self, tmp_context: Path):
        # mode_a: 6 tasks, all SHIP → high confidence
        # mode_b: 6 tasks, half SHIP → lower confidence
        mode_a = [_task(f"a{i}", sequencing_mode="mode_a", outcome="SHIP")
                  for i in range(6)]
        mode_b = (
            [_task(f"bs{i}", sequencing_mode="mode_b", outcome="SHIP")
             for i in range(3)]
            + [_task(f"bf{i}", sequencing_mode="mode_b", outcome="REVISE")
               for i in range(3)]
        )
        learner = self._make_learner(tmp_context, mode_a + mode_b)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) >= 2
        confidences = [p.confidence for p in patterns]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# min_sample_size filtering
# DECISION: 3 tests parameterized into 1 covering below/at/straddling threshold.
# ---------------------------------------------------------------------------

class TestMinSampleSize:
    @pytest.mark.parametrize("counts,mode_tag,expected_pattern_count", [
        # 4 tasks in one group — below the threshold of 5 → excluded
        ({"only": 4}, "only", 0),
        # exactly at threshold of 5 → included
        ({"only": 5}, "only", 1),
        # 8 in big_mode (passes), 3 in small_mode (fails) → 1 pattern for big_mode
        ({"big_mode": 8, "small_mode": 3}, "big_mode", 1),
    ])
    def test_min_sample_size_filter(
        self,
        tmp_context: Path,
        counts: dict[str, int],
        mode_tag: str,
        expected_pattern_count: int,
    ):
        tasks = []
        for mode, n in counts.items():
            tasks += [_task(f"{mode}-{i}", sequencing_mode=mode, outcome="SHIP")
                      for i in range(n)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        matching = [p for p in patterns if p.task_type == mode_tag]
        assert len(matching) == expected_pattern_count


# ---------------------------------------------------------------------------
# min_confidence filtering
# DECISION: 4 tests reduced to 2 — parametrize below/above threshold, keep
# confidence_formula standalone (different assertion type), merge capped_at_one
# into formula check since formula naturally tests the cap.
# ---------------------------------------------------------------------------

class TestMinConfidence:
    @pytest.mark.parametrize("ship_count,revise_count,min_conf,expect_pattern", [
        # 6 tasks, 3 SHIP, 3 REVISE → success_rate=0.5, confidence=0.2 → excluded at 0.7
        (3, 3, 0.7, False),
        # 15 tasks all SHIP → confidence = 1.0 → included at 0.7
        (15, 0, 0.7, True),
    ])
    def test_confidence_threshold_filter(
        self, tmp_context: Path,
        ship_count: int, revise_count: int, min_conf: float, expect_pattern: bool,
    ):
        tasks = (
            [_task(f"s{i}", outcome="SHIP") for i in range(ship_count)]
            + [_task(f"f{i}", outcome="REVISE") for i in range(revise_count)]
        )
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=min_conf)
        assert (len(patterns) == 1) == expect_pattern

    def test_confidence_formula(self, tmp_context: Path, tmp_path: Path):
        # 10 tasks, 8 SHIP → success_rate=0.8, confidence = (10/15)*0.8 = 0.5333
        tasks = (
            [_task(f"s{i}", outcome="SHIP") for i in range(8)]
            + [_task(f"f{i}", outcome="REVISE") for i in range(2)]
        )
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        expected_confidence = min(1.0, (10 / 15) * 0.8)
        assert patterns[0].confidence == pytest.approx(expected_confidence, abs=0.01)

    def test_confidence_capped_at_one(self, tmp_path: Path):
        # 30 tasks all SHIP → (30/15)*1.0 = 2.0, capped to 1.0
        ctx = tmp_path / "cap-context"
        tasks = [_task(f"t{i}", outcome="SHIP") for i in range(30)]
        _write_tasks(ctx / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=ctx)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# refresh() and load_patterns()
# ---------------------------------------------------------------------------

class TestRefreshAndLoad:
    def test_refresh_writes_json_file(self, tmp_context: Path):
        # 12 SHIP tasks: confidence = min(1.0, 12/15 * 1.0) = 0.8 > 0.7
        tasks = [_task(f"t{i}", outcome="SHIP") for i in range(12)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        learner.refresh()
        assert (tmp_context / "learned-patterns.json").exists()

    def test_refresh_returns_patterns(self, tmp_context: Path):
        # 12 SHIP tasks: confidence = 0.8 > default 0.7 threshold
        tasks = [_task(f"t{i}", outcome="SHIP") for i in range(12)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.refresh()
        assert len(patterns) == 1

    def test_load_patterns_reads_back_written_data(self, tmp_context: Path):
        # 12 SHIP tasks: confidence = 0.8 > default 0.7 threshold
        tasks = [_task(f"t{i}", outcome="SHIP") for i in range(12)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        written = learner.refresh()
        loaded = learner.load_patterns()
        assert len(loaded) == len(written)
        assert loaded[0].pattern_id == written[0].pattern_id
        assert loaded[0].task_type == written[0].task_type

    def test_load_patterns_returns_empty_when_file_missing(self, tmp_context: Path):
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_patterns() == []

    def test_load_patterns_returns_empty_for_invalid_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "learned-patterns.json").write_text(
            "NOT_JSON", encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_patterns() == []

    def test_load_patterns_returns_empty_for_non_list_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "learned-patterns.json").write_text(
            '{"pattern_id": "x"}', encoding="utf-8"
        )
        learner = PatternLearner(team_context_root=tmp_context)
        assert learner.load_patterns() == []

    def test_load_patterns_skips_malformed_items(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        good = LearnedPattern(
            pattern_id="ok-001", task_type="ok", stack=None,
            recommended_template="t", recommended_agents=[],
            confidence=0.9, sample_size=10, success_rate=1.0,
            avg_token_cost=0,
        )
        # Write one good and one bad item
        raw = json.dumps([good.to_dict(), {"broken": True}])
        (tmp_context / "learned-patterns.json").write_text(raw, encoding="utf-8")
        learner = PatternLearner(team_context_root=tmp_context)
        loaded = learner.load_patterns()
        # "broken" item lacks required "pattern_id" and "task_type" keys —
        # from_dict raises KeyError so it is silently skipped.
        assert len(loaded) == 1
        assert loaded[0].pattern_id == "ok-001"

    def test_refresh_overwrites_previous_patterns(self, tmp_context: Path):
        # First refresh: 12 SHIP tasks → confidence = 0.8 > 0.7
        tasks_v1 = [_task(f"t{i}", outcome="SHIP") for i in range(12)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks_v1)
        learner = PatternLearner(team_context_root=tmp_context)
        learner.refresh()
        first_load = learner.load_patterns()

        # Add 12 more tasks and refresh again → 24 total
        log_path = tmp_context / "usage-log.jsonl"
        logger = UsageLogger(log_path)
        for i in range(12, 24):
            logger.log(_task(f"t{i}", outcome="SHIP"))
        second_patterns = learner.refresh()
        second_load = learner.load_patterns()

        # sample_size should have grown
        assert second_load[0].sample_size > first_load[0].sample_size
        assert len(second_patterns) == len(second_load)


# ---------------------------------------------------------------------------
# get_patterns_for_task()
# ---------------------------------------------------------------------------

class TestGetPatternsForTask:
    def _learner_with_patterns(self, tmp_context: Path) -> PatternLearner:
        patterns = [
            LearnedPattern(
                pattern_id="phased-001",
                task_type="phased_delivery",
                stack=None,
                recommended_template="t",
                recommended_agents=["arch"],
                confidence=0.9, sample_size=15, success_rate=1.0,
                avg_token_cost=0,
            ),
            LearnedPattern(
                pattern_id="phased-002",
                task_type="phased_delivery",
                stack="python/fastapi",
                recommended_template="t",
                recommended_agents=["arch", "be"],
                confidence=0.8, sample_size=12, success_rate=0.9,
                avg_token_cost=0,
            ),
            LearnedPattern(
                pattern_id="bugfix-001",
                task_type="bug_fix",
                stack=None,
                recommended_template="t",
                recommended_agents=["be"],
                confidence=0.75, sample_size=10, success_rate=0.8,
                avg_token_cost=0,
            ),
        ]
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "learned-patterns.json").write_text(
            json.dumps([p.to_dict() for p in patterns]),
            encoding="utf-8",
        )
        return PatternLearner(team_context_root=tmp_context)

    def test_returns_all_patterns_for_task_type(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        results = learner.get_patterns_for_task("phased_delivery")
        assert len(results) == 2
        assert all(p.task_type == "phased_delivery" for p in results)

    def test_returns_empty_for_unknown_task_type(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        assert learner.get_patterns_for_task("nonexistent") == []

    def test_stack_filter_includes_none_stack_patterns(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        # stack="python/fastapi" → both stack=None and stack="python/fastapi" match
        results = learner.get_patterns_for_task("phased_delivery", stack="python/fastapi")
        assert len(results) == 2

    def test_stack_filter_excludes_different_stack(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        # stack="node/express" → only the stack=None pattern matches
        results = learner.get_patterns_for_task("phased_delivery", stack="node/express")
        assert len(results) == 1
        assert results[0].stack is None

    def test_no_stack_filter_returns_all_matching_task_type(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        results = learner.get_patterns_for_task("phased_delivery", stack=None)
        assert len(results) == 2

    def test_results_sorted_by_confidence_descending(self, tmp_context: Path):
        learner = self._learner_with_patterns(tmp_context)
        results = learner.get_patterns_for_task("phased_delivery")
        confidences = [p.confidence for p in results]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# generate_report()
# DECISION: 6 "report contains X" tests parameterized into 1 (test_report_content).
# test_empty_report and test_report_has_entry_per_pattern kept standalone
# (different setup).
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_empty_report_when_no_patterns(self, tmp_context: Path):
        learner = PatternLearner(team_context_root=tmp_context)
        report = learner.generate_report()
        assert "No patterns found" in report
        assert "baton patterns --refresh" in report

    @pytest.mark.parametrize("tasks_count,min_conf,expected_fragment", [
        # 12 SHIP → confidence=0.8; pattern_id "phased_delivery" present
        (12, None,  "phased_delivery"),
        # 15 SHIP → confidence=1.0 → "100%"
        (15, None,  "100%"),
        # 8 SHIP, min_confidence=0.0 → sample size "8" present
        (8,  0.0,   "8"),
        # 8 SHIP, min_confidence=0.0 → report starts with H1
        (8,  0.0,   "# Learned Patterns"),
    ])
    def test_report_content(
        self, tmp_context: Path,
        tasks_count: int, min_conf: float | None, expected_fragment: str,
    ):
        tasks = [_task(f"t{i}", outcome="SHIP") for i in range(tasks_count)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        if min_conf is not None:
            learner.refresh(min_confidence=min_conf)
        else:
            learner.refresh()
        report = learner.generate_report()
        assert expected_fragment in report

    def test_report_has_entry_per_pattern(self, tmp_context: Path):
        # 12 SHIP tasks per mode → confidence = 0.8 > default 0.7
        mode_a = [_task(f"a{i}", sequencing_mode="mode_a", outcome="SHIP")
                  for i in range(12)]
        mode_b = [_task(f"b{i}", sequencing_mode="mode_b", outcome="SHIP")
                  for i in range(12)]
        _write_tasks(tmp_context / "usage-log.jsonl", mode_a + mode_b)
        learner = PatternLearner(team_context_root=tmp_context)
        learner.refresh()
        report = learner.generate_report()
        assert "mode_a" in report
        assert "mode_b" in report


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_all_failures_produces_no_patterns_at_default_confidence(
        self, tmp_context: Path
    ):
        # 10 tasks, all REVISE → success_rate=0, confidence=0 → excluded
        tasks = [_task(f"f{i}", outcome="REVISE") for i in range(10)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.7)
        assert patterns == []

    def test_all_failures_with_min_confidence_zero(self, tmp_context: Path):
        tasks = [_task(f"f{i}", outcome="REVISE") for i in range(10)]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].success_rate == pytest.approx(0.0)
        assert patterns[0].confidence == pytest.approx(0.0)

    def test_single_task_type_only(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="solo_mode", outcome="SHIP")
            for i in range(7)
        ]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].task_type == "solo_mode"

    def test_empty_agent_list_handled_gracefully(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP", agents=[])
            for i in range(6)
        ]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert len(patterns) == 1
        assert patterns[0].recommended_agents == []

    def test_retry_stats_included_in_template_description(self, tmp_context: Path):
        # 6 tasks with retries=0 → should say "low retry rate"
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a", retries=0)])
            for i in range(6)
        ]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert "low retry rate" in patterns[0].recommended_template

    def test_gate_pass_rate_included_in_template_description(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", outcome="SHIP",
                  agents=[_agent("a", gate_results=["PASS", "PASS"])])
            for i in range(6)
        ]
        _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        assert "100%" in patterns[0].recommended_template

    def test_pattern_id_is_unique_across_modes(self, tmp_context: Path):
        for letter in "abc":
            tasks = [
                _task(f"{letter}{i}", sequencing_mode=f"mode_{letter}",
                      outcome="SHIP")
                for i in range(6)
            ]
            _write_tasks(tmp_context / "usage-log.jsonl", tasks)
        learner = PatternLearner(team_context_root=tmp_context)
        patterns = learner.analyze(min_sample_size=5, min_confidence=0.0)
        ids = [p.pattern_id for p in patterns]
        assert len(ids) == len(set(ids)), "Duplicate pattern_ids found"
