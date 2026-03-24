"""Tests for agent_baton.core.improve.experiments.ExperimentManager."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.improve.experiments import ExperimentManager
from agent_baton.models.improvement import Recommendation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(rec_id: str = "rec-001", target: str = "architect") -> Recommendation:
    return Recommendation(
        rec_id=rec_id,
        category="budget_tier",
        target=target,
        action="downgrade budget",
        description="test",
        confidence=0.9,
        risk="low",
        auto_applicable=True,
        created_at="2026-03-24T00:00:00+00:00",
    )


def _mgr(tmp_path: Path) -> ExperimentManager:
    return ExperimentManager(tmp_path / "improvements")


# ---------------------------------------------------------------------------
# create_experiment
# ---------------------------------------------------------------------------

class TestCreateExperiment:
    def test_creates_experiment(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(
            recommendation=_rec(),
            metric="avg_tokens_per_use",
            baseline_value=50000.0,
            target_value=25000.0,
        )
        assert exp is not None
        assert exp.recommendation_id == "rec-001"
        assert exp.status == "running"

    def test_max_two_per_agent(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp1 = mgr.create_experiment(_rec("r1"), "m1", 0.5, 0.6, "arch")
        exp2 = mgr.create_experiment(_rec("r2"), "m2", 0.5, 0.6, "arch")
        exp3 = mgr.create_experiment(_rec("r3"), "m3", 0.5, 0.6, "arch")
        assert exp1 is not None
        assert exp2 is not None
        assert exp3 is None  # Third experiment blocked

    def test_different_agents_get_independent_limits(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.create_experiment(_rec("r1", "agent-a"), "m1", 0.5, 0.6, "agent-a")
        mgr.create_experiment(_rec("r2", "agent-a"), "m2", 0.5, 0.6, "agent-a")
        exp3 = mgr.create_experiment(_rec("r3", "agent-b"), "m3", 0.5, 0.6, "agent-b")
        assert exp3 is not None  # Different agent, no limit hit


# ---------------------------------------------------------------------------
# record_sample
# ---------------------------------------------------------------------------

class TestRecordSample:
    def test_records_sample(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        updated = mgr.record_sample(exp.experiment_id, 0.55)
        assert updated is not None
        assert len(updated.samples) == 1
        assert updated.samples[0] == 0.55

    def test_returns_none_for_missing_experiment(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.record_sample("nonexistent", 0.5) is None

    def test_returns_none_for_concluded_experiment(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        mgr.conclude(exp.experiment_id, "improved")
        assert mgr.record_sample(exp.experiment_id, 0.7) is None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_insufficient_data_below_min_samples(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        for i in range(4):  # Only 4 samples, need 5
            mgr.record_sample(exp.experiment_id, 0.55)
        assert mgr.evaluate(exp.experiment_id) == "insufficient_data"

    def test_improved_when_above_threshold(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        # >5% improvement over baseline 0.5 => avg needs to be > 0.525
        for _ in range(5):
            mgr.record_sample(exp.experiment_id, 0.6)
        result = mgr.evaluate(exp.experiment_id)
        assert result == "improved"

    def test_degraded_when_below_threshold(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        # >5% loss from baseline 0.5 => avg needs to be < 0.475
        for _ in range(5):
            mgr.record_sample(exp.experiment_id, 0.4)
        result = mgr.evaluate(exp.experiment_id)
        assert result == "degraded"

    def test_inconclusive_when_within_threshold(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        # Within 5% of baseline 0.5
        for _ in range(5):
            mgr.record_sample(exp.experiment_id, 0.51)
        result = mgr.evaluate(exp.experiment_id)
        assert result == "inconclusive"

    def test_not_found(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.evaluate("nonexistent") == "not_found"

    def test_evaluate_sets_status_to_concluded(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        for _ in range(5):
            mgr.record_sample(exp.experiment_id, 0.6)
        mgr.evaluate(exp.experiment_id)
        loaded = mgr.get(exp.experiment_id)
        assert loaded is not None
        assert loaded.status == "concluded"


# ---------------------------------------------------------------------------
# conclude and mark_rolled_back
# ---------------------------------------------------------------------------

class TestConcludeAndRollback:
    def test_conclude(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        assert mgr.conclude(exp.experiment_id, "improved") is True
        loaded = mgr.get(exp.experiment_id)
        assert loaded is not None
        assert loaded.status == "concluded"
        assert loaded.result == "improved"

    def test_conclude_missing_returns_false(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.conclude("nonexistent", "improved") is False

    def test_mark_rolled_back(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp = mgr.create_experiment(_rec(), "m", 0.5, 0.6)
        assert exp is not None
        assert mgr.mark_rolled_back(exp.experiment_id) is True
        loaded = mgr.get(exp.experiment_id)
        assert loaded is not None
        assert loaded.status == "rolled_back"
        assert loaded.result == "degraded"


# ---------------------------------------------------------------------------
# list / active
# ---------------------------------------------------------------------------

class TestListAndActive:
    def test_list_all(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.create_experiment(_rec("r1"), "m1", 0.5, 0.6)
        mgr.create_experiment(_rec("r2"), "m2", 0.5, 0.6, "other")
        assert len(mgr.list_all()) == 2

    def test_active_filters_running(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        exp1 = mgr.create_experiment(_rec("r1"), "m1", 0.5, 0.6)
        mgr.create_experiment(_rec("r2"), "m2", 0.5, 0.6, "other")
        assert exp1 is not None
        mgr.conclude(exp1.experiment_id, "improved")
        assert len(mgr.active()) == 1

    def test_active_for_agent(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.create_experiment(_rec("r1", "agent-a"), "m1", 0.5, 0.6, "agent-a")
        mgr.create_experiment(_rec("r2", "agent-b"), "m2", 0.5, 0.6, "agent-b")
        assert len(mgr.active_for_agent("agent-a")) == 1
        assert len(mgr.active_for_agent("agent-c")) == 0

    def test_empty_when_no_dir(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.list_all() == []
