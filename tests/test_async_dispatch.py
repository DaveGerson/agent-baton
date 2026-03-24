"""Tests for agent_baton.core.async_dispatch.AsyncDispatcher and AsyncTask."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.distribute.experimental.async_dispatch import AsyncDispatcher, AsyncTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(
    task_id: str = "task-001",
    command: str = "echo hello",
    dispatch_type: str = "shell",
    status: str = "pending",
) -> AsyncTask:
    return AsyncTask(
        task_id=task_id,
        command=command,
        dispatch_type=dispatch_type,
        status=status,
    )


# ---------------------------------------------------------------------------
# AsyncTask — serialisation
# ---------------------------------------------------------------------------

# DECISION: Removed test_required_fields_stored (trivial dataclass field storage),
# test_optional_defaults (trivial dataclass defaults), and
# test_to_dict_contains_all_keys (only checks dict structure, not values —
# covered by the roundtrip test). Kept the roundtrip and the defaults-from-dict
# and exit_code tests, which all exercise real logic (serde, field defaults).

class TestAsyncTaskFields:
    def test_from_dict_roundtrip(self) -> None:
        task = AsyncTask(
            task_id="rt-1",
            command="python test.py",
            dispatch_type="manual",
            status="completed",
            dispatched_at="2026-03-20T10:00:00",
            completed_at="2026-03-20T10:05:00",
            result="All tests passed",
            exit_code=0,
        )
        restored = AsyncTask.from_dict(task.to_dict())
        assert restored.task_id == task.task_id
        assert restored.command == task.command
        assert restored.dispatch_type == task.dispatch_type
        assert restored.status == task.status
        assert restored.dispatched_at == task.dispatched_at
        assert restored.completed_at == task.completed_at
        assert restored.result == task.result
        assert restored.exit_code == task.exit_code

    def test_from_dict_defaults_for_missing_keys(self) -> None:
        task = AsyncTask.from_dict({"task_id": "min", "command": "echo"})
        assert task.dispatch_type == "shell"
        assert task.status == "pending"
        assert task.exit_code is None

    def test_exit_code_can_be_none(self) -> None:
        task = AsyncTask.from_dict({"task_id": "t", "command": "c", "exit_code": None})
        assert task.exit_code is None


# ---------------------------------------------------------------------------
# AsyncDispatcher.dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_dispatch_creates_json_file(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        task = _task("my-task")
        path = dispatcher.dispatch(task)
        assert path.exists()
        assert path.suffix == ".json"

    def test_dispatch_creates_parent_dirs(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "deep" / "tasks"
        dispatcher = AsyncDispatcher(tasks_dir)
        task = _task("my-task")
        path = dispatcher.dispatch(task)
        assert path.exists()

    def test_dispatch_sets_status_to_dispatched(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        task = _task("t1", status="pending")
        dispatcher.dispatch(task)
        saved = dispatcher.check_status("t1")
        assert saved is not None
        assert saved.status == "dispatched"

    def test_dispatch_preserves_command(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        task = _task("cmd-task", command="python run_analysis.py")
        dispatcher.dispatch(task)
        saved = dispatcher.check_status("cmd-task")
        assert saved is not None
        assert saved.command == "python run_analysis.py"


# ---------------------------------------------------------------------------
# AsyncDispatcher.check_status
# ---------------------------------------------------------------------------

class TestCheckStatus:
    def test_returns_none_for_missing_task(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        assert dispatcher.check_status("does-not-exist") is None

    def test_returns_task_for_existing(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1", command="echo hi"))
        task = dispatcher.check_status("t1")
        assert task is not None
        assert task.task_id == "t1"
        assert task.command == "echo hi"


# ---------------------------------------------------------------------------
# AsyncDispatcher.mark_complete and mark_failed
# ---------------------------------------------------------------------------

# DECISION: Merged mark_complete and mark_failed tests into one class.
# The two methods are symmetric — each sets status, result, and exit_code.
# Parameterize the "sets status" behaviour (complete vs failed) into one
# test. The noop-on-nonexistent and default-exit-code tests remain separate
# because they test distinct edge-case logic.

class TestMarkComplete:
    def test_mark_complete_sets_status(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1"))
        dispatcher.mark_complete("t1", result="done", exit_code=0)
        task = dispatcher.check_status("t1")
        assert task is not None
        assert task.status == "completed"
        assert task.result == "done"
        assert task.exit_code == 0

    def test_mark_complete_nonexistent_is_noop(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.mark_complete("ghost", result="")  # must not raise

    def test_mark_failed_sets_status(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t2"))
        dispatcher.mark_failed("t2", result="timeout", exit_code=1)
        task = dispatcher.check_status("t2")
        assert task is not None
        assert task.status == "failed"
        assert task.result == "timeout"
        assert task.exit_code == 1

    def test_mark_failed_nonexistent_is_noop(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.mark_failed("ghost")  # must not raise

    def test_default_exit_code_zero_for_complete(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t3"))
        dispatcher.mark_complete("t3")
        task = dispatcher.check_status("t3")
        assert task is not None
        assert task.exit_code == 0

    def test_default_exit_code_one_for_failed(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t4"))
        dispatcher.mark_failed("t4")
        task = dispatcher.check_status("t4")
        assert task is not None
        assert task.exit_code == 1


# ---------------------------------------------------------------------------
# AsyncDispatcher.list_tasks
# ---------------------------------------------------------------------------

class TestListTasks:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        assert dispatcher.list_tasks() == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path / "nonexistent")
        assert dispatcher.list_tasks() == []

    def test_lists_all_tasks(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1"))
        dispatcher.dispatch(_task("t2"))
        dispatcher.dispatch(_task("t3"))
        tasks = dispatcher.list_tasks()
        ids = {t.task_id for t in tasks}
        assert ids == {"t1", "t2", "t3"}

    # DECISION: Parameterize test_filter_by_status_dispatched,
    # test_filter_by_status_completed, and test_filter_by_status_failed into
    # one test. Each tuple is (mark_action, filter_status, expected_in, expected_out).
    @pytest.mark.parametrize("mark_fn,mark_task,status_filter,expected_in,expected_out", [
        # mark_complete("t1") → t1 becomes "completed", t2 stays "dispatched"
        ("mark_complete", "t1", "dispatched", "t2", "t1"),
        ("mark_complete", "t1", "completed", "t1", "t2"),
        # mark_failed("t2") → t2 becomes "failed", t1 stays "dispatched"
        ("mark_failed", "t2", "failed", "t2", "t1"),
    ])
    def test_filter_by_status(
        self, tmp_path: Path, mark_fn, mark_task, status_filter, expected_in, expected_out
    ) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1"))
        dispatcher.dispatch(_task("t2"))
        getattr(dispatcher, mark_fn)(mark_task)
        filtered = dispatcher.list_tasks(status=status_filter)
        ids = {t.task_id for t in filtered}
        assert expected_in in ids
        assert expected_out not in ids

    def test_filter_returns_empty_when_none_match(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1"))
        assert dispatcher.list_tasks(status="completed") == []


# ---------------------------------------------------------------------------
# AsyncDispatcher.list_pending
# ---------------------------------------------------------------------------

class TestListPending:
    def test_returns_only_pending_tasks(self, tmp_path: Path) -> None:
        # Note: dispatch() changes status to "dispatched", so we need to
        # write a task in "pending" status directly for this test.
        tasks_dir = tmp_path
        dispatcher = AsyncDispatcher(tasks_dir)
        # Manually write a pending task JSON
        import json
        pending_task = AsyncTask(task_id="pending-1", command="echo", status="pending")
        path = tasks_dir / "pending-1.json"
        path.write_text(json.dumps(pending_task.to_dict()), encoding="utf-8")
        # Dispatch a second (becomes "dispatched")
        dispatcher.dispatch(_task("dispatched-1"))
        pending = dispatcher.list_pending()
        ids = {t.task_id for t in pending}
        assert "pending-1" in ids
        assert "dispatched-1" not in ids

    def test_empty_when_no_pending_tasks(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path)
        dispatcher.dispatch(_task("t1"))  # becomes "dispatched"
        assert dispatcher.list_pending() == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        dispatcher = AsyncDispatcher(tmp_path / "nonexistent")
        assert dispatcher.list_pending() == []
