"""Tests for agent_baton.core.federate.enrich — spec-draft auto-enrichment.

Covers the cost-forecast half of ``enrich()``:

  1. per_agent_breakdown_is_populated — the per-agent breakdown has one row
     per agent in the risk-level roster, each row carrying the agent's own
     token estimate and the cost that corresponds to *those* tokens.
  2. breakdown_totals_match_mid_estimate — the per-agent dollar figures sum
     to the headline mid estimate (no attribution is lost or invented).
  3. history_calibrated_forecast_upgrades_confidence — when ``central.db``
     exists and the historical forecaster returns samples, its bands and
     breakdown win and ``cost_confidence`` reports the calibrated value.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agent_baton.core.federate.enrich as enrich_mod
from agent_baton.core.engine.cost_estimator import (
    normalise_model,
    role_baseline_tokens,
    step_cost_usd,
)
from agent_baton.core.federate.enrich import _DEFAULT_ROSTER_BY_RISK, enrich
from agent_baton.models.cost_forecast import CostForecast

# A deliberately high-risk spec so the roster has several distinct agents
# with *different* role token baselines (developer 4k, code-reviewer 8k,
# auditor 6k, security-reviewer 6k).
_TITLE = "Add HIPAA-compliant patient record export with PHI redaction"
_BODY = (
    "We must handle protected health information (PHI) and write HIPAA "
    "compliance audit logs for every export.\n\n"
    "## Acceptance criteria\n"
    "- Exported records redact PHI fields before leaving the service.\n"
    "- Every export writes an immutable audit-log row.\n"
)


@pytest.fixture()
def no_central_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the central-db probe at a path that does not exist.

    Keeps the default-forecast tests hermetic on machines that happen to
    have a populated ``~/.baton/central.db``.
    """
    monkeypatch.setattr(
        enrich_mod, "_CENTRAL_DB_DEFAULT", tmp_path / "absent" / "central.db"
    )


# ---------------------------------------------------------------------------
# 1. The per-agent breakdown is actually produced
# ---------------------------------------------------------------------------


def test_per_agent_breakdown_is_populated(no_central_db: None) -> None:
    """enrich() returns one breakdown row per roster agent, with real numbers.

    Each row must carry the agent's *own* token estimate (the cost
    estimator's role baseline) and a dollar figure derived from those
    tokens — not a shared or zeroed placeholder.
    """
    data = enrich(_TITLE, _BODY)

    expected_roster = _DEFAULT_ROSTER_BY_RISK.get(data.risk_level, ["developer"])
    assert len(expected_roster) > 1, (
        f"expected a multi-agent roster for risk {data.risk_level!r}; "
        "the fixture text should classify as HIGH or CRITICAL"
    )

    assert data.breakdown, "enrich() produced an empty per-agent cost breakdown"
    assert [row["agent_name"] for row in data.breakdown] == expected_roster

    for row in data.breakdown:
        agent = row["agent_name"]
        assert agent, "breakdown row has an empty agent_name"
        assert row["est_steps"] == 1
        assert normalise_model(row["model"]) == "sonnet"

        assert row["est_tokens"] > 0
        assert row["est_tokens"] == role_baseline_tokens(agent), (
            f"row for {agent!r} does not carry that agent's own token estimate"
        )
        assert row["est_usd"] == pytest.approx(
            step_cost_usd(row["est_tokens"], row["model"]), rel=1e-3
        ), f"row for {agent!r} priced inconsistently with its own token count"

    # Distinct role baselines must survive into the breakdown: a flat
    # per-agent split would collapse these to a single value.
    assert len({row["est_tokens"] for row in data.breakdown}) > 1


# ---------------------------------------------------------------------------
# 2. The breakdown reconciles with the headline estimate
# ---------------------------------------------------------------------------


def test_breakdown_totals_match_mid_estimate(no_central_db: None) -> None:
    """Per-agent dollars sum to est_usd_mid, and the bands bracket the mid."""
    data = enrich(_TITLE, _BODY)

    assert data.est_usd_mid > 0
    assert data.breakdown, "enrich() produced an empty per-agent cost breakdown"

    total = sum(row["est_usd"] for row in data.breakdown)
    assert total == pytest.approx(data.est_usd_mid, rel=1e-3)

    assert data.est_usd_low < data.est_usd_mid < data.est_usd_high


# ---------------------------------------------------------------------------
# 3. The history-calibrated upgrade path is reachable
# ---------------------------------------------------------------------------


def test_history_calibrated_forecast_upgrades_confidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When central.db has history, its forecast replaces the default one.

    Proves the calibrated block downstream of the breakdown build is
    actually reached rather than being skipped by an earlier failure.
    """
    central_db = tmp_path / "central.db"
    central_db.touch()
    monkeypatch.setattr(enrich_mod, "_CENTRAL_DB_DEFAULT", central_db)

    calls: list[str] = []
    calibrated_breakdown = [
        {
            "agent_name": "developer",
            "model": "sonnet",
            "est_steps": 1,
            "est_tokens": 12_345,
            "est_usd": 0.5,
        }
    ]

    class _StubForecaster:
        def __init__(self, store, **kwargs) -> None:
            calls.append(str(store))

        def forecast(self, plan) -> CostForecast:
            return CostForecast(
                plan_id=plan.task_id,
                computed_at="2026-01-01T00:00:00+00:00",
                est_input_tokens=10_000,
                est_output_tokens=2_345,
                est_usd_low=0.375,
                est_usd_mid=0.5,
                est_usd_high=0.625,
                basis_window_days=14,
                sample_size=3,
                breakdown=list(calibrated_breakdown),
            )

    monkeypatch.setattr(
        "agent_baton.core.observe.cost_forecaster.CostForecaster", _StubForecaster
    )

    data = enrich(_TITLE, _BODY)

    assert calls == [str(central_db)], (
        "the history-calibrated forecaster was never consulted — the "
        "calibrated block is unreachable"
    )
    assert data.cost_confidence == "high"
    assert data.est_usd_low == pytest.approx(0.375)
    assert data.est_usd_mid == pytest.approx(0.5)
    assert data.est_usd_high == pytest.approx(0.625)
    assert data.breakdown == calibrated_breakdown
