"""Tests for the statistical cost anomaly detector (O1.3, bd-91c7)."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.improve.cost_anomaly import (
    CostAnomaly,
    CostAnomalyDetector,
    _StepRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    step_id: str,
    tokens: int,
    agent: str = "developer",
    model: str = "sonnet",
    task_id: str = "t1",
) -> _StepRecord:
    return _StepRecord(
        task_id=task_id,
        step_id=step_id,
        agent_name=agent,
        model=model,
        tokens=tokens,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


def _detector_with(records, tmp_path: Path) -> CostAnomalyDetector:
    return CostAnomalyDetector(
        records=records,
        ack_store_path=tmp_path / "acks.json",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_data_no_anomalies(tmp_path: Path) -> None:
    detector = _detector_with([], tmp_path)
    assert detector.detect() == []


def test_steady_state_no_anomalies(tmp_path: Path) -> None:
    # Tight cluster around 1000 tokens.
    records = [_record(f"s{i}", 1000 + (i % 3) * 5) for i in range(30)]
    detector = _detector_with(records, tmp_path)
    assert detector.detect() == []


def test_single_outlier_flagged_with_high_z_score(tmp_path: Path) -> None:
    # 29 baseline samples around 1000 + one massive outlier at 50_000.
    baseline = [_record(f"s{i}", 1000 + (i % 3) * 5) for i in range(29)]
    outlier = _record("OUTLIER", 50_000)
    detector = _detector_with(baseline + [outlier], tmp_path)

    anomalies = detector.detect()
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.step_id == "OUTLIER"
    assert a.observed_tokens == 50_000
    # Either z is huge OR the IQR-only path bumped it to high.
    assert a.severity == "high"


def test_iqr_fence_catches_outlier_when_distribution_is_skewed(tmp_path: Path) -> None:
    """IQR fence catches outliers even when the stdev is inflated."""
    # Heavily skewed distribution: lots of small values + a few big spikes.
    # The huge tail values inflate stdev so z-score alone may miss the
    # newest extreme spike.  IQR is robust to this.
    records = []
    for i in range(20):
        records.append(_record(f"low{i}", 500 + i * 10))  # 500..690
    # Two prior moderate outliers that bump stdev:
    records.append(_record("mod1", 5_000))
    records.append(_record("mod2", 6_000))
    # The actual extreme outlier:
    extreme = _record("EXTREME", 100_000)
    records.append(extreme)

    detector = _detector_with(records, tmp_path)
    anomalies = detector.detect()
    flagged_ids = {a.step_id for a in anomalies}
    assert "EXTREME" in flagged_ids
    # IQR factor on the extreme one must be substantial.
    extreme_anom = next(a for a in anomalies if a.step_id == "EXTREME")
    assert extreme_anom.iqr_factor > 3.0


def test_severity_bucketing_matches_z_score_thresholds(tmp_path: Path) -> None:
    """The severity classifier must respect the documented z-score buckets.

    We exercise the classifier directly so the test isn't sensitive to
    how the injected outliers themselves shift the baseline stats.
    """
    from agent_baton.core.improve.cost_anomaly import _classify_severity

    # Pure z-score paths (IQR-only override disabled).
    assert _classify_severity(z=3.5, iqr_factor=0.0,
                              flagged_iqr=False, flagged_z=True) == "low"
    assert _classify_severity(z=4.5, iqr_factor=0.0,
                              flagged_iqr=False, flagged_z=True) == "medium"
    assert _classify_severity(z=7.0, iqr_factor=0.0,
                              flagged_iqr=False, flagged_z=True) == "high"

    # IQR-only override fires for stdev-suppressed cases.
    assert _classify_severity(z=0.0, iqr_factor=10.0,
                              flagged_iqr=True, flagged_z=False) == "high"

    # Large IQR factor promotes a medium z to high.
    assert _classify_severity(z=4.5, iqr_factor=8.0,
                              flagged_iqr=True, flagged_z=True) == "high"

    # Sanity end-to-end: at least one of the injected outliers comes back
    # flagged.  We don't pin its exact severity bucket because injected
    # outliers shift the stats they're being measured against.
    base = [_record(f"b{i}", 900 + (i * 13) % 200) for i in range(50)]
    detector = _detector_with(
        base + [_record("BIG", 50_000)],
        tmp_path,
    )
    anomalies = detector.detect()
    assert any(a.step_id == "BIG" for a in anomalies)
    big = next(a for a in anomalies if a.step_id == "BIG")
    assert big.severity == "high"


def test_per_pair_isolation(tmp_path: Path) -> None:
    """An anomaly in agent A / model X must not pollute agent B / model Y."""
    # Agent A / sonnet: tight baseline + one big outlier.
    a_records = [_record(f"a{i}", 1000) for i in range(20)] + [
        _record("A_OUT", 50_000, agent="agentA", model="sonnet"),
    ]
    for r in a_records[:-1]:
        r.agent_name = "agentA"
        r.model = "sonnet"

    # Agent B / haiku: completely steady, smaller token count.
    b_records = [
        _record(f"b{i}", 200, agent="agentB", model="haiku")
        for i in range(20)
    ]

    detector = _detector_with(a_records + b_records, tmp_path)
    anomalies = detector.detect()

    # Exactly one anomaly, and it must be the agent-A outlier.
    assert len(anomalies) == 1
    assert anomalies[0].agent == "agentA"
    assert anomalies[0].step_id == "A_OUT"

    # If agentB's stats had been polluted by agentA's huge value, then a
    # low-token value (200) would NOT have looked like an outlier --
    # confirm none of B's steps were flagged.
    assert not any(a.agent == "agentB" for a in anomalies)


def test_json_roundtrip(tmp_path: Path) -> None:
    a = CostAnomaly(
        step_id="s1",
        task_id="t1",
        agent="developer",
        model="sonnet",
        observed_tokens=12345,
        baseline_mean=1000.5,
        baseline_stdev=120.7,
        z_score=8.2,
        iqr_factor=4.1,
        severity="high",
        completed_at="2026-04-25T10:00:00+00:00",
    )
    payload = json.dumps([a.to_dict()])
    restored = [CostAnomaly.from_dict(d) for d in json.loads(payload)]
    assert len(restored) == 1
    r = restored[0]
    assert r.step_id == "s1"
    assert r.observed_tokens == 12345
    assert r.severity == "high"
    assert r.z_score == pytest.approx(8.2)
    assert r.iqr_factor == pytest.approx(4.1)
    assert r.to_dict() == a.to_dict()


def test_acknowledge_suppresses_re_surfacing(tmp_path: Path) -> None:
    base = [_record(f"b{i}", 1000 + (i % 3) * 5) for i in range(29)]
    outlier = _record("OUTLIER", 50_000)
    detector = _detector_with(base + [outlier], tmp_path)

    first = detector.detect()
    assert len(first) == 1

    added = detector.acknowledge(first)
    assert added == 1

    # Second run on identical data should now be empty (acked filtered out).
    second = detector.detect()
    assert second == []

    # include_acked=True still surfaces them for audit views.
    audit = detector.detect(include_acked=True)
    assert len(audit) == 1


def test_to_anomaly_dict_matches_anomaly_schema() -> None:
    """Serialising as an ``Anomaly`` entry must round-trip via Anomaly.from_dict."""
    from agent_baton.models.improvement import Anomaly

    ca = CostAnomaly(
        step_id="s9",
        task_id="t9",
        agent="planner",
        model="opus",
        observed_tokens=99_000,
        baseline_mean=1500.0,
        baseline_stdev=200.0,
        z_score=12.5,
        iqr_factor=8.0,
        severity="high",
    )
    anom = Anomaly.from_dict(ca.to_anomaly_dict())
    assert anom.anomaly_type == "cost_anomaly"
    assert anom.severity == "high"
    assert anom.agent_name == "planner"
    assert anom.metric == "tokens_per_step"
    assert anom.current_value == pytest.approx(99_000.0)
    assert "z=12.50" in " ".join(anom.evidence)


def test_db_round_trip_via_sqlite(tmp_path: Path) -> None:
    """Detector reads from a real SQLite step_results table."""
    db_path = tmp_path / "baton.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE step_results (
            task_id TEXT,
            step_id TEXT,
            agent_name TEXT,
            model_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            completed_at TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("t1", f"s{i}", "developer", "sonnet", 500, 500, now)
        for i in range(20)
    ]
    rows.append(("t1", "OUTLIER", "developer", "sonnet", 25_000, 25_000, now))
    conn.executemany(
        "INSERT INTO step_results VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()

    detector = CostAnomalyDetector(
        db_path=db_path,
        ack_store_path=tmp_path / "acks.json",
    )
    anomalies = detector.detect()
    assert len(anomalies) == 1
    assert anomalies[0].step_id == "OUTLIER"
    assert anomalies[0].observed_tokens == 50_000
    assert anomalies[0].agent == "developer"
    assert anomalies[0].model == "sonnet"


def test_detector_completes_quickly_for_30_days_x_50_pairs(tmp_path: Path) -> None:
    """Performance budget: <1s for window of 30d × 50 pairs."""
    records: list[_StepRecord] = []
    for pair_idx in range(50):
        agent = f"agent{pair_idx}"
        model = "sonnet" if pair_idx % 2 == 0 else "haiku"
        for i in range(30):  # 30 samples per pair
            records.append(
                _StepRecord(
                    task_id="t1",
                    step_id=f"{agent}-{i}",
                    agent_name=agent,
                    model=model,
                    tokens=1000 + (i * 7) % 200,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            )

    detector = _detector_with(records, tmp_path)
    start = time.monotonic()
    detector.detect()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"Detector took {elapsed:.3f}s (budget: 1.0s)"


def test_does_not_block_on_missing_db(tmp_path: Path) -> None:
    """If the DB doesn't exist, the detector returns [] silently."""
    detector = CostAnomalyDetector(
        db_path=tmp_path / "does-not-exist.db",
        ack_store_path=tmp_path / "acks.json",
    )
    assert detector.detect() == []
