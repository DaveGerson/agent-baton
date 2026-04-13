"""SQLite-backed persistence and query engine for Bead memory.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

``BeadStore`` owns all reads and writes to the ``beads`` and ``bead_tags``
tables defined in ``core/storage/schema.py`` (added in schema v4).  It uses
the same ``ConnectionManager`` pattern as ``SqliteStorage`` — one connection
per thread, WAL mode, schema applied on first access.

Design invariants:
- All SQL uses parameterised queries (no f-string interpolation of values).
- ``write()`` writes ``beads`` and ``bead_tags`` in a single transaction.
- If the database is unavailable or the table does not yet exist, every
  method returns a safe empty/None value and logs a warning rather than
  raising.  This supports graceful degradation when running against an
  older schema.

See ``docs/superpowers/specs/2026-04-12-bead-memory-design.md`` for the
full design rationale and ``models/bead.py`` for the data model.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.bead import Bead, BeadLink

_log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BeadStore:
    """SQLite-backed bead persistence and query engine.

    Wraps the ``beads`` and ``bead_tags`` tables introduced in schema v4.
    All operations degrade gracefully when the tables are absent (older
    schema) — they return ``None`` / ``[]`` / ``0`` without raising.

    Args:
        db_path: Absolute path to the project's ``baton.db``.
    """

    def __init__(self, db_path: Path) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):  # type: ignore[return]
        return self._conn_mgr.get_connection()

    def _table_exists(self) -> bool:
        """Return True if the beads table exists (schema v4 applied)."""
        try:
            row = self._conn().execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='beads'"
            ).fetchone()
            return row is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, bead: "Bead") -> str:  # noqa: F821
        """Persist *bead* and its normalised ``bead_tags`` rows.

        Both writes occur in a single transaction.  If the bead already
        exists (same ``bead_id``) the row is replaced.

        Args:
            bead: The :class:`~agent_baton.models.bead.Bead` to persist.

        Returns:
            The ``bead_id`` of the written bead, or an empty string on
            failure.
        """
        if not self._table_exists():
            _log.debug("BeadStore.write: beads table not found — skipping")
            return ""
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO beads (
                    bead_id, task_id, step_id, agent_name, bead_type,
                    content, confidence, scope, tags, affected_files,
                    status, created_at, closed_at, summary, links,
                    source, token_estimate
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?
                )
                """,
                (
                    bead.bead_id,
                    bead.task_id,
                    bead.step_id,
                    bead.agent_name,
                    bead.bead_type,
                    bead.content,
                    bead.confidence,
                    bead.scope,
                    json.dumps(bead.tags),
                    json.dumps(bead.affected_files),
                    bead.status,
                    bead.created_at or _utcnow(),
                    bead.closed_at,
                    bead.summary,
                    json.dumps([lnk.to_dict() for lnk in bead.links]),
                    bead.source,
                    bead.token_estimate,
                ),
            )
            # Normalised tag rows — delete existing tags for this bead first
            # so that a replace operation does not leave stale tags.
            conn.execute("DELETE FROM bead_tags WHERE bead_id = ?", (bead.bead_id,))
            for tag in bead.tags:
                conn.execute(
                    "INSERT OR IGNORE INTO bead_tags (bead_id, tag) VALUES (?, ?)",
                    (bead.bead_id, tag),
                )
            conn.commit()
            return bead.bead_id
        except Exception as exc:
            _log.warning("BeadStore.write failed for %s: %s", bead.bead_id, exc)
            return ""

    def read(self, bead_id: str) -> "Bead | None":  # noqa: F821
        """Fetch a single bead by ID.

        Args:
            bead_id: The ``bead_id`` to look up.

        Returns:
            The :class:`~agent_baton.models.bead.Bead` or ``None`` if not
            found (or if the table does not exist).
        """
        if not self._table_exists():
            return None
        try:
            row = self._conn().execute(
                "SELECT * FROM beads WHERE bead_id = ?", (bead_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_bead(row)
        except Exception as exc:
            _log.warning("BeadStore.read failed for %s: %s", bead_id, exc)
            return None

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_name: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> "list[Bead]":  # noqa: F821
        """Filtered search with AND semantics, ordered by ``created_at DESC``.

        All filter parameters are optional.  When *tags* is provided every
        tag in the list must be present on the returned bead (AND semantics,
        implemented via a ``bead_tags`` subquery).

        Args:
            task_id: Filter to beads from a specific execution.
            agent_name: Filter to beads produced by a specific agent.
            bead_type: Filter to a specific bead type (e.g. ``"warning"``).
            status: Filter to a specific status (e.g. ``"open"``).
            tags: Only return beads that have ALL of these tags.
            limit: Maximum number of results to return.

        Returns:
            List of matching :class:`~agent_baton.models.bead.Bead` objects,
            newest first.
        """
        if not self._table_exists():
            return []
        try:
            conditions: list[str] = []
            params: list[object] = []

            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)
            if agent_name is not None:
                conditions.append("agent_name = ?")
                params.append(agent_name)
            if bead_type is not None:
                conditions.append("bead_type = ?")
                params.append(bead_type)
            if status is not None:
                conditions.append("status = ?")
                params.append(status)

            if tags:
                # AND semantics: bead must have every tag in the list.
                placeholders = ", ".join("?" * len(tags))
                conditions.append(
                    f"bead_id IN ("
                    f"  SELECT bead_id FROM bead_tags WHERE tag IN ({placeholders})"
                    f"  GROUP BY bead_id HAVING COUNT(DISTINCT tag) = ?"
                    f")"
                )
                params.extend(tags)
                params.append(len(tags))

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = (
                f"SELECT * FROM beads {where} "
                f"ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)

            rows = self._conn().execute(sql, params).fetchall()
            return [self._row_to_bead(r) for r in rows]
        except Exception as exc:
            _log.warning("BeadStore.query failed: %s", exc)
            return []

    def ready(self, task_id: str) -> "list[Bead]":  # noqa: F821
        """Return open beads whose ``blocked_by`` dependencies are all satisfied.

        A bead is "ready" when it is ``open`` and every bead it depends on
        via a ``blocked_by`` link is no longer ``open`` (i.e. ``closed`` or
        ``archived``).

        Args:
            task_id: Scope the search to a specific execution.

        Returns:
            List of ready :class:`~agent_baton.models.bead.Bead` objects.
        """
        open_beads = self.query(task_id=task_id, status="open", limit=1000)
        result: list[Bead] = []  # type: ignore[name-defined]
        for bead in open_beads:
            blocked_by_ids = [
                lnk.target_bead_id
                for lnk in bead.links
                if lnk.link_type == "blocked_by"
            ]
            if not blocked_by_ids:
                result.append(bead)
                continue
            # Check that all blocking beads are closed/archived
            is_blocked = False
            for blocking_id in blocked_by_ids:
                blocking = self.read(blocking_id)
                if blocking is not None and blocking.status == "open":
                    is_blocked = True
                    break
            if not is_blocked:
                result.append(bead)
        return result

    def close(self, bead_id: str, summary: str) -> None:
        """Close a bead with a compacted summary.

        Sets ``status`` to ``"closed"`` and records the ISO 8601 timestamp
        in ``closed_at``.  Idempotent — closing an already-closed bead is
        a no-op.

        Args:
            bead_id: The bead to close.
            summary: A one-line compacted description of the bead's outcome.
        """
        if not self._table_exists():
            return
        try:
            now = _utcnow()
            conn = self._conn()
            conn.execute(
                """
                UPDATE beads
                SET status = 'closed', closed_at = ?, summary = ?
                WHERE bead_id = ? AND status = 'open'
                """,
                (now, summary, bead_id),
            )
            conn.commit()
        except Exception as exc:
            _log.warning("BeadStore.close failed for %s: %s", bead_id, exc)

    def link(self, source_id: str, target_id: str, link_type: str) -> None:
        """Add a typed link from *source_id* to *target_id*.

        The link is stored by appending a :class:`~agent_baton.models.bead.BeadLink`
        to the ``links`` JSON column of the source bead.  Both beads must
        already exist.

        Args:
            source_id: Bead that originates the link.
            target_id: Bead that the link points to.
            link_type: Relationship kind (see :class:`~agent_baton.models.bead.BeadLink`).
        """
        if not self._table_exists():
            return
        try:
            from agent_baton.models.bead import BeadLink

            row = self._conn().execute(
                "SELECT links FROM beads WHERE bead_id = ?", (source_id,)
            ).fetchone()
            if row is None:
                _log.warning("BeadStore.link: source bead %s not found", source_id)
                return
            existing: list[dict] = json.loads(row["links"] or "[]")
            new_link = BeadLink(
                target_bead_id=target_id,
                link_type=link_type,
                created_at=_utcnow(),
            )
            existing.append(new_link.to_dict())
            conn = self._conn()
            conn.execute(
                "UPDATE beads SET links = ? WHERE bead_id = ?",
                (json.dumps(existing), source_id),
            )
            conn.commit()
        except Exception as exc:
            _log.warning(
                "BeadStore.link failed (%s -> %s, %s): %s",
                source_id, target_id, link_type, exc,
            )

    def decay(self, max_age_days: int, task_id: str | None = None) -> int:
        """Archive closed beads older than *max_age_days*.

        Transitions beads from ``closed`` to ``archived`` status.  Archived
        beads retain their structure (``bead_id``, ``bead_type``, ``summary``,
        ``links``) but their verbose ``content`` is replaced by an archival
        marker, freeing context budget.

        Args:
            max_age_days: Closed beads older than this many days are archived.
            task_id: If given, only archive beads from this execution.

        Returns:
            Number of beads archived.
        """
        if not self._table_exists():
            return 0
        try:
            cutoff = datetime.now(timezone.utc)
            from datetime import timedelta
            cutoff_str = (cutoff - timedelta(days=max_age_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            conditions = ["status = 'closed'", "closed_at != ''", "closed_at < ?"]
            params: list[object] = [cutoff_str]
            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)

            where = " AND ".join(conditions)
            conn = self._conn()
            cursor = conn.execute(
                f"UPDATE beads SET status = 'archived', "
                f"content = '[archived — see summary]' "
                f"WHERE {where}",
                params,
            )
            conn.commit()
            count = cursor.rowcount
            _log.debug("BeadStore.decay: archived %d beads", count)
            return count
        except Exception as exc:
            _log.warning("BeadStore.decay failed: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Internal conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_bead(row) -> "Bead":  # noqa: F821
        """Convert a ``sqlite3.Row`` to a :class:`~agent_baton.models.bead.Bead`."""
        from agent_baton.models.bead import Bead, BeadLink

        raw_tags = row["tags"] if row["tags"] else "[]"
        raw_files = row["affected_files"] if row["affected_files"] else "[]"
        raw_links = row["links"] if row["links"] else "[]"

        try:
            tags = json.loads(raw_tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
        try:
            affected_files = json.loads(raw_files)
        except (json.JSONDecodeError, TypeError):
            affected_files = []
        try:
            links_data = json.loads(raw_links)
            links = [BeadLink.from_dict(d) for d in links_data]
        except (json.JSONDecodeError, TypeError):
            links = []

        return Bead(
            bead_id=row["bead_id"],
            task_id=row["task_id"],
            step_id=row["step_id"],
            agent_name=row["agent_name"],
            bead_type=row["bead_type"],
            content=row["content"] or "",
            confidence=row["confidence"] or "medium",
            scope=row["scope"] or "step",
            tags=tags,
            affected_files=affected_files,
            status=row["status"] or "open",
            created_at=row["created_at"] or "",
            closed_at=row["closed_at"] or "",
            summary=row["summary"] or "",
            links=links,
            source=row["source"] or "agent-signal",
            token_estimate=int(row["token_estimate"] or 0),
        )
