"""Tests for agent_baton data model classes."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_baton.models.agent import AgentDefinition
from agent_baton.models.enums import (
    AgentCategory,
    FailureClass,
)
from agent_baton.models.plan import MissionLogEntry


# ---------------------------------------------------------------------------
# AgentDefinition
# ---------------------------------------------------------------------------

class TestAgentDefinitionBaseName:
    def test_unflavored_name_returns_self(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.base_name == "architect"

    def test_flavored_name_strips_suffix(self):
        agent = AgentDefinition(name="backend-engineer--python", description="")
        assert agent.base_name == "backend-engineer"

    def test_flavored_frontend_strips_suffix(self):
        agent = AgentDefinition(name="frontend-engineer--react", description="")
        assert agent.base_name == "frontend-engineer"

    def test_hyphenated_base_without_double_dash(self):
        agent = AgentDefinition(name="test-engineer", description="")
        assert agent.base_name == "test-engineer"


class TestAgentDefinitionFlavor:
    def test_unflavored_returns_none(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.flavor is None

    def test_flavored_returns_suffix(self):
        agent = AgentDefinition(name="backend-engineer--python", description="")
        assert agent.flavor == "python"

    def test_react_flavor(self):
        agent = AgentDefinition(name="frontend-engineer--react", description="")
        assert agent.flavor == "react"

    def test_node_flavor(self):
        agent = AgentDefinition(name="backend-engineer--node", description="")
        assert agent.flavor == "node"


class TestAgentDefinitionIsFlavored:
    def test_unflavored_is_false(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.is_flavored is False

    def test_flavored_is_true(self):
        agent = AgentDefinition(name="backend-engineer--python", description="")
        assert agent.is_flavored is True

    def test_hyphenated_name_without_double_dash_is_false(self):
        agent = AgentDefinition(name="test-engineer", description="")
        assert agent.is_flavored is False


class TestAgentDefinitionCategory:
    @pytest.mark.parametrize("name,expected", [
        ("architect", AgentCategory.ENGINEERING),
        ("backend-engineer", AgentCategory.ENGINEERING),
        ("backend-engineer--python", AgentCategory.ENGINEERING),
        ("frontend-engineer", AgentCategory.ENGINEERING),
        ("frontend-engineer--react", AgentCategory.ENGINEERING),
        ("devops-engineer", AgentCategory.ENGINEERING),
        ("test-engineer", AgentCategory.ENGINEERING),
        ("data-engineer", AgentCategory.ENGINEERING),
    ])
    def test_engineering_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("data-scientist", AgentCategory.DATA),
        ("data-analyst", AgentCategory.DATA),
        ("visualization-expert", AgentCategory.DATA),
    ])
    def test_data_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("subject-matter-expert", AgentCategory.DOMAIN),
    ])
    def test_domain_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("security-reviewer", AgentCategory.REVIEW),
        ("code-reviewer", AgentCategory.REVIEW),
        ("auditor", AgentCategory.REVIEW),
    ])
    def test_review_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("talent-builder", AgentCategory.META),
        ("orchestrator", AgentCategory.META),
    ])
    def test_meta_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    def test_unknown_name_defaults_to_engineering(self):
        agent = AgentDefinition(name="unknown-widget", description="")
        assert agent.category == AgentCategory.ENGINEERING


class TestAgentDefinitionDefaults:
    def test_default_model_is_sonnet(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.model == "sonnet"

    def test_default_permission_mode_is_default(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.permission_mode == "default"

    def test_default_color_is_none(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.color is None

    def test_default_tools_is_empty_list(self):
        agent = AgentDefinition(name="architect", description="")
        assert agent.tools == []


# ---------------------------------------------------------------------------
# MissionLogEntry
# ---------------------------------------------------------------------------

class TestMissionLogEntryToMarkdown:
    def _make_entry(self, **kwargs) -> MissionLogEntry:
        defaults = dict(
            agent_name="architect",
            status="COMPLETE",
            assignment="Design the API",
            timestamp=datetime(2026, 1, 15, 10, 0, 0),
        )
        defaults.update(kwargs)
        return MissionLogEntry(**defaults)

    def test_contains_agent_name(self):
        entry = self._make_entry(agent_name="backend-engineer--python")
        md = entry.to_markdown()
        assert "backend-engineer--python" in md

    def test_contains_status(self):
        entry = self._make_entry(status="FAILED")
        md = entry.to_markdown()
        assert "FAILED" in md

    def test_contains_assignment(self):
        entry = self._make_entry(assignment="Write the migration script")
        md = entry.to_markdown()
        assert "Write the migration script" in md

    def test_result_appears_when_set(self):
        entry = self._make_entry(result="Migration successful")
        md = entry.to_markdown()
        assert "Migration successful" in md

    def test_result_absent_when_empty(self):
        entry = self._make_entry(result="")
        md = entry.to_markdown()
        assert "Result:" not in md

    def test_files_appear_when_set(self):
        entry = self._make_entry(files=["src/api.py", "tests/test_api.py"])
        md = entry.to_markdown()
        assert "src/api.py" in md
        assert "tests/test_api.py" in md

    def test_decisions_appear_as_list(self):
        entry = self._make_entry(decisions=["Used FastAPI", "SQLite for dev"])
        md = entry.to_markdown()
        assert "Used FastAPI" in md
        assert "SQLite for dev" in md
        assert "Decisions:" in md

    def test_issues_appear_as_list(self):
        entry = self._make_entry(issues=["Auth not implemented"])
        md = entry.to_markdown()
        assert "Auth not implemented" in md
        assert "Issues:" in md

    def test_handoff_appears_when_set(self):
        entry = self._make_entry(handoff="Hand off to test-engineer")
        md = entry.to_markdown()
        assert "Hand off to test-engineer" in md

    def test_commit_hash_appears_when_set(self):
        entry = self._make_entry(commit_hash="abc1234")
        md = entry.to_markdown()
        assert "abc1234" in md

    def test_failure_class_appears_when_set(self):
        entry = self._make_entry(
            status="FAILED",
            failure_class=FailureClass.QUALITY,
        )
        md = entry.to_markdown()
        assert "Quality Failure" in md

    def test_failure_class_absent_when_none(self):
        entry = self._make_entry(failure_class=None)
        md = entry.to_markdown()
        assert "Failure class:" not in md

    def test_timestamp_appears_in_header(self):
        entry = self._make_entry(timestamp=datetime(2026, 1, 15, 10, 0, 0))
        md = entry.to_markdown()
        assert "2026-01-15" in md

    def test_returns_string(self):
        entry = self._make_entry()
        assert isinstance(entry.to_markdown(), str)

    def test_ends_with_blank_line(self):
        entry = self._make_entry()
        md = entry.to_markdown()
        assert md.endswith("\n")
