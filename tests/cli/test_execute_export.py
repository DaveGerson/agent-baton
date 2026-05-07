"""Slice 15: ``baton execute export`` and ``dump_state_to_json``.

Stage 2-3 of the file-backend deprecation.  The factory no longer
returns FileStorage but operators still need a flat-JSON snapshot
path; the helper + CLI verb provide that.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.storage import dump_state_to_json
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


def _save_minimal_state(context_root: Path, task_id: str = "task-export") -> ExecutionState:
    """Persist a small state to baton.db so the export helper can find it."""
    plan = MachinePlan(
        task_id=task_id,
        task_summary="export test",
        phases=[
            PlanPhase(
                phase_id=0,
                name="p0",
                steps=[PlanStep(
                    step_id="0.1", agent_name="x", task_description="t",
                )],
            ),
        ],
    )
    state = ExecutionState(task_id=task_id, plan=plan)
    storage = SqliteStorage(context_root / "baton.db")
    storage.save_execution(state)
    return state


class TestDumpStateToJson:
    def test_dump_writes_state_dict(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        state = _save_minimal_state(ctx)

        out = tmp_path / "snapshot.json"
        dump_state_to_json(state.task_id, context_root=ctx, out_path=out)

        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["task_id"] == state.task_id
        assert loaded["status"] == "running"
        # The PrivateAttr _loaded_version stays out of the snapshot.
        assert "_loaded_version" not in loaded
        # The OCC version column also stays out (storage-internal).
        assert "version" not in loaded

    def test_dump_creates_parent_directory(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _save_minimal_state(ctx)

        out = tmp_path / "deeper" / "still-deeper" / "snapshot.json"
        dump_state_to_json("task-export", context_root=ctx, out_path=out)
        assert out.exists()

    def test_dump_raises_for_unknown_task(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _save_minimal_state(ctx)
        with pytest.raises(FileNotFoundError):
            dump_state_to_json(
                "no-such-task",
                context_root=ctx,
                out_path=tmp_path / "snapshot.json",
            )
