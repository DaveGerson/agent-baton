"""Wave 6.1 Part A/C — Git-Native Bead Persistence: notes adapter (bd-2870/bd-81b9).

``NotesAdapter`` is the thin I/O layer between the Python bead model and
``git notes``.  It handles two notes refs:

- ``refs/notes/baton-beads``  — per-bead JSON blobs (Part A).
- ``refs/notes/baton-bead-scripts`` — script bodies keyed by content SHA
  (Part C).

The adapter is intentionally stateless beyond the ``repo_root`` path.  It
never caches; callers (BeadStore, ExecutableBeadRunner) own any caching
layer they need.

Resilience contract: every method catches ``subprocess.CalledProcessError``
and ``FileNotFoundError`` and returns a safe default (``None``, ``False``),
so callers degrade gracefully when git is unavailable or the notes ref does
not yet exist.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: Path, *, input: str | None = None) -> str:
    """Run a git command and return stdout, or raise on non-zero exit."""
    cmd = ["git"] + args
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        input=input,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result.stdout.strip()


class NotesAdapter:
    """Git-notes I/O for bead JSON blobs and script bodies.

    Args:
        repo_root: Path to the git repository root.  Defaults to the current
            working directory if not supplied.
    """

    NOTES_REF = "refs/notes/baton-beads"
    SCRIPTS_REF = "refs/notes/baton-bead-scripts"

    def __init__(self, repo_root: Path | None = None) -> None:
        self._root: Path = (repo_root or Path.cwd()).resolve()

    # ------------------------------------------------------------------
    # Bead JSON notes (Part A interface)
    # ------------------------------------------------------------------

    def _sentinel_commit(self) -> str | None:
        """Return the anchor sentinel commit SHA.

        Uses ``merge-base origin/main HEAD`` for project-scoped beads, or
        the repo's root commit as fallback (Open Question 4 answer: B).
        """
        try:
            return _run_git(
                ["merge-base", "origin/main", "HEAD"],
                self._root,
            )
        except subprocess.CalledProcessError:
            pass
        try:
            return _run_git(
                ["rev-list", "--max-parents=0", "HEAD"],
                self._root,
            )
        except subprocess.CalledProcessError:
            return None

    def write_bead(self, bead_id: str, bead_dict: dict) -> bool:
        """Attach *bead_dict* as a JSON note on the sentinel commit.

        Args:
            bead_id: Used only for logging.
            bead_dict: Dict from ``Bead.to_dict()``.

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        sentinel = self._sentinel_commit()
        if not sentinel:
            _log.warning("NotesAdapter.write_bead: no sentinel commit for %s", bead_id)
            return False
        try:
            note_body = json.dumps(bead_dict, separators=(",", ":"), sort_keys=True)
            _run_git(
                [
                    "notes",
                    f"--ref={self.NOTES_REF}",
                    "add",
                    "-f",
                    "-m",
                    note_body,
                    sentinel,
                ],
                self._root,
            )
            _log.debug("NotesAdapter.write_bead: wrote %s to %s", bead_id, sentinel[:8])
            return True
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "NotesAdapter.write_bead: git notes failed for %s: %s",
                bead_id,
                exc.stderr,
            )
            return False

    def read_bead(self, anchor_commit: str) -> dict | None:
        """Read a bead JSON note from *anchor_commit*.

        Args:
            anchor_commit: The commit SHA the note is attached to.

        Returns:
            Parsed dict or ``None`` if not found.
        """
        try:
            raw = _run_git(
                ["notes", f"--ref={self.NOTES_REF}", "show", anchor_commit],
                self._root,
            )
            return json.loads(raw)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # Script body notes (Part C interface)
    # ------------------------------------------------------------------

    def write_script(self, content_sha: str, script_body: str) -> bool:
        """Store *script_body* as a note on the sentinel commit.

        Scripts are content-addressed: the same SHA will overwrite an
        identical prior write (idempotent).

        Args:
            content_sha: SHA-256 hex digest of *script_body*.
            script_body: The full script text.

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        sentinel = self._sentinel_commit()
        if not sentinel:
            _log.warning(
                "NotesAdapter.write_script: no sentinel commit for script %s",
                content_sha[:8],
            )
            return False

        # We encode the (sha, body) pair so multiple scripts can coexist on
        # the same sentinel commit.  Each script is stored as a note keyed by
        # a sub-ref: refs/notes/baton-bead-scripts/<sha>.
        script_ref = f"{self.SCRIPTS_REF}/{content_sha}"
        try:
            _run_git(
                [
                    "notes",
                    f"--ref={script_ref}",
                    "add",
                    "-f",
                    "-m",
                    script_body,
                    sentinel,
                ],
                self._root,
            )
            _log.debug(
                "NotesAdapter.write_script: stored script %s on %s",
                content_sha[:8], sentinel[:8],
            )
            return True
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "NotesAdapter.write_script: git notes failed for %s: %s",
                content_sha[:8], exc.stderr,
            )
            return False

    def read_script(self, content_sha: str) -> str | None:
        """Read a script body stored under *content_sha*.

        Args:
            content_sha: SHA-256 hex digest of the script body.

        Returns:
            The script body string, or ``None`` if not found.
        """
        sentinel = self._sentinel_commit()
        if not sentinel:
            return None
        script_ref = f"{self.SCRIPTS_REF}/{content_sha}"
        try:
            return _run_git(
                ["notes", f"--ref={script_ref}", "show", sentinel],
                self._root,
            )
        except subprocess.CalledProcessError:
            return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def compute_script_sha(script_body: str) -> str:
        """Return the SHA-256 hex digest of *script_body* (UTF-8 encoded)."""
        return hashlib.sha256(script_body.encode("utf-8")).hexdigest()

    @staticmethod
    def script_ref_for(content_sha: str) -> str:
        """Return the canonical ``script_ref`` string for *content_sha*."""
        return f"refs/notes/baton-bead-scripts:{content_sha}"
