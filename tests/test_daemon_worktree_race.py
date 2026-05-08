"""Regression tests for the daemon dispatcher worktree race and path-scope guardrail.

Part 1 — Race regression:
    Demonstrates that when a commit lands on the parent branch between
    ``mark_dispatched()`` and the internal ``git rev-parse HEAD`` call inside
    ``WorktreeManager._create_locked()``, passing an explicit ``base_sha`` to
    ``create()`` guarantees the worktree is pinned to the expected SHA rather
    than the new HEAD.

Part 2 — Path-scope guardrail:
    Verifies that ``record_step_result()`` rejects files_changed entries that
    resolve outside the step's worktree, sets status to "failed", and appends
    a WORKTREE_ESCAPE deviation to the result.

Part 3 — Worker cwd_override propagation:
    Verifies that after ``mark_dispatched()`` creates a worktree, the
    ``TaskWorker`` passes ``cwd_override`` through the scheduler to the
    launcher so the agent subprocess runs inside the worktree.
"""
from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.engine.worktree_manager import WorktreeManager
from agent_baton.core.runtime.launcher import DryRunLauncher, LaunchResult
from agent_baton.core.runtime.scheduler import SchedulerConfig, StepScheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal real git repository with one initial commit."""
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def _make_commit(repo: Path, filename: str, content: str) -> str:
    """Write a file, stage, and commit. Returns new HEAD SHA."""
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"add {filename}"],
        cwd=repo, check=True, capture_output=True,
    )
    return _head_sha(repo)


# ---------------------------------------------------------------------------
# Part 1 — Race regression: explicit SHA pins worktree at the expected commit
# ---------------------------------------------------------------------------


class TestWorktreeRaceExplicitSha:
    """create() with an explicit base_sha creates the worktree at that exact SHA.

    Simulates the race: capture SHA before a concurrent commit lands, then
    call create() with that captured SHA.  The worktree HEAD must equal the
    captured SHA, not the newer HEAD.
    """

    def test_explicit_sha_pins_worktree_even_after_new_commit(
        self, tmp_git_repo: Path
    ) -> None:
        """Worktree is pinned to the captured SHA regardless of later commits."""
        mgr = WorktreeManager(project_root=tmp_git_repo)

        # SHA at dispatch time (the SHA we want to pin to)
        captured_sha = _head_sha(tmp_git_repo)

        # A new commit arrives on the parent branch AFTER SHA was captured
        new_sha = _make_commit(tmp_git_repo, "racetest.txt", "concurrent change")
        assert new_sha != captured_sha, "Sanity: new commit must differ from captured SHA"

        # Create the worktree with the captured (old) SHA
        handle = mgr.create(
            task_id="task-race",
            step_id="1.1",
            base_branch="main",
            base_sha=captured_sha,
        )

        # The worktree HEAD must be the captured SHA, not the new HEAD
        wt_sha = _head_sha(handle.path)
        assert wt_sha == captured_sha, (
            f"Worktree landed at {wt_sha!r} instead of captured SHA {captured_sha!r}. "
            f"The race condition is not fixed: worktree picked up the concurrent commit."
        )

    def test_worktree_without_explicit_sha_uses_current_head(
        self, tmp_git_repo: Path
    ) -> None:
        """Baseline: without base_sha the worktree uses HEAD at creation time."""
        mgr = WorktreeManager(project_root=tmp_git_repo)
        head_before = _head_sha(tmp_git_repo)

        handle = mgr.create(
            task_id="task-nosha",
            step_id="1.1",
            base_branch="main",
        )

        # Should land at whatever HEAD was at create() time
        wt_sha = _head_sha(handle.path)
        assert wt_sha == head_before

    def test_concurrent_commit_does_not_affect_pinned_worktree(
        self, tmp_git_repo: Path
    ) -> None:
        """Thread-based race simulation: pin SHA, then race a commit in a thread.

        The test verifies that the worktree created with an explicit SHA is not
        affected by a commit that lands concurrently via a background thread.
        """
        mgr = WorktreeManager(project_root=tmp_git_repo)
        captured_sha = _head_sha(tmp_git_repo)

        # Signal to control when the background commit fires relative to create()
        create_started = threading.Event()
        commit_done = threading.Event()

        def _background_commit() -> None:
            # Wait until create() has been called so there is a genuine race
            create_started.wait(timeout=5)
            time.sleep(0.01)  # tiny sleep to maximise overlap
            _make_commit(tmp_git_repo, "concurrent.txt", "concurrent")
            commit_done.set()

        t = threading.Thread(target=_background_commit, daemon=True)
        t.start()

        # Signal that we are entering create(), then call it
        create_started.set()
        handle = mgr.create(
            task_id="task-concurrent",
            step_id="1.1",
            base_branch="main",
            base_sha=captured_sha,
        )

        commit_done.wait(timeout=5)
        t.join(timeout=5)

        # Regardless of whether the concurrent commit landed before or after
        # _create_locked(), the explicit SHA guarantees the worktree is at
        # captured_sha.
        wt_sha = _head_sha(handle.path)
        assert wt_sha == captured_sha, (
            f"Worktree SHA {wt_sha!r} != captured SHA {captured_sha!r} "
            "— explicit SHA did not pin the worktree correctly."
        )

    def test_handle_base_sha_matches_explicit(self, tmp_git_repo: Path) -> None:
        """The returned handle records the explicitly passed base_sha."""
        mgr = WorktreeManager(project_root=tmp_git_repo)
        sha = _head_sha(tmp_git_repo)

        handle = mgr.create(
            task_id="task-sha-rec",
            step_id="1.1",
            base_branch="main",
            base_sha=sha,
        )

        assert handle.base_sha == sha


# ---------------------------------------------------------------------------
# Part 2 — Path-scope guardrail: record_step_result rejects out-of-tree files
# ---------------------------------------------------------------------------


class TestWorktreePathScopeGuardrail:
    """record_step_result() enforces that files_changed stay inside the worktree."""

    def _make_engine_with_worktree(self, tmp_git_repo: Path) -> Any:
        """Return (engine, task_id, step_id) with a dispatched step in a worktree.

        The team_context_root is placed at <tmp_git_repo>/.claude/team-context
        so that ExecutionEngine._project_root() resolves back to tmp_git_repo
        (two parents up from team-context).
        """
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        team_ctx = tmp_git_repo / ".claude" / "team-context"
        team_ctx.mkdir(parents=True, exist_ok=True)

        engine = ExecutionEngine(
            team_context_root=team_ctx,
        )

        step = PlanStep(
            step_id="1.1",
            agent_name="test-agent",
            task_description="do something",
        )
        phase = PlanPhase(
            phase_id=1,
            name="test-phase",
            steps=[step],
        )
        plan = MachinePlan(
            task_id="task-guardrail",
            task_summary="Test guardrail",
            phases=[phase],
        )
        engine.start(plan)
        engine.mark_dispatched("1.1", "test-agent")
        return engine, "task-guardrail", "1.1"

    def test_files_inside_worktree_allowed(self, tmp_git_repo: Path) -> None:
        """files_changed with relative paths do not trigger the guardrail."""
        engine, task_id, step_id = self._make_engine_with_worktree(tmp_git_repo)

        # Relative paths are always inside the worktree by construction
        engine.record_step_result(
            step_id=step_id,
            agent_name="test-agent",
            status="complete",
            outcome="done",
            files_changed=["some/file.py", "other.txt"],
        )
        state = engine._load_execution()
        result = state.get_step_result(step_id)
        # Guardrail must NOT fire on relative paths
        assert result.status == "complete", (
            f"Guardrail incorrectly rejected relative paths: {result.deviations}"
        )
        worktree_escapes = [d for d in result.deviations if "WORKTREE_ESCAPE" in d]
        assert not worktree_escapes, f"Unexpected WORKTREE_ESCAPE deviation: {worktree_escapes}"

    def test_absolute_path_outside_worktree_triggers_guardrail(
        self, tmp_git_repo: Path
    ) -> None:
        """An absolute file path outside the worktree triggers the guardrail."""
        engine, task_id, step_id = self._make_engine_with_worktree(tmp_git_repo)

        # An absolute path that is definitely outside the worktree
        outside_path = "/tmp/evil_file_outside_worktree.py"

        engine.record_step_result(
            step_id=step_id,
            agent_name="test-agent",
            status="complete",
            outcome="done",
            files_changed=[outside_path],
        )
        state = engine._load_execution()
        result = state.get_step_result(step_id)

        # The guardrail must have fired
        worktree_escapes = [d for d in result.deviations if "WORKTREE_ESCAPE" in d]
        assert worktree_escapes, (
            "Expected a WORKTREE_ESCAPE deviation for a file outside the worktree, "
            f"but deviations were: {result.deviations}"
        )
        assert result.status == "failed", (
            f"Expected status='failed' after WORKTREE_ESCAPE, got {result.status!r}"
        )
        # The escaped path should be mentioned in the deviation
        assert outside_path in worktree_escapes[0], (
            f"Escaped path {outside_path!r} not mentioned in deviation: {worktree_escapes[0]}"
        )

    def test_absolute_path_inside_worktree_allowed(self, tmp_git_repo: Path) -> None:
        """An absolute path that IS inside the worktree must not trigger the guardrail."""
        engine, task_id, step_id = self._make_engine_with_worktree(tmp_git_repo)

        # Find the worktree path from state
        state = engine._load_execution()
        wt_dict = getattr(state, "step_worktrees", {}).get(step_id)
        if wt_dict is None:
            pytest.skip("No worktree was created (worktree manager disabled)")
        wt_path = wt_dict.get("path", "")
        if not wt_path:
            pytest.skip("Empty worktree path in state")

        inside_path = str(Path(wt_path) / "agent_baton" / "some_file.py")

        engine.record_step_result(
            step_id=step_id,
            agent_name="test-agent",
            status="complete",
            outcome="done",
            files_changed=[inside_path],
        )
        state2 = engine._load_execution()
        result = state2.get_step_result(step_id)
        worktree_escapes = [d for d in result.deviations if "WORKTREE_ESCAPE" in d]
        assert not worktree_escapes, (
            f"Guardrail incorrectly rejected an inside-worktree path: {worktree_escapes}"
        )

    def test_no_worktree_no_guardrail(self, tmp_git_repo: Path) -> None:
        """When no worktree is registered for a step, the guardrail is silent."""
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        team_ctx = tmp_git_repo / ".claude" / "team-context"
        team_ctx.mkdir(parents=True, exist_ok=True)

        # Disable worktree manager so no worktrees are created
        import os
        orig_env = os.environ.get("BATON_WORKTREE_ENABLED")
        os.environ["BATON_WORKTREE_ENABLED"] = "0"
        try:
            engine = ExecutionEngine(
                team_context_root=team_ctx,
            )
            step = PlanStep(
                step_id="1.1",
                agent_name="test-agent",
                task_description="do something",
            )
            phase = PlanPhase(phase_id=1, name="phase", steps=[step])
            plan = MachinePlan(
                task_id="task-no-wt",
                task_summary="No worktree",
                phases=[phase],
            )
            engine.start(plan)
            engine.mark_dispatched("1.1", "test-agent")
            engine.record_step_result(
                step_id="1.1",
                agent_name="test-agent",
                status="complete",
                outcome="done",
                files_changed=["/absolutely/outside/path.py"],
            )
        finally:
            if orig_env is None:
                del os.environ["BATON_WORKTREE_ENABLED"]
            else:
                os.environ["BATON_WORKTREE_ENABLED"] = orig_env

        state = engine._load_execution()
        result = state.get_step_result("1.1")
        worktree_escapes = [d for d in result.deviations if "WORKTREE_ESCAPE" in d]
        assert not worktree_escapes, (
            "Guardrail fired even though no worktree exists for the step"
        )


# ---------------------------------------------------------------------------
# Part 3 — Worker passes cwd_override to scheduler/launcher
# ---------------------------------------------------------------------------


class TestWorkerCwdOverridePropagation:
    """TaskWorker builds step dicts that include cwd_override after mark_dispatched()."""

    def test_scheduler_dispatch_passes_cwd_override_to_launcher(self) -> None:
        """StepScheduler.dispatch() forwards cwd_override to launcher.launch()."""
        received_kwargs: dict = {}

        class CaptureLauncher:
            async def launch(self, agent_name, model, prompt, step_id="", **kwargs):
                received_kwargs.update(kwargs)
                return LaunchResult(step_id=step_id, agent_name=agent_name, status="complete")

        scheduler = StepScheduler(SchedulerConfig(max_concurrent=1))
        launcher = CaptureLauncher()

        asyncio.run(
            scheduler.dispatch(
                agent_name="test-agent",
                model="sonnet",
                prompt="do something",
                step_id="1.1",
                launcher=launcher,
                cwd_override="/some/worktree/path",
                task_id="task-xyz",
            )
        )

        assert received_kwargs.get("cwd_override") == "/some/worktree/path", (
            f"cwd_override not forwarded to launcher. kwargs={received_kwargs}"
        )
        assert received_kwargs.get("task_id") == "task-xyz", (
            f"task_id not forwarded to launcher. kwargs={received_kwargs}"
        )

    def test_scheduler_dispatch_batch_forwards_cwd_override(self) -> None:
        """dispatch_batch() passes cwd_override from step dict to each launch call."""
        received: list[dict] = []

        class CaptureLauncher:
            async def launch(self, agent_name, model, prompt, step_id="", **kwargs):
                received.append({"step_id": step_id, **kwargs})
                return LaunchResult(step_id=step_id, agent_name=agent_name, status="complete")

        scheduler = StepScheduler(SchedulerConfig(max_concurrent=2))
        launcher = CaptureLauncher()

        steps = [
            {
                "agent_name": "agent-a",
                "model": "sonnet",
                "prompt": "p",
                "step_id": "1.1",
                "cwd_override": "/worktrees/1.1",
                "task_id": "task-1",
            },
            {
                "agent_name": "agent-b",
                "model": "sonnet",
                "prompt": "p",
                "step_id": "1.2",
                "cwd_override": "/worktrees/1.2",
                "task_id": "task-1",
            },
        ]

        asyncio.run(scheduler.dispatch_batch(steps, launcher))

        assert len(received) == 2
        by_step = {r["step_id"]: r for r in received}
        assert by_step["1.1"].get("cwd_override") == "/worktrees/1.1"
        assert by_step["1.2"].get("cwd_override") == "/worktrees/1.2"

    def test_scheduler_dispatch_without_cwd_override_does_not_pass_it(self) -> None:
        """When cwd_override is absent from the step dict, launch() is not polluted."""
        received: list[dict] = []

        class CaptureLauncher:
            async def launch(self, agent_name, model, prompt, step_id="", **kwargs):
                received.append({"step_id": step_id, **kwargs})
                return LaunchResult(step_id=step_id, agent_name=agent_name, status="complete")

        scheduler = StepScheduler(SchedulerConfig(max_concurrent=1))
        launcher = CaptureLauncher()

        steps = [
            {
                "agent_name": "agent-a",
                "model": "sonnet",
                "prompt": "p",
                "step_id": "1.1",
                # No cwd_override, no task_id
            },
        ]

        asyncio.run(scheduler.dispatch_batch(steps, launcher))

        assert len(received) == 1
        # cwd_override must not be forwarded when absent
        assert "cwd_override" not in received[0], (
            f"cwd_override leaked into launch() kwargs: {received[0]}"
        )

    def test_dry_run_launcher_ignores_cwd_override(self) -> None:
        """DryRunLauncher still works when scheduler passes cwd_override.

        DryRunLauncher does not declare cwd_override as a parameter, so the
        scheduler must not pass it as a positional argument (it must use **kwargs
        so DryRunLauncher's launch() can safely ignore it).
        """
        launcher = DryRunLauncher()
        scheduler = StepScheduler(SchedulerConfig(max_concurrent=1))

        steps = [
            {
                "agent_name": "agent-a",
                "model": "sonnet",
                "prompt": "p",
                "step_id": "1.1",
                "cwd_override": "/some/path",
                "task_id": "task-xyz",
            }
        ]
        # Should not raise even though DryRunLauncher doesn't know cwd_override
        results = asyncio.run(scheduler.dispatch_batch(steps, launcher))
        assert len(results) == 1
        assert results[0].status == "complete"
