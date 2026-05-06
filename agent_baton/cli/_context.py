"""Shared context-root resolution for CLI and API modules.

The :func:`resolve_context_root` function locates the
``.claude/team-context/`` directory that anchors all execution state,
plans, and ``baton.db`` for a project.

This utility is intentionally kept separate from the private copy in
``agent_baton.cli.commands.execution.execute`` -- that module is on
the critical execution path and should not import from here.  The two
implementations must stay in sync.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_context_root() -> Path:
    """Resolve the team-context root to an absolute path.

    Strategy:
        1. Ask ``git rev-parse --show-toplevel`` for the repo root,
           then check for ``.claude/team-context/`` beneath it.
        2. Walk up from ``Path.cwd()`` looking for the marker directory.
        3. Fall back to ``Path.cwd() / ".claude/team-context"`` (allows
           bootstrapping a fresh project).

    Returns:
        An absolute ``Path`` to the ``.claude/team-context/`` directory.
    """
    # Fastest path: ask git for the repo root.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            ctx = Path(result.stdout.strip()) / ".claude" / "team-context"
            if ctx.is_dir():
                return ctx.resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: walk up the directory tree.
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context"
        if candidate.is_dir():
            return candidate.resolve()

    # Last resort: relative to CWD (allows bootstrap of fresh project).
    return (cwd / ".claude" / "team-context").resolve()
