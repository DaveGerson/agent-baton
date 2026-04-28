"""Git-notes replication configuration helpers (end-user readiness #5).

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


def maybe_warn_replication(repo_path: Path) -> None:
    """Emit a one-time BEAD_WARNING if notes replication is not configured.

    Does nothing when:
    - The warning has already been emitted this session.
    - ``BATON_SKIP_GIT_NOTES_SETUP=1`` is set in the environment.
    - Notes replication is already configured.

    Args:
        repo_path: Absolute path to the git repository root.
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
