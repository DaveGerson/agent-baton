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
    # Decision: 6 individual substring-presence tests collapsed into one
    # parametrized test.  Each tuple tests one rendering concern and fails
    # independently.
    @pytest.mark.parametrize("build_tmpl,expected_substring", [
        (
            lambda: IncidentTemplate(name="My Template", description="desc", severity="P2"),
            "# Incident Template: My Template",
        ),
        (
            lambda: IncidentTemplate(name="t", description="d", severity="P1"),
            "P1",
        ),
        (
            lambda: IncidentTemplate(name="t", description="d"),
            "## Phases",
        ),
        (
            lambda: IncidentTemplate(
                name="t", description="d",
                phases=[
                    IncidentPhase(name="Alpha", description="first"),
                    IncidentPhase(name="Beta",  description="second"),
                ],
            ),
            "Alpha",
        ),
        (
            lambda: IncidentTemplate(
                name="t", description="d",
                phases=[IncidentPhase(name="Fix", description="fix it",
                                      agents=["backend-engineer"])],
            ),
            "backend-engineer",
        ),
        (
            lambda: IncidentTemplate(
                name="t", description="d",
                phases=[IncidentPhase(name="Verify", description="verify it",
                                      gate="verification_passed")],
            ),
            "verification_passed",
        ),
    ])
    def test_markdown_contains(self, build_tmpl, expected_substring):
        assert expected_substring in build_tmpl().to_markdown()

    def test_both_phase_names_in_markdown(self) -> None:
        # Kept separate: ensures *both* names appear, not just "Alpha".
        tmpl = IncidentTemplate(
            name="t", description="d",
            phases=[
                IncidentPhase(name="Alpha", description="first"),
                IncidentPhase(name="Beta",  description="second"),
            ],
        )
        md = tmpl.to_markdown()
        assert "Alpha" in md
        assert "Beta" in md


# ---------------------------------------------------------------------------
# IncidentManager.get_template — built-in templates
# ---------------------------------------------------------------------------

class TestGetTemplate:
    # Decision: 4 severity→phase-count tests collapsed into one parametrized
    # test.  Each tuple is an independent boundary: P1→5, P2→4, P3→3, P4→2.
    @pytest.mark.parametrize("severity,expected_phases", [
        ("P1", 5),
        ("P2", 4),
        ("P3", 3),
        ("P4", 2),
    ])
    def test_severity_phase_count(self, tmp_path: Path, severity, expected_phases):
        assert len(IncidentManager(tmp_path).get_template(severity).phases) == expected_phases

    def test_unknown_severity_defaults_to_p2(self, tmp_path: Path) -> None:
        tmpl = IncidentManager(tmp_path).get_template("P99")
        assert tmpl.severity == "P2"

    def test_severity_case_insensitive(self, tmp_path: Path) -> None:
        assert len(IncidentManager(tmp_path).get_template("p1").phases) == 5

    def test_p1_has_triage_phase(self, tmp_path: Path) -> None:
        phase_names = [p.name for p in IncidentManager(tmp_path).get_template("P1").phases]
        assert "Triage" in phase_names

    def test_p1_has_post_incident_report(self, tmp_path: Path) -> None:
        phase_names = [p.name for p in IncidentManager(tmp_path).get_template("P1").phases]
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
        path = IncidentManager(tmp_path).create_incident("INC-001", "P2", "API returning 500 errors")
        assert path.exists()
        assert path.suffix == ".md"

    def test_create_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = IncidentManager(tmp_path / "deep" / "incidents").create_incident("INC-001", "P2", "test")
        assert path.exists()

    # Decision: 4 content-contains tests collapsed into one parameterized test.
    # Each incident is created with a distinct id/severity/description to make
    # each failure traceable, yet they all exercise the same "create then load"
    # code path.
    @pytest.mark.parametrize("inc_id,severity,description,expected", [
        ("INC-007", "P1", "Critical DB failure",        "INC-007"),
        ("INC-002", "P3", "Minor UI bug",               "P3"),
        ("INC-003", "P2", "Payment service unresponsive","Payment service unresponsive"),
    ])
    def test_incident_content_contains(self, tmp_path: Path,
                                       inc_id, severity, description, expected):
        manager = IncidentManager(tmp_path / inc_id)
        manager.create_incident(inc_id, severity, description)
        content = manager.load_incident(inc_id)
        assert content is not None
        assert expected in content

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
        assert IncidentManager(tmp_path).list_incidents() == []

    def test_missing_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert IncidentManager(tmp_path / "nonexistent").list_incidents() == []

    def test_lists_created_incidents(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-A", "P2", "first")
        manager.create_incident("INC-B", "P3", "second")
        stems = [p.stem for p in manager.list_incidents()]
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
        assert IncidentManager(tmp_path).load_incident("does-not-exist") is None

    def test_load_returns_string_for_existing(self, tmp_path: Path) -> None:
        manager = IncidentManager(tmp_path)
        manager.create_incident("INC-X", "P1", "desc")
        content = manager.load_incident("INC-X")
        assert isinstance(content, str)
        assert len(content) > 0
