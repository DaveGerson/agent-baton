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
# Path properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prop,expected_name", [
    ("plan_path", "plan.md"),
    ("context_path", "context.md"),
    ("mission_log_path", "mission-log.md"),
])
def test_path_property_has_correct_filename(tmp_team_context: Path, prop, expected_name):
    cm = ContextManager(tmp_team_context)
    assert getattr(cm, prop).name == expected_name


# ---------------------------------------------------------------------------
# write_plan / read_plan
# ---------------------------------------------------------------------------

class TestWriteReadPlan:
    def test_write_plan_creates_file_and_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan("Build the API")
        result = cm.write_plan(plan)
        assert cm.plan_path.exists()
        assert result == cm.plan_path

    def test_read_plan_returns_task_summary(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_plan(_simple_plan("My important task"))
        content = cm.read_plan()
        assert content is not None
        assert "My important task" in content

    def test_read_plan_returns_none_when_no_file(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_plan() is None

    def test_plan_roundtrip_preserves_risk_level(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        plan = _simple_plan()
        plan.risk_level = RiskLevel.HIGH
        cm.write_plan(plan)
        assert "HIGH" in cm.read_plan()

    def test_write_plan_creates_parent_directory(self, tmp_path: Path):
        ctx_dir = tmp_path / "new" / "nested" / "context"
        cm = ContextManager(ctx_dir)
        cm.write_plan(_simple_plan())
        assert cm.plan_path.exists()


# ---------------------------------------------------------------------------
# write_context
# ---------------------------------------------------------------------------

class TestWriteContext:
    def test_creates_context_file_and_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.write_context(task="Build payment module")
        assert cm.context_path.exists()
        assert result == cm.context_path

    def test_task_appears_in_header(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Migrate the database")
        assert "Migrate the database" in cm.context_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("kwarg,section_header,section_value", [
        ("stack", "## Stack", "Python 3.12, FastAPI 0.115"),
        ("architecture", "## Architecture", "Monolith with service modules"),
        ("conventions", "## Conventions", "PEP 8, type hints required"),
        ("guardrails", "## Guardrails", "No secrets in code"),
        ("agent_assignments", "## Agent Assignments", "Phase 1: architect"),
        ("domain_context", "## Domain Context", "HIPAA compliance required"),
    ])
    def test_optional_section_appears_when_provided(
        self, tmp_team_context: Path, kwarg, section_header, section_value
    ):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", **{kwarg: section_value})
        content = cm.context_path.read_text(encoding="utf-8")
        assert section_header in content
        assert section_value in content

    def test_domain_context_absent_when_empty(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task", domain_context="")
        assert "## Domain Context" not in cm.context_path.read_text(encoding="utf-8")

    def test_default_placeholder_for_missing_stack(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_context(task="Task")
        assert "_Not yet researched._" in cm.context_path.read_text(encoding="utf-8")

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
# init_mission_log / append_to_mission_log / read_mission_log
# ---------------------------------------------------------------------------

class TestMissionLog:
    def test_init_creates_file_and_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.init_mission_log("Deploy service")
        assert cm.mission_log_path.exists()
        assert result == cm.mission_log_path

    def test_init_content_structure(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Deploy authentication service", risk_level="HIGH")
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert content.startswith("# Mission Log")
        assert "Deploy authentication service" in content
        assert "HIGH" in content
        assert "---" in content

    def test_init_default_risk_level_is_low(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        assert "LOW" in cm.mission_log_path.read_text(encoding="utf-8")

    def test_append_entry_after_init(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Important task")
        cm.append_to_mission_log(_simple_entry(agent_name="architect", status="COMPLETE"))
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "architect" in content
        assert "COMPLETE" in content
        assert "# Mission Log — Important task" in content

    def test_auto_initializes_when_log_does_not_exist(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.append_to_mission_log(_simple_entry())
        assert cm.mission_log_path.exists()

    def test_multiple_entries_all_present(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        cm.append_to_mission_log(_simple_entry(agent_name="architect"))
        cm.append_to_mission_log(_simple_entry(agent_name="backend-engineer--python"))
        content = cm.mission_log_path.read_text(encoding="utf-8")
        assert "architect" in content
        assert "backend-engineer--python" in content

    def test_read_mission_log_after_init(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.init_mission_log("Task")
        content = cm.read_mission_log()
        assert isinstance(content, str)
        assert len(content) > 0

    def test_read_mission_log_returns_none_when_no_log(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_mission_log() is None


# ---------------------------------------------------------------------------
# write_profile / read_profile / profile_exists
# ---------------------------------------------------------------------------

class TestProfileRoundtrip:
    def test_write_creates_file_and_returns_path(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        result = cm.write_profile("# Codebase Profile\n\nPython monorepo.")
        assert cm.profile_path.exists()
        assert result == cm.profile_path

    def test_profile_path_filename(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.profile_path.name == "codebase-profile.md"

    def test_content_roundtrip_exact(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        original = "# Codebase\n\n- Language: Python\n- Framework: FastAPI\n"
        cm.write_profile(original)
        assert cm.read_profile() == original

    def test_read_returns_none_when_missing(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.read_profile() is None

    def test_profile_exists_reflects_state(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        assert cm.profile_exists() is False
        cm.write_profile("content")
        assert cm.profile_exists() is True


# ---------------------------------------------------------------------------
# recovery_files_exist
# ---------------------------------------------------------------------------

class TestRecoveryFilesExist:
    def test_all_false_when_no_files_written(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        state = cm.recovery_files_exist()
        assert set(state.keys()) == {"plan", "context", "mission_log", "profile"}
        assert state == {
            "plan": False,
            "context": False,
            "mission_log": False,
            "profile": False,
        }

    def test_all_true_after_full_setup(self, tmp_team_context: Path):
        cm = ContextManager(tmp_team_context)
        cm.write_plan(_simple_plan())
        cm.write_context(task="Task")
        cm.init_mission_log("Task")
        cm.write_profile("content")
        assert all(cm.recovery_files_exist().values())

    @pytest.mark.parametrize("setup_fn,key", [
        (lambda cm: cm.write_plan(_simple_plan()), "plan"),
        (lambda cm: cm.write_context(task="Task"), "context"),
        (lambda cm: cm.init_mission_log("Task"), "mission_log"),
        (lambda cm: cm.write_profile("content"), "profile"),
    ])
    def test_individual_file_flips_its_key(
        self, tmp_team_context: Path, setup_fn, key
    ):
        cm = ContextManager(tmp_team_context)
        setup_fn(cm)
        state = cm.recovery_files_exist()
        assert state[key] is True
        # All other keys remain False
        for k, v in state.items():
            if k != key:
                assert v is False, f"Expected {k}=False but got True"
