"""Pure helper for executable-bead script hashing (ADR-13b, WP-1 §C).

Relocated from :class:`~agent_baton.core.engine.notes_adapter.NotesAdapter`
so that exec-side code (``runner.py``, ``bead_cmd.py``) can compute script
SHAs without importing the git-notes layer.

:func:`compute_script_sha` and :func:`script_ref_for` are kept as static
methods on ``NotesAdapter`` as re-export shims so any existing callers that
import from ``notes_adapter`` continue to work unchanged.
"""
from __future__ import annotations

import hashlib

# Canonical git-notes ref prefix for content-addressed script storage.
_SCRIPTS_REF_PREFIX = "refs/notes/baton-bead-scripts"


def compute_script_sha(script_body: str) -> str:
    """Return the SHA-256 hex digest of *script_body* (UTF-8 encoded).

    Args:
        script_body: The full script text.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(script_body.encode("utf-8")).hexdigest()


def script_ref_for(content_sha: str) -> str:
    """Return the canonical sub-ref string for a script keyed by *content_sha*.

    Args:
        content_sha: SHA-256 hex digest returned by :func:`compute_script_sha`.

    Returns:
        A string of the form ``refs/notes/baton-bead-scripts/<sha>``.
    """
    return f"{_SCRIPTS_REF_PREFIX}/{content_sha}"
