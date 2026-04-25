"""SQLite-backed persistence for the Spec entity (F0.1).

``SpecStore`` mirrors the design pattern of ``BeadStore`` and
``KnowledgeRegistry``: it owns one SQLite path, provides typed CRUD methods,
and enforces lifecycle transitions.  It persists to ``central.db`` by default
(specs are cross-project artifacts) but can target any SQLite file.

Schema dependency: ``specs`` and ``spec_plan_links`` tables from v16 migration.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_baton.models.spec import Spec, SPEC_STATES, _hash_content, _now_iso

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft":     frozenset({"reviewed", "approved", "archived"}),
    "reviewed":  frozenset({"approved", "draft", "archived"}),
    "approved":  frozenset({"executing", "archived"}),
    "executing": frozenset({"completed", "archived"}),
    "completed": frozenset({"archived"}),
    "archived":  frozenset(),
}


class SpecStore:
    """Create, read, update, and link Spec entities.

    Args:
        db_path: Path to the SQLite database.  Defaults to
            ``~/.baton/central.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _CENTRAL_DB_DEFAULT).resolve()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        content: str = "",
        task_type: str = "",
        template_id: str = "",
        author_id: str = "local-user",
        project_id: str = "default",
        spec_id: str | None = None,
    ) -> Spec:
        """Insert a new Spec in ``draft`` state.

        Args:
            title: Short human-readable title.
            content: Full YAML body (may be empty on creation).
            task_type: Inferred task category.
            template_id: Template used to scaffold this spec.
            author_id: Identity of the creator.
            project_id: Owning project.
            spec_id: Explicit ID; auto-generated as UUID4 if omitted.

        Returns:
            The persisted ``Spec`` instance.
        """
        sid = spec_id or str(uuid.uuid4())
        now = _now_iso()
        content_hash = _hash_content(content) if content else ""
        spec = Spec(
            spec_id=sid,
            project_id=project_id,
            author_id=author_id,
            task_type=task_type,
            template_id=template_id,
            title=title,
            state="draft",
            content=content,
            content_hash=content_hash,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO specs
                    (spec_id, project_id, author_id, task_type, template_id,
                     title, state, content, content_hash, score_json,
                     created_at, updated_at, approved_at, approved_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec.spec_id, spec.project_id, spec.author_id,
                    spec.task_type, spec.template_id, spec.title,
                    spec.state, spec.content, spec.content_hash,
                    spec.score_json, spec.created_at, spec.updated_at,
                    spec.approved_at, spec.approved_by,
                ),
            )
            conn.commit()
        return spec

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, spec_id: str) -> Spec | None:
        """Load a Spec by ID, including linked plan IDs.

        Args:
            spec_id: The spec identifier.

        Returns:
            A ``Spec`` instance, or ``None`` if not found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM specs WHERE spec_id = ?", (spec_id,)
            ).fetchone()
            if row is None:
                return None
            spec = self._row_to_spec(row)
            spec.linked_plan_ids = self._load_links(conn, spec_id)
        return spec

    def list(
        self,
        *,
        project_id: str | None = None,
        state: str | None = None,
        author_id: str | None = None,
        limit: int = 50,
    ) -> list[Spec]:
        """List specs with optional filters.

        Args:
            project_id: Filter by project.
            state: Filter by lifecycle state.
            author_id: Filter by author.
            limit: Maximum rows to return.

        Returns:
            List of ``Spec`` instances ordered newest first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if author_id is not None:
            clauses.append("author_id = ?")
            params.append(author_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM specs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            specs = [self._row_to_spec(r) for r in rows]
            for s in specs:
                s.linked_plan_ids = self._load_links(conn, s.spec_id)
        return specs

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_state(
        self,
        spec_id: str,
        new_state: str,
        *,
        actor: str = "local-user",
    ) -> Spec:
        """Advance the spec's lifecycle state.

        Args:
            spec_id: The spec to update.
            new_state: Target state (must be a valid transition from current).
            actor: Identity performing the transition (recorded on approval).

        Returns:
            Updated ``Spec`` instance.

        Raises:
            ValueError: If ``spec_id`` not found or transition is invalid.
        """
        if new_state not in SPEC_STATES:
            raise ValueError(f"Unknown state: {new_state!r}")
        spec = self.get(spec_id)
        if spec is None:
            raise ValueError(f"Spec not found: {spec_id!r}")
        allowed = _VALID_TRANSITIONS.get(spec.state, frozenset())
        if new_state not in allowed:
            raise ValueError(
                f"Cannot transition from {spec.state!r} to {new_state!r}"
            )
        now = _now_iso()
        approved_at = now if new_state == "approved" else spec.approved_at
        approved_by = actor if new_state == "approved" else spec.approved_by
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE specs SET state=?, updated_at=?, approved_at=?, approved_by=?
                WHERE spec_id=?
                """,
                (new_state, now, approved_at, approved_by, spec_id),
            )
            conn.commit()
        spec.state = new_state
        spec.updated_at = now
        spec.approved_at = approved_at
        spec.approved_by = approved_by
        return spec

    def update_content(self, spec_id: str, content: str) -> Spec:
        """Replace the spec body and refresh hash + updated_at.

        Args:
            spec_id: The spec to update.
            content: New YAML body.

        Returns:
            Updated ``Spec``.
        """
        spec = self.get(spec_id)
        if spec is None:
            raise ValueError(f"Spec not found: {spec_id!r}")
        now = _now_iso()
        content_hash = _hash_content(content)
        with self._connect() as conn:
            conn.execute(
                "UPDATE specs SET content=?, content_hash=?, updated_at=? WHERE spec_id=?",
                (content, content_hash, now, spec_id),
            )
            conn.commit()
        spec.content = content
        spec.content_hash = content_hash
        spec.updated_at = now
        return spec

    def score(self, spec_id: str, scorecard: dict[str, Any]) -> Spec:
        """Record a multi-dimensional scorecard on the spec.

        Args:
            spec_id: The spec to score.
            scorecard: Dict of dimension → score.

        Returns:
            Updated ``Spec``.
        """
        spec = self.get(spec_id)
        if spec is None:
            raise ValueError(f"Spec not found: {spec_id!r}")
        now = _now_iso()
        score_json = json.dumps(scorecard)
        with self._connect() as conn:
            conn.execute(
                "UPDATE specs SET score_json=?, updated_at=? WHERE spec_id=?",
                (score_json, now, spec_id),
            )
            conn.commit()
        spec.score_json = score_json
        spec.updated_at = now
        return spec

    # ------------------------------------------------------------------
    # Link
    # ------------------------------------------------------------------

    def link_to_plan(
        self,
        spec_id: str,
        task_id: str,
        *,
        project_id: str = "default",
    ) -> None:
        """Create a ``spec_plan_links`` row linking a spec to a plan.

        Args:
            spec_id: The spec identifier.
            task_id: The plan/execution task ID.
            project_id: Project context.
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO spec_plan_links (spec_id, task_id, project_id, linked_at)
                VALUES (?,?,?,?)
                """,
                (spec_id, task_id, project_id, now),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def export_json(self, spec_id: str) -> str:
        """Serialise a spec to JSON string.

        Args:
            spec_id: The spec to export.

        Returns:
            JSON string.
        """
        spec = self.get(spec_id)
        if spec is None:
            raise ValueError(f"Spec not found: {spec_id!r}")
        return json.dumps(spec.to_dict(), indent=2)

    def import_json(self, json_str: str, *, overwrite: bool = False) -> Spec:
        """Import a spec from a JSON string.

        Args:
            json_str: JSON-serialised spec (output of ``export_json``).
            overwrite: When ``True``, replace an existing spec with the same ID.

        Returns:
            The imported ``Spec``.
        """
        data = json.loads(json_str)
        existing = self.get(data["spec_id"])
        if existing is not None and not overwrite:
            return existing
        spec = Spec.from_dict(data)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO specs
                    (spec_id, project_id, author_id, task_type, template_id,
                     title, state, content, content_hash, score_json,
                     created_at, updated_at, approved_at, approved_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec.spec_id, spec.project_id, spec.author_id,
                    spec.task_type, spec.template_id, spec.title,
                    spec.state, spec.content, spec.content_hash,
                    spec.score_json, spec.created_at, spec.updated_at,
                    spec.approved_at, spec.approved_by,
                ),
            )
            conn.commit()
        return spec

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_spec(row: sqlite3.Row) -> Spec:
        return Spec(
            spec_id=row["spec_id"],
            project_id=row["project_id"],
            author_id=row["author_id"],
            task_type=row["task_type"],
            template_id=row["template_id"],
            title=row["title"],
            state=row["state"],
            content=row["content"],
            content_hash=row["content_hash"],
            score_json=row["score_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_at=row["approved_at"],
            approved_by=row["approved_by"],
        )

    @staticmethod
    def _load_links(conn: sqlite3.Connection, spec_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT task_id FROM spec_plan_links WHERE spec_id = ?", (spec_id,)
        ).fetchall()
        return [r["task_id"] for r in rows]
