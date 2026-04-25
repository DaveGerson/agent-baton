"""SQLite-backed read/write helpers for the central ``users`` table (H3.1).

Owns reads and writes against the ``users`` table that lives in
``~/.baton/central.db``.  Mirrors the design of
:class:`agent_baton.core.storage.handoff_store.HandoffStore`:

- One :class:`ConnectionManager` per store, schema configured on first
  access.
- All SQL is parameterised.
- Methods degrade gracefully when the table is absent (older schema or
  read-only environments) -- they return safe empty values rather than
  raising.

The store also exposes a :func:`get_user_role` module-level helper so
callers that just want a user's :class:`HumanRole` -- e.g. future PMO
view code or G1.4 SoD policy -- do not have to instantiate the store
themselves.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agent_baton.models.identity import HumanRole, UserIdentity

_log = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


def _resolve_central_db(db_path: Path | None) -> Path:
    """Return *db_path* or the standard ``~/.baton/central.db`` fallback."""
    return db_path or _CENTRAL_DB_DEFAULT


class UserStore:
    """SQLite-backed persistence for PMO user identities.

    Args:
        db_path: Absolute path to ``central.db``.  Defaults to
            ``~/.baton/central.db`` when ``None``.

    Notes:
        * The store uses :class:`ConnectionManager` configured with the
          full ``CENTRAL_SCHEMA_DDL``, so first access against a fresh
          ``central.db`` provisions every central table -- not just
          ``users``.  This matches the pattern other central stores use.
        * Methods that read the table never raise on schema mismatch; a
          warning is logged and a safe default is returned.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import (
            CENTRAL_SCHEMA_DDL,
            SCHEMA_VERSION,
        )

        self._db_path = _resolve_central_db(db_path)
        self._conn_mgr = ConnectionManager(self._db_path)
        self._conn_mgr.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()

    def _table_exists(self) -> bool:
        try:
            row = self._conn().execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='users'"
            ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001 - defensive
            return False

    def _row_to_identity(self, row: sqlite3.Row) -> UserIdentity:
        keys = set(row.keys())
        # human_role may be absent on databases at schema < v16.  Tolerate.
        human_role_value = row["human_role"] if "human_role" in keys else ""
        return UserIdentity(
            user_id=row["user_id"],
            display_name=row["display_name"] or "",
            email=row["email"] or "",
            role=row["role"] or "creator",
            human_role=HumanRole.parse(human_role_value),
            created_at=row["created_at"] or "",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, user_id: str) -> UserIdentity | None:
        """Return the user record for *user_id* or ``None`` if absent."""
        if not user_id or not self._table_exists():
            return None
        try:
            row = self._conn().execute(
                "SELECT user_id, display_name, email, role, "
                "human_role, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            # ``human_role`` column missing on a pre-v16 database that
            # somehow escaped migration -- fall back to the legacy shape.
            if "no such column" in str(exc).lower():
                return self._get_legacy(user_id)
            _log.warning("UserStore.get failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("UserStore.get failed: %s", exc)
            return None
        if row is None:
            return None
        return self._row_to_identity(row)

    def _get_legacy(self, user_id: str) -> UserIdentity | None:
        """Read a user record without the ``human_role`` column (pre-v16)."""
        try:
            row = self._conn().execute(
                "SELECT user_id, display_name, email, role, created_at "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("UserStore._get_legacy failed: %s", exc)
            return None
        if row is None:
            return None
        return UserIdentity(
            user_id=row["user_id"],
            display_name=row["display_name"] or "",
            email=row["email"] or "",
            role=row["role"] or "creator",
            human_role=HumanRole.UNASSIGNED,
            created_at=row["created_at"] or "",
        )

    def list_all(self) -> list[UserIdentity]:
        """Return every user, ordered by ``user_id`` ascending."""
        if not self._table_exists():
            return []
        try:
            cur = self._conn().execute(
                "SELECT user_id, display_name, email, role, "
                "human_role, created_at FROM users ORDER BY user_id ASC"
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc).lower():
                return self._list_legacy()
            _log.warning("UserStore.list_all failed: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("UserStore.list_all failed: %s", exc)
            return []
        return [self._row_to_identity(r) for r in rows]

    def _list_legacy(self) -> list[UserIdentity]:
        """List rows on a pre-v16 schema (no ``human_role`` column)."""
        try:
            cur = self._conn().execute(
                "SELECT user_id, display_name, email, role, created_at "
                "FROM users ORDER BY user_id ASC"
            )
            rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("UserStore._list_legacy failed: %s", exc)
            return []
        return [
            UserIdentity(
                user_id=r["user_id"],
                display_name=r["display_name"] or "",
                email=r["email"] or "",
                role=r["role"] or "creator",
                human_role=HumanRole.UNASSIGNED,
                created_at=r["created_at"] or "",
            )
            for r in rows
        ]

    def upsert(self, identity: UserIdentity) -> None:
        """Insert or replace a user row.

        Uses ``INSERT ... ON CONFLICT(user_id) DO UPDATE`` so that the
        ``created_at`` column is preserved across updates (only the
        mutable fields -- display_name, email, role, human_role -- are
        overwritten).
        """
        if not self._table_exists():
            _log.warning(
                "UserStore.upsert: users table not found (schema v14+ required)"
            )
            return
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, display_name, email, role,
                    human_role, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    email        = excluded.email,
                    role         = excluded.role,
                    human_role   = excluded.human_role
                """,
                (
                    identity.user_id,
                    identity.display_name,
                    identity.email,
                    identity.role,
                    identity.human_role.value,
                    identity.created_at,
                ),
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc).lower():
                # Legacy schema -- skip human_role.
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id, display_name, email, role, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        email        = excluded.email,
                        role         = excluded.role
                    """,
                    (
                        identity.user_id,
                        identity.display_name,
                        identity.email,
                        identity.role,
                        identity.created_at,
                    ),
                )
                conn.commit()
            else:
                raise

    def assign_role(self, user_id: str, role: HumanRole) -> UserIdentity:
        """Set *user_id*'s :attr:`human_role` to *role*, creating the row
        if it does not yet exist.

        Returns the resulting :class:`UserIdentity`.  Newly-created rows
        get a default ``role`` of ``"creator"``; existing rows keep
        their current ``role``.
        """
        existing = self.get(user_id)
        if existing is None:
            identity = UserIdentity(user_id=user_id, human_role=role)
        else:
            existing.human_role = role
            identity = existing
        self.upsert(identity)
        return identity


# ---------------------------------------------------------------------------
# Module-level convenience helpers
# ---------------------------------------------------------------------------


def get_user_role(
    user_id: str,
    *,
    db_path: Path | None = None,
) -> HumanRole:
    """Return *user_id*'s :class:`HumanRole`, or :attr:`HumanRole.UNASSIGNED`.

    A thin wrapper over :class:`UserStore` for callers that only need
    the role (PMO views, future SoD policy, dispatch heuristics) and do
    not want to manage a store instance themselves.

    Args:
        user_id: The user identifier resolved by the identity
            middleware.  Empty / unknown values return ``UNASSIGNED``.
        db_path: Override path for ``central.db``.  Defaults to
            ``~/.baton/central.db``.

    Returns:
        The user's :class:`HumanRole`, or :attr:`HumanRole.UNASSIGNED`
        when the user is unknown or an error occurs.
    """
    if not user_id:
        return HumanRole.UNASSIGNED
    try:
        store = UserStore(db_path=db_path)
        identity = store.get(user_id)
    except Exception as exc:  # noqa: BLE001 - defensive
        _log.warning("get_user_role(%r) failed: %s", user_id, exc)
        return HumanRole.UNASSIGNED
    if identity is None:
        return HumanRole.UNASSIGNED
    return identity.human_role
