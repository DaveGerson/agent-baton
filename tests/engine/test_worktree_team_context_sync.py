"""Regression tests: team-context deliverable hand-off across worktree isolation.

Defect (BLOCKER): the ``adversarial-tdd`` preset briefs stage 1 to write
``.claude/team-context/executions/<task_id>/spec.md`` — a *relative* path.
Under default worktree isolation the agent's cwd is the worktree, so the
spec landed inside the worktree; ``.claude/team-context/`` is gitignored by
design, so fold-back (which folds commits only) never carried it back, and
worktree cleanup then deleted it.  Every downstream stage received a
``context_file`` that had never existed — silently.

These tests exercise the real ``WorktreeManager`` against a real git repo:
create → write a deliverable inside the worktree → fold/cleanup → assert the
parent project has the file.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from agent_baton.core.engine.worktree_manager import WorktreeManager


TASK_ID = "2026-08-13-spec-handoff"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """A real git repo that gitignores .claude/team-context (as baton does)."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".claude/team-context/\n.claude/worktrees/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


@pytest.fixture
def mgr(git_project: Path, monkeypatch: pytest.MonkeyPatch) -> WorktreeManager:
    monkeypatch.delenv("BATON_TEAM_CONTEXT_ROOT", raising=False)
    return WorktreeManager(project_root=git_project)


def _parent_exec_dir(project: Path) -> Path:
    return project / ".claude" / "team-context" / "executions" / TASK_ID


def _worktree_exec_dir(worktree: Path) -> Path:
    return worktree / ".claude" / "team-context" / "executions" / TASK_ID


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── Harvest (the blocker) ───────────────────────────────────────────────────


def test_spec_written_in_worktree_survives_cleanup(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """create → agent writes spec.md inside the worktree → cleanup → parent has it."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")

    # The agent follows its briefing: a *relative* path from its cwd (worktree).
    spec_in_worktree = _worktree_exec_dir(handle.path) / "spec.md"
    _write(spec_in_worktree, "# Spec\n\nBehaviour A must hold.\n")

    # Nothing is committed (team-context is gitignored) — the old code path.
    mgr.cleanup(handle, on_failure=False)

    parent_spec = _parent_exec_dir(git_project) / "spec.md"
    assert parent_spec.is_file(), (
        "spec.md written inside the worktree was lost on cleanup — downstream "
        "stages would reference a context_file that never exists"
    )
    assert "Behaviour A must hold." in parent_spec.read_text(encoding="utf-8")
    # And the worktree really is gone (we did not merely skip cleanup).
    assert not handle.path.exists()


def test_harvest_preserves_nested_deliverables(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """Sub-directories under the per-task dir are harvested too."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "spec")
    _write(_worktree_exec_dir(handle.path) / "design" / "architecture.md", "arch")

    mgr.cleanup(handle, on_failure=False)

    parent = _parent_exec_dir(git_project)
    assert (parent / "spec.md").read_text(encoding="utf-8") == "spec"
    assert (parent / "design" / "architecture.md").read_text(encoding="utf-8") == "arch"


def test_fold_back_harvests_before_cleanup(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """fold_back() rescues the deliverable even though it is never committed."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "# Spec via fold\n")

    # A normal step also commits tracked work; the spec is *not* in that commit
    # (team-context is gitignored), so only the harvest can save it.
    subprocess.run(["git", "switch", handle.branch], cwd=handle.path, check=True,
                   capture_output=True)
    (handle.path / "src.py").write_text("print('x')\n")
    subprocess.run(["git", "add", "src.py"], cwd=handle.path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "step 1.1"], cwd=handle.path, check=True,
                   capture_output=True)

    mgr.fold_back(handle, strategy="none")

    parent_spec = _parent_exec_dir(git_project) / "spec.md"
    assert parent_spec.is_file()
    assert parent_spec.read_text(encoding="utf-8") == "# Spec via fold\n"


def test_harvest_runs_on_failure_retain(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """A step that fails *after* writing a deliverable still hands it back."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "partial spec")

    mgr.cleanup(handle, on_failure=True)  # retain-for-forensics path

    assert (_parent_exec_dir(git_project) / "spec.md").read_text(encoding="utf-8") == (
        "partial spec"
    )
    assert handle.path.exists(), "failed worktrees must still be retained"


def test_harvest_on_gc_reclaim(mgr: WorktreeManager, git_project: Path) -> None:
    """gc_stale() reclaims a worktree — deliverables are rescued first."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "gc spec")

    mgr.gc_stale(max_age_hours=0, terminal_step_ids={"1.1"})

    assert not handle.path.exists()
    assert (_parent_exec_dir(git_project) / "spec.md").read_text(encoding="utf-8") == (
        "gc spec"
    )


def test_harvest_conflict_keeps_newer_parent_file(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """Newest-wins: a parent file modified after the worktree copy is kept."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "worktree version")
    time.sleep(0.01)
    _write(_parent_exec_dir(git_project) / "spec.md", "parent version (newer)")

    mgr.cleanup(handle, on_failure=False)

    assert (_parent_exec_dir(git_project) / "spec.md").read_text(encoding="utf-8") == (
        "parent version (newer)"
    )


# ── Seeding (the other half of the hand-off) ────────────────────────────────


def test_create_seeds_parent_deliverables_into_worktree(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """A downstream step's worktree can read the spec at its briefed path."""
    _write(_parent_exec_dir(git_project) / "spec.md", "# Spec from stage 1\n")

    handle = mgr.create(task_id=TASK_ID, step_id="2.1", base_branch="main")

    seeded = _worktree_exec_dir(handle.path) / "spec.md"
    assert seeded.is_file(), (
        "downstream worktree has no .claude/team-context — its context_file "
        "reference would dangle"
    )
    assert seeded.read_text(encoding="utf-8") == "# Spec from stage 1\n"
    # And the briefing's *relative* path resolves from the worktree cwd.
    rel = Path(".claude/team-context/executions") / TASK_ID / "spec.md"
    assert (handle.path / rel).is_file()


def test_seed_does_not_touch_other_tasks(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """Only the step's own task directory is seeded."""
    _write(_parent_exec_dir(git_project) / "spec.md", "mine")
    other = (
        git_project / ".claude" / "team-context" / "executions" / "other-task" / "spec.md"
    )
    _write(other, "not mine")

    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")

    wt_root = handle.path / ".claude" / "team-context" / "executions"
    assert (wt_root / TASK_ID / "spec.md").is_file()
    assert not (wt_root / "other-task").exists()


def test_engine_owned_bookkeeping_is_not_seeded(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """Live engine state must not be duplicated into a worktree.

    A nested ``baton`` invocation inside the worktree discovers the closest
    ``.claude/team-context`` by walking up from its cwd — a seeded
    ``execution-state.json`` would hand it a stale state to write to.
    """
    parent = _parent_exec_dir(git_project)
    _write(parent / "events" / "log.jsonl", "{}\n")
    _write(parent / "execution-state.json", '{"status": "running"}')
    _write(parent / "plan.json", "{}")
    _write(parent / "spec.md", "spec")

    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")

    wt = _worktree_exec_dir(handle.path)
    assert (wt / "spec.md").is_file()
    assert not (wt / "events").exists()
    assert not (wt / "execution-state.json").exists()
    assert not (wt / "plan.json").exists()


def test_engine_owned_bookkeeping_is_not_harvested(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """A stale state file inside a worktree can never overwrite the parent's."""
    _write(_parent_exec_dir(git_project) / "execution-state.json", "PARENT-LIVE")
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    time.sleep(0.01)
    _write(_worktree_exec_dir(handle.path) / "execution-state.json", "WORKTREE-STALE")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "spec")

    mgr.cleanup(handle, on_failure=False)

    parent = _parent_exec_dir(git_project)
    assert parent.joinpath("execution-state.json").read_text(encoding="utf-8") == (
        "PARENT-LIVE"
    )
    assert parent.joinpath("spec.md").read_text(encoding="utf-8") == "spec"


def test_round_trip_seed_then_harvest_is_idempotent(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """An untouched seeded file is not rewritten back over the parent."""
    parent_spec = _parent_exec_dir(git_project) / "spec.md"
    _write(parent_spec, "original")
    original_mtime = parent_spec.stat().st_mtime

    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    copied = mgr.harvest_team_context(handle)

    assert copied == []
    assert parent_spec.stat().st_mtime == original_mtime
    assert parent_spec.read_text(encoding="utf-8") == "original"


def test_agent_edit_of_seeded_file_wins_on_harvest(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """A seeded file the agent then edits is copied back."""
    _write(_parent_exec_dir(git_project) / "spec.md", "original")
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")

    time.sleep(0.01)
    (_worktree_exec_dir(handle.path) / "spec.md").write_text("revised by agent")

    mgr.cleanup(handle, on_failure=False)

    assert (_parent_exec_dir(git_project) / "spec.md").read_text(encoding="utf-8") == (
        "revised by agent"
    )


# ── Configuration / robustness ──────────────────────────────────────────────


def test_team_context_root_env_override(
    git_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BATON_TEAM_CONTEXT_ROOT relocates the *parent* side of the hand-off."""
    alt_root = tmp_path / "elsewhere" / "team-context"
    monkeypatch.setenv("BATON_TEAM_CONTEXT_ROOT", str(alt_root))
    mgr = WorktreeManager(project_root=git_project)

    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    _write(_worktree_exec_dir(handle.path) / "spec.md", "relocated")
    mgr.cleanup(handle, on_failure=False)

    assert (alt_root / "executions" / TASK_ID / "spec.md").read_text(
        encoding="utf-8"
    ) == "relocated"


def test_sync_is_noop_when_manager_disabled(git_project: Path) -> None:
    """Disabled manager returns the /dev/null dummy handle — no crash, no copy."""
    mgr = WorktreeManager(project_root=git_project, enabled=False)
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")

    assert mgr.seed_team_context(handle) == []
    assert mgr.harvest_team_context(handle) == []


def test_harvest_missing_worktree_dir_is_silent(
    mgr: WorktreeManager, git_project: Path
) -> None:
    """No team-context written by the step → harvest is a clean no-op."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    assert mgr.harvest_team_context(handle) == []
    mgr.cleanup(handle, on_failure=False)
    assert not _parent_exec_dir(git_project).exists() or not list(
        _parent_exec_dir(git_project).iterdir()
    )


def test_symlinks_are_not_followed(mgr: WorktreeManager, git_project: Path) -> None:
    """A symlink inside the worktree cannot smuggle files into the parent."""
    handle = mgr.create(task_id=TASK_ID, step_id="1.1", base_branch="main")
    secret = git_project / "secret.txt"
    secret.write_text("do not copy me")
    exec_dir = _worktree_exec_dir(handle.path)
    exec_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(secret, exec_dir / "linked.txt")
    _write(exec_dir / "spec.md", "spec")

    mgr.cleanup(handle, on_failure=False)

    assert (_parent_exec_dir(git_project) / "spec.md").is_file()
    assert not (_parent_exec_dir(git_project) / "linked.txt").exists()
