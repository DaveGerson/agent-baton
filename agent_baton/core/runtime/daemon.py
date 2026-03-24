"""UNIX double-fork daemonization for agent-baton.

This module provides a single public function :func:`daemonize` that detaches
the calling process from its controlling terminal using the classic double-fork
technique.

Must be called BEFORE ``asyncio.run()``.  Calling it inside an already-running
event loop is undefined behaviour.
"""
from __future__ import annotations

import os
import sys


def daemonize() -> None:
    """Classic UNIX double-fork to detach from controlling terminal.

    Must be called BEFORE asyncio.run().

    Sequence:
    1. First fork — parent exits, child continues
    2. os.setsid() — child becomes session leader
    3. Second fork — session leader exits, grandchild continues
    4. Redirect stdin/stdout/stderr to /dev/null
    5. Preserve working directory (agent-baton uses relative paths)

    Raises:
        RuntimeError: On Windows (daemonization requires POSIX).
        RuntimeError: If either fork() call fails.
    """
    if sys.platform == "win32":
        raise RuntimeError(
            "Daemonization requires POSIX (Linux/macOS). Use --foreground on Windows."
        )

    # ── First fork ───────────────────────────────────────────────────────────
    # Parent exits so the child is adopted by init (PID 1).  This lets the
    # shell return immediately.
    try:
        pid = os.fork()
    except OSError as exc:
        raise RuntimeError(f"First fork failed: {exc}") from exc
    if pid > 0:
        os._exit(0)  # Parent exits

    # ── Child becomes session leader ─────────────────────────────────────────
    # setsid() creates a new session and detaches from the controlling terminal.
    os.setsid()

    # ── Second fork ──────────────────────────────────────────────────────────
    # The session leader (child after setsid) can reacquire a controlling
    # terminal by opening a terminal device.  Forking again ensures the
    # grandchild is NOT a session leader and therefore can never reacquire a
    # controlling terminal.
    try:
        pid = os.fork()
    except OSError as exc:
        raise RuntimeError(f"Second fork failed: {exc}") from exc
    if pid > 0:
        os._exit(0)  # Session leader exits

    # ── Grandchild: redirect standard file descriptors to /dev/null ──────────
    # IMPORTANT: Only redirect FDs 0, 1, and 2.  Do NOT close higher FDs —
    # they may include logging FileHandler FDs and PID file flock FDs that must
    # remain open for the daemon's lifetime.
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)  # stdin  → /dev/null
    os.dup2(devnull, 1)  # stdout → /dev/null
    os.dup2(devnull, 2)  # stderr → /dev/null
    if devnull > 2:
        os.close(devnull)
    # Working directory is intentionally preserved — agent-baton resolves
    # project-relative paths (e.g. .claude/team-context/) from cwd.
