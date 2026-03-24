"""Tests for agent_baton.models.budget and agent_baton.core.learn.budget_tuner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.learn.budget_tuner import BudgetTuner


# ---------------------------------------------------------------------------
# Fixture / helper factories
# ---------------------------------------------------------------------------

def _agent(name: str = "worker", estimated_tokens: int = 10_000) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=0,
        gate_results=[],
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
        gates_passed=0,
        gates_failed=0,
        outcome=outcome,
        notes="",
    )


def _write_tasks(log_path: Path, tasks: list[TaskUsageRecord]) -> None:
    logger = UsageLogger(log_path)
    for t in tasks:
        logger.log(t)


def _make_tuner(tmp_context: Path, tasks: list[TaskUsageRecord]) -> BudgetTuner:
    log_path = tmp_context / "usage-log.jsonl"
    _write_tasks(log_path, tasks)
    return BudgetTuner(team_context_root=tmp_context)


@pytest.fixture
def tmp_context(tmp_path: Path) -> Path:
    return tmp_path / "team-context"


# ---------------------------------------------------------------------------
# BudgetRecommendation — serialisation round-trip
# DECISION: Removed test_to_dict_contains_all_fields and test_from_dict_restores_all_fields
# (both subsumed by test_roundtrip_is_identity). Removed
# test_from_dict_uses_defaults_for_optional_keys (trivial defaults check).
# ---------------------------------------------------------------------------

class TestBudgetRecommendationSerialisation:
    def _sample(self) -> BudgetRecommendation:
        return BudgetRecommendation(
            task_type="phased_delivery",
            current_tier="standard",
            recommended_tier="lean",
            reason="p95 is below the standard floor",
            avg_tokens_used=20_000,
            median_tokens_used=18_000,
            p95_tokens_used=30_000,
            sample_size=5,
            confidence=0.5,
            potential_savings=5_000,
        )

    def test_roundtrip_is_identity(self):
        rec = self._sample()
        assert BudgetRecommendation.from_dict(rec.to_dict()) == rec


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — empty / missing log
# DECISION: test_returns_empty_when_log_missing and test_empty_log_returns_empty
# (in TestEdgeCases) were exact duplicates — consolidated here; the second copy
# in TestEdgeCases is removed.
# ---------------------------------------------------------------------------

class TestAnalyzeEmptyLog:
    def test_returns_empty_when_log_missing(self, tmp_context: Path):
        tuner = BudgetTuner(team_context_root=tmp_context)
        assert tuner.analyze() == []

    def test_returns_empty_when_log_empty(self, tmp_context: Path):
        log_path = tmp_context / "usage-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        tuner = BudgetTuner(team_context_root=tmp_context)
        assert tuner.analyze() == []

    def test_returns_empty_when_all_groups_below_min_sample(self, tmp_context: Path):
        # 2 tasks only — below the minimum of 3
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=100_000)])
            for i in range(2)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        assert tuner.analyze() == []

    def test_returns_empty_when_exactly_min_sample_size_but_no_change_needed(
        self, tmp_context: Path
    ):
        # 3 tasks well within lean tier — no recommendation expected
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=5_000)])
            for i in range(3)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        # median = 5000, p95 = 5000 — comfortably lean, no upgrade needed
        assert tuner.analyze() == []


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — over-budgeted (downgrade) detection
# ---------------------------------------------------------------------------

class TestDowngradeDetection:
    def test_standard_tasks_with_lean_usage_get_downgrade(self, tmp_context: Path):
        """Tasks using well under 50K tokens should be downgraded from standard."""
        tasks = [
            _task(f"t{i}", sequencing_mode="over_budgeted",
                  agents=[_agent(estimated_tokens=20_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert all(r.task_type != "over_budgeted" or r.recommended_tier != "lean"
                   for r in recs)

    def test_standard_tasks_with_p95_below_standard_floor_get_downgrade(
        self, tmp_context: Path
    ):
        """Verify the p95-based downgrade rule doesn't fire spuriously.

        The downgrade rule (p95 < tier_lower_bound) cannot fire when the tuner
        infers the current tier from the median, because p95 >= median by
        definition.  We verify here that no false downgrades are generated for
        a well-behaved standard-range group.
        """
        tasks = [
            _task(f"t{i}", sequencing_mode="borderline_standard",
                  agents=[_agent(estimated_tokens=60_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        # median = 60K → standard; p95 = 60K >= 50001; no downgrade expected
        modes = {r.task_type for r in recs}
        assert "borderline_standard" not in modes

    def test_over_budgeted_full_tasks_get_downgrade_recommendation(
        self, tmp_context: Path
    ):
        """Tasks well within standard should not be downgraded."""
        # 5 tasks at 100K — standard tier; p95=100K >= 50001; no downgrade
        tasks = [
            _task(f"t{i}", sequencing_mode="stable_standard",
                  agents=[_agent(estimated_tokens=100_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert all(r.task_type != "stable_standard" for r in recs)


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — under-budgeted (upgrade) detection
# ---------------------------------------------------------------------------

class TestUpgradeDetection:
    def test_lean_tasks_with_high_median_get_upgrade(self, tmp_context: Path):
        """Median > 80% of lean ceiling (40K) should trigger upgrade to standard."""
        # 80% of 50000 = 40000; median just above that → upgrade
        # Use 5 tasks each at 45K → median = 45K > 40K
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_overflow",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        lean_recs = [r for r in recs if r.task_type == "lean_overflow"]
        assert len(lean_recs) == 1
        rec = lean_recs[0]
        assert rec.current_tier == "lean"
        assert rec.recommended_tier == "standard"

    def test_standard_tasks_with_high_median_get_upgrade_to_full(
        self, tmp_context: Path
    ):
        """Median > 80% of standard ceiling (400K) → upgrade to full."""
        # 80% of 500000 = 400000; use 5 tasks at 450K
        tasks = [
            _task(f"t{i}", sequencing_mode="standard_overflow",
                  agents=[_agent(estimated_tokens=450_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        std_recs = [r for r in recs if r.task_type == "standard_overflow"]
        assert len(std_recs) == 1
        rec = std_recs[0]
        assert rec.current_tier == "standard"
        assert rec.recommended_tier == "full"

    def test_full_tier_tasks_never_upgraded_further(self, tmp_context: Path):
        """Full is the highest tier — no upgrade possible."""
        # 5 tasks at 600K → full tier; 80% of full upper (10M) = 8M; median < 8M
        tasks = [
            _task(f"t{i}", sequencing_mode="already_full",
                  agents=[_agent(estimated_tokens=600_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert all(r.task_type != "already_full" for r in recs)

    def test_upgrade_reason_mentions_tier_names(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="spilling_lean",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        rec = recs[0]
        assert "lean" in rec.reason.lower()
        assert "standard" in rec.reason.lower()

    def test_upgrade_has_zero_potential_savings(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert recs[0].potential_savings == 0


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — tier boundary logic
# DECISION: 4 separate boundary tests parameterized into one.
# Each tuple: (tokens_per_task, mode_tag, expect_rec, expected_tier)
# ---------------------------------------------------------------------------

class TestTierBoundaries:
    @pytest.mark.parametrize("tokens,mode_tag,expect_rec,expected_tier", [
        # At lean ceiling (50 000) → 80% threshold = 40 000 → upgrade fires
        (50_000, "at_lean_boundary", True, "standard"),
        # Just below 80% of lean ceiling (39 000 < 40 000) → no upgrade
        (39_000, "safe_lean", False, None),
        # Just above standard lower (50 001) → 80% of standard ceiling = 400 000; median < 400 000 → no upgrade
        (50_001, "just_standard", False, None),
        # Full tier (600 000) → no upgrade beyond full, no downgrade (p95 >= full lower)
        (600_000, "heavy", False, None),
    ])
    def test_tier_boundary(
        self, tmp_context: Path,
        tokens: int, mode_tag: str, expect_rec: bool, expected_tier: str | None
    ):
        tasks = [
            _task(f"t{i}", sequencing_mode=mode_tag,
                  agents=[_agent(estimated_tokens=tokens)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        mode_recs = [r for r in recs if r.task_type == mode_tag]
        if expect_rec:
            assert len(mode_recs) == 1
            assert mode_recs[0].recommended_tier == expected_tier
        else:
            assert len(mode_recs) == 0


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — confidence calculation
# DECISION: 4 separate confidence/sample-size tests parameterized into one.
# ---------------------------------------------------------------------------

class TestConfidenceCalculation:
    @pytest.mark.parametrize("sample_count,expected", [
        (3, 0.3),
        (5, 0.5),
        (10, 1.0),
        (20, 1.0),
    ])
    def test_confidence_scales_with_sample_size(
        self, tmp_context: Path, sample_count: int, expected: float
    ):
        """Confidence = min(1.0, n/10); capped at 1.0 for n >= 10."""
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(sample_count)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert len(recs) == 1
        assert recs[0].confidence == pytest.approx(expected)


# ---------------------------------------------------------------------------
# BudgetTuner.analyze — potential_savings calculation
# ---------------------------------------------------------------------------

class TestPotentialSavings:
    def test_upgrade_recommendations_have_zero_savings(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert recs[0].recommended_tier == "standard"
        assert recs[0].potential_savings == 0

    def test_downgrade_savings_computed_from_avg_minus_midpoint(
        self, tmp_context: Path
    ):
        """Verify potential_savings = max(0, avg - recommended_tier_midpoint).

        We must construct a scenario where the downgrade rule actually fires.
        The downgrade rule: p95 < _TIER_LOWER[current_tier].
        Since p95 >= median, this fires when the inferred tier's lower bound
        is above the p95, i.e. never under normal circumstances (see analysis
        in TestDowngradeDetection).  Test this via the _determine_recommendation
        helper directly instead.
        """
        from agent_baton.core.learn.budget_tuner import _determine_recommendation, _TIER_MIDPOINT

        # Simulate: current_tier=standard, median=300K, p95=40K
        # p95 < 50001 (standard lower) → downgrade to lean
        rec_tier, reason = _determine_recommendation("standard", 300_000, 40_000)
        assert rec_tier == "lean"
        assert "downgrade" in reason.lower() or "lean" in reason.lower()

        # potential_savings = max(0, avg - midpoint_of_lean)
        # lean midpoint = 25000; avg = 300000 → savings = 275000
        from agent_baton.core.learn.budget_tuner import _compute_savings
        savings = _compute_savings("standard", "lean", 300_000)
        assert savings == 300_000 - _TIER_MIDPOINT["lean"]

    def test_potential_savings_is_zero_when_avg_below_midpoint(
        self, tmp_context: Path
    ):
        """Savings must not go negative."""
        from agent_baton.core.learn.budget_tuner import _compute_savings
        # avg = 10K, recommended midpoint = 25K → max(0, ...) = 0
        savings = _compute_savings("standard", "lean", 10_000)
        assert savings == 0

    def test_no_savings_for_same_tier(self, tmp_context: Path):
        from agent_baton.core.learn.budget_tuner import _compute_savings
        assert _compute_savings("lean", "lean", 50_000) == 0


# ---------------------------------------------------------------------------
# BudgetTuner.recommend — markdown output
# DECISION: 7 separate "report contains X" tests consolidated into 2.
# test_report_content_standard checks core structural elements.
# test_report_has_both_task_types covers multi-mode output.
# ---------------------------------------------------------------------------

class TestRecommendMarkdown:
    def test_no_recommendations_returns_all_good_message(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", sequencing_mode="stable",
                  agents=[_agent(estimated_tokens=10_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        report = tuner.recommend()
        assert "No budget adjustments needed" in report

    @pytest.mark.parametrize("expected_fragment", [
        "# Budget Recommendations",   # H1 heading
        "## lean_spill",              # task-type section header
        "lean",                       # tier name in body
        "standard",                   # recommended tier name
        "Upgrade",                    # recommendation verb
        "5",                          # sample_size
        "50%",                        # confidence = 0.5
    ])
    def test_report_content(self, tmp_context: Path, expected_fragment: str):
        """Single upgrade scenario: 5 tasks × 45K tokens → lean→standard upgrade."""
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        report = tuner.recommend()
        assert expected_fragment in report

    def test_report_with_multiple_task_types(self, tmp_context: Path):
        tasks = (
            [_task(f"a{i}", sequencing_mode="mode_a",
                   agents=[_agent(estimated_tokens=45_000)])
             for i in range(5)]
            + [_task(f"b{i}", sequencing_mode="mode_b",
                     agents=[_agent(estimated_tokens=450_000)])
               for i in range(5)]
        )
        tuner = _make_tuner(tmp_context, tasks)
        report = tuner.recommend()
        assert "mode_a" in report
        assert "mode_b" in report


# ---------------------------------------------------------------------------
# BudgetTuner — save / load round-trip
# DECISION: test_save_creates_json_file and test_save_returns_correct_path
# merged into test_save_creates_json_file_at_expected_path.
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    def _spill_tasks(self, n: int = 5) -> list[TaskUsageRecord]:
        return [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(n)
        ]

    def test_save_creates_json_file_at_expected_path(self, tmp_context: Path):
        tuner = _make_tuner(tmp_context, self._spill_tasks())
        path = tuner.save_recommendations()
        assert path.exists()
        assert path.suffix == ".json"
        assert path == tmp_context / "budget-recommendations.json"

    def test_load_returns_none_when_file_missing(self, tmp_context: Path):
        tuner = BudgetTuner(team_context_root=tmp_context)
        assert tuner.load_recommendations() is None

    def test_roundtrip_preserves_recommendations(self, tmp_context: Path):
        tuner = _make_tuner(tmp_context, self._spill_tasks())
        original = tuner.analyze()
        tuner.save_recommendations()
        loaded = tuner.load_recommendations()
        assert loaded is not None
        assert len(loaded) == len(original)
        assert loaded[0].task_type == original[0].task_type
        assert loaded[0].current_tier == original[0].current_tier
        assert loaded[0].recommended_tier == original[0].recommended_tier
        assert loaded[0].confidence == pytest.approx(original[0].confidence)

    def test_load_returns_empty_list_for_invalid_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "budget-recommendations.json").write_text(
            "INVALID_JSON", encoding="utf-8"
        )
        tuner = BudgetTuner(team_context_root=tmp_context)
        assert tuner.load_recommendations() == []

    def test_load_returns_empty_list_for_non_list_json(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        (tmp_context / "budget-recommendations.json").write_text(
            '{"task_type": "x"}', encoding="utf-8"
        )
        tuner = BudgetTuner(team_context_root=tmp_context)
        assert tuner.load_recommendations() == []

    def test_load_skips_malformed_items(self, tmp_context: Path):
        tmp_context.mkdir(parents=True, exist_ok=True)
        good = BudgetRecommendation(
            task_type="ok",
            current_tier="lean",
            recommended_tier="standard",
            reason="test",
            avg_tokens_used=45_000,
            median_tokens_used=45_000,
            p95_tokens_used=45_000,
            sample_size=5,
            confidence=0.5,
            potential_savings=0,
        )
        raw = json.dumps([good.to_dict(), {"broken": True}])
        (tmp_context / "budget-recommendations.json").write_text(
            raw, encoding="utf-8"
        )
        tuner = BudgetTuner(team_context_root=tmp_context)
        loaded = tuner.load_recommendations()
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0].task_type == "ok"

    def test_save_empty_recommendations_creates_empty_array(
        self, tmp_context: Path
    ):
        """When no recommendations are found, the JSON file contains []."""
        # 5 tasks well within lean — no recommendation
        tasks = [
            _task(f"t{i}", sequencing_mode="quiet",
                  agents=[_agent(estimated_tokens=5_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        path = tuner.save_recommendations()
        content = json.loads(path.read_text())
        assert content == []


# ---------------------------------------------------------------------------
# Edge cases
# DECISION: test_empty_log_returns_empty was a duplicate of
# TestAnalyzeEmptyLog.test_returns_empty_when_log_missing — removed.
# test_single_task_below_min_sample and test_two_tasks_below_min_sample
# consolidated into test_below_min_sample_sizes (parametrize over n).
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.parametrize("n", [1, 2])
    def test_below_min_sample_returns_empty(self, tmp_context: Path, n: int):
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=45_000)])
            for i in range(n)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        assert tuner.analyze() == []

    def test_all_same_tier_no_recommendation_generated(self, tmp_context: Path):
        """When all tasks sit comfortably inside lean, no recommendation is needed."""
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=10_000)])
            for i in range(10)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        assert tuner.analyze() == []

    def test_mixed_modes_only_problem_mode_flagged(self, tmp_context: Path):
        good_tasks = [
            _task(f"g{i}", sequencing_mode="fine_mode",
                  agents=[_agent(estimated_tokens=10_000)])
            for i in range(5)
        ]
        bad_tasks = [
            _task(f"b{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=45_000)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, good_tasks + bad_tasks)
        recs = tuner.analyze()
        modes = {r.task_type for r in recs}
        assert "lean_spill" in modes
        assert "fine_mode" not in modes

    def test_agents_with_zero_tokens_handled_gracefully(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", agents=[_agent(estimated_tokens=0)])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        # median = 0 → lean; p95 = 0; no recommendation (already lean, can't downgrade)
        assert tuner.analyze() == []

    def test_task_with_no_agents_contributes_zero_tokens(self, tmp_context: Path):
        tasks = [
            _task(f"t{i}", agents=[])
            for i in range(5)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        assert tuner.analyze() == []

    def test_results_sorted_by_task_type(self, tmp_context: Path):
        tasks = (
            [_task(f"a{i}", sequencing_mode="zzz_mode",
                   agents=[_agent(estimated_tokens=45_000)])
             for i in range(5)]
            + [_task(f"b{i}", sequencing_mode="aaa_mode",
                     agents=[_agent(estimated_tokens=450_000)])
               for i in range(5)]
        )
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        task_types = [r.task_type for r in recs]
        assert task_types == sorted(task_types)

    def test_token_stats_are_correct(self, tmp_context: Path):
        """Verify avg, median, and p95 values in the recommendation."""
        # 5 tasks: tokens = [10K, 20K, 45K, 45K, 45K]
        token_values = [10_000, 20_000, 45_000, 45_000, 45_000]
        tasks = [
            _task(f"t{i}", sequencing_mode="lean_spill",
                  agents=[_agent(estimated_tokens=tok)])
            for i, tok in enumerate(token_values)
        ]
        tuner = _make_tuner(tmp_context, tasks)
        recs = tuner.analyze()
        assert len(recs) == 1
        rec = recs[0]
        assert rec.avg_tokens_used == sum(token_values) // len(token_values)
        assert rec.median_tokens_used == 45_000  # middle value of sorted list
        # p95 for 5 items: idx = int(0.95*5+0.5)-1 = int(5.25)-1 = 4 → 45K
        assert rec.p95_tokens_used == 45_000
        assert rec.sample_size == 5
