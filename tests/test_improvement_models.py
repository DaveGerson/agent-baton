"""Tests for agent_baton.models.improvement — all 6 dataclasses."""
from __future__ import annotations

import pytest

from agent_baton.models.improvement import (
    Anomaly,
    Experiment,
    ImprovementConfig,
    ImprovementReport,
    Recommendation,
    TriggerConfig,
)


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

class TestRecommendation:
    def _sample(self) -> Recommendation:
        return Recommendation(
            rec_id="rec-001",
            category="budget_tier",
            target="phased_delivery",
            action="downgrade budget",
            description="p95 is below the standard floor",
            evidence=["avg=20000, median=18000"],
            confidence=0.85,
            risk="low",
            auto_applicable=True,
            proposed_change={"type": "budget_tier", "from": "standard", "to": "lean"},
            rollback_spec={"type": "budget_tier", "from": "lean", "to": "standard"},
            created_at="2026-03-24T00:00:00+00:00",
            status="proposed",
        )

    def test_roundtrip_is_identity(self):
        rec = self._sample()
        restored = Recommendation.from_dict(rec.to_dict())
        assert restored.rec_id == rec.rec_id
        assert restored.category == rec.category
        assert restored.confidence == rec.confidence
        assert restored.auto_applicable == rec.auto_applicable
        assert restored.status == rec.status

    def test_created_at_auto_populated(self):
        rec = Recommendation(rec_id="r1", category="budget_tier", target="t", action="a", description="d")
        assert rec.created_at != ""

    def test_from_dict_handles_missing_optional_fields(self):
        data = {"rec_id": "r1"}
        rec = Recommendation.from_dict(data)
        assert rec.rec_id == "r1"
        assert rec.confidence == 0.0
        assert rec.evidence == []

    @pytest.mark.parametrize("field,value", [
        ("status", "applied"),
        ("risk", "high"),
        ("auto_applicable", False),
    ])
    def test_field_values(self, field, value):
        rec = self._sample()
        setattr(rec, field, value)
        d = rec.to_dict()
        assert d[field] == value


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------

class TestExperiment:
    def _sample(self) -> Experiment:
        return Experiment(
            experiment_id="exp-001",
            recommendation_id="rec-001",
            hypothesis="Downgrade improves cost",
            metric="avg_tokens_per_use",
            baseline_value=50000.0,
            target_value=25000.0,
            agent_name="architect",
            started_at="2026-03-24T00:00:00+00:00",
            min_samples=5,
            max_duration_days=14,
            status="running",
            samples=[45000.0, 42000.0],
            result="",
        )

    def test_roundtrip_is_identity(self):
        exp = self._sample()
        restored = Experiment.from_dict(exp.to_dict())
        assert restored.experiment_id == exp.experiment_id
        assert restored.samples == exp.samples
        assert restored.min_samples == exp.min_samples

    def test_started_at_auto_populated(self):
        exp = Experiment(experiment_id="e1", recommendation_id="r1", hypothesis="h", metric="m")
        assert exp.started_at != ""

    def test_from_dict_defaults(self):
        data = {"experiment_id": "e1"}
        exp = Experiment.from_dict(data)
        assert exp.min_samples == 5
        assert exp.samples == []
        assert exp.status == "running"


# ---------------------------------------------------------------------------
# Anomaly
# ---------------------------------------------------------------------------

class TestAnomaly:
    def _sample(self) -> Anomaly:
        return Anomaly(
            anomaly_type="high_failure_rate",
            severity="medium",
            agent_name="backend",
            metric="failure_rate",
            current_value=0.4,
            threshold=0.3,
            sample_size=10,
            evidence=["4/10 tasks had retries"],
        )

    def test_roundtrip_is_identity(self):
        a = self._sample()
        restored = Anomaly.from_dict(a.to_dict())
        assert restored.anomaly_type == a.anomaly_type
        assert restored.current_value == a.current_value

    def test_from_dict_defaults(self):
        data = {}
        a = Anomaly.from_dict(data)
        assert a.anomaly_type == ""
        assert a.severity == "low"


# ---------------------------------------------------------------------------
# TriggerConfig
# ---------------------------------------------------------------------------

class TestTriggerConfig:
    def test_defaults(self):
        tc = TriggerConfig()
        assert tc.min_tasks_before_analysis == 10
        assert tc.analysis_interval_tasks == 5
        assert tc.agent_failure_threshold == 0.3
        assert tc.gate_failure_threshold == 0.2
        assert tc.budget_deviation_threshold == 0.5
        assert tc.confidence_threshold == 0.7

    def test_roundtrip(self):
        tc = TriggerConfig(min_tasks_before_analysis=20, analysis_interval_tasks=10)
        restored = TriggerConfig.from_dict(tc.to_dict())
        assert restored.min_tasks_before_analysis == 20
        assert restored.analysis_interval_tasks == 10


# ---------------------------------------------------------------------------
# ImprovementReport
# ---------------------------------------------------------------------------

class TestImprovementReport:
    def _sample(self) -> ImprovementReport:
        return ImprovementReport(
            report_id="report-001",
            timestamp="2026-03-24T00:00:00+00:00",
            anomalies=[{"anomaly_type": "high_failure_rate"}],
            recommendations=[{"rec_id": "r1"}],
            auto_applied=["r1"],
            escalated=["r2"],
            active_experiments=["exp-001"],
        )

    def test_roundtrip(self):
        r = self._sample()
        restored = ImprovementReport.from_dict(r.to_dict())
        assert restored.report_id == r.report_id
        assert restored.auto_applied == r.auto_applied
        assert not restored.skipped

    def test_skipped_report(self):
        r = ImprovementReport(report_id="r1", skipped=True, reason="No data")
        d = r.to_dict()
        assert d["skipped"] is True
        assert d["reason"] == "No data"

    def test_timestamp_auto_populated(self):
        r = ImprovementReport(report_id="r1")
        assert r.timestamp != ""


# ---------------------------------------------------------------------------
# ImprovementConfig
# ---------------------------------------------------------------------------

class TestImprovementConfig:
    def test_defaults(self):
        ic = ImprovementConfig()
        assert ic.auto_apply_threshold == 0.8
        assert ic.paused is False

    def test_roundtrip(self):
        ic = ImprovementConfig(auto_apply_threshold=0.9, paused=True)
        restored = ImprovementConfig.from_dict(ic.to_dict())
        assert restored.auto_apply_threshold == 0.9
        assert restored.paused is True
