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
