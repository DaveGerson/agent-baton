"""Tests for agent_baton.core.context.ContextManager."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from agent_baton.core.context import ContextManager
from agent_baton.models.enums import (
    BudgetTier,
    ExecutionMode,
    GitStrategy,
    RiskLevel,
)
from agent_baton.models.plan import (
    AgentAssignment,
    ExecutionPlan,
    MissionLogEntry,
    Phase,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_plan(task: str = "Test task") -> ExecutionPlan:
    return ExecutionPlan(
        task_summary=task,
        risk_level=RiskLevel.LOW,
        budget_tier=BudgetTier.STANDARD,
        execution_mode=ExecutionMode.PHASED,
        git_strategy=GitStrategy.COMMIT_PER_AGENT,
        phases=[],
    )


def _simple_entry(**kwargs) -> MissionLogEntry:
    defaults = dict(
        agent_name="architect",
        status="COMPLETE",
        assignment="Design the schema",
        timestamp=datetime(2026, 1, 15, 12, 0, 0),
    )
    defaults.update(kwargs)
    return MissionLogEntry(**defaults)


# ---------------------------------------------------------------------------
# ensure_dir
# ---------------------------------------------------------------------------

class TestEnsureDir:
    def test_creates_directory_when_missing(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert not tmp_team_context.exists()
        cm.ensure_dir()
        assert tmp_team_context.is_dir()

    def test_idempotent_when_directory_already_exists(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.ensure_dir()
        cm.ensure_dir()  # should not raise
        assert tmp_team_context.is_dir()

    def test_creates_nested_directories(self, tmp_path: Path):
        deep_dir = tmp_path / "a" / "b" / "c" / "context"
        cm = ContextManager(deep_dir)
        cm.ensure_dir()
        assert deep_dir.is_dir()

    def test_dir_property_returns_configured_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.dir == tmp_team_context


# ---------------------------------------------------------------------------
# write_plan / read_plan
# ---------------------------------------------------------------------------

class TestWriteReadPlan:
    def test_write_plan_creates_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan("Build the API")
        cm.write_plan(plan)
        assert cm.plan_path.exists()

    def test_plan_path_is_plan_md(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.plan_path.name == "plan.md"

    def test_read_plan_returns_written_content(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan("My important task")
        cm.write_plan(plan)
        content = cm.read_plan()
        assert content is not None
        assert "My important task" in content

    def test_read_plan_returns_none_when_no_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_plan() is None

    def test_write_plan_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan()
        result = cm.write_plan(plan)
        assert result == cm.plan_path

    def test_plan_roundtrip_preserves_risk_level(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan()
        plan.risk_level = RiskLevel.HIGH
        cm.write_plan(plan)
        content = cm.read_plan()
        assert "HIGH" in content

    def test_write_plan_creates_parent_directory(self, tmp_path: Path):
        ctx_dir = tmp_path / "new" / "nested" / "context"
        cm = ContextManager(ctx_dir)
        plan = _simple_plan()
        cm.write_plan(plan)
        assert cm.plan_path.exists()


# ---------------------------------------------------------------------------
# write_context
# ---------------------------------------------------------------------------

class TestWriteContext:
    def test_creates_context_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Build payment module")
        assert cm.context_path.exists()

    def test_context_path_is_context_md(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.context_path.name == "context.md"

    def test_task_appears_in_header(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Migrate the database")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "Migrate the database" in content

    def test_stack_section_appears(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", stack="Python 3.12, FastAPI 0.115")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Stack" in content
        assert "Python 3.12, FastAPI 0.115" in content

    def test_architecture_section_appears(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", architecture="Monolith with service modules")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Architecture" in content
        assert "Monolith with service modules" in content

    def test_conventions_section_appears(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", conventions="PEP 8, type hints required")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Conventions" in content
        assert "PEP 8, type hints required" in content

    def test_guardrails_section_appears(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", guardrails="No secrets in code")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Guardrails" in content
        assert "No secrets in code" in content

    def test_agent_assignments_section_appears(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", agent_assignments="Phase 1: architect")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Agent Assignments" in content
        assert "Phase 1: architect" in content

    def test_domain_context_section_appears_when_provided(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", domain_context="HIPAA compliance required")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Domain Context" in content
        assert "HIPAA compliance required" in content

    def test_domain_context_absent_when_empty(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", domain_context="")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "## Domain Context" not in content

    def test_default_placeholder_for_missing_stack(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task")
        content = cm.context_path.read_text(encoding="utf-8")
        assert "_Not yet researched._" in content

    def test_returns_context_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.write_context(task="Task")
        assert result == cm.context_path

    def test_read_context_returns_written_content(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="My task")
        content = cm.read_context()
        assert content is not None
        assert "My task" in content

    def test_read_context_returns_none_when_no_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_context() is None


# ---------------------------------------------------------------------------
# init_mission_log
# ---------------------------------------------------------------------------

class TestInitMissionLog:
    def test_creates_mission_log_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Deploy service")
        assert cm.mission_log_path.exists()

    def test_mission_log_path_is_mission_log_md(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.mission_log_path.name == "mission-log.md"

    def test_task_name_in_header(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Deploy authentication service")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "Deploy authentication service" in content

    def test_risk_level_in_log(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task", risk_level="HIGH")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "HIGH" in content

    def test_default_risk_level_is_low(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "LOW" in content

    def test_starts_with_h1(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert content.startswith("# Mission Log")

    def test_contains_separator(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "---" in content

    def test_returns_mission_log_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.init_mission_log("Task")
        assert result == cm.mission_log_path


# ---------------------------------------------------------------------------
# append_to_mission_log
# ---------------------------------------------------------------------------

class TestAppendToMissionLog:
    def test_appends_entry_after_init(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        entry = _simple_entry(agent_name="architect", status="COMPLETE")
        cm.append_to_mission_log(entry)
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "architect" in content
        assert "COMPLETE" in content

    def test_auto_initializes_when_log_does_not_exist(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        entry = _simple_entry()
        cm.append_to_mission_log(entry)
        assert cm.mission_log_path.exists()

    def test_multiple_entries_are_all_present(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        cm.append_to_mission_log(_simple_entry(agent_name="architect", status="COMPLETE"))
        cm.append_to_mission_log(
            _simple_entry(agent_name="backend-engineer--python", status="COMPLETE")
        )
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "architect" in content
        assert "backend-engineer--python" in content

    def test_entries_are_separated_by_hr(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        cm.append_to_mission_log(_simple_entry())
        content = cm.mission_log_path.read_text(encoding="utf-8")
        # The append adds "---\n\n" after each entry
        assert "---" in content

    def test_original_header_preserved_after_append(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Important task")
        cm.append_to_mission_log(_simple_entry())
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "# Mission Log — Important task" in content


# ---------------------------------------------------------------------------
# read_mission_log
# ---------------------------------------------------------------------------

class TestReadMissionLog:
    def test_returns_content_after_init(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        content = cm.read_mission_log()
        assert content is not None
        assert len(content) > 0

    def test_returns_none_when_no_log(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_mission_log() is None

    def test_returns_string(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        assert isinstance(cm.read_mission_log(), str)


# ---------------------------------------------------------------------------
# write_profile / read_profile / profile_exists
# ---------------------------------------------------------------------------

class TestProfileRoundtrip:
    def test_write_profile_creates_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_profile("# Codebase Profile\n\nPython monorepo.")
        assert cm.profile_path.exists()

    def test_profile_path_is_codebase_profile_md(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.profile_path.name == "codebase-profile.md"

    def test_read_profile_returns_written_content(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_profile("Python monorepo using Poetry.")
        result = cm.read_profile()
        assert result is not None
        assert "Python monorepo using Poetry." in result

    def test_read_profile_returns_none_when_missing(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_profile() is None

    def test_profile_exists_false_when_no_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.profile_exists() is False

    def test_profile_exists_true_after_write(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_profile("Some profile content.")
        assert cm.profile_exists() is True

    def test_write_profile_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.write_profile("content")
        assert result == cm.profile_path

    def test_profile_content_roundtrip_exact(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        original = "# Codebase\n\n- Language: Python\n- Framework: FastAPI\n"
        cm.write_profile(original)
        assert cm.read_profile() == original


# ---------------------------------------------------------------------------
# recovery_files_exist
# ---------------------------------------------------------------------------

class TestRecoveryFilesExist:
    def test_all_false_when_no_files_written(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        state = cm.recovery_files_exist()
        assert state == {
            "plan": False,
            "context": False,
            "mission_log": False,
            "profile": False,
        }

    def test_plan_true_after_write_plan(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_plan(_simple_plan())
        state = cm.recovery_files_exist()
        assert state["plan"] is True
        assert state["context"] is False

    def test_context_true_after_write_context(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task")
        state = cm.recovery_files_exist()
        assert state["context"] is True

    def test_mission_log_true_after_init(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        state = cm.recovery_files_exist()
        assert state["mission_log"] is True

    def test_profile_true_after_write_profile(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_profile("content")
        state = cm.recovery_files_exist()
        assert state["profile"] is True

    def test_all_true_after_full_setup(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_plan(_simple_plan())
        cm.write_context(task="Task")
        cm.init_mission_log("Task")
        cm.write_profile("content")
        state = cm.recovery_files_exist()
        assert all(state.values())

    def test_returns_dict_with_four_keys(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        state = cm.recovery_files_exist()
        assert set(state.keys()) == {"plan", "context", "mission_log", "profile"}

    def test_partial_state_is_accurate(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_plan(_simple_plan())
        cm.write_profile("some profile")
        state = cm.recovery_files_exist()
        assert state["plan"] is True
        assert state["profile"] is True
        assert state["context"] is False
        assert state["mission_log"] is False
