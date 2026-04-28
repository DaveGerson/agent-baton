"""Git-notes-backed storage adapter for bead persistence.

Part A of the Gastown bead architecture (bd-2870).  Stores one git note per
bead in ``refs/notes/baton-beads``, anchored to the commit that the engine
considers the bead's creation point.

Notes reference: ``refs/notes/baton-beads``
Storage format: one JSON blob per note, anchored to the bead's creation
commit (task-scoped beads → HEAD at step start; project-scoped beads →
``git merge-base origin/main HEAD``).

All git operations are performed via ``subprocess.run`` — no GitPython
dependency.  Every method degrades gracefully: any subprocess failure returns
a safe empty value and logs a warning.  No exception is propagated to callers.

Replication safety (end-user readiness #5)
------------------------------------------
Git notes are stored in ``refs/notes/*`` which git does NOT replicate by
default on ``git fetch`` / ``git push``.  Without explicit refspec
configuration, bead notes written on one clone will never reach another.

``verify_notes_replication_configured()`` checks whether the required fetch
refspec is present in the repo's git config.  The first bead write per
process session emits a ``BEAD_WARNING`` log line when the check fails, so
users see an actionable message without being spammed on every write.

To configure replication run ``scripts/install.sh`` (any scope) or:

    git config --add remote.origin.fetch '+refs/notes/*:refs/notes/*'
    git config --add remote.origin.push  '+refs/notes/*:refs/notes/*'

Set ``BATON_SKIP_GIT_NOTES_SETUP=1`` to silence the warning if you
intentionally manage notes replication outside of baton.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)

_GIT_TIMEOUT = 15  # seconds per git subprocess call

# Refspec that must be present in remote.origin.fetch for notes to replicate.
_NOTES_FETCH_REFSPEC = "+refs/notes/*:refs/notes/*"
_NOTES_PUSH_REFSPEC = "+refs/notes/*:refs/notes/*"

# Per-process flag: have we already emitted the replication warning?
_replication_warning_emitted: bool = False


def verify_notes_replication_configured(repo_path: Path) -> bool:
    """Return ``True`` if the notes-replication fetch refspec is configured.

    Checks ``git config --get-all remote.origin.fetch`` in *repo_path* for
    the presence of ``+refs/notes/*:refs/notes/*``.  Returns ``False`` when
    the refspec is absent or when git is unavailable / repo_path is not a git
    repository.

    This function never raises; all failures return ``False`` and log at
    DEBUG level.

    Args:
        repo_path: Absolute path to the git repository root.

    Returns:
        ``True`` if the fetch refspec is configured, ``False`` otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get-all", "remote.origin.fetch"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode not in (0, 5):
            # returncode 5 means "no value found"; anything else is unexpected
            _log.debug(
                "verify_notes_replication_configured: git config exited %d in %s",
                result.returncode,
                repo_path,
            )
            return False
        configured_refspecs = result.stdout.splitlines()
        return _NOTES_FETCH_REFSPEC in configured_refspecs
    except Exception as exc:
        _log.debug(
            "verify_notes_replication_configured: error checking refspec in %s: %s",
            repo_path,
            exc,
        )
        return False


def _maybe_warn_replication(repo_path: Path) -> None:
    """Emit a one-time BEAD_WARNING if notes replication is not configured.

    Does nothing when:
    - The warning has already been emitted this session.
    - ``BATON_SKIP_GIT_NOTES_SETUP=1`` is set in the environment.
    - Notes replication is already configured.
    """
    global _replication_warning_emitted
    if _replication_warning_emitted:
        return
    if os.environ.get("BATON_SKIP_GIT_NOTES_SETUP", "").strip() == "1":
        return
    if not verify_notes_replication_configured(repo_path):
        _log.warning(
            "BEAD_WARNING: git-notes replication not configured — beads will not sync "
            "to other clones. Run scripts/install.sh or "
            "`git config remote.origin.fetch +refs/notes/*:refs/notes/*` to fix."
        )
        _replication_warning_emitted = True


class NotesAdapter:
    """Git-notes-backed adapter for the BeadStore facade.

    Notes ref: ``refs/notes/baton-beads``
    Storage: one note per bead, JSON blob anchored to the bead's creation
    commit.  The note key is the commit SHA; the blob contains the full
    ``Bead.to_dict()`` payload (including the ``bead_id`` field so callers
    can reconstruct which bead a note belongs to on a ``list()`` scan).

    Args:
        repo_root: Absolute path to the git repository root.  All ``git``
            subprocess calls use ``git -C <repo_root>``.
    """

    NOTES_REF = "refs/notes/baton-beads"

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in the repo root.  Returns the CompletedProcess."""
        cmd = ["git", "-C", str(self._repo_root), *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Ref lifecycle
    # ------------------------------------------------------------------

    def has_ref(self) -> bool:
        """Return True if the notes ref ``refs/notes/baton-beads`` exists."""
        try:
            result = self._git("rev-parse", "--verify", self.NOTES_REF)
            return result.returncode == 0
        except Exception as exc:
            _log.debug("NotesAdapter.has_ref error: %s", exc)
            return False

    def init_ref(self) -> None:
        """Create the notes ref if it does not already exist.

        Uses ``git notes --ref=<ref> list`` as a no-op probe; if that fails
        (ref absent), we write an empty initial note to a sentinel that we
        immediately remove, which is the safest way to materialise the ref
        without needing an arbitrary commit to annotate.

        In practice ``git notes`` creates the ref lazily on the first
        ``add`` call, so this method is a pre-flight convenience for callers
        that want to verify the ref is reachable before any writes.
        """
        if self.has_ref():
            return
        # The ref is created implicitly by the first ``git notes add`` call.
        # We log a debug note here but do not force any write.
        _log.debug(
            "NotesAdapter.init_ref: ref %s does not exist yet — "
            "it will be created on the first write()",
            self.NOTES_REF,
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def write(self, bead_id: str, anchor_commit: str, blob: dict) -> None:
        """Attach *blob* as a JSON note to *anchor_commit*.

        Uses ``git notes --ref=<ref> add -f -m '<json>' <anchor>`` with the
        force flag so that closing/replacing a bead overwrites any prior note
        on the same anchor commit.

        On the first call per process session this method also checks whether
        git-notes replication is configured (see ``verify_notes_replication_configured``)
        and emits a one-time ``BEAD_WARNING`` log line if not.

        Args:
            bead_id: Bead identifier (stored inside the blob but also used for
                logging).
            anchor_commit: The commit SHA to annotate.  Must be a reachable
                commit in the repository.
            blob: Plain dict to serialize as the note body.  Typically the
                output of ``Bead.to_dict()``.

        Raises:
            Nothing — all failures are logged as warnings and silently ignored
            to preserve the "notes write is warn-only" contract.
        """
        # Emit a one-time replication warning if not already configured.
        _maybe_warn_replication(self._repo_root)

        if not anchor_commit:
            _log.warning(
                "NotesAdapter.write: bead %s has empty anchor_commit — skipping note write",
                bead_id,
            )
            return
        try:
            json_body = json.dumps(blob, separators=(",", ":"), ensure_ascii=False)
            result = self._git(
                "notes",
                f"--ref={self.NOTES_REF}",
                "add",
                "-f",
                "-m",
                json_body,
                anchor_commit,
            )
            if result.returncode != 0:
                _log.warning(
                    "BEAD_WARNING: notes-write-failed bead=%s anchor=%s stderr=%r",
                    bead_id,
                    anchor_commit,
                    result.stderr.strip(),
                )
        except Exception as exc:
            _log.warning(
                "BEAD_WARNING: notes-write-failed bead=%s exc=%s",
                bead_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def read(self, bead_id: str, anchor_commit: str) -> dict | None:
        """Fetch the note attached to *anchor_commit* and return the parsed dict.

        Uses ``git notes --ref=<ref> show <anchor>``.  Returns ``None`` on any
        failure (no note, parse error, git error).

        Args:
            bead_id: Only used for diagnostic logging.
            anchor_commit: The commit SHA whose note to fetch.

        Returns:
            Parsed JSON dict, or ``None`` if not found.
        """
        if not anchor_commit:
            return None
        try:
            result = self._git("notes", f"--ref={self.NOTES_REF}", "show", anchor_commit)
            if result.returncode != 0:
                _log.debug(
                    "NotesAdapter.read: no note for bead=%s anchor=%s",
                    bead_id,
                    anchor_commit,
                )
                return None
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            _log.warning(
                "NotesAdapter.read: JSON parse error for bead=%s anchor=%s: %s",
                bead_id,
                anchor_commit,
                exc,
            )
            return None
        except Exception as exc:
            _log.warning(
                "NotesAdapter.read: unexpected error bead=%s: %s",
                bead_id,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # List / scan
    # ------------------------------------------------------------------

    def list(self) -> list[tuple[str, str]]:
        """Return all ``(anchor_commit, bead_id)`` pairs in the notes ref.

        Parses the output of ``git notes --ref=<ref> list``.  Each output line
        has the form ``<note_object_sha> <annotated_commit_sha>``.  We resolve
        each note object to its content and extract the ``bead_id`` field from
        the JSON blob.

        Lines whose note body is not valid JSON or is missing a ``bead_id``
        field are skipped with a debug log.

        Returns:
            List of ``(anchor_commit_sha, bead_id)`` tuples, one per valid
            note in the ref.  Empty list if the ref doesn't exist or on any
            error.
        """
        try:
            result = self._git("notes", f"--ref={self.NOTES_REF}", "list")
            if result.returncode != 0:
                _log.debug("NotesAdapter.list: no notes ref or empty ref")
                return []

            pairs: list[tuple[str, str]] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 2:
                    _log.debug("NotesAdapter.list: unexpected line %r", line)
                    continue
                note_sha, anchor_commit = parts[0], parts[1]
                # Resolve note object to its text content
                cat_result = self._git("cat-file", "-p", note_sha)
                if cat_result.returncode != 0:
                    _log.debug(
                        "NotesAdapter.list: cannot cat-file note %s", note_sha
                    )
                    continue
                try:
                    blob = json.loads(cat_result.stdout)
                    bead_id = blob.get("bead_id", "")
                    if bead_id:
                        pairs.append((anchor_commit, bead_id))
                    else:
                        _log.debug(
                            "NotesAdapter.list: note at anchor %s has no bead_id",
                            anchor_commit,
                        )
                except json.JSONDecodeError:
                    _log.debug(
                        "NotesAdapter.list: non-JSON note at anchor %s — skipping",
                        anchor_commit,
                    )
            return pairs
        except Exception as exc:
            _log.warning("NotesAdapter.list: error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Anchor resolution helpers
    # ------------------------------------------------------------------

    def resolve_head(self) -> str:
        """Return the current HEAD commit SHA, or empty string on failure."""
        try:
            result = self._git("rev-parse", "HEAD")
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            _log.debug("NotesAdapter.resolve_head: %s", exc)
        return ""

    def resolve_merge_base(self) -> str:
        """Return ``git merge-base origin/main HEAD``, falling back to root commit.

        Used as the anchor for project-scoped beads.  On failure (no
        ``origin/main``, detached HEAD, unborn branch) falls back to the
        repository root commit via ``git rev-list --max-parents=0 HEAD``.
        """
        try:
            result = self._git("merge-base", "origin/main", "HEAD")
            if result.returncode == 0:
                sha = result.stdout.strip()
                if sha:
                    return sha
        except Exception as exc:
            _log.debug("NotesAdapter.resolve_merge_base: merge-base failed: %s", exc)

        # Fall back to root commit
        try:
            result = self._git("rev-list", "--max-parents=0", "HEAD")
            if result.returncode == 0:
                sha = result.stdout.strip().splitlines()[0]
                if sha:
                    _log.debug(
                        "NotesAdapter.resolve_merge_base: fell back to root commit %s",
                        sha,
                    )
                    return sha
        except Exception as exc:
            _log.debug("NotesAdapter.resolve_merge_base: root-commit fallback failed: %s", exc)

        return ""

    def resolve_branch(self) -> str:
        """Return the current branch name, or empty string on failure (detached HEAD etc.)."""
        try:
            result = self._git("rev-parse", "--abbrev-ref", "HEAD")
            if result.returncode == 0:
                branch = result.stdout.strip()
                # ``git rev-parse --abbrev-ref HEAD`` returns "HEAD" when detached
                if branch and branch != "HEAD":
                    return branch
        except Exception as exc:
            _log.debug("NotesAdapter.resolve_branch: %s", exc)
        return ""
