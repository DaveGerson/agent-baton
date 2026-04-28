"""SQLite-backed index mapping bead_id → anchor_commit for O(1) git-notes lookup.

Part A of the Gastown bead architecture (bd-2870).

The ``bead_anchors`` table lives in the per-project ``baton.db`` alongside all
other project tables.  It is created by the v31 schema migration (or by the
``ensure_table`` helper for graceful degradation on pre-v31 databases).

Design invariants:
- All SQL uses parameterised queries (no f-string interpolation of values).
- ``put`` is an upsert — idempotent by design.
- ``rebuild_from_notes`` is the disaster-recovery path: it drops and repopulates
  the entire index from a live ``NotesAdapter.list()`` scan.  It returns the row
  count so callers can log/report the outcome.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.notes_adapter import NotesAdapter

_log = logging.getLogger(__name__)

# DDL for the bead_anchors table and its index.
# Kept here (not only in schema.py) so that BeadAnchorIndex can create the
# table lazily on databases that pre-date v31 without importing the full schema.
_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bead_anchors (
    bead_id        TEXT PRIMARY KEY,
    anchor_commit  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_bead_anchors_commit ON bead_anchors(anchor_commit);
"""


class BeadAnchorIndex:
    """O(1) bead_id → anchor_commit lookup backed by SQLite.

    Lives alongside the existing per-project ``baton.db`` tables.  The table is
    created via ``CREATE TABLE IF NOT EXISTS`` so ``BeadAnchorIndex`` is safe to
    instantiate even against a pre-v31 database — the table will be created on
    first use.

    Args:
        conn: An open ``sqlite3.Connection``.  The caller (``BeadStore``) owns
            the connection lifecycle.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._ensure_table()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """Create ``bead_anchors`` and its index if they don't exist yet."""
        try:
            for statement in _CREATE_TABLE_DDL.strip().split(";"):
                stmt = statement.strip()
                if stmt:
                    self._conn.execute(stmt)
            self._conn.commit()
        except Exception as exc:
            _log.warning("BeadAnchorIndex._ensure_table failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, bead_id: str) -> str | None:
        """Return the anchor commit SHA for *bead_id*, or ``None`` if not found.

        Args:
            bead_id: The bead ID to look up.

        Returns:
            The anchor commit SHA string, or ``None`` when not in the index.
        """
        try:
            row = self._conn.execute(
                "SELECT anchor_commit FROM bead_anchors WHERE bead_id = ?",
                (bead_id,),
            ).fetchone()
            if row is not None:
                return row[0]
            return None
        except Exception as exc:
            _log.debug("BeadAnchorIndex.get failed for %s: %s", bead_id, exc)
            return None

    def put(self, bead_id: str, anchor_commit: str) -> None:
        """Insert or update the anchor commit for *bead_id*.

        Idempotent — calling ``put`` twice with the same arguments is safe.

        Args:
            bead_id: Bead identifier.
            anchor_commit: The commit SHA to associate with this bead.
        """
        if not bead_id or not anchor_commit:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO bead_anchors (bead_id, anchor_commit, last_seen_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(bead_id) DO UPDATE SET
                    anchor_commit = excluded.anchor_commit,
                    last_seen_at  = excluded.last_seen_at
                """,
                (bead_id, anchor_commit),
            )
            self._conn.commit()
        except Exception as exc:
            _log.debug("BeadAnchorIndex.put failed for %s: %s", bead_id, exc)

    def rebuild_from_notes(self, adapter: "NotesAdapter") -> int:
        """Drop all rows and repopulate from *adapter*.

        Walks ``NotesAdapter.list()`` (which scans ``refs/notes/baton-beads``)
        and writes one row per ``(anchor_commit, bead_id)`` pair.  This is the
        disaster-recovery path for when the SQLite index drifts from notes.

        Args:
            adapter: A ``NotesAdapter`` instance pointing at the same repo.

        Returns:
            Number of rows written.
        """
        try:
            self._conn.execute("DELETE FROM bead_anchors")
            self._conn.commit()
        except Exception as exc:
            _log.warning("BeadAnchorIndex.rebuild_from_notes: delete failed: %s", exc)
            return 0

        pairs = adapter.list()
        count = 0
        for anchor_commit, bead_id in pairs:
            self.put(bead_id, anchor_commit)
            count += 1

        _log.debug("BeadAnchorIndex.rebuild_from_notes: wrote %d rows", count)
        return count
