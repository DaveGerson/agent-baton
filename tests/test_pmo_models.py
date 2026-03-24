"""Tests for agent_baton.models.pmo — data model classes."""
from __future__ import annotations

import pytest

from agent_baton.models.pmo import (
    PMO_COLUMNS,
    PmoCard,
    PmoConfig,
    PmoProject,
    PmoSignal,
    ProgramHealth,
    status_to_column,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(**kwargs) -> PmoProject:
    defaults = dict(
        project_id="nds",
        name="NDS Project",
        path="/srv/nds",
        program="NDS",
    )
    defaults.update(kwargs)
    return PmoProject(**defaults)


def _card(**kwargs) -> PmoCard:
    defaults = dict(
        card_id="task-001",
        project_id="nds",
        program="NDS",
        title="Build the thing",
        column="executing",
    )
    defaults.update(kwargs)
    return PmoCard(**defaults)


def _signal(**kwargs) -> PmoSignal:
    defaults = dict(
        signal_id="sig-001",
        signal_type="bug",
        title="Login fails on Safari",
    )
    defaults.update(kwargs)
    return PmoSignal(**defaults)


def _health(**kwargs) -> ProgramHealth:
    defaults = dict(program="NDS")
    defaults.update(kwargs)
    return ProgramHealth(**defaults)


# ---------------------------------------------------------------------------
# PMO_COLUMNS
# ---------------------------------------------------------------------------

class TestPmoColumns:
    def test_contains_six_columns(self):
        assert len(PMO_COLUMNS) == 6

    def test_contains_queued(self):
        assert "queued" in PMO_COLUMNS

    def test_contains_planning(self):
        assert "planning" in PMO_COLUMNS

    def test_contains_executing(self):
        assert "executing" in PMO_COLUMNS

    def test_contains_awaiting_human(self):
        assert "awaiting_human" in PMO_COLUMNS

    def test_contains_validating(self):
        assert "validating" in PMO_COLUMNS

    def test_contains_deployed(self):
        assert "deployed" in PMO_COLUMNS

    def test_order_is_logical_lifecycle(self):
        # queued must come before deployed
        assert PMO_COLUMNS.index("queued") < PMO_COLUMNS.index("deployed")


# ---------------------------------------------------------------------------
# status_to_column
# ---------------------------------------------------------------------------

class TestStatusToColumn:
    @pytest.mark.parametrize("status,expected", [
        ("running",           "executing"),
        ("gate_pending",      "validating"),
        ("approval_pending",  "awaiting_human"),
        ("complete",          "deployed"),
        ("failed",            "executing"),
    ])
    def test_known_statuses(self, status: str, expected: str):
        assert status_to_column(status) == expected

    def test_none_returns_queued(self):
        assert status_to_column(None) == "queued"

    def test_unknown_status_returns_executing(self):
        assert status_to_column("some_unknown_state") == "executing"

    def test_all_results_are_valid_columns(self):
        statuses = ["running", "gate_pending", "approval_pending",
                    "complete", "failed", None]
        for s in statuses:
            assert status_to_column(s) in PMO_COLUMNS


# ---------------------------------------------------------------------------
# PmoProject
# ---------------------------------------------------------------------------

class TestPmoProject:
    def test_roundtrip_is_identity(self):
        p = _project(
            project_id="atl",
            name="ATL Project",
            path="/srv/atl",
            program="ATL",
            color="blue",
            description="Main ATL repo",
            registered_at="2026-01-01T00:00:00+00:00",
            ado_project="ATL-ADO",
        )
        assert PmoProject.from_dict(p.to_dict()) == p

    def test_to_dict_then_from_dict_then_to_dict_is_stable(self):
        p = _project()
        first = p.to_dict()
        second = PmoProject.from_dict(first).to_dict()
        assert first == second

    def test_default_color_is_empty_string(self):
        p = _project()
        assert p.color == ""

    def test_default_description_is_empty_string(self):
        p = _project()
        assert p.description == ""

    def test_default_registered_at_is_empty_string(self):
        p = _project()
        assert p.registered_at == ""

    def test_default_ado_project_is_empty_string(self):
        p = _project()
        assert p.ado_project == ""

    def test_from_dict_uses_defaults_for_optional_keys(self):
        p = PmoProject.from_dict({
            "project_id": "x",
            "name": "X",
            "path": "/x",
            "program": "X",
        })
        assert p.color == ""
        assert p.description == ""
        assert p.registered_at == ""
        assert p.ado_project == ""

    def test_to_dict_contains_all_fields(self):
        p = _project(color="red", description="desc", registered_at="ts", ado_project="ADO")
        d = p.to_dict()
        assert d["project_id"] == "nds"
        assert d["name"] == "NDS Project"
        assert d["path"] == "/srv/nds"
        assert d["program"] == "NDS"
        assert d["color"] == "red"
        assert d["description"] == "desc"
        assert d["registered_at"] == "ts"
        assert d["ado_project"] == "ADO"


# ---------------------------------------------------------------------------
# PmoCard
# ---------------------------------------------------------------------------

class TestPmoCard:
    def test_roundtrip_is_identity(self):
        c = _card(
            card_id="task-abc",
            project_id="nds",
            program="NDS",
            title="Deploy API",
            column="deployed",
            risk_level="HIGH",
            priority=2,
            agents=["backend-engineer", "test-engineer"],
            steps_completed=3,
            steps_total=5,
            gates_passed=1,
            current_phase="Testing",
            error="",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
            external_id="ADO-123",
        )
        assert PmoCard.from_dict(c.to_dict()) == c

    def test_to_dict_then_from_dict_then_to_dict_is_stable(self):
        c = _card()
        first = c.to_dict()
        second = PmoCard.from_dict(first).to_dict()
        assert first == second

    def test_default_risk_level_is_low(self):
        assert _card().risk_level == "LOW"

    def test_default_priority_is_zero(self):
        assert _card().priority == 0

    def test_default_agents_is_empty_list(self):
        assert _card().agents == []

    def test_default_steps_completed_is_zero(self):
        assert _card().steps_completed == 0

    def test_default_steps_total_is_zero(self):
        assert _card().steps_total == 0

    def test_default_gates_passed_is_zero(self):
        assert _card().gates_passed == 0

    def test_default_current_phase_is_empty_string(self):
        assert _card().current_phase == ""

    def test_default_error_is_empty_string(self):
        assert _card().error == ""

    def test_default_external_id_is_empty_string(self):
        assert _card().external_id == ""

    def test_from_dict_uses_defaults_for_optional_keys(self):
        c = PmoCard.from_dict({
            "card_id": "t1",
            "project_id": "p1",
            "program": "X",
            "title": "T",
            "column": "queued",
        })
        assert c.risk_level == "LOW"
        assert c.priority == 0
        assert c.agents == []
        assert c.steps_completed == 0
        assert c.steps_total == 0
        assert c.gates_passed == 0
        assert c.current_phase == ""
        assert c.error == ""
        assert c.external_id == ""

    def test_agents_list_survives_roundtrip(self):
        c = _card(agents=["architect", "test-engineer"])
        assert PmoCard.from_dict(c.to_dict()).agents == ["architect", "test-engineer"]


# ---------------------------------------------------------------------------
# PmoSignal
# ---------------------------------------------------------------------------

class TestPmoSignal:
    def test_roundtrip_is_identity(self):
        s = _signal(
            signal_id="sig-xyz",
            signal_type="escalation",
            title="Pipeline blocked",
            description="CI is red",
            source_project_id="nds",
            severity="critical",
            status="triaged",
            created_at="2026-01-01T00:00:00+00:00",
            resolved_at="",
            forge_task_id="task-999",
        )
        assert PmoSignal.from_dict(s.to_dict()) == s

    def test_to_dict_then_from_dict_then_to_dict_is_stable(self):
        s = _signal()
        first = s.to_dict()
        second = PmoSignal.from_dict(first).to_dict()
        assert first == second

    def test_default_description_is_empty_string(self):
        assert _signal().description == ""

    def test_default_source_project_id_is_empty_string(self):
        assert _signal().source_project_id == ""

    def test_default_severity_is_medium(self):
        assert _signal().severity == "medium"

    def test_default_status_is_open(self):
        assert _signal().status == "open"

    def test_default_created_at_is_empty_string(self):
        assert _signal().created_at == ""

    def test_default_resolved_at_is_empty_string(self):
        assert _signal().resolved_at == ""

    def test_default_forge_task_id_is_empty_string(self):
        assert _signal().forge_task_id == ""

    def test_from_dict_uses_defaults_for_optional_keys(self):
        s = PmoSignal.from_dict({
            "signal_id": "s1",
            "signal_type": "bug",
            "title": "T",
        })
        assert s.severity == "medium"
        assert s.status == "open"
        assert s.description == ""
        assert s.source_project_id == ""
        assert s.forge_task_id == ""


# ---------------------------------------------------------------------------
# ProgramHealth
# ---------------------------------------------------------------------------

class TestProgramHealth:
    def test_roundtrip_is_identity(self):
        h = ProgramHealth(
            program="ATL",
            total_plans=10,
            active=3,
            completed=5,
            blocked=1,
            failed=1,
            completion_pct=50.0,
        )
        assert ProgramHealth.from_dict(h.to_dict()) == h

    def test_to_dict_then_from_dict_then_to_dict_is_stable(self):
        h = _health()
        first = h.to_dict()
        second = ProgramHealth.from_dict(first).to_dict()
        assert first == second

    def test_defaults_are_zero(self):
        h = _health()
        assert h.total_plans == 0
        assert h.active == 0
        assert h.completed == 0
        assert h.blocked == 0
        assert h.failed == 0
        assert h.completion_pct == 0.0

    def test_from_dict_uses_defaults_for_missing_keys(self):
        h = ProgramHealth.from_dict({"program": "NDS"})
        assert h.total_plans == 0
        assert h.completion_pct == 0.0


# ---------------------------------------------------------------------------
# PmoConfig
# ---------------------------------------------------------------------------

class TestPmoConfig:
    def test_roundtrip_is_identity(self):
        config = PmoConfig(
            projects=[_project(project_id="nds"), _project(project_id="atl", name="ATL", path="/atl", program="ATL")],
            programs=["NDS", "ATL"],
            signals=[_signal(signal_id="s1"), _signal(signal_id="s2", signal_type="blocker", title="B")],
            version="2",
        )
        restored = PmoConfig.from_dict(config.to_dict())
        assert restored.version == "2"
        assert len(restored.projects) == 2
        assert len(restored.signals) == 2
        assert restored.programs == ["NDS", "ATL"]

    def test_to_dict_then_from_dict_then_to_dict_is_stable(self):
        config = PmoConfig(
            projects=[_project()],
            signals=[_signal()],
        )
        first = config.to_dict()
        second = PmoConfig.from_dict(first).to_dict()
        assert first == second

    def test_empty_config_defaults(self):
        config = PmoConfig()
        assert config.projects == []
        assert config.programs == []
        assert config.signals == []
        assert config.version == "1"

    def test_from_dict_empty_dict_gives_defaults(self):
        config = PmoConfig.from_dict({})
        assert config.projects == []
        assert config.programs == []
        assert config.signals == []
        assert config.version == "1"

    def test_nested_projects_are_pmo_project_instances(self):
        config = PmoConfig.from_dict({
            "projects": [{"project_id": "x", "name": "X", "path": "/x", "program": "X"}],
        })
        assert isinstance(config.projects[0], PmoProject)

    def test_nested_signals_are_pmo_signal_instances(self):
        config = PmoConfig.from_dict({
            "signals": [{"signal_id": "s1", "signal_type": "bug", "title": "T"}],
        })
        assert isinstance(config.signals[0], PmoSignal)

    def test_to_dict_projects_are_dicts(self):
        config = PmoConfig(projects=[_project()])
        d = config.to_dict()
        assert isinstance(d["projects"][0], dict)

    def test_to_dict_signals_are_dicts(self):
        config = PmoConfig(signals=[_signal()])
        d = config.to_dict()
        assert isinstance(d["signals"][0], dict)

    def test_programs_list_survives_roundtrip(self):
        config = PmoConfig(programs=["NDS", "ATL", "CORE"])
        assert PmoConfig.from_dict(config.to_dict()).programs == ["NDS", "ATL", "CORE"]
