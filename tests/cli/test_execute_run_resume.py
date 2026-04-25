"""Tests for ``baton execute run`` resume-aware behaviour (bd-7444).

The bug: when an execution had been started, an approval recorded, and the
operator then invoked ``baton execute run`` (without --task-id and without
BATON_TASK_ID), the run subcommand silently re-dispatched the already-
completed first step from scratch, burning agent tokens.  When stdin was not
a TTY it then auto-rejected the approval and marked the execution failed.

These tests pin the resume contract:

1. Pre-recorded approval is honored: the completed step is NOT re-dispatched
   and the run loop proceeds to the next phase.
2. Pre-passed gate is honored: the gate is NOT re-run.
3. Non-TTY at an unresolved approval prompt exits non-zero with a clear
   error and DOES NOT mutate state (no silent reject).
4. Active-task-marker fallback: with no flag and no env var, the SQLite/file
   active-task pointer is consulted and resume happens through it.
5. Plan-task-id collision: even without an active marker, a plan whose
   task_id matches an existing execution row triggers resume rather than
   overwrite.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import execute as _mod
from agent_baton.cli.commands.execution.execute import _handle_run
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    StepResult,
)


_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


# A two-phase plan with phase 1 carrying an approval gate.  Used to
# reproduce the bd-7444 scenario: phase 1's lone step completes, approval
# is recorded, and `execute run` should skip directly to phase 2's step.
_TWO_PHASE_PLAN: dict[str, Any] = {
    "task_id": "resume-test-task",
    "task_summary": "Resume test",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Phase 1 (architect)",
            "approval_required": True,
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "architect",
                    "task_description": "Design the system",
                    "model": "sonnet",
                }
            ],
        },
        {
            "phase_id": 2,
            "name": "Phase 2 (build)",
            "steps": [
                {
                    "step_id": "2.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Build it",
                    "model": "sonnet",
                }
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeStorage:
    """Minimal storage stub that points to no active task."""

    def __init__(self, active_task: str | None = None) -> None:
        self._active = active_task

    def get_active_task(self) -> str | None:
        return self._active

    def set_active_task(self, task_id: str) -> None:
        self._active = task_id


def _make_args(
    plan: str,
    *,
    task_id: str | None = None,
    dry_run: bool = True,
    max_steps: int = 50,
) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand="run",
        plan=plan,
        model="sonnet",
        max_steps=max_steps,
        dry_run=dry_run,
        task_id=task_id,
        output="text",
        token_budget=0,
    )


def _seed_partial_state(
    *,
    context_root: Path,
    plan_dict: dict,
    completed_step_ids: list[str],
    approvals: list[tuple[int, str]] | None = None,
    gates: list[tuple[int, bool]] | None = None,
    status: str = "running",
    current_phase: int = 0,
) -> ExecutionState:
    """Write an ExecutionState to ``context_root`` simulating a partially-
    completed execution.

    ``completed_step_ids`` lists step_ids whose StepResult should be marked
    ``status="complete"``.  ``approvals`` is a list of (phase_id, result)
    tuples; ``gates`` is a list of (phase_id, passed) tuples.
    """
    plan = MachinePlan.from_dict(plan_dict)
    state = ExecutionState(
        task_id=plan.task_id,
        plan=plan,
        current_phase=current_phase,
        status=status,
    )
    for sid in completed_step_ids:
        # Find the agent name for this step from the plan.
        agent = ""
        for ph in plan.phases:
            for st in ph.steps:
                if st.step_id == sid:
                    agent = st.agent_name
        state.step_results.append(
            StepResult(
                step_id=sid,
                agent_name=agent,
                status="complete",
                outcome="(seeded) prior run output",
            )
        )
    for phase_id, result in (approvals or []):
        state.approval_results.append(
            ApprovalResult(phase_id=phase_id, result=result, feedback="seeded")
        )
    for phase_id, passed in (gates or []):
        state.gate_results.append(
            GateResult(
                phase_id=phase_id,
                gate_type="test",
                passed=passed,
                output="seeded",
            )
        )
    sp = StatePersistence(context_root, task_id=plan.task_id)
    sp.save(state)
    return state


def _patches_for_run(
    tmp_path: Path,
    *,
    storage: _FakeStorage | None = None,
    active_marker: str | None = None,
    backend: str = "file",
):
    """Build the standard patch context-manager list for `_handle_run`.

    Uses a real ExecutionEngine bound to ``tmp_path`` for full state machine
    fidelity, while suppressing real disk side-effects (SQLite, sync,
    ContextManager).
    """
    storage = storage or _FakeStorage()
    real_engine_factory = lambda **kwargs: ExecutionEngine(
        team_context_root=tmp_path,
        bus=kwargs.get("bus"),
        task_id=kwargs.get("task_id"),
        storage=kwargs.get("storage"),
        token_budget=kwargs.get("token_budget"),
        knowledge_resolver=kwargs.get("knowledge_resolver"),
        policy_engine=kwargs.get("policy_engine"),
    )
    return [
        patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
        patch(f"{_EXECUTE_MOD}.ExecutionEngine", side_effect=real_engine_factory),
        patch(f"{_EXECUTE_MOD}.ContextManager"),
        patch(
            "agent_baton.core.storage.sync.auto_sync_current_project",
            return_value=None,
        ),
        patch(f"{_EXECUTE_MOD}.detect_backend", return_value=backend),
        patch(
            f"{_EXECUTE_MOD}.StatePersistence.get_active_task_id",
            staticmethod(lambda _root: active_marker),
        ),
        patch(f"{_EXECUTE_MOD}._resolve_context_root", return_value=tmp_path),
    ]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Clear BATON_TASK_ID so tests start from a known-empty env."""
    monkeypatch.delenv("BATON_TASK_ID", raising=False)


# ---------------------------------------------------------------------------
# Pre-recorded approval is honored
# ---------------------------------------------------------------------------

class TestPreRecordedApprovalHonored:
    """An approval recorded before `execute run` must NOT trigger
    re-dispatch of the prior phase's completed step."""

    def test_completed_step_is_not_redispatched(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")

        # Seed: phase 1's step 1.1 is complete and phase 1 is approved.
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
            status="running",
            current_phase=0,
        )

        # No --task-id, no env, no marker — but the existing execution row
        # has task_id == _TWO_PHASE_PLAN["task_id"], so the probe-based
        # resume path must catch it.
        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err

        # Must indicate resumption, not a fresh start.
        assert "Resuming execution" in out, out
        assert "Started execution" not in out, out

        # Architect (1.1) must NOT appear as a re-dispatch in this run —
        # it should remain "complete" from the seed.  Verify by checking
        # the persisted state: only one StepResult for 1.1, and 2.1 was
        # dispatched in this dry-run.
        sp = StatePersistence(tmp_path, task_id=_TWO_PHASE_PLAN["task_id"])
        final = sp.load()
        assert final is not None
        results_by_step = {r.step_id: r for r in final.step_results}
        assert "1.1" in results_by_step
        # Step 1.1's outcome must still be the seeded value (proves no
        # re-dispatch overwrote it).
        assert "(seeded)" in results_by_step["1.1"].outcome
        # Step 2.1 must have been processed in this run.
        assert "2.1" in results_by_step

    def test_run_proceeds_to_phase_two_after_approval(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
            current_phase=0,
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        # Phase 2 step must be the one that ran in dry-run mode.
        assert "2.1" in out
        assert "backend-engineer" in out
        assert "COMPLETE" in out

    def test_approval_results_preserved_after_resume(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
            current_phase=0,
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        sp = StatePersistence(tmp_path, task_id=_TWO_PHASE_PLAN["task_id"])
        final = sp.load()
        assert final is not None
        # The seeded approval must still be present (not wiped).
        approvals = [a for a in final.approval_results if a.phase_id == 1]
        assert len(approvals) >= 1
        assert any(a.result == "approve" for a in approvals)


# ---------------------------------------------------------------------------
# Pre-passed gate is honored
# ---------------------------------------------------------------------------

_GATE_PLAN: dict[str, Any] = {
    "task_id": "gate-resume-task",
    "task_summary": "Gate resume test",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Phase 1",
            "gate": {
                "gate_type": "test",
                "command": "echo should-not-run && false",
            },
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Build",
                    "model": "sonnet",
                }
            ],
        },
        {
            "phase_id": 2,
            "name": "Phase 2",
            "steps": [
                {
                    "step_id": "2.1",
                    "agent_name": "test-engineer",
                    "task_description": "Test",
                    "model": "sonnet",
                }
            ],
        },
    ],
}


class TestPrePassedGateHonored:
    """A passing GateResult on disk must NOT cause the gate command to
    re-run on resume."""

    def test_passed_gate_is_not_rerun(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_GATE_PLAN), encoding="utf-8")

        # Seed: phase 1 step complete + gate already passed.  The seeded
        # gate_command in the plan would FAIL if re-run (`false` after
        # echo), so re-running it would crash the test.
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_GATE_PLAN,
            completed_step_ids=["1.1"],
            gates=[(1, True)],
            current_phase=0,
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        # The seeded gate command's marker must not appear — proving the
        # gate did not re-run.
        assert "should-not-run" not in out, out
        # Execution should have proceeded to phase 2 and completed.
        assert "COMPLETE" in out
        assert "2.1" in out

    def test_gate_results_preserved_after_resume(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_GATE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_GATE_PLAN,
            completed_step_ids=["1.1"],
            gates=[(1, True)],
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        sp = StatePersistence(tmp_path, task_id=_GATE_PLAN["task_id"])
        final = sp.load()
        assert final is not None
        passed = [g for g in final.gate_results if g.phase_id == 1 and g.passed]
        assert len(passed) >= 1


# ---------------------------------------------------------------------------
# Non-TTY at unresolved approval — must fail loudly, not mutate state
# ---------------------------------------------------------------------------

_APPROVAL_PLAN: dict[str, Any] = {
    "task_id": "non-tty-approval-task",
    "task_summary": "Non-TTY approval guard",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Phase 1",
            "approval_required": True,
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "architect",
                    "task_description": "Design",
                    "model": "sonnet",
                }
            ],
        },
    ],
}


class TestNonTtyApprovalSafety:
    """When stdin is not a TTY and an approval prompt is required without a
    recorded decision, the run subcommand must exit non-zero and leave
    state untouched (no silent reject that destroys the execution)."""

    def test_non_tty_exits_nonzero_when_approval_pending(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_APPROVAL_PLAN), encoding="utf-8")
        # Seed: step 1.1 complete, no approval recorded → execution sits
        # at approval_pending after next_action() runs.
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_APPROVAL_PLAN,
            completed_step_ids=["1.1"],
            approvals=[],
            status="running",
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=False)
        patches = _patches_for_run(tmp_path)
        # Force isatty() False to simulate piped/headless invocation.
        with (
            patches[0], patches[1], patches[2], patches[3],
            patches[4], patches[5], patches[6],
            patch("sys.stdin.isatty", return_value=False),
            patch(
                "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher",
                MagicMock(),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        # Must exit non-zero (we use exit code 2 for "needs explicit
        # decision"); accept anything non-zero to be lenient on the code.
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        out = captured.out + captured.err
        # Error message must reference the approval and mention the
        # remediation command.
        assert "approval" in out.lower()
        assert "phase" in out.lower()
        assert "approve" in out.lower()

    def test_non_tty_does_not_mutate_state(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_APPROVAL_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_APPROVAL_PLAN,
            completed_step_ids=["1.1"],
            approvals=[],
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=False)
        patches = _patches_for_run(tmp_path)
        with (
            patches[0], patches[1], patches[2], patches[3],
            patches[4], patches[5], patches[6],
            patch("sys.stdin.isatty", return_value=False),
            patch(
                "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher",
                MagicMock(),
            ),
            pytest.raises(SystemExit),
        ):
            _handle_run(args)

        sp = StatePersistence(tmp_path, task_id=_APPROVAL_PLAN["task_id"])
        final = sp.load()
        assert final is not None
        # No approval should have been recorded by the non-TTY path.
        assert all(a.result != "reject" for a in final.approval_results)
        # Status must NOT be "failed" — the prior bug set it via reject.
        assert final.status != "failed"


# ---------------------------------------------------------------------------
# Active-marker fallback resolution
# ---------------------------------------------------------------------------

class TestActiveMarkerFallback:
    """Without --task-id and without BATON_TASK_ID, the active-task marker
    (SQLite or file) must be consulted — without it, `execute run` would
    fall to the fresh-start branch and overwrite state (bd-7444)."""

    def test_file_active_marker_drives_resume(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(
            tmp_path,
            active_marker=_TWO_PHASE_PLAN["task_id"],
            backend="file",
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "Resuming execution" in out
        assert _TWO_PHASE_PLAN["task_id"] in out

    def test_env_var_drives_resume(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
        )

        monkeypatch.setenv("BATON_TASK_ID", _TWO_PHASE_PLAN["task_id"])
        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "Resuming execution" in out


# ---------------------------------------------------------------------------
# Idempotency: start → record-as-complete → run resumes
# ---------------------------------------------------------------------------

class TestIdempotentRunAfterApproval:
    """End-to-end idempotency: a complete step + recorded approval must
    NOT cause the agent to be re-launched on the next `execute run`.
    This is the core bd-7444 regression."""

    def test_architect_step_not_redispatched_after_approval(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_TWO_PHASE_PLAN,
            completed_step_ids=["1.1"],
            approvals=[(1, "approve")],
        )

        # Track which agents were "dispatched" by counting record_step_result
        # calls that reference the architect.  Easier: capture the engine
        # actions by patching mark_dispatched.
        dispatched_step_ids: list[str] = []

        original_mark = ExecutionEngine.mark_dispatched

        def _spy_mark(self, step_id: str, agent_name: str = "", **kwargs):
            dispatched_step_ids.append(step_id)
            return original_mark(self, step_id=step_id, agent_name=agent_name, **kwargs)

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with (
            patches[0], patches[1], patches[2], patches[3],
            patches[4], patches[5], patches[6],
            patch.object(ExecutionEngine, "mark_dispatched", _spy_mark),
        ):
            _handle_run(args)

        # 1.1 (architect) was completed BEFORE the run; it must NOT be
        # in the dispatched list for this invocation.
        assert "1.1" not in dispatched_step_ids, (
            f"architect step was re-dispatched on resume: {dispatched_step_ids}"
        )
        # 2.1 should have been dispatched (this is the new work).
        assert "2.1" in dispatched_step_ids
