"""Knowledge lifecycle service — freshness, deprecation, retirement.

Knowledge documents live on disk under ``.claude/knowledge/<pack>/``.
This service tracks lifecycle metadata for each ``"<pack_name>/<doc_name>"``
key in the ``knowledge_items`` SQLite table and provides the operations
behind the ``baton knowledge`` CLI.

Velocity-positive defaults:
    * ``mark_deprecated`` is the only thing that flips an item out of
      ``active`` -- there is no auto-deprecation by age.
    * ``find_stale`` is purely informational; it returns candidates and
      never mutates state.
    * ``auto_retire_expired`` only retires items the user already
      deprecated whose grace period has elapsed.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

# Sentinel timestamps stored in TEXT columns are written as empty strings
# (matches the schema defaults) so callers can distinguish "never set" from
# any real ISO 8601 value.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Default freshness thresholds: an item is "stale" when both signals agree.
DEFAULT_STALE_DAYS = 90
DEFAULT_MAX_USAGE = 5

# Default deprecation grace period.
DEFAULT_GRACE_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FORMAT)


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _split_id(knowledge_id: str) -> tuple[str, str]:
    """Split ``"pack/doc"`` into ``(pack, doc)``.  Bare ids fall back to
    pack="" so the row remains insertable; callers should use the canonical
    ``"<pack>/<doc>"`` form."""
    if "/" in knowledge_id:
        pack, doc = knowledge_id.split("/", 1)
        return pack, doc
    return "", knowledge_id


class KnowledgeLifecycle:
    """Service object for the ``knowledge_items`` table.

    Args:
        db_path: Path to the project's ``baton.db``.
        stale_days: Default age threshold (in days since last use) above
            which an item is considered for staleness reporting.
        max_usage: Default upper bound on usage_count for staleness
            consideration.  Items at or above this count are never stale.
        clock: Optional callable returning the current UTC datetime.
            Defaults to :func:`datetime.now(timezone.utc)`; tests can
            inject a deterministic clock.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        stale_days: int = DEFAULT_STALE_DAYS,
        max_usage: int = DEFAULT_MAX_USAGE,
        clock=None,
    ) -> None:
        self._db_path = Path(db_path)
        self._stale_days = stale_days
        self._max_usage = max_usage
        self._clock = clock or _utc_now

    # ------------------------------------------------------------------
    # Connection plumbing
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with the project schema configured.

        Schema configuration is idempotent: if the DB is already at the
        current version, ``configure_schema`` is a no-op.
        """
        cm = ConnectionManager(self._db_path)
        cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        return cm.get_connection()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def record_usage(self, knowledge_id: str) -> None:
        """Bump usage_count and refresh last_used_at.

        Creates the row if it does not yet exist (lifecycle_state="active").
        Idempotent in the sense that repeated calls just keep incrementing
        the counter; the row's lifecycle_state is preserved.
        """
        if not knowledge_id:
            return
        pack, doc = _split_id(knowledge_id)
        now = _iso(self._clock())
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO knowledge_items
                    (knowledge_id, pack_name, doc_name, lifecycle_state,
                     usage_count, last_used_at, created_at, updated_at)
                VALUES (?, ?, ?, 'active', 1, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used_at = excluded.last_used_at,
                    updated_at = excluded.updated_at
                """,
                (knowledge_id, pack, doc, now, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_deprecated(
        self,
        knowledge_id: str,
        *,
        grace_days: int = DEFAULT_GRACE_DAYS,
        reason: str | None = None,
    ) -> None:
        """Flag an item as deprecated and schedule its retirement.

        ``deprecated_at`` is set to now; ``retire_after`` is set to
        now + ``grace_days``.  The item remains accessible during the
        grace window so existing references do not break immediately.
        """
        if not knowledge_id:
            return
        pack, doc = _split_id(knowledge_id)
        now_dt = self._clock()
        now_iso = _iso(now_dt)
        retire_iso = _iso(now_dt + timedelta(days=int(grace_days)))
        reason_text = reason or ""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO knowledge_items
                    (knowledge_id, pack_name, doc_name, lifecycle_state,
                     usage_count, last_used_at, deprecated_at, retire_after,
                     deprecation_reason, created_at, updated_at)
                VALUES (?, ?, ?, 'deprecated', 0, '', ?, ?, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    lifecycle_state = 'deprecated',
                    deprecated_at = excluded.deprecated_at,
                    retire_after = excluded.retire_after,
                    deprecation_reason = excluded.deprecation_reason,
                    updated_at = excluded.updated_at
                """,
                (knowledge_id, pack, doc, now_iso, retire_iso,
                 reason_text, now_iso, now_iso),
            )
            conn.commit()
        finally:
            conn.close()

    def retire(self, knowledge_id: str) -> None:
        """Mark an item as retired immediately.

        Works whether or not the item was previously deprecated.  The row
        is created if it does not exist so manual retirements are
        recorded for audit.  Content lives on disk and is *not* deleted
        here -- removal of the source files remains a separate operator
        action so the audit trail and the filesystem stay independent.
        """
        if not knowledge_id:
            return
        pack, doc = _split_id(knowledge_id)
        now_iso = _iso(self._clock())
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO knowledge_items
                    (knowledge_id, pack_name, doc_name, lifecycle_state,
                     usage_count, last_used_at, deprecated_at, retire_after,
                     created_at, updated_at)
                VALUES (?, ?, ?, 'retired', 0, '', ?, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    lifecycle_state = 'retired',
                    retire_after = excluded.retire_after,
                    updated_at = excluded.updated_at
                """,
                (knowledge_id, pack, doc, now_iso, now_iso, now_iso, now_iso),
            )
            conn.commit()
        finally:
            conn.close()

    def auto_retire_expired(self) -> list[str]:
        """Retire every deprecated item whose ``retire_after`` is in the past.

        Returns the list of knowledge_ids that were transitioned.  Items
        whose ``retire_after`` is empty (defensive: a deprecation row
        written without a grace timestamp) are skipped so the operator
        retains control.
        """
        cutoff = _iso(self._clock())
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT knowledge_id FROM knowledge_items
                WHERE lifecycle_state = 'deprecated'
                  AND retire_after <> ''
                  AND retire_after <= ?
                """,
                (cutoff,),
            ).fetchall()
            retired = [r[0] for r in rows]
            if retired:
                conn.executemany(
                    "UPDATE knowledge_items "
                    "SET lifecycle_state = 'retired', updated_at = ? "
                    "WHERE knowledge_id = ?",
                    [(cutoff, kid) for kid in retired],
                )
                conn.commit()
            return retired
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def compute_staleness(self, knowledge_id: str) -> dict:
        """Return the freshness summary for a single item.

        Result shape::

            {
                "knowledge_id": str,
                "lifecycle_state": str,   # "" when the row does not exist
                "usage_count": int,
                "days_since_use": int,    # -1 when never used
                "last_used_at": str,      # "" when never used
                "is_stale": bool,
            }

        Stale condition (default thresholds):
            ``days_since_use > stale_days`` AND ``usage_count < max_usage``.
        Both inequalities are strict so an item exactly at the boundary
        is treated as fresh.  Items that have never been used at all are
        reported as stale to surface obviously dead packs.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT lifecycle_state, usage_count, last_used_at "
                "FROM knowledge_items WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {
                "knowledge_id": knowledge_id,
                "lifecycle_state": "",
                "usage_count": 0,
                "days_since_use": -1,
                "last_used_at": "",
                "is_stale": False,
            }

        state, usage_count, last_used_at = row
        days = self._days_since(last_used_at)
        is_stale = self._is_stale(state, usage_count, days, last_used_at)
        return {
            "knowledge_id": knowledge_id,
            "lifecycle_state": state,
            "usage_count": int(usage_count),
            "days_since_use": days,
            "last_used_at": last_used_at,
            "is_stale": is_stale,
        }

    def find_stale(
        self,
        *,
        stale_days: int | None = None,
        max_usage: int | None = None,
    ) -> list[str]:
        """Return knowledge_ids of *active* items that look stale.

        Items in ``deprecated`` or ``retired`` state are excluded -- the
        operator has already acted on them.  An item with no last_used_at
        is considered stale once it exists in the table at all (a row
        with usage_count=0 and no last_used_at is a clear "dead pack"
        signal).
        """
        threshold_days = self._stale_days if stale_days is None else stale_days
        threshold_usage = self._max_usage if max_usage is None else max_usage
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT knowledge_id, usage_count, last_used_at "
                "FROM knowledge_items WHERE lifecycle_state = 'active'"
            ).fetchall()
        finally:
            conn.close()

        stale: list[str] = []
        for kid, usage_count, last_used_at in rows:
            days = self._days_since(last_used_at)
            if self._is_stale_threshold(
                usage_count, days, last_used_at,
                threshold_days=threshold_days,
                threshold_usage=threshold_usage,
            ):
                stale.append(kid)
        return stale

    def list_items(
        self,
        *,
        states: Iterable[str] | None = None,
    ) -> list[dict]:
        """Return all rows, optionally filtered by lifecycle_state.

        Used by the CLI for ``baton knowledge usage`` listings.
        """
        conn = self._connect()
        try:
            sql = (
                "SELECT knowledge_id, pack_name, doc_name, lifecycle_state, "
                "       usage_count, last_used_at, deprecated_at, "
                "       retire_after, deprecation_reason "
                "FROM knowledge_items"
            )
            params: tuple = ()
            if states is not None:
                state_list = list(states)
                placeholders = ",".join("?" * len(state_list))
                sql += f" WHERE lifecycle_state IN ({placeholders})"
                params = tuple(state_list)
            sql += " ORDER BY knowledge_id"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        return [
            {
                "knowledge_id": r[0],
                "pack_name": r[1],
                "doc_name": r[2],
                "lifecycle_state": r[3],
                "usage_count": int(r[4]),
                "last_used_at": r[5],
                "deprecated_at": r[6],
                "retire_after": r[7],
                "deprecation_reason": r[8],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _days_since(self, last_used_at: str) -> int:
        last = _parse(last_used_at)
        if last is None:
            return -1
        delta = self._clock() - last
        return int(delta.total_seconds() // 86400)

    def _is_stale(
        self,
        state: str,
        usage_count: int,
        days_since_use: int,
        last_used_at: str,
    ) -> bool:
        """Per-item staleness used by ``compute_staleness``.

        Includes items in any lifecycle_state so the CLI can show why an
        item *was* deprecated -- ``find_stale`` filters by state.
        """
        return self._is_stale_threshold(
            usage_count, days_since_use, last_used_at,
            threshold_days=self._stale_days,
            threshold_usage=self._max_usage,
        )

    @staticmethod
    def _is_stale_threshold(
        usage_count: int,
        days_since_use: int,
        last_used_at: str,
        *,
        threshold_days: int,
        threshold_usage: int,
    ) -> bool:
        # Never used -> stale (clear signal of a dead pack).
        if days_since_use < 0 and not last_used_at:
            return int(usage_count) < threshold_usage
        return (
            days_since_use > threshold_days
            and int(usage_count) < threshold_usage
        )
