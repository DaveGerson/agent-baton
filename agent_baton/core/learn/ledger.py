"""LearningLedger — SQLite CRUD for LearningIssue records.

Issues are stored in the ``learning_issues`` table in the project-level
``baton.db`` database and federated to ``central.db`` via the existing
SyncEngine.

Deduplication is by ``(issue_type, target)``: when a signal recurs for the
same subject, ``occurrence_count`` is incremented and the new evidence entry
is appended rather than creating a duplicate row.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.learning import LearningEvidence, LearningIssue

_log = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("resolved", "wontfix")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningLedger:
    """SQLite-backed CRUD for LearningIssue records.

    The ledger writes to the ``learning_issues`` table in *db_path* (which
    must already be initialised with the schema via ``ConnectionManager``).
    If the table does not yet exist (e.g. a database created before schema v5),
    the ledger creates it on first access.

    Args:
        db_path: Absolute path to the project-level ``baton.db``.
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS learning_issues (
            issue_id          TEXT PRIMARY KEY,
            issue_type        TEXT NOT NULL,
            severity          TEXT NOT NULL DEFAULT 'medium',
            status            TEXT NOT NULL DEFAULT 'open',
            title             TEXT NOT NULL,
            target            TEXT NOT NULL,
            evidence          TEXT NOT NULL DEFAULT '[]',
            first_seen        TEXT NOT NULL,
            last_seen         TEXT NOT NULL,
            occurrence_count  INTEGER NOT NULL DEFAULT 1,
            proposed_fix      TEXT,
            resolution        TEXT,
            resolution_type   TEXT,
            experiment_id     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_learning_issues_type
            ON learning_issues(issue_type);
        CREATE INDEX IF NOT EXISTS idx_learning_issues_status
            ON learning_issues(status);
        CREATE INDEX IF NOT EXISTS idx_learning_issues_target
            ON learning_issues(target);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_issues_type_target_open
            ON learning_issues(issue_type, target)
            WHERE status NOT IN ('resolved', 'wontfix');
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_table()

    # ------------------------------------------------------------------
    # Connection helper
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_table(self) -> None:
        """Create the learning_issues table if it does not yet exist.

        This is always attempted so that the ledger works both against a
        fully-initialised baton.db (created by ConnectionManager) and against
        a bare SQLite file created directly (e.g. in tests).  The
        ``CREATE TABLE IF NOT EXISTS`` guard makes this idempotent.
        """
        conn = None
        try:
            conn = self._connect()
            conn.executescript(self._CREATE_TABLE)
            conn.commit()
        except Exception as exc:
            _log.debug("LearningLedger._ensure_table failed: %s", exc)
        finally:
            if conn is not None:
                conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def record_issue(
        self,
        issue_type: str,
        target: str,
        severity: str,
        title: str,
        evidence: LearningEvidence | None = None,
    ) -> LearningIssue:
        """Create a new issue or update an existing open one.

        If an open issue with the same ``(issue_type, target)`` already exists,
        its ``occurrence_count`` is incremented, ``last_seen`` is updated, and
        the new evidence entry (if provided) is appended.  Otherwise a new
        record is created.

        Args:
            issue_type: Category — one of the VALID_ISSUE_TYPES.
            target: What the issue is about (agent name, stack key, etc.).
            severity: Impact rating — one of VALID_SEVERITIES.
            title: Human-readable summary.
            evidence: Optional evidence from the current execution.

        Returns:
            The current LearningIssue (new or updated).
        """
        now = _utcnow()
        conn = self._connect()
        try:
            # Look for an existing open issue for this (type, target) pair
            existing_row = conn.execute(
                """
                SELECT issue_id, evidence, occurrence_count
                FROM learning_issues
                WHERE issue_type = ?
                  AND target = ?
                  AND status NOT IN ('resolved', 'wontfix')
                LIMIT 1
                """,
                (issue_type, target),
            ).fetchone()

            if existing_row is not None:
                issue_id = existing_row["issue_id"]
                existing_evidence: list[dict] = json.loads(
                    existing_row["evidence"] or "[]"
                )
                if evidence is not None:
                    existing_evidence.append(evidence.to_dict())
                new_count = existing_row["occurrence_count"] + 1
                # Semantic severity ordering: escalate only if the new
                # severity is higher than the existing one.  SQLite string
                # comparison is lexicographic which doesn't match semantic
                # order, so we use a CASE expression with explicit ranking.
                conn.execute(
                    """
                    UPDATE learning_issues
                    SET occurrence_count = ?,
                        last_seen = ?,
                        evidence = ?,
                        severity = CASE
                            WHEN (CASE ?
                                    WHEN 'critical' THEN 4
                                    WHEN 'high' THEN 3
                                    WHEN 'medium' THEN 2
                                    WHEN 'low' THEN 1
                                    ELSE 0 END)
                                 > (CASE severity
                                    WHEN 'critical' THEN 4
                                    WHEN 'high' THEN 3
                                    WHEN 'medium' THEN 2
                                    WHEN 'low' THEN 1
                                    ELSE 0 END)
                            THEN ?
                            ELSE severity
                        END
                    WHERE issue_id = ?
                    """,
                    (
                        new_count,
                        now,
                        json.dumps(existing_evidence),
                        severity,
                        severity,
                        issue_id,
                    ),
                )
                conn.commit()
                return self.get_issue(issue_id)  # type: ignore[return-value]
            else:
                issue_id = str(uuid.uuid4())
                evidence_list = [evidence.to_dict()] if evidence is not None else []
                conn.execute(
                    """
                    INSERT INTO learning_issues
                        (issue_id, issue_type, severity, status, title, target,
                         evidence, first_seen, last_seen, occurrence_count)
                    VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        issue_id,
                        issue_type,
                        severity,
                        title,
                        target,
                        json.dumps(evidence_list),
                        now,
                        now,
                    ),
                )
                conn.commit()
        finally:
            conn.close()

        return self.get_issue(issue_id)  # type: ignore[return-value]

    def update_status(
        self,
        issue_id: str,
        status: str,
        resolution: str | None = None,
        resolution_type: str | None = None,
        experiment_id: str | None = None,
        proposed_fix: str | None = None,
    ) -> bool:
        """Update the lifecycle status of an issue.

        Args:
            issue_id: The issue to update.
            status: New status value.
            resolution: Description of how it was resolved (optional).
            resolution_type: ``"auto"``, ``"human"``, or ``"interview"``.
            experiment_id: Links to an Experiment if auto-applied.
            proposed_fix: Proposed remediation description (for proposed status).

        Returns:
            True if a row was updated.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                UPDATE learning_issues
                SET status = ?,
                    resolution = COALESCE(?, resolution),
                    resolution_type = COALESCE(?, resolution_type),
                    experiment_id = COALESCE(?, experiment_id),
                    proposed_fix = COALESCE(?, proposed_fix)
                WHERE issue_id = ?
                """,
                (
                    status,
                    resolution,
                    resolution_type,
                    experiment_id,
                    proposed_fix,
                    issue_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_issue(self, issue_id: str) -> LearningIssue | None:
        """Fetch a single issue by ``issue_id``."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM learning_issues WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_issue(row)

    def get_open_issues(
        self,
        issue_type: str | None = None,
        severity: str | None = None,
    ) -> list[LearningIssue]:
        """Return open issues with optional filters.

        Args:
            issue_type: Filter to a specific issue category, or None for all.
            severity: Filter to a specific severity, or None for all.

        Returns:
            List of matching LearningIssue records ordered by occurrence_count
            descending (most frequent first).
        """
        clauses = [
            "status NOT IN ('resolved', 'wontfix')",
        ]
        params: list = []
        if issue_type is not None:
            clauses.append("issue_type = ?")
            params.append(issue_type)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM learning_issues WHERE {where} ORDER BY occurrence_count DESC"

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_issue(r) for r in rows]

    def get_issues_above_threshold(
        self, issue_type: str, min_occurrences: int
    ) -> list[LearningIssue]:
        """Return open issues of *issue_type* with at least *min_occurrences*.

        Used by LearningEngine to identify candidates for auto-apply.

        Args:
            issue_type: Issue category to filter.
            min_occurrences: Minimum occurrence count threshold.

        Returns:
            Matching issues ordered by occurrence_count descending.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM learning_issues
                WHERE issue_type = ?
                  AND status NOT IN ('resolved', 'wontfix')
                  AND occurrence_count >= ?
                ORDER BY occurrence_count DESC
                """,
                (issue_type, min_occurrences),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_issue(r) for r in rows]

    def get_history(self, limit: int = 50) -> list[LearningIssue]:
        """Return resolved/wontfix issues ordered by most recently updated.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of terminal-state issues.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM learning_issues
                WHERE status IN ('resolved', 'wontfix')
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_issue(r) for r in rows]

    def get_all_issues(
        self,
        status: str | None = None,
        issue_type: str | None = None,
        severity: str | None = None,
    ) -> list[LearningIssue]:
        """Return issues with flexible status/type/severity filters.

        Args:
            status: Filter by status, or None for all statuses.
            issue_type: Filter by issue type, or None for all.
            severity: Filter by severity, or None for all.

        Returns:
            Matching issues ordered by last_seen descending.
        """
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if issue_type is not None:
            clauses.append("issue_type = ?")
            params.append(issue_type)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM learning_issues{where} ORDER BY last_seen DESC"

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [self._row_to_issue(r) for r in rows]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_issue(row: sqlite3.Row) -> LearningIssue:
        """Convert a SQLite row to a LearningIssue dataclass."""
        data = dict(row)
        raw_evidence = data.get("evidence", "[]") or "[]"
        if isinstance(raw_evidence, str):
            try:
                evidence_dicts = json.loads(raw_evidence)
            except (ValueError, TypeError):
                evidence_dicts = []
        else:
            evidence_dicts = raw_evidence
        evidence = [LearningEvidence.from_dict(e) for e in evidence_dicts]
        return LearningIssue(
            issue_id=data["issue_id"],
            issue_type=data["issue_type"],
            severity=data.get("severity", "medium"),
            status=data.get("status", "open"),
            title=data.get("title", ""),
            target=data.get("target", ""),
            evidence=evidence,
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
            occurrence_count=int(data.get("occurrence_count", 1)),
            proposed_fix=data.get("proposed_fix"),
            resolution=data.get("resolution"),
            resolution_type=data.get("resolution_type"),
            experiment_id=data.get("experiment_id"),
        )
