"""Tests for agent_baton.core.incident.IncidentManager, IncidentTemplate, IncidentPhase."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.incident import IncidentManager, IncidentPhase, IncidentTemplate


# ---------------------------------------------------------------------------
# IncidentPhase — dataclass fields
# ---------------------------------------------------------------------------

class TestIncidentPhaseFields:
    def test_name_and_description_stored(self) -> None:
        phase = IncidentPhase(name="Triage", description="Assess scope")
        assert phase.name == "Triage"
        assert phase.description == "Assess scope"

    def test_optional_defaults(self) -> None:
        phase = IncidentPhase(name="p", description="d")
        assert phase.agents == []
        assert phase.gate == ""

    def test_agents_and_gate_stored(self) -> None:
        phase = IncidentPhase(
            name="Investigate",
            description="RCA",
            agents=["backend-engineer", "devops-engineer"],
            gate="root_cause_identified",
        )
        assert phase.agents == ["backend-engineer", "devops-engineer"]
        assert phase.gate == "root_cause_identified"


# ---------------------------------------------------------------------------
# IncidentTemplate — dataclass fields + to_markdown
# ---------------------------------------------------------------------------

class TestIncidentTemplateFields:
    def test_fields_stored(self) -> None:
        tmpl = IncidentTemplate(
            name="Critical Outage",
            description="P1 template",
            severity="P1",
        )
        assert tmpl.name == "Critical Outage"
        assert tmpl.description == "P1 template"
        assert tmpl.severity == "P1"
        assert tmpl.phases == []

    def test_default_severity(self) -> None:
        tmpl = IncidentTemplate(name="t", description="d")
        assert tmpl.severity == "P2"


class TestIncidentTemplateToMarkdown:
    def test_heading_present(self) -> None:
        tmpl = IncidentTemplate(name="My Template", description="desc", severity="P2")
        md = tmpl.to_markdown()
        assert "# Incident Template: My Template" in md

    def test_severity_present(self) -> None:
        tmpl = IncidentTemplate(name="t", description="d", severity="P1")
        md = tmpl.to_markdown()
        assert "P1" in md

    def test_phases_heading_present(self) -> None:
        tmpl = IncidentTemplate(name="t", description="d")
        assert "## Phases" in tmpl.to_markdown()

    def test_phase_names_present(self) -> None:
        tmpl = IncidentTemplate(
            name="t",
            description="d",
            phases=[
                IncidentPhase(name="Alpha", description="first"),
                IncidentPhase(name="Beta", description="second"),
            ],
        )
        md = tmpl.to_markdown()
        assert "Alpha" in md
        assert "Beta" in md

    def test_phase_agents_present(self) -> None:
        tmpl = IncidentTemplate(
            name="t",
            description="d",
            phases=[
                IncidentPhase(
                    name="Fix",
                    description="fix it",
                    agents=["backend-engineer"],
                )
            ],
        )
        md = tmpl.to_markdown()
        assert "backend-engineer" in md

    def test_phase_gate_present(self) -> None:
        tmpl = IncidentTemplate(
            name="t",
            description="d",
            phases=[
                IncidentPhase(
                    name="Verify",
                    description="verify it",
                    gate="verification_passed",
                )
            ],
        )
        md = tmpl.to_markdown()
        assert "verification_passed" in md


# ---------------------------------------------------------------------------
# IncidentManager.get_template — built-in templates
# ---------------------------------------------------------------------------

class TestGetTemplate:
    def test_p1_returns_five_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P1")
        assert len(tmpl.phases) == 5

    def test_p2_returns_four_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P2")
        assert len(tmpl.phases) == 4

    def test_p3_returns_three_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P3")
        assert len(tmpl.phases) == 3

    def test_p4_returns_two_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P4")
        assert len(tmpl.phases) == 2

    def test_unknown_severity_defaults_to_p2(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P99")
        assert tmpl.severity == "P2"

    def test_severity_case_insensitive(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("p1")
        assert len(tmpl.phases) == 5

    def test_p1_has_triage_phase(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P1")
        phase_names = [p.name for p in tmpl.phases]
        assert "Triage" in phase_names

    def test_p1_has_post_incident_report(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        tmpl = manager.get_template("P1")
        phase_names = [p.name for p in tmpl.phases]
        assert any("Post" in n or "Report" in n for n in phase_names)

    def test_all_templates_have_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        for sev in ("P1", "P2", "P3", "P4"):
            tmpl = manager.get_template(sev)
            assert len(tmpl.phases) > 0, f"{sev} template has no phases"


# ---------------------------------------------------------------------------
# IncidentManager.create_incident — create/load roundtrip
# ---------------------------------------------------------------------------

class TestCreateIncident:
    def test_create_returns_path(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        path = manager.create_incident("INC-001", "P2", "API returning 500 errors")
        assert path.exists()
        assert path.suffix == ".md"

    def test_create_creates_parent_dirs(self, tmp_path: Path) -> None:
        incidents_dir = tmp_path / "deep" / "incidents"
        manager = IncidentManager(incidents_dir)
        path = manager.create_incident("INC-001", "P2", "test")
        assert path.exists()

    def test_incident_content_contains_id(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-007", "P1", "Critical DB failure")
        content = manager.load_incident("INC-007")
        assert content is not None
        assert "INC-007" in content

    def test_incident_content_contains_severity(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-002", "P3", "Minor UI bug")
        content = manager.load_incident("INC-002")
        assert content is not None
        assert "P3" in content

    def test_incident_content_contains_description(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-003", "P2", "Payment service unresponsive")
        content = manager.load_incident("INC-003")
        assert content is not None
        assert "Payment service unresponsive" in content

    def test_incident_content_contains_template_phases(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-004", "P2", "desc")
        content = manager.load_incident("INC-004")
        assert content is not None
        assert "Investigate" in content
        assert "Verify" in content


# ---------------------------------------------------------------------------
# IncidentManager.list_incidents
# ---------------------------------------------------------------------------

class TestListIncidents:
    def test_empty_returns_empty_list(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        assert manager.list_incidents() == []

    def test_missing_dir_returns_empty_list(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path / "nonexistent")
        assert manager.list_incidents() == []

    def test_lists_created_incidents(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-A", "P2", "first")
        manager.create_incident("INC-B", "P3", "second")
        incidents = manager.list_incidents()
        stems = [p.stem for p in incidents]
        assert "INC-A" in stems
        assert "INC-B" in stems

    def test_list_count_matches_created(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        for i in range(4):
            manager.create_incident(f"INC-{i}", "P2", f"incident {i}")
        assert len(manager.list_incidents()) == 4


# ---------------------------------------------------------------------------
# IncidentManager.load_incident
# ---------------------------------------------------------------------------

class TestLoadIncident:
    def test_load_returns_none_for_missing_id(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        assert manager.load_incident("does-not-exist") is None

    def test_load_returns_string_for_existing(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-X", "P1", "desc")
        content = manager.load_incident("INC-X")
        assert isinstance(content, str)
        assert len(content) > 0
