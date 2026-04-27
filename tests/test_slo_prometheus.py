"""Prometheus exposition tests for SLO metrics (O1.5 -> O1.6 hook)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.observe import prometheus as slo_prom
from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import DEFAULT_SLOS, SLOMeasurement


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "baton.db"
    s = SLOStore(p)
    s.list_definitions()  # force schema init before close
    s.close()
    return p


def _seed(db: Path) -> SLOStore:
    store = SLOStore(db)
    for s in DEFAULT_SLOS:
        store.upsert_definition(s)
    return store


class TestTextFallback:
    def test_render_emits_three_metric_blocks(self, db: Path) -> None:
        _seed(db)
        text = slo_prom.render_slo_metrics_text(db)
        assert "# TYPE agent_baton_slo_sli gauge" in text
        assert "# TYPE agent_baton_slo_target gauge" in text
        assert "# TYPE agent_baton_slo_error_budget_remaining gauge" in text

    def test_render_emits_one_series_per_slo(self, db: Path) -> None:
        _seed(db)
        text = slo_prom.render_slo_metrics_text(db)
        for s in DEFAULT_SLOS:
            assert f'agent_baton_slo_target{{name="{s.name}"}} {s.target}' in text

    def test_render_uses_latest_measurement(self, db: Path) -> None:
        store = _seed(db)
        store.insert_measurement(
            SLOMeasurement(
                slo_name="dispatch_success_rate",
                window_start="2026-04-01T00:00:00Z",
                window_end="2026-04-29T00:00:00Z",
                sli_value=0.997,
                target=0.99,
                is_meeting=True,
                error_budget_remaining_pct=0.7,
                computed_at="2026-04-29T00:00:00Z",
                sample_size=1000,
            )
        )
        text = slo_prom.render_slo_metrics_text(db)
        assert (
            'agent_baton_slo_sli{name="dispatch_success_rate"} 0.997'
            in text
        )
        assert (
            'agent_baton_slo_error_budget_remaining{name="dispatch_success_rate"} 0.7'
            in text
        )

    def test_empty_db_has_no_series(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.db"
        s = SLOStore(empty)
        s.list_definitions()  # force schema init
        s.close()
        text = slo_prom.render_slo_metrics_text(empty)
        # The HELP / TYPE preamble exists even with no SLOs defined.
        assert "agent_baton_slo_sli" in text
        # ...but no concrete labelled series.
        assert 'name="' not in text


class TestRegisterSLOMetrics:
    def test_register_with_real_prometheus_client(self, db: Path) -> None:
        prometheus_client = pytest.importorskip("prometheus_client")
        registry = prometheus_client.CollectorRegistry()
        store = _seed(db)
        store.insert_measurement(
            SLOMeasurement(
                slo_name="gate_pass_rate",
                window_start="2026-04-01T00:00:00Z",
                window_end="2026-04-29T00:00:00Z",
                sli_value=0.96,
                target=0.95,
                is_meeting=True,
                error_budget_remaining_pct=0.2,
                computed_at="2026-04-29T00:00:00Z",
                sample_size=50,
            )
        )
        slo_prom.register_slo_metrics(registry, db)
        sli = registry.get_sample_value(
            "agent_baton_slo_sli", labels={"name": "gate_pass_rate"}
        )
        assert sli == pytest.approx(0.96)
        budget = registry.get_sample_value(
            "agent_baton_slo_error_budget_remaining",
            labels={"name": "gate_pass_rate"},
        )
        assert budget == pytest.approx(0.2)
