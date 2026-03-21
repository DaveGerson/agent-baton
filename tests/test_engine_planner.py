"""Tests for agent_baton.core.engine.planner.IntelligentPlanner."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.planner import (
    IntelligentPlanner,
    _DEFAULT_AGENTS,
    _PHASE_NAMES,
    _TASK_TYPE_KEYWORDS,
)
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep
from agent_baton.models.pattern import LearnedPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pattern(
    pattern_id: str = "new-feature-001",
    task_type: str = "new-feature",
    recommended_agents: list[str] | None = None,
    confidence: float = 0.85,
    success_rate: float = 0.9,
    sample_size: int = 10,
    recommended_template: str = "phased delivery",
) -> LearnedPattern:
    return LearnedPattern(
        pattern_id=pattern_id,
        task_type=task_type,
        stack=None,
        recommended_template=recommended_template,
        recommended_agents=recommended_agents or ["architect", "backend-engineer"],
        confidence=confidence,
        sample_size=sample_size,
        success_rate=success_rate,
        avg_token_cost=120_000,
        evidence=["task-1", "task-2"],
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


@pytest.fixture
def tmp_agents_dir(tmp_path: Path) -> Path:
    """Create a minimal agents directory for the registry."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    agents = [
        ("backend-engineer--python", "Python backend specialist.", "sonnet"),
        ("architect", "System design specialist.", "opus"),
        ("test-engineer", "Testing specialist.", "sonnet"),
        ("code-reviewer", "Code review specialist.", "opus"),
        ("data-analyst", "Data analysis specialist.", "sonnet"),
        ("auditor", "Audit and compliance specialist.", "opus"),
        ("backend-engineer", "Generic backend engineer.", "sonnet"),
    ]
    for name, desc, model in agents:
        content = (
            f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\n"
            f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")

    return agents_dir


@pytest.fixture
def planner(tmp_path: Path, tmp_agents_dir: Path) -> IntelligentPlanner:
    """An IntelligentPlanner with a temp team-context and agent registry."""
    ctx = tmp_path / "team-context"
    ctx.mkdir()
    p = IntelligentPlanner(team_context_root=ctx)
    # Re-load the registry from our temp dir so routing works
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    reg = AgentRegistry()
    reg.load_directory(tmp_agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


@pytest.fixture
def python_project(tmp_path: Path) -> Path:
    """A fake Python project root with pyproject.toml."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\n', encoding="utf-8"
    )
    return project


# ---------------------------------------------------------------------------
# Task-ID generation
# ---------------------------------------------------------------------------

class TestGenerateTaskId:
    def test_format_has_date_prefix(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("Build a widget API")
        # Format: YYYY-MM-DD-slug
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}-", tid)

    def test_slug_is_lowercased(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("Build Widget API")
        assert tid == tid.lower()

    def test_spaces_become_hyphens(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("build widget api")
        assert "build-widget-api" in tid

    def test_special_characters_removed(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("Add @user's profile endpoint!")
        assert "@" not in tid
        assert "'" not in tid
        assert "!" not in tid

    def test_empty_summary_returns_date_only(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("")
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", tid)

    def test_long_summary_truncated(self, planner: IntelligentPlanner):
        long_summary = "word " * 30  # 150 chars
        tid = planner._generate_task_id(long_summary)
        # slug portion should be <= 50 chars
        slug = tid[len("YYYY-MM-DD-"):]  # approximate
        assert len(tid) <= 65  # date(10) + dash(1) + slug(50) + trailing hyphen removal


# ---------------------------------------------------------------------------
# Task type inference
# ---------------------------------------------------------------------------

class TestInferTaskType:
    @pytest.mark.parametrize("summary,expected", [
        ("fix the login bug", "bug-fix"),
        ("broken auth endpoint", "bug-fix"),
        ("there is an error in signup", "bug-fix"),
        ("add OAuth2 login", "new-feature"),
        ("build a new dashboard", "data-analysis"),
        ("create the user API", "new-feature"),
        ("refactor the payment service", "refactor"),
        ("clean up helper utilities", "refactor"),
        ("reorganize the models directory", "refactor"),
        ("analyze user retention data", "data-analysis"),
        ("generate a monthly report", "data-analysis"),
        ("write the API readme", "documentation"),
        ("update the spec document", "documentation"),
        ("migrate the database to Postgres", "migration"),
        ("upgrade the ORM version", "migration"),
    ])
    def test_keyword_matching(self, planner: IntelligentPlanner, summary: str, expected: str):
        assert planner._infer_task_type(summary) == expected

    def test_unknown_summary_defaults_to_new_feature(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("something completely different") == "new-feature"

    def test_case_insensitive(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("FIX THE BUG") == "bug-fix"

    def test_bug_fix_beats_new_feature_ordering(self, planner: IntelligentPlanner):
        # "fix" matches bug-fix which comes before new-feature in the keyword list
        result = planner._infer_task_type("fix and add new feature")
        assert result == "bug-fix"


# ---------------------------------------------------------------------------
# Default phases
# ---------------------------------------------------------------------------

class TestDefaultPhases:
    def test_new_feature_has_four_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("new-feature", ["architect", "backend-engineer"])
        assert len(phases) == 4

    def test_new_feature_phase_names(self, planner: IntelligentPlanner):
        phases = planner._default_phases("new-feature", ["architect"])
        names = [p.name for p in phases]
        assert names == ["Design", "Implement", "Test", "Review"]

    def test_bug_fix_has_three_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("bug-fix", ["backend-engineer"])
        assert len(phases) == 3
        assert [p.name for p in phases] == ["Investigate", "Fix", "Test"]

    def test_refactor_has_four_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("refactor", ["backend-engineer"])
        assert len(phases) == 4

    def test_data_analysis_has_three_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("data-analysis", ["data-analyst"])
        assert [p.name for p in phases] == ["Design", "Implement", "Review"]

    def test_documentation_has_three_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("documentation", [])
        assert [p.name for p in phases] == ["Research", "Draft", "Review"]

    def test_migration_has_four_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("migration", ["architect", "backend-engineer"])
        assert len(phases) == 4

    def test_every_phase_has_at_least_one_step(self, planner: IntelligentPlanner):
        phases = planner._default_phases("new-feature", ["architect", "backend-engineer"])
        for phase in phases:
            assert len(phase.steps) >= 1

    def test_steps_have_unique_step_ids(self, planner: IntelligentPlanner):
        phases = planner._default_phases(
            "new-feature",
            ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
        )
        step_ids = [s.step_id for p in phases for s in p.steps]
        assert len(step_ids) == len(set(step_ids))

    def test_empty_agents_still_produces_phases(self, planner: IntelligentPlanner):
        phases = planner._default_phases("new-feature", [])
        assert len(phases) == 4
        for phase in phases:
            assert len(phase.steps) >= 1

    def test_unknown_task_type_falls_back_to_defaults(self, planner: IntelligentPlanner):
        phases = planner._default_phases("unknown-type", ["backend-engineer"])
        assert len(phases) > 0


# ---------------------------------------------------------------------------
# Default gates
# ---------------------------------------------------------------------------

class TestDefaultGate:
    def test_implement_phase_gets_build_gate(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Implement")
        assert gate is not None
        assert gate.gate_type == "build"

    def test_fix_phase_gets_build_gate(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Fix")
        assert gate is not None
        assert gate.gate_type == "build"

    def test_test_phase_gets_test_gate(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Test")
        assert gate is not None
        assert gate.gate_type == "test"

    def test_review_phase_has_no_gate(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Review")
        assert gate is None

    def test_design_phase_has_no_gate(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Design")
        assert gate is None

    def test_implement_gate_has_pytest_command(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Implement")
        assert gate is not None
        assert "pytest" in gate.command

    def test_test_gate_has_coverage_flag(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Test")
        assert gate is not None
        assert "--cov" in gate.command


# ---------------------------------------------------------------------------
# create_plan — minimal input
# ---------------------------------------------------------------------------

class TestCreatePlanMinimal:
    def test_returns_machine_plan(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert isinstance(plan, MachinePlan)

    def test_task_summary_preserved(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.task_summary == "Add user authentication"

    def test_task_id_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.task_id
        assert "add-user-authentication" in plan.task_id

    def test_plan_has_phases(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert len(plan.phases) > 0

    def test_plan_has_steps(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.total_steps > 0

    def test_risk_level_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Deploy to production")
        assert plan.risk_level == "HIGH"

    def test_low_risk_task(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Rename a helper function")
        assert plan.risk_level == "LOW"

    def test_budget_tier_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.budget_tier in ("lean", "standard", "full")

    def test_shared_context_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.shared_context
        assert "Add user authentication" in plan.shared_context

    def test_created_at_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.created_at

    def test_git_strategy_is_set(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        # Enum values are "Commit-per-agent" / "Branch-per-agent"
        assert plan.git_strategy.lower() in ("commit-per-agent", "branch-per-agent")


# ---------------------------------------------------------------------------
# create_plan — task_type override
# ---------------------------------------------------------------------------

class TestCreatePlanTaskTypeOverride:
    def test_override_task_type_controls_phases(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Do something ambiguous",
            task_type="bug-fix",
        )
        phase_names = [p.name for p in plan.phases]
        assert phase_names == ["Investigate", "Fix", "Test"]

    def test_override_documentation_type(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Write the feature spec",
            task_type="documentation",
        )
        phase_names = [p.name for p in plan.phases]
        assert phase_names == ["Research", "Draft", "Review"]


# ---------------------------------------------------------------------------
# create_plan — agent override
# ---------------------------------------------------------------------------

class TestCreatePlanAgentOverride:
    def test_explicit_agents_are_used(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Add user authentication",
            agents=["architect", "backend-engineer"],
        )
        all_agent_names = plan.all_agents
        # architect should appear; backend-engineer may be routed to a flavored
        # variant depending on the detected stack (e.g. backend-engineer--python)
        assert any(a == "architect" or a.startswith("architect") for a in all_agent_names)
        assert any(
            a == "backend-engineer" or a.startswith("backend-engineer")
            for a in all_agent_names
        )

    def test_explicit_agents_skip_pattern(self, planner: IntelligentPlanner):
        # Even if a pattern would recommend different agents, explicit overrides win
        plan = planner.create_plan(
            "Add user authentication",
            agents=["test-engineer"],
        )
        assert all(a == "test-engineer" for a in plan.all_agents)


# ---------------------------------------------------------------------------
# create_plan — phases override
# ---------------------------------------------------------------------------

class TestCreatePlanPhasesOverride:
    def test_explicit_phases_used(self, planner: IntelligentPlanner):
        explicit = [
            {"name": "Alpha", "agents": ["architect"]},
            {"name": "Beta", "agents": ["backend-engineer"]},
        ]
        plan = planner.create_plan(
            "Custom workflow task",
            phases=explicit,
        )
        assert len(plan.phases) == 2
        assert plan.phases[0].name == "Alpha"
        assert plan.phases[1].name == "Beta"

    def test_explicit_phases_contain_correct_agents(self, planner: IntelligentPlanner):
        explicit = [{"name": "Work", "agents": ["test-engineer"]}]
        plan = planner.create_plan("Custom", phases=explicit)
        assert plan.phases[0].steps[0].agent_name == "test-engineer"

    def test_explicit_phases_with_gate(self, planner: IntelligentPlanner):
        explicit = [
            {
                "name": "Implement",
                "agents": ["backend-engineer"],
                "gate": {"gate_type": "lint", "command": "ruff check ."},
            }
        ]
        plan = planner.create_plan("Task", phases=explicit)
        gate = plan.phases[0].gate
        assert gate is not None
        assert gate.gate_type == "lint"


# ---------------------------------------------------------------------------
# create_plan — agent routing
# ---------------------------------------------------------------------------

class TestCreatePlanAgentRouting:
    def test_python_project_routes_backend_engineer(
        self, planner: IntelligentPlanner, python_project: Path
    ):
        plan = planner.create_plan(
            "Add a new API endpoint",
            task_type="new-feature",
            project_root=python_project,
        )
        # backend-engineer should be routed to backend-engineer--python
        all_agents = plan.all_agents
        assert any("python" in a for a in all_agents)

    def test_routing_notes_populated(
        self, planner: IntelligentPlanner, python_project: Path
    ):
        planner.create_plan(
            "Add a new API endpoint",
            task_type="new-feature",
            project_root=python_project,
        )
        # Routing should have produced notes since backend-engineer -> --python
        assert len(planner._last_routing_notes) > 0

    def test_no_project_root_leaves_base_names(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Add a new API endpoint",
            agents=["architect"],
        )
        assert "architect" in plan.all_agents


# ---------------------------------------------------------------------------
# create_plan — pattern integration
# ---------------------------------------------------------------------------

class TestCreatePlanWithPattern:
    def test_high_confidence_pattern_used(self, planner: IntelligentPlanner, tmp_path: Path):
        """Verify that a high-confidence stored pattern influences agent selection."""
        pattern = _make_pattern(
            task_type="new-feature",
            recommended_agents=["architect", "backend-engineer"],
            confidence=0.9,
        )
        # Write pattern to disk so PatternLearner.load_patterns picks it up
        patterns_file = planner._pattern_learner._patterns_path
        patterns_file.parent.mkdir(parents=True, exist_ok=True)
        patterns_file.write_text(
            json.dumps([pattern.to_dict()], indent=2), encoding="utf-8"
        )

        plan = planner.create_plan("Add user authentication", task_type="new-feature")

        assert plan.pattern_source == pattern.pattern_id
        assert planner._last_pattern_used is not None
        assert planner._last_pattern_used.pattern_id == pattern.pattern_id

    def test_low_confidence_pattern_ignored(self, planner: IntelligentPlanner):
        """A pattern below the confidence threshold should not be used."""
        pattern = _make_pattern(
            task_type="new-feature",
            recommended_agents=["data-analyst"],
            confidence=0.5,  # below 0.7 threshold
        )
        patterns_file = planner._pattern_learner._patterns_path
        patterns_file.parent.mkdir(parents=True, exist_ok=True)
        patterns_file.write_text(
            json.dumps([pattern.to_dict()], indent=2), encoding="utf-8"
        )

        plan = planner.create_plan("Add user authentication", task_type="new-feature")

        assert plan.pattern_source is None
        # Should not use data-analyst (that's from the low-confidence pattern)
        assert "data-analyst" not in plan.all_agents


# ---------------------------------------------------------------------------
# create_plan — score warnings
# ---------------------------------------------------------------------------

class TestCreatePlanScoreWarnings:
    def test_low_health_agent_generates_warning(self, planner: IntelligentPlanner):
        """When a low-health scorecard is returned, a warning should be recorded."""
        from agent_baton.core.improve.scoring import AgentScorecard

        mock_card = AgentScorecard(
            agent_name="backend-engineer",
            times_used=10,
            first_pass_rate=0.2,   # low — triggers 'needs-improvement'
            retry_rate=3.0,
            negative_mentions=2,
        )
        assert mock_card.health == "needs-improvement"

        with patch.object(planner._scorer, "score_agent", return_value=mock_card):
            planner.create_plan(
                "Add user authentication",
                agents=["backend-engineer"],
            )

        assert len(planner._last_score_warnings) > 0
        assert "backend-engineer" in planner._last_score_warnings[0]

    def test_strong_agent_generates_no_warning(self, planner: IntelligentPlanner):
        """A healthy agent should not trigger warnings."""
        from agent_baton.core.improve.scoring import AgentScorecard

        mock_card = AgentScorecard(
            agent_name="architect",
            times_used=5,
            first_pass_rate=0.9,
            negative_mentions=0,
        )
        assert mock_card.health == "strong"

        with patch.object(planner._scorer, "score_agent", return_value=mock_card):
            planner.create_plan(
                "Design the system architecture",
                agents=["architect"],
            )

        assert len(planner._last_score_warnings) == 0


# ---------------------------------------------------------------------------
# explain_plan
# ---------------------------------------------------------------------------

class TestExplainPlan:
    def test_returns_string(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert isinstance(result, str)

    def test_contains_task_summary(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Add user authentication" in result

    def test_contains_risk_level(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Risk Level" in result

    def test_contains_budget_tier(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Budget Tier" in result

    def test_pattern_section_present(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Pattern Influence" in result

    def test_no_pattern_says_default(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        assert plan.pattern_source is None
        result = planner.explain_plan(plan)
        assert "Default phase templates" in result

    def test_pattern_mention_in_explanation(self, planner: IntelligentPlanner):
        """When a pattern was used, explanation should mention its ID."""
        pattern = _make_pattern(confidence=0.9)
        patterns_file = planner._pattern_learner._patterns_path
        patterns_file.parent.mkdir(parents=True, exist_ok=True)
        patterns_file.write_text(
            json.dumps([pattern.to_dict()], indent=2), encoding="utf-8"
        )
        plan = planner.create_plan("Add user authentication", task_type="new-feature")
        if plan.pattern_source:
            result = planner.explain_plan(plan)
            assert pattern.pattern_id in result

    def test_phase_summary_in_explanation(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Phase Summary" in result

    def test_score_warnings_section_present(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert "Score Warnings" in result

    def test_warning_included_when_agent_has_low_health(self, planner: IntelligentPlanner):
        from agent_baton.core.improve.scoring import AgentScorecard

        bad_card = AgentScorecard(
            agent_name="backend-engineer",
            times_used=5,
            first_pass_rate=0.1,
            negative_mentions=3,
        )
        with patch.object(planner._scorer, "score_agent", return_value=bad_card):
            plan = planner.create_plan(
                "Add user authentication",
                agents=["backend-engineer"],
            )
            result = planner.explain_plan(plan)
        assert "backend-engineer" in result


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_no_patterns_file_still_works(self, planner: IntelligentPlanner):
        """When there is no learned-patterns.json, defaults are used silently."""
        # Ensure the patterns file does not exist
        if planner._pattern_learner._patterns_path.exists():
            planner._pattern_learner._patterns_path.unlink()
        plan = planner.create_plan("Add user authentication")
        assert isinstance(plan, MachinePlan)
        assert plan.total_steps > 0

    def test_no_budget_recommendations_falls_back_to_heuristic(
        self, planner: IntelligentPlanner
    ):
        budget_path = planner._budget_tuner._recs_path
        if budget_path.exists():
            budget_path.unlink()
        plan = planner.create_plan("Add user authentication")
        assert plan.budget_tier in ("lean", "standard", "full")

    def test_scorer_exception_does_not_crash(self, planner: IntelligentPlanner):
        """If PerformanceScorer raises, create_plan should still succeed."""
        with patch.object(
            planner._scorer, "score_agent", side_effect=RuntimeError("db unavailable")
        ):
            plan = planner.create_plan("Add user authentication")
        assert isinstance(plan, MachinePlan)

    def test_router_exception_does_not_crash(self, planner: IntelligentPlanner, tmp_path: Path):
        """If AgentRouter.detect_stack raises, create_plan should still succeed."""
        with patch.object(
            planner._router, "detect_stack", side_effect=OSError("permission denied")
        ):
            plan = planner.create_plan(
                "Add user authentication",
                project_root=tmp_path,
            )
        assert isinstance(plan, MachinePlan)

    def test_pattern_learner_exception_does_not_crash(self, planner: IntelligentPlanner):
        with patch.object(
            planner._pattern_learner,
            "get_patterns_for_task",
            side_effect=RuntimeError("corrupt file"),
        ):
            plan = planner.create_plan("Add user authentication")
        assert isinstance(plan, MachinePlan)

    def test_missing_agents_for_task_type_still_produces_phases(
        self, planner: IntelligentPlanner
    ):
        """documentation task type has no default agents — plan should still work."""
        plan = planner.create_plan("Write the design document", task_type="documentation")
        assert len(plan.phases) > 0
        for phase in plan.phases:
            assert len(phase.steps) >= 1


# ---------------------------------------------------------------------------
# Budget tier logic
# ---------------------------------------------------------------------------

class TestBudgetTier:
    def test_lean_tier_for_few_agents(self, planner: IntelligentPlanner):
        tier = planner._select_budget_tier("bug-fix", 1)
        assert tier == "lean"

    def test_standard_tier_for_medium_agents(self, planner: IntelligentPlanner):
        tier = planner._select_budget_tier("new-feature", 4)
        assert tier == "standard"

    def test_full_tier_for_many_agents(self, planner: IntelligentPlanner):
        tier = planner._select_budget_tier("migration", 6)
        assert tier == "full"

    def test_budget_recommendation_overrides_heuristic(
        self, planner: IntelligentPlanner
    ):
        """If a saved recommendation exists for the task type, use it."""
        from agent_baton.models.budget import BudgetRecommendation

        rec = BudgetRecommendation(
            task_type="new-feature",
            current_tier="standard",
            recommended_tier="full",
            reason="Median exceeds 80% of standard tier ceiling.",
            avg_tokens_used=450_000,
            median_tokens_used=430_000,
            p95_tokens_used=490_000,
            sample_size=8,
            confidence=0.8,
            potential_savings=0,
        )
        with patch.object(
            planner._budget_tuner, "load_recommendations", return_value=[rec]
        ):
            tier = planner._select_budget_tier("new-feature", 3)
        assert tier == "full"


# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------

class TestBuildSharedContext:
    def test_shared_context_contains_task_summary(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Build search feature")
        assert "Build search feature" in plan.shared_context

    def test_shared_context_contains_read_instruction(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Build search feature")
        assert "context.md" in plan.shared_context

    def test_shared_context_contains_task_id(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Build search feature")
        assert plan.task_id in plan.shared_context

    def test_shared_context_contains_risk_level(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Build search feature")
        assert plan.risk_level in plan.shared_context


# ---------------------------------------------------------------------------
# QA gates on phases
# ---------------------------------------------------------------------------

class TestQAGates:
    def test_implement_phase_gets_gate(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add OAuth2 login", task_type="new-feature")
        implement_phases = [p for p in plan.phases if p.name == "Implement"]
        assert implement_phases, "Expected an 'Implement' phase"
        assert implement_phases[0].gate is not None

    def test_test_phase_gets_gate(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add OAuth2 login", task_type="new-feature")
        test_phases = [p for p in plan.phases if p.name == "Test"]
        assert test_phases, "Expected a 'Test' phase"
        assert test_phases[0].gate is not None

    def test_review_phase_has_no_gate(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add OAuth2 login", task_type="new-feature")
        review_phases = [p for p in plan.phases if p.name == "Review"]
        if review_phases:
            assert review_phases[0].gate is None
