"""Governance override + justification log (G1.6, bd-1a09).

Every use of an operator override flag (``--force``, ``--skip-gate``,
``--risk-override``, ...) is recorded in two places:

1. The ``governance_overrides`` SQL table — with the full justification
   text, the flag, the command, the actor (``$USER``), the argv JSON,
   and a back-reference to the audit-chain entry.
2. ``compliance-audit.jsonl`` via :class:`ComplianceChainWriter` — with
   the metadata BUT NOT the justification text.  The chain is exported
   for external auditors and frequently leaves the host machine, so
   the rationale stays in the integrity-protected SQL row that travels
   only to authorised reviewers.

Tying the two together via ``chain_hash`` lets compliance reviewers
prove the SQL row was emitted at a particular point in the chain
without needing the SQL row itself to live in the chain.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_baton.core.govern.compliance import ComplianceChainWriter


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO-8601 form (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _os_identity() -> str | None:
    """Return the OS-derived identity (tamper-resistant) or ``None``.

    Resolved via ``pwd.getpwuid(os.geteuid()).pw_name`` on POSIX.  Unlike
    ``$USER``, this cannot be trivially spoofed by setting an environment
    variable — it reflects the actual effective UID of the process.

    Returns ``None`` on platforms without ``pwd`` (e.g. Windows) or when
    the lookup fails (no passwd entry for the effective UID).
    """
    try:
        import pwd  # POSIX-only
        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, OSError):
        return None


def _current_actor() -> str:
    """Return the operator identity for audit attribution.

    Resolution order (bd-fe42 hardening):

    1. The OS-derived identity from ``pwd.getpwuid(os.geteuid())`` — this
       is tamper-resistant: setting ``USER=auditor`` in the environment
       does NOT change the effective UID of the process.
    2. If the OS identity is available AND ``$USER`` / ``$USERNAME`` is
       set to a different value, the entry is tagged ``"<os>?env=<env>"``
       so reviewers can see the spoof attempt.
    3. ``USER`` → ``USERNAME`` (Windows fallback).
    4. ``"unknown"``.

    The audit trail is local-dev-grade (a determined attacker with shell
    access can still escalate), but blind ``$USER`` spoofing is now
    visibly recorded rather than silently trusted.
    """
    os_user = _os_identity()
    env_user = os.environ.get("USER") or os.environ.get("USERNAME")
    if os_user:
        if env_user and env_user != os_user:
            # Spoof attempt: keep the trustworthy OS identity but record
            # the divergent env value so it's visible in the audit log.
            return f"{os_user}?env={env_user}"
        return os_user
    return env_user or "unknown"


class OverrideLog:
    """Persist and query governance override events.

    The log writes two artifacts per call to :meth:`record`:

    * one row in the ``governance_overrides`` SQL table (full detail)
    * one entry in ``compliance-audit.jsonl`` (metadata only — never the
      justification text)

    Args:
        db_path: Path to the project ``baton.db``.  The
            ``governance_overrides`` table is created automatically by
            the schema migration; this class only writes / reads.
        chain_log_path: Path to the hash-chained compliance JSONL file.
            Defaults to a sibling of ``db_path``.
    """

    def __init__(
        self,
        db_path: Path,
        chain_log_path: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        if chain_log_path is None:
            chain_log_path = self._db_path.parent / "compliance-audit.jsonl"
        self._chain_log_path = Path(chain_log_path)
        # bd-5000: cache the ConnectionManager + one-shot schema-config flag
        # so subsequent _connect() calls don't re-issue PRAGMA / DDL setup.
        self._cm: Any = None
        self._schema_configured: bool = False

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record(
        self,
        flag: str,
        command: str,
        args: list[str],
        justification: str | None,
    ) -> str:
        """Record an override event and return the new ``override_id``.

        Args:
            flag: Which override flag fired (e.g. ``"--force"``).
            command: The CLI command path being run (e.g.
                ``"baton execute gate"``).
            args: Full argv as a list of strings.  Stored as JSON.
            justification: Operator-supplied reason.  May be ``None`` /
                empty when the caller is non-interactive — that case is
                allowed but the chain entry will still record
                ``justification_present=False``.

        Returns:
            The freshly minted ``override_id`` (UUIDv4 hex).
        """
        override_id = uuid.uuid4().hex
        actor = _current_actor()
        created_at = _utcnow_iso()
        justification_text = justification or ""

        # Step 1: append the chain entry.  Justification text stays out
        # of the chain by design — only metadata about its presence.
        chain_payload: dict[str, Any] = {
            "event": "override",
            "override_id": override_id,
            "flag": flag,
            "command": command,
            "actor": actor,
            "justification_present": bool(justification_text.strip()),
            "timestamp": created_at,
        }
        writer = ComplianceChainWriter(log_path=self._chain_log_path)
        chain_entry = writer.append(chain_payload)
        chain_hash = chain_entry.get("entry_hash", "")

        # Step 2: persist the SQL row (full justification included).
        self._insert_row(
            override_id=override_id,
            actor=actor,
            command=command,
            args_json=json.dumps(args),
            flag=flag,
            justification=justification_text,
            created_at=created_at,
            chain_hash=chain_hash,
        )
        return override_id

    def _insert_row(
        self,
        *,
        override_id: str,
        actor: str,
        command: str,
        args_json: str,
        flag: str,
        justification: str,
        created_at: str,
        chain_hash: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO governance_overrides (
                    override_id, actor, command, args_json,
                    flag, justification, created_at, chain_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    override_id, actor, command, args_json,
                    flag, justification, created_at, chain_hash,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return up to *limit* most-recent overrides, newest first."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT override_id, actor, command, args_json, flag,
                       justification, created_at, chain_hash
                FROM governance_overrides
                ORDER BY created_at DESC, override_id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    def get(self, override_id: str) -> dict[str, Any] | None:
        """Return a single override by ID, or ``None`` if not found."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT override_id, actor, command, args_json, flag,
                       justification, created_at, chain_hash
                FROM governance_overrides
                WHERE override_id = ?
                """,
                (override_id,),
            )
            row = cur.fetchone()
            return self._row_to_dict(row) if row else None

    def export_since(self, since_iso: str | None) -> list[dict[str, Any]]:
        """Return all overrides created on or after *since_iso* (ISO-8601).

        ``since_iso=None`` returns the full log.
        """
        with self._connect() as conn:
            if since_iso:
                cur = conn.execute(
                    """
                    SELECT override_id, actor, command, args_json, flag,
                           justification, created_at, chain_hash
                    FROM governance_overrides
                    WHERE created_at >= ?
                    ORDER BY created_at ASC, override_id ASC
                    """,
                    (since_iso,),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT override_id, actor, command, args_json, flag,
                           justification, created_at, chain_hash
                    FROM governance_overrides
                    ORDER BY created_at ASC, override_id ASC
                    """,
                )
            return [self._row_to_dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a connection, configuring schema only on first use.

        bd-5000: previously rebuilt the :class:`ConnectionManager` and
        re-ran ``configure_schema`` on every call, which thrashed the
        thread-local connection cache and re-checked the schema for
        every read (``list_recent`` / ``get`` / ``export_since``).  Now
        the manager is built once per :class:`OverrideLog` instance and
        ``configure_schema`` is called exactly once.

        Going through :class:`ConnectionManager` still ensures the
        ``governance_overrides`` table exists on first use even when the
        caller's project DB pre-dates v17.
        """
        if self._cm is None:
            from agent_baton.core.storage.connection import ConnectionManager

            self._cm = ConnectionManager(self._db_path)
        if not self._schema_configured:
            from agent_baton.core.storage.schema import (
                PROJECT_SCHEMA_DDL,
                SCHEMA_VERSION,
            )

            self._cm.configure_schema(PROJECT_SCHEMA_DDL, version=SCHEMA_VERSION)
            self._schema_configured = True
        return self._cm.get_connection()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "override_id": row["override_id"],
            "actor": row["actor"],
            "command": row["command"],
            "args_json": row["args_json"],
            "flag": row["flag"],
            "justification": row["justification"],
            "created_at": row["created_at"],
            "chain_hash": row["chain_hash"],
        }
