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
