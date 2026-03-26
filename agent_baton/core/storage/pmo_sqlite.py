"""SQLite-backed PMO store -- replaces the legacy JSON file persistence.

Backed by ``~/.baton/pmo.db`` (or a caller-supplied path, commonly
``central.db`` via ``get_pmo_central_store``).  Implements the same
interface as the file-based ``PmoStore`` and extends it with:

* ``list_projects`` / ``list_programs`` / ``add_program`` -- program
  management.
* ``get_signal`` -- lookup by signal ID.
* ``create_forge_session`` / ``complete_forge_session`` /
  ``list_forge_sessions`` -- Smart Forge session lifecycle.
* ``record_metric`` / ``read_metrics`` -- time-series PMO metrics.

All public methods use parameterised queries; no string interpolation in
SQL.  The schema is defined in ``schema.PMO_SCHEMA_DDL`` and applied
automatically by ``ConnectionManager`` on first access.

Database tables accessed:
    ``projects`` -- registered project entries (PK: ``project_id``).
    ``programs`` -- program names (PK: ``name``).
    ``signals`` -- cross-project signals/alerts (PK: ``signal_id``).
    ``archived_cards`` -- completed execution cards (PK: ``card_id``).
    ``forge_sessions`` -- Smart Forge plan-creation sessions
        (PK: ``session_id``).
    ``pmo_metrics`` -- time-series metric data points (auto-increment PK).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PMO_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.pmo import PmoCard, PmoConfig, PmoProject, PmoSignal


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PmoSqliteStore:
    """SQLite-backed PMO store replacing JSON file persistence.

    Implements the same interface as ``PmoStore`` but backed by an SQLite
    database (``~/.baton/pmo.db`` or ``central.db``).  Thread-safe via
    ``ConnectionManager`` (one connection per thread, WAL mode).

    Attributes:
        _conn_mgr: The underlying ``ConnectionManager`` managing the
            SQLite connection and schema initialization.
    """

    def __init__(self, db_path: Path) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PMO_SCHEMA_DDL, SCHEMA_VERSION)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        self._conn_mgr.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):
        return self._conn_mgr.get_connection()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def register_project(self, project: PmoProject) -> None:
        """Register (or update) a project in the ``projects`` table.

        Uses ``INSERT OR REPLACE`` against the ``projects`` table keyed on
        ``project_id``.  If ``project.registered_at`` is falsy, it is set
        to the current UTC timestamp before insertion.

        Args:
            project: The project to register.  All fields are persisted.
        """
        if not project.registered_at:
            project.registered_at = _utcnow()
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO projects
                (project_id, name, path, program, color, description,
                 registered_at, ado_project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.project_id,
                project.name,
                project.path,
                project.program,
                project.color,
                project.description,
                project.registered_at,
                project.ado_project,
            ),
        )
        conn.commit()

    def unregister_project(self, project_id: str) -> bool:
        """Remove a project from the ``projects`` table.

        Args:
            project_id: Primary key of the project to remove.

        Returns:
            ``True`` if a row was deleted, ``False`` if no matching row
            existed.
        """
        conn = self._conn()
        cur = conn.execute(
            "DELETE FROM projects WHERE project_id = ?", (project_id,)
        )
        conn.commit()
        return cur.rowcount > 0

    def get_project(self, project_id: str) -> PmoProject | None:
        """Fetch a single project from the ``projects`` table by primary key.

        Args:
            project_id: The project identifier to look up.

        Returns:
            A ``PmoProject`` instance if found, otherwise ``None``.
        """
        row = self._conn().execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def list_projects(self) -> list[PmoProject]:
        """Return all registered projects ordered by name."""
        rows = self._conn().execute(
            "SELECT * FROM projects ORDER BY name"
        ).fetchall()
        return [_row_to_project(r) for r in rows]

    # ------------------------------------------------------------------
    # Programs
    # ------------------------------------------------------------------

    def add_program(self, name: str) -> None:
        """Upsert a program name into the programs table."""
        conn = self._conn()
        conn.execute(
            "INSERT OR IGNORE INTO programs (name) VALUES (?)", (name,)
        )
        conn.commit()

    def list_programs(self) -> list[str]:
        """Return all program names sorted alphabetically."""
        rows = self._conn().execute(
            "SELECT name FROM programs ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def add_signal(self, signal: PmoSignal) -> None:
        """Insert or replace a signal in the ``signals`` table.

        If ``signal.created_at`` is falsy, it is set to the current UTC
        timestamp before insertion.  Uses ``INSERT OR REPLACE`` so
        re-submitting the same ``signal_id`` updates the existing row.

        Args:
            signal: The signal to persist.
        """
        if not signal.created_at:
            signal.created_at = _utcnow()
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO signals
                (signal_id, signal_type, title, description,
                 source_project_id, severity, status,
                 created_at, resolved_at, forge_task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.signal_id,
                signal.signal_type,
                signal.title,
                signal.description,
                signal.source_project_id,
                signal.severity,
                signal.status,
                signal.created_at,
                signal.resolved_at,
                signal.forge_task_id,
            ),
        )
        conn.commit()

    def resolve_signal(self, signal_id: str) -> bool:
        """Mark a signal as resolved in the ``signals`` table.

        Sets ``status = 'resolved'`` and ``resolved_at`` to the current
        UTC timestamp.

        Args:
            signal_id: Primary key of the signal to resolve.

        Returns:
            ``True`` if a row was updated, ``False`` if the signal was
            not found.
        """
        conn = self._conn()
        cur = conn.execute(
            """
            UPDATE signals
               SET status = 'resolved', resolved_at = ?
             WHERE signal_id = ?
            """,
            (_utcnow(), signal_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def get_open_signals(self) -> list[PmoSignal]:
        """Return all signals whose status is not 'resolved'."""
        rows = self._conn().execute(
            "SELECT * FROM signals WHERE status != 'resolved' ORDER BY created_at"
        ).fetchall()
        return [_row_to_signal(r) for r in rows]

    def get_signal(self, signal_id: str) -> PmoSignal | None:
        """Return a single signal by ID, or None."""
        row = self._conn().execute(
            "SELECT * FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        return _row_to_signal(row) if row else None

    def resolve_signals(self, signal_ids: list[str]) -> tuple[list[str], list[str]]:
        """Resolve multiple signals in a single transaction.

        Issues one ``UPDATE`` per signal ID within a single connection
        transaction, collecting which IDs were found vs. not found.

        Args:
            signal_ids: List of signal IDs to resolve.

        Returns:
            A ``(resolved, not_found)`` tuple where ``resolved`` contains
            the IDs that were successfully updated and ``not_found``
            contains IDs that matched no row.
        """
        conn = self._conn()
        now = _utcnow()
        resolved: list[str] = []
        not_found: list[str] = []
        for sid in signal_ids:
            cur = conn.execute(
                """
                UPDATE signals
                   SET status = 'resolved', resolved_at = ?
                 WHERE signal_id = ?
                """,
                (now, sid),
            )
            if cur.rowcount > 0:
                resolved.append(sid)
            else:
                not_found.append(sid)
        conn.commit()
        return resolved, not_found

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def archive_card(self, card: PmoCard) -> None:
        """Persist a completed execution card in the ``archived_cards`` table.

        Uses ``INSERT OR REPLACE`` keyed on ``card_id``.  The ``agents``
        list is JSON-serialised before storage.  Archived cards appear on
        the PMO dashboard as "deployed" column items.

        Args:
            card: The card to archive.
        """
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO archived_cards
                (card_id, project_id, program, title, column_name,
                 risk_level, priority, agents, steps_completed,
                 steps_total, gates_passed, current_phase, error,
                 created_at, updated_at, external_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card.card_id,
                card.project_id,
                card.program,
                card.title,
                card.column,
                card.risk_level,
                card.priority,
                json.dumps(card.agents),
                card.steps_completed,
                card.steps_total,
                card.gates_passed,
                card.current_phase,
                card.error,
                card.created_at,
                card.updated_at,
                card.external_id,
            ),
        )
        conn.commit()

    def read_archive(self, limit: int = 100) -> list[PmoCard]:
        """Return the most-recent *limit* archived cards.

        Queries the ``archived_cards`` table ordered by ``rowid``
        descending (insertion order) and reverses the result so the
        caller receives oldest-first within the window, matching the
        original JSONL append-only behaviour.

        Args:
            limit: Maximum number of cards to return.

        Returns:
            List of ``PmoCard`` instances, oldest-first within the
            requested window.
        """
        rows = self._conn().execute(
            """
            SELECT * FROM archived_cards
             ORDER BY rowid DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        # Reverse so oldest-first within the window, matching the JSONL behaviour
        return [_row_to_card(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # Forge Sessions
    # ------------------------------------------------------------------

    def create_forge_session(
        self, session_id: str, project_id: str, title: str
    ) -> None:
        """Create a new Smart Forge session in the ``forge_sessions`` table.

        A forge session tracks the lifecycle of a consultative plan
        creation flow.  It starts as ``'active'`` and transitions to
        ``'completed'`` via ``complete_forge_session`` once the user
        approves the generated plan.

        Args:
            session_id: Unique session identifier (typically a UUID).
            project_id: The project this forge session belongs to.
            title: Human-readable title describing the planned work.
        """
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO forge_sessions
                (session_id, project_id, title, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (session_id, project_id, title, _utcnow()),
        )
        conn.commit()

    def complete_forge_session(self, session_id: str, task_id: str) -> None:
        """Mark a forge session as completed and record the resulting plan.

        Updates the ``forge_sessions`` row: sets ``status = 'completed'``,
        records the ``task_id`` of the generated execution plan, and
        timestamps ``completed_at``.

        Args:
            session_id: The session to complete.
            task_id: The ``task_id`` of the plan produced by this session.
        """
        conn = self._conn()
        conn.execute(
            """
            UPDATE forge_sessions
               SET status = 'completed', task_id = ?, completed_at = ?
             WHERE session_id = ?
            """,
            (task_id, _utcnow(), session_id),
        )
        conn.commit()

    def list_forge_sessions(self, status: str | None = None) -> list[dict]:
        """Return forge sessions as plain dicts, most recent first.

        Queries the ``forge_sessions`` table ordered by ``created_at``
        descending.

        Args:
            status: Optional filter (e.g. ``'active'`` or ``'completed'``).
                If ``None``, all sessions are returned.

        Returns:
            List of dicts with keys matching the ``forge_sessions`` columns.
        """
        if status is not None:
            rows = self._conn().execute(
                "SELECT * FROM forge_sessions WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM forge_sessions ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def record_metric(
        self, program: str, metric_name: str, value: float
    ) -> None:
        """Append a metric data-point to the ``pmo_metrics`` table.

        Each call inserts a new row timestamped with the current UTC time.
        Metrics are never updated in place -- the table acts as a
        time-series append log for trend analysis.

        Args:
            program: Program code this metric belongs to (e.g. ``"RW"``).
            metric_name: Name of the metric (e.g. ``"completion_rate"``).
            value: Numeric metric value.
        """
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO pmo_metrics (timestamp, program, metric_name, metric_value)
            VALUES (?, ?, ?, ?)
            """,
            (_utcnow(), program, metric_name, value),
        )
        conn.commit()

    def read_metrics(
        self, metric_name: str, limit: int = 100
    ) -> list[dict]:
        """Return the most-recent data-points for a given metric.

        Queries ``pmo_metrics`` filtered by ``metric_name``, ordered by
        ``timestamp`` descending.

        Args:
            metric_name: The metric to query.
            limit: Maximum number of data-points to return.

        Returns:
            List of dicts with keys matching the ``pmo_metrics`` columns.
        """
        rows = self._conn().execute(
            """
            SELECT * FROM pmo_metrics
             WHERE metric_name = ?
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (metric_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # PmoConfig compatibility shim
    # ------------------------------------------------------------------

    def load_config(self) -> PmoConfig:
        """Build a ``PmoConfig`` from DB tables for backward compatibility.

        Reads ``projects``, ``programs``, and ``signals`` tables and
        assembles them into the legacy in-memory ``PmoConfig`` structure.
        Used by callers that expect the JSON-based ``PmoStore`` interface.

        Returns:
            A ``PmoConfig`` populated from all database rows.
        """
        return PmoConfig(
            projects=self.list_projects(),
            programs=self.list_programs(),
            signals=[_row_to_signal(r) for r in self._conn().execute(
                "SELECT * FROM signals ORDER BY created_at"
            ).fetchall()],
            version="1",
        )

    def save_config(self, config: PmoConfig) -> None:
        """Write a ``PmoConfig`` to DB tables for backward compatibility.

        Iterates over ``config.projects``, ``config.programs``, and
        ``config.signals``, upserting each into the corresponding table.
        Programs use ``INSERT OR IGNORE`` so existing entries are never
        removed -- this is a merge-only operation.

        Args:
            config: The configuration to persist.
        """
        for project in config.projects:
            self.register_project(project)
        for name in config.programs:
            self.add_program(name)
        for signal in config.signals:
            self.add_signal(signal)


# ------------------------------------------------------------------
# Private row-to-model converters
#
# These functions map ``sqlite3.Row`` objects returned by queries
# against the PMO tables into their corresponding data-model instances.
# Null columns are coerced to empty strings to satisfy model field
# defaults.
# ------------------------------------------------------------------

def _row_to_project(row) -> PmoProject:
    return PmoProject(
        project_id=row["project_id"],
        name=row["name"],
        path=row["path"],
        program=row["program"],
        color=row["color"] or "",
        description=row["description"] or "",
        registered_at=row["registered_at"] or "",
        ado_project=row["ado_project"] or "",
    )


def _row_to_signal(row) -> PmoSignal:
    return PmoSignal(
        signal_id=row["signal_id"],
        signal_type=row["signal_type"],
        title=row["title"],
        description=row["description"] or "",
        source_project_id=row["source_project_id"] or "",
        severity=row["severity"] or "medium",
        status=row["status"] or "open",
        created_at=row["created_at"] or "",
        resolved_at=row["resolved_at"] or "",
        forge_task_id=row["forge_task_id"] or "",
    )


def _row_to_card(row) -> PmoCard:
    agents_raw = row["agents"]
    try:
        agents = json.loads(agents_raw) if agents_raw else []
    except (json.JSONDecodeError, TypeError):
        agents = []
    return PmoCard(
        card_id=row["card_id"],
        project_id=row["project_id"],
        program=row["program"],
        title=row["title"],
        column=row["column_name"],
        risk_level=row["risk_level"] or "LOW",
        priority=row["priority"] or 0,
        agents=agents,
        steps_completed=row["steps_completed"] or 0,
        steps_total=row["steps_total"] or 0,
        gates_passed=row["gates_passed"] or 0,
        current_phase=row["current_phase"] or "",
        error=row["error"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        external_id=row["external_id"] or "",
    )
