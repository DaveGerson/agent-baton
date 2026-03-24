"""Tests for agent_baton.core.pmo.forge.ForgeSession."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.pmo.forge import ForgeSession
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import PmoProject, PmoSignal


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _project(tmp_path: Path, project_id: str = "nds") -> PmoProject:
    project_root = tmp_path / project_id
    project_root.mkdir(exist_ok=True)
    return PmoProject(
        project_id=project_id,
        name=project_id.upper(),
        path=str(project_root),
        program=project_id.upper(),
    )


def _signal(**kwargs) -> PmoSignal:
    defaults = dict(
        signal_id="sig-001",
        signal_type="bug",
        title="Login fails on Safari",
        description="Users can't log in using Safari 16+",
        source_project_id="nds",
    )
    defaults.update(kwargs)
    return PmoSignal(**defaults)


def _step(step_id: str = "1.1", agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="Fix the bug",
    )


def _plan(
    task_id: str = "task-001",
    task_summary: str = "Fix a bug",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    if phases is None:
        phases = [PlanPhase(phase_id=0, name="Fix", steps=[_step()])]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases,
    )


def _mock_planner(returned_plan: MachinePlan | None = None) -> MagicMock:
    planner = MagicMock()
    planner.create_plan.return_value = returned_plan or _plan()
    return planner


def _forge(planner: object, store: PmoStore) -> ForgeSession:
    return ForgeSession(planner=planner, store=store)


# ---------------------------------------------------------------------------
# create_plan
# ---------------------------------------------------------------------------

class TestCreatePlan:
    def test_delegates_to_planner(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.create_plan(
            description="Add login endpoint",
            program="NDS",
            project_id="nds",
        )
        planner.create_plan.assert_called_once()

    def test_returns_plan_from_planner(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        expected_plan = _plan(task_id="planner-task", task_summary="Planner made this")
        planner = _mock_planner(returned_plan=expected_plan)
        forge = _forge(planner, store)
        result = forge.create_plan(
            description="Do something",
            program="NDS",
            project_id="nds",
        )
        assert result is expected_plan

    def test_passes_description_as_task_summary(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.create_plan(
            description="My task description",
            program="NDS",
            project_id="nds",
        )
        call_kwargs = planner.create_plan.call_args
        assert call_kwargs.kwargs.get("task_summary") == "My task description" or \
               call_kwargs.args[0] == "My task description" or \
               "My task description" in str(call_kwargs)

    def test_passes_task_type_to_planner(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.create_plan(
            description="Fix bug",
            program="NDS",
            project_id="nds",
            task_type="bug-fix",
        )
        call_kwargs = planner.create_plan.call_args
        # task_type should be passed through
        all_args = str(call_kwargs)
        assert "bug-fix" in all_args

    def test_passes_project_root_as_path_when_project_found(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.create_plan(
            description="Describe",
            program="NDS",
            project_id="nds",
        )
        call_kwargs = planner.create_plan.call_args
        # project_root should be a Path
        project_root_arg = call_kwargs.kwargs.get("project_root")
        assert project_root_arg is not None
        assert isinstance(project_root_arg, Path)

    def test_project_root_is_none_when_project_not_found(self, tmp_path: Path):
        store = _store(tmp_path)
        # No project registered

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.create_plan(
            description="Describe",
            program="NDS",
            project_id="nonexistent",
        )
        call_kwargs = planner.create_plan.call_args
        project_root_arg = call_kwargs.kwargs.get("project_root")
        assert project_root_arg is None


# ---------------------------------------------------------------------------
# save_plan
# ---------------------------------------------------------------------------

class TestSavePlan:
    def test_writes_plan_json_to_team_context(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        plan = _plan(task_id="saved-task")
        returned_path = forge.save_plan(plan, project)

        expected = Path(project.path) / ".claude" / "team-context" / "plan.json"
        assert returned_path == expected
        assert expected.exists()

    def test_plan_json_contains_valid_plan_data(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        plan = _plan(task_id="json-check", task_summary="Check JSON content")
        path = forge.save_plan(plan, project)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["task_id"] == "json-check"
        assert data["task_summary"] == "Check JSON content"

    def test_writes_plan_md_to_team_context(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        plan = _plan(task_summary="Human readable plan")
        forge.save_plan(plan, project)

        md_path = Path(project.path) / ".claude" / "team-context" / "plan.md"
        assert md_path.exists()

    def test_plan_md_contains_task_summary(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        plan = _plan(task_summary="Refactor login service")
        forge.save_plan(plan, project)

        md_path = Path(project.path) / ".claude" / "team-context" / "plan.md"
        content = md_path.read_text(encoding="utf-8")
        assert "Refactor login service" in content

    def test_creates_team_context_directory(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        # The directory should not exist yet
        context_root = Path(project.path) / ".claude" / "team-context"
        assert not context_root.exists()

        forge.save_plan(_plan(), project)
        assert context_root.is_dir()

    def test_save_plan_no_tmp_file_left(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        forge.save_plan(_plan(), project)
        tmp_file = Path(project.path) / ".claude" / "team-context" / "plan.json.tmp"
        assert not tmp_file.exists()

    def test_save_plan_returns_path_to_plan_json(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        planner = _mock_planner()
        forge = _forge(planner, store)

        path = forge.save_plan(_plan(), project)
        assert path.name == "plan.json"
        assert path.exists()


# ---------------------------------------------------------------------------
# signal_to_plan
# ---------------------------------------------------------------------------

class TestSignalToPlan:
    def test_returns_none_for_nonexistent_signal(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        result = forge.signal_to_plan("no-such-signal", "nds")
        assert result is None

    def test_returns_none_for_nonexistent_project(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        # No project registered

        planner = _mock_planner()
        forge = _forge(planner, store)
        result = forge.signal_to_plan("sig-001", "nonexistent-project")
        assert result is None

    def test_returns_plan_when_signal_and_project_exist(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal())

        planner = _mock_planner(returned_plan=_plan(task_id="forge-plan"))
        forge = _forge(planner, store)
        result = forge.signal_to_plan("sig-001", "nds")
        assert result is not None
        assert isinstance(result, MachinePlan)

    def test_creates_bug_fix_plan(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal(title="Payment gateway fails"))

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        call_kwargs = planner.create_plan.call_args
        all_args = str(call_kwargs)
        assert "bug-fix" in all_args

    def test_description_includes_signal_title(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal(title="Unique Title XYZ"))

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        call_kwargs = planner.create_plan.call_args
        task_summary = call_kwargs.kwargs.get("task_summary", "")
        assert "Unique Title XYZ" in task_summary

    def test_description_includes_signal_description_when_present(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal(description="Reproducible with steps A, B, C"))

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        call_kwargs = planner.create_plan.call_args
        task_summary = call_kwargs.kwargs.get("task_summary", "")
        assert "Reproducible with steps A, B, C" in task_summary

    def test_links_plan_task_id_to_signal(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal())

        returned_plan = _plan(task_id="linked-plan-id")
        planner = _mock_planner(returned_plan=returned_plan)
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        config = store.load_config()
        signal = next(s for s in config.signals if s.signal_id == "sig-001")
        assert signal.forge_task_id == "linked-plan-id"

    def test_sets_signal_status_to_triaged(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal())

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        config = store.load_config()
        signal = next(s for s in config.signals if s.signal_id == "sig-001")
        assert signal.status == "triaged"

    def test_does_not_modify_other_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)
        store.add_signal(_signal(signal_id="sig-001"))
        store.add_signal(_signal(signal_id="sig-002", signal_type="blocker", title="B"))

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "nds")

        config = store.load_config()
        sig2 = next(s for s in config.signals if s.signal_id == "sig-002")
        assert sig2.status == "open"
        assert sig2.forge_task_id == ""

    def test_planner_not_called_for_nonexistent_signal(self, tmp_path: Path):
        store = _store(tmp_path)
        project = _project(tmp_path)
        store.register_project(project)

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("ghost-signal", "nds")
        planner.create_plan.assert_not_called()

    def test_planner_not_called_for_nonexistent_project(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())

        planner = _mock_planner()
        forge = _forge(planner, store)
        forge.signal_to_plan("sig-001", "ghost-project")
        planner.create_plan.assert_not_called()
