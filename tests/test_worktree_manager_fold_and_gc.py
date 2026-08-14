"""Behaviour tests for WorktreeManager fold-back and stale-GC safety.

Companion to ``tests/test_worktree_manager.py``.  Everything here drives the
real ``WorktreeManager`` against a real git repository and a real baton.db
built from ``PROJECT_SCHEMA_DDL`` — no mocks, no source-string assertions.

Covers two defects:

F046  ``fold_back()`` must actually integrate the agent's commits into the
      parent branch under the DEFAULT ("rebase") strategy, and under "merge",
      leaving the canonical repo's working tree clean.  A genuine content
      conflict must surface as ``WorktreeFoldError`` carrying the conflicting
      paths in ``conflict_files``.

F049  ``gc_stale()`` must not destroy a worktree that a live execution still
      references, as recorded in the *production* schema (``executions`` +
      ``step_worktrees``), and must fail safe (retain) when the state database
      cannot be queried.  Genuinely finished worktrees must still be reclaimed.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.engine.worktree_manager import (
    WorktreeFoldError,
    WorktreeHandle,
    WorktreeManager,
)
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A minimal real git repository with one initial commit on ``main``."""
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
def mgr(tmp_git_repo: Path) -> WorktreeManager:
    return WorktreeManager(project_root=tmp_git_repo)


def _commit_file(repo_path: Path, filename: str, content: str) -> str:
    """Write + stage + commit *filename*; return the new HEAD SHA."""
    (repo_path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"],
                   cwd=repo_path, check=True, capture_output=True)
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path,
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _backdate_manifest(handle: WorktreeHandle, hours: float) -> None:
    """Rewrite ``.baton-worktree.json`` so the worktree looks *hours* old."""
    backdated = (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    data = handle.to_dict()
    data["created_at"] = backdated
    (handle.path / ".baton-worktree.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _build_baton_db(
    db_path: Path,
    *,
    task_id: str,
    status: str,
    handle: WorktreeHandle,
) -> None:
    """Create a baton.db with the REAL production schema and one execution.

    The execution owns exactly one ``step_worktrees`` row pointing at
    ``handle.path`` — this is how worktree ownership is actually persisted.
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(PROJECT_SCHEMA_DDL)
        conn.execute(
            "INSERT INTO executions (task_id, status, started_at) VALUES (?, ?, ?)",
            (task_id, status, datetime.now(tz=timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT INTO step_worktrees "
            "(task_id, step_id, worktree_path, branch, base_branch, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                handle.step_id,
                str(handle.path),
                handle.branch,
                handle.base_branch,
                handle.created_at,
            ),
        )


# ---------------------------------------------------------------------------
# F046 — fold_back() must integrate the agent's work
# ---------------------------------------------------------------------------


class TestFoldBackIntegratesAgentCommits:
    """The default fold strategy must land the agent's commit on the parent branch."""

    def test_default_strategy_lands_agent_file_on_parent_branch(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold-default", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")

        # Parent branch advances independently with a NON-conflicting commit.
        _commit_file(tmp_git_repo, "parent.txt", "parent output\n")

        # Default strategy (rebase) — must not raise.
        mgr.fold_back(handle, commit_hash=agent_sha)

        shown = _git(["show", "main:agent.txt"], cwd=tmp_git_repo)
        assert shown.returncode == 0, (
            "agent.txt must be reachable from main after fold_back; "
            f"git show said: {shown.stderr.strip()}"
        )
        assert shown.stdout == "agent output\n"

    def test_default_strategy_preserves_parent_branch_commit(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold-preserve", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")
        _commit_file(tmp_git_repo, "parent.txt", "parent output\n")

        mgr.fold_back(handle, commit_hash=agent_sha)

        shown = _git(["show", "main:parent.txt"], cwd=tmp_git_repo)
        assert shown.returncode == 0, (
            "the parent branch's own commit must survive the fold; "
            f"git show said: {shown.stderr.strip()}"
        )

    def test_default_strategy_leaves_canonical_working_tree_clean(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold-clean", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")
        _commit_file(tmp_git_repo, "parent.txt", "parent output\n")

        mgr.fold_back(handle, commit_hash=agent_sha)

        status = _git(["status", "--porcelain"], cwd=tmp_git_repo)
        assert status.returncode == 0
        assert status.stdout.strip() == "", (
            "canonical repo working tree must match the folded branch, "
            f"but git status reported:\n{status.stdout}"
        )
        assert (tmp_git_repo / "agent.txt").is_file(), (
            "the agent's file must be present in the canonical working tree "
            "after fold_back"
        )

    def test_merge_strategy_lands_agent_file_and_leaves_tree_clean(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-fold-merge", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")
        _commit_file(tmp_git_repo, "parent.txt", "parent output\n")

        mgr.fold_back(handle, commit_hash=agent_sha, strategy="merge")

        shown = _git(["show", "main:agent.txt"], cwd=tmp_git_repo)
        assert shown.returncode == 0, (
            "merge fold must land agent.txt on main; "
            f"git show said: {shown.stderr.strip()}"
        )
        status = _git(["status", "--porcelain"], cwd=tmp_git_repo)
        assert status.stdout.strip() == "", (
            f"canonical working tree must be clean after a merge fold, got:\n{status.stdout}"
        )


class TestFoldBackGenuineConflict:
    """A real content conflict must be reported as a conflict, with the paths."""

    def test_conflict_error_names_the_conflicting_file(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-real-conflict", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "same.txt", "agent version\n")
        _commit_file(tmp_git_repo, "same.txt", "parent version\n")

        with pytest.raises(WorktreeFoldError) as excinfo:
            mgr.fold_back(handle, commit_hash=agent_sha)

        assert excinfo.value.conflict_files, (
            "WorktreeFoldError raised by a genuine content conflict must carry "
            "the conflicting paths in conflict_files (an empty list means the "
            f"fold failed for some other reason: {excinfo.value})"
        )
        assert any("same.txt" in f for f in excinfo.value.conflict_files), (
            f"expected same.txt among conflict_files, got {excinfo.value.conflict_files}"
        )

    def test_worktree_retained_after_genuine_conflict(
        self, mgr: WorktreeManager, tmp_git_repo: Path
    ) -> None:
        handle = mgr.create(task_id="task-real-conflict2", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "same.txt", "agent version\n")
        _commit_file(tmp_git_repo, "same.txt", "parent version\n")

        with pytest.raises(WorktreeFoldError):
            mgr.fold_back(handle, commit_hash=agent_sha)

        assert handle.path.is_dir(), "worktree must be retained after a fold conflict"


# ---------------------------------------------------------------------------
# F049 — gc_stale() in-flight guard against the REAL schema
# ---------------------------------------------------------------------------


class TestGcStaleInFlightGuardRealSchema:
    """The in-flight guard must consult the production schema, not a fiction."""

    def test_running_execution_protects_its_worktree(
        self, mgr: WorktreeManager, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handle = mgr.create(task_id="task-live", step_id="1.1", base_branch="main")
        _commit_file(handle.path, "wip.txt", "unmerged agent work\n")
        _backdate_manifest(handle, hours=10)

        db_path = tmp_git_repo / "state.db"
        _build_baton_db(db_path, task_id="task-live", status="running", handle=handle)
        monkeypatch.setenv("BATON_DB_PATH", str(db_path))

        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert reclaimed == [], (
            "gc_stale must not reclaim a worktree owned by a running execution; "
            f"reclaimed={[h.step_id for h in reclaimed]}"
        )
        assert handle.path.is_dir(), (
            "the worktree of a running execution must survive gc_stale"
        )

    def test_paused_takeover_execution_protects_its_worktree(
        self, mgr: WorktreeManager, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A worktree retained for developer takeover must not be force-deleted."""
        handle = mgr.create(task_id="task-takeover", step_id="1.1", base_branch="main")
        _commit_file(handle.path, "wip.txt", "developer takeover work\n")
        _backdate_manifest(handle, hours=10)

        db_path = tmp_git_repo / "state.db"
        _build_baton_db(db_path, task_id="task-takeover",
                        status="paused-takeover", handle=handle)
        monkeypatch.setenv("BATON_DB_PATH", str(db_path))

        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert reclaimed == [], (
            "gc_stale must not reclaim a worktree held open for takeover; "
            f"reclaimed={[h.step_id for h in reclaimed]}"
        )
        assert handle.path.is_dir()

    def test_unreadable_state_db_fails_safe_and_retains_worktree(
        self, mgr: WorktreeManager, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the guard cannot be evaluated, deletion must be refused, not authorised."""
        handle = mgr.create(task_id="task-baddb", step_id="1.1", base_branch="main")
        _backdate_manifest(handle, hours=10)

        db_path = tmp_git_repo / "state.db"
        db_path.write_bytes(b"this is not a sqlite database at all")
        monkeypatch.setenv("BATON_DB_PATH", str(db_path))

        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert reclaimed == [], (
            "an unreadable state DB must block reclaim (fail safe), not permit it; "
            f"reclaimed={[h.step_id for h in reclaimed]}"
        )
        assert handle.path.is_dir(), (
            "worktree must survive when the in-flight guard cannot be evaluated"
        )

    def test_finished_execution_worktree_is_still_reclaimed(
        self, mgr: WorktreeManager, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: the guard must not become a blanket refusal to ever GC."""
        handle = mgr.create(task_id="task-done", step_id="1.1", base_branch="main")
        _backdate_manifest(handle, hours=10)

        db_path = tmp_git_repo / "state.db"
        _build_baton_db(db_path, task_id="task-done", status="completed", handle=handle)
        monkeypatch.setenv("BATON_DB_PATH", str(db_path))

        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert [h.step_id for h in reclaimed] == ["1.1"], (
            "a stale worktree belonging to a finished execution must still be reclaimed"
        )
        assert not handle.path.is_dir()
