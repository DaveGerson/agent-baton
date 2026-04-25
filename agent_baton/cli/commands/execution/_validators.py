"""Shared step-ID validators for ``baton execute`` subcommands.

Step IDs come in two shapes:

* **Plain step**:  ``"N.N"`` — e.g. ``"1.1"``, ``"7.3"``.
* **Team-member**: ``"N.N.x"`` (and nested ``"N.N.x.y..."``) — emitted by the
  executor when a parent team step is fanned out to individual members.

Several CLI subcommands (``dispatched``, ``record``, ``team-record`` ...)
previously each defined their own regex.  ``next`` and ``next --all`` emit
team-member IDs but the recording subcommands rejected them, producing a
DX defect (bd-e201).  This module is the single source of truth for the
accepted shapes.

Usage:

    from agent_baton.cli.commands.execution._validators import (
        STEP_ID_RE,
        TEAM_MEMBER_ID_RE,
        is_team_member_id,
        validate_step_id,
    )
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# Plain step (no team-member suffix).
PLAIN_STEP_ID_RE = re.compile(r'^\d+\.\d+$')

# Team-member ID — accepts both single-level (``1.1.a``) and arbitrarily-nested
# sub-team forms (``1.1.a.b``, ``1.1.a.b.c``).  Each suffix segment must be a
# run of one or more lowercase letters.
TEAM_MEMBER_ID_RE = re.compile(r'^\d+\.\d+(?:\.[a-z]+)+$')

# Unified regex accepted by every ``--step`` argument across the execute
# subcommands.  Anything emitted by ``baton execute next`` / ``next --all``
# must satisfy this regex.
STEP_ID_RE = re.compile(r'^\d+\.\d+(?:\.[a-z]+)*$')

# Human-readable description used in error messages.
STEP_ID_FORMAT_HINT = (
    "expected format: 'N.N' (plain step, e.g. '1.1') or 'N.N.x' "
    "(team-member, e.g. '1.1.a' / '1.1.a.b' for nested teams)"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_team_member_id(step_id: str) -> bool:
    """Return True if *step_id* is a team-member ID (e.g. ``"1.1.a"``)."""
    return bool(TEAM_MEMBER_ID_RE.match(step_id))


def is_plain_step_id(step_id: str) -> bool:
    """Return True if *step_id* is a plain step ID (e.g. ``"1.1"``)."""
    return bool(PLAIN_STEP_ID_RE.match(step_id))


def is_valid_step_id(step_id: str) -> bool:
    """Return True if *step_id* is any accepted form."""
    return bool(STEP_ID_RE.match(step_id))


def parent_step_id(step_id: str) -> str:
    """For a team-member ID like ``"1.1.a.b"`` return the parent ``"1.1"``.

    For a plain step ID return it unchanged.
    """
    if not step_id:
        return step_id
    parts = step_id.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return step_id


def validate_step_id(step_id: str, validation_error_fn) -> None:
    """Validate *step_id* and call *validation_error_fn* on failure.

    *validation_error_fn* should be the CLI's ``validation_error`` (which
    raises / exits).  This helper exists so callers can stay one-liners
    while keeping a consistent error message.
    """
    if not is_valid_step_id(step_id):
        validation_error_fn(
            f"invalid step ID '{step_id}' ({STEP_ID_FORMAT_HINT})"
        )
