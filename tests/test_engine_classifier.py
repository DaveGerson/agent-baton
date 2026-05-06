"""Tests for agent_baton.core.engine.classifier."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_baton.core.engine.classifier import (
    FallbackClassifier,
    KeywordClassifier,
    TalentAgentClassifier,
    TaskClassification,
    _score_task_type,
)
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.agent import AgentDefinition


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------

def _make_registry(agents: list[tuple[str, str]] | None = None) -> AgentRegistry:
    """Build a registry with mock agents. Each tuple is (name, description)."""
    reg = AgentRegistry()
    for name, desc in (agents or [
        ("architect", "System design, technical decisions, module boundaries"),
        ("backend-engineer", "Server-side implementation, API endpoints, business logic"),
        ("backend-engineer--python", "Python backend specialist. FastAPI, Django, SQLAlchemy"),
        ("frontend-engineer--react", "React UI components, state management"),
        ("test-engineer", "Write and organize tests"),
        ("code-reviewer", "Quality review before commits"),
        ("auditor", "Safety review for guardrail changes"),
        ("data-engineer", "Database schema, migrations, ETL pipelines"),
        ("data-engineer--databricks", "Databricks pipelines, Delta Lake, Spark"),
    ]):
        agent = AgentDefinition(name=name, description=desc)
        reg._agents[name] = agent
    return reg


# ---------------------------------------------------------------------------
# Task 1: TaskClassification dataclass
# ---------------------------------------------------------------------------

class TestTaskClassification:
    def test_fields_stored(self):
        tc = TaskClassification(
            task_type="migration",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Simple file move",
            source="keyword-fallback",
        )
        assert tc.task_type == "migration"
        assert tc.complexity == "light"
        assert tc.agents == ["backend-engineer"]
        assert tc.phases == ["Implement"]
        assert tc.reasoning == "Simple file move"
        assert tc.source == "keyword-fallback"

    def test_complexity_must_be_valid(self):
        with pytest.raises(ValueError):
            TaskClassification(
                task_type="migration",
                complexity="tiny",
                agents=["backend-engineer"],
                phases=["Implement"],
                reasoning="test",
                source="test",
            )

    def test_agents_must_be_nonempty(self):
        with pytest.raises(ValueError):
            TaskClassification(
                task_type="migration",
                complexity="light",
                agents=[],
                phases=["Implement"],
                reasoning="test",
                source="test",
            )

    def test_phases_must_be_nonempty(self):
        with pytest.raises(ValueError):
            TaskClassification(
                task_type="migration",
                complexity="light",
                agents=["backend-engineer"],
                phases=[],
                reasoning="test",
                source="test",
            )


# ---------------------------------------------------------------------------
# Task 2: KeywordClassifier
# ---------------------------------------------------------------------------

class TestKeywordClassifierComplexity:
    def setup_method(self):
        self.classifier = KeywordClassifier()
        self.registry = _make_registry()

    def test_light_simple_file_move(self):
        result = self.classifier.classify("Move 3 files to docs/historical", self.registry)
        assert result.complexity == "light"
        assert result.source == "keyword-fallback"

    def test_light_rename_file(self):
        result = self.classifier.classify("Rename config.yaml to config.yml", self.registry)
        assert result.complexity == "light"

    def test_light_small_quantifier(self):
        result = self.classifier.classify("Update 2 test fixtures", self.registry)
        assert result.complexity == "light"

    def test_heavy_system_wide(self):
        result = self.classifier.classify(
            "Redesign the entire authentication system across frontend and backend",
            self.registry,
        )
        assert result.complexity == "heavy"

    def test_heavy_multi_domain(self):
        result = self.classifier.classify(
            "Rearchitect the API and UI to support multi-tenancy",
            self.registry,
        )
        assert result.complexity == "heavy"

    def test_medium_is_default(self):
        result = self.classifier.classify("Implement user profile endpoint", self.registry)
        assert result.complexity == "medium"

    def test_preserves_task_type_inference(self):
        result = self.classifier.classify("Fix the login bug", self.registry)
        assert result.task_type == "bug-fix"

    def test_move_classified_as_migration(self):
        result = self.classifier.classify("Move 3 files to docs/historical", self.registry)
        assert result.task_type == "migration"


class TestKeywordClassifierAgentScaling:
    def setup_method(self):
        self.classifier = KeywordClassifier()
        self.registry = _make_registry()

    def test_light_single_agent(self):
        result = self.classifier.classify("Move 3 files to docs/historical", self.registry)
        assert len(result.agents) == 1

    def test_light_single_phase(self):
        result = self.classifier.classify("Move 3 files to docs/historical", self.registry)
        assert result.phases == ["Implement"]

    def test_medium_has_multiple_agents(self):
        result = self.classifier.classify("Implement user profile endpoint", self.registry)
        assert len(result.agents) >= 2

    def test_heavy_includes_reviewer(self):
        result = self.classifier.classify(
            "Redesign the entire authentication system across frontend and backend",
            self.registry,
        )
        agent_names = result.agents
        has_reviewer = any(
            a in ("code-reviewer", "auditor", "security-reviewer")
            for a in agent_names
        )
        assert has_reviewer

    def test_heavy_full_phase_list(self):
        result = self.classifier.classify(
            "Redesign the entire authentication system across frontend and backend",
            self.registry,
        )
        assert len(result.phases) >= 3


class TestKeywordClassifierRegistryAwareness:
    def test_discovers_project_specific_agent(self):
        registry = _make_registry([
            ("backend-engineer", "Server-side implementation"),
            ("data-engineer--databricks", "Databricks pipelines, Delta Lake, Spark"),
            ("test-engineer", "Write and organize tests"),
        ])
        classifier = KeywordClassifier()
        result = classifier.classify(
            "Migrate Databricks Delta tables to new schema",
            registry,
        )
        assert "data-engineer--databricks" in result.agents

    def test_does_not_pick_irrelevant_agents(self):
        classifier = KeywordClassifier()
        registry = _make_registry()
        result = classifier.classify("Fix the login bug", registry)
        assert "data-engineer--databricks" not in result.agents


# ---------------------------------------------------------------------------
# Task 3 / Task 4: TalentAgentClassifier
# ---------------------------------------------------------------------------

class TestTalentAgentClassifier:
    """Tests for TalentAgentClassifier._parse_response."""

    def setup_method(self):
        self.registry = _make_registry()
        self.classifier = TalentAgentClassifier()

    def test_parses_valid_json(self):
        raw = json.dumps({
            "task_type": "audit",
            "complexity": "heavy",
            "risk": "LOW",
            "agents": ["architect", "code-reviewer"],
            "phases": ["Prepare", "Audit", "Synthesize", "Review"],
            "reasoning": "Codebase audit needs architect and reviewer",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert result.task_type == "audit"
        assert result.complexity == "heavy"
        assert result.source == "talent-agent"
        assert result.agents == ["architect", "code-reviewer"]

    def test_attaches_risk_hint(self):
        raw = json.dumps({
            "task_type": "audit",
            "complexity": "medium",
            "risk": "LOW",
            "agents": ["architect"],
            "phases": ["Audit"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert getattr(result, "_cli_risk_hint", None) == "LOW"

    def test_rejects_invalid_agents(self):
        raw = json.dumps({
            "task_type": "audit",
            "complexity": "medium",
            "agents": ["made-up-agent", "architect"],
            "phases": ["Audit"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert "made-up-agent" not in result.agents
        assert "architect" in result.agents

    def test_returns_none_on_no_valid_agents(self):
        raw = json.dumps({
            "task_type": "audit",
            "complexity": "medium",
            "agents": ["fake-agent"],
            "phases": ["Audit"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is None

    def test_returns_none_on_bad_json(self):
        result = self.classifier._parse_response("not json at all", self.registry)
        assert result is None

    def test_strips_markdown_fences(self):
        payload = json.dumps({
            "task_type": "bug-fix",
            "complexity": "light",
            "agents": ["backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "test",
        })
        raw = f"```json\n{payload}\n```"
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert result.task_type == "bug-fix"

    def test_unknown_task_type_becomes_generic(self):
        raw = json.dumps({
            "task_type": "invented-type",
            "complexity": "medium",
            "agents": ["architect"],
            "phases": ["Design"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert result.task_type == "generic"

    def test_caps_agents_by_complexity(self):
        raw = json.dumps({
            "task_type": "new-feature",
            "complexity": "light",
            "agents": ["architect", "backend-engineer", "test-engineer"],
            "phases": ["Implement"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(raw, self.registry)
        assert result is not None
        assert len(result.agents) == 1

    def test_prompt_includes_agent_list_and_type_map(self):
        prompt = self.classifier._build_prompt("Audit the codebase", self.registry)
        assert "architect" in prompt
        assert "backend-engineer" in prompt
        assert "audit:" in prompt.lower() or "audit" in prompt.lower()
        assert "Prepare" in prompt


class TestFallbackClassifierKeywordPath:
    """Tests for FallbackClassifier's keyword fallback path.

    TalentAgent is mocked to return None so keyword fallback is exercised.
    """

    def setup_method(self):
        self.registry = _make_registry()

    def test_keyword_fallback_returns_valid_classification(self):
        with patch.object(TalentAgentClassifier, "classify", return_value=None):
            classifier = FallbackClassifier()
            result = classifier.classify("Add user authentication", self.registry)
        assert isinstance(result, TaskClassification)
        assert result.source == "keyword-fallback"
        assert result.complexity in ("light", "medium", "heavy")
        assert len(result.agents) >= 1
        assert len(result.phases) >= 1

    def test_keyword_fallback_logs_info(self, caplog):
        import logging
        with patch.object(TalentAgentClassifier, "classify", return_value=None):
            classifier = FallbackClassifier()
            with caplog.at_level(logging.INFO, logger="agent_baton.core.engine.classifier"):
                classifier.classify("Fix the auth crash", self.registry)
        assert any(
            "unavailable" in record.message.lower() or "keyword" in record.message.lower()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# Edge cases: empty registry
# ---------------------------------------------------------------------------

class TestKeywordClassifierEmptyRegistry:
    """Verify the classifier degrades gracefully when no agents are registered."""

    def setup_method(self):
        self.classifier = KeywordClassifier()
        self.empty_registry = AgentRegistry()  # no agents loaded

    def test_empty_registry_returns_valid_result(self):
        result = self.classifier.classify("Add a new feature", self.empty_registry)
        assert isinstance(result, TaskClassification)
        assert result.source == "keyword-fallback"

    def test_empty_registry_agents_nonempty(self):
        """Even with no registry agents, a fallback agent name must be returned."""
        result = self.classifier.classify("Add a new feature", self.empty_registry)
        assert len(result.agents) >= 1

    def test_empty_registry_phases_nonempty(self):
        result = self.classifier.classify("Add a new feature", self.empty_registry)
        assert len(result.phases) >= 1

    def test_empty_registry_light_task_still_produces_single_agent(self):
        result = self.classifier.classify("Rename 1 file", self.empty_registry)
        assert result.complexity == "light"
        assert len(result.agents) == 1


# ---------------------------------------------------------------------------
# Edge cases: very long task summaries
# ---------------------------------------------------------------------------

class TestKeywordClassifierLongSummary:
    """Verify no crash or pathological behaviour on very long input strings."""

    def setup_method(self):
        self.classifier = KeywordClassifier()
        self.registry = _make_registry()

    def test_1000_word_summary_returns_result(self):
        long_summary = ("redesign the entire system " * 200).strip()  # 800+ words
        result = self.classifier.classify(long_summary, self.registry)
        assert isinstance(result, TaskClassification)

    def test_1000_word_summary_complexity_is_valid(self):
        long_summary = ("redesign the entire system " * 200).strip()
        result = self.classifier.classify(long_summary, self.registry)
        assert result.complexity in ("light", "medium", "heavy")

    def test_long_summary_with_heavy_keywords_classified_heavy(self):
        # Repeat enough times that heavy signals dominate
        long_summary = (
            "Redesign the entire authentication system across frontend and backend. " * 20
        )
        result = self.classifier.classify(long_summary, self.registry)
        assert result.complexity == "heavy"

    def test_single_character_summary_does_not_crash(self):
        result = self.classifier.classify("x", self.registry)
        assert isinstance(result, TaskClassification)

    def test_empty_summary_does_not_crash(self):
        result = self.classifier.classify("", self.registry)
        assert isinstance(result, TaskClassification)


# ---------------------------------------------------------------------------
# Edge cases: complexity signal interactions
# ---------------------------------------------------------------------------

class TestComplexitySignalBoundaries:
    """Test the boundary conditions in _infer_complexity scoring logic."""

    def setup_method(self):
        self.classifier = KeywordClassifier()
        self.registry = _make_registry()

    def test_heavy_beats_light_when_two_heavy_signals(self):
        # "entire" (scope) + "redesign" (arch) + "3 files" (light quantifier)
        summary = "Redesign the entire API with 3 new files"
        result = self.classifier.classify(summary, self.registry)
        assert result.complexity == "heavy"

    def test_single_heavy_signal_with_light_signal_is_medium(self):
        # "entire" (1 heavy) + "rename" (1 light) → medium
        summary = "Rename the entire config namespace"
        result = self.classifier.classify(summary, self.registry)
        assert result.complexity == "medium"

    def test_one_heavy_signal_no_light_is_heavy(self):
        # "across frontend and backend" (multi-domain, 1 heavy signal), no light signals
        summary = "Update API across frontend and backend services"
        result = self.classifier.classify(summary, self.registry)
        assert result.complexity == "heavy"

    def test_light_quantifier_alone_gives_light(self):
        summary = "Add 2 test fixtures"
        result = self.classifier.classify(summary, self.registry)
        assert result.complexity == "light"

    def test_light_verb_alone_gives_light(self):
        summary = "Delete the unused helper"
        result = self.classifier.classify(summary, self.registry)
        assert result.complexity == "light"


# ---------------------------------------------------------------------------
# Task 8: End-to-end integration tests (classifier -> planner -> plan)
# ---------------------------------------------------------------------------

class TestEndToEndClassification:
    """Integration tests — classifier -> planner -> plan.

    These tests create an IntelligentPlanner which builds a real
    AgentRegistry via load_default_paths(). To make the tests
    deterministic regardless of which .claude/agents/ files are
    present, we patch load_default_paths to populate a known set
    of agents.
    """

    _TEST_AGENTS = [
        ("architect", "System design, technical decisions, module boundaries"),
        ("backend-engineer", "Server-side implementation, API endpoints, business logic"),
        ("backend-engineer--python", "Python backend specialist. FastAPI, Django, SQLAlchemy"),
        ("frontend-engineer--react", "React UI components, state management"),
        ("test-engineer", "Write and organize tests"),
        ("code-reviewer", "Quality review before commits"),
        ("auditor", "Safety review for guardrail changes"),
        ("data-engineer", "Database schema, migrations, ETL pipelines"),
        ("data-engineer--databricks", "Databricks pipelines, Delta Lake, Spark"),
    ]

    def setup_method(self):
        """Patch AgentRegistry.load_default_paths to use a fixed agent set."""
        original_load = AgentRegistry.load_default_paths

        def _load_test_agents(registry_self):
            for name, desc in self._TEST_AGENTS:
                agent = AgentDefinition(name=name, description=desc)
                registry_self._agents[name] = agent
            return len(registry_self._agents)

        self._registry_patcher = patch.object(
            AgentRegistry,
            "load_default_paths",
            _load_test_agents,
        )
        self._registry_patcher.start()

    def teardown_method(self):
        self._registry_patcher.stop()

    def test_simple_task_produces_light_plan(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan("Move 3 files to docs/historical")
        assert plan.complexity == "light"
        assert plan.archetype == "direct"
        assert len(plan.phases) == 2  # DIRECT: Implement + Review
        assert plan.total_steps == 2

    def test_complex_task_produces_heavy_plan(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan(
            "Redesign the entire authentication system across frontend and backend"
        )
        assert plan.complexity == "heavy"
        assert len(plan.phases) >= 3
        assert plan.total_steps >= 3

    def test_default_planner_uses_fallback_classifier(self):
        """Default planner (no explicit classifier) should still work via keyword fallback."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add user authentication")
        assert plan.complexity in ("light", "medium", "heavy")
        assert plan.classification_source in ("talent-agent", "keyword-fallback")

    def test_light_plan_has_single_phase_implement(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan("Rename the config file")
        assert plan.complexity == "light"
        assert plan.phases[0].name == "Implement"

    def test_heavy_plan_includes_review_phase(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan(
            "Overhaul the entire data pipeline across frontend and backend"
        )
        assert plan.complexity == "heavy"
        phase_names = [p.name for p in plan.phases]
        assert any("Review" in name or "Test" in name for name in phase_names)

    def test_classification_source_recorded_on_plan(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan("Fix the login bug")
        assert plan.classification_source == "keyword-fallback"

    def test_explicit_complexity_override_bypasses_classifier(self):
        """Passing complexity= to create_plan should skip classification entirely."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        # Summary would be classified as light, but we force heavy
        plan = planner.create_plan(
            "Move 1 file",
            complexity="heavy",
        )
        assert plan.complexity == "heavy"


# ---------------------------------------------------------------------------
# KeywordClassifier agent cap
# ---------------------------------------------------------------------------

class TestKeywordClassifierAgentCap:
    """KeywordClassifier must cap agents by complexity tier."""

    def _large_registry(self) -> AgentRegistry:
        """Build a registry with many agents that share common description words."""
        agents = [
            ("architect", "System design technical decisions module boundaries"),
            ("backend-engineer", "Server-side implementation API endpoints business logic"),
            ("backend-engineer--python", "Python backend specialist FastAPI Django SQLAlchemy"),
            ("frontend-engineer", "Client-side UI components state management"),
            ("frontend-engineer--react", "React UI components state management hooks"),
            ("test-engineer", "Write and organize tests coverage suites"),
            ("code-reviewer", "Quality review before commits code style"),
            ("auditor", "Safety review for guardrail changes compliance"),
            ("security-reviewer", "Security audit OWASP auth secrets vulnerabilities"),
            ("data-engineer", "Database schema migrations ETL pipelines data"),
            ("data-analyst", "Business intelligence reporting data queries KPIs"),
            ("data-scientist", "Statistical analysis machine learning modeling data"),
            ("devops-engineer", "Infrastructure CI/CD Docker deployment configuration"),
            ("visualization-expert", "Charts dashboards visual data storytelling"),
            ("talent-builder", "Create new agent definitions knowledge packs"),
            ("subject-matter-expert", "Domain-specific business operations compliance"),
        ]
        reg = AgentRegistry()
        for name, desc in agents:
            agent = AgentDefinition(name=name, description=desc)
            reg._agents[name] = agent
        return reg

    def test_heavy_keyword_capped_at_five(self):
        """A broad heavy task against a large registry must not exceed 5 agents."""
        registry = self._large_registry()
        classifier = KeywordClassifier()
        result = classifier.classify(
            "Redesign the entire authentication system across frontend and backend "
            "with new database schema, API endpoints, and comprehensive test coverage",
            registry,
        )
        assert result.complexity == "heavy"
        assert len(result.agents) <= 5

    def test_medium_keyword_capped_at_three(self):
        """A medium task must not exceed 3 agents even with many registry matches."""
        registry = self._large_registry()
        classifier = KeywordClassifier()
        result = classifier.classify(
            "Implement user profile endpoint with database migration",
            registry,
        )
        assert result.complexity == "medium"
        assert len(result.agents) <= 3

    def test_light_keyword_returns_single_agent(self):
        registry = self._large_registry()
        classifier = KeywordClassifier()
        result = classifier.classify("Move 2 files to a new folder", registry)
        assert result.complexity == "light"
        assert len(result.agents) == 1

    def test_irrelevant_agents_excluded_from_pure_backend_task(self):
        """A pure Python backend task should not include visualization, talent, etc."""
        registry = self._large_registry()
        classifier = KeywordClassifier()
        result = classifier.classify(
            "Fix the broken API endpoint that returns 500 errors on user login",
            registry,
        )
        irrelevant = {"visualization-expert", "talent-builder", "data-scientist", "data-analyst"}
        assert not irrelevant.intersection(result.agents), (
            f"Irrelevant agents in roster: {irrelevant.intersection(result.agents)}"
        )

    def test_flavoured_variant_not_duplicated_with_base(self):
        """If base agent (e.g. backend-engineer) is a default, its --python variant
        should not also appear as a scored extra."""
        registry = self._large_registry()
        classifier = KeywordClassifier()
        result = classifier.classify(
            "Add a new API endpoint for user management",
            registry,
        )
        backend_agents = [a for a in result.agents if a.startswith("backend-engineer")]
        assert len(backend_agents) <= 1, (
            f"Multiple backend-engineer variants in roster: {backend_agents}"
        )


# ---------------------------------------------------------------------------
# _score_task_type — word-boundary scoring regression tests
# ---------------------------------------------------------------------------

class TestScoreTaskType:
    """Regression tests for the shared task-type scoring function.

    Uses the production keyword list from planner._TASK_TYPE_KEYWORDS
    to verify scoring behaves correctly against real-world task summaries.
    """

    @pytest.fixture(autouse=True)
    def _load_keywords(self):
        from agent_baton.core.engine.planner import _TASK_TYPE_KEYWORDS
        self.keywords = _TASK_TYPE_KEYWORDS

    # -- Correct primary classifications --

    @pytest.mark.parametrize("summary,expected", [
        ("Implement Tiers 2, 3, and 4 of the bead memory system", "new-feature"),
        ("Add OAuth2 login support", "new-feature"),
        ("Build and integrate the event bus pipeline", "new-feature"),
        ("Create a REST API for user management", "new-feature"),
        ("Develop the notification subsystem", "new-feature"),
        ("Fix the broken login endpoint", "bug-fix"),
        ("Fix bug where error crashes the report generator", "bug-fix"),
        ("The auth token is broken and crashes on refresh", "bug-fix"),
        ("Migrate the database to PostgreSQL", "migration"),
        ("Move 3 files to docs/historical", "migration"),
        ("Refactor the auth module for readability", "refactor"),
        ("Simplify the payment service logic", "refactor"),
        ("Analyze user retention data", "data-analysis"),
        ("Build a query to compute monthly KPI metrics", "data-analysis"),
        ("Write unit tests for the parser module", "test"),
        ("Add integration tests for the API layer", "test"),
        ("Document the API endpoints in the wiki", "documentation"),
        ("Update the readme with new instructions", "documentation"),
    ])
    def test_correct_classification(self, summary: str, expected: str):
        assert _score_task_type(summary, self.keywords) == expected

    # -- False positive regression tests --

    @pytest.mark.parametrize("summary", [
        "Remove the deprecated method and improve the API",
        "Deploy the latest version to staging",
        "Add a prefix to all logger output lines",
        "Renew the SSL certificates",
        "Build a cleaner interface for the dashboard",
        "Add error report generation to the logging pipeline",
        "Implement the memory system per docs/plans/execution.md",
    ])
    def test_no_false_positive_away_from_new_feature(self, summary: str):
        """These summaries should NOT be misclassified as non-feature types."""
        result = _score_task_type(summary, self.keywords)
        assert result == "new-feature", (
            f"{summary!r} classified as {result!r}, expected 'new-feature'"
        )

    @pytest.mark.parametrize("summary", [
        "Deploy the latest prefix configuration",
        "Contest the billing statement format",
        "The greatest improvement to date",
    ])
    def test_no_substring_false_positives(self, summary: str):
        """Words containing keywords as substrings must not trigger matches."""
        result = _score_task_type(summary, self.keywords)
        assert result == "new-feature", (
            f"{summary!r} should default to new-feature, got {result!r}"
        )

    # -- Scoring tie-break and dominance --

    def test_higher_score_wins_over_list_order(self):
        # "fix" (1 bug-fix hit) vs "add" + "feature" (2 new-feature hits)
        result = _score_task_type("fix something and add a feature", self.keywords)
        assert result == "new-feature"

    def test_equal_score_prefers_earlier_list_entry(self):
        # new-feature is first in list, so wins ties
        result = _score_task_type("fix something and add something", self.keywords)
        assert result == "new-feature"

    def test_dominant_type_wins(self):
        # Multiple bug-fix signals beat a single new-feature signal
        result = _score_task_type("fix the broken crash in signup", self.keywords)
        assert result == "bug-fix"

    # -- Edge cases --

    def test_empty_summary_defaults(self):
        assert _score_task_type("", self.keywords) == "new-feature"

    def test_single_char_defaults(self):
        assert _score_task_type("x", self.keywords) == "new-feature"

    def test_no_keywords_defaults(self):
        assert _score_task_type("do something", self.keywords) == "new-feature"

    def test_case_insensitive(self):
        assert _score_task_type("FIX THE BUG", self.keywords) == "bug-fix"
