"""FinOps tenancy attribution-coverage scanner (bd-ebd8).

Scans ``usage_records`` in a baton SQLite database and reports the
percentage of rows that carry a non-default value in each tenancy
dimension:

* ``org_id``     — tagged when ``!= 'default'``
* ``team_id``    — tagged when ``!= 'default'``
* ``user_id``    — tagged when ``NOT IN ('local-user', 'default')``
* ``cost_center``— tagged when ``!= ''``

These thresholds mirror the NON-NULL DEFAULT values introduced in the
F0.2 schema migration (schema v16).  Rows at these defaults are
"unattributed" — they roll up under the anonymous bucket and produce
meaningless chargeback attribution.

This module is **read-only**: it never writes to ``usage_records``.

Examples
--------
::

    from agent_baton.core.observability.attribution_coverage import CoverageScanner
    report = CoverageScanner(db_path=Path("baton.db")).scan()
    print(report.to_table())
    print(report.to_json())
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default-value sentinels
# ---------------------------------------------------------------------------

#: ``org_id`` values that indicate "not yet attributed".
DEFAULT_ORG_IDS: frozenset[str] = frozenset({"default"})

#: ``team_id`` values that indicate "not yet attributed".
DEFAULT_TEAM_IDS: frozenset[str] = frozenset({"default"})

#: ``user_id`` values that indicate "not yet attributed" (local dev / CI default).
DEFAULT_USER_IDS: frozenset[str] = frozenset({"local-user", "default"})

#: ``cost_center`` is unset when the string is empty.
_EMPTY_COST_CENTER: str = ""


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass
class DimensionCoverage:
    """Coverage statistics for a single tenancy dimension."""

    dimension: str
    tagged_rows: int
    total_rows: int
    coverage_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "tagged_rows": self.tagged_rows,
            "total_rows": self.total_rows,
            "coverage_pct": round(self.coverage_pct, 2),
        }


@dataclass
class AttributionCoverageReport:
    """Output of :meth:`CoverageScanner.scan`.

    Contains per-dimension coverage statistics and the total row count.
    """

    total_rows: int
    dimensions: list[DimensionCoverage] = field(default_factory=list)
    db_path: str = ""

    @property
    def org_id_pct(self) -> float:
        return self._pct("org_id")

    @property
    def team_id_pct(self) -> float:
        return self._pct("team_id")

    @property
    def user_id_pct(self) -> float:
        return self._pct("user_id")

    @property
    def cost_center_pct(self) -> float:
        return self._pct("cost_center")

    def _pct(self, dimension: str) -> float:
        for d in self.dimensions:
            if d.dimension == dimension:
                return d.coverage_pct
        return 0.0

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_table(self) -> str:
        """Render as a human-readable aligned table.

        Example output::

            Attribution Coverage Report
            ===========================
            Total rows: 33

            Dimension     Tagged    Total    Coverage
            ----------    ------    -----    --------
            org_id             4        6      66.67%
            team_id            2        6      33.33%
            user_id            2        6      33.33%
            cost_center        2        6      33.33%

        When ``total_rows == 0`` the table still renders with 0.00% for
        every dimension and a note that the database is empty.
        """
        lines: list[str] = []
        lines.append("Attribution Coverage Report")
        lines.append("===========================")
        if self.total_rows == 0:
            lines.append("Total rows: 0  (database is empty — no attribution data yet)")
            lines.append("")
            lines.append(
                f"{'Dimension':<16}{'Tagged':>8}{'Total':>8}{'Coverage':>12}"
            )
            lines.append(f"{'----------':<16}{'------':>8}{'-----':>8}{'--------':>12}")
            for dim in self.dimensions:
                lines.append(
                    f"{dim.dimension:<16}{dim.tagged_rows:>8}{dim.total_rows:>8}"
                    f"{'0.00%':>12}"
                )
        else:
            lines.append(f"Total rows: {self.total_rows}")
            lines.append("")
            lines.append(
                f"{'Dimension':<16}{'Tagged':>8}{'Total':>8}{'Coverage':>12}"
            )
            lines.append(f"{'----------':<16}{'------':>8}{'-----':>8}{'--------':>12}")
            for dim in self.dimensions:
                pct_str = f"{dim.coverage_pct:.2f}%"
                lines.append(
                    f"{dim.dimension:<16}{dim.tagged_rows:>8}{dim.total_rows:>8}"
                    f"{pct_str:>12}"
                )
        lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Render as pretty JSON.

        Schema::

            {
              "total_rows": <int>,
              "db_path": "<str>",
              "dimensions": [
                {
                  "dimension": "<str>",
                  "tagged_rows": <int>,
                  "total_rows": <int>,
                  "coverage_pct": <float>
                },
                ...
              ]
            }
        """
        return json.dumps(
            {
                "total_rows": self.total_rows,
                "db_path": self.db_path,
                "dimensions": [d.to_dict() for d in self.dimensions],
            },
            indent=2,
            sort_keys=False,
        )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class CoverageScanner:
    """Compute tenancy attribution coverage from a baton SQLite database.

    Args:
        db_path: Path to a baton.db (per-project) or central.db.  The
            database must already contain the F0.2 tenancy columns
            (schema v16+, i.e. ``org_id``, ``team_id``, ``user_id``,
            ``cost_center`` on ``usage_records``).

    Raises:
        FileNotFoundError: If *db_path* does not exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        if not self._db_path.exists():
            raise FileNotFoundError(f"Database not found: {self._db_path}")

    @property
    def db_path(self) -> Path:
        return self._db_path

    def scan(self) -> AttributionCoverageReport:
        """Scan ``usage_records`` and return an :class:`AttributionCoverageReport`.

        The query runs a single pass over ``usage_records`` using
        conditional-COUNT aggregates so it stays ``O(1)`` in extra
        round-trips regardless of how many dimensions we check.
        """
        # Build the IN-list placeholder for user_id defaults
        user_default_list = ", ".join(
            f"'{v}'" for v in sorted(DEFAULT_USER_IDS)
        )

        sql = f"""
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN org_id  NOT IN ('default')          THEN 1 ELSE 0 END) AS org_tagged,
                SUM(CASE WHEN team_id NOT IN ('default')          THEN 1 ELSE 0 END) AS team_tagged,
                SUM(CASE WHEN user_id NOT IN ({user_default_list}) THEN 1 ELSE 0 END) AS user_tagged,
                SUM(CASE WHEN cost_center <> ''                    THEN 1 ELSE 0 END) AS cc_tagged
            FROM usage_records
        """

        with sqlite3.connect(str(self._db_path), timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql).fetchone()

        total = int(row["total_rows"] or 0)

        def _pct(tagged: int) -> float:
            if total == 0:
                return 0.0
            return (tagged / total) * 100.0

        org_tagged = int(row["org_tagged"] or 0)
        team_tagged = int(row["team_tagged"] or 0)
        user_tagged = int(row["user_tagged"] or 0)
        cc_tagged = int(row["cc_tagged"] or 0)

        dimensions = [
            DimensionCoverage(
                dimension="org_id",
                tagged_rows=org_tagged,
                total_rows=total,
                coverage_pct=_pct(org_tagged),
            ),
            DimensionCoverage(
                dimension="team_id",
                tagged_rows=team_tagged,
                total_rows=total,
                coverage_pct=_pct(team_tagged),
            ),
            DimensionCoverage(
                dimension="user_id",
                tagged_rows=user_tagged,
                total_rows=total,
                coverage_pct=_pct(user_tagged),
            ),
            DimensionCoverage(
                dimension="cost_center",
                tagged_rows=cc_tagged,
                total_rows=total,
                coverage_pct=_pct(cc_tagged),
            ),
        ]

        return AttributionCoverageReport(
            total_rows=total,
            dimensions=dimensions,
            db_path=str(self._db_path),
        )
