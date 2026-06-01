"""Bead-store backend selector (ADR-13b staged migration).

Returns either the legacy SQLite :class:`BeadStore` or the new ``bd``-backed
:class:`BdBeadStore`, based on ``BATON_BD_BACKEND``:

- ``sqlite`` (current default) — legacy SQLite store. Keeps every existing
  consumer and test green while the remaining consumers (synthesizer, PMO UI,
  executable beads, sync) are migrated off SQLite.
- ``bd``    — force the ``bd`` backend (used by the new bd tests and by
  operators who have completed the cutover).
- ``auto``  — use ``bd`` when it is enabled (:func:`bd_enabled`) *and* the
  binary is available, otherwise fall back to SQLite.

The default will flip to ``auto`` once the consumer migration lands (the final
phase of ADR-13b), at which point a fresh install — whose installer has placed
``bd`` on PATH — runs entirely off beads.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)

_BACKEND_ENV = "BATON_BD_BACKEND"
# ADR-13b step F — behavioural cutover: default to ``auto``.  When ``bd`` is
# enabled (BATON_BD_ENABLED!=0) and the binary is present (the installer puts
# it there), the engine runs off beads; otherwise it falls back to the SQLite
# store so environments without ``bd`` (some CI) keep working.
_DEFAULT_BACKEND = "auto"


def selected_backend() -> str:
    """Return the configured backend name: ``sqlite`` | ``bd`` | ``auto``."""
    val = os.environ.get(_BACKEND_ENV, "").strip().lower()
    return val if val in ("sqlite", "bd", "auto") else _DEFAULT_BACKEND


def make_bead_store(
    db_path: Path,
    *,
    soul_router=None,
    repo_root: Path | None = None,
    gastown_dual_write: bool = False,
):
    """Construct the appropriate bead store for the current configuration.

    Args:
        db_path: Path to the project ``baton.db`` (used by the SQLite backend
            and to locate the project root / ``.beads/`` for the bd backend).
        soul_router: Optional SoulRouter (SQLite backend only).
        repo_root: Project root that owns ``.beads/``; derived from ``db_path``
            when not supplied.
        gastown_dual_write: Forwarded to the SQLite backend (git-notes mirror).

    Returns:
        A bead store exposing the standard ``write``/``read``/``query``/
        ``ready``/``close``/``annotate``/``link`` surface.
    """
    backend = selected_backend()
    root = repo_root or _derive_repo_root(db_path)

    if backend in ("bd", "auto"):
        from agent_baton.core.engine.bd_client import BdClient, bd_enabled

        client = BdClient(root)
        if backend == "bd" or (bd_enabled() and client.available()):
            from agent_baton.core.engine.bd_bead_store import BdBeadStore

            _log.debug("Bead backend: bd (repo=%s)", root)
            return BdBeadStore(client)
        _log.debug("Bead backend: auto fell back to sqlite (bd unavailable/disabled)")

    from agent_baton.core.engine.bead_store import BeadStore

    return BeadStore(
        db_path,
        soul_router=soul_router,
        repo_root=repo_root,
        gastown_dual_write=gastown_dual_write,
    )


def _derive_repo_root(db_path: Path) -> Path:
    """Walk upward from *db_path* to a directory containing ``.git``/``.beads``.

    Falls back to the db's grandparent (``.claude/team-context`` → project root)
    and finally the db parent.
    """
    candidate = db_path.parent
    for _ in range(10):
        if (candidate / ".git").exists() or (candidate / ".beads").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    # .claude/team-context/baton.db -> project root is two levels up.
    try:
        return db_path.parent.parent.parent
    except Exception:
        return db_path.parent
