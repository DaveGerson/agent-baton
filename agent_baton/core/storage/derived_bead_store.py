"""Rebuildable projection DB for derived bead analytics (ADR-13b WP-1 §2).

:class:`DerivedBeadStore` manages a small SQLite database at
``.claude/team-context/baton-derived.db`` that holds the three analytics
tables that have no equivalent in the ``bd`` backend:

- ``bead_edges`` — typed similarity / conflict edges (file_overlap, tag_overlap,
  conflict) produced by :class:`~agent_baton.core.intel.bead_synthesizer.BeadSynthesizer`.
- ``bead_clusters`` — connected components over high-weight file_overlap edges.
- ``handoff_beads`` — compact (≤400 char) handoff summaries persisted by
  :class:`~agent_baton.core.intel.handoff_synthesizer.HandoffSynthesizer`.

The database is a **rebuildable, disposable cache** — it is created on init
with no migration-backup ceremony.  Deleting it and running
``baton beads synthesize`` is always a safe recovery path.

Bead IDs are plain TEXT columns with no FK constraints so the store can be
populated regardless of which bead backend (SQLite or bd) is active.

DDL is moved verbatim from the corresponding migration blocks in
:mod:`agent_baton.core.storage.schema` (v28 / v29).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL — moved verbatim from schema.py v28 / v29 (no FK constraints).
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS bead_edges (
    src_bead_id  TEXT NOT NULL,
    dst_bead_id  TEXT NOT NULL,
    edge_type    TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 0.0,
    created_at   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (src_bead_id, dst_bead_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_bead_edges_src  ON bead_edges(src_bead_id);
CREATE INDEX IF NOT EXISTS idx_bead_edges_dst  ON bead_edges(dst_bead_id);
CREATE INDEX IF NOT EXISTS idx_bead_edges_type ON bead_edges(edge_type);

CREATE TABLE IF NOT EXISTS bead_clusters (
    cluster_id  TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    bead_ids    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS handoff_beads (
    handoff_id   TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL DEFAULT '',
    from_step_id TEXT NOT NULL DEFAULT '',
    to_step_id   TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_handoff_beads_task ON handoff_beads(task_id);
CREATE INDEX IF NOT EXISTS idx_handoff_beads_from ON handoff_beads(from_step_id);
CREATE INDEX IF NOT EXISTS idx_handoff_beads_to   ON handoff_beads(to_step_id);
"""


class DerivedBeadStore:
    """Thin SQLite store for bead analytics (edges, clusters, handoffs).

    Args:
        db_path: Path to ``baton-derived.db``.  Conventionally located at
            ``<project>/.claude/team-context/baton-derived.db`` alongside
            ``baton.db``.  Created (with the required schema) on first
            :meth:`connection` call.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _apply_schema(self) -> None:
        """Create the derived DB and ensure all required tables exist."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.executescript(_DDL)
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "DerivedBeadStore: schema init failed for %s: %s",
                self._db_path, exc,
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield an open :class:`sqlite3.Connection` for reading or writing.

        The connection is committed and closed when the context exits normally.
        On exception, changes are rolled back before closing.

        Usage::

            with derived.connection() as conn:
                rows = conn.execute("SELECT * FROM bead_edges").fetchall()
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def edges_for(self, bead_ids: list[str]) -> list[dict]:
        """Return all edges where either endpoint is in *bead_ids*.

        Args:
            bead_ids: List of bead_id strings to look up.

        Returns:
            List of dicts with keys ``src_bead_id``, ``dst_bead_id``,
            ``edge_type``, ``weight``, ``created_at``.  Empty list on any
            error.
        """
        if not bead_ids:
            return []
        try:
            placeholders = ",".join("?" * len(bead_ids))
            sql = (
                f"SELECT src_bead_id, dst_bead_id, edge_type, weight, created_at "
                f"FROM bead_edges "
                f"WHERE src_bead_id IN ({placeholders}) "
                f"   OR dst_bead_id IN ({placeholders})"
            )
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(sql, bead_ids + bead_ids).fetchall()
            conn.close()
            return [
                {
                    "src_bead_id": r[0],
                    "dst_bead_id": r[1],
                    "edge_type": r[2],
                    "weight": r[3],
                    "created_at": r[4],
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning("DerivedBeadStore.edges_for failed: %s", exc)
            return []

    def clusters(self) -> list[dict]:
        """Return all bead clusters.

        Returns:
            List of dicts with keys ``cluster_id``, ``label``,
            ``bead_ids`` (JSON-encoded list string), ``created_at``.
            Empty list on any error.
        """
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT cluster_id, label, bead_ids, created_at FROM bead_clusters"
            ).fetchall()
            conn.close()
            return [
                {
                    "cluster_id": r[0],
                    "label": r[1],
                    "bead_ids": r[2],
                    "created_at": r[3],
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning("DerivedBeadStore.clusters failed: %s", exc)
            return []

    def handoffs(self, task_id: str) -> list[dict]:
        """Return handoff beads for *task_id*, ordered by creation time.

        Args:
            task_id: The task to scope the query to.

        Returns:
            List of dicts with keys ``handoff_id``, ``task_id``,
            ``from_step_id``, ``to_step_id``, ``content``, ``created_at``.
            Empty list on any error or when *task_id* is empty.
        """
        if not task_id:
            return []
        try:
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute(
                "SELECT handoff_id, task_id, from_step_id, to_step_id, "
                "content, created_at "
                "FROM handoff_beads WHERE task_id = ? "
                "ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            conn.close()
            return [
                {
                    "handoff_id": r[0],
                    "task_id": r[1],
                    "from_step_id": r[2],
                    "to_step_id": r[3],
                    "content": r[4],
                    "created_at": r[5],
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning("DerivedBeadStore.handoffs failed: %s", exc)
            return []
