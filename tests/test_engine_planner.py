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


@pytest.fixture
def ts_project(tmp_path: Path) -> Path:
    """A fake TypeScript project root with tsconfig.json + package.json."""
    project = tmp_path / "ts-project"
    project.mkdir()
    (project / "tsconfig.json").write_text('{}', encoding="utf-8")
    (project / "package.json").write_text(
        '{"name": "myapp", "scripts": {"test": "vitest run"}}',
        encoding="utf-8",
    )
    return project


@pytest.fixture
def extended_agents_dir(tmp_path: Path) -> Path:
    """Agents dir including frontend-engineer for cross-concern tests."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)

    agents = [
        ("backend-engineer--python", "Python backend specialist.", "sonnet"),
        ("backend-engineer--node", "Node.js backend specialist.", "sonnet"),
        ("architect", "System design specialist.", "opus"),
        ("test-engineer", "Testing specialist.", "sonnet"),
        ("code-reviewer", "Code review specialist.", "opus"),
        ("data-analyst", "Data analysis specialist.", "sonnet"),
        ("auditor", "Audit and compliance specialist.", "opus"),
        ("backend-engineer", "Generic backend engineer.", "sonnet"),
        ("frontend-engineer", "Frontend UI specialist.", "sonnet"),
        ("frontend-engineer--react", "React frontend specialist.", "sonnet"),
    ]
    for name, desc, model in agents:
        content = (
            f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\n"
            f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")

    return agents_dir


@pytest.fixture
def extended_planner(tmp_path: Path, extended_agents_dir: Path) -> IntelligentPlanner:
    """Planner with extended agent registry including frontend-engineer."""
    ctx = tmp_path / "team-context"
    ctx.mkdir(exist_ok=True)
    p = IntelligentPlanner(team_context_root=ctx)
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    reg = AgentRegistry()
    reg.load_directory(extended_agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


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
    """Task type inference uses word-boundary scoring: all types are scored
    by keyword hit count; highest scorer wins; ties broken by list order
    (new-feature first = safest default)."""

    @pytest.mark.parametrize("summary,expected", [
        ("fix the login bug", "bug-fix"),
        ("broken auth endpoint", "bug-fix"),
        ("there is an error in signup", "bug-fix"),
        ("add OAuth2 login", "new-feature"),
        ("build a new dashboard", "new-feature"),
        ("create the user API", "new-feature"),
        ("refactor the payment service", "refactor"),
        ("clean up helper utilities", "refactor"),
        ("reorganize the models directory", "refactor"),
        ("analyze user retention data", "data-analysis"),
        ("generate a monthly analytics report", "data-analysis"),
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

    def test_higher_score_wins_over_list_order(self, planner: IntelligentPlanner):
        # "fix" (1 hit) vs "add" + "feature" (2 hits) — new-feature wins by score
        result = planner._infer_task_type("fix and add new feature")
        assert result == "new-feature"

    def test_equal_score_uses_list_order(self, planner: IntelligentPlanner):
        # "fix" (1 hit, bug-fix) vs "add" (1 hit, new-feature) — new-feature
        # wins because it's first in list order
        result = planner._infer_task_type("fix something and add something")
        assert result == "new-feature"

    def test_bug_fix_wins_when_dominant(self, planner: IntelligentPlanner):
        # Multiple bug-fix keywords should beat a single new-feature keyword
        result = planner._infer_task_type("fix the broken crash in signup")
        assert result == "bug-fix"

    def test_new_feature_beats_documentation_when_build_verb(self, planner: IntelligentPlanner):
        """'Build X with documentation' should be new-feature, not documentation."""
        result = planner._infer_task_type("Build a health check API with tests and documentation")
        assert result == "new-feature"

    def test_no_false_positive_from_substrings(self, planner: IntelligentPlanner):
        """Substrings should not trigger false matches."""
        # "prefix" should NOT match "fix", "latest" should NOT match "test"
        result = planner._infer_task_type("Deploy the latest prefix configuration")
        assert result == "new-feature"

    def test_file_paths_dont_trigger_documentation(self, planner: IntelligentPlanner):
        """File paths like 'docs/plans/...' should not trigger documentation type."""
        result = planner._infer_task_type(
            "Implement the memory system per docs/plans/execution.md"
        )
        assert result == "new-feature"


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

    def test_no_gate_for_review(self, planner: IntelligentPlanner):
        assert planner._default_gate("Review") is None


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
        # for phases the override agent is allowed to own.  bd-1974: a Review
        # phase must still be routed to a reviewer-class agent — the planner
        # synthesizes ``code-reviewer`` rather than letting test-engineer
        # (an implementer) own a review step.
        plan = planner.create_plan(
            "Add user authentication",
            agents=["test-engineer"],
        )
        # Every non-review phase step must be the explicit override agent.
        for phase in plan.phases:
            for step in phase.steps:
                if phase.name.lower() == "review":
                    # Review phase enforces reviewer-class routing (bd-1974).
                    assert step.agent_name.split("--")[0] in {
                        "code-reviewer", "security-reviewer", "auditor",
                    }, (
                        f"Review phase routed to non-reviewer "
                        f"{step.agent_name!r}"
                    )
                else:
                    assert step.agent_name == "test-engineer", (
                        f"Non-review phase {phase.name!r} should keep the "
                        f"explicit override; got {step.agent_name!r}"
                    )


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
        """Shared context must include the task summary and risk level
        so every dispatched agent has full context.
        Note: context.md instruction moved to PromptDispatcher delegation prompt."""
        plan = planner.create_plan("Build search feature")
        ctx = plan.shared_context
        assert "Build search feature" in ctx
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


# ---------------------------------------------------------------------------
# Stack-aware gate commands
# ---------------------------------------------------------------------------

class TestStackAwareGates:
    """Gates should use language-appropriate test/build commands."""

    def test_python_project_gets_pytest_gates(
        self, planner: IntelligentPlanner, python_project: Path
    ):
        plan = planner.create_plan(
            "Add user authentication",
            task_type="new-feature",
            project_root=python_project,
        )
        gates = [p.gate for p in plan.phases if p.gate]
        assert any("pytest" in g.command for g in gates)

    def test_typescript_project_gets_npm_test_gates(
        self, extended_planner: IntelligentPlanner, ts_project: Path
    ):
        plan = extended_planner.create_plan(
            "Add user authentication",
            task_type="new-feature",
            project_root=ts_project,
        )
        gates = [p.gate for p in plan.phases if p.gate]
        assert gates, "Expected at least one gated phase"
        assert any("npm" in g.command for g in gates), (
            f"Expected 'npm' in gate commands for TS project, "
            f"got: {[g.command for g in gates]}"
        )

    def test_no_stack_defaults_to_pytest(self, planner: IntelligentPlanner):
        gate = planner._default_gate("Implement")
        assert gate is not None
        assert "pytest" in gate.command

    @pytest.mark.parametrize("phase_name", ["Review", "Investigate", "Research"])
    def test_no_gate_for_non_code_phases(self, planner: IntelligentPlanner, phase_name: str):
        gate = planner._default_gate(phase_name)
        assert gate is None

    def test_gate_type_matches_phase(self, planner: IntelligentPlanner):
        from agent_baton.core.orchestration.router import StackProfile
        ts_stack = StackProfile(language="typescript", framework=None)
        gate = planner._default_gate("Test", stack=ts_stack)
        assert gate is not None
        assert gate.gate_type == "test"
        assert "npm test" in gate.command

    def test_gate_for_go_project(self, planner: IntelligentPlanner):
        from agent_baton.core.orchestration.router import StackProfile
        go_stack = StackProfile(language="go", framework=None)
        gate = planner._default_gate("Implement", stack=go_stack)
        assert gate is not None
        assert "go" in gate.command


# ---------------------------------------------------------------------------
# Compound task decomposition
# ---------------------------------------------------------------------------

class TestParseSubtasks:
    """_parse_subtasks should extract numbered items from descriptions."""

    def test_parenthesized_numbers(self, planner: IntelligentPlanner):
        summary = "Do everything: (1) Write tests (2) Fix bugs (3) Deploy"
        subtasks = planner._parse_subtasks(summary)
        assert len(subtasks) == 3
        assert subtasks[0] == (1, "Write tests")
        assert subtasks[1] == (2, "Fix bugs")
        assert subtasks[2] == (3, "Deploy")

    def test_dot_numbers(self, planner: IntelligentPlanner):
        summary = "Tasks: 1. Write tests 2. Fix bugs 3. Deploy"
        subtasks = planner._parse_subtasks(summary)
        assert len(subtasks) == 3

    def test_paren_numbers(self, planner: IntelligentPlanner):
        summary = "Tasks: 1) Write tests 2) Fix bugs"
        subtasks = planner._parse_subtasks(summary)
        assert len(subtasks) == 2

    def test_single_item_returns_empty(self, planner: IntelligentPlanner):
        summary = "Do: (1) Just one thing"
        subtasks = planner._parse_subtasks(summary)
        assert subtasks == []

    def test_no_numbers_returns_empty(self, planner: IntelligentPlanner):
        summary = "Add a login feature with OAuth2"
        subtasks = planner._parse_subtasks(summary)
        assert subtasks == []


class TestCompoundDecomposition:
    """Compound tasks should produce independent phases per sub-task."""

    def test_compound_task_creates_multiple_phases(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "UI work: (1) Develop comprehensive test suite (2) Fix functional gaps (3) UX evaluation using browser navigation"
        )
        assert len(plan.phases) == 3

    def test_compound_phases_have_independent_agents(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Full stack: (1) Write unit tests for API (2) Fix broken login page (3) Review code quality"
        )
        # Each phase should have at least one step
        for phase in plan.phases:
            assert len(phase.steps) >= 1
        # The plan should involve more than just one agent type
        # (include team members for consolidated phases)
        all_agents: set[str] = set()
        for phase in plan.phases:
            for step in phase.steps:
                all_agents.add(step.agent_name)
                for member in step.team:
                    all_agents.add(member.agent_name)
        all_agents.discard("team")  # team is a wrapper, not an agent
        assert len(all_agents) >= 2, (
            f"Expected multiple agent types, got: {all_agents}"
        )

    def test_compound_task_routes_test_subtask_to_test_engineer(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Work: (1) Write comprehensive test suite (2) Fix the bug in auth"
        )
        # First sub-task mentions "test" → should route to test-engineer
        phase1_agents = [s.agent_name for s in plan.phases[0].steps]
        assert any("test-engineer" in a for a in phase1_agents)

    def test_compound_task_routes_fix_subtask_to_backend(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Work: (1) Write comprehensive test suite (2) Fix the bug in auth"
        )
        # Second sub-task mentions "fix" → should include backend-engineer
        # May be consolidated into a team step, so check all agent names
        # including team members.
        all_names: list[str] = []
        for phase in plan.phases:
            for step in phase.steps:
                all_names.append(step.agent_name)
                for member in step.team:
                    all_names.append(member.agent_name)
        assert any("backend-engineer" in a for a in all_names), (
            f"Expected backend-engineer for 'fix' subtask, got: {all_names}"
        )

    def test_compound_task_phase_names_match_subtask_types(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Work: (1) Write tests for coverage (2) Fix broken layout (3) Build new auth feature"
        )
        phase_names = [p.name for p in plan.phases]
        # Sub-task 1 is "test" → "Test" phase name
        assert phase_names[0] == "Test"
        # Sub-task 2 is "bug-fix" → "Fix" phase name
        assert phase_names[1] == "Fix"
        # Sub-task 3 is "new-feature" → "Implement" phase name
        assert phase_names[2] == "Implement"

    def test_explicit_phases_override_still_works(
        self, extended_planner: IntelligentPlanner
    ):
        """Explicit phases should NOT be decomposed even with numbered text."""
        explicit = [{"name": "Custom", "agents": ["architect"]}]
        plan = extended_planner.create_plan(
            "Work: (1) Test (2) Fix",
            phases=explicit,
        )
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "Custom"

    def test_compound_honors_explicit_agents_override(
        self, extended_planner: IntelligentPlanner
    ):
        """bd-701e: when --agents is explicit on a compound task, every
        subtask phase must use that roster, not the type-defaulted agents.

        Reproduces the multi-concern HIGH-risk thin-plan failure: a 6-item
        numbered task with an explicit --agents list previously produced
        phases with 0 implementer steps because the compound path silently
        substituted ``_DEFAULT_AGENTS.get(st_type)`` for every subtask.
        """
        explicit_agents = [
            "architect",
            "backend-engineer",
            "test-engineer",
            "documentation-architect",
            "code-reviewer",
        ]
        plan = extended_planner.create_plan(
            "Multi-concern feature: "
            "(1) Design data model "
            "(2) Implement service layer "
            "(3) Write integration tests "
            "(4) Document the API "
            "(5) Add new auth feature "
            "(6) Refactor legacy module",
            agents=explicit_agents,
        )
        # Every phase must have at least one step (no gate-only phases).
        for phase in plan.phases:
            assert len(phase.steps) >= 1, (
                f"phase {phase.phase_id} ({phase.name}) is empty; "
                "compound path dropped the explicit --agents roster"
            )

        # The non-reviewer agents from the override must each appear at
        # least once across all steps (ratio guard for bd-701e).
        all_names: set[str] = set()
        for phase in plan.phases:
            for step in phase.steps:
                all_names.add(step.agent_name.split("--")[0])
                for member in step.team:
                    all_names.add(member.agent_name.split("--")[0])
        for required in ("architect", "backend-engineer", "test-engineer",
                         "documentation-architect"):
            assert required in all_names, (
                f"explicit --agents included {required!r} but it does not "
                f"appear in any phase; got {sorted(all_names)}"
            )


# ---------------------------------------------------------------------------
# Cross-concern agent expansion
# ---------------------------------------------------------------------------

def _collect_all_agent_names(plan) -> list[str]:
    """Collect all agent names including team members."""
    names: list[str] = []
    for phase in plan.phases:
        for step in phase.steps:
            if step.agent_name != "team":
                names.append(step.agent_name)
            for member in step.team:
                names.append(member.agent_name)
    return names


class TestCrossConcernExpansion:
    """Agent rosters should expand when description mentions cross-concern work."""

    def test_task_type_test_with_fix_mention_adds_backend(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Test everything and fix gaps found in the UI",
            task_type="test",
        )
        all_names = _collect_all_agent_names(plan)
        # "fix" should trigger addition of backend-engineer
        assert any("backend-engineer" in a for a in all_names), (
            f"Expected backend-engineer for 'fix' keyword, got: {all_names}"
        )

    def test_task_type_test_with_ux_mention_adds_frontend(
        self, extended_planner: IntelligentPlanner
    ):
        plan = extended_planner.create_plan(
            "Test suite and UX evaluation of the application",
            task_type="test",
        )
        all_names = _collect_all_agent_names(plan)
        assert any("frontend-engineer" in a for a in all_names), (
            f"Expected frontend-engineer for 'ux' keyword, got: {all_names}"
        )

    def test_expand_does_not_duplicate_existing_agents(
        self, extended_planner: IntelligentPlanner
    ):
        agents = ["test-engineer", "backend-engineer"]
        expanded = extended_planner._expand_agents_for_concerns(
            agents, "Fix the test suite"
        )
        # backend-engineer already present — should not be added again
        backend_count = sum(1 for a in expanded if a.split("--")[0] == "backend-engineer")
        assert backend_count == 1

    def test_expand_preserves_original_roster(
        self, extended_planner: IntelligentPlanner
    ):
        agents = ["test-engineer"]
        expanded = extended_planner._expand_agents_for_concerns(
            agents, "Just run the tests"
        )
        assert "test-engineer" in expanded

    def test_no_expansion_for_clean_description(
        self, extended_planner: IntelligentPlanner
    ):
        agents = ["test-engineer"]
        expanded = extended_planner._expand_agents_for_concerns(
            agents, "Verify correctness"
        )
        # No cross-concern keywords → no expansion
        assert expanded == ["test-engineer"]


# ---------------------------------------------------------------------------
# Integration: the user's original problem scenario
# ---------------------------------------------------------------------------

class TestOriginalProblemScenario:
    """Regression test for the reported plan generation gap."""

    def test_multi_concern_task_decomposes_correctly(
        self, extended_planner: IntelligentPlanner, ts_project: Path
    ):
        """The original problem: a 3-concern task should produce 3+ phases
        with different agents and stack-appropriate gate commands."""
        plan = extended_planner.create_plan(
            "Exhaustive UI testing and UX evaluation: "
            "(1) Develop comprehensive UI test suite "
            "(2) Fix functional gaps found "
            "(3) UX expert evaluation using browser navigation",
            project_root=ts_project,
        )
        # Should have 3 phases (one per sub-task)
        assert len(plan.phases) >= 3, (
            f"Expected 3+ phases, got {len(plan.phases)}: "
            f"{[p.name for p in plan.phases]}"
        )

        # Should involve more than just test-engineer
        all_names = _collect_all_agent_names(plan)
        agent_bases = {a.split("--")[0] for a in all_names}
        assert len(agent_bases) >= 2, (
            f"Expected multiple agent types, got: {agent_bases}"
        )

        # Gate commands should be npm-based (TypeScript project), not pytest
        gates = [p.gate for p in plan.phases if p.gate]
        gate_commands = [g.command for g in gates]
        assert not any("pytest" in cmd for cmd in gate_commands), (
            f"Expected no pytest gates for TS project, got: {gate_commands}"
        )

    def test_task_type_test_override_still_expands(
        self, extended_planner: IntelligentPlanner, ts_project: Path
    ):
        """Even with --task-type test, cross-concerns should expand the roster."""
        plan = extended_planner.create_plan(
            "UI testing: (1) Write test suite (2) Fix broken components (3) Evaluate UX",
            task_type="test",
            project_root=ts_project,
        )
        all_names = _collect_all_agent_names(plan)
        agent_bases = {a.split("--")[0] for a in all_names}
        # Should have test-engineer + at least one more
        assert "test-engineer" in agent_bases
        assert len(agent_bases) >= 2, (
            f"Expected multiple agent types with --task-type test, got: {agent_bases}"
        )


# ---------------------------------------------------------------------------
# Structured description parser (_parse_structured_description)
# ---------------------------------------------------------------------------

class TestStructuredDescriptionParser:
    """Tests for the Phase N: / numbered-list / semicolon parser."""

    def test_phase_labeled_description_produces_phases(self, planner: IntelligentPlanner):
        """Phase N: labels with agent hints are parsed into phase dicts."""
        summary = (
            "Phase 1: architect designs the API. "
            "Phase 2: backend-engineer implements endpoints."
        )
        phases, agents = planner._parse_structured_description(summary)
        assert phases is not None
        assert len(phases) >= 2

    def test_phase_labeled_description_extracts_agents(self, planner: IntelligentPlanner):
        """Agent names embedded in Phase N: labels are returned in the agents list."""
        summary = (
            "Phase 1: architect designs the API. "
            "Phase 2: backend-engineer implements endpoints."
        )
        phases, agents = planner._parse_structured_description(summary)
        assert agents is not None
        assert "architect" in agents
        assert "backend-engineer" in agents

    def test_numbered_list_description_returns_phases(self, planner: IntelligentPlanner):
        """Numbered lists (1. … 2. …) are parsed and produce phase dicts."""
        summary = (
            "1. Design the schema with architect. "
            "2. Implement with backend-engineer. "
            "3. Test with test-engineer."
        )
        phases, agents = planner._parse_structured_description(summary)
        assert phases is not None
        assert len(phases) >= 2

    def test_numbered_list_description_extracts_multiple_agents(self, planner: IntelligentPlanner):
        """At least two agent names are detected from a numbered list."""
        summary = (
            "1. Design the schema with architect. "
            "2. Implement with backend-engineer. "
            "3. Test with test-engineer."
        )
        phases, agents = planner._parse_structured_description(summary)
        assert agents is not None
        assert len(agents) >= 2

    def test_agent_alias_viz_resolves(self):
        """Alias 'viz' maps to 'visualization-expert' in _AGENT_ALIASES."""
        from agent_baton.core.engine.planner import _AGENT_ALIASES
        assert _AGENT_ALIASES.get("viz") == "visualization-expert"

    def test_agent_alias_sme_resolves(self):
        """Alias 'sme' maps to 'subject-matter-expert' in _AGENT_ALIASES."""
        from agent_baton.core.engine.planner import _AGENT_ALIASES
        assert _AGENT_ALIASES.get("sme") == "subject-matter-expert"

    def test_agent_alias_backend_resolves(self):
        """Alias 'backend' maps to 'backend-engineer' in _AGENT_ALIASES."""
        from agent_baton.core.engine.planner import _AGENT_ALIASES
        assert _AGENT_ALIASES.get("backend") == "backend-engineer"

    def test_unstructured_returns_none_phases(self, planner: IntelligentPlanner):
        """Plain text without structure returns None for phases."""
        summary = "Fix the login bug that crashes on invalid passwords"
        phases, agents = planner._parse_structured_description(summary)
        assert phases is None

    def test_unstructured_returns_none_agents(self, planner: IntelligentPlanner):
        """Plain text without structure returns None for agents."""
        summary = "Fix the login bug that crashes on invalid passwords"
        phases, agents = planner._parse_structured_description(summary)
        assert agents is None

    def test_structured_creates_plan_with_at_least_two_phases(self, planner: IntelligentPlanner):
        """Structured descriptions feed into create_plan and produce ≥2 phases."""
        plan = planner.create_plan(
            "Phase 1: architect designs data model. "
            "Phase 2: backend-engineer implements API.",
            task_type="new-feature",
        )
        assert len(plan.phases) >= 2

    def test_single_clause_not_treated_as_structured(self, planner: IntelligentPlanner):
        """A single-clause description without numbering should not be parsed as structured."""
        summary = "architect builds the whole system"
        phases, agents = planner._parse_structured_description(summary)
        # Single clause — not enough structure to split into phases
        assert phases is None


# ---------------------------------------------------------------------------
# Audit/assessment/scorecard/evaluate keyword mapping
# ---------------------------------------------------------------------------

class TestTaskTypeKeywords:
    """Verify that audit/assessment/scorecard/evaluate map to data-analysis."""

    def test_audit_maps_to_data_analysis(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("Audit the dashboard metrics") == "data-analysis"

    def test_assessment_maps_to_data_analysis(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("Assessment of data quality") == "data-analysis"

    def test_scorecard_maps_to_data_analysis(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("Executive scorecard for quarterly KPIs") == "data-analysis"

    def test_evaluate_maps_to_data_analysis(self, planner: IntelligentPlanner):
        assert planner._infer_task_type("Evaluate system performance") == "data-analysis"

    def test_bug_fix_still_works(self, planner: IntelligentPlanner):
        """Existing bug-fix keyword matching is unaffected by the new keywords."""
        assert planner._infer_task_type("Fix the login crash") == "bug-fix"

    def test_audit_keyword_in_task_type_keywords_constant(self):
        """The 'audit' keyword must be present in _TASK_TYPE_KEYWORDS for data-analysis."""
        data_analysis_keywords = next(
            (kws for tt, kws in _TASK_TYPE_KEYWORDS if tt == "data-analysis"), []
        )
        assert "audit" in data_analysis_keywords

    def test_assessment_keyword_in_task_type_keywords_constant(self):
        """The 'assessment' keyword must be present in _TASK_TYPE_KEYWORDS for data-analysis."""
        data_analysis_keywords = next(
            (kws for tt, kws in _TASK_TYPE_KEYWORDS if tt == "data-analysis"), []
        )
        assert "assessment" in data_analysis_keywords


# ---------------------------------------------------------------------------
# Team phase detection (_is_team_phase)
# ---------------------------------------------------------------------------

class TestIsTeamPhase:
    """Tests for the static _is_team_phase helper."""

    def test_implement_with_multiple_steps_is_team(self, planner: IntelligentPlanner):
        """Implement phase with 2+ steps always qualifies as a team phase."""
        phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="test-engineer", task_description="b"),
            ],
        )
        assert planner._is_team_phase(phase, "implement the feature") is True

    def test_fix_with_multiple_steps_is_team(self, planner: IntelligentPlanner):
        """Fix phase with 2+ steps qualifies as a team phase (same rule as Implement)."""
        phase = PlanPhase(
            phase_id=1,
            name="Fix",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="auditor", task_description="b"),
            ],
        )
        assert planner._is_team_phase(phase, "fix the bug") is True

    def test_single_step_implement_not_team(self, planner: IntelligentPlanner):
        """Implement phase with only one step is not a team phase."""
        phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a"),
            ],
        )
        assert planner._is_team_phase(phase, "implement the feature") is False

    def test_pairing_signal_triggers_team_on_review(self, planner: IntelligentPlanner):
        """'joint' in task_summary causes a multi-step Review phase to become a team phase."""
        phase = PlanPhase(
            phase_id=1,
            name="Review",
            steps=[
                PlanStep(step_id="1.1", agent_name="code-reviewer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="auditor", task_description="b"),
            ],
        )
        assert planner._is_team_phase(phase, "joint review of security changes") is True

    def test_no_signal_review_with_multiple_steps_not_team(self, planner: IntelligentPlanner):
        """Review phase with 2+ steps but no pairing signal is NOT a team phase."""
        phase = PlanPhase(
            phase_id=1,
            name="Review",
            steps=[
                PlanStep(step_id="1.1", agent_name="code-reviewer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="auditor", task_description="b"),
            ],
        )
        assert planner._is_team_phase(phase, "review the changes") is False

    def test_team_signal_in_summary_triggers_team(self, planner: IntelligentPlanner):
        """'team' keyword in summary triggers consolidation for any multi-step phase."""
        phase = PlanPhase(
            phase_id=2,
            name="Design",
            steps=[
                PlanStep(step_id="2.1", agent_name="architect", task_description="a"),
                PlanStep(step_id="2.2", agent_name="backend-engineer", task_description="b"),
            ],
        )
        assert planner._is_team_phase(phase, "team effort to redesign the system") is True

    def test_empty_steps_not_team(self, planner: IntelligentPlanner):
        """A phase with no steps is not a team phase."""
        phase = PlanPhase(phase_id=1, name="Implement", steps=[])
        assert planner._is_team_phase(phase, "implement something together") is False


# ---------------------------------------------------------------------------
# Concern-splitting (_parse_concerns + _split_implement_phase_by_concerns)
# Covers Bug 1: multi-concern implement phases must split into parallel steps,
# not collapse into one team step.
# ---------------------------------------------------------------------------

class TestParseConcerns:
    """Tests for the static _parse_concerns helper."""

    def test_feature_id_markers_detected(self):
        """F0.1/F0.2/F0.3/F0.4 patterns produce 4 concerns."""
        summary = (
            "Phase 0: F0.1 Spec entity, F0.2 Tenancy hierarchy, "
            "F0.3 Hash-chain audit log, F0.4 Knowledge telemetry"
        )
        concerns = IntelligentPlanner._parse_concerns(summary)
        markers = [c[0] for c in concerns]
        assert markers == ["F0.1", "F0.2", "F0.3", "F0.4"]
        assert concerns[0][1] == "Spec entity"
        assert concerns[3][1] == "Knowledge telemetry"

    def test_parenthesized_markers_detected(self):
        """(1)/(2)/(3) parenthesized markers produce 3 concerns."""
        summary = "(1) login UI; (2) backend OAuth; (3) test coverage"
        concerns = IntelligentPlanner._parse_concerns(summary)
        assert [c[0] for c in concerns] == ["1", "2", "3"]

    def test_bare_numbered_markers_detected(self):
        """1./2./3. bare-numbered markers produce 3 concerns."""
        summary = "1. Refactor schema. 2. Migrate data. 3. Update API."
        concerns = IntelligentPlanner._parse_concerns(summary)
        assert [c[0] for c in concerns] == ["1", "2", "3"]

    def test_under_threshold_returns_empty(self):
        """Fewer than 3 markers returns no concerns (below split threshold)."""
        summary = "Two items: (1) login, (2) signup"
        assert IntelligentPlanner._parse_concerns(summary) == []

    def test_no_markers_returns_empty(self):
        """A plain task summary returns no concerns."""
        summary = "Add a single OAuth2 endpoint to the API"
        assert IntelligentPlanner._parse_concerns(summary) == []

    def test_decimal_versions_not_matched(self):
        """Decimals like '1.5.2' must not be parsed as concern markers."""
        summary = "release version 1.5.2 today and tag the build"
        assert IntelligentPlanner._parse_concerns(summary) == []

    def test_single_feature_id_not_split(self):
        """A single F1.5 reference must not trigger splitting."""
        assert IntelligentPlanner._parse_concerns("Fix bug F1.5 in the parser") == []


class TestConcernSplitting:
    """End-to-end tests for concern-splitting in create_plan()."""

    def test_four_concern_summary_produces_four_parallel_steps(
        self, extended_planner: IntelligentPlanner
    ):
        """A 4-concern task must produce a 4-step parallel implement phase
        (NOT a single team step)."""
        summary = (
            "Implement Phase 0 foundations: F0.1 Spec entity (DB schema "
            "and CRUD), F0.2 Tenancy hierarchy (org/team API endpoints), "
            "F0.3 Hash-chain audit log (verifier), F0.4 Knowledge "
            "telemetry (UI dashboard)"
        )
        plan = extended_planner.create_plan(summary, task_type="new-feature")
        impl_phases = [p for p in plan.phases if p.name.lower() == "implement"]
        assert impl_phases, "expected at least one Implement phase"
        impl = impl_phases[0]
        # Bug 1 expectation: 4 parallel steps, NOT a 1-step team
        assert len(impl.steps) == 4, (
            f"expected 4 parallel concern-steps, got {len(impl.steps)}: "
            f"{[(s.step_id, s.agent_name) for s in impl.steps]}"
        )
        # No step should be a team-wrapper
        for s in impl.steps:
            assert s.agent_name != "team", (
                f"step {s.step_id} is a team-wrapper; expected single-agent "
                f"per-concern split"
            )
            assert s.team == [], f"step {s.step_id} unexpectedly has team members"
        # Step IDs renumber 1..4
        assert [s.step_id for s in impl.steps] == [
            f"{impl.phase_id}.{i}" for i in range(1, 5)
        ]
        # Concern markers preserved in descriptions
        descriptions = " ".join(s.task_description for s in impl.steps)
        for marker in ("F0.1", "F0.2", "F0.3", "F0.4"):
            assert marker in descriptions, f"marker {marker} missing from step descriptions"

    def test_single_concern_summary_does_not_split(
        self, extended_planner: IntelligentPlanner
    ):
        """A 1-concern task must NOT produce a multi-step parallel implement
        phase (no regression to existing single-task planning)."""
        summary = "Add a single OAuth2 endpoint to the API"
        plan = extended_planner.create_plan(summary, task_type="new-feature")
        impl_phases = [p for p in plan.phases if p.name.lower() == "implement"]
        assert impl_phases
        impl = impl_phases[0]
        # Existing behaviour: 1-step or a team step (concern-split must NOT
        # have produced ≥3 steps).  Concern markers should be absent.
        for s in impl.steps:
            assert "F0." not in s.task_description
            assert "(F0" not in s.task_description

    def test_pick_agent_for_concern_routes_ui_to_frontend(
        self, extended_planner: IntelligentPlanner
    ):
        """The picker should select frontend-engineer for a UI concern
        when frontend-engineer is in the candidate roster."""
        candidates = [
            "architect",
            "backend-engineer",
            "frontend-engineer",
            "test-engineer",
        ]
        agent = extended_planner._pick_agent_for_concern(
            "React UI dashboard with visual layout", candidates,
        )
        assert agent == "frontend-engineer", (
            f"expected frontend-engineer for UI concern, got {agent}"
        )

    def test_pick_agent_for_concern_routes_db_to_backend(
        self, extended_planner: IntelligentPlanner
    ):
        """The picker should select backend-engineer for a database concern."""
        candidates = ["architect", "backend-engineer", "frontend-engineer"]
        agent = extended_planner._pick_agent_for_concern(
            "Database schema migration for the audit log", candidates,
        )
        assert agent == "backend-engineer", (
            f"expected backend-engineer for DB concern, got {agent}"
        )

    def test_pick_agent_for_concern_excludes_reviewers(
        self, extended_planner: IntelligentPlanner
    ):
        """The picker must never return a reviewer-class agent, even when
        only reviewers + one valid agent are in the candidate list."""
        candidates = ["auditor", "code-reviewer", "backend-engineer"]
        agent = extended_planner._pick_agent_for_concern(
            "Audit and review the security log", candidates,
        )
        # Even though 'audit' and 'review' keywords match code-reviewer,
        # reviewers are excluded — backend-engineer is the only eligible.
        assert agent == "backend-engineer"


# ---------------------------------------------------------------------------
# Reviewer-agent filtering on team-step expansion
# Covers Bug 2: auditor / code-reviewer / etc. must not appear as
# implementers on an implement-type team step.
# ---------------------------------------------------------------------------

class TestReviewerAgentFilter:
    """Tests for the REVIEWER_AGENTS filter applied in _consolidate_team_step
    and the --agents override warning."""

    def test_consolidate_drops_auditor_from_implement_phase(
        self, planner: IntelligentPlanner
    ):
        """An Implement phase with auditor among its steps must not produce
        a team member with role=implementer for the auditor."""
        phase = PlanPhase(
            phase_id=3,
            name="Implement",
            steps=[
                PlanStep(step_id="3.1", agent_name="backend-engineer", task_description="A"),
                PlanStep(step_id="3.2", agent_name="auditor", task_description="B"),
                PlanStep(step_id="3.3", agent_name="test-engineer", task_description="C"),
            ],
        )
        consolidated = planner._consolidate_team_step(phase)
        assert consolidated.team, "expected consolidated team members"
        member_agents = [m.agent_name for m in consolidated.team]
        assert "auditor" not in member_agents, (
            f"auditor must be filtered from implement-phase team; got {member_agents}"
        )
        # No member should have role=implementer for a reviewer
        for m in consolidated.team:
            assert not (
                m.agent_name == "auditor" and m.role == "implementer"
            )

    def test_consolidate_keeps_auditor_in_review_phase(
        self, planner: IntelligentPlanner
    ):
        """A non-implement phase (e.g. Review) keeps reviewer agents intact."""
        phase = PlanPhase(
            phase_id=4,
            name="Review",
            steps=[
                PlanStep(step_id="4.1", agent_name="code-reviewer", task_description="A"),
                PlanStep(step_id="4.2", agent_name="auditor", task_description="B"),
            ],
        )
        consolidated = planner._consolidate_team_step(phase)
        member_agents = [m.agent_name for m in consolidated.team]
        # Both reviewers retained on a review-type phase
        assert "auditor" in member_agents
        assert "code-reviewer" in member_agents

    def test_consolidate_logs_warning_when_dropping_reviewers(
        self, planner: IntelligentPlanner, caplog
    ):
        """A warning must be logged when reviewer agents are filtered out."""
        import logging
        phase = PlanPhase(
            phase_id=3,
            name="Implement",
            steps=[
                PlanStep(step_id="3.1", agent_name="backend-engineer", task_description="A"),
                PlanStep(step_id="3.2", agent_name="auditor", task_description="B"),
            ],
        )
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.planner"):
            planner._consolidate_team_step(phase)
        assert any(
            "auditor" in r.getMessage() and "Implement" in r.getMessage()
            for r in caplog.records
        ), f"expected warning mentioning auditor + Implement; got {[r.getMessage() for r in caplog.records]}"

    def test_agents_override_with_auditor_logs_warning(
        self, extended_planner: IntelligentPlanner, caplog
    ):
        """When --agents (the agents= kwarg) includes auditor on an implement
        task, the planner logs a warning AND the auditor never appears as an
        implementer on the implement phase."""
        import logging
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.planner"):
            plan = extended_planner.create_plan(
                "Add a new tenancy API endpoint with database migration",
                task_type="new-feature",
                agents=["backend-engineer", "auditor", "test-engineer"],
            )
        # Warning was emitted
        assert any(
            "auditor" in r.getMessage() and "reviewer" in r.getMessage().lower()
            for r in caplog.records
        ), f"expected reviewer warning; got {[r.getMessage() for r in caplog.records]}"

        # Implement phase has NO auditor team member with role=implementer
        impl_phases = [p for p in plan.phases if p.name.lower() == "implement"]
        for impl in impl_phases:
            for step in impl.steps:
                # Single-agent step
                if step.agent_name != "team":
                    assert step.agent_name != "auditor", (
                        f"auditor leaked as implement step agent on {step.step_id}"
                    )
                # Team step
                for m in step.team:
                    assert not (
                        m.agent_name == "auditor" and m.role == "implementer"
                    ), f"auditor leaked as implementer on {m.member_id}"

    def test_all_reviewers_phase_keeps_executable(
        self, planner: IntelligentPlanner, caplog
    ):
        """If every step in an implement phase is a reviewer, _consolidate
        keeps the original list (degraded executability) and logs a warning,
        rather than emitting an empty team."""
        import logging
        phase = PlanPhase(
            phase_id=3,
            name="Implement",
            steps=[
                PlanStep(step_id="3.1", agent_name="auditor", task_description="A"),
                PlanStep(step_id="3.2", agent_name="code-reviewer", task_description="B"),
            ],
        )
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.planner"):
            consolidated = planner._consolidate_team_step(phase)
        # Plan remains executable (members non-empty)
        assert consolidated.team, "expected non-empty team to keep plan executable"
        # The "all-reviewers" warning was raised
        assert any(
            "All members" in r.getMessage() and "reviewer" in r.getMessage().lower()
            for r in caplog.records
        )
