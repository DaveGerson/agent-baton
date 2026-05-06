"""Tests for CostForecaster and CostForecast.

Uses :memory: SQLite with seeded agent_usage / usage_records rows.
No mocks for internal code; behaviour is tested via the public API.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.core.observe.cost_forecaster import CostForecaster, _model_key
from agent_baton.models.cost_forecast import CostForecast
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ── Helpers ────────────────────────────────────────────────────────────────────

def _schema_ddl() -> str:
    """Minimal DDL for usage_records + agent_usage tables."""
    return """
    CREATE TABLE usage_records (
        task_id   TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        total_agents INTEGER NOT NULL DEFAULT 0,
        risk_level TEXT NOT NULL DEFAULT 'LOW',
        sequencing_mode TEXT NOT NULL DEFAULT 'phased_delivery',
        gates_passed INTEGER NOT NULL DEFAULT 0,
        gates_failed INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE agent_usage (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id          TEXT NOT NULL,
        agent_name       TEXT NOT NULL,
        model            TEXT NOT NULL DEFAULT 'sonnet',
        steps            INTEGER NOT NULL DEFAULT 1,
        retries          INTEGER NOT NULL DEFAULT 0,
        gate_results     TEXT NOT NULL DEFAULT '[]',
        estimated_tokens INTEGER NOT NULL DEFAULT 0,
        duration_seconds REAL NOT NULL DEFAULT 0.0
    );
    """


def _make_conn(rows: list[tuple] | None = None) -> sqlite3.Connection:
    """Create a :memory: DB seeded with optional agent_usage rows.

    Each *rows* tuple: (task_id, timestamp_iso, agent_name, model, estimated_tokens)
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for stmt in _schema_ddl().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    if rows:
        for task_id, ts, agent, model, tokens in rows:
            conn.execute(
                "INSERT OR IGNORE INTO usage_records (task_id, timestamp) VALUES (?, ?)",
                (task_id, ts),
            )
            conn.execute(
                "INSERT INTO agent_usage (task_id, agent_name, model, estimated_tokens) "
                "VALUES (?, ?, ?, ?)",
                (task_id, agent, model, tokens),
            )
        conn.commit()
    return conn


def _make_plan(steps: list[tuple[str, str]]) -> MachinePlan:
    """Build a MachinePlan with one phase containing the given (agent, model) steps."""
    plan_steps = [
        PlanStep(
            step_id=f"1.{i + 1}",
            agent_name=agent,
            task_description="test",
            model=model,
        )
        for i, (agent, model) in enumerate(steps)
    ]
    phase = PlanPhase(phase_id=1, name="phase", steps=plan_steps)
    return MachinePlan(
        task_id="test-plan",
        task_summary="test",
        phases=[phase],
    )


def _recent_ts(days_ago: int = 0) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat(timespec="seconds")


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestEmptyPlan:
    def test_empty_plan_gives_zero_forecast(self) -> None:
        conn = _make_conn()
        plan = _make_plan([])
        fc = CostForecaster(conn).forecast(plan)
        assert fc.est_input_tokens == 0
        assert fc.est_output_tokens == 0
        assert fc.est_usd_mid == 0.0
        assert fc.est_usd_low == 0.0
        assert fc.est_usd_high == 0.0
        assert fc.breakdown == []
        conn.close()


class TestDefaultsWhenNoHistory:
    def test_one_sonnet_step_uses_defaults(self) -> None:
        conn = _make_conn()  # no seeded rows
        plan = _make_plan([("backend-engineer", "sonnet")])
        fc = CostForecaster(conn).forecast(plan)
        # defaults: sonnet = 8000 input / 2000 output
        assert fc.est_input_tokens == 8_000
        assert fc.est_output_tokens == 2_000
        assert fc.est_usd_mid > 0.0
        conn.close()

    def test_haiku_defaults(self) -> None:
        conn = _make_conn()
        plan = _make_plan([("light-agent", "haiku")])
        fc = CostForecaster(conn).forecast(plan)
        assert fc.est_input_tokens == 2_000
        assert fc.est_output_tokens == 500
        conn.close()

    def test_opus_defaults(self) -> None:
        conn = _make_conn()
        plan = _make_plan([("heavy-agent", "opus")])
        fc = CostForecaster(conn).forecast(plan)
        assert fc.est_input_tokens == 20_000
        assert fc.est_output_tokens == 5_000
        conn.close()


class TestHistoricalUsage:
    def test_historical_median_used_when_present(self) -> None:
        # Seed 3 rows totalling 30000 tokens (median 10000)
        ts = _recent_ts(0)
        rows = [
            ("t1", ts, "backend-engineer", "sonnet", 8_000),
            ("t2", ts, "backend-engineer", "sonnet", 10_000),
            ("t3", ts, "backend-engineer", "sonnet", 12_000),
        ]
        conn = _make_conn(rows)
        plan = _make_plan([("backend-engineer", "sonnet")])
        fc = CostForecaster(conn).forecast(plan)
        # median of [8000, 10000, 12000] = 10000
        assert fc.est_input_tokens + fc.est_output_tokens == 10_000
        # sample_size should reflect all 3 rows
        assert fc.sample_size == 3
        conn.close()


class TestMixedAgentsModels:
    def test_mixed_breakdown_sums_to_total(self) -> None:
        conn = _make_conn()
        plan = _make_plan([
            ("backend-engineer", "sonnet"),
            ("test-engineer", "haiku"),
            ("auditor", "opus"),
        ])
        fc = CostForecaster(conn).forecast(plan)

        breakdown_usd = sum(r["est_usd"] for r in fc.breakdown)
        assert abs(breakdown_usd - fc.est_usd_mid) < 1e-4

        breakdown_tokens = sum(r["est_tokens"] for r in fc.breakdown)
        assert breakdown_tokens == fc.est_input_tokens + fc.est_output_tokens
        conn.close()


class TestConfidenceBands:
    def test_bands_are_correct_ratios(self) -> None:
        conn = _make_conn()
        plan = _make_plan([("backend-engineer", "sonnet")])
        fc = CostForecaster(conn).forecast(plan)
        assert fc.est_usd_low < fc.est_usd_mid < fc.est_usd_high
        assert abs(fc.est_usd_low - 0.75 * fc.est_usd_mid) < 1e-6
        assert abs(fc.est_usd_high - 1.25 * fc.est_usd_mid) < 1e-6
        conn.close()

    def test_zero_mid_gives_zero_bands(self) -> None:
        conn = _make_conn()
        plan = _make_plan([])
        fc = CostForecaster(conn).forecast(plan)
        assert fc.est_usd_low == 0.0
        assert fc.est_usd_high == 0.0
        conn.close()


class TestWindowRespected:
    def test_records_outside_window_are_ignored(self) -> None:
        old_ts = _recent_ts(days_ago=30)
        recent_ts = _recent_ts(days_ago=1)
        rows = [
            # Old record with huge token count — should be excluded
            ("old-1", old_ts, "backend-engineer", "sonnet", 999_000),
            # Recent record with a modest count
            ("new-1", recent_ts, "backend-engineer", "sonnet", 9_000),
        ]
        conn = _make_conn(rows)
        plan = _make_plan([("backend-engineer", "sonnet")])
        # 14-day window: only new-1 is within range
        fc = CostForecaster(conn, basis_window_days=14).forecast(plan)
        # should use median of [9000], not [999000]
        assert fc.est_input_tokens + fc.est_output_tokens == 9_000
        conn.close()

    def test_records_within_wide_window_are_included(self) -> None:
        old_ts = _recent_ts(days_ago=25)
        rows = [
            ("old-1", old_ts, "backend-engineer", "sonnet", 9_000),
        ]
        conn = _make_conn(rows)
        plan = _make_plan([("backend-engineer", "sonnet")])
        fc = CostForecaster(conn, basis_window_days=60).forecast(plan)
        # Wide window includes the 25-day-old record
        assert fc.est_input_tokens + fc.est_output_tokens == 9_000
        conn.close()


class TestToDictRoundtrip:
    def test_to_dict_roundtrip(self) -> None:
        conn = _make_conn()
        plan = _make_plan([("backend-engineer", "sonnet"), ("test-engineer", "haiku")])
        fc = CostForecaster(conn).forecast(plan)
        d = fc.to_dict()
        assert isinstance(d, dict)
        # All required fields present
        for key in (
            "plan_id", "computed_at", "est_input_tokens", "est_output_tokens",
            "est_usd_low", "est_usd_mid", "est_usd_high",
            "basis_window_days", "sample_size", "breakdown",
        ):
            assert key in d, f"Missing key: {key}"
        # Round-trip via JSON
        restored = json.loads(json.dumps(d))
        assert restored["est_usd_mid"] == d["est_usd_mid"]
        assert len(restored["breakdown"]) == len(d["breakdown"])
        conn.close()


class TestCLIMarkdownRender:
    def test_cli_renders_markdown_on_default_plan(self, tmp_path: Path) -> None:
        """CLI handler renders markdown without crashing when given a valid plan."""
        import argparse

        from agent_baton.cli.commands.observe import forecast_cmd

        # Build a minimal plan.json
        plan = _make_plan([("backend-engineer", "sonnet")])
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

        args = argparse.Namespace(
            plan=str(plan_path),
            window=14,
            output_json=False,
            forecast_cmd="cost",
        )

        output_lines: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **kw: output_lines.append(" ".join(str(x) for x in a))):
            # No baton.db exists in tmp_path — forecaster falls back to defaults
            with patch(
                "agent_baton.cli.commands.observe.forecast_cmd._open_db",
                return_value=None,
            ):
                forecast_cmd.handler(args)

        rendered = "\n".join(output_lines)
        assert "Cost Forecast" in rendered
        assert "backend-engineer" in rendered
        assert "sonnet" in rendered
        assert "Confidence band" in rendered
