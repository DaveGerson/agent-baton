"""Tests for the foresight engine and its integration with the planner.

Verifies that:
- ForesightEngine detects capability gaps, prerequisites, edge cases, and tooling needs.
- Matching inserts preparatory phases before the triggering phase.
- Duplicate rule matches are collapsed (one insertion per rule).
- Confidence thresholds interact correctly with risk levels.
- Non-matching plans pass through unchanged.
- Integration: IntelligentPlanner.create_plan() invokes foresight and records insights.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.foresight import ForesightEngine, ForesightRule, _BUILTIN_RULES
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.taxonomy import ForesightInsight, StepIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_phases(specs: list[tuple[str, str, str]]) -> list[PlanPhase]:
    """Build phases from (name, agent, description) tuples."""
    phases = []
    for i, (name, agent, desc) in enumerate(specs, start=1):
        step = PlanStep(
            step_id=f"{i}.1",
            agent_name=agent,
            task_description=desc,
        )
        phases.append(PlanPhase(phase_id=i, name=name, steps=[step]))
    return phases


def _make_agent_dir(tmp_path: Path) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in [
        "backend-engineer", "architect", "test-engineer", "code-reviewer",
        "data-engineer", "data-analyst", "devops-engineer",
    ]:
        content = (
            f"---\nname: {name}\ndescription: {name} specialist.\n"
            f"model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return agents_dir


# ---------------------------------------------------------------------------
# ForesightEngine unit tests
# ---------------------------------------------------------------------------

class TestForesightEngineBasics:
    """Core foresight rule matching and phase insertion."""

    def test_no_match_returns_unchanged(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Implement", "backend-engineer", "Add a simple utility function"),
        ])
        result, insights = engine.analyze(phases, "Add a simple utility function")
        assert len(insights) == 0
        assert len(result) == 1
        assert result[0].name == "Implement"

    def test_data_crud_gap_detected(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Design", "architect", "Design the data quality pipeline"),
            ("Implement", "data-engineer", "Update records and clean data for deduplication"),
        ])
        result, insights = engine.analyze(
            phases, "Build a data quality pipeline", existing_agents=["data-engineer"]
        )
        assert len(insights) >= 1
        crud_insight = next(
            (i for i in insights if i.source_rule == "foresight-data-crud"), None
        )
        assert crud_insight is not None
        assert crud_insight.category == "capability_gap"
        # Prep phase should be inserted before the Implement phase
        prep_phases = [p for p in result if p.name == "Prepare: Data Tooling"]
        assert len(prep_phases) == 1
        # Prep phase should come before the original Implement phase
        prep_idx = result.index(prep_phases[0])
        impl_idx = next(i for i, p in enumerate(result) if p.name == "Implement")
        assert prep_idx < impl_idx

    def test_migration_rollback_detected(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Design", "architect", "Design the database migration"),
            ("Implement", "backend-engineer", "Run the migration to drop column and move data"),
        ])
        result, insights = engine.analyze(phases, "Migrate the database schema")
        rollback = next(
            (i for i in insights if i.source_rule == "foresight-migration-rollback"), None
        )
        assert rollback is not None
        assert rollback.category == "prerequisite"

    def test_destructive_safety_detected(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Implement", "backend-engineer", "Delete all stale records and truncate logs"),
        ])
        result, insights = engine.analyze(phases, "Clean up stale data")
        safety = next(
            (i for i in insights if i.source_rule == "foresight-destructive-safety"), None
        )
        assert safety is not None
        assert safety.category == "edge_case"

    def test_api_schema_detected(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Implement", "backend-engineer", "Create new api endpoint for user profiles"),
        ])
        result, insights = engine.analyze(phases, "Add new api endpoint")
        schema = next(
            (i for i in insights if i.source_rule == "foresight-api-schema"), None
        )
        assert schema is not None
        assert schema.category == "prerequisite"

    def test_infra_env_detected(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Implement", "devops-engineer", "Deploy to kubernetes with terraform"),
        ])
        result, insights = engine.analyze(phases, "Set up infrastructure")
        infra = next(
            (i for i in insights if i.source_rule == "foresight-infra-env"), None
        )
        assert infra is not None
        assert infra.category == "tooling"

    def test_duplicate_rules_collapsed(self):
        """Same rule matching two steps should only insert one prep phase."""
        engine = ForesightEngine()
        phases = _make_phases([
            ("Phase1", "data-engineer", "Update records for data quality"),
            ("Phase2", "data-analyst", "Clean data and deduplicate records"),
        ])
        result, insights = engine.analyze(phases, "Data quality pipeline")
        crud_insights = [i for i in insights if i.source_rule == "foresight-data-crud"]
        assert len(crud_insights) == 1

    def test_phase_ids_renumbered(self):
        """After insertion, all phase IDs should be sequential."""
        engine = ForesightEngine()
        phases = _make_phases([
            ("Design", "architect", "Design the migration"),
            ("Implement", "backend-engineer", "Run the migration to alter table"),
        ])
        result, insights = engine.analyze(phases, "Database migration")
        ids = [p.phase_id for p in result]
        assert ids == list(range(1, len(result) + 1))
        # Step IDs should match their parent phase
        for phase in result:
            for step in phase.steps:
                assert step.step_id.startswith(f"{phase.phase_id}.")


class TestForesightConfidence:
    """Confidence threshold behavior."""

    def test_low_confidence_rule_skipped(self):
        low_conf_rule = ForesightRule(
            rule_id="test-low-conf",
            name="Low confidence rule",
            description="Should be skipped",
            trigger_keywords=["utility"],
            confidence=0.5,
        )
        engine = ForesightEngine(rules=[low_conf_rule], min_confidence=0.7)
        phases = _make_phases([
            ("Implement", "backend-engineer", "Add a utility function"),
        ])
        result, insights = engine.analyze(phases, "utility")
        assert len(insights) == 0

    def test_high_risk_lowers_threshold(self):
        """HIGH risk should lower the threshold so marginal rules fire."""
        rule = ForesightRule(
            rule_id="test-marginal",
            name="Marginal rule",
            description="Fires at lower threshold",
            trigger_keywords=["utility"],
            confidence=0.6,
        )
        engine = ForesightEngine(rules=[rule], min_confidence=0.7)
        phases = _make_phases([
            ("Implement", "backend-engineer", "Add a utility function"),
        ])
        # At LOW risk, 0.6 < 0.7 threshold → should not fire
        _, insights_low = engine.analyze(phases, "utility", risk_level="LOW")
        assert len(insights_low) == 0

        # At HIGH risk, threshold drops to 0.55 → 0.6 >= 0.55 → should fire
        phases2 = _make_phases([
            ("Implement", "backend-engineer", "Add a utility function"),
        ])
        _, insights_high = engine.analyze(phases2, "utility", risk_level="HIGH")
        assert len(insights_high) == 1


class TestForesightAgentResolution:
    """Agent resolution in foresight steps."""

    def test_flavored_agent_preferred(self):
        engine = ForesightEngine()
        phases = _make_phases([
            ("Implement", "data-engineer--python", "Update records and clean data"),
        ])
        result, insights = engine.analyze(
            phases,
            "Data quality pipeline",
            existing_agents=["data-engineer--python", "architect"],
        )
        if insights:
            # The prep step should use the flavored variant
            prep_phase = next(p for p in result if "Prepare" in p.name)
            agent = prep_phase.steps[0].agent_name
            assert agent == "data-engineer--python"


class TestForesightInsightSerialization:
    """ForesightInsight round-trip serialization."""

    def test_roundtrip(self):
        insight = ForesightInsight(
            category="capability_gap",
            description="Needs CRUD operations",
            resolution="Provision delete capability",
            inserted_phase_name="Prepare: Data Tooling",
            inserted_step_ids=["1.1"],
            confidence=0.85,
            source_rule="foresight-data-crud",
        )
        d = insight.to_dict()
        restored = ForesightInsight.from_dict(d)
        assert restored.category == "capability_gap"
        assert restored.description == "Needs CRUD operations"
        assert restored.confidence == 0.85
        assert restored.source_rule == "foresight-data-crud"
        assert restored.inserted_step_ids == ["1.1"]


# ---------------------------------------------------------------------------
# Planner integration test
# ---------------------------------------------------------------------------

class TestForesightPlannerIntegration:
    """Verify foresight runs during IntelligentPlanner.create_plan()."""

    @pytest.fixture()
    def agents_dir(self, tmp_path: Path) -> Path:
        return _make_agent_dir(tmp_path)

    @pytest.fixture()
    def ctx(self, tmp_path: Path) -> Path:
        d = tmp_path / "team-context"
        d.mkdir()
        return d

    def test_foresight_insights_recorded_on_plan(self, agents_dir, ctx, monkeypatch):
        monkeypatch.setenv("AGENT_BATON_AGENTS_DIR", str(agents_dir))
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.engine.classifier import KeywordClassifier

        planner = IntelligentPlanner(
            team_context_root=ctx,
            task_classifier=KeywordClassifier(),
        )
        plan = planner.create_plan(
            "Build a data quality pipeline to update records and clean data for deduplication",
            task_type="new-feature",
            agents=["data-engineer", "architect"],
        )
        # The plan should have foresight_insights
        assert hasattr(plan, "foresight_insights")
        assert isinstance(plan.foresight_insights, list)
        # With data quality + dedup keywords and data-engineer, the CRUD rule should fire
        crud = [i for i in plan.foresight_insights if i.source_rule == "foresight-data-crud"]
        assert len(crud) >= 1
        # The insight should be in the explain output
        explanation = planner.explain_plan(plan)
        assert "Foresight Insights" in explanation
        assert "foresight-data-crud" in explanation

    def test_plan_without_foresight_triggers(self, agents_dir, ctx, monkeypatch):
        monkeypatch.setenv("AGENT_BATON_AGENTS_DIR", str(agents_dir))
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.engine.classifier import KeywordClassifier

        planner = IntelligentPlanner(
            team_context_root=ctx,
            task_classifier=KeywordClassifier(),
        )
        plan = planner.create_plan(
            "Add a simple helper function to utils.py",
            task_type="new-feature",
            agents=["backend-engineer"],
        )
        assert plan.foresight_insights == []
        explanation = planner.explain_plan(plan)
        assert "self-contained" in explanation

    def test_foresight_insights_serialized_in_plan_dict(self, agents_dir, ctx, monkeypatch):
        monkeypatch.setenv("AGENT_BATON_AGENTS_DIR", str(agents_dir))
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.engine.classifier import KeywordClassifier

        planner = IntelligentPlanner(
            team_context_root=ctx,
            task_classifier=KeywordClassifier(),
        )
        plan = planner.create_plan(
            "Migrate database schema and drop column",
            task_type="migration",
            agents=["backend-engineer", "architect"],
        )
        d = plan.to_dict()
        assert "foresight_insights" in d
        restored = MachinePlan.from_dict(d)
        assert len(restored.foresight_insights) == len(plan.foresight_insights)
        for orig, rest in zip(plan.foresight_insights, restored.foresight_insights):
            assert orig.source_rule == rest.source_rule
            assert orig.category == rest.category
