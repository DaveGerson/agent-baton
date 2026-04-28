"""FinOps chargeback / showback report builder (O1.2).

Produces token + USD spend attribution across the F0.2 tenancy hierarchy
(org -> team -> project -> user, plus cost_center) by joining the
``usage_records`` and ``agent_usage`` tables and applying the
:mod:`agent_baton.core.engine.cost_estimator` model price table.

This module is **read-only**: it never writes to ``usage_records`` or
``agent_usage``.  It does optionally CREATE a ``v_chargeback`` SQL view
on first use, which is a pure projection over the two existing tables.

Design notes
------------
* Stdlib only (``sqlite3`` + ``csv`` + ``json`` + ``datetime``).
* Pricing comes from ``cost_estimator.MODEL_PRICING`` so chargeback and
  forecast share a single source of truth.
* A row is emitted **per ``(scope, model)`` pair** so a team that ran
  Opus + Sonnet in the same period appears as two rows.  Aggregation
  scope is controlled by ``group_by`` (``org``, ``team``, ``project``,
  ``user``, ``cost_center``).
* Per-project DBs have no ``project_id`` column; the report synthesises
  ``project_id = 'default'`` for those rows so consumers can union across
  central + per-project DBs.

Schema gap handling
-------------------
F0.2 added the tenancy columns with NON-NULL defaults
(``org_id='default'``, ``team_id='default'``, ``user_id='local-user'``,
``cost_center=''``).  Legacy rows therefore roll up under the ``default``
buckets rather than NULL.  Callers that need to detect "unattributed"
spend should look for the literal default values.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_baton.core.engine.cost_estimator import MODEL_PRICING, normalise_model

# ---------------------------------------------------------------------------
# Group-by configuration
# ---------------------------------------------------------------------------

#: Allowed values for ``--group-by``.
VALID_GROUP_BY: tuple[str, ...] = ("org", "team", "project", "user", "cost_center")

#: Map ``group_by`` value -> the ``usage_records`` column to group on.
_GROUP_COL: dict[str, str] = {
    "org": "org_id",
    "team": "team_id",
    "project": "project_id",
    "user": "user_id",
    "cost_center": "cost_center",
}

#: Default lookback window in days when ``--since`` is not provided.
DEFAULT_LOOKBACK_DAYS: int = 30


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

#: Canonical column ordering for CSV/JSON output.
CSV_COLUMNS: tuple[str, ...] = (
    "org",
    "team",
    "project",
    "cost_center",
    "user",
    "model",
    "total_tokens",
    "total_cost_usd",
    "step_count",
    "period_start",
    "period_end",
    "last_activity",
)


@dataclass
class ChargebackRow:
    """One attribution row in the chargeback report.

    Each row is keyed by ``(scope_value, model)`` -- a team that ran two
    models produces two rows.  Fields that are not part of the active
    grouping carry the empty string so the JSON/CSV shape is uniform.
    """

    org: str = ""
    team: str = ""
    project: str = ""
    cost_center: str = ""
    user: str = ""
    model: str = ""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    step_count: int = 0
    period_start: str = ""
    period_end: str = ""
    last_activity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "org": self.org,
            "team": self.team,
            "project": self.project,
            "cost_center": self.cost_center,
            "user": self.user,
            "model": self.model,
            "total_tokens": int(self.total_tokens),
            "total_cost_usd": round(float(self.total_cost_usd), 6),
            "step_count": int(self.step_count),
            "period_start": self.period_start,
            "period_end": self.period_end,
            "last_activity": self.last_activity,
        }


@dataclass
class ChargebackReport:
    """Output of :meth:`ChargebackBuilder.build`.

    Wraps the rows plus the period metadata so callers always know what
    window the numbers cover.
    """

    rows: list[ChargebackRow] = field(default_factory=list)
    group_by: str = "project"
    period_start: str = ""
    period_end: str = ""
    db_path: str = ""

    def to_csv(self) -> str:
        """Render the report as CSV with the canonical header."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in self.rows:
            writer.writerow(row.to_dict())
        return buf.getvalue()

    def to_json(self) -> str:
        """Render the report as pretty JSON.

        Structure:
        ``{"group_by": ..., "period_start": ..., "period_end": ...,
        "rows": [...]}``
        """
        return json.dumps(
            {
                "group_by": self.group_by,
                "period_start": self.period_start,
                "period_end": self.period_end,
                "db_path": self.db_path,
                "rows": [r.to_dict() for r in self.rows],
            },
            indent=2,
            sort_keys=False,
        )


# ---------------------------------------------------------------------------
# View management
# ---------------------------------------------------------------------------

#: SQL that creates the ``v_chargeback`` view.  The view normalises the
#: shape so per-project and central DBs can both be queried with the same
#: WHERE clauses.  It joins agent_usage onto usage_records on task_id,
#: then exposes the tenancy columns + model + tokens + duration + the
#: usage_record timestamp as ``activity_ts``.
#:
#: Two flavours -- per-project DBs lack ``project_id`` so we synthesise
#: it; central DBs include it natively.
_VIEW_PROJECT_DB = """
DROP VIEW IF EXISTS v_chargeback;
CREATE VIEW v_chargeback AS
SELECT
    ur.task_id        AS task_id,
    ur.timestamp      AS activity_ts,
    ur.org_id         AS org_id,
    ur.team_id        AS team_id,
    'default'         AS project_id,
    ur.user_id        AS user_id,
    ur.cost_center    AS cost_center,
    au.model          AS model,
    au.estimated_tokens AS tokens,
    au.steps          AS steps,
    au.duration_seconds AS duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id;
"""

_VIEW_CENTRAL_DB = """
DROP VIEW IF EXISTS v_chargeback;
CREATE VIEW v_chargeback AS
SELECT
    ur.task_id        AS task_id,
    ur.timestamp      AS activity_ts,
    ur.org_id         AS org_id,
    ur.team_id        AS team_id,
    ur.project_id     AS project_id,
    ur.user_id        AS user_id,
    ur.cost_center    AS cost_center,
    au.model          AS model,
    au.estimated_tokens AS tokens,
    au.steps          AS steps,
    au.duration_seconds AS duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
    AND au.project_id = ur.project_id;
"""


def _has_project_id_column(conn: sqlite3.Connection) -> bool:
    """Return True if usage_records has a project_id column (central.db)."""
    cur = conn.execute("PRAGMA table_info(usage_records)")
    cols = {row[1] for row in cur.fetchall()}
    return "project_id" in cols


def _ensure_view(conn: sqlite3.Connection) -> None:
    """Create or refresh the ``v_chargeback`` view for *conn*.

    The view is rebuilt every call (DROP IF EXISTS + CREATE) so that a DB
    upgraded mid-life from per-project to central shape gets the right
    flavour.  This is a metadata-only operation -- no row writes.
    """
    if _has_project_id_column(conn):
        conn.executescript(_VIEW_CENTRAL_DB)
    else:
        conn.executescript(_VIEW_PROJECT_DB)
    conn.commit()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class ChargebackBuilder:
    """Build :class:`ChargebackReport` from a baton SQLite database.

    Args:
        db_path: Path to a baton.db (per-project) or central.db.  The
            database must already contain the F0.2 tenancy columns
            (schema v16+).

    Raises:
        FileNotFoundError: If the database does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        if not self._db_path.exists():
            raise FileNotFoundError(f"Database not found: {self._db_path}")

    @property
    def db_path(self) -> Path:
        return self._db_path

    def build(
        self,
        since: date | datetime | str | None = None,
        until: date | datetime | str | None = None,
        group_by: str = "project",
    ) -> ChargebackReport:
        """Compute a chargeback report.

        Args:
            since: Lower bound on ``usage_records.timestamp`` (inclusive).
                Accepts a ``date``, ``datetime``, or ISO-8601 string.
                Defaults to ``today - DEFAULT_LOOKBACK_DAYS``.
            until: Upper bound on ``usage_records.timestamp`` (inclusive).
                Defaults to *now*.
            group_by: One of :data:`VALID_GROUP_BY`.

        Returns:
            A populated :class:`ChargebackReport`.

        Raises:
            ValueError: If ``group_by`` is not a recognised scope.
        """
        if group_by not in VALID_GROUP_BY:
            raise ValueError(
                f"group_by must be one of {VALID_GROUP_BY}, got {group_by!r}"
            )

        since_iso = _to_iso(since) if since is not None else _default_since_iso()
        until_iso = _to_iso(until) if until is not None else _now_iso()
        scope_col = _GROUP_COL[group_by]

        with sqlite3.connect(str(self._db_path), timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_view(conn)
            rows = self._query(conn, scope_col, since_iso, until_iso)

        chargeback_rows = [
            self._row_to_chargeback(group_by, r, since_iso, until_iso)
            for r in rows
        ]
        return ChargebackReport(
            rows=chargeback_rows,
            group_by=group_by,
            period_start=since_iso,
            period_end=until_iso,
            db_path=str(self._db_path),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _query(
        self,
        conn: sqlite3.Connection,
        scope_col: str,
        since_iso: str,
        until_iso: str,
    ) -> list[sqlite3.Row]:
        """Run the aggregation query.

        Aggregates over ``v_chargeback`` rows whose ``activity_ts`` falls
        in ``[since_iso, until_iso]`` and whose ``model`` is non-empty
        (rows from usage_records with no agent_usage join produce NULL
        models -- those represent zero-token tasks and are excluded
        from the report).
        """
        # We always select all five tenancy columns so the row can populate
        # whichever scope is active without re-querying.  The GROUP BY
        # collapses on (scope, model) -- non-scope dimensions get
        # MAX()-style placeholders since they may legitimately differ
        # within the bucket (e.g. multiple users in one team).  When the
        # scope is fine-grained (user), all dims are stable.
        sql = f"""
            SELECT
                {scope_col} AS scope_value,
                COALESCE(MAX(org_id), '')         AS org_id,
                COALESCE(MAX(team_id), '')        AS team_id,
                COALESCE(MAX(project_id), '')     AS project_id,
                COALESCE(MAX(user_id), '')        AS user_id,
                COALESCE(MAX(cost_center), '')    AS cost_center,
                COALESCE(model, '')               AS model,
                COALESCE(SUM(tokens), 0)          AS total_tokens,
                COALESCE(SUM(steps), 0)           AS step_count,
                MAX(activity_ts)                  AS last_activity
            FROM v_chargeback
            WHERE activity_ts >= ?
              AND activity_ts <= ?
              AND model IS NOT NULL
              AND model <> ''
            GROUP BY {scope_col}, model
            ORDER BY {scope_col}, model
        """
        return list(conn.execute(sql, (since_iso, until_iso)).fetchall())

    @staticmethod
    def _row_to_chargeback(
        group_by: str,
        row: sqlite3.Row,
        since_iso: str,
        until_iso: str,
    ) -> ChargebackRow:
        tokens = int(row["total_tokens"] or 0)
        model = row["model"] or ""
        family = normalise_model(model)
        rate = MODEL_PRICING.get(family, MODEL_PRICING["sonnet"])
        cost = (tokens / 1_000_000.0) * rate

        # Populate the canonical 5 tenancy fields from the row.  When a
        # scope is selected (e.g. team) the non-scope dims are best-
        # effort MAX() values from the bucket -- still useful context.
        scope_value = row["scope_value"] or ""
        org = row["org_id"] or ""
        team = row["team_id"] or ""
        project = row["project_id"] or ""
        user = row["user_id"] or ""
        cost_center = row["cost_center"] or ""

        # Force the scope column to its grouping key (defends against
        # MAX() picking a different value when the bucket is the scope).
        if group_by == "org":
            org = scope_value
        elif group_by == "team":
            team = scope_value
        elif group_by == "project":
            project = scope_value
        elif group_by == "user":
            user = scope_value
        elif group_by == "cost_center":
            cost_center = scope_value

        return ChargebackRow(
            org=org,
            team=team,
            project=project,
            cost_center=cost_center,
            user=user,
            model=family,
            total_tokens=tokens,
            total_cost_usd=cost,
            step_count=int(row["step_count"] or 0),
            period_start=since_iso,
            period_end=until_iso,
            last_activity=row["last_activity"] or "",
        )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_since_iso() -> str:
    today = datetime.now(timezone.utc)
    since = today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(value: date | datetime | str) -> str:
    """Normalise a date/datetime/string to ISO-8601 ``YYYY-MM-DDTHH:MM:SSZ``.

    Bare ``date`` values are pinned to midnight UTC; bare ISO date
    strings (``YYYY-MM-DD``) become ``YYYY-MM-DDT00:00:00Z``.
    """
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return _default_since_iso()
        # Already ISO-ish?  Trust the caller.
        if "T" in s:
            return s if s.endswith("Z") or "+" in s else s + "Z"
        # Bare date string.
        try:
            d = date.fromisoformat(s)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Unrecognised date string: {value!r}") from exc
        return f"{d.isoformat()}T00:00:00Z"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"
    raise TypeError(f"Unsupported time type: {type(value).__name__}")
