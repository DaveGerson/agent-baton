"""Tests for agent_baton.core.engine.classifier."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.classifier import (
    FallbackClassifier,
    HaikuClassifier,
    KeywordClassifier,
    TaskClassification,
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
# Task 3: HaikuClassifier
# ---------------------------------------------------------------------------

class TestHaikuClassifier:
    def setup_method(self):
        self.registry = _make_registry()

    def test_builds_prompt_with_all_agents(self):
        classifier = HaikuClassifier()
        prompt = classifier._build_prompt("Move 3 files", self.registry)
        assert "Move 3 files" in prompt
        assert "backend-engineer:" in prompt or "backend-engineer --" in prompt
        assert "data-engineer--databricks:" in prompt or "data-engineer--databricks --" in prompt

    def test_parses_valid_json_response(self):
        classifier = HaikuClassifier()
        response_json = json.dumps({
            "task_type": "migration",
            "complexity": "light",
            "agents": ["backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "Simple file move, no architecture needed",
        })
        result = classifier._parse_response(response_json, self.registry)
        assert result.task_type == "migration"
        assert result.complexity == "light"
        assert result.agents == ["backend-engineer"]
        assert result.source == "haiku"

    def test_rejects_agents_not_in_registry(self):
        classifier = HaikuClassifier()
        response_json = json.dumps({
            "task_type": "migration",
            "complexity": "light",
            "agents": ["made-up-agent", "backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "test",
        })
        result = classifier._parse_response(response_json, self.registry)
        assert "made-up-agent" not in result.agents
        assert "backend-engineer" in result.agents

    def test_raises_on_empty_agents_after_filtering(self):
        classifier = HaikuClassifier()
        response_json = json.dumps({
            "task_type": "migration",
            "complexity": "light",
            "agents": ["made-up-agent"],
            "phases": ["Implement"],
            "reasoning": "test",
        })
        with pytest.raises(ValueError, match="agents"):
            classifier._parse_response(response_json, self.registry)

    def test_raises_on_invalid_json(self):
        classifier = HaikuClassifier()
        with pytest.raises(ValueError):
            classifier._parse_response("not json", self.registry)

    @patch("agent_baton.core.engine.classifier._call_haiku")
    def test_classify_calls_api(self, mock_call):
        mock_call.return_value = json.dumps({
            "task_type": "migration",
            "complexity": "light",
            "agents": ["backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "Simple file move",
        })
        classifier = HaikuClassifier()
        result = classifier.classify("Move 3 files", self.registry)
        assert result.complexity == "light"
        assert result.source == "haiku"
        mock_call.assert_called_once()


# ---------------------------------------------------------------------------
# Task 4: FallbackClassifier
# ---------------------------------------------------------------------------

class TestFallbackClassifier:
    def setup_method(self):
        self.registry = _make_registry()

    @patch("agent_baton.core.engine.classifier._call_haiku")
    def test_uses_haiku_when_available(self, mock_call):
        mock_call.return_value = json.dumps({
            "task_type": "migration",
            "complexity": "light",
            "agents": ["backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "Simple move",
        })
        classifier = FallbackClassifier()
        result = classifier.classify("Move 3 files", self.registry)
        assert result.source == "haiku"
        mock_call.assert_called_once()

    @patch("agent_baton.core.engine.classifier._call_haiku", side_effect=Exception("API error"))
    def test_falls_back_on_api_error(self, mock_call):
        classifier = FallbackClassifier()
        result = classifier.classify("Move 3 files", self.registry)
        assert result.source == "keyword-fallback"

    @patch("agent_baton.core.engine.classifier._call_haiku", side_effect=ImportError("no anthropic"))
    def test_falls_back_on_missing_sdk(self, mock_call):
        classifier = FallbackClassifier()
        result = classifier.classify("Move 3 files", self.registry)
        assert result.source == "keyword-fallback"

    @patch("agent_baton.core.engine.classifier._call_haiku", return_value="not json")
    def test_falls_back_on_bad_response(self, mock_call):
        classifier = FallbackClassifier()
        result = classifier.classify("Move 3 files", self.registry)
        assert result.source == "keyword-fallback"


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
# Edge cases: HaikuClassifier markdown-wrapped JSON
# ---------------------------------------------------------------------------

class TestHaikuClassifierMarkdownWrapping:
    """HaikuClassifier must strip markdown code fences before parsing JSON."""

    def setup_method(self):
        self.classifier = HaikuClassifier()
        self.registry = _make_registry()

    def _valid_payload(self) -> dict:
        return {
            "task_type": "new-feature",
            "complexity": "medium",
            "agents": ["backend-engineer"],
            "phases": ["Design", "Implement"],
            "reasoning": "Moderate new feature",
        }

    def test_json_fenced_with_backticks_parsed(self):
        raw = "```\n" + json.dumps(self._valid_payload()) + "\n```"
        result = self.classifier._parse_response(raw, self.registry)
        assert result.task_type == "new-feature"
        assert result.source == "haiku"

    def test_json_fenced_with_language_tag_parsed(self):
        raw = "```json\n" + json.dumps(self._valid_payload()) + "\n```"
        result = self.classifier._parse_response(raw, self.registry)
        assert result.complexity == "medium"
        assert result.source == "haiku"

    def test_invalid_complexity_in_markdown_json_defaults_to_medium(self):
        payload = self._valid_payload()
        payload["complexity"] = "extreme"  # invalid
        raw = "```json\n" + json.dumps(payload) + "\n```"
        result = self.classifier._parse_response(raw, self.registry)
        assert result.complexity == "medium"

    def test_invalid_task_type_in_markdown_json_defaults_to_new_feature(self):
        payload = self._valid_payload()
        payload["task_type"] = "unknown-type"  # invalid
        raw = "```json\n" + json.dumps(payload) + "\n```"
        result = self.classifier._parse_response(raw, self.registry)
        assert result.task_type == "new-feature"

    def test_missing_phases_in_response_defaults_to_implement(self):
        payload = self._valid_payload()
        del payload["phases"]
        raw = json.dumps(payload)
        result = self.classifier._parse_response(raw, self.registry)
        assert result.phases == ["Implement"]

    def test_empty_phases_in_response_defaults_to_implement(self):
        payload = self._valid_payload()
        payload["phases"] = []
        raw = json.dumps(payload)
        result = self.classifier._parse_response(raw, self.registry)
        assert result.phases == ["Implement"]

    def test_missing_reasoning_uses_fallback_string(self):
        payload = self._valid_payload()
        del payload["reasoning"]
        raw = json.dumps(payload)
        result = self.classifier._parse_response(raw, self.registry)
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0


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
    """Integration tests — classifier -> planner -> plan."""

    def test_simple_task_produces_light_plan(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan("Move 3 files to docs/historical")
        assert plan.complexity == "light"
        assert len(plan.phases) == 1
        assert plan.total_steps == 1

    def test_complex_task_produces_heavy_plan(self):
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner(task_classifier=KeywordClassifier())
        plan = planner.create_plan(
            "Redesign the entire authentication system across frontend and backend"
        )
        assert plan.complexity == "heavy"
        assert len(plan.phases) >= 3
        assert plan.total_steps >= 3

    @patch("agent_baton.core.engine.classifier._call_haiku", side_effect=Exception("no api"))
    def test_default_planner_uses_fallback_classifier(self, _mock):
        """Default planner (no explicit classifier) should still work via keyword fallback."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add user authentication")
        assert plan.complexity in ("light", "medium", "heavy")
        assert plan.classification_source in ("haiku", "keyword-fallback")

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

    @patch("agent_baton.core.engine.classifier._call_haiku")
    def test_haiku_classifier_source_recorded_on_plan(self, mock_call):
        from agent_baton.core.engine.planner import IntelligentPlanner

        mock_call.return_value = json.dumps({
            "task_type": "bug-fix",
            "complexity": "light",
            "agents": ["backend-engineer"],
            "phases": ["Implement"],
            "reasoning": "Simple fix",
        })
        planner = IntelligentPlanner(task_classifier=HaikuClassifier())
        plan = planner.create_plan("Fix the login bug")
        assert plan.classification_source == "haiku"
        assert plan.complexity == "light"

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
# Haiku agent cap by complexity
# ---------------------------------------------------------------------------

class TestHaikuClassifierAgentCap:
    """Haiku must cap agents based on complexity tier to prevent bloated plans."""

    def setup_method(self):
        self.classifier = HaikuClassifier()
        self.registry = _make_registry()

    def test_light_complexity_caps_at_one_agent(self):
        response_json = json.dumps({
            "task_type": "new-feature",
            "complexity": "light",
            "agents": ["backend-engineer", "architect", "test-engineer"],
            "phases": ["Implement"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(response_json, self.registry)
        assert len(result.agents) == 1
        assert result.agents == ["backend-engineer"]

    def test_medium_complexity_caps_at_three_agents(self):
        response_json = json.dumps({
            "task_type": "new-feature",
            "complexity": "medium",
            "agents": [
                "backend-engineer", "architect", "test-engineer",
                "code-reviewer", "auditor",
            ],
            "phases": ["Design", "Implement", "Test"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(response_json, self.registry)
        assert len(result.agents) == 3

    def test_heavy_complexity_caps_at_five_agents(self):
        response_json = json.dumps({
            "task_type": "new-feature",
            "complexity": "heavy",
            "agents": [
                "backend-engineer", "architect", "test-engineer",
                "code-reviewer", "auditor", "data-engineer",
                "frontend-engineer--react",
            ],
            "phases": ["Design", "Implement", "Test", "Review"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(response_json, self.registry)
        assert len(result.agents) == 5

    def test_agents_under_cap_are_not_trimmed(self):
        response_json = json.dumps({
            "task_type": "new-feature",
            "complexity": "medium",
            "agents": ["backend-engineer", "test-engineer"],
            "phases": ["Implement", "Test"],
            "reasoning": "test",
        })
        result = self.classifier._parse_response(response_json, self.registry)
        assert len(result.agents) == 2
