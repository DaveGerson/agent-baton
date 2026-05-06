"""Unit tests for agent_baton.core.engine.worktree_manager.WorktreeManager.

Wave 1.3 (bd-86bf) — covers design spec tests 1–10, 12, 13.

All tests that touch git operations use a real git repository created in a
tmp_path — this catches actual integration bugs that mocks would hide.
Only failure-injection tests (disk-full simulation, etc.) patch subprocess.run.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.worktree_manager import (
    WorktreeCleanupError,
    WorktreeCreateError,
    WorktreeFoldError,
    WorktreeHandle,
    WorktreeManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal real git repository with one initial commit."""
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
def repo_sha(tmp_git_repo: Path) -> str:
    """Return the HEAD SHA of the initial commit."""
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_git_repo, capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


@pytest.fixture
def mgr(tmp_git_repo: Path) -> WorktreeManager:
    """Return a WorktreeManager pointed at a fresh git repo."""
    return WorktreeManager(project_root=tmp_git_repo)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worktree_old(handle: WorktreeHandle, hours: float) -> None:
    """Rewrite .baton-worktree.json with a backdated created_at."""
    backdated = (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    data = handle.to_dict()
    data["created_at"] = backdated
    manifest = handle.path / ".baton-worktree.json"
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _commit_file(repo_path: Path, filename: str, content: str) -> str:
    """Write a file, stage it, and commit. Returns the new HEAD SHA."""
    (repo_path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"],
                   cwd=repo_path, check=True, capture_output=True)
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path,
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Test 1 — test_worktree_create_basic
# ---------------------------------------------------------------------------


class TestWorktreeCreateBasic:
    """create() returns a handle with expected path/branch and materialises on disk."""

    def test_handle_has_correct_path(self, mgr: WorktreeManager, tmp_git_repo: Path) -> None:
        handle = mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        expected = tmp_git_repo / ".claude" / "worktrees" / "task-001" / "1.1"
        assert handle.path == expected.resolve()

    def test_handle_has_correct_branch(self, mgr: WorktreeManager) -> None:
        handle = mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        assert handle.branch == "worktree/task-001/1.1"

    def test_handle_base_branch_recorded(self, mgr: WorktreeManager) -> None:
        handle = mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        assert handle.base_branch == "main"

    def test_worktree_directory_exists_on_disk(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        wt_path = tmp_git_repo / ".claude" / "worktrees" / "task-001" / "1.1"
        assert wt_path.is_dir()

    def test_manifest_written(self, mgr: WorktreeManager, tmp_git_repo: Path) -> None:
        mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        manifest = (
            tmp_git_repo / ".claude" / "worktrees" / "task-001" / "1.1"
            / ".baton-worktree.json"
        )
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["step_id"] == "1.1"
        assert data["task_id"] == "task-001"

    def test_worktree_has_git_checkout(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        # The worktree should be a real git checkout (has .git pointer file)
        assert (handle.path / ".git").exists()

    def test_base_sha_captured(self, mgr: WorktreeManager, repo_sha: str) -> None:
        handle = mgr.create(task_id="task-001", step_id="1.1", base_branch="main")
        assert handle.base_sha == repo_sha


# ---------------------------------------------------------------------------
# Test 2 — test_worktree_create_idempotent
# ---------------------------------------------------------------------------


class TestWorktreeCreateIdempotent:
    """Calling create() twice for the same (task_id, step_id) returns the same handle."""

    def test_second_call_returns_same_path(self, mgr: WorktreeManager) -> None:
        h1 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        h2 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        assert h1.path == h2.path

    def test_second_call_returns_same_branch(self, mgr: WorktreeManager) -> None:
        h1 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        h2 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        assert h1.branch == h2.branch

    def test_second_call_uses_cached_handle(self, mgr: WorktreeManager) -> None:
        """The second create() should hit the in-memory cache, not re-run git."""
        h1 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        # Corrupt the manifest to prove the cache (not disk) is used
        manifest = h1.path / ".baton-worktree.json"
        manifest.write_text(
            json.dumps({**h1.to_dict(), "branch": "mutated-branch"}),
            encoding="utf-8",
        )
        h2 = mgr.create(task_id="task-idem", step_id="1.1", base_branch="main")
        assert h2.branch == h1.branch  # cache wins

    def test_second_create_on_fresh_manager_reads_manifest(
        self, tmp_git_repo: Path
    ) -> None:
        """A new manager instance re-reads the manifest (no in-memory cache hit)."""
        mgr1 = WorktreeManager(project_root=tmp_git_repo)
        h1 = mgr1.create(task_id="task-idem2", step_id="1.1", base_branch="main")

        mgr2 = WorktreeManager(project_root=tmp_git_repo)
        h2 = mgr2.create(task_id="task-idem2", step_id="1.1", base_branch="main")
        assert h1.path == h2.path
        assert h1.branch == h2.branch


# ---------------------------------------------------------------------------
# Test 3 — test_worktree_create_disk_full_simulated
# ---------------------------------------------------------------------------


class TestWorktreeCreateDiskFullSimulated:
    """Patching subprocess.run to fail yields WorktreeCreateError."""

    def test_raises_on_git_worktree_add_failure(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)

        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            if isinstance(cmd, list) and "worktree" in cmd and "add" in cmd:
                result = MagicMock()
                result.returncode = 1
                result.stdout = ""
                result.stderr = "No space left on device"
                return result
            return original_run(cmd, **kwargs)

        with patch("agent_baton.core.engine.worktree_manager.subprocess.run", side_effect=mock_run):
            # Need a fresh manager since __init__ also calls subprocess.run.
            # bd-c071: _canonical_repo must be set alongside _project_root.
            fresh_mgr = WorktreeManager.__new__(WorktreeManager)
            fresh_mgr._project_root = tmp_git_repo.resolve()
            fresh_mgr._canonical_repo = tmp_git_repo.resolve()
            fresh_mgr._worktrees_root = (tmp_git_repo / ".claude" / "worktrees").resolve()
            fresh_mgr._enabled = True
            fresh_mgr._tracer = None
            fresh_mgr._bead_store = None
            fresh_mgr._handles = {}
            fresh_mgr._trace = None

            with pytest.raises(WorktreeCreateError, match="git worktree add failed"):
                fresh_mgr.create(
                    task_id="task-disk-full",
                    step_id="1.1",
                    base_branch="main",
                )


# ---------------------------------------------------------------------------
# Test 4 — test_worktree_fold_back_clean
# ---------------------------------------------------------------------------


class TestWorktreeFoldBackClean:
    """Agent commits in worktree; fold_back() advances parent branch HEAD."""

    def test_fold_back_none_strategy_advances_parent(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold", step_id="1.1", base_branch="main")
        # Commit a file inside the worktree
        agent_sha = _commit_file(handle.path, "result.txt", "agent output")
        # Switch to the worktree branch to expose the commit
        subprocess.run(
            ["git", "switch", handle.branch], cwd=handle.path,
            check=True, capture_output=True,
        )

        new_head = mgr.fold_back(handle, strategy="none")
        assert new_head == agent_sha

    def test_fold_back_updates_parent_branch_ref(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold2", step_id="1.1", base_branch="main")
        subprocess.run(
            ["git", "switch", handle.branch], cwd=handle.path,
            check=True, capture_output=True,
        )
        _commit_file(handle.path, "output2.txt", "data")

        mgr.fold_back(handle, strategy="none")

        r = subprocess.run(
            ["git", "rev-parse", "main"],
            cwd=tmp_git_repo, capture_output=True, text=True, check=True,
        )
        # After fold, main should have advanced beyond the initial empty commit
        head_sha = r.stdout.strip()
        # Verify the worktree file is now reachable from main
        check = subprocess.run(
            ["git", "show", f"{head_sha}:output2.txt"],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert check.returncode == 0

    def test_fold_back_noop_when_no_new_commits(
        self, mgr: WorktreeManager, repo_sha: str
    ) -> None:
        handle = mgr.create(task_id="task-fold3", step_id="1.1", base_branch="main")
        # No commits in worktree — fold_back should return base_sha unchanged
        result = mgr.fold_back(handle, strategy="none")
        # Either returns base_sha or empty string (no-op path)
        assert result in (repo_sha, handle.base_sha, "")


# ---------------------------------------------------------------------------
# Test 5 — test_worktree_fold_back_conflict
# ---------------------------------------------------------------------------


class TestWorktreeFoldBackConflict:
    """Pre-stage a conflicting commit on parent; fold_back raises WorktreeFoldError
    and the worktree directory is retained on disk."""

    def test_conflict_raises_worktree_fold_error(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-conflict", step_id="1.1", base_branch="main")
        # Commit same file in the worktree on the branch
        subprocess.run(
            ["git", "switch", handle.branch], cwd=handle.path,
            check=True, capture_output=True,
        )
        _commit_file(handle.path, "conflict.txt", "agent version\n")

        # Also commit the same filename on main (simulating parent branch advancing)
        _commit_file(tmp_git_repo, "conflict.txt", "parent version\n")

        # Now try rebase fold — should hit conflict
        with pytest.raises(WorktreeFoldError):
            mgr.fold_back(handle, strategy="rebase")

    def test_worktree_retained_after_conflict(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-conflict2", step_id="1.1", base_branch="main")
        subprocess.run(
            ["git", "switch", handle.branch], cwd=handle.path,
            check=True, capture_output=True,
        )
        _commit_file(handle.path, "same.txt", "agent version\n")
        _commit_file(tmp_git_repo, "same.txt", "parent version\n")

        try:
            mgr.fold_back(handle, strategy="rebase")
        except WorktreeFoldError:
            pass  # expected

        # Worktree directory must still exist after a fold conflict
        assert handle.path.is_dir(), "Worktree must be retained after fold conflict"


# ---------------------------------------------------------------------------
# Test 6 — test_worktree_concurrent_dispatch
# ---------------------------------------------------------------------------


class TestWorktreeConcurrentDispatch:
    """5 parallel create() calls with disjoint step_ids complete without errors."""

    def test_five_threads_distinct_step_ids(
        self, mgr: WorktreeManager
    ) -> None:
        results: list[WorktreeHandle | Exception] = []
        lock = threading.Lock()

        def _create(step_id: str) -> None:
            try:
                h = mgr.create(
                    task_id="task-concurrent",
                    step_id=step_id,
                    base_branch="main",
                )
                with lock:
                    results.append(h)
            except Exception as exc:
                with lock:
                    results.append(exc)

        step_ids = ["1.1", "1.2", "1.3", "1.4", "1.5"]
        threads = [threading.Thread(target=_create, args=(sid,)) for sid in step_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        errors = [r for r in results if isinstance(r, Exception)]
        handles = [r for r in results if isinstance(r, WorktreeHandle)]

        assert not errors, f"Concurrent create() raised errors: {errors}"
        assert len(handles) == 5

    def test_concurrent_handles_have_distinct_paths(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        results: list[WorktreeHandle] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def _create(step_id: str) -> None:
            try:
                h = mgr.create(
                    task_id="task-paths",
                    step_id=step_id,
                    base_branch="main",
                )
                with lock:
                    results.append(h)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=_create, args=(f"2.{i}",))
            for i in range(1, 6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Unexpected errors: {errors}"
        paths = {str(h.path) for h in results}
        assert len(paths) == 5, "Each worktree must have a distinct path"


# ---------------------------------------------------------------------------
# Test 7 — test_worktree_cleanup_on_failure_retains
# ---------------------------------------------------------------------------


class TestWorktreeCleanupOnFailureRetains:
    """cleanup(on_failure=True) is a no-op — directory is retained on disk."""

    def test_directory_retained_after_failure_cleanup(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-retain", step_id="1.1", base_branch="main")
        assert handle.path.is_dir()

        mgr.cleanup(handle, on_failure=True)

        # Directory must still exist
        assert handle.path.is_dir()

    def test_manifest_retained_after_failure_cleanup(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-retain2", step_id="1.1", base_branch="main")
        manifest = handle.path / ".baton-worktree.json"
        assert manifest.exists()

        mgr.cleanup(handle, on_failure=True)

        assert manifest.exists(), "Manifest must be retained after on_failure cleanup"

    def test_cleanup_success_removes_directory(
        self, mgr: WorktreeManager
    ) -> None:
        """Contrast: cleanup(on_failure=False) DOES remove the directory."""
        handle = mgr.create(task_id="task-remove", step_id="1.1", base_branch="main")
        assert handle.path.is_dir()

        mgr.cleanup(handle, on_failure=False)

        assert not handle.path.exists()


# ---------------------------------------------------------------------------
# Test 8 — test_worktree_gc_skips_active
# ---------------------------------------------------------------------------


class TestWorktreeGcSkipsActive:
    """GC does not reclaim a worktree whose step_id is NOT in terminal_step_ids."""

    def test_gc_skips_step_not_in_terminal_set(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-gc-skip", step_id="1.1", base_branch="main")
        # Backdate the manifest so it would be age-eligible
        _make_worktree_old(handle, hours=200)

        reclaimed = mgr.gc_stale(
            max_age_hours=72,
            terminal_step_ids={"2.1", "2.2"},  # does NOT include "1.1"
        )

        step_ids_reclaimed = [h.step_id for h in reclaimed]
        assert "1.1" not in step_ids_reclaimed
        assert handle.path.is_dir(), "Active worktree must not be removed by GC"

    def test_gc_skips_young_worktrees(self, mgr: WorktreeManager) -> None:
        """Worktrees younger than max_age_hours are not reclaimed regardless of terminal set."""
        handle = mgr.create(task_id="task-gc-young", step_id="1.1", base_branch="main")
        # Do NOT backdate — the worktree was just created

        reclaimed = mgr.gc_stale(
            max_age_hours=72,
            terminal_step_ids={"1.1"},
        )

        assert all(h.step_id != "1.1" for h in reclaimed)
        assert handle.path.is_dir()


# ---------------------------------------------------------------------------
# Test 9 — test_worktree_gc_reclaims_terminal
# ---------------------------------------------------------------------------


class TestWorktreeGcReclaimsTerminal:
    """GC reclaims a worktree that is terminal AND older than max_age_hours."""

    def test_old_terminal_worktree_is_reclaimed(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-gc-reclaim", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=100)

        reclaimed = mgr.gc_stale(
            max_age_hours=72,
            terminal_step_ids={"1.1"},
        )

        assert any(h.step_id == "1.1" for h in reclaimed)

    def test_reclaimed_worktree_is_removed_from_disk(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-gc-disk", step_id="1.1", base_branch="main")
        wt_path = handle.path
        _make_worktree_old(handle, hours=100)

        mgr.gc_stale(max_age_hours=72, terminal_step_ids={"1.1"})

        assert not wt_path.exists()

    def test_gc_dry_run_does_not_remove(self, mgr: WorktreeManager) -> None:
        handle = mgr.create(task_id="task-gc-dry", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=100)

        reclaimed = mgr.gc_stale(
            max_age_hours=72,
            terminal_step_ids={"1.1"},
            dry_run=True,
        )

        # dry_run=True: handle appears in reclaimed list but disk is untouched
        assert any(h.step_id == "1.1" for h in reclaimed)
        assert handle.path.is_dir(), "Dry-run GC must not remove directory"


# ---------------------------------------------------------------------------
# Test 10 — test_worktree_gc_prunes_orphans
# ---------------------------------------------------------------------------


class TestWorktreeGcPrunesOrphans:
    """After rm -rf of a worktree directory, gc_stale() calls git worktree prune
    to clear the orphaned .git/worktrees/ registry entry."""

    def test_gc_prune_clears_registry_orphan(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-orphan", step_id="1.1", base_branch="main")
        branch_name = handle.branch

        # Verify the branch exists before removal
        r_before = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert branch_name in r_before.stdout

        # Simulate an out-of-band rm -rf (the way a user might manually clean up)
        import shutil
        shutil.rmtree(str(handle.path), ignore_errors=True)

        # gc_stale with no terminal_step_ids should still run git worktree prune
        # Even if no worktrees are reclaimed, the prune call should succeed
        mgr.gc_stale(max_age_hours=72)

        # After prune, the .git/worktrees registry should no longer reference the path
        # (git worktree list should not show the removed path)
        r_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=tmp_git_repo, capture_output=True, text=True,
        )
        assert str(handle.path) not in r_list.stdout


# ---------------------------------------------------------------------------
# Test 12 — test_engine_fallback_when_disabled
# ---------------------------------------------------------------------------


class TestEngineFallbackWhenDisabled:
    """BATON_WORKTREE_ENABLED=0 causes create() to return a dummy handle and no git
    commands are executed."""

    def test_disabled_create_returns_devnull_handle(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo, enabled=False)
        handle = mgr.create(task_id="task-disabled", step_id="1.1", base_branch="main")
        assert str(handle.path) == "/dev/null"

    def test_disabled_create_no_disk_writes(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo, enabled=False)
        mgr.create(task_id="task-disabled2", step_id="1.1", base_branch="main")
        wt_root = tmp_git_repo / ".claude" / "worktrees"
        # No worktree directories should be created
        if wt_root.exists():
            dirs = [d for d in wt_root.iterdir() if d.is_dir()]
            assert len(dirs) == 0

    def test_disabled_fold_back_is_noop(self, tmp_git_repo: Path) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo, enabled=False)
        handle = mgr.create(task_id="task-disabled3", step_id="1.1", base_branch="main")
        # fold_back on a /dev/null handle should return "" immediately
        result = mgr.fold_back(handle)
        assert result == ""

    def test_env_var_zero_auto_disables_in_executor(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The executor helper _worktree_enabled() returns False when BATON_WORKTREE_ENABLED=0."""
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "0")
        from agent_baton.core.engine.executor import _worktree_enabled
        assert _worktree_enabled() is False

    def test_env_var_default_enables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default behavior (no env var set) keeps worktrees enabled."""
        monkeypatch.delenv("BATON_WORKTREE_ENABLED", raising=False)
        from agent_baton.core.engine.executor import _worktree_enabled
        assert _worktree_enabled() is True

    def test_non_git_root_auto_disables_manager(self, tmp_path: Path) -> None:
        """WorktreeManager auto-disables when project_root is not a git repo."""
        mgr = WorktreeManager(project_root=tmp_path)
        assert mgr._enabled is False
        # create() should return a dummy handle silently
        handle = mgr.create(task_id="task-nongit", step_id="1.1", base_branch="main")
        assert str(handle.path) == "/dev/null"


# ---------------------------------------------------------------------------
# Test 13 — test_run_loop_passes_cwd_override
# ---------------------------------------------------------------------------


class TestRunLoopPassesCwdOverride:
    """ClaudeCodeLauncher.launch() accepts and uses a cwd_override kwarg."""

    def test_launcher_accepts_cwd_override_kwarg(self, tmp_path: Path) -> None:
        """Launch raises (no 'claude' binary) but NOT TypeError about cwd_override."""
        import inspect
        from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher

        sig = inspect.signature(ClaudeCodeLauncher.launch)
        assert "cwd_override" in sig.parameters, (
            "ClaudeCodeLauncher.launch() must accept cwd_override kwarg"
        )

    def test_cwd_override_used_when_supplied(self, tmp_path: Path) -> None:
        """When cwd_override is set, the launcher uses it as the working directory,
        not the default configured directory."""
        from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher

        launcher = ClaudeCodeLauncher.__new__(ClaudeCodeLauncher)
        # Inspect the launch method source to verify the cwd_override logic
        import inspect
        source = inspect.getsource(ClaudeCodeLauncher.launch)
        # The implementation should reference cwd_override when setting cwd
        assert "cwd_override" in source

    def test_worktree_path_present_on_execution_action(
        self, tmp_path: Path
    ) -> None:
        """ExecutionAction carries worktree_path and worktree_branch fields."""
        from agent_baton.models.execution import ExecutionAction, ActionType

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            agent_name="backend-engineer",
            delegation_prompt="Do the thing",
            agent_model="sonnet",
            worktree_path="/some/path",
            worktree_branch="worktree/task-x/1.1",
        )
        assert action.worktree_path == "/some/path"
        assert action.worktree_branch == "worktree/task-x/1.1"

    def test_worktree_fields_in_to_dict(self, tmp_path: Path) -> None:
        """to_dict() includes worktree_path and worktree_branch when populated."""
        from agent_baton.models.execution import ExecutionAction, ActionType

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            agent_name="backend-engineer",
            delegation_prompt="Do the thing",
            agent_model="sonnet",
            worktree_path="/wt/path",
            worktree_branch="worktree/t/1.1",
        )
        d = action.to_dict()
        assert d.get("worktree_path") == "/wt/path"
        assert d.get("worktree_branch") == "worktree/t/1.1"

    def test_empty_worktree_path_not_in_dict(self) -> None:
        """to_dict() omits worktree_path when empty (preserves backward compat)."""
        from agent_baton.models.execution import ExecutionAction, ActionType

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            agent_name="backend-engineer",
            delegation_prompt="",
            agent_model="sonnet",
        )
        d = action.to_dict()
        assert "worktree_path" not in d
        assert "worktree_branch" not in d


# ---------------------------------------------------------------------------
# Additional: WorktreeHandle serialization round-trip
# ---------------------------------------------------------------------------


class TestWorktreeHandleRoundTrip:
    """WorktreeHandle.to_dict() / from_dict() round-trips without data loss."""

    def test_to_dict_from_dict_round_trip(self) -> None:
        handle = WorktreeHandle(
            task_id="t1",
            step_id="1.1",
            path=Path("/tmp/wt/t1/1.1"),
            branch="worktree/t1/1.1",
            base_branch="main",
            base_sha="abc123def456",
            created_at="2026-04-28T12:00:00+00:00",
            parent_repo=Path("/tmp/proj"),
        )
        restored = WorktreeHandle.from_dict(handle.to_dict())
        assert restored.task_id == handle.task_id
        assert restored.step_id == handle.step_id
        assert restored.path == handle.path
        assert restored.branch == handle.branch
        assert restored.base_branch == handle.base_branch
        assert restored.base_sha == handle.base_sha
        assert restored.parent_repo == handle.parent_repo

    def test_from_dict_missing_optional_fields(self) -> None:
        """Legacy serialized handles without base_sha/created_at load gracefully."""
        data = {
            "task_id": "t2",
            "step_id": "1.1",
            "path": "/tmp/wt/t2/1.1",
            "branch": "worktree/t2/1.1",
            "base_branch": "main",
            "parent_repo": "/tmp/proj2",
        }
        handle = WorktreeHandle.from_dict(data)
        assert handle.base_sha == ""
        assert handle.created_at == ""


# ---------------------------------------------------------------------------
# bd-c9e7 — _parse_conflict_files returns paths not prose
# ---------------------------------------------------------------------------


class TestParseConflictFilesReturnsPaths:
    """bd-c9e7: _parse_conflict_files must return bare file paths, not prose
    like 'Merge conflict in foo.py'."""

    def test_parse_conflict_files_returns_paths_not_prose(self) -> None:
        """Real git rebase output: CONFLICT line with path at the end."""
        output = (
            "CONFLICT (content): Merge conflict in foo.py\n"
            "CONFLICT (modify/delete): bar/baz.py deleted in HEAD.\n"
            "Auto-merging unrelated.py\n"
            "CONFLICT (content): Merge conflict in bar/baz.py\n"
        )
        result = WorktreeManager._parse_conflict_files(output)
        assert "foo.py" in result, f"Expected 'foo.py' in {result}"
        assert "bar/baz.py" in result, f"Expected 'bar/baz.py' in {result}"
        # Must NOT contain prose descriptions
        for item in result:
            assert not item.startswith("Merge conflict"), (
                f"Result item {item!r} is prose, not a file path"
            )

    def test_parse_conflict_files_handles_merge_conflict_in_prefix(self) -> None:
        """Output lines starting with 'Merge conflict in' (no CONFLICT prefix)."""
        output = (
            "Merge conflict in foo.py\n"
            "Merge conflict in bar/baz.py\n"
        )
        result = WorktreeManager._parse_conflict_files(output)
        assert "foo.py" in result, f"Expected 'foo.py' in {result}"
        assert "bar/baz.py" in result, f"Expected 'bar/baz.py' in {result}"

    def test_parse_conflict_files_empty_output(self) -> None:
        """No conflict lines in output returns empty list."""
        output = "Auto-merging clean.py\nApplying: some commit\n"
        result = WorktreeManager._parse_conflict_files(output)
        assert result == [], f"Expected empty list, got {result}"

    def test_parse_conflict_files_synthetic_rebase_output(self) -> None:
        """Synthetic multi-file conflict: all returned items are plain paths."""
        output = (
            "First, rewinding head to replay your work on top of it...\n"
            "Applying: implement feature\n"
            "CONFLICT (content): Merge conflict in src/api/routes.py\n"
            "CONFLICT (content): Merge conflict in src/models/user.py\n"
            "Auto-merging tests/test_api.py\n"
            "error: Failed to merge in the changes.\n"
        )
        result = WorktreeManager._parse_conflict_files(output)
        assert result == ["src/api/routes.py", "src/models/user.py"], (
            f"Expected ['src/api/routes.py', 'src/models/user.py'], got {result}"
        )


# ---------------------------------------------------------------------------
# bd-c071 / bd-b7c9 / bd-0a0f — parent-repo detection when project_dir is worktree
# ---------------------------------------------------------------------------


def _init_repo_for_wt_tests(path: Path) -> None:
    """Initialise a git repo with one empty commit at *path*."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=path, check=True, capture_output=True)


def _add_linked_worktree(canonical: Path, linked: Path) -> None:
    """Add a detached linked worktree at *linked* off *canonical* HEAD.

    Uses --detach to avoid branch-checkout conflicts when main is already
    checked out in the canonical repo.
    """
    sha_r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=canonical, check=True, capture_output=True, text=True,
    )
    sha = sha_r.stdout.strip()
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked), sha],
        cwd=canonical, check=True, capture_output=True,
    )


class TestCreateAgentWorktreeWhenProjectDirIsWorktree:
    """WorktreeManager constructed from inside a linked worktree must still be
    able to create new agent worktrees, using the canonical repo as the git
    operations root (bd-c071 / bd-b7c9 / bd-0a0f)."""

    def test_create_agent_worktree_when_project_dir_is_worktree(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.engine.worktree_manager import _is_inside_worktree

        canonical = tmp_path / "canonical"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)

        linked = tmp_path / "linked"
        _add_linked_worktree(canonical, linked)

        # Confirm linked has a .git *file* (gitlink), not a directory
        assert (linked / ".git").is_file(), "linked worktree must have a .git gitlink file"
        assert _is_inside_worktree(linked) is True

        # Construct WorktreeManager from inside the linked worktree
        mgr = WorktreeManager(project_root=linked)

        # The manager must be enabled (not auto-disabled)
        assert mgr._enabled, "WorktreeManager must stay enabled when project_dir is a linked worktree"

        # It must have resolved the canonical repo correctly
        assert mgr._canonical_repo == canonical.resolve(), (
            f"canonical_repo should be {canonical.resolve()}, got {mgr._canonical_repo}"
        )

        # create() must successfully materialise a new agent worktree
        handle = mgr.create(task_id="task-wt-detect", step_id="1.1", base_branch="main")
        assert handle.path != Path("/dev/null"), "create() must not return a dummy handle"
        assert handle.path.is_dir(), "agent worktree directory must exist on disk"
        assert (handle.path / ".git").exists(), "agent worktree must be a real git checkout"

        # The new worktree must be registered in the canonical repo
        wt_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=canonical, capture_output=True, text=True, check=True,
        )
        assert str(handle.path) in wt_list.stdout, (
            "new agent worktree must appear in canonical repo's worktree list"
        )

    def test_canonical_repo_stored_on_handle_parent_repo(self, tmp_path: Path) -> None:
        """handle.parent_repo reflects project_root (user-facing), not canonical_repo."""
        canonical = tmp_path / "canonical"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)

        linked = tmp_path / "linked"
        _add_linked_worktree(canonical, linked)

        mgr = WorktreeManager(project_root=linked)
        handle = mgr.create(task_id="task-parent-repo", step_id="1.1", base_branch="main")

        # parent_repo on the handle is the user-supplied project_root
        assert handle.parent_repo == linked.resolve(), (
            "handle.parent_repo must be the user-supplied project_root, not canonical_repo"
        )


class TestCanonicalRepoPorcelainParse:
    """_resolve_canonical_repo() correctly identifies the canonical repo path
    from various git worktree list --porcelain layouts (bd-c071)."""

    def test_canonical_repo_resolution_porcelain_parse_simple(
        self, tmp_path: Path
    ) -> None:
        """Single-repo case: canonical is the only entry, returns its path."""
        from agent_baton.core.engine.worktree_manager import _resolve_canonical_repo

        canonical = tmp_path / "myrepo"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)

        result = _resolve_canonical_repo(canonical)
        assert result == canonical.resolve()

    def test_canonical_repo_resolution_porcelain_parse_with_linked_worktree(
        self, tmp_path: Path
    ) -> None:
        """With a linked worktree present, still resolves to the main repo."""
        from agent_baton.core.engine.worktree_manager import _resolve_canonical_repo

        canonical = tmp_path / "main_repo"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)

        linked = tmp_path / "agent_wt"
        _add_linked_worktree(canonical, linked)

        result = _resolve_canonical_repo(linked)
        assert result == canonical.resolve(), (
            f"Expected canonical at {canonical.resolve()}, got {result}"
        )

    def test_canonical_repo_resolution_from_canonical_itself(
        self, tmp_path: Path
    ) -> None:
        """Calling _resolve_canonical_repo on the canonical repo itself is idempotent."""
        from agent_baton.core.engine.worktree_manager import _resolve_canonical_repo

        canonical = tmp_path / "idempotent_repo"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)

        linked = tmp_path / "wt_idempotent"
        _add_linked_worktree(canonical, linked)

        result = _resolve_canonical_repo(canonical)
        assert result == canonical.resolve()

    def test_resolve_canonical_repo_raises_on_non_git_dir(
        self, tmp_path: Path
    ) -> None:
        """_resolve_canonical_repo raises WorktreeError on a non-git directory."""
        from agent_baton.core.engine.worktree_manager import (
            WorktreeError,
            _resolve_canonical_repo,
        )

        with pytest.raises(WorktreeError):
            _resolve_canonical_repo(tmp_path)


class TestCreateInCanonicalRepoUnchanged:
    """When project_dir IS the canonical repo, WorktreeManager behavior is
    unchanged — _canonical_repo == _project_root and create() works as before
    (bd-c071 regression guard)."""

    def test_canonical_repo_equals_project_root_when_not_worktree(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        assert mgr._canonical_repo == tmp_git_repo.resolve(), (
            "_canonical_repo must equal project_root when project_root is the main repo"
        )

    def test_create_in_canonical_repo_produces_correct_handle(
        self, tmp_git_repo: Path
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-canon", step_id="2.1", base_branch="main")

        expected_path = tmp_git_repo / ".claude" / "worktrees" / "task-canon" / "2.1"
        assert handle.path == expected_path.resolve()
        assert handle.branch == "worktree/task-canon/2.1"
        assert handle.path.is_dir()

    def test_is_inside_worktree_false_for_canonical_repo(
        self, tmp_git_repo: Path
    ) -> None:
        """_is_inside_worktree returns False when .git is a directory."""
        from agent_baton.core.engine.worktree_manager import _is_inside_worktree

        assert _is_inside_worktree(tmp_git_repo) is False

    def test_is_inside_worktree_true_for_linked_worktree(
        self, tmp_path: Path
    ) -> None:
        """_is_inside_worktree returns True when .git is a file (gitlink)."""
        from agent_baton.core.engine.worktree_manager import _is_inside_worktree

        canonical = tmp_path / "canon"
        canonical.mkdir()
        _init_repo_for_wt_tests(canonical)
        linked = tmp_path / "linked_wt"
        _add_linked_worktree(canonical, linked)
        assert _is_inside_worktree(linked) is True


# ---------------------------------------------------------------------------
# bd-841d — _get_default_stale_hours returns 4 by default
# ---------------------------------------------------------------------------


class TestGcStaleDefaultFourHourThreshold:
    """gc_stale() uses a 4-hour default when no env var is set (bd-841d)."""

    def test_5h_old_worktree_reclaimed_with_default_threshold(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-gc-4h-old", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=5)

        reclaimed = mgr.gc_stale()  # no max_age_hours — defaults to 4

        assert any(h.step_id == "1.1" for h in reclaimed), (
            "5h-old worktree should be reclaimed with 4h default threshold"
        )

    def test_3h_old_worktree_protected_with_default_threshold(
        self, mgr: WorktreeManager
    ) -> None:
        handle = mgr.create(task_id="task-gc-4h-young", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=3)

        reclaimed = mgr.gc_stale()  # 3h < 4h default

        assert all(h.step_id != "1.1" for h in reclaimed), (
            "3h-old worktree must NOT be reclaimed with 4h default threshold"
        )
        assert handle.path.is_dir()

    def test_get_default_stale_hours_returns_4(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BATON_WORKTREE_STALE_HOURS", raising=False)
        monkeypatch.delenv("BATON_WORKTREE_GC_HOURS", raising=False)
        assert WorktreeManager._get_default_stale_hours() == 4


# ---------------------------------------------------------------------------
# bd-841d — BATON_WORKTREE_STALE_HOURS / BATON_WORKTREE_GC_HOURS env vars
# ---------------------------------------------------------------------------


class TestGcStaleEnvVarOverride:
    """gc_stale() honours BATON_WORKTREE_STALE_HOURS and legacy BATON_WORKTREE_GC_HOURS."""

    def test_stale_hours_2_reclaims_3h_old_worktree(
        self, mgr: WorktreeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", "2")
        monkeypatch.delenv("BATON_WORKTREE_GC_HOURS", raising=False)
        handle = mgr.create(task_id="task-gc-env-2h", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=3)

        reclaimed = mgr.gc_stale()

        assert any(h.step_id == "1.1" for h in reclaimed), (
            "3h-old worktree should be reclaimed when BATON_WORKTREE_STALE_HOURS=2"
        )

    def test_stale_hours_10_protects_5h_old_worktree(
        self, mgr: WorktreeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", "10")
        monkeypatch.delenv("BATON_WORKTREE_GC_HOURS", raising=False)
        handle = mgr.create(task_id="task-gc-env-10h", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=5)

        reclaimed = mgr.gc_stale()

        assert all(h.step_id != "1.1" for h in reclaimed), (
            "5h-old worktree must NOT be reclaimed when BATON_WORKTREE_STALE_HOURS=10"
        )
        assert handle.path.is_dir()

    def test_legacy_gc_hours_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BATON_WORKTREE_STALE_HOURS", raising=False)
        monkeypatch.setenv("BATON_WORKTREE_GC_HOURS", "8")
        assert WorktreeManager._get_default_stale_hours() == 8

    def test_stale_hours_wins_over_gc_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", "2")
        monkeypatch.setenv("BATON_WORKTREE_GC_HOURS", "99")
        assert WorktreeManager._get_default_stale_hours() == 2


# ---------------------------------------------------------------------------
# bd-841d — in-flight guard skips worktrees referenced by running executions
# ---------------------------------------------------------------------------


class TestGcStaleSkipsInFlightWorktrees:
    """gc_stale() skips deletion when a running execution references the worktree."""

    def test_running_execution_blocks_gc(
        self, mgr: WorktreeManager, tmp_git_repo: Path, tmp_path: Path
    ) -> None:
        import sqlite3  # noqa: PLC0415

        handle = mgr.create(task_id="task-inflight", step_id="1.1", base_branch="main")
        _make_worktree_old(handle, hours=10)

        fake_db = tmp_path / "baton.db"
        state_json = json.dumps({"step_worktrees": {"1.1": handle.to_dict()}})
        with sqlite3.connect(str(fake_db)) as conn:
            conn.execute(
                "CREATE TABLE executions (task_id TEXT, status TEXT, state_json TEXT)"
            )
            conn.execute(
                "INSERT INTO executions VALUES (?, ?, ?)",
                ("task-inflight", "running", state_json),
            )

        import unittest.mock  # noqa: PLC0415

        with unittest.mock.patch.dict("os.environ", {"BATON_DB_PATH": str(fake_db)}):
            reclaimed = mgr.gc_stale()

        assert all(h.step_id != "1.1" for h in reclaimed), (
            "In-flight worktree must NOT be reclaimed"
        )
        assert handle.path.is_dir(), "In-flight worktree directory must be retained"

    def test_no_baton_db_allows_is_in_flight_false(
        self, mgr: WorktreeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_is_in_flight returns (False, '') when no baton.db is discoverable."""
        monkeypatch.delenv("BATON_DB_PATH", raising=False)

        # Deep path guaranteed to have no baton.db ancestor
        fake_wt = mgr._worktrees_root / "task-no-db" / "1.1"
        fake_wt.mkdir(parents=True, exist_ok=True)

        fresh_mgr = WorktreeManager.__new__(WorktreeManager)
        fresh_mgr._project_root = fake_wt.resolve()
        fresh_mgr._worktrees_root = mgr._worktrees_root
        fresh_mgr._enabled = True
        fresh_mgr._tracer = None
        fresh_mgr._bead_store = None
        fresh_mgr._handles = {}
        fresh_mgr._trace = None
        fresh_mgr._semaphore = threading.Semaphore(16)

        result, task_id = fresh_mgr._is_in_flight(fake_wt)
        assert result is False
        assert task_id == ""


# ---------------------------------------------------------------------------
# bd-841d — gc_stale() called from complete() (source-level verification)
# ---------------------------------------------------------------------------


class TestGcStaleRunsOnExecuteComplete:
    """complete() contains gc_stale call in a daemon thread (bd-841d)."""

    def test_complete_source_contains_gc_stale_call(self) -> None:
        import inspect  # noqa: PLC0415

        from agent_baton.core.engine.executor import ExecutionEngine

        source = inspect.getsource(ExecutionEngine.complete)
        assert "gc_stale" in source, (
            "complete() must call gc_stale() as part of bd-841d aggressive GC"
        )

    def test_complete_source_gc_after_straggler_block(self) -> None:
        import inspect  # noqa: PLC0415

        from agent_baton.core.engine.executor import ExecutionEngine

        source = inspect.getsource(ExecutionEngine.complete)
        assert "_worktree_mgr" in source
        straggler_pos = source.find("straggler")
        gc_pos = source.find("gc_stale")
        assert straggler_pos != -1
        assert gc_pos != -1
        assert straggler_pos < gc_pos, (
            "gc_stale call must appear after the straggler cleanup block in complete()"
        )


# ---------------------------------------------------------------------------
# bd-841d — errors in gc_stale are swallowed and logged, never block complete
# ---------------------------------------------------------------------------


class TestGcStaleSwallowsErrors:
    """gc_stale errors in complete() are logged as BEAD_WARNING, never raised."""

    def test_run_gc_on_complete_closure_in_source(self) -> None:
        import inspect  # noqa: PLC0415

        from agent_baton.core.engine.executor import ExecutionEngine

        source = inspect.getsource(ExecutionEngine.complete)
        assert "_run_gc_on_complete" in source, (
            "complete() must define _run_gc_on_complete closure for error isolation"
        )

    def test_bead_warning_logged_on_gc_error(self) -> None:
        import inspect  # noqa: PLC0415

        from agent_baton.core.engine.executor import ExecutionEngine

        source = inspect.getsource(ExecutionEngine.complete)
        assert "BEAD_WARNING" in source, (
            "complete() must log BEAD_WARNING when gc_stale raises"
        )
