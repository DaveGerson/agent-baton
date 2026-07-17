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
import asyncio
import contextlib
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
from agent_baton.core.runtime.decisions import DecisionManager, deterministic_decision_id
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.execution import (
    ActionType,
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
    TeamMember,
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

    # bd-96d8: SQLite fallback path in ExecutionEngine now calls
    # storage.load_execution(task_id) — the shim needs the method even if
    # it always returns None (file persistence takes over).
    def load_execution(self, task_id: str):  # noqa: ARG002
        return None


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
        # it should remain "complete" from the seed.  Verify the seeded
        # outcome is still on disk and that the engine printed a
        # [DRY RUN] dispatch line for 2.1 (read-only since bd-29bf).
        sp = StatePersistence(tmp_path, task_id=_TWO_PHASE_PLAN["task_id"])
        final = sp.load()
        assert final is not None
        results_by_step = {r.step_id: r for r in final.step_results}
        assert "1.1" in results_by_step
        # The seeded outcome must survive — proves no real re-dispatch
        # overwrote it. Under bd-29bf (read-only dry-run) the engine prints
        # a [DRY RUN] preview line for every step but does NOT mutate state.
        assert "(seeded)" in results_by_step["1.1"].outcome

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


# ===========================================================================
# Non-TTY approval pause is durable across a process boundary and completes
# exactly once
#
# This is the central behavioral contract for Phase 2, step 2.3
# (docs/internal/execution-runtime-contract.md §5, §6, §8's "Compat: two
# decision systems" row): a task paused on a non-TTY approval prompt must
# (1) leave persisted execution AND decision state untouched across the
# process exit, (2) not duplicate the pending decision if re-invoked before
# it is resolved, (3) pick up and apply a resolution supplied through ANY
# other supported surface (here: the DecisionManager directly, the same
# object the REST API's /decisions/{id}/resolve route and the PMO decision
# inbox both delegate to) without asking again, and (4) complete exactly
# once -- a further invocation after completion must refuse to restart.
# ===========================================================================

class TestNonTtyApprovalPauseSurvivesRestartAndCompletesOnce:
    def _dm(self, tmp_path: Path) -> DecisionManager:
        return DecisionManager(decisions_dir=tmp_path / "decisions")

    @contextlib.contextmanager
    def _non_tty_run_patches(self, tmp_path: Path):
        """All patches needed for one non-TTY `_handle_run` invocation,
        as a single reusable context manager (each call mints fresh
        `patch(...)` objects, so this may be invoked more than once per
        test to simulate successive process boundaries)."""
        with contextlib.ExitStack() as stack:
            for cm in _patches_for_run(tmp_path):
                stack.enter_context(cm)
            stack.enter_context(patch("sys.stdin.isatty", return_value=False))
            stack.enter_context(patch(
                "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher",
                MagicMock(),
            ))
            yield

    def test_pause_persists_then_resolves_via_decision_manager_then_completes_once(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_APPROVAL_PLAN), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=_APPROVAL_PLAN,
            completed_step_ids=["1.1"],
            approvals=[],
            status="running",
        )

        task_id = _APPROVAL_PLAN["task_id"]
        args = _make_args(str(plan_path), task_id=None, dry_run=False)
        dm = self._dm(tmp_path)
        request_id = deterministic_decision_id(task_id, "approval", 1)

        before = StatePersistence(tmp_path, task_id=task_id).load()
        assert before is not None
        assert before.status == "running"

        # --- 1) First process: no TTY, no recorded decision -> must pause
        # durably (non-zero exit) WITHOUT mutating execution state beyond
        # the (state-machine-owned) approval_pending transition, and must
        # record a durable decision request other surfaces can see. ---
        with self._non_tty_run_patches(tmp_path), pytest.raises(SystemExit) as exc1:
            _handle_run(args)
        assert exc1.value.code != 0

        after_pause = StatePersistence(tmp_path, task_id=task_id).load()
        assert after_pause is not None
        assert after_pause.status == "approval_pending"
        assert after_pause.approval_results == []

        pending = dm.get(request_id)
        assert pending is not None
        assert pending.status == "pending"

        # --- 2) A second process boundary before resolution: re-invoking
        # must pause again (not silently reject / not silently complete)
        # and must NOT duplicate the pending decision request. ---
        with self._non_tty_run_patches(tmp_path), pytest.raises(SystemExit) as exc2:
            _handle_run(args)
        assert exc2.value.code != 0

        still_pending = dm.get(request_id)
        assert still_pending is not None
        assert still_pending.status == "pending"
        assert [r.request_id for r in dm.pending()] == [request_id]

        unchanged = StatePersistence(tmp_path, task_id=task_id).load()
        assert unchanged is not None
        assert unchanged.status == "approval_pending"
        assert unchanged.approval_results == []

        # --- 3) The decision is answered through a DIFFERENT surface --
        # directly via DecisionManager, mirroring what the REST API and
        # the PMO decision inbox both ultimately call. ---
        resolved = dm.resolve(
            request_id=request_id, chosen_option="approve", rationale="lgtm",
        )
        assert resolved is True

        # --- 4) Re-invoking `_handle_run` (still non-TTY, still a brand
        # new process) must apply the durable resolution exactly once and
        # drive the (single-phase) plan to COMPLETE without prompting
        # again. ---
        with self._non_tty_run_patches(tmp_path):
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "COMPLETE" in out

        final = StatePersistence(tmp_path, task_id=task_id).load()
        assert final is not None
        assert final.status == "complete"
        assert len(final.approval_results) == 1
        assert final.approval_results[0].result == "approve"

        # --- 5) A further invocation must refuse to restart the now-
        # terminal task -- "complete once, and only once". ---
        with self._non_tty_run_patches(tmp_path), pytest.raises(SystemExit) as exc4:
            _handle_run(args)
        assert exc4.value.code != 0

        final_after = StatePersistence(tmp_path, task_id=task_id).load()
        assert final_after is not None
        assert final_after.status == "complete"
        assert len(final_after.approval_results) == 1


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

        # Track which step_ids the engine offers as DISPATCH actions during
        # this dry-run.  In dry-run mode neither mark_dispatched nor
        # record_step_result is called (bd-92bc made dry-run fully read-only),
        # so we spy on engine.next_action() — the one call that happens on
        # every loop iteration regardless of dry_run.  We capture any action
        # whose action_type is DISPATCH and record its step_id.
        dispatched_step_ids: list[str] = []

        original_next_action = ExecutionEngine.next_action

        def _spy_next_action(self):
            action = original_next_action(self)
            if action.action_type == ActionType.DISPATCH:
                dispatched_step_ids.append(action.step_id)
            return action

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with (
            patches[0], patches[1], patches[2], patches[3],
            patches[4], patches[5], patches[6],
            patch.object(ExecutionEngine, "next_action", _spy_next_action),
        ):
            _handle_run(args)

        # 1.1 (architect) was completed BEFORE the run; it must NOT be
        # offered as a fresh DISPATCH by the engine.
        assert "1.1" not in dispatched_step_ids, (
            f"architect step was re-dispatched on resume: {dispatched_step_ids}"
        )
        # 2.1 should have been offered as DISPATCH (this is the pending work).
        assert "2.1" in dispatched_step_ids


# ---------------------------------------------------------------------------
# bd-5d4f — `baton execute resume` parser exposes --abort and --no-rerun-gate
# ---------------------------------------------------------------------------


class TestResumeParserDeclaresTakeoverFlags:
    """The resume subparser must declare --abort and --no-rerun-gate so they
    surface in `baton execute resume --help`.

    Before the fix the dispatch code read these flags via getattr() with
    safe defaults, but argparse never knew about them — the help text was
    silent and `--abort foo` was rejected as an unrecognized argument.
    """

    @staticmethod
    def _build_resume_parser() -> argparse.ArgumentParser:
        """Build a minimal parser containing only the `execute resume` chain."""
        from agent_baton.cli.commands.execution import execute as exec_mod

        root = argparse.ArgumentParser(prog="baton")
        sub = root.add_subparsers(dest="command")
        exec_mod.register(sub)
        return root

    def test_resume_help_lists_abort(self) -> None:
        root = self._build_resume_parser()
        # Locate the resume subparser via argparse's choices map.
        execute_sp = root._subparsers._group_actions[0].choices["execute"]
        resume_sp = execute_sp._subparsers._group_actions[0].choices["resume"]
        help_text = resume_sp.format_help()

        assert "--abort" in help_text, (
            "resume parser must declare --abort so help text surfaces it; "
            f"got:\n{help_text}"
        )

    def test_resume_help_lists_no_rerun_gate(self) -> None:
        root = self._build_resume_parser()
        execute_sp = root._subparsers._group_actions[0].choices["execute"]
        resume_sp = execute_sp._subparsers._group_actions[0].choices["resume"]
        help_text = resume_sp.format_help()

        assert "--no-rerun-gate" in help_text, (
            "resume parser must declare --no-rerun-gate so help text "
            f"surfaces it; got:\n{help_text}"
        )

    def test_resume_parses_abort_and_no_rerun_gate_flags(self) -> None:
        """Argparse must accept the flags without falling back to error."""
        root = self._build_resume_parser()
        ns = root.parse_args(["execute", "resume", "--abort", "--no-rerun-gate"])
        assert getattr(ns, "abort", None) is True
        assert getattr(ns, "no_rerun_gate", None) is True

    def test_resume_defaults_when_flags_omitted(self) -> None:
        """Both flags default to False when omitted."""
        root = self._build_resume_parser()
        ns = root.parse_args(["execute", "resume"])
        assert getattr(ns, "abort", None) is False
        assert getattr(ns, "no_rerun_gate", None) is False


def _team_resume_plan() -> MachinePlan:
    """Single-phase plan whose only step is a team step, so resume() walks
    straight into _team_dispatch_action (which selects the team backend)."""
    return MachinePlan(
        task_id="team-resume-strict",
        task_summary="team resume strict backend",
        phases=[PlanPhase(
            phase_id=1, name="Build",
            steps=[PlanStep(
                step_id="1.1", agent_name="team",
                task_description="implement and review", model="sonnet",
                team=[
                    TeamMember(
                        member_id="1.1.a", agent_name="backend-engineer",
                        role="implementer", task_description="impl",
                        model="sonnet",
                    ),
                    TeamMember(
                        member_id="1.1.b", agent_name="code-reviewer",
                        role="reviewer", task_description="review",
                        model="sonnet",
                    ),
                ],
            )],
        )],
    )


class TestResumeUnknownStrictBackend:
    """C3 regression: `baton execute resume` must translate an
    UnknownTeamBackendError (raised through _team_dispatch_action when
    BATON_TEAMS_BACKEND is unknown under BATON_TEAMS_BACKEND_STRICT=1) into a
    clean, non-zero exit with the API's message shape — never a raw
    traceback. The API maps this deliberately (executions.py:93-104); the CLI
    previously had no handler.
    """

    def test_resume_unknown_strict_backend_clean_exit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "not-a-real-backend")
        monkeypatch.setenv("BATON_TEAMS_BACKEND_STRICT", "1")

        plan = _team_resume_plan()
        state = ExecutionState(
            task_id=plan.task_id, plan=plan,
            current_phase=0, status="running",
        )
        StatePersistence(tmp_path, task_id=plan.task_id).save(state)

        args = argparse.Namespace(
            subcommand="resume",
            task_id=plan.task_id,
            output="text",
            abort=False,
            no_rerun_gate=False,
            force_override=False,
            override_justification="",
        )
        patches = _patches_for_run(tmp_path)
        with (
            patches[0], patches[1], patches[2], patches[3],
            patches[4], patches[5], patches[6],
            pytest.raises(SystemExit) as exc_info,
        ):
            _mod.handler(args)

        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "Unknown BATON_TEAMS_BACKEND" in out
        # No traceback leaked to the user.
        assert "Traceback" not in out


# ===========================================================================
# Phase 6, 6.4 -- CHECKPOINT threshold / dedup / restart
#
# 6.2 implemented CHECKPOINT (ExecutionEngine._checkpoint_trigger /
# _emit_checkpoint) but explicitly shipped without test coverage ("tests/ is
# outside this step's allowed_paths" -- see its commit message). These tests
# close that gap across all three consumers: the bare engine, `baton execute
# run` (via _handle_run, matching the rest of this file), and TaskWorker.
# ===========================================================================

_CHECKPOINT_PLAN: dict[str, Any] = {
    "task_id": "checkpoint-base-task",
    "task_summary": "Checkpoint threshold/dedup/restart test",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Phase 1",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "architect",
                    "task_description": "Design",
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
                    "agent_name": "backend-engineer",
                    "task_description": "Build",
                    "model": "sonnet",
                }
            ],
        },
    ],
}


def _checkpoint_plan(task_id: str) -> "MachinePlan":
    data = json.loads(json.dumps(_CHECKPOINT_PLAN))
    data["task_id"] = task_id
    return MachinePlan.from_dict(data)


class TestCheckpointEngineThresholdsAndDedup:
    """Direct-engine coverage of the three independent checkpoint triggers
    and the dedup guard that makes a single phase boundary un-checkpointable
    twice -- the foundation the CLI/TaskWorker tests below build on."""

    def test_phase_interval_threshold_triggers_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-phase"))
        engine.record_step_result("1.1", "architect")

        action = engine.next_action()

        assert action.action_type == ActionType.CHECKPOINT
        assert action.checkpoint_handoff is not None
        assert action.checkpoint_handoff["trigger"] == "phase_interval"
        assert action.checkpoint_handoff["phase_id"] == 2
        assert "baton execute resume" in action.checkpoint_handoff["resume_command"]

    def test_checkpoint_is_durably_persisted_on_execution_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        task_id = "checkpoint-engine-persist"
        # Both engine instances are constructed with an explicit task_id so
        # StatePersistence resolves the SAME namespaced path
        # (<root>/executions/<task_id>/execution-state.json) on both sides
        # -- constructing the first engine without one leaves persistence
        # pinned to the legacy flat path instead (see ExecutionEngine.start's
        # file-mode docstring), which a task_id-bearing reload would then
        # never find.
        engine = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
        engine.start(_checkpoint_plan(task_id))
        engine.record_step_result("1.1", "architect")
        engine.next_action()

        reloaded = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
        state = reloaded._load_state()
        assert state is not None
        assert state.checkpoint_count == 1
        assert len(state.checkpoints) == 1
        assert state.checkpoints[0].trigger == "phase_interval"
        assert state.last_checkpoint_phase == 1  # advanced phase index

    def test_same_boundary_is_never_checkpointed_twice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dedup guard: calling next_action() again at the SAME boundary
        must proceed straight to DISPATCH, never emit a second CHECKPOINT
        -- including across what would otherwise look like a retry."""
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-dedup"))
        engine.record_step_result("1.1", "architect")

        first = engine.next_action()
        assert first.action_type == ActionType.CHECKPOINT

        second = engine.next_action()

        assert second.action_type == ActionType.DISPATCH
        assert second.step_id == "2.1"
        state = engine._load_state()
        assert state.checkpoint_count == 1
        assert len(state.checkpoints) == 1

    def test_turn_threshold_triggers_independent_of_phase_interval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A phase_interval too high to trip on its own must not suppress
        the (independently deterministic) turn-count trigger."""
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "100")
        monkeypatch.setenv("BATON_CHECKPOINT_TURN_THRESHOLD", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_TOKEN_THRESHOLD", "100000000")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-turns"))
        engine.record_step_result("1.1", "architect")  # bumps turn_count to 1

        action = engine.next_action()

        assert action.action_type == ActionType.CHECKPOINT
        assert action.checkpoint_handoff["trigger"] == "turn_threshold"

    def test_token_threshold_triggers_independent_of_phase_interval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "100")
        monkeypatch.setenv("BATON_CHECKPOINT_TURN_THRESHOLD", "100000")
        monkeypatch.setenv("BATON_CHECKPOINT_TOKEN_THRESHOLD", "10")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-tokens"))
        engine.record_step_result("1.1", "architect", estimated_tokens=50)

        action = engine.next_action()

        assert action.action_type == ActionType.CHECKPOINT
        assert action.checkpoint_handoff["trigger"] == "token_threshold"

    def test_checkpoint_disabled_via_env_never_emits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "0")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-disabled"))
        engine.record_step_result("1.1", "architect")

        action = engine.next_action()

        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_next_actions_plural_withholds_batch_when_checkpoint_due(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """next_actions() (plural -- TaskWorker/PMO/`--all`) must return an
        empty batch at a due-but-unemitted checkpoint boundary so every
        caller falls back to next_action(), the only method that actually
        persists the checkpoint (docstring contract in executor.py)."""
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_checkpoint_plan("checkpoint-engine-plural"))
        engine.record_step_result("1.1", "architect")

        assert engine.next_actions() == []

        action = engine.next_action()
        assert action.action_type == ActionType.CHECKPOINT


class TestCheckpointCLIRunAndResume:
    """`baton execute run` (via _handle_run) must stop cleanly at a
    CHECKPOINT and a later, independent invocation against the same
    on-disk state must resume past it without redispatching phase 1 or
    re-emitting the checkpoint."""

    def test_checkpoint_stops_run_cleanly_with_resume_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")

        plan_dict = json.loads(json.dumps(_CHECKPOINT_PLAN))
        plan_dict["task_id"] = "checkpoint-cli-stop"
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan_dict), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=plan_dict,
            completed_step_ids=["1.1"],
            status="running",
            current_phase=0,
        )

        args = _make_args(str(plan_path), task_id=None, dry_run=True)
        patches = _patches_for_run(tmp_path)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "CHECKPOINT" in out
        assert "Resume in a fresh session with" in out
        assert "baton execute resume" in out
        assert "COMPLETE" not in out
        assert "FAILED" not in out

        sp = StatePersistence(tmp_path, task_id=plan_dict["task_id"])
        final = sp.load()
        assert final is not None
        assert final.checkpoint_count == 1
        assert final.current_phase == 1
        assert "2.1" not in final.dispatched_step_ids

    def test_fresh_invocation_resumes_past_checkpoint_without_recheckpointing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")

        plan_dict = json.loads(json.dumps(_CHECKPOINT_PLAN))
        plan_dict["task_id"] = "checkpoint-cli-restart"
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan_dict), encoding="utf-8")
        _seed_partial_state(
            context_root=tmp_path,
            plan_dict=plan_dict,
            completed_step_ids=["1.1"],
            status="running",
            current_phase=0,
        )
        args = _make_args(str(plan_path), task_id=None, dry_run=True)

        # First invocation: hits the checkpoint boundary and stops.
        patches1 = _patches_for_run(tmp_path)
        with patches1[0], patches1[1], patches1[2], patches1[3], patches1[4], patches1[5], patches1[6]:
            _handle_run(args)
        capsys.readouterr()  # discard first invocation's output

        # Second, wholly independent invocation ("fresh session" / restart)
        # against the SAME on-disk state must resume past the already-
        # checkpointed boundary straight into phase 2.
        patches2 = _patches_for_run(tmp_path)
        with patches2[0], patches2[1], patches2[2], patches2[3], patches2[4], patches2[5], patches2[6]:
            _handle_run(args)

        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "Resuming execution" in out
        assert "CHECKPOINT" not in out
        assert "2.1" in out
        assert "backend-engineer" in out

        sp = StatePersistence(tmp_path, task_id=plan_dict["task_id"])
        final = sp.load()
        assert final is not None
        assert final.checkpoint_count == 1  # dedup held across the restart


class TestCheckpointTaskWorker:
    """TaskWorker must treat CHECKPOINT as a non-terminal, paused-for-
    refresh stop (never COMPLETE/FAILED), and a fresh worker/engine pair
    resuming the same persisted execution must not redispatch completed
    work or re-checkpoint the boundary a prior worker already crossed."""

    def test_worker_stops_cleanly_at_checkpoint_without_redispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")

        async def _run() -> None:
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(_checkpoint_plan("checkpoint-worker-stop"))
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher)

            summary = await worker.run()

            assert "checkpoint" in summary.lower()
            assert "baton execute resume" in summary
            assert not worker.is_running
            launched_ids = {launch["step_id"] for launch in launcher.launches}
            assert "1.1" in launched_ids
            # The worker must have stopped BEFORE ever asking the launcher
            # to dispatch phase 2's step.
            assert "2.1" not in launched_ids

        asyncio.run(_run())

    def test_fresh_worker_resumes_past_checkpoint_and_completes_without_redoubling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")
        task_id = "checkpoint-worker-restart"

        async def _first_run() -> None:
            # Constructed with an explicit task_id (matching the resumed
            # engine below) so StatePersistence resolves the same
            # namespaced path on both sides -- see the sibling engine-level
            # test's comment for why this matters.
            engine = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
            engine.start(_checkpoint_plan(task_id))
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "checkpoint" in summary.lower()

        async def _second_run() -> None:
            # A brand-new engine + worker instance against the SAME
            # persisted state on disk -- simulates a fresh process resuming
            # after the checkpoint (no in-memory state carried over).
            resumed_engine = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=resumed_engine, launcher=launcher)

            summary = await worker.run()

            assert "complete" in summary.lower()
            launched_ids = {launch["step_id"] for launch in launcher.launches}
            # Phase 1's step must NOT be redispatched by the resumed worker
            # -- only phase 2's step is genuinely new work for it.
            assert "1.1" not in launched_ids
            assert "2.1" in launched_ids

            state = resumed_engine._load_state()
            assert state is not None
            assert state.checkpoint_count == 1  # dedup held across the restart

        asyncio.run(_first_run())
        asyncio.run(_second_run())
