"""Memory decay helper for the Bead memory system.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

``decay_beads()`` is a thin wrapper around :meth:`BeadStore.decay` that
converts the hours-based TTL used by the CLI into the days-based TTL
expected by the store, and adds dry-run support so operators can preview
what would be archived before committing.

Decay transitions ``closed`` beads older than the TTL to ``archived``
status.  Archived beads retain their structure (``bead_id``, ``bead_type``,
``summary``, ``links``) but their verbose ``content`` is replaced by a
compact archival marker, freeing context budget for future agents.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore

_log = logging.getLogger(__name__)

# Default TTL: 7 days = 168 hours.
_DEFAULT_TTL_HOURS = 168


def decay_beads(
    bead_store: "BeadStore",
    ttl_hours: int = _DEFAULT_TTL_HOURS,
    task_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Archive closed beads older than *ttl_hours*.

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    Converts the hours-based *ttl_hours* into the days-based parameter
    expected by :meth:`BeadStore.decay`.  When *dry_run* is ``True`` the
    count of eligible beads is returned but no rows are modified.

    Args:
        bead_store: Live :class:`~agent_baton.core.engine.bead_store.BeadStore`.
        ttl_hours: Closed beads older than this many hours are archived.
            Defaults to 168 (7 days).
        task_id: If given, only archive beads from this execution.
        dry_run: When ``True``, return the eligible count without modifying
            any rows.

    Returns:
        Number of beads archived (or eligible, when *dry_run* is ``True``).
        Returns ``0`` on any error or when the bead store is unavailable.
    """
    if bead_store is None:
        return 0
    try:
        if dry_run:
            return _count_eligible(bead_store, ttl_hours, task_id)
        # Convert hours to fractional days (store uses days).
        max_age_days_float = ttl_hours / 24.0
        # Round up to the nearest integer day to avoid premature archiving.
        import math
        max_age_days = max(1, math.ceil(max_age_days_float))
        count = bead_store.decay(max_age_days=max_age_days, task_id=task_id)
        _log.debug(
            "decay_beads: archived %d bead(s) (ttl=%dh, task=%s)",
            count, ttl_hours, task_id or "all",
        )
        return count
    except Exception as exc:
        _log.warning("decay_beads failed (non-fatal): %s", exc)
        return 0


def _count_eligible(
    bead_store: "BeadStore",
    ttl_hours: int,
    task_id: str | None,
) -> int:
    """Return the number of closed beads that would be archived by decay."""
    from datetime import datetime, timedelta, timezone
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        closed = bead_store.query(
            task_id=task_id, status="closed", limit=10000
        )
        eligible = [
            b for b in closed
            if b.closed_at and b.closed_at < cutoff_str
        ]
        return len(eligible)
    except Exception:
        return 0
