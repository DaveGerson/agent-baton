"""Bead-store backend factory (ADR-13b WP-G — bd mandatory).

After the ADR-13b teardown the ``bd`` CLI is the **only** bead backend.
:func:`make_bead_store` always returns :class:`BdBeadStore`; it raises
:class:`~agent_baton.core.engine.bd_client.BdNotAvailable` when the ``bd``
binary cannot be found.

The legacy ``BATON_BD_BACKEND`` environment variable is accepted but if set
to anything other than ``"bd"`` a deprecation warning is logged.  The
``gastown_dual_write`` parameter has been removed — the Gastown git-notes
infrastructure was deleted in WP-G.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)

_BACKEND_ENV = "BATON_BD_BACKEND"

# bd-7is: ~20 call sites construct the bead store through a
# ``try/except -> bead_store = None`` wrapper at *debug* level (by design --
# a missing bd binary must never crash a caller that can degrade). That
# means, pre-fix, a missing ``bd`` binary was invisible unless someone
# happened to run ``baton doctor`` or grep debug logs. Rather than touching
# every call site, emit exactly ONE process-wide WARNING here, at the single
# seam all of them funnel through, the first time construction fails.
# Subsequent failures in the same process stay at debug level so a busy
# session doesn't spam the log once the operator has been told.
_warned_bd_unavailable = False


def selected_backend() -> str:
    """Return ``"bd"`` — the only supported bead backend after WP-G.

    If ``BATON_BD_BACKEND`` is set to ``sqlite`` or ``auto`` a deprecation
    warning is logged and ``"bd"`` is returned anyway.
    """
    val = os.environ.get(_BACKEND_ENV, "").strip().lower()
    if val and val != "bd":
        _log.warning(
            "BATON_BD_BACKEND=%r is deprecated after ADR-13b WP-G; "
            "the SQLite bead store has been removed.  "
            "Only 'bd' is supported.  Ignoring and using 'bd'.",
            val,
        )
    return "bd"


def make_bead_store(
    db_path: Path,
    *,
    soul_router=None,
    repo_root: Path | None = None,
):
    """Construct a :class:`~agent_baton.core.engine.bd_bead_store.BdBeadStore`.

    Args:
        db_path: Path to the project ``baton.db`` (used to locate the
            project root / ``.beads/`` for the bd backend).
        soul_router: Accepted but ignored (was SQLite-backend only).
        repo_root: Project root that owns ``.beads/``; derived from
            ``db_path`` when not supplied.

    Returns:
        A :class:`~agent_baton.core.engine.bd_bead_store.BdBeadStore`.

    Raises:
        :class:`~agent_baton.core.engine.bd_client.BdNotAvailable`: When
            the ``bd`` binary is not on PATH.
    """
    from agent_baton.core.engine.bd_client import BdClient, BdNotAvailable

    root = repo_root or _derive_repo_root(db_path)
    client = BdClient(root)

    if not client.available():
        global _warned_bd_unavailable
        if not _warned_bd_unavailable:
            _log.warning(
                "bd unavailable — incidents/retrospectives will not be "
                "recorded; install bd or set BATON_BD_BIN"
            )
            _warned_bd_unavailable = True
        else:
            _log.debug("bd unavailable (repeat construction failure; see earlier warning)")
        raise BdNotAvailable(
            "The 'bd' CLI is required after ADR-13b WP-G but was not found "
            "on PATH.  Install it with:  npm install -g @beads/bd  "
            "(or)  brew install beads"
        )

    from agent_baton.core.engine.bd_bead_store import BdBeadStore

    _log.debug("Bead backend: bd (repo=%s)", root)
    return BdBeadStore(client)


def _derive_repo_root(db_path: Path) -> Path:
    """Walk upward from *db_path* to a directory containing ``.git``/``.beads``.

    Falls back to the db's grandparent (``.claude/team-context`` → project root)
    ONLY when *db_path* actually follows that ``<root>/.claude/team-context/
    baton.db`` convention -- otherwise falls back to the db's own parent
    directory instead.

    bd-p6k: the old unconditional ``db_path.parent.parent.parent`` fallback
    assumed every caller's ``db_path`` follows the 3-level convention. Callers
    that pass a shallower path (e.g. ``<dir>/baton.db``, one level deep) got
    a *different directory three levels above db_path* -- which, for a path
    like ``/tmp/pytest-of-*/pytest-N/<test>/baton.db``, overshoots past the
    test's own tmp_path into ``/tmp/pytest-of-*`` (the pytest session's
    *shared* base temp dir). :func:`make_bead_store` then constructs a
    ``BdClient`` rooted there, and the first bead write's lazy ``bd init``
    plants a stray ``.git``/``.beads`` in that shared ancestor -- which
    every *other* test's ``tmp_path`` (a descendant of the same shared base)
    then discovers via git's upward repo search, corrupting unrelated
    "not in a git repo" assertions for the rest of the pytest session. See
    ``tests/test_worktree_manager.py::TestEngineFallbackWhenDisabled::
    test_non_git_root_auto_disables_manager`` and
    ``TestCanonicalRepoPorcelainParse::test_resolve_canonical_repo_raises_on_non_git_dir``,
    which fail on Linux CI purely because a *different, earlier* test in the
    same session poisoned the shared tmp base this way.

    Falling back to ``db_path.parent`` instead is always safe: it can never
    escape the directory the caller explicitly pointed ``db_path`` at.
    """
    candidate = db_path.parent
    for _ in range(10):
        if (candidate / ".git").exists() or (candidate / ".beads").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    # .claude/team-context/baton.db -> project root is two levels up, but
    # only trust that shape when db_path actually matches it (bd-p6k).
    try:
        team_context = db_path.parent
        claude_dir = team_context.parent
        if team_context.name == "team-context" and claude_dir.name == ".claude":
            return claude_dir.parent
        return db_path.parent
    except Exception:
        return db_path.parent
