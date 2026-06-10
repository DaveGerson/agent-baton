"""SQLite-backed persistence for SpecDraft entities (007 Phase I).

``SpecDraftStore`` follows the same pattern as ``SpecStore``: it owns one
SQLite path, provides typed CRUD methods, and enforces lifecycle transitions.
It targets the same central.db that the existing SpecStore uses (defaults to
``~/.baton/central.db``) but can be overridden for tests via *db_path*.

Schema dependency: ``spec_drafts`` table from migration v45.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_baton.models.spec_draft import (
    EnrichmentData,
    ReviewData,
    SpecDraft,
    _VALID_TRANSITIONS,
    _now_iso,
)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"

_ENSURE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS spec_drafts (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'manual',
    source_ref      TEXT NOT NULL DEFAULT '',
    submitted_by    TEXT NOT NULL DEFAULT 'local-user',
    submitted_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    status          TEXT NOT NULL DEFAULT 'submitted',
    enrichment_json TEXT,
    review_json     TEXT,
    task_id         TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_spec_drafts_status       ON spec_drafts(status);
CREATE INDEX IF NOT EXISTS idx_spec_drafts_submitted_by ON spec_drafts(submitted_by);
CREATE INDEX IF NOT EXISTS idx_spec_drafts_submitted_at ON spec_drafts(submitted_at);
"""


class SpecDraftStore:
    """Create, read, update, and transition SpecDraft entities.

    Args:
        db_path: Path to the SQLite database.  Defaults to
            ``~/.baton/central.db`` (same as SpecStore).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _CENTRAL_DB_DEFAULT).resolve()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Internal connection helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        """Create the spec_drafts table if it does not exist yet."""
        with self._connect() as conn:
            conn.executescript(_ENSURE_TABLE_DDL)
            conn.commit()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        body: str = "",
        source: str = "manual",
        source_ref: str = "",
        submitted_by: str = "local-user",
        draft_id: str | None = None,
    ) -> SpecDraft:
        """Insert a new SpecDraft in ``submitted`` state.

        Args:
            title: Short human-readable title.
            body: Full markdown spec body.
            source: Origin — ``"manual"``, ``"github"``, or ``"ado"``.
            source_ref: External reference URL or ID.
            submitted_by: Identity of the creator.
            draft_id: Explicit ID; auto-generated as UUID4 if omitted.

        Returns:
            The persisted ``SpecDraft`` instance.
        """
        sid = draft_id or str(uuid.uuid4())
        now = _now_iso()
        draft = SpecDraft(
            id=sid,
            title=title,
            body=body,
            source=source,
            source_ref=source_ref,
            submitted_by=submitted_by,
            submitted_at=now,
            status="submitted",
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO spec_drafts
                    (id, title, body, source, source_ref, submitted_by,
                     submitted_at, status, enrichment_json, review_json,
                     task_id, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    draft.id, draft.title, draft.body, draft.source,
                    draft.source_ref, draft.submitted_by, draft.submitted_at,
                    draft.status, None, None, None, draft.updated_at,
                ),
            )
            conn.commit()
        return draft

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, draft_id: str) -> SpecDraft | None:
        """Load a SpecDraft by ID.

        Args:
            draft_id: The draft identifier.

        Returns:
            A ``SpecDraft`` instance, or ``None`` if not found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spec_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_draft(row)

    def list(
        self,
        *,
        status: str | None = None,
        submitted_by: str | None = None,
        limit: int = 100,
    ) -> list[SpecDraft]:
        """List spec drafts with optional filters.

        Args:
            status: Filter by lifecycle status.
            submitted_by: Filter by submitter.
            limit: Maximum rows to return.

        Returns:
            List of ``SpecDraft`` instances ordered newest-first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if submitted_by is not None:
            clauses.append("submitted_by = ?")
            params.append(submitted_by)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM spec_drafts {where} ORDER BY submitted_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_enrichment(
        self,
        draft_id: str,
        enrichment: EnrichmentData,
    ) -> SpecDraft:
        """Persist enrichment data and advance status to ``enriched``.

        Args:
            draft_id: The draft to update.
            enrichment: Enrichment results to store.

        Returns:
            Updated ``SpecDraft``.

        Raises:
            ValueError: If not found or transition is invalid.
        """
        draft = self._require(draft_id)
        if not draft.can_transition_to("enriched") and draft.status != "enriched":
            raise ValueError(
                f"Cannot enrich spec_draft in status {draft.status!r}"
            )
        now = _now_iso()
        enrichment_json = enrichment.model_dump_json()
        new_status = "enriched" if draft.status == "submitted" else draft.status
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE spec_drafts
                   SET enrichment_json=?, status=?, updated_at=?
                 WHERE id=?
                """,
                (enrichment_json, new_status, now, draft_id),
            )
            conn.commit()
        draft.enrichment = enrichment
        draft.status = new_status
        draft.updated_at = now
        return draft

    def update_status(
        self,
        draft_id: str,
        new_status: str,
        *,
        review: ReviewData | None = None,
    ) -> SpecDraft:
        """Advance the spec draft's lifecycle status.

        Args:
            draft_id: The draft to update.
            new_status: Target status (must be a valid transition).
            review: Required when *new_status* is ``"approved"`` or
                ``"bounced"``; stored as review_json.

        Returns:
            Updated ``SpecDraft``.

        Raises:
            ValueError: If not found or transition is invalid.
        """
        draft = self._require(draft_id)
        allowed = _VALID_TRANSITIONS.get(draft.status, frozenset())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition spec_draft from {draft.status!r} to {new_status!r}"
            )
        now = _now_iso()
        review_json = review.model_dump_json() if review is not None else None
        with self._connect() as conn:
            if review_json is not None:
                conn.execute(
                    "UPDATE spec_drafts SET status=?, review_json=?, updated_at=? WHERE id=?",
                    (new_status, review_json, now, draft_id),
                )
            else:
                conn.execute(
                    "UPDATE spec_drafts SET status=?, updated_at=? WHERE id=?",
                    (new_status, now, draft_id),
                )
            conn.commit()
        draft.status = new_status
        if review is not None:
            draft.review = review
        draft.updated_at = now
        return draft

    def set_task_id(self, draft_id: str, task_id: str) -> SpecDraft:
        """Record the fired execution task ID and advance status to ``fired``.

        Args:
            draft_id: The draft to update.
            task_id: The execution task ID generated by plan creation.

        Returns:
            Updated ``SpecDraft``.

        Raises:
            ValueError: If not found or not in ``approved`` status.
        """
        draft = self._require(draft_id)
        if not draft.can_transition_to("fired"):
            raise ValueError(
                f"Cannot fire spec_draft in status {draft.status!r}"
            )
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE spec_drafts SET task_id=?, status='fired', updated_at=? WHERE id=?",
                (task_id, now, draft_id),
            )
            conn.commit()
        draft.task_id = task_id
        draft.status = "fired"
        draft.updated_at = now
        return draft

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, draft_id: str) -> SpecDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError(f"SpecDraft not found: {draft_id!r}")
        return draft

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> SpecDraft:
        enrichment: EnrichmentData | None = None
        if row["enrichment_json"]:
            try:
                enrichment = EnrichmentData.model_validate_json(row["enrichment_json"])
            except Exception:  # noqa: BLE001
                pass

        review: ReviewData | None = None
        if row["review_json"]:
            try:
                review = ReviewData.model_validate_json(row["review_json"])
            except Exception:  # noqa: BLE001
                pass

        return SpecDraft(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            source=row["source"],
            source_ref=row["source_ref"],
            submitted_by=row["submitted_by"],
            submitted_at=row["submitted_at"],
            status=row["status"],
            enrichment=enrichment,
            review=review,
            task_id=row["task_id"],
            updated_at=row["updated_at"],
        )
