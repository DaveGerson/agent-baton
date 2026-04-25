"""Tests for WS1: Central Store to Learning Materialization.

Covers:
- LearnedPattern.source field — serialisation round-trip
- BudgetRecommendation.source field — serialisation round-trip
- PatternLearner.merge_cross_project_signals()
- BudgetTuner.merge_cross_project_cost_signals()
- ImprovementLoop._apply_central_signals() — success and failure paths
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.pattern import LearnedPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _local_pattern(pattern_id: str = "local-001", task_type: str = "phased_delivery") -> LearnedPattern:
    return LearnedPattern(
        pattern_id=pattern_id,
        task_type=task_type,
        stack=None,
        recommended_template="phased_delivery workflow with 2 agent(s); low retry rate",
        recommended_agents=["architect", "backend-engineer"],
        confidence=0.85,
        sample_size=10,
        success_rate=0.9,
        avg_token_cost=50_000,
        evidence=["t1", "t2"],
        created_at="2026-04-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
        source=None,
    )


def _local_budget_rec(task_type: str = "phased_delivery") -> BudgetRecommendation:
    return BudgetRecommendation(
        task_type=task_type,
        current_tier="lean",
        recommended_tier="standard",
        reason="Median usage exceeds lean ceiling.",
        avg_tokens_used=60_000,
        median_tokens_used=55_000,
        p95_tokens_used=80_000,
        sample_size=5,
        confidence=0.5,
        potential_savings=0,
        source=None,
    )


# ---------------------------------------------------------------------------
# LearnedPattern — source field serialisation
# ---------------------------------------------------------------------------

class TestLearnedPatternSourceField:
    def test_source_none_omitted_from_dict(self):
        p = _local_pattern()
        d = p.to_dict()
        assert "source" not in d

    def test_source_central_included_in_dict(self):
        p = _local_pattern()
        p.source = "central"
        d = p.to_dict()
        assert d["source"] == "central"

    def test_roundtrip_preserves_source_none(self):
        p = _local_pattern()
        assert LearnedPattern.from_dict(p.to_dict()).source is None

    def test_roundtrip_preserves_source_central(self):
        p = _local_pattern()
        p.source = "central"
        restored = LearnedPattern.from_dict(p.to_dict())
        assert restored.source == "central"

    def test_from_dict_missing_source_defaults_none(self):
        d = _local_pattern().to_dict()
        d.pop("source", None)
        assert LearnedPattern.from_dict(d).source is None


# ---------------------------------------------------------------------------
# BudgetRecommendation — source field serialisation
# ---------------------------------------------------------------------------

class TestBudgetRecommendationSourceField:
    def test_source_none_omitted_from_dict(self):
        r = _local_budget_rec()
        d = r.to_dict()
        assert "source" not in d

    def test_source_central_included_in_dict(self):
        r = _local_budget_rec()
        r.source = "central"
        d = r.to_dict()
        assert d["source"] == "central"

    def test_roundtrip_preserves_source_none(self):
        r = _local_budget_rec()
        assert BudgetRecommendation.from_dict(r.to_dict()).source is None

    def test_roundtrip_preserves_source_central(self):
        r = _local_budget_rec()
        r.source = "central"
        restored = BudgetRecommendation.from_dict(r.to_dict())
        assert restored.source == "central"

    def test_from_dict_missing_source_defaults_none(self):
        d = _local_budget_rec().to_dict()
        d.pop("source", None)
        assert BudgetRecommendation.from_dict(d).source is None


# ---------------------------------------------------------------------------
# PatternLearner.merge_cross_project_signals
# ---------------------------------------------------------------------------

@pytest.fixture
def learner_root(tmp_path: Path) -> Path:
    root = tmp_path / "team-context"
    root.mkdir()
    return root


class TestPatternLearnerMergeCrossProjectSignals:
    def test_empty_signals_writes_local_patterns_unchanged(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        # Seed a local pattern directly on disk.
        local = _local_pattern()
        learner._write_patterns([local])

        merged = learner.merge_cross_project_signals([])

        assert len(merged) == 1
        assert merged[0].pattern_id == "local-001"
        assert merged[0].source is None

    def test_central_patterns_appended_with_source_tag(self, learner_root: Path):
        learner = PatternLearner(learner_root)

        reliability_rows = [
            {
                "agent_name": "backend-engineer",
                "success_rate": 0.9,
                "total_steps": 20,
                "avg_tokens": 40_000,
            }
        ]
        merged = learner.merge_cross_project_signals(reliability_rows)

        assert len(merged) == 1
        central_pat = merged[0]
        assert central_pat.source == "central"
        assert central_pat.recommended_agents == ["backend-engineer"]
        assert central_pat.success_rate == 0.9
        assert central_pat.avg_token_cost == 40_000

    def test_central_patterns_persisted_to_disk(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        reliability_rows = [
            {"agent_name": "architect", "success_rate": 0.85, "total_steps": 10, "avg_tokens": 0}
        ]
        learner.merge_cross_project_signals(reliability_rows)

        on_disk = learner.load_patterns()
        assert len(on_disk) == 1
        assert on_disk[0].source == "central"

    def test_local_and_central_coexist(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        local = _local_pattern()
        learner._write_patterns([local])

        reliability_rows = [
            {"agent_name": "test-engineer", "success_rate": 0.8, "total_steps": 15, "avg_tokens": 20_000}
        ]
        merged = learner.merge_cross_project_signals(reliability_rows)

        assert len(merged) == 2
        sources = {p.source for p in merged}
        assert sources == {None, "central"}

    def test_stale_central_entries_replaced_on_rerun(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        # First merge — inserts one central pattern.
        learner.merge_cross_project_signals(
            [{"agent_name": "architect", "success_rate": 0.8, "total_steps": 10, "avg_tokens": 0}]
        )
        # Second merge — different row.
        learner.merge_cross_project_signals(
            [{"agent_name": "backend-engineer", "success_rate": 0.9, "total_steps": 20, "avg_tokens": 0}]
        )

        on_disk = learner.load_patterns()
        central_patterns = [p for p in on_disk if p.source == "central"]
        # Stale "architect" entry should be gone; only the new one should remain.
        assert len(central_patterns) == 1
        assert central_patterns[0].recommended_agents == ["backend-engineer"]

    def test_rows_below_confidence_threshold_excluded(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        # success_rate < 0.7 must be filtered out.
        reliability_rows = [
            {"agent_name": "flaky-agent", "success_rate": 0.5, "total_steps": 10, "avg_tokens": 0}
        ]
        merged = learner.merge_cross_project_signals(reliability_rows)
        assert merged == []

    def test_row_missing_agent_name_excluded(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        merged = learner.merge_cross_project_signals([{"success_rate": 0.9, "total_steps": 10, "avg_tokens": 0}])
        assert merged == []

    def test_confidence_capped_at_one(self, learner_root: Path):
        learner = PatternLearner(learner_root)
        # total_steps >> calibration constant → confidence should cap at 1.0
        reliability_rows = [
            {"agent_name": "architect", "success_rate": 1.0, "total_steps": 1000, "avg_tokens": 0}
        ]
        merged = learner.merge_cross_project_signals(reliability_rows)
        assert merged[0].confidence == 1.0


# ---------------------------------------------------------------------------
# BudgetTuner.merge_cross_project_cost_signals
# ---------------------------------------------------------------------------

@pytest.fixture
def tuner_root(tmp_path: Path) -> Path:
    root = tmp_path / "team-context"
    root.mkdir()
    return root


class TestBudgetTunerMergeCrossProjectCostSignals:
    def test_empty_signals_produces_empty_file(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        merged = tuner.merge_cross_project_cost_signals([])
        assert merged == []
        assert (tuner_root / "budget-recommendations.json").exists()

    def test_central_entry_appended_for_new_task_type(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        cost_rows = [
            {
                "task_type_hint": "new-api-endpoint",
                "avg_tokens_per_agent": 200_000,
                "task_count": 5,
            }
        ]
        merged = tuner.merge_cross_project_cost_signals(cost_rows)

        assert len(merged) == 1
        rec = merged[0]
        assert rec.source == "central"
        assert rec.task_type == "new-api-endpoint"
        assert rec.recommended_tier == "standard"

    def test_central_entry_persisted_to_disk(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        cost_rows = [
            {"task_type_hint": "refactor", "avg_tokens_per_agent": 600_000, "task_count": 4}
        ]
        tuner.merge_cross_project_cost_signals(cost_rows)

        on_disk = tuner.load_recommendations()
        assert on_disk is not None
        assert len(on_disk) == 1
        assert on_disk[0].source == "central"
        assert on_disk[0].recommended_tier == "full"

    def test_local_task_type_not_overridden_by_central(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        # Seed a local recommendation for "phased_delivery".
        local = _local_budget_rec("phased_delivery")
        tuner._recs_path.parent.mkdir(parents=True, exist_ok=True)
        tuner._recs_path.write_text(
            json.dumps([local.to_dict()], indent=2) + "\n", encoding="utf-8"
        )

        cost_rows = [
            # Same task type as the local recommendation — should be skipped.
            {"task_type_hint": "phased_delivery", "avg_tokens_per_agent": 300_000, "task_count": 6},
            # New task type — should be added.
            {"task_type_hint": "bug-fix", "avg_tokens_per_agent": 80_000, "task_count": 4},
        ]
        merged = tuner.merge_cross_project_cost_signals(cost_rows)

        task_types = {r.task_type for r in merged}
        assert "phased_delivery" in task_types
        assert "bug-fix" in task_types

        # The phased_delivery entry must be the local one (no source tag).
        local_rec = next(r for r in merged if r.task_type == "phased_delivery")
        assert local_rec.source is None

    def test_lean_tier_rows_skipped(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        # avg_tokens_per_agent=10_000 → lean tier → same as baseline → skip
        cost_rows = [
            {"task_type_hint": "tiny-task", "avg_tokens_per_agent": 10_000, "task_count": 5}
        ]
        merged = tuner.merge_cross_project_cost_signals(cost_rows)
        assert merged == []

    def test_rows_below_min_sample_skipped(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        # task_count < _MIN_SAMPLE (3) → skip
        cost_rows = [
            {"task_type_hint": "rare-task", "avg_tokens_per_agent": 200_000, "task_count": 2}
        ]
        merged = tuner.merge_cross_project_cost_signals(cost_rows)
        assert merged == []

    def test_stale_central_entries_replaced_on_rerun(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        tuner.merge_cross_project_cost_signals(
            [{"task_type_hint": "old-type", "avg_tokens_per_agent": 200_000, "task_count": 5}]
        )
        tuner.merge_cross_project_cost_signals(
            [{"task_type_hint": "new-type", "avg_tokens_per_agent": 200_000, "task_count": 5}]
        )

        on_disk = tuner.load_recommendations() or []
        central_recs = [r for r in on_disk if r.source == "central"]
        assert len(central_recs) == 1
        assert central_recs[0].task_type == "new-type"

    def test_row_with_zero_avg_tokens_skipped(self, tuner_root: Path):
        tuner = BudgetTuner(tuner_root)
        merged = tuner.merge_cross_project_cost_signals(
            [{"task_type_hint": "zero-task", "avg_tokens_per_agent": 0, "task_count": 5}]
        )
        assert merged == []

    def test_task_type_hint_fallback_to_task_type_key(self, tuner_root: Path):
        """Rows using 'task_type' key instead of 'task_type_hint' should work."""
        tuner = BudgetTuner(tuner_root)
        cost_rows = [
            {"task_type": "alt-type", "avg_tokens_per_agent": 200_000, "task_count": 4}
        ]
        merged = tuner.merge_cross_project_cost_signals(cost_rows)
        assert len(merged) == 1
        assert merged[0].task_type == "alt-type"


# ---------------------------------------------------------------------------
# ImprovementLoop._apply_central_signals
# ---------------------------------------------------------------------------

class TestImprovementLoopApplyCentralSignals:
    """Unit tests for _apply_central_signals — exercises the method in isolation."""

    def _make_loop(self, tmp_path: Path, learner=None, tuner=None):
        from agent_baton.core.improve.loop import ImprovementLoop
        from agent_baton.core.improve.proposals import ProposalManager
        from agent_baton.core.improve.rollback import RollbackManager
        from agent_baton.core.improve.scoring import PerformanceScorer
        from agent_baton.core.improve.triggers import TriggerEvaluator
        from agent_baton.core.learn.recommender import Recommender
        from agent_baton.models.improvement import ImprovementConfig

        improvements_dir = tmp_path / "improvements"

        recommender = MagicMock(spec=Recommender)
        recommender.analyze.return_value = []
        recommender._learner = learner
        recommender._tuner = tuner

        return ImprovementLoop(
            trigger_evaluator=MagicMock(spec=TriggerEvaluator, **{
                "should_analyze.return_value": True,
                "detect_anomalies.return_value": [],
            }),
            recommender=recommender,
            proposal_manager=ProposalManager(improvements_dir),
            rollback_manager=RollbackManager(improvements_dir=improvements_dir),
            scorer=MagicMock(spec=PerformanceScorer),
            config=ImprovementConfig(),
            improvements_dir=improvements_dir,
        )

    def test_no_op_when_central_store_import_fails(self, tmp_path: Path):
        """_apply_central_signals must not raise when CentralStore cannot be imported."""
        loop = self._make_loop(tmp_path)
        with patch.dict("sys.modules", {"agent_baton.core.storage.central": None}):
            # Should not raise.
            loop._apply_central_signals()

    def test_no_op_when_central_store_init_raises(self, tmp_path: Path):
        """_apply_central_signals must not raise when CentralStore() fails."""
        loop = self._make_loop(tmp_path)
        with patch(
            "agent_baton.core.storage.central.CentralStore.__init__",
            side_effect=OSError("no central.db"),
        ):
            loop._apply_central_signals()  # must not raise

    def test_learner_merge_called_with_reliability_rows(self, tmp_path: Path):
        learner = MagicMock()
        loop = self._make_loop(tmp_path, learner=learner)

        reliability_rows = [
            {"agent_name": "architect", "success_rate": 0.9, "total_steps": 20, "avg_tokens": 0}
        ]
        mock_central = MagicMock()
        mock_central.agent_reliability.return_value = reliability_rows
        mock_central.cost_by_task_type.return_value = []
        mock_central.recurring_knowledge_gaps.return_value = []
        mock_central.project_failure_rates.return_value = []

        with patch("agent_baton.core.storage.central.CentralStore", return_value=mock_central):
            loop._apply_central_signals()

        learner.merge_cross_project_signals.assert_called_once_with(reliability_rows)

    def test_tuner_merge_called_with_cost_rows(self, tmp_path: Path):
        tuner = MagicMock()
        loop = self._make_loop(tmp_path, tuner=tuner)

        cost_rows = [
            {"task_type_hint": "new-feature", "avg_tokens_per_agent": 200_000, "task_count": 5}
        ]
        mock_central = MagicMock()
        mock_central.agent_reliability.return_value = []
        mock_central.cost_by_task_type.return_value = cost_rows
        mock_central.recurring_knowledge_gaps.return_value = []
        mock_central.project_failure_rates.return_value = []

        with patch("agent_baton.core.storage.central.CentralStore", return_value=mock_central):
            loop._apply_central_signals()

        tuner.merge_cross_project_cost_signals.assert_called_once_with(cost_rows)

    def test_agent_reliability_failure_does_not_abort_cost_query(self, tmp_path: Path):
        """A failure in agent_reliability must not prevent cost_by_task_type from running."""
        tuner = MagicMock()
        loop = self._make_loop(tmp_path, tuner=tuner)

        cost_rows = [
            {"task_type_hint": "bug-fix", "avg_tokens_per_agent": 100_000, "task_count": 4}
        ]
        mock_central = MagicMock()
        mock_central.agent_reliability.side_effect = RuntimeError("db locked")
        mock_central.cost_by_task_type.return_value = cost_rows
        mock_central.recurring_knowledge_gaps.return_value = []
        mock_central.project_failure_rates.return_value = []

        with patch("agent_baton.core.storage.central.CentralStore", return_value=mock_central):
            loop._apply_central_signals()  # must not raise

        tuner.merge_cross_project_cost_signals.assert_called_once_with(cost_rows)

    def test_central_close_called_even_on_query_failure(self, tmp_path: Path):
        """central.close() must be called in the finally block."""
        loop = self._make_loop(tmp_path)

        mock_central = MagicMock()
        mock_central.agent_reliability.side_effect = RuntimeError("boom")
        mock_central.cost_by_task_type.side_effect = RuntimeError("boom")
        mock_central.recurring_knowledge_gaps.side_effect = RuntimeError("boom")
        mock_central.project_failure_rates.side_effect = RuntimeError("boom")

        with patch("agent_baton.core.storage.central.CentralStore", return_value=mock_central):
            loop._apply_central_signals()

        mock_central.close.assert_called_once()

    def test_apply_central_signals_called_during_run_cycle(self, tmp_path: Path):
        """run_cycle must invoke _apply_central_signals (verifies wiring)."""
        loop = self._make_loop(tmp_path)
        loop._apply_central_signals = MagicMock()  # type: ignore[method-assign]
        loop.run_cycle(force=True)
        loop._apply_central_signals.assert_called_once()

    def test_no_op_when_learner_and_tuner_absent(self, tmp_path: Path):
        """_apply_central_signals must be safe when recommender has no _learner/_tuner."""
        loop = self._make_loop(tmp_path, learner=None, tuner=None)

        mock_central = MagicMock()
        mock_central.agent_reliability.return_value = []
        mock_central.cost_by_task_type.return_value = []
        mock_central.recurring_knowledge_gaps.return_value = []
        mock_central.project_failure_rates.return_value = []

        with patch("agent_baton.core.storage.central.CentralStore", return_value=mock_central):
            loop._apply_central_signals()  # must not raise
