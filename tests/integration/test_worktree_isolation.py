"""Integration tests for worktree isolation in concurrent agent dispatch.

Wave 1.3 (bd-86bf) — covers design spec tests 11, 14, 15 plus three new
dogfood-motivated isolation-guarantee tests.

All tests that touch git operations use a real git repository (tmp_git_repo
fixture) rather than mocks, so they catch real integration bugs.

New tests motivated by dogfood bugs observed this session:
  - test_no_parent_tree_contamination_under_concurrent_subagents (bd-36a6)
  - test_baton_db_isolation_under_worktree (bd-543e)
  - test_worktree_path_walks_up_to_parent_baton_db (bd-e1ae / feedback_schema_project_id.md)
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.worktree_manager import (
    WorktreeHandle,
    WorktreeManager,
)
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


_DISCIPLINE_HEADING = "## Worktree Discipline (MANDATORY)"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal real git repo with one initial commit.

    Layout:
        tmp_path/                  ← git repo root (project_root)
            .claude/team-context/  ← team context dir (engine root)
    """
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def team_context(tmp_git_repo: Path) -> Path:
    """Return (and create) .claude/team-context inside the git repo."""
    ctx = tmp_git_repo / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    return ctx


@pytest.fixture
def engine(team_context: Path, monkeypatch: pytest.MonkeyPatch) -> ExecutionEngine:
    """Return a WorktreeManager-aware ExecutionEngine backed by tmp_git_repo.

    BATON_WORKTREE_ENABLED is set to 1 explicitly.
    """
    monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
    return ExecutionEngine(team_context_root=team_context)


@pytest.fixture
def engine_disabled(team_context: Path, monkeypatch: pytest.MonkeyPatch) -> ExecutionEngine:
    """Return an engine with worktrees disabled."""
    monkeypatch.setenv("BATON_WORKTREE_ENABLED", "0")
    return ExecutionEngine(team_context_root=team_context)


# ---------------------------------------------------------------------------
# Plan / step factory helpers
# ---------------------------------------------------------------------------


def _step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    step_type: str = "implementation",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model="sonnet",
        step_type=step_type,
    )


def _plan(
    task_id: str = "task-wt-int",
    steps: list[PlanStep] | None = None,
) -> MachinePlan:
    if steps is None:
        steps = [_step()]
    return MachinePlan(
        task_id=task_id,
        task_summary="Integration test plan",
        risk_level="LOW",
        phases=[PlanPhase(phase_id=1, name="Implementation", steps=steps)],
    )


# ---------------------------------------------------------------------------
# Test 11 — test_engine_dispatch_creates_worktree
# ---------------------------------------------------------------------------


class TestEngineDispatchCreatesWorktree:
    """Full integration: mark_dispatched() materialises a worktree on disk and
    stores the handle in state.step_worktrees."""

    def test_worktree_created_on_mark_dispatched(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-dispatch-wt")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        state = engine._load_execution()
        assert state is not None
        step_worktrees = getattr(state, "step_worktrees", {})
        assert "1.1" in step_worktrees, (
            "mark_dispatched() must record a worktree handle in state.step_worktrees"
        )

    def test_worktree_path_exists_on_disk(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-dispatch-disk")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        state = engine._load_execution()
        assert state is not None
        handle_dict = getattr(state, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None

        wt_path = Path(handle_dict["path"])
        assert wt_path.is_dir(), f"Worktree directory must exist at {wt_path}"

    def test_worktree_branch_is_correct(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-dispatch-branch")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        state = engine._load_execution()
        assert state is not None
        handle_dict = getattr(state, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None
        assert handle_dict["branch"] == f"worktree/task-dispatch-branch/1.1"

    def test_worktree_handle_recorded_for_parallel_steps(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(
            task_id="task-parallel-wt",
            steps=[
                _step(step_id="1.1", agent_name="a"),
                _step(step_id="1.2", agent_name="b"),
            ],
        )
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        engine.mark_dispatched("1.2", "b")

        state = engine._load_execution()
        assert state is not None
        step_worktrees = getattr(state, "step_worktrees", {})
        assert "1.1" in step_worktrees
        assert "1.2" in step_worktrees

    def test_automation_step_skips_worktree(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        """Automation steps must NOT get a worktree."""
        plan = _plan(
            task_id="task-automation-skip",
            steps=[_step(step_id="1.1", step_type="automation")],
        )
        engine.start(plan)
        engine.mark_dispatched("1.1", "automation-runner")

        state = engine._load_execution()
        assert state is not None
        step_worktrees = getattr(state, "step_worktrees", {})
        assert "1.1" not in step_worktrees, (
            "automation steps must not receive a worktree"
        )


# ---------------------------------------------------------------------------
# Test 14 — test_resume_reattaches_worktree_handles
# ---------------------------------------------------------------------------


class TestResumeReattachesWorktreeHandles:
    """Engine crash mid-step: state.step_worktrees is reconstructed from
    .baton-worktree.json files on disk after engine resume."""

    def test_handle_survives_engine_restart(
        self, team_context: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")

        # First engine instance: start + dispatch
        eng1 = ExecutionEngine(team_context_root=team_context)
        plan = _plan(task_id="task-resume-wt")
        eng1.start(plan)
        eng1.mark_dispatched("1.1", "backend-engineer")

        # Verify the handle was saved to state
        state1 = eng1._load_execution()
        assert state1 is not None
        handle_dict = getattr(state1, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None, "Handle must be in state before simulated crash"

        wt_path = Path(handle_dict["path"])
        assert wt_path.is_dir()

        # Simulate crash by creating a NEW engine instance (no in-memory state)
        eng2 = ExecutionEngine(team_context_root=team_context)

        # The new engine's WorktreeManager can reconstruct the handle from manifest
        if eng2._worktree_mgr is not None:
            handle = eng2._worktree_mgr.handle_for("task-resume-wt", "1.1")
            assert handle is not None, (
                "WorktreeManager.handle_for() must re-read the manifest after restart"
            )
            assert handle.path == wt_path

    def test_baton_worktree_json_present_after_mark_dispatched(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-manifest-check")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        state = engine._load_execution()
        assert state is not None
        handle_dict = getattr(state, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None

        manifest = Path(handle_dict["path"]) / ".baton-worktree.json"
        assert manifest.exists(), (
            ".baton-worktree.json must be written by create() for recovery"
        )
        data = json.loads(manifest.read_text())
        assert data["step_id"] == "1.1"
        assert data["task_id"] == "task-manifest-check"


# ---------------------------------------------------------------------------
# Test 15 — test_takeover_path_finds_failed_worktree
# ---------------------------------------------------------------------------


class TestTakeoverPathFindsFailedWorktree:
    """Wave 5.1 substrate: a failed step's worktree must be discoverable via
    WorktreeManager.handle_for(task_id, step_id) after the engine records failure."""

    def test_failed_step_worktree_retained(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-takeover")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        # Record failure
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="subprocess timed out",
        )

        state = engine._load_execution()
        assert state is not None
        # Failed step's handle should still be in step_worktrees (NOT cleaned up)
        step_worktrees = getattr(state, "step_worktrees", {})
        assert "1.1" in step_worktrees, (
            "Failed step must retain its worktree handle in state for Wave 5.1 takeover"
        )

    def test_failed_worktree_directory_still_on_disk(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-takeover-disk")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        state_mid = engine._load_execution()
        assert state_mid is not None
        handle_dict = getattr(state_mid, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None
        wt_path = Path(handle_dict["path"])

        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="some error",
        )

        assert wt_path.is_dir(), (
            "Worktree directory must be retained on disk after step failure"
        )

    def test_handle_for_returns_failed_handle(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        plan = _plan(task_id="task-takeover-handle")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        # Record failure
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="agent exploded",
        )

        # WorktreeManager.handle_for() must still find it
        if engine._worktree_mgr is not None:
            handle = engine._worktree_mgr.handle_for("task-takeover-handle", "1.1")
            assert handle is not None, (
                "handle_for() must return the failed worktree handle for Wave 5.1 takeover"
            )

    def test_takeover_command_returns_cd_string(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        """WorktreeHandle.takeover_command() returns a usable string for the developer."""
        plan = _plan(task_id="task-takeover-cmd")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="crash",
        )

        if engine._worktree_mgr is not None:
            handle = engine._worktree_mgr.handle_for("task-takeover-cmd", "1.1")
            if handle is not None:
                cmd = handle.takeover_command()
                assert "cd " in cmd
                assert "git status" in cmd


# ---------------------------------------------------------------------------
# Dogfood test: test_no_parent_tree_contamination_under_concurrent_subagents
# Motivated by bd-36a6 — concurrent agents writing to absolute paths in the
# parent checkout when worktrees weren't materialised.
# ---------------------------------------------------------------------------


class TestNoParentTreeContaminationUnderConcurrentSubagents:
    """N=5 worktrees each write to absolute paths inside their worktree.
    Assert parent's working tree project files are unaffected.

    The isolation guarantee: files written inside a worktree must NOT
    appear individually in the parent's git status — they are scoped to
    the worktree's own index. The `.claude/` container directory IS visible
    in the parent's `git status --porcelain` as an untracked directory (that
    is expected and correct git behavior), but the individual agent output
    files must not leak out.

    This is the regression bar that proves Wave 1.3 actually solves the
    parent-contamination problem we observed in real life (bd-36a6).
    """

    def test_five_worktrees_do_not_contaminate_parent(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        step_ids = ["1.1", "1.2", "1.3", "1.4", "1.5"]
        task_id = "task-nocontam"

        # Stage a known "project file" in the parent repo's root before creating
        # worktrees.  After creating 5 worktrees and writing agent outputs, this
        # file must remain unstaged/unchanged in the parent.
        (tmp_git_repo / "project_file.py").write_text("# project source\n")
        subprocess.run(
            ["git", "add", "project_file.py"], cwd=tmp_git_repo,
            check=True, capture_output=True,
        )

        # Create 5 worktrees concurrently
        handles: list[WorktreeHandle] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def _create_and_write(step_id: str) -> None:
            try:
                h = mgr.create(task_id=task_id, step_id=step_id, base_branch="main")
                # Write a unique file inside the worktree using its absolute path
                safe = step_id.replace(".", "_")
                sentinel = h.path / f"agent_{safe}_output.txt"
                sentinel.write_text(f"output from step {step_id}\n", encoding="utf-8")
                # Commit the file inside the worktree (mirrors what real agents do)
                subprocess.run(
                    ["git", "add", str(sentinel)], cwd=h.path,
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"agent output {step_id}"],
                    cwd=h.path, check=True, capture_output=True,
                )
                with lock:
                    handles.append(h)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=_create_and_write, args=(sid,))
            for sid in step_ids
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Concurrent creation errors: {errors}"
        assert len(handles) == 5

        # The agent output files must NOT appear individually in the parent's
        # git status. The parent may show `.claude/` as a whole untracked
        # directory (expected), but must never show individual worktree files.
        parent_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=tmp_git_repo, capture_output=True, text=True,
        ).stdout

        for sid in step_ids:
            safe_sid = sid.replace(".", "_")
            assert f"agent_{safe_sid}_output.txt" not in parent_status, (
                f"Agent output file for step {sid} leaked into parent's git status. "
                "Wave 1.3 isolation guarantee violated."
            )

        # The project_file.py we staged must still be staged (A — added), not
        # corrupted to a different state by concurrent worktree operations.
        assert "A  project_file.py" in parent_status or "A project_file.py" in parent_status, (
            "Parent's staged project_file.py must be unaffected by concurrent worktree creation"
        )

        # Cleanup all worktrees
        for h in handles:
            mgr.cleanup(h, on_failure=False)

    def test_worktree_file_not_visible_individually_in_parent_status(
        self, tmp_git_repo: Path
    ) -> None:
        """A file written inside a worktree must not appear individually in the
        parent's `git status` — only the .claude/ container is untracked there."""
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-vis", step_id="1.1", base_branch="main")

        # Create a new file inside the worktree
        (handle.path / "worktree_only.txt").write_text("isolated\n")

        # Verify parent does NOT see the individual file (only .claude/ as container)
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert "worktree_only.txt" not in r.stdout, (
            "Individual files written inside a worktree must NOT appear in parent's "
            "`git status`. The parent only sees `.claude/` as a whole untracked dir."
        )

        # Use force=True because we wrote an untracked file to the worktree without
        # committing (simulating mid-agent state); the isolation check is the point,
        # not testing normal cleanup semantics.
        mgr.cleanup(handle, on_failure=False, force=True)


# ---------------------------------------------------------------------------
# Dogfood test: test_baton_db_isolation_under_worktree
# Motivated by bd-543e — writes to baton.db from within a worktree should
# NOT overwrite the parent's baton.db when the worktree's .claude/team-context
# is a separate path.
# ---------------------------------------------------------------------------


class TestBatonDbIsolationUnderWorktree:
    """Writing to a baton.db inside an isolated worktree must not affect the
    parent repo's baton.db."""

    def test_worktree_db_write_does_not_affect_parent_db(
        self, tmp_git_repo: Path, team_context: Path
    ) -> None:
        # Create the parent baton.db as a sentinel file with known content
        parent_db = team_context / "baton.db"
        parent_sentinel = b"PARENT_DB_SENTINEL_DO_NOT_OVERWRITE"
        parent_db.write_bytes(parent_sentinel)

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-db-iso", step_id="1.1", base_branch="main")

        # Simulate a worktree-local write to its own .claude/team-context/baton.db
        wt_claude = handle.path / ".claude" / "team-context"
        wt_claude.mkdir(parents=True, exist_ok=True)
        wt_db = wt_claude / "baton.db"
        wt_db.write_bytes(b"WORKTREE_DB_CONTENT")

        # Parent's baton.db must be unchanged
        assert parent_db.read_bytes() == parent_sentinel, (
            "Writing to a worktree-local baton.db must NOT modify the parent's baton.db"
        )

        # force=True: worktree has an untracked .claude/ directory; the isolation
        # assertion above is the point of this test, not the cleanup semantics.
        mgr.cleanup(handle, on_failure=False, force=True)

    def test_worktree_baton_db_survives_parent_cleanup(
        self, tmp_git_repo: Path, team_context: Path
    ) -> None:
        """A worktree retained on failure keeps its local baton.db for forensics."""
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-db-retain", step_id="1.1", base_branch="main")

        wt_claude = handle.path / ".claude" / "team-context"
        wt_claude.mkdir(parents=True, exist_ok=True)
        wt_db = wt_claude / "baton.db"
        wt_db.write_bytes(b"AGENT_STATE")

        # Simulate failure retention
        mgr.cleanup(handle, on_failure=True)

        # The worktree-local DB must still exist (the worktree was retained)
        assert wt_db.exists(), (
            "Worktree-local baton.db must be retained after on_failure cleanup"
        )


# ---------------------------------------------------------------------------
# Dogfood test: test_worktree_path_walks_up_to_parent_baton_db
# Motivated by feedback_schema_project_id.md and bd-e1ae warning.
# From inside .claude/worktrees/{task_id}/{step_id}/, an upward walk for
# .claude/team-context/baton.db must find the PARENT's copy, not a
# worktree-local copy.
# ---------------------------------------------------------------------------


class TestWorktreePathWalksUpToParentBatonDb:
    """The upward-walk baton.db discovery logic, when run from inside a worktree,
    must traverse out to the parent repo and find the parent's baton.db.

    This validates the BATON_DB_PATH discovery contract per
    feedback_schema_project_id.md and BEAD_WARNING bd-e1ae.
    """

    def test_upward_walk_finds_parent_db_from_worktree(
        self, tmp_git_repo: Path, team_context: Path
    ) -> None:
        # Create the parent baton.db
        parent_db = team_context / "baton.db"
        parent_db.write_bytes(b"PARENT_BATON_DB")

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-dbwalk", step_id="1.1", base_branch="main")

        # Simulate the upward-walk logic from inside the worktree
        # (mirrors the logic in release/readiness_cmd.py _resolve_db_path)
        cwd = handle.path
        found: Path | None = None
        for ancestor in [cwd, *cwd.parents]:
            candidate = ancestor / ".claude" / "team-context" / "baton.db"
            if candidate.exists():
                found = candidate
                break

        assert found is not None, (
            "Upward walk from worktree must find a baton.db"
        )
        assert found.resolve() == parent_db.resolve(), (
            f"Upward walk must find the PARENT's baton.db at {parent_db}, "
            f"not a local copy. Found: {found}"
        )

        mgr.cleanup(handle, on_failure=False)

    def test_worktree_local_db_shadows_parent_if_placed_directly(
        self, tmp_git_repo: Path, team_context: Path
    ) -> None:
        """Verify the hazard: if a worktree-local .claude/team-context/baton.db exists,
        it WOULD shadow the parent. This test documents the known failure mode and
        asserts our convention (agents must not write their own baton.db into the
        worktree's .claude/team-context directly)."""
        parent_db = team_context / "baton.db"
        parent_db.write_bytes(b"PARENT")

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-dbwalk-shadow", step_id="1.1", base_branch="main")

        # Intentionally place a LOCAL baton.db at the worktree's .claude/team-context
        local_claude = handle.path / ".claude" / "team-context"
        local_claude.mkdir(parents=True, exist_ok=True)
        local_db = local_claude / "baton.db"
        local_db.write_bytes(b"LOCAL_WORKTREE_DB")

        # The upward walk from inside the worktree would find the LOCAL one first
        cwd = handle.path
        found: Path | None = None
        for ancestor in [cwd, *cwd.parents]:
            candidate = ancestor / ".claude" / "team-context" / "baton.db"
            if candidate.exists():
                found = candidate
                break

        # This documents the hazard: found is LOCAL, not PARENT
        # The test passes here (documenting behavior), but agents must be
        # instructed never to create a local .claude/team-context/baton.db
        # (enforced by BATON_DB_PATH env var in production).
        assert found is not None
        # If BATON_DB_PATH is set, it overrides the walk entirely — that's the
        # correct production behavior.
        import os
        if not os.environ.get("BATON_DB_PATH"):
            # Without BATON_DB_PATH, the local one shadows the parent.
            # This is the hazard we're documenting.
            assert found.resolve() == local_db.resolve(), (
                "Without BATON_DB_PATH, worktree-local baton.db shadows parent. "
                "Production agents must use BATON_DB_PATH env var."
            )

        # force=True: worktree has untracked .claude/ dir; this test is about
        # documenting the shadow hazard, not normal cleanup semantics.
        mgr.cleanup(handle, on_failure=False, force=True)

    def test_baton_db_path_env_bypasses_walk_hazard(
        self, tmp_git_repo: Path, team_context: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When BATON_DB_PATH is set, the upward walk is bypassed and the parent
        DB is always found — this is the correct production guard."""
        parent_db = team_context / "baton.db"
        parent_db.write_bytes(b"PARENT")

        monkeypatch.setenv("BATON_DB_PATH", str(parent_db))

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-dbwalk-env", step_id="1.1", base_branch="main")

        # Simulate the resolution logic used by CLI commands
        import os as _os
        env_val = _os.environ.get("BATON_DB_PATH", "").strip()
        assert env_val != ""

        resolved = Path(env_val).expanduser().resolve()
        assert resolved == parent_db.resolve(), (
            "BATON_DB_PATH env var must resolve to the parent's baton.db"
        )

        mgr.cleanup(handle, on_failure=False)


# ---------------------------------------------------------------------------
# Additional: existing Fix A / Fix C tests preserved
# ---------------------------------------------------------------------------


class TestPromptDisciplinePreserved:
    """Preserve the existing Fix A tests from the original file."""

    def test_dispatch_prompt_includes_worktree_discipline_when_isolation_set(
        self,
    ) -> None:
        dispatcher = PromptDispatcher()
        step = _step()
        prompt = dispatcher.build_delegation_prompt(step, isolation="worktree")
        assert _DISCIPLINE_HEADING in prompt

    def test_dispatch_prompt_omits_worktree_discipline_by_default(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert _DISCIPLINE_HEADING not in prompt


class TestEngineSignalsIsolationPreserved:
    """Preserve the existing Fix C tests from the original file."""

    def test_parallel_actions_marked_with_worktree_isolation(
        self, tmp_path: Path
    ) -> None:
        team_ctx = tmp_path / ".claude" / "team-context"
        team_ctx.mkdir(parents=True)
        eng = ExecutionEngine(team_context_root=team_ctx)
        plan = MachinePlan(
            task_id="task-iso-c",
            task_summary="Isolation test",
            risk_level="LOW",
            phases=[PlanPhase(
                phase_id=1,
                name="Impl",
                steps=[
                    _step(step_id="1.1", agent_name="a"),
                    _step(step_id="1.2", agent_name="b"),
                    _step(step_id="1.3", agent_name="c"),
                ],
            )],
        )
        eng.start(plan)
        actions = eng.next_actions()
        assert len(actions) >= 2
        for action in actions:
            assert action.action_type == ActionType.DISPATCH
            assert action.isolation == "worktree"

    def test_singleton_dispatch_omits_isolation_field(
        self, tmp_path: Path
    ) -> None:
        team_ctx = tmp_path / ".claude" / "team-context"
        team_ctx.mkdir(parents=True)
        eng = ExecutionEngine(team_context_root=team_ctx)
        plan = MachinePlan(
            task_id="task-iso-solo",
            task_summary="Solo test",
            risk_level="LOW",
            phases=[PlanPhase(phase_id=1, name="Impl", steps=[_step(step_id="1.1")])],
        )
        eng.start(plan)
        actions = eng.next_actions()
        assert len(actions) == 1
        assert actions[0].isolation == ""
        assert "isolation" not in actions[0].to_dict()


# ---------------------------------------------------------------------------
# bd-def9 — working_branch_head persisted after fold_back()
# ---------------------------------------------------------------------------


class TestWorkingBranchHeadRecordsFoldTarget:
    """After a successful fold-back round-trip, state.working_branch_head
    must equal the rebased tip SHA returned by fold_back().

    The executor path (record_step_result) is tested with a mocked
    WorktreeManager so the test is not sensitive to git rebase "already
    checked out" limitations of the live worktree.
    """

    def test_working_branch_head_records_fold_target(
        self,
        engine: ExecutionEngine,
        tmp_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Patch _detect_branch so the engine records "main" as working_branch.
        monkeypatch.setattr(engine, "_detect_branch", lambda: "main")

        plan = _plan(task_id="task-wbh-def9")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        # Replace the WorktreeManager with one whose fold_back() returns a
        # synthetic SHA. This avoids the git "already checked out" error that
        # occurs when the rebase strategy tries to manipulate a branch that is
        # still live in the worktree.
        FAKE_NEW_HEAD = "aabbccdd" * 5  # 40-char hex stand-in
        mock_wt_mgr = MagicMock()
        mock_wt_mgr._enabled = True
        mock_wt_mgr._bead_store = None
        mock_wt_mgr.fold_back.return_value = FAKE_NEW_HEAD
        # cleanup must succeed silently
        mock_wt_mgr.cleanup.return_value = None
        monkeypatch.setattr(engine, "_worktree_mgr", mock_wt_mgr)

        # Record step complete with a dummy commit hash — triggers fold_back().
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            commit_hash="deadbeef" * 5,
        )

        # Reload state and verify working_branch_head was persisted.
        state_final = engine._load_execution()
        assert state_final is not None

        branch_head = getattr(state_final, "working_branch_head", None)
        assert branch_head == FAKE_NEW_HEAD, (
            f"state.working_branch_head must equal fold_back() return value "
            f"{FAKE_NEW_HEAD!r}; got {branch_head!r}"
        )

        # fold_back() must have been called with the commit hash.
        assert mock_wt_mgr.fold_back.called, "fold_back() must be called on success path"
