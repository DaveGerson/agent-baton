"""Wave 5.1 — Seamless Developer Takeover (bd-e208).

Provides the data structures and helpers that manage the ``paused-takeover``
state for an execution step.  When a gate fails and the developer wants to
inspect and fix the worktree manually, this module coordinates the state
transition, editor/shell launch, and resume-time HEAD-diff check.

Design invariant: **pure helpers only**.  No engine state mutation lives here.
All state transitions (status, step_results, takeover_records) are performed
in executor.py's ``start_takeover`` / ``resume_from_takeover`` methods, which
call into this module for pure computation and error validation.

State machine (status values):
    running         → paused-takeover   (via engine.start_takeover)
    paused-takeover → running           (via engine.resume_from_takeover, gate pass)
    paused-takeover → failed            (via engine.resume_from_takeover --abort)
    paused-takeover → paused-takeover   (idempotent re-entry / gate still failing)

Forbidden: dispatched → paused-takeover, complete → paused-takeover.

Open Q5 (TODO bd-e208): Takeover within nested Team-step — v1 drops into
parent worktree; revisit when team-member isolation lands.
"""
from __future__ import annotations

import getpass
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.worktree_manager import WorktreeHandle

_log = logging.getLogger(__name__)

__all__ = [
    "TakeoverRecord",
    "TakeoverError",
    "TakeoverWorktreeMissingError",
    "TakeoverInvalidStateError",
    "TakeoverSession",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TakeoverError(Exception):
    """Base class for all takeover errors."""


class TakeoverWorktreeMissingError(TakeoverError):
    """Raised when no retained worktree exists for the requested step.

    This means either:
    - The step never ran with worktree isolation (BATON_WORKTREE_ENABLED=0).
    - The worktree was already GC'd.
    - The step ID is incorrect.
    """


class TakeoverInvalidStateError(TakeoverError):
    """Raised when a takeover is requested for a step in a disallowed state.

    Allowed source states: ``running``, ``gate_failed``, ``failed``.
    Forbidden: ``dispatched``, ``complete``.
    """


# ---------------------------------------------------------------------------
# TakeoverRecord dataclass
# ---------------------------------------------------------------------------


@dataclass
class TakeoverRecord:
    """Immutable-style audit record for a developer takeover on a step.

    Fields match the design spec Part A verbatim.  ``resumed_at`` and
    ``resolution`` are filled in on resume; empty string means "still active".

    Resolution values: ``'completed'`` | ``'aborted'`` | ``'still-failing'``.
    """

    step_id: str
    started_at: str
    started_by: str            # getpass.getuser() fallback to "unknown"
    reason: str
    editor_or_shell: str       # the command that was launched
    pid: int                   # PID of the editor/shell subprocess
    last_known_worktree_head: str   # git HEAD at takeover start
    resumed_at: str = ""       # empty → still active
    resolution: str = ""       # 'completed' | 'aborted' | 'still-failing'

    def is_active(self) -> bool:
        """Return True when this record has not yet been resolved."""
        return not self.resumed_at

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "started_at": self.started_at,
            "started_by": self.started_by,
            "reason": self.reason,
            "editor_or_shell": self.editor_or_shell,
            "pid": self.pid,
            "last_known_worktree_head": self.last_known_worktree_head,
            "resumed_at": self.resumed_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TakeoverRecord:
        return cls(
            step_id=data["step_id"],
            started_at=data.get("started_at", ""),
            started_by=data.get("started_by", "unknown"),
            reason=data.get("reason", ""),
            editor_or_shell=data.get("editor_or_shell", ""),
            pid=int(data.get("pid", 0)),
            last_known_worktree_head=data.get("last_known_worktree_head", ""),
            resumed_at=data.get("resumed_at", ""),
            resolution=data.get("resolution", ""),
        )


# ---------------------------------------------------------------------------
# TakeoverSession
# ---------------------------------------------------------------------------

# States from which takeover is permitted as the source.
_ALLOWED_TAKEOVER_SOURCE_STATES: frozenset[str] = frozenset({
    "running",
    "gate_failed",
    "failed",
    "paused-takeover",   # idempotent re-entry
})

# States that are forbidden as takeover sources (final or in-flight).
_FORBIDDEN_TAKEOVER_SOURCE_STATES: frozenset[str] = frozenset({
    "dispatched",
    "complete",
    "cancelled",
})


class TakeoverSession:
    """Pure helper that validates preconditions and computes takeover state.

    This class contains no engine state.  The executor calls these helpers
    and then applies the returned data to ``ExecutionState``.

    Args:
        worktree_mgr: The engine's ``WorktreeManager`` instance (may be None
            when worktrees are disabled — raises
            ``TakeoverWorktreeMissingError`` in that case).
        task_id: The active task ID.
    """

    def __init__(
        self,
        worktree_mgr: object | None,   # WorktreeManager | None
        task_id: str,
    ) -> None:
        self._mgr = worktree_mgr
        self._task_id = task_id

    # ── Validation ───────────────────────────────────────────────────────────

    def validate_source_state(self, step_id: str, current_status: str) -> None:
        """Raise ``TakeoverInvalidStateError`` if *current_status* is disallowed.

        Allowed source states for a takeover:
            running, gate_failed, failed, paused-takeover (idempotent).
        """
        if current_status in _FORBIDDEN_TAKEOVER_SOURCE_STATES:
            raise TakeoverInvalidStateError(
                f"Cannot start takeover on step '{step_id}': "
                f"status '{current_status}' is not a takeover-eligible state. "
                f"Allowed states: {sorted(_ALLOWED_TAKEOVER_SOURCE_STATES)}."
            )
        if current_status not in _ALLOWED_TAKEOVER_SOURCE_STATES:
            raise TakeoverInvalidStateError(
                f"Cannot start takeover on step '{step_id}': "
                f"unrecognised status '{current_status}'."
            )

    def resolve_handle(self, step_id: str) -> WorktreeHandle:
        """Return the ``WorktreeHandle`` for *step_id* or raise.

        Raises:
            TakeoverWorktreeMissingError: when the worktree manager is None
                (disabled) or no retained worktree exists for this step.
        """
        if self._mgr is None:
            raise TakeoverWorktreeMissingError(
                f"Cannot take over step '{step_id}': "
                "WorktreeManager is disabled (BATON_WORKTREE_ENABLED=0 or not a git repo). "
                "Takeover requires a retained failed worktree."
            )
        handle = self._mgr.handle_for(self._task_id, step_id)  # type: ignore[attr-defined]
        if handle is None:
            raise TakeoverWorktreeMissingError(
                f"No retained worktree found for step '{step_id}' "
                f"(task_id={self._task_id!r}). "
                "The worktree may have been GC'd (72h default) or the step "
                "never ran with worktree isolation. "
                "Check: baton execute worktree-gc --dry-run"
            )
        return handle

    # ── Git helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def read_head(worktree_path: Path) -> str:
        """Return the current HEAD SHA of *worktree_path*, or '' on error."""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(worktree_path),
                timeout=10,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception as exc:
            _log.debug("TakeoverSession.read_head: error reading HEAD at %s: %s", worktree_path, exc)
        return ""

    @staticmethod
    def compute_dev_commits(worktree_path: Path, base_sha: str, current_head: str) -> list[str]:
        """Return the list of SHAs added by the developer (base_sha..current_head).

        Returns an empty list when HEAD is unchanged or on error.
        """
        if not base_sha or not current_head or base_sha == current_head:
            return []
        try:
            r = subprocess.run(
                ["git", "log", "--pretty=format:%H", f"{base_sha}..{current_head}"],
                capture_output=True,
                text=True,
                cwd=str(worktree_path),
                timeout=10,
            )
            if r.returncode == 0:
                lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
                return lines
        except Exception as exc:
            _log.debug("TakeoverSession.compute_dev_commits: error: %s", exc)
        return []

    @staticmethod
    def has_diff(worktree_path: Path, base_sha: str, head_sha: str) -> bool:
        """Return True when there is a non-empty diff between base and head."""
        if not base_sha or not head_sha or base_sha == head_sha:
            return base_sha != head_sha
        try:
            r = subprocess.run(
                ["git", "diff", "--quiet", base_sha, head_sha],
                capture_output=True,
                cwd=str(worktree_path),
                timeout=10,
            )
            # exit 1 → has diff; exit 0 → no diff
            return r.returncode == 1
        except Exception:
            return False

    @staticmethod
    def append_coauthored_trailer(worktree_path: Path, agent_name: str) -> bool:
        """Append a ``Co-Authored-By:`` trailer to the last commit.

        Uses ``git commit --amend --trailer`` with ``--no-edit``.  Developer
        remains the Author; the trailer records provenance for Wave 6.1.

        When Wave 6.1 souls aren't yet shipped, the trailer falls back to
        ``agent-baton-<agent_name> <agent_name>@baton.local``.

        Returns True on success, False on failure.
        """
        trailer_value = f"agent-baton-{agent_name} <{agent_name}@baton.local>"
        try:
            r = subprocess.run(
                [
                    "git", "commit", "--amend", "--no-edit",
                    "--trailer", f"Co-Authored-By: {trailer_value}",
                ],
                capture_output=True,
                text=True,
                cwd=str(worktree_path),
                timeout=30,
            )
            if r.returncode == 0:
                _log.info(
                    "TakeoverSession: appended Co-Authored-By trailer at %s", worktree_path
                )
                return True
            _log.warning(
                "TakeoverSession: git commit --amend failed (exit %d): %s",
                r.returncode, r.stderr.strip(),
            )
        except Exception as exc:
            _log.warning("TakeoverSession: Co-Authored-By trailer append failed: %s", exc)
        return False

    # ── Editor / shell launch ────────────────────────────────────────────────

    @staticmethod
    def resolve_editor_command(*, use_shell: bool = False, editor_override: str = "") -> str:
        """Resolve the editor or shell command to launch.

        Selection priority:
        1. ``--shell`` flag → ``$SHELL`` (fallback: ``/bin/bash``).
        2. Explicit ``--editor`` override.
        3. ``$EDITOR`` env var.
        4. Hard fallback: ``vim``.

        Special case: if the resolved editor is ``code`` (VS Code), ``-w``
        is appended to force blocking mode.
        """
        if use_shell:
            return os.environ.get("SHELL", "/bin/bash")
        if editor_override:
            return editor_override
        editor = os.environ.get("EDITOR", "vim")
        # Auto-append -w for VS Code to force blocking mode.
        if editor.strip() == "code":
            editor = "code -w"
        return editor

    @staticmethod
    def launch_editor(
        editor_cmd: str,
        worktree_path: Path,
        *,
        target_file: str = "",
    ) -> subprocess.Popen:  # type: ignore[type-arg]
        """Launch *editor_cmd* inside *worktree_path* and return the ``Popen`` handle.

        Uses ``shlex.split`` to allow multi-word editor commands like ``code -w``.
        If *target_file* is non-empty it is appended as the final argument.

        The subprocess runs in the worktree directory so the developer lands
        there immediately without needing to cd.
        """
        parts = shlex.split(editor_cmd)
        if target_file:
            parts.append(target_file)
        _log.info(
            "TakeoverSession.launch_editor: launching %r in %s", parts, worktree_path
        )
        proc = subprocess.Popen(
            parts,
            cwd=str(worktree_path),
        )
        return proc

    # ── Banner ───────────────────────────────────────────────────────────────

    @staticmethod
    def print_banner(
        step_id: str,
        task_id: str,
        worktree_path: Path,
        branch: str,
        editor_cmd: str,
    ) -> None:
        """Print the takeover start banner to stdout."""
        print(f"TAKEOVER: {step_id} / task={task_id}")
        print(f"worktree: {worktree_path}")
        print(f"branch:   {branch}")
        print(f"state:    paused-takeover")
        print(f"editor:   {editor_cmd}")
        print(f"on exit:  baton execute resume")

    @staticmethod
    def current_user() -> str:
        """Return the current OS user, or 'unknown' on failure."""
        try:
            return getpass.getuser()
        except Exception:
            return "unknown"
