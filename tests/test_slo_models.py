"""Unit tests for the SLO data models (O1.5)."""
from __future__ import annotations

from agent_baton.models.slo import (
    DEFAULT_SLOS,
    ErrorBudgetBurn,
    SLODefinition,
    SLOMeasurement,
)


# ---------------------------------------------------------------------------
# SLODefinition
# ---------------------------------------------------------------------------


class TestSLODefinitionRoundtrip:
    def test_roundtrip_preserves_all_fields(self) -> None:
        d = SLODefinition(
            name="dispatch_success_rate",
            sli_query="dispatch_success_rate",
            target=0.99,
            window_days=28,
            description="canonical",
        )
        restored = SLODefinition.from_dict(d.to_dict())
        assert restored == d

    def test_from_dict_uses_defaults(self) -> None:
        d = SLODefinition.from_dict(
            {"name": "x", "sli_query": "gate_pass_rate", "target": 0.9}
        )
        assert d.window_days == 28
        assert d.description == ""

    def test_target_coerced_to_float(self) -> None:
        d = SLODefinition.from_dict(
            {"name": "x", "sli_query": "gate_pass_rate", "target": 1, "window_days": "7"}
        )
        assert isinstance(d.target, float)
        assert d.window_days == 7


# ---------------------------------------------------------------------------
# SLOMeasurement
# ---------------------------------------------------------------------------


class TestSLOMeasurementRoundtrip:
    def test_roundtrip_preserves_all_fields(self) -> None:
        m = SLOMeasurement(
            slo_name="dispatch_success_rate",
            window_start="2026-04-01T00:00:00Z",
            window_end="2026-04-29T00:00:00Z",
            sli_value=0.991,
            target=0.99,
            is_meeting=True,
            error_budget_remaining_pct=0.1,
            computed_at="2026-04-29T00:00:00Z",
            sample_size=512,
        )
        assert SLOMeasurement.from_dict(m.to_dict()) == m

    def test_defaults(self) -> None:
        m = SLOMeasurement.from_dict(
            {
                "slo_name": "x",
                "sli_value": 0.5,
                "target": 0.99,
                "is_meeting": False,
                "error_budget_remaining_pct": 0.0,
                "computed_at": "2026-04-29T00:00:00Z",
            }
        )
        assert m.sample_size == 0
        assert m.window_start == ""


# ---------------------------------------------------------------------------
# ErrorBudgetBurn
# ---------------------------------------------------------------------------


class TestErrorBudgetBurnRoundtrip:
    def test_roundtrip_with_incident(self) -> None:
        b = ErrorBudgetBurn(
            slo_name="dispatch_success_rate",
            burn_rate=0.1,
            budget_consumed_pct=0.4,
            started_at="2026-04-29T01:00:00Z",
            ended_at="2026-04-29T05:00:00Z",
            incident_id="inc-123",
            id=42,
        )
        assert ErrorBudgetBurn.from_dict(b.to_dict()) == b

    def test_roundtrip_without_incident(self) -> None:
        b = ErrorBudgetBurn(
            slo_name="x",
            burn_rate=0.05,
            budget_consumed_pct=0.1,
            started_at="2026-04-29T01:00:00Z",
        )
        d = b.to_dict()
        assert "incident_id" not in d
        assert "id" not in d
        restored = ErrorBudgetBurn.from_dict(d)
        assert restored.slo_name == b.slo_name
        assert restored.incident_id is None
        assert restored.id is None

    def test_empty_string_incident_id_becomes_none(self) -> None:
        b = ErrorBudgetBurn.from_dict(
            {
                "slo_name": "x",
                "burn_rate": 0.0,
                "budget_consumed_pct": 0.0,
                "started_at": "2026-04-29T01:00:00Z",
                "incident_id": "",
            }
        )
        assert b.incident_id is None


# ---------------------------------------------------------------------------
# DEFAULT_SLOS
# ---------------------------------------------------------------------------


class TestDefaultSLOs:
    def test_default_slos_cover_canonical_signals(self) -> None:
        names = {s.name for s in DEFAULT_SLOS}
        assert names == {"dispatch_success_rate", "gate_pass_rate", "engine_uptime"}

    def test_default_targets_match_spec(self) -> None:
        by_name = {s.name: s for s in DEFAULT_SLOS}
        assert by_name["dispatch_success_rate"].target == 0.99
        assert by_name["gate_pass_rate"].target == 0.95
        assert by_name["engine_uptime"].target == 0.999
