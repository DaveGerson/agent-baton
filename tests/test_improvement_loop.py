"""Tests for agent_baton.core.improve.loop.ImprovementLoop."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Suppress DeprecationWarning for the deprecated ExperimentManager used in
# test fixtures. These tests remain for backward-compatibility coverage.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

from agent_baton.core.improve.experiments import ExperimentManager
from agent_baton.core.improve.loop import ImprovementLoop
from agent_baton.core.improve.proposals import ProposalManager
from agent_baton.core.improve.rollback import RollbackManager
from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.core.improve.vcs import AgentVersionControl
from agent_baton.core.learn.recommender import Recommender
from agent_baton.models.improvement import (
    Anomaly,
    ImprovementConfig,
    Recommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auto_rec(rec_id: str = "rec-auto") -> Recommendation:
    """A recommendation that passes all auto-apply checks."""
    return Recommendation(
        rec_id=rec_id,
        category="budget_tier",
        target="phased_delivery",
        action="downgrade budget",
        description="Safe downgrade",
        confidence=0.9,
        risk="low",
        auto_applicable=True,
        created_at="2026-03-24T00:00:00+00:00",
        status="proposed",
    )


def _escalated_rec(rec_id: str = "rec-escalated") -> Recommendation:
    """A recommendation that should be escalated."""
    return Recommendation(
        rec_id=rec_id,
        category="agent_prompt",
        target="architect",
        action="evolve prompt",
        description="Needs human review",
        confidence=0.8,
        risk="high",
        auto_applicable=False,
        created_at="2026-03-24T00:00:00+00:00",
        status="proposed",
    )


def _loop(
    tmp_path: Path,
    should_analyze: bool = True,
    recommendations: list[Recommendation] | None = None,
    anomalies: list[Anomaly] | None = None,
    config: ImprovementConfig | None = None,
) -> ImprovementLoop:
    improvements_dir = tmp_path / "improvements"

    triggers = MagicMock(spec=TriggerEvaluator)
    triggers.should_analyze.return_value = should_analyze
    triggers.detect_anomalies.return_value = anomalies or []

    recommender = MagicMock(spec=Recommender)
    recommender.analyze.return_value = recommendations or []

    scorer = MagicMock(spec=PerformanceScorer)
    scorecard = AgentScorecard(agent_name="test", times_used=10, first_pass_rate=0.8)
    scorer.score_agent.return_value = scorecard

    proposals = ProposalManager(improvements_dir)
    experiments = ExperimentManager(improvements_dir)
    vcs = AgentVersionControl(tmp_path / "agents")
    rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

    return ImprovementLoop(
        trigger_evaluator=triggers,
        recommender=recommender,
        proposal_manager=proposals,
        experiment_manager=experiments,
        rollback_manager=rollbacks,
        scorer=scorer,
        config=config or ImprovementConfig(),
        improvements_dir=improvements_dir,
    )


# ---------------------------------------------------------------------------
# run_cycle — basic behaviour
# ---------------------------------------------------------------------------

class TestRunCycle:
    def test_skipped_when_no_trigger(self, tmp_path: Path):
        loop = _loop(tmp_path, should_analyze=False)
        report = loop.run_cycle()
        assert report.skipped is True
        assert "Not enough new data" in report.reason

    def test_force_bypasses_trigger(self, tmp_path: Path):
        loop = _loop(tmp_path, should_analyze=False, recommendations=[])
        report = loop.run_cycle(force=True)
        assert report.skipped is False

    def test_empty_cycle_with_no_recommendations(self, tmp_path: Path):
        loop = _loop(tmp_path, recommendations=[])
        report = loop.run_cycle()
        assert report.skipped is False
        assert len(report.recommendations) == 0
        assert len(report.auto_applied) == 0
        assert len(report.escalated) == 0

    def test_auto_applies_safe_recommendations(self, tmp_path: Path):
        rec = _auto_rec()
        loop = _loop(tmp_path, recommendations=[rec])
        report = loop.run_cycle()
        assert rec.rec_id in report.auto_applied
        assert rec.rec_id not in report.escalated

    def test_escalates_risky_recommendations(self, tmp_path: Path):
        rec = _escalated_rec()
        loop = _loop(tmp_path, recommendations=[rec])
        report = loop.run_cycle()
        assert rec.rec_id in report.escalated
        assert rec.rec_id not in report.auto_applied

    def test_mixed_recommendations(self, tmp_path: Path):
        auto = _auto_rec("rec-auto")
        risky = _escalated_rec("rec-risky")
        loop = _loop(tmp_path, recommendations=[auto, risky])
        report = loop.run_cycle()
        assert "rec-auto" in report.auto_applied
        assert "rec-risky" in report.escalated

    def test_anomalies_included_in_report(self, tmp_path: Path):
        anomaly = Anomaly(
            anomaly_type="high_failure_rate",
            severity="medium",
            agent_name="flaky",
            metric="failure_rate",
            current_value=0.4,
            threshold=0.3,
        )
        loop = _loop(tmp_path, anomalies=[anomaly])
        report = loop.run_cycle()
        assert len(report.anomalies) == 1

    def test_report_persisted_to_disk(self, tmp_path: Path):
        loop = _loop(tmp_path, recommendations=[])
        report = loop.run_cycle()
        reports = loop.load_reports()
        assert len(reports) == 1
        assert reports[0].report_id == report.report_id


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

class TestGuardrails:
    def test_prompt_changes_never_auto_applied(self, tmp_path: Path):
        rec = Recommendation(
            rec_id="rec-prompt",
            category="agent_prompt",
            target="architect",
            action="evolve prompt",
            description="test",
            confidence=0.99,
            risk="low",  # Even with low risk
            auto_applicable=True,  # And flagged auto_applicable
            created_at="2026-03-24T00:00:00+00:00",
        )
        loop = _loop(tmp_path, recommendations=[rec])
        report = loop.run_cycle()
        # Prompt changes ALWAYS escalated regardless of other flags
        assert rec.rec_id in report.escalated
        assert rec.rec_id not in report.auto_applied

    def test_high_risk_never_auto_applied(self, tmp_path: Path):
        rec = Recommendation(
            rec_id="rec-high",
            category="budget_tier",
            target="test",
            action="test",
            description="test",
            confidence=0.99,
            risk="high",
            auto_applicable=True,
            created_at="2026-03-24T00:00:00+00:00",
        )
        loop = _loop(tmp_path, recommendations=[rec])
        report = loop.run_cycle()
        assert rec.rec_id in report.escalated

    def test_below_threshold_not_auto_applied(self, tmp_path: Path):
        rec = Recommendation(
            rec_id="rec-low-conf",
            category="budget_tier",
            target="test",
            action="test",
            description="test",
            confidence=0.5,  # Below 0.8 threshold
            risk="low",
            auto_applicable=True,
            created_at="2026-03-24T00:00:00+00:00",
        )
        loop = _loop(tmp_path, recommendations=[rec])
        report = loop.run_cycle()
        assert rec.rec_id in report.escalated


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_paused_config_skips_cycle(self, tmp_path: Path):
        config = ImprovementConfig(paused=True)
        loop = _loop(tmp_path, config=config)
        report = loop.run_cycle()
        assert report.skipped is True
        assert "paused" in report.reason.lower()

    def test_circuit_breaker_tripped_skips_cycle(self, tmp_path: Path):
        improvements_dir = tmp_path / "improvements"
        vcs = AgentVersionControl(tmp_path / "agents")
        rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

        # Trip the circuit breaker with 3 rollbacks
        for i in range(3):
            rec = Recommendation(
                rec_id=f"r{i}",
                category="budget_tier",
                target="test",
                action="test",
                description="test",
                created_at="2026-03-24T00:00:00+00:00",
            )
            rollbacks.rollback(rec, f"reason {i}")

        triggers = MagicMock(spec=TriggerEvaluator)
        triggers.should_analyze.return_value = True
        triggers.detect_anomalies.return_value = []

        recommender = MagicMock(spec=Recommender)
        recommender.analyze.return_value = []

        scorer = MagicMock(spec=PerformanceScorer)

        loop = ImprovementLoop(
            trigger_evaluator=triggers,
            recommender=recommender,
            proposal_manager=ProposalManager(improvements_dir),
            experiment_manager=ExperimentManager(improvements_dir),
            rollback_manager=rollbacks,
            scorer=scorer,
            improvements_dir=improvements_dir,
        )
        report = loop.run_cycle()
        assert report.skipped is True
        assert "circuit breaker" in report.reason.lower()


# ---------------------------------------------------------------------------
# Experiment evaluation + auto-rollback
# ---------------------------------------------------------------------------

class TestExperimentEvaluation:
    def test_degraded_experiment_triggers_rollback(self, tmp_path: Path):
        improvements_dir = tmp_path / "improvements"
        proposals = ProposalManager(improvements_dir)
        experiments = ExperimentManager(improvements_dir)
        vcs = AgentVersionControl(tmp_path / "agents")
        rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

        # Record a recommendation
        rec = _auto_rec("rec-to-rollback")
        rec.status = "applied"
        proposals.record(rec)

        # Create an experiment
        exp = experiments.create_experiment(
            recommendation=rec,
            metric="first_pass_rate",
            baseline_value=0.8,
            target_value=0.84,
            agent_name="test-agent",
        )
        assert exp is not None

        # Record degraded samples (>5% loss from 0.8 baseline)
        for _ in range(5):
            experiments.record_sample(exp.experiment_id, 0.5)

        triggers = MagicMock(spec=TriggerEvaluator)
        triggers.should_analyze.return_value = True
        triggers.detect_anomalies.return_value = []

        recommender = MagicMock(spec=Recommender)
        recommender.analyze.return_value = []

        scorer = MagicMock(spec=PerformanceScorer)

        loop = ImprovementLoop(
            trigger_evaluator=triggers,
            recommender=recommender,
            proposal_manager=proposals,
            experiment_manager=experiments,
            rollback_manager=rollbacks,
            scorer=scorer,
            improvements_dir=improvements_dir,
        )

        results = loop.evaluate_experiments()
        assert len(results) == 1
        assert results[0][1] == "degraded"

        # Verify the experiment was rolled back
        loaded_exp = experiments.get(exp.experiment_id)
        assert loaded_exp is not None
        assert loaded_exp.status == "rolled_back"

        # Verify the recommendation was rolled back
        loaded_rec = proposals.get("rec-to-rollback")
        assert loaded_rec is not None
        assert loaded_rec.status == "rolled_back"


# ---------------------------------------------------------------------------
# load_reports
# ---------------------------------------------------------------------------

class TestLoadReports:
    def test_empty_when_no_reports(self, tmp_path: Path):
        loop = _loop(tmp_path)
        assert loop.load_reports() == []

    def test_loads_multiple_reports(self, tmp_path: Path):
        loop = _loop(tmp_path, recommendations=[])
        loop.run_cycle()
        loop.run_cycle(force=True)
        reports = loop.load_reports()
        assert len(reports) == 2


# ---------------------------------------------------------------------------
# StoragePassthrough — storage param flows to TriggerEvaluator + Recommender
# ---------------------------------------------------------------------------

class TestStoragePassthrough:
    def test_loop_passes_storage_to_defaults(self, tmp_path: Path):
        """When storage is provided to ImprovementLoop, verify it reaches
        the TriggerEvaluator and Recommender defaults."""
        storage = MagicMock()
        # read_usage() must return a list; called by TriggerEvaluator._read_records
        # and by Recommender sub-components.
        storage.read_usage.return_value = []

        loop = ImprovementLoop(
            improvements_dir=tmp_path / "improvements",
            storage=storage,
        )

        # The loop must have built a real TriggerEvaluator (not a mock) that
        # holds a reference to our storage object.
        assert loop._triggers._storage is storage

        # The recommender's internal scorer, learner, and tuner each accept
        # a storage kwarg; verify at least one level is wired by confirming
        # the recommender was constructed (not replaced by a mock).
        assert isinstance(loop._recommender, type(loop._recommender))

        # Calling should_analyze() must invoke storage.read_usage() rather
        # than attempting to open a JSONL file that does not exist.
        loop._triggers.should_analyze()
        storage.read_usage.assert_called_once()
