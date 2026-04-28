"""Worktree manager -- create and lifecycle-manage one git worktree per parallel PlanStep.

Wave 1.3 (bd-86bf): Automated Git Worktree Isolation.

Each dispatched non-automation step receives an isolated git worktree at:
  <project_root>/.claude/worktrees/<task_id>/<step_id>/

The worktree is created at ``mark_dispatched`` time, used as the subprocess
``cwd`` for the launched Claude Code agent, and folded back into the parent
branch on successful completion.  Failed worktrees are retained on disk for
Wave 5.1 takeover and reclaimed by ``gc_stale()`` after 72h.

Configuration (env vars until baton.yaml Wave 1.2 lands):
    BATON_WORKTREE_ENABLED   ``1`` (default) / ``0`` to disable entirely.
    BATON_WORKTREE_GC_HOURS  default ``72``; max age for GC reclaim.
    BATON_WORKTREE_ROOT      default ``.claude/worktrees`` relative to project root.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from agent_baton.core.observe.trace import TraceRecorder
    from agent_baton.models.trace import TaskTrace

__all__ = [
    "WorktreeManager",
    "WorktreeHandle",
    "WorktreeError",
    "WorktreeCreateError",
    "WorktreeCleanupError",
    "WorktreeFoldError",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class WorktreeError(Exception):
    """Base class for all worktree errors."""


class WorktreeCreateError(WorktreeError):
    """Raised when a worktree cannot be created."""


class WorktreeCleanupError(WorktreeError):
    """Raised when a worktree cannot be removed (force=True path only)."""


class WorktreeFoldError(WorktreeError):
    """Raised when fold-back fails (merge conflict or non-fast-forward).

    The worktree is LEFT INTACT for forensic inspection / Wave 5.1 takeover.
    """

    def __init__(self, message: str, conflict_files: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflict_files: list[str] = conflict_files or []


# ---------------------------------------------------------------------------
# Handle dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeHandle:
    """Immutable record of a created worktree."""

    task_id: str
    step_id: str
    path: Path                  # absolute, resolved
    branch: str                 # e.g. "worktree/2026-04-28-foo/1.1"
    base_branch: str            # parent branch HEAD captured at create time
    base_sha: str               # SHA of base_branch at create time
    created_at: str             # ISO 8601
    parent_repo: Path           # absolute path to parent repo's project root

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for JSON state + .baton-worktree.json)."""
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "path": str(self.path),
            "branch": self.branch,
            "base_branch": self.base_branch,
            "base_sha": self.base_sha,
            "created_at": self.created_at,
            "parent_repo": str(self.parent_repo),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorktreeHandle:
        return cls(
            task_id=data["task_id"],
            step_id=data["step_id"],
            path=Path(data["path"]),
            branch=data["branch"],
            base_branch=data["base_branch"],
            base_sha=data.get("base_sha", ""),
            created_at=data.get("created_at", ""),
            parent_repo=Path(data["parent_repo"]),
        )

    def takeover_command(self) -> str:
        """Convenience string for Wave 5.1 takeover UI."""
        return f"cd {self.path} && git status"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _run_git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command, returning CompletedProcess.

    Raises:
        WorktreeCreateError: if the process fails and check=True.
    """
    cmd = ["git", *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    if check and result.returncode != 0:
        raise WorktreeCreateError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _safe_branch_name(task_id: str, step_id: str) -> str:
    """Return the worktree branch name: ``worktree/<task_id>/<step_id>``."""
    return f"worktree/{task_id}/{step_id}"


def _safe_worktree_name(task_id: str, step_id: str) -> str:
    """Return a git-safe worktree registry name (used in .git/worktrees/).

    Dots are replaced with dashes; slashes with underscores.
    """
    safe_task = task_id.replace("/", "_").replace(".", "-")
    safe_step = step_id.replace(".", "-").replace("/", "_")
    return f"{safe_task}_{safe_step}"


def _lock_path_for(worktrees_root: Path, task_id: str) -> Path:
    """Return the flock file path for this task."""
    return worktrees_root / task_id / ".lock"


class _FileLock:
    """POSIX flock-based per-task lock with timeout."""

    def __init__(self, path: Path, timeout_seconds: int = 30) -> None:
        self._path = path
        self._timeout = timeout_seconds
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        fd = os.open(str(self._path), os.O_RDWR)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise WorktreeCreateError(
                        f"Timed out acquiring worktree lock at {self._path} after {self._timeout}s"
                    )
                time.sleep(0.25)

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            finally:
                self._fd = None

    def __enter__(self) -> _FileLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------


class WorktreeManager:
    """Create and lifecycle-manage isolated git worktrees for PlanStep dispatch.

    One instance is constructed per ``ExecutionEngine``.  When disabled
    (``enabled=False`` or ``BATON_WORKTREE_ENABLED=0``), all methods are
    no-ops that preserve backward compatibility.

    Args:
        project_root: The parent repository root (where ``.git`` lives).
        worktrees_root: Where worktree directories are created.
            Defaults to ``project_root / ".claude/worktrees"``.
        enabled: Global kill switch.  When ``False``, ``create()`` returns
            a dummy handle and no git commands are run.
        trace_recorder: Optional ``TraceRecorder`` for observability events.
        bead_store: Optional ``BeadStore`` for filing warning beads.
    """

    def __init__(
        self,
        project_root: Path,
        worktrees_root: Path | None = None,
        enabled: bool = True,
        trace_recorder: object | None = None,  # TraceRecorder | None
        bead_store: object | None = None,       # BeadStore | None
    ) -> None:
        self._project_root = project_root.resolve()
        self._worktrees_root = (
            worktrees_root or self._project_root / ".claude" / "worktrees"
        ).resolve()
        self._enabled = enabled
        self._tracer = trace_recorder  # may be None
        self._bead_store = bead_store   # may be None
        # In-memory index: (task_id, step_id) -> WorktreeHandle
        self._handles: dict[tuple[str, str], WorktreeHandle] = {}
        # Active trace reference (set by engine before calls)
        self._trace: object | None = None  # TaskTrace | None

        # Auto-disable when project_root is not a git repository.
        # This protects test suites that use temp directories as roots.
        if self._enabled:
            check = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                cwd=str(self._project_root),
            )
            if check.returncode != 0:
                _log.debug(
                    "WorktreeManager: project_root %s is not a git repo — disabled",
                    self._project_root,
                )
                self._enabled = False

    # ── Trace helpers ────────────────────────────────────────────────────────

    def _emit(self, event_type: str, details: dict, duration_ms: int = 0) -> None:
        """Emit a trace event if a tracer + active trace are available."""
        if self._tracer is None or self._trace is None:
            return
        try:
            self._tracer.record_event(
                self._trace,
                event_type,
                agent_name=None,
                phase=0,
                step=0,
                details={**details, "duration_ms": duration_ms},
            )
        except Exception as exc:
            _log.debug("WorktreeManager: trace emit failed (non-fatal): %s", exc)

    def _file_bead_warning(self, task_id: str, step_id: str, content: str) -> None:
        """File a BEAD_WARNING via bead_store (best-effort, non-fatal)."""
        if self._bead_store is None:
            return
        try:
            from agent_baton.models.bead import Bead, _generate_bead_id
            ts = _utcnow()
            existing_count = 0
            try:
                existing_count = len(self._bead_store.query(task_id=task_id, limit=10000))
            except Exception:
                pass
            bead_id = _generate_bead_id(task_id, step_id, content, ts, existing_count)
            bead = Bead(
                bead_id=bead_id,
                task_id=task_id,
                step_id=step_id,
                agent_name="worktree-manager",
                bead_type="warning",
                content=content,
                confidence="high",
                scope="step",
                created_at=ts,
                source="agent-signal",
            )
            self._bead_store.write(bead)
        except Exception as exc:
            _log.debug("WorktreeManager: bead warning failed (non-fatal): %s", exc)

    # ── Primary lifecycle ────────────────────────────────────────────────────

    def create(
        self,
        task_id: str,
        step_id: str,
        base_branch: str,
        *,
        base_sha: str | None = None,
    ) -> WorktreeHandle:
        """Materialize an isolated worktree.

        Idempotent: if a worktree at the canonical path already exists AND
        its branch matches the expected name AND HEAD == base_sha, the
        existing handle is returned.

        Raises:
            WorktreeCreateError: on lock contention, dirty path, base ref
                missing, or disk failure.
        """
        if not self._enabled:
            return WorktreeHandle(
                task_id=task_id,
                step_id=step_id,
                path=Path("/dev/null"),
                branch="",
                base_branch=base_branch,
                base_sha="",
                created_at=_utcnow(),
                parent_repo=self._project_root,
            )

        branch_name = _safe_branch_name(task_id, step_id)
        worktree_path = self._worktrees_root / task_id / step_id
        lock_path = _lock_path_for(self._worktrees_root, task_id)

        t_start = time.monotonic()

        with _FileLock(lock_path):
            # ── Idempotency check ────────────────────────────────────────────
            cached = self._handles.get((task_id, step_id))
            if cached is not None:
                return cached

            manifest = worktree_path / ".baton-worktree.json"
            if manifest.exists():
                try:
                    handle = WorktreeHandle.from_dict(json.loads(manifest.read_text("utf-8")))
                    self._handles[(task_id, step_id)] = handle
                    _log.info(
                        "WorktreeManager.create: reusing existing worktree at %s (step=%s)",
                        worktree_path, step_id,
                    )
                    return handle
                except Exception as exc:
                    _log.warning(
                        "WorktreeManager.create: cannot parse existing manifest at %s: %s — recreating",
                        manifest, exc,
                    )

            # ── Resolve base SHA ─────────────────────────────────────────────
            if base_sha is None:
                r = _run_git(["rev-parse", "HEAD"], cwd=self._project_root)
                base_sha = r.stdout.strip()

            # ── Create worktree directory parent ────────────────────────────
            worktree_path.parent.mkdir(parents=True, exist_ok=True)

            # ── git worktree add --detach ────────────────────────────────────
            _log.info(
                "WorktreeManager.create: adding worktree path=%s branch=%s sha=%s",
                worktree_path, branch_name, base_sha[:8],
            )
            try:
                _run_git(
                    ["worktree", "add", "--detach", str(worktree_path), base_sha],
                    cwd=self._project_root,
                )
            except WorktreeCreateError as exc:
                raise WorktreeCreateError(
                    f"git worktree add failed for step={step_id}: {exc}"
                ) from exc

            # ── Create the worktree branch ───────────────────────────────────
            try:
                _run_git(
                    ["switch", "-c", branch_name],
                    cwd=worktree_path,
                )
            except WorktreeCreateError as exc:
                # Best-effort cleanup of the worktree we just added
                try:
                    _run_git(
                        ["worktree", "remove", "--force", str(worktree_path)],
                        cwd=self._project_root,
                        check=False,
                    )
                except Exception:
                    pass
                raise WorktreeCreateError(
                    f"git switch -c failed for step={step_id}: {exc}"
                ) from exc

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            handle = WorktreeHandle(
                task_id=task_id,
                step_id=step_id,
                path=worktree_path.resolve(),
                branch=branch_name,
                base_branch=base_branch,
                base_sha=base_sha,
                created_at=_utcnow(),
                parent_repo=self._project_root,
            )

            # ── Persist manifest ─────────────────────────────────────────────
            manifest.write_text(
                json.dumps(handle.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self._handles[(task_id, step_id)] = handle

            _log.info(
                "WorktreeManager.create: done task=%s step=%s path=%s elapsed_ms=%d",
                task_id, step_id, worktree_path, elapsed_ms,
            )

            self._emit("worktree_create", {
                "task_id": task_id,
                "step_id": step_id,
                "path": str(handle.path),
                "branch": branch_name,
                "base_branch": base_branch,
                "base_sha": base_sha,
            }, duration_ms=elapsed_ms)

            return handle

    def fold_back(
        self,
        handle: WorktreeHandle,
        *,
        commit_hash: str = "",
        strategy: str = "rebase",  # "rebase" | "merge" | "none"
    ) -> str:
        """Fast-forward the parent branch with the worktree's commit(s).

        Returns the parent branch HEAD SHA after fold.

        Raises:
            WorktreeFoldError: rebase/merge conflict; worktree is LEFT INTACT.
        """
        if not self._enabled or str(handle.path) == "/dev/null":
            return ""

        if not commit_hash:
            # Check if the worktree HEAD differs from base_sha; skip fold if not.
            r = _run_git(["rev-parse", "HEAD"], cwd=handle.path)
            current_sha = r.stdout.strip()
            if current_sha == handle.base_sha:
                _log.info(
                    "WorktreeManager.fold_back: no commits to fold for step=%s; skipping",
                    handle.step_id,
                )
                return handle.base_sha
            commit_hash = current_sha

        t_start = time.monotonic()

        _log.info(
            "WorktreeManager.fold_back: task=%s step=%s strategy=%s branch=%s commit=%s",
            handle.task_id, handle.step_id, strategy, handle.branch, commit_hash[:8],
        )

        try:
            if strategy == "none":
                # Fast-forward only — skip rebase.
                new_head = self._fast_forward(handle, commit_hash)
            elif strategy == "merge":
                new_head = self._merge_fold(handle, commit_hash)
            else:
                # Default: rebase
                new_head = self._rebase_fold(handle, commit_hash)
        except WorktreeFoldError:
            raise
        except WorktreeCreateError as exc:
            raise WorktreeFoldError(str(exc)) from exc

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        files_r = _run_git(
            ["diff", "--name-only", handle.base_sha, new_head],
            cwd=self._project_root,
            check=False,
        )
        files_count = len([f for f in files_r.stdout.splitlines() if f.strip()])

        self._emit("worktree_fold", {
            "task_id": handle.task_id,
            "step_id": handle.step_id,
            "branch": handle.branch,
            "parent_branch": handle.base_branch,
            "new_head": new_head,
            "strategy": strategy,
            "files_count": files_count,
        }, duration_ms=elapsed_ms)

        return new_head

    def _rebase_fold(self, handle: WorktreeHandle, commit_hash: str) -> str:
        """Rebase worktree branch onto current working branch tip and FF."""
        # Step 1: fetch the worktree's branch ref into the parent repo
        _run_git(
            ["fetch", str(handle.path), f"{handle.branch}:{handle.branch}"],
            cwd=self._project_root,
        )

        # Step 2: rebase agent's commits onto current working-branch tip
        rebase_result = subprocess.run(
            ["git", "rebase", "--onto", handle.base_branch, handle.base_sha, handle.branch],
            capture_output=True,
            text=True,
            cwd=str(self._project_root),
        )

        if rebase_result.returncode != 0:
            # Abort the rebase to leave a clean state
            subprocess.run(
                ["git", "rebase", "--abort"],
                capture_output=True,
                cwd=str(self._project_root),
            )
            # Find conflict files from rebase output
            conflict_files = self._parse_conflict_files(rebase_result.stdout + rebase_result.stderr)
            raise WorktreeFoldError(
                f"Rebase conflict for step={handle.step_id}: {rebase_result.stderr.strip()}",
                conflict_files=conflict_files,
            )

        # Step 3: resolve new tip of the rebased branch
        tip_r = _run_git(["rev-parse", handle.branch], cwd=self._project_root)
        new_tip = tip_r.stdout.strip()

        # Step 4: fast-forward working branch to new tip
        _run_git(
            ["update-ref", f"refs/heads/{handle.base_branch}", new_tip],
            cwd=self._project_root,
        )

        return new_tip

    def _merge_fold(self, handle: WorktreeHandle, commit_hash: str) -> str:
        """Merge worktree branch into working branch."""
        _run_git(
            ["fetch", str(handle.path), f"{handle.branch}:{handle.branch}"],
            cwd=self._project_root,
        )
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", handle.branch, "-m",
             f"Merge worktree/{handle.step_id} into {handle.base_branch}"],
            capture_output=True,
            text=True,
            cwd=str(self._project_root),
        )
        if merge_result.returncode != 0:
            subprocess.run(
                ["git", "merge", "--abort"],
                capture_output=True,
                cwd=str(self._project_root),
            )
            conflict_files = self._parse_conflict_files(merge_result.stdout + merge_result.stderr)
            raise WorktreeFoldError(
                f"Merge conflict for step={handle.step_id}: {merge_result.stderr.strip()}",
                conflict_files=conflict_files,
            )
        tip_r = _run_git(["rev-parse", handle.base_branch], cwd=self._project_root)
        return tip_r.stdout.strip()

    def _fast_forward(self, handle: WorktreeHandle, commit_hash: str) -> str:
        """Fast-forward working branch directly to commit_hash."""
        _run_git(
            ["update-ref", f"refs/heads/{handle.base_branch}", commit_hash],
            cwd=self._project_root,
        )
        return commit_hash

    @staticmethod
    def _parse_conflict_files(output: str) -> list[str]:
        """Extract conflict file names from git output."""
        files: list[str] = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("CONFLICT") and ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    files.append(parts[1].strip())
            elif line.startswith("Auto-merging "):
                pass  # not a conflict
        return files

    def cleanup(
        self,
        handle: WorktreeHandle,
        *,
        on_failure: bool = False,
        force: bool = False,
    ) -> None:
        """Remove a worktree.

        Behavior:
        - on_failure=True:  NO-OP (worktree retained for takeover/forensics).
        - on_failure=False: ``git worktree remove`` + branch delete.
        - force=True:       skips the on_failure guard (used by GC).

        Always emits a ``worktree_cleanup`` trace event, even on no-op.
        """
        reason = "success" if not on_failure else "failure"
        if force:
            reason = "gc"

        if on_failure and not force:
            # Retain the worktree — no-op + trace + bead
            _log.info(
                "WorktreeManager.cleanup: RETAINING worktree for failed step=%s path=%s",
                handle.step_id, handle.path,
            )
            self._emit("worktree_cleanup", {
                "task_id": handle.task_id,
                "step_id": handle.step_id,
                "path": str(handle.path),
                "retained": True,
                "reason": reason,
            })
            self._file_bead_warning(
                task_id=handle.task_id,
                step_id=handle.step_id,
                content=(
                    f"BEAD_DISCOVERY: worktree-retained step={handle.step_id} "
                    f"path={handle.path}"
                ),
            )
            return

        if not self._enabled or str(handle.path) == "/dev/null":
            return

        lock_path = _lock_path_for(self._worktrees_root, handle.task_id)
        with _FileLock(lock_path):
            self._do_cleanup(handle, force=force)

        self._handles.pop((handle.task_id, handle.step_id), None)

        self._emit("worktree_cleanup", {
            "task_id": handle.task_id,
            "step_id": handle.step_id,
            "path": str(handle.path),
            "retained": False,
            "reason": reason,
        })

    def _do_cleanup(self, handle: WorktreeHandle, force: bool) -> None:
        """Internal: actually remove the worktree + branch."""
        # Remove .baton-worktree.json so list_active() won't resurrect it
        manifest = handle.path / ".baton-worktree.json"
        try:
            manifest.unlink(missing_ok=True)
        except Exception:
            pass

        # git worktree remove
        if handle.path.exists():
            result = subprocess.run(
                ["git", "worktree", "remove", str(handle.path)],
                capture_output=True,
                text=True,
                cwd=str(self._project_root),
            )
            if result.returncode != 0:
                if not force:
                    raise WorktreeCleanupError(
                        f"git worktree remove failed for step={handle.step_id}: "
                        f"{result.stderr.strip()}"
                    )
                # force=True: retry with --force
                result2 = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(handle.path)],
                    capture_output=True,
                    text=True,
                    cwd=str(self._project_root),
                )
                if result2.returncode != 0:
                    self._file_bead_warning(
                        task_id=handle.task_id,
                        step_id=handle.step_id,
                        content=(
                            f"BEAD_WARNING: worktree-cleanup-failed step={handle.step_id} "
                            f"path={handle.path} reason={result2.stderr.strip()}"
                        ),
                    )
                    raise WorktreeCleanupError(
                        f"git worktree remove --force failed for step={handle.step_id}: "
                        f"{result2.stderr.strip()}"
                    )

        # Delete the worktree branch
        subprocess.run(
            ["git", "branch", "-D", handle.branch],
            capture_output=True,
            cwd=str(self._project_root),
        )

        # Prune stale registry entries
        subprocess.run(
            ["git", "worktree", "prune"],
            capture_output=True,
            cwd=str(self._project_root),
        )

        _log.info(
            "WorktreeManager.cleanup: removed worktree step=%s path=%s",
            handle.step_id, handle.path,
        )

    def gc_stale(
        self,
        max_age_hours: int = 72,
        *,
        terminal_step_ids: set[str] | None = None,
        dry_run: bool = False,
    ) -> list[WorktreeHandle]:
        """Remove worktrees older than max_age_hours whose step is terminal.

        SAFETY: never deletes a worktree whose step is still in
        ``running``, ``dispatched``, ``interacting``, or ``gate_pending``.

        Returns the list of handles reclaimed.
        """
        if not self._enabled:
            return []

        reclaimed: list[WorktreeHandle] = []
        skipped = 0
        gc_log = self._worktrees_root / ".gc.log"

        now = datetime.now(tz=timezone.utc)

        # Walk all task subdirectories
        if not self._worktrees_root.is_dir():
            return []

        for task_dir in self._worktrees_root.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            for step_dir in task_dir.iterdir():
                if not step_dir.is_dir():
                    continue
                manifest = step_dir / ".baton-worktree.json"
                if not manifest.exists():
                    skipped += 1
                    continue
                try:
                    handle = WorktreeHandle.from_dict(json.loads(manifest.read_text("utf-8")))
                except Exception as exc:
                    _log.debug("gc_stale: skipping %s (bad manifest: %s)", step_dir, exc)
                    skipped += 1
                    continue

                # Age check
                try:
                    created = datetime.fromisoformat(handle.created_at)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_hours = (now - created).total_seconds() / 3600.0
                except Exception:
                    age_hours = 0.0

                if age_hours < max_age_hours:
                    _log.debug(
                        "gc_stale: skipping step=%s (age=%.1fh < %dh)",
                        handle.step_id, age_hours, max_age_hours,
                    )
                    skipped += 1
                    continue

                # Terminal check (safety gate)
                if terminal_step_ids is not None:
                    if handle.step_id not in terminal_step_ids:
                        _log.debug(
                            "gc_stale: skipping step=%s (not in terminal_step_ids)",
                            handle.step_id,
                        )
                        skipped += 1
                        self._append_gc_log(gc_log, "skipped", handle, reason="not_terminal")
                        continue

                if dry_run:
                    _log.info("gc_stale: [DRY RUN] would reclaim step=%s path=%s", handle.step_id, handle.path)
                    reclaimed.append(handle)
                    continue

                try:
                    self._do_cleanup(handle, force=True)
                    reclaimed.append(handle)
                    self._handles.pop((handle.task_id, handle.step_id), None)
                    self._append_gc_log(gc_log, "reclaimed", handle, reason="max_age_expired")
                    _log.info("gc_stale: reclaimed step=%s path=%s", handle.step_id, handle.path)
                except Exception as exc:
                    _log.warning("gc_stale: cleanup failed for step=%s: %s", handle.step_id, exc)
                    skipped += 1
                    self._append_gc_log(gc_log, "failed", handle, reason=str(exc))

        # Prune orphaned .git/worktrees/ registry entries
        subprocess.run(
            ["git", "worktree", "prune"],
            capture_output=True,
            cwd=str(self._project_root),
        )

        self._emit("worktree_gc", {
            "reclaimed": len(reclaimed),
            "skipped": skipped,
            "max_age_hours": max_age_hours,
            "dry_run": dry_run,
        })

        _log.info(
            "gc_stale: complete — reclaimed=%d skipped=%d max_age_hours=%d dry_run=%s",
            len(reclaimed), skipped, max_age_hours, dry_run,
        )
        return reclaimed

    @staticmethod
    def _append_gc_log(gc_log: Path, action: str, handle: WorktreeHandle, reason: str) -> None:
        try:
            gc_log.parent.mkdir(parents=True, exist_ok=True)
            with gc_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": _utcnow(),
                    "action": action,
                    "task_id": handle.task_id,
                    "step_id": handle.step_id,
                    "path": str(handle.path),
                    "reason": reason,
                }) + "\n")
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def list_active(self) -> list[WorktreeHandle]:
        """Read .baton-worktree.json files to enumerate live worktrees."""
        handles: list[WorktreeHandle] = []
        if not self._worktrees_root.is_dir():
            return handles
        for task_dir in self._worktrees_root.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            for step_dir in task_dir.iterdir():
                if not step_dir.is_dir():
                    continue
                manifest = step_dir / ".baton-worktree.json"
                if manifest.exists():
                    try:
                        handle = WorktreeHandle.from_dict(json.loads(manifest.read_text("utf-8")))
                        handles.append(handle)
                        self._handles[(handle.task_id, handle.step_id)] = handle
                    except Exception as exc:
                        _log.debug("list_active: skipping %s (%s)", step_dir, exc)
        return handles

    def handle_for(self, task_id: str, step_id: str) -> WorktreeHandle | None:
        """Return the handle for an existing worktree, or None."""
        cached = self._handles.get((task_id, step_id))
        if cached is not None:
            return cached
        manifest = self._worktrees_root / task_id / step_id / ".baton-worktree.json"
        if manifest.exists():
            try:
                handle = WorktreeHandle.from_dict(json.loads(manifest.read_text("utf-8")))
                self._handles[(task_id, step_id)] = handle
                return handle
            except Exception:
                pass
        return None

    # ── Context manager ───────────────────────────────────────────────────────

    @contextmanager
    def session(
        self,
        task_id: str,
        step_id: str,
        base_branch: str,
        *,
        fold_on_success: bool = True,
    ) -> Iterator[WorktreeHandle]:
        """create() + fold_back()-on-success + cleanup() in one block.

        On exception: cleanup(on_failure=True) is invoked (no-op retain).
        """
        handle = self.create(task_id=task_id, step_id=step_id, base_branch=base_branch)
        try:
            yield handle
        except Exception:
            self.cleanup(handle, on_failure=True)
            raise
        else:
            if fold_on_success:
                self.fold_back(handle)
            self.cleanup(handle, on_failure=False)
