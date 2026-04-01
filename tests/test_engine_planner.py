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

    def test_empty_summary_returns_date_with_uuid(self, planner: IntelligentPlanner):
        tid = planner._generate_task_id("")
        import re
        # Format: YYYY-MM-DD-<8-char-uuid>
        assert re.match(r"^\d{4}-\d{2}-\d{2}-[a-f0-9]{8}$", tid)

    def test_long_summary_truncated(self, planner: IntelligentPlanner):
        long_summary = "word " * 30  # 150 chars
        tid = planner._generate_task_id(long_summary)
        # date(10) + dash(1) + slug(<=50) + dash(1) + uuid(8) = max 70
        assert len(tid) <= 70

    def test_task_ids_are_unique(self, planner: IntelligentPlanner):
        ids = {planner._generate_task_id("same task") for _ in range(20)}
        assert len(ids) == 20, "UUID suffix should prevent collisions"


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

    def test_new_feature_beats_documentation_when_build_verb(self, planner: IntelligentPlanner):
        """'Build X with documentation' should be new-feature, not documentation."""
        result = planner._infer_task_type("Build a health check API with tests and documentation")
        assert result == "new-feature"

    def test_review_still_classifies_as_documentation(self, planner: IntelligentPlanner):
        """'review the codebase' should still classify as documentation."""
        result = planner._infer_task_type("review the codebase architecture")
        assert result == "documentation"


# ---------------------------------------------------------------------------
# Default phases
# DECISION: 11 individual tests consolidated into 3 parametrized tests covering
# phase count + names per task type, plus 3 structural invariants kept separate.
# ---------------------------------------------------------------------------

class TestDefaultPhases:
    @pytest.mark.parametrize("task_type,expected_count,expected_names", [
        ("new-feature", 4, ["Design", "Implement", "Test", "Review"]),
        ("bug-fix",     3, ["Investigate", "Fix", "Test"]),
        ("data-analysis", 3, ["Design", "Implement", "Review"]),
        ("documentation", 3, ["Research", "Draft", "Review"]),
        ("migration",   4, None),   # count check only
        ("refactor",    4, None),   # count check only
    ])
    def test_phase_count_and_names(
        self,
        planner: IntelligentPlanner,
        task_type: str,
        expected_count: int,
        expected_names: list[str] | None,
    ):
        phases = planner._default_phases(task_type, ["architect", "backend-engineer"])
        assert len(phases) == expected_count
        if expected_names is not None:
            assert [p.name for p in phases] == expected_names

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
# Agent assignment — Pass 4 overflow
# ---------------------------------------------------------------------------

class TestAgentOverflowToWorkPhases:
    """Leftover agents from Pass 4 must only go to work phases (Implement/Fix/Draft),
    not to Design/Research/Review, to prevent bloated plans."""

    def test_leftover_agents_land_in_implement_not_design(self, planner: IntelligentPlanner):
        """When there are more agents than phases, extras should go to Implement."""
        agents = [
            "architect", "backend-engineer", "test-engineer",
            "code-reviewer", "data-engineer", "frontend-engineer--react",
        ]
        phases = planner._default_phases("new-feature", agents)
        design_phase = next(p for p in phases if p.name == "Design")
        implement_phase = next(p for p in phases if p.name == "Implement")
        # Design should have at most 1 agent (the primary from Pass 1)
        assert len(design_phase.steps) <= 1, (
            f"Design phase has {len(design_phase.steps)} steps — "
            f"leftover agents are leaking into Design"
        )
        # Implement should absorb the overflow
        assert len(implement_phase.steps) >= 2

    def test_review_phase_not_bloated(self, planner: IntelligentPlanner):
        """Review phase should not accumulate extra agents from overflow."""
        agents = [
            "architect", "backend-engineer", "test-engineer",
            "code-reviewer", "auditor", "security-reviewer",
        ]
        phases = planner._default_phases("new-feature", agents)
        review_phase = next(p for p in phases if p.name == "Review")
        # Review should have at most 1 agent from Passes 1-3
        assert len(review_phase.steps) <= 1, (
            f"Review phase has {len(review_phase.steps)} steps — "
            f"extra agents should go to Implement instead"
        )

    def test_many_agents_produce_bounded_total_steps(self, planner: IntelligentPlanner):
        """Even with many agents, total step count should stay reasonable."""
        agents = [
            "architect", "backend-engineer", "test-engineer",
            "code-reviewer", "data-engineer", "frontend-engineer--react",
            "auditor", "security-reviewer",
        ]
        phases = planner._default_phases("new-feature", agents)
        total_steps = sum(len(p.steps) for p in phases)
        # 8 agents across 4 phases — each agent should appear once
        assert total_steps == len(agents), (
            f"Expected {len(agents)} total steps, got {total_steps}"
        )


# ---------------------------------------------------------------------------
# Default gates
# DECISION: 7 individual tests consolidated into 2 parametrized tests:
# one for phases that get gates (with gate_type + command checks),
# one for phases that return None.
# ---------------------------------------------------------------------------

class TestDefaultGate:
    @pytest.mark.parametrize("phase_name,expected_gate_type,expected_cmd_fragment", [
        ("Implement", "build", "pytest"),
        ("Fix",       "build", "pytest"),
        ("Test",      "test",  "--cov"),
    ])
    def test_gated_phases(
        self,
        planner: IntelligentPlanner,
        phase_name: str,
        expected_gate_type: str,
        expected_cmd_fragment: str,
    ):
        gate = planner._default_gate(phase_name)
        assert gate is not None
        assert gate.gate_type == expected_gate_type
        assert expected_cmd_fragment in gate.command

    @pytest.mark.parametrize("phase_name", ["Review", "Design"])
    def test_no_gate_phases(self, planner: IntelligentPlanner, phase_name: str):
        assert planner._default_gate(phase_name) is None


# ---------------------------------------------------------------------------
# create_plan — minimal input
# DECISION: Structural field-presence tests (plan_has_phases, plan_has_steps,
# created_at_is_set, budget_tier_is_set, shared_context_is_set, git_strategy)
# collapsed into test_plan_structural_fields. Risk-level correctness kept as
# two separate tests (different inputs). task_summary and task_id kept because
# they verify non-trivial slug logic.
# ---------------------------------------------------------------------------

class TestCreatePlanMinimal:
    def test_plan_structural_fields(self, planner: IntelligentPlanner):
        """Verify that all required top-level fields are populated after create_plan."""
        plan = planner.create_plan("Add user authentication")
        assert isinstance(plan, MachinePlan)
        assert plan.task_summary == "Add user authentication"
        assert plan.task_id and "add-user-authentication" in plan.task_id
        assert len(plan.phases) > 0
        assert plan.total_steps > 0
        assert plan.created_at
        assert plan.shared_context and "Add user authentication" in plan.shared_context
        assert plan.budget_tier in ("lean", "standard", "full")
        assert plan.git_strategy.lower() in ("commit-per-agent", "branch-per-agent")

    def test_high_risk_task_classification(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Deploy to production")
        assert plan.risk_level == "HIGH"

    def test_low_risk_task_classification(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Rename a helper function")
        assert plan.risk_level == "LOW"


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
# DECISION: 8 "contains X" checks folded into 1 parametrized test.
# test_no_pattern_says_default, test_pattern_mention_in_explanation, and
# test_warning_included_when_agent_has_low_health kept standalone because they
# require distinct setup or conditional logic.
# ---------------------------------------------------------------------------

class TestExplainPlan:
    @pytest.mark.parametrize("expected_fragment", [
        "Add user authentication",  # task summary
        "Risk Level",
        "Budget Tier",
        "Pattern Influence",
        "Default phase templates",  # no pattern → default text
        "Phase Summary",
        "Score Warnings",
    ])
    def test_explain_plan_contains(
        self, planner: IntelligentPlanner, expected_fragment: str
    ):
        plan = planner.create_plan("Add user authentication")
        result = planner.explain_plan(plan)
        assert isinstance(result, str)
        assert expected_fragment in result

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
    @pytest.mark.parametrize("task_type,agent_count,expected_tier", [
        ("bug-fix",    1, "lean"),
        ("new-feature", 4, "standard"),
        ("migration",  6, "full"),
    ])
    def test_tier_selection(
        self,
        planner: IntelligentPlanner,
        task_type: str,
        agent_count: int,
        expected_tier: str,
    ):
        tier = planner._select_budget_tier(task_type, agent_count)
        assert tier == expected_tier

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
# DECISION: all 4 "contains X" checks collapsed into one test. task_id and
# risk_level checks are non-trivial (dynamic values), so they stay but share
# one plan instance via a single test method.
# ---------------------------------------------------------------------------

class TestBuildSharedContext:
    def test_shared_context_content(self, planner: IntelligentPlanner):
        """Shared context must include the task summary,
        task ID, and risk level so every dispatched agent has full context.
        Note: context.md instruction moved to PromptDispatcher delegation prompt."""
        plan = planner.create_plan("Build search feature")
        ctx = plan.shared_context
        assert "Build search feature" in ctx
        assert plan.task_id in ctx
        assert plan.risk_level in ctx


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


# ---------------------------------------------------------------------------
# Risk assessment — structural signals
# DECISION: 13 individual risk tests consolidated into 3 parametrized groups:
# (1) agent/verb signals that elevate risk, (2) tasks that stay LOW,
# (3) the boundary test (exactly 5 agents) and the _assess_risk direct call
# kept as standalone tests.
# ---------------------------------------------------------------------------

class TestRiskAssessmentStructural:
    """Tests for structural risk signals beyond keyword matching."""

    @pytest.mark.parametrize("summary,agents", [
        # 6 agents → elevates
        ("Add a simple feature", ["a", "b", "c", "d", "e", "f"]),
        # devops agent → elevates
        ("Add a simple feature", ["devops-engineer"]),
        # devops prefix variant → elevates
        ("Add a simple feature", ["devops-specialist"]),
        # auditor agent → elevates
        ("Add a simple feature", ["auditor"]),
        # security-reviewer agent → elevates
        ("Add a simple feature", ["security-reviewer"]),
        # delete verb → elevates
        ("Remove unused database tables", ["backend-engineer"]),
        # destroy verb → elevates
        ("Destroy the old test environment", ["backend-engineer"]),
        # auditor overrides read-only dampening
        ("Review the production code", ["auditor"]),
    ])
    def test_risk_elevated(self, planner: IntelligentPlanner, summary: str, agents: list[str]):
        plan = planner.create_plan(summary, agents=agents)
        assert plan.risk_level in ("MEDIUM", "HIGH")

    @pytest.mark.parametrize("summary,agents", [
        ("Review the production code for style issues", ["code-reviewer"]),
        ("Analyze the production logs for anomalies", ["backend-engineer"]),
        ("Add a helper utility function", ["backend-engineer"]),
    ])
    def test_risk_stays_low(self, planner: IntelligentPlanner, summary: str, agents: list[str]):
        plan = planner.create_plan(summary, agents=agents)
        assert plan.risk_level == "LOW"

    def test_five_agents_not_elevated(self, planner: IntelligentPlanner):
        # Threshold is >5 — exactly 5 should not elevate
        plan = planner.create_plan(
            "Add a simple feature",
            agents=["a", "b", "c", "d", "e"],  # 5 agents
        )
        # 5 agents alone should not push above LOW (no other signals)
        assert plan.risk_level == "LOW"

    def test_direct_assess_risk_method(self, planner: IntelligentPlanner):
        """_assess_risk is callable directly; returns a string risk level."""
        result = planner._assess_risk("Add a helper function", ["backend-engineer"])
        assert result in ("LOW", "MEDIUM", "HIGH")


# ---------------------------------------------------------------------------
# Step description decomposition — agent+phase templates
# ---------------------------------------------------------------------------

class TestStepDescriptionDecomposition:
    """Verify that _step_description produces role-specific descriptions."""

    def test_architect_design_uses_template(self, planner: IntelligentPlanner):
        desc = planner._step_description("Design", "architect", "Add OAuth2 login")
        # Outcome-oriented template: describes what to achieve, not how
        assert "Add OAuth2 login" in desc
        assert "design" in desc.lower() or "produce" in desc.lower()
        # Should NOT be the generic fallback format
        assert "(as architect)" not in desc

    def test_backend_implement_uses_template(self, planner: IntelligentPlanner):
        desc = planner._step_description("Implement", "backend-engineer", "Add OAuth2 login")
        # Outcome-oriented: "Implement: {task}" not "Implement the server-side..."
        assert "Add OAuth2 login" in desc
        assert "implement" in desc.lower()

    def test_backend_python_flavor_matches_base(self, planner: IntelligentPlanner):
        """Flavored agent name (--python) should match the base agent template."""
        desc = planner._step_description("Implement", "backend-engineer--python", "Add OAuth2 login")
        assert "Add OAuth2 login" in desc
        assert "implement" in desc.lower()

    def test_test_engineer_test_uses_template(self, planner: IntelligentPlanner):
        desc = planner._step_description("Test", "test-engineer", "Add OAuth2 login")
        # Outcome-oriented: "Verify: {task}. Deliver tests that would catch regressions."
        assert "Add OAuth2 login" in desc
        assert "verify" in desc.lower() or "test" in desc.lower()

    def test_code_reviewer_review_uses_template(self, planner: IntelligentPlanner):
        desc = planner._step_description("Review", "code-reviewer", "Add OAuth2 login")
        # Outcome-oriented: "Review: {task}. Approve or flag issues blocking merge."
        assert "Add OAuth2 login" in desc
        assert "review" in desc.lower() or "approve" in desc.lower() or "flag" in desc.lower()

    def test_security_reviewer_uses_template(self, planner: IntelligentPlanner):
        desc = planner._step_description("Review", "security-reviewer", "Add OAuth2 login")
        assert "security" in desc.lower() or "audit" in desc.lower() or "vulnerabilities" in desc.lower()

    def test_unknown_agent_falls_back_to_verb(self, planner: IntelligentPlanner):
        desc = planner._step_description("Implement", "custom-agent", "Do something")
        assert "Do something" in desc
        assert "(as custom-agent)" in desc

    def test_unknown_phase_falls_back_to_name(self, planner: IntelligentPlanner):
        desc = planner._step_description("Validate", "architect", "Check stuff")
        assert "Check stuff" in desc
        # Falls to generic since "validate" is not in _PHASE_VERBS or templates
        assert "(as architect)" in desc

    def test_empty_task_summary_falls_back(self, planner: IntelligentPlanner):
        desc = planner._step_description("Implement", "backend-engineer", "")
        assert "phase" in desc.lower()
        assert "backend-engineer" in desc

    @pytest.mark.parametrize("agent,phase", [
        ("architect", "design"),
        ("architect", "research"),
        ("architect", "review"),
        ("backend-engineer", "implement"),
        ("backend-engineer", "fix"),
        ("backend-engineer", "investigate"),
        ("frontend-engineer", "implement"),
        ("test-engineer", "test"),
        ("code-reviewer", "review"),
        ("data-engineer", "implement"),
        ("data-analyst", "implement"),
        ("auditor", "review"),
    ])
    def test_all_template_entries_produce_output(
        self, planner: IntelligentPlanner, agent: str, phase: str
    ):
        desc = planner._step_description(phase.capitalize(), agent, "Sample task")
        assert len(desc) > 20
        assert "Sample task" in desc
        # Should use template, not fallback
        assert f"(as {agent})" not in desc

    def test_different_agents_get_different_descriptions(self, planner: IntelligentPlanner):
        """Core quality check: architect and backend-engineer in the same phase
        should get meaningfully different descriptions."""
        task = "Add a health check API"
        arch_desc = planner._step_description("Design", "architect", task)
        be_desc = planner._step_description("Design", "backend-engineer", task)
        assert arch_desc != be_desc
        # Architect design description should be outcome-oriented
        assert "design" in arch_desc.lower() or "produce" in arch_desc.lower()
        assert "endpoint" in be_desc.lower() or "backend" in be_desc.lower()


# ---------------------------------------------------------------------------
# Cross-phase enrichment
# ---------------------------------------------------------------------------

class TestEnrichPhases:
    """Verify _enrich_phases adds cross-phase context and deliverables."""

    def test_first_phase_has_no_cross_reference(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add a health check API", task_type="new-feature")
        first_step = plan.phases[0].steps[0]
        assert "Build on the" not in first_step.task_description

    def test_second_phase_references_first(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add a health check API", task_type="new-feature")
        if len(plan.phases) >= 2:
            second_step = plan.phases[1].steps[0]
            assert "phase 1" in second_step.task_description.lower()

    def test_cross_reference_names_prior_agents(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Add a health check API",
            task_type="new-feature",
            agents=["architect", "backend-engineer", "test-engineer", "code-reviewer"],
        )
        if len(plan.phases) >= 2:
            second_step = plan.phases[1].steps[0]
            # Should mention the agent from phase 1
            first_agents = [s.agent_name for s in plan.phases[0].steps]
            assert any(a in second_step.task_description for a in first_agents)

    def test_deliverables_populated_for_known_agents(self, planner: IntelligentPlanner):
        plan = planner.create_plan(
            "Add a health check API",
            agents=["architect", "backend-engineer", "test-engineer"],
        )
        for phase in plan.phases:
            for step in phase.steps:
                base = step.agent_name.split("--")[0]
                if base in ("architect", "backend-engineer", "test-engineer"):
                    assert len(step.deliverables) > 0, (
                        f"{step.agent_name} in {phase.name} has empty deliverables"
                    )

    def test_deliverables_not_overwritten_when_explicit(self, planner: IntelligentPlanner):
        """If a step already has deliverables, _enrich_phases should not replace them."""
        phases = [PlanPhase(
            phase_id=1, name="Implement",
            steps=[PlanStep(
                step_id="1.1",
                agent_name="backend-engineer",
                task_description="Custom task",
                deliverables=["my-explicit-file.py"],
            )],
        )]
        enriched = planner._enrich_phases(phases)
        assert enriched[0].steps[0].deliverables == ["my-explicit-file.py"]

    def test_unknown_agent_gets_no_deliverables(self, planner: IntelligentPlanner):
        phases = [PlanPhase(
            phase_id=1, name="Implement",
            steps=[PlanStep(
                step_id="1.1",
                agent_name="custom-unknown-agent",
                task_description="Custom task",
            )],
        )]
        enriched = planner._enrich_phases(phases)
        assert enriched[0].steps[0].deliverables == []


# ---------------------------------------------------------------------------
# End-to-end plan quality — descriptions through create_plan
# ---------------------------------------------------------------------------

class TestPlanDescriptionQuality:
    """Verify that full plans have rich, differentiated step descriptions."""

    def test_new_feature_steps_are_differentiated(self, planner: IntelligentPlanner):
        """Each step in a new-feature plan should have a unique description."""
        plan = planner.create_plan("Build a REST API for user management")
        descriptions = [
            s.task_description for p in plan.phases for s in p.steps
        ]
        # All descriptions should be unique
        assert len(descriptions) == len(set(descriptions))

    def test_descriptions_are_substantial(self, planner: IntelligentPlanner):
        """Descriptions should be sentences, not just 'Implement phase — agent'."""
        plan = planner.create_plan("Add payment processing")
        for phase in plan.phases:
            for step in phase.steps:
                # Should be a real sentence, not a stub
                assert len(step.task_description) > 30, (
                    f"Step {step.step_id} ({step.agent_name}) description too short: "
                    f"{step.task_description!r}"
                )
                # Should not contain the old generic format
                assert "phase —" not in step.task_description

    def test_bug_fix_plan_has_investigation_language(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Fix the login timeout bug")
        all_descs = " ".join(s.task_description for p in plan.phases for s in p.steps)
        # Bug fix plans should reference investigation/diagnosis
        assert any(w in all_descs.lower() for w in [
            "diagnose", "investigate", "root cause", "trace", "fix"
        ])


# ---------------------------------------------------------------------------
# Fix 1: _DEFAULT_AGENTS["documentation"] must be non-empty
# ---------------------------------------------------------------------------

class TestDefaultAgentsDocumentation:
    def test_documentation_agents_list_is_non_empty(self) -> None:
        """_DEFAULT_AGENTS['documentation'] must not be an empty list."""
        agents = _DEFAULT_AGENTS.get("documentation", [])
        assert agents, (
            "_DEFAULT_AGENTS['documentation'] is empty — "
            "documentation tasks would produce a plan with no agents"
        )

    def test_documentation_agents_include_expected_roles(self) -> None:
        """documentation agents should cover research/drafting/review roles."""
        agents = _DEFAULT_AGENTS["documentation"]
        # At least one agent should handle drafting/content creation
        content_agents = {"talent-builder", "agent-definition-engineer", "backend-engineer"}
        assert any(a in content_agents for a in agents), (
            f"Expected at least one content-creating agent in {agents}"
        )

    def test_documentation_agents_include_reviewer(self) -> None:
        """documentation plan should include a review role."""
        agents = _DEFAULT_AGENTS["documentation"]
        review_agents = {"code-reviewer", "auditor", "architect"}
        assert any(a in review_agents for a in agents), (
            f"Expected at least one review agent in {agents}"
        )


# ---------------------------------------------------------------------------
# create_plan — default_model override
# ---------------------------------------------------------------------------

class TestDefaultModelOverride:
    def test_default_model_applied_when_agent_has_no_model(
        self, tmp_path: Path,
    ):
        """When default_model is specified, steps whose agent has no model
        definition should use the default_model instead of 'sonnet'."""
        # Create agents WITHOUT model fields so default_model takes effect
        agents_dir = tmp_path / "nomodel-agents"
        agents_dir.mkdir()
        for name in ("architect", "backend-engineer", "test-engineer", "code-reviewer"):
            content = (
                f"---\nname: {name}\ndescription: Specialist.\n"
                f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
            )
            (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")

        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        ctx = tmp_path / "team-context-nomodel"
        ctx.mkdir()
        p = IntelligentPlanner(team_context_root=ctx)
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan("Add a utility function", default_model="opus")
        for phase in plan.phases:
            for step in phase.steps:
                assert step.model == "opus", (
                    f"Step {step.step_id} has model '{step.model}', expected 'opus'"
                )

    def test_agent_definition_model_overrides_default_model(
        self, planner: IntelligentPlanner,
    ):
        """Agent definitions with explicit model take priority over default_model."""
        plan = planner.create_plan(
            "Add a simple utility function",
            default_model="haiku",
        )
        # architect has model: opus in the fixture — should keep opus
        design_step = plan.phases[0].steps[0]
        assert design_step.agent_name == "architect"
        assert design_step.model == "opus", (
            "Agent definition model should take priority over default_model"
        )

    def test_default_model_none_keeps_sonnet(self, planner: IntelligentPlanner):
        """When default_model is None, the built-in 'sonnet' default is used."""
        plan = planner.create_plan("Fix a typo", default_model=None)
        for phase in plan.phases:
            for step in phase.steps:
                # Steps get either the agent definition model or default "sonnet"
                agent_def = planner._registry.get(step.agent_name)
                if agent_def and agent_def.model:
                    assert step.model == agent_def.model
                else:
                    assert step.model == "sonnet"

    def test_all_task_types_have_non_empty_agents(self) -> None:
        """Every known task type should map to at least one agent."""
        for task_type, agents in _DEFAULT_AGENTS.items():
            assert agents, f"_DEFAULT_AGENTS['{task_type}'] is empty"


# ---------------------------------------------------------------------------
# Task 5: MachinePlan complexity fields
# ---------------------------------------------------------------------------

class TestMachinePlanComplexityFields:
    def test_default_complexity_is_medium(self):
        plan = MachinePlan(task_id="test", task_summary="test")
        assert plan.complexity == "medium"

    def test_default_classification_source(self):
        plan = MachinePlan(task_id="test", task_summary="test")
        assert plan.classification_source == "keyword-fallback"

    def test_to_dict_includes_complexity(self):
        plan = MachinePlan(task_id="test", task_summary="test", complexity="light")
        d = plan.to_dict()
        assert d["complexity"] == "light"
        assert d["classification_source"] == "keyword-fallback"

    def test_from_dict_reads_complexity(self):
        data = {
            "task_id": "test",
            "task_summary": "test",
            "complexity": "heavy",
            "classification_source": "haiku",
        }
        plan = MachinePlan.from_dict(data)
        assert plan.complexity == "heavy"
        assert plan.classification_source == "haiku"

    def test_from_dict_defaults_missing_complexity(self):
        """Backward compat: plans without complexity field default to medium."""
        data = {"task_id": "test", "task_summary": "test"}
        plan = MachinePlan.from_dict(data)
        assert plan.complexity == "medium"
        assert plan.classification_source == "keyword-fallback"

    def test_to_markdown_includes_complexity(self):
        plan = MachinePlan(
            task_id="test",
            task_summary="test",
            complexity="light",
            classification_source="haiku",
        )
        md = plan.to_markdown()
        assert "light" in md.lower()


# ---------------------------------------------------------------------------
# Task 6: Classification-aware planning
# ---------------------------------------------------------------------------

from agent_baton.core.engine.classifier import (
    FallbackClassifier,
    KeywordClassifier,
    TaskClassification,
    TaskClassifier,
)


class _StubClassifier:
    """A stub classifier that returns a fixed classification."""
    def __init__(self, classification: TaskClassification):
        self._classification = classification

    def classify(self, summary, registry, project_root=None):
        return self._classification


class TestClassificationAwarePlanning:
    def test_light_plan_has_single_phase(self, planner: IntelligentPlanner):
        stub = _StubClassifier(TaskClassification(
            task_type="migration",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Simple file move",
            source="test-stub",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan("Move 3 files to docs/historical")
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "Implement"
        assert plan.complexity == "light"
        assert plan.classification_source == "test-stub"

    def test_light_plan_has_single_agent(self, planner: IntelligentPlanner):
        stub = _StubClassifier(TaskClassification(
            task_type="migration",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Simple file move",
            source="test-stub",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan("Move 3 files to docs/historical")
        assert len(plan.all_agents) == 1

    def test_medium_regression_unchanged(self, planner: IntelligentPlanner):
        """When classifier returns medium, plan should match legacy behavior."""
        stub = _StubClassifier(TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["architect", "backend-engineer", "test-engineer", "code-reviewer"],
            phases=["Design", "Implement", "Test", "Review"],
            reasoning="Standard feature",
            source="test-stub",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan("Add user authentication")
        assert len(plan.phases) == 4
        assert plan.complexity == "medium"

    def test_explicit_task_type_bypasses_classifier(self, planner: IntelligentPlanner):
        stub = _StubClassifier(TaskClassification(
            task_type="bug-fix",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Should not be used",
            source="test-stub",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan(
            "Fix something",
            task_type="new-feature",
        )
        assert plan.task_type == "new-feature"

    def test_explicit_agents_bypasses_classifier(self, planner: IntelligentPlanner):
        stub = _StubClassifier(TaskClassification(
            task_type="migration",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Should not be used for agents",
            source="test-stub",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan(
            "Move 3 files",
            agents=["architect", "test-engineer"],
        )
        # Explicit agents override classifier
        agents_in_plan = plan.all_agents
        assert any("architect" in a for a in agents_in_plan) or any(
            "test-engineer" in a for a in agents_in_plan
        )

    def test_explicit_complexity_parameter(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Something", complexity="light")
        assert plan.complexity == "light"

    def test_explain_plan_includes_classification(self, planner: IntelligentPlanner):
        stub = _StubClassifier(TaskClassification(
            task_type="migration",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Simple file relocation",
            source="haiku",
        ))
        planner._task_classifier = stub
        plan = planner.create_plan("Move 3 files")
        explanation = planner.explain_plan(plan)
        assert "light" in explanation.lower()
        assert "haiku" in explanation.lower()


# ---------------------------------------------------------------------------
# Task 7: CLI --complexity override
# ---------------------------------------------------------------------------

class TestComplexityCLIOverride:
    def test_complexity_parameter_flows_to_plan(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add a login page", complexity="light")
        assert plan.complexity == "light"
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "Implement"

    def test_complexity_heavy_preserves_full_plan(self, planner: IntelligentPlanner):
        plan = planner.create_plan("Add a login page", complexity="heavy")
        assert plan.complexity == "heavy"
        assert len(plan.phases) >= 3
