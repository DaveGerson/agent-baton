"""Quality fix #1 regression tests: structured-spec phase title preservation.

The plan-explosion incident
(docs/internal/competitive-audit/INCIDENT-plan-explosion.md) happened
in part because the planner detected "Phase 1: ... / Phase 2: ..."
structure but threw away the phase titles, producing phases named
just "Phase 1" / "Phase 2".  Operators couldn't correlate baton
phases with their spec phases and ended up dispatching each spec line
as its own plan.

These tests pin the new behavior: when the summary contains "Phase N:
Title" headers, the resulting plan phases keep the title.
"""
from __future__ import annotations

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.structured_spec import (
    enrich_phase_titles,
    extract_phase_titles,
)


class TestExtractPhaseTitles:
    def test_extracts_three_titles_from_phase_headers(self) -> None:
        summary = (
            "Phase 1: Authentication — implement OAuth callback. "
            "Phase 2: Authorization — token validation and RBAC. "
            "Phase 3: Tenancy — per-org isolation."
        )
        titles = extract_phase_titles(summary)
        assert [n for n, _ in titles] == ["1", "2", "3"]
        assert [t for _, t in titles] == [
            "Authentication",
            "Authorization",
            "Tenancy",
        ]

    def test_unstructured_summary_returns_empty(self) -> None:
        assert extract_phase_titles("Fix the login bug.") == []

    def test_dotted_phase_numbers_supported(self) -> None:
        summary = "Phase 1.1: Setup. Phase 1.2: Migrate. Phase 2.1: Cutover."
        titles = extract_phase_titles(summary)
        assert ("1.1", "Setup") in titles
        assert ("1.2", "Migrate") in titles
        assert ("2.1", "Cutover") in titles

    def test_step_keyword_also_recognized(self) -> None:
        titles = extract_phase_titles("Step 1: Build. Step 2: Test.")
        assert titles == [("1", "Build"), ("2", "Test")]


class TestEnrichPhaseTitles:
    def test_replaces_generic_phase_n_with_extracted_title(self) -> None:
        phases = [
            {"name": "Phase 1", "agents": []},
            {"name": "Phase 2", "agents": []},
        ]
        summary = "Phase 1: Authentication — do the thing. Phase 2: Tenancy — split orgs."
        out = enrich_phase_titles(phases, summary)
        assert out[0]["name"] == "Phase 1: Authentication"
        assert out[1]["name"] == "Phase 2: Tenancy"

    def test_empty_phase_list_returns_empty(self) -> None:
        assert enrich_phase_titles([], "Phase 1: foo") == []

    def test_no_titles_in_summary_leaves_phases_unchanged(self) -> None:
        phases = [{"name": "Phase 1", "agents": []}]
        out = enrich_phase_titles(phases, "Just a plain summary.")
        assert out[0]["name"] == "Phase 1"


class TestPlanQualityFix:
    """End-to-end: structured-spec summaries produce titled phases."""

    def test_structured_spec_produces_titled_phases(self) -> None:
        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Phase 1: Authentication — OAuth callback. "
            "Phase 2: Authorization — token validation. "
            "Phase 3: Tenancy — per-org isolation."
        )
        names = [p.name for p in plan.phases]
        # Every phase must carry its spec title — never just "Phase N".
        for n in names:
            assert ":" in n or n.lower() in {"design", "implement", "test", "review"}, (
                f"Phase {n!r} lost its spec title — plan-explosion regression"
            )
        # And the three titles should all be present.
        joined = " | ".join(names).lower()
        assert "authentication" in joined
        assert "authorization" in joined
        assert "tenancy" in joined

    def test_unstructured_summary_uses_default_phases(self) -> None:
        planner = IntelligentPlanner()
        plan = planner.create_plan("Add a hello-world endpoint")
        names = {p.name for p in plan.phases}
        # Should fall back to the default new-feature phase template.
        assert names & {"Design", "Implement", "Test", "Review"}
