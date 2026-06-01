"""Pure helper for executable-bead script hashing (ADR-13b WP-G).

Script SHAs are content-addressed identifiers used to detect tampering.
After ADR-13b WP-G, script bodies are stored exclusively in the bd bead
metadata blob — the git-notes layer (NotesAdapter) has been removed.
"""
from __future__ import annotations

import hashlib

# Canonical ref-style prefix for content-addressed script identity (used as an
# opaque identifier string; git notes are no longer written).
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
