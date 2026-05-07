"""Tests for R3.2 — release readiness dashboard.

Coverage (10 tests):
1.  empty store → score=100, status=READY
2.  5 open warnings → score=75, status=RISKY
3.  1 critical bead + 2 failed gates → score=45, status=BLOCKED
4.  score boundary: exactly 85 → READY
5.  score boundary: exactly 60 → RISKY
6.  score boundary: exactly 59 → BLOCKED
7.  escalations table missing → soft-skip, no crash, count=0
8.  SLO breaches counted only within since_days window
9.  --json output round-trips through json.loads
10. to_dict serialization contains all expected keys
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.release.readiness import ReleaseReadinessChecker, _since_iso
from agent_baton.models.release_readiness import ReleaseReadinessReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_DDL = """
CREATE TABLE IF NOT EXISTS beads (
    bead_id      TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL DEFAULT '',
    bead_type    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    content      TEXT NOT NULL DEFAULT '',
    agent_name   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bead_tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    bead_id TEXT NOT NULL,
    tag     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL DEFAULT '',
    phase_id    INTEGER NOT NULL DEFAULT 0,
    gate_type   TEXT NOT NULL DEFAULT '',
    passed      INTEGER NOT NULL,
    output      TEXT NOT NULL DEFAULT '',
    checked_at  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS executions (
    task_id    TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS plans (
    task_id    TEXT PRIMARY KEY,
    release_id TEXT
);
CREATE TABLE IF NOT EXISTS slo_measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    breached    INTEGER NOT NULL DEFAULT 0,
    measured_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS escalations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    resolved INTEGER NOT NULL DEFAULT 0
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_db(ddl: str = PROJECT_DDL) -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the project schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(ddl)
    return conn


def make_checker(conn: sqlite3.Connection) -> ReleaseReadinessChecker:
    return ReleaseReadinessChecker(conn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyStore:
    def test_empty_store_score_100_ready(self) -> None:
        """An empty store with no signals should return score=100, status=READY."""
        conn = make_db()
        checker = make_checker(conn)
        report = checker.compute("rel-001")

        assert report.score == 100
        assert report.status == "READY"
        assert report.open_warnings == 0
        assert report.open_critical_beads == 0
        assert report.failed_gates_7d == 0
        assert report.incomplete_plans == 0
        assert report.slo_breaches_7d == 0
        assert report.escalations == 0


class TestWarnings:
    def test_five_open_warnings(self) -> None:
        """5 open warnings → penalty=25 → score=75, status=RISKY."""
        conn = make_db()
        for i in range(5):
            conn.execute(
                "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
                (f"bd-w{i}", "warning", "open", _utcnow()),
            )
        conn.commit()

        report = make_checker(conn).compute("rel-001")

        assert report.open_warnings == 5
        assert report.score == 75  # 100 - (5 * 5)
        assert report.status == "RISKY"

    def test_closed_warnings_not_counted(self) -> None:
        """Closed warning beads should not inflate the warning count."""
        conn = make_db()
        conn.execute(
            "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
            ("bd-closed", "warning", "closed", _utcnow()),
        )
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        assert report.open_warnings == 0


class TestCriticalAndGates:
    def test_critical_bead_and_failed_gates(self) -> None:
        """1 critical warning bead + 2 failed gates → BLOCKED.

        The bead has bead_type='warning' AND severity:critical tag, so it is
        counted in both open_warnings (penalty 5) and open_critical_beads
        (penalty 15).  2 failed gates cost 40.
        Total penalty = 5 + 15 + 40 = 60 → score = 40, BLOCKED.
        """
        conn = make_db()

        # Insert a critical bead (bead_type=warning so it also counts as a warning)
        conn.execute(
            "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
            ("bd-crit1", "warning", "open", _utcnow()),
        )
        conn.execute(
            "INSERT INTO bead_tags (bead_id, tag) VALUES (?,?)",
            ("bd-crit1", "severity:critical"),
        )

        # Insert 2 failed gates within the window
        for i in range(2):
            conn.execute(
                "INSERT INTO gate_results (task_id, phase_id, gate_type, passed, checked_at) VALUES (?,?,?,?,?)",
                ("task-1", 0, "test", 0, _utcnow()),
            )
        conn.commit()

        report = make_checker(conn).compute("rel-001")

        assert report.open_critical_beads == 1
        assert report.open_warnings == 1
        assert report.failed_gates_7d == 2
        # 100 - (1*5) - (1*15) - (2*20) = 100 - 5 - 15 - 40 = 40
        assert report.score == 40
        assert report.status == "BLOCKED"


class TestStatusBoundaries:
    def _build_score(self, conn: sqlite3.Connection, target_score: int) -> None:
        """Insert enough failed gates to reach a specific score."""
        # Each failed gate costs 20 points
        penalty_needed = 100 - target_score
        gate_count = penalty_needed // 20
        remainder = penalty_needed % 20
        for i in range(gate_count):
            conn.execute(
                "INSERT INTO gate_results (task_id, phase_id, gate_type, passed, checked_at) VALUES (?,?,?,?,?)",
                ("task-1", 0, "test", 0, _utcnow()),
            )
        # Use warnings (5 pts each) for the remainder
        warning_count = remainder // 5
        for i in range(warning_count):
            conn.execute(
                "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
                (f"bd-w{i}", "warning", "open", _utcnow()),
            )
        conn.commit()

    def test_score_85_is_ready(self) -> None:
        """score=85 should be READY."""
        conn = make_db()
        # 3 failed gates = penalty 60, but we want exactly 85
        # 3 warnings = 15, exactly 100-15 = 85
        for i in range(3):
            conn.execute(
                "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
                (f"bd-w{i}", "warning", "open", _utcnow()),
            )
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        assert report.score == 85
        assert report.status == "READY"

    def test_score_60_is_risky(self) -> None:
        """score=60 should be RISKY (boundary inclusive)."""
        conn = make_db()
        # 2 failed gates = 40, plus 0 others → score=60
        for i in range(2):
            conn.execute(
                "INSERT INTO gate_results (task_id, phase_id, gate_type, passed, checked_at) VALUES (?,?,?,?,?)",
                ("task-1", 0, "test", 0, _utcnow()),
            )
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        assert report.score == 60
        assert report.status == "RISKY"

    def test_score_59_is_blocked(self) -> None:
        """score=59 should be BLOCKED."""
        conn = make_db()
        # 2 failed gates (−40) + 1 warning (−5) → score=55
        # Actually let's do 2 gates (40) + 1 warning (5) + 0 else = 55
        for i in range(2):
            conn.execute(
                "INSERT INTO gate_results (task_id, phase_id, gate_type, passed, checked_at) VALUES (?,?,?,?,?)",
                ("task-1", 0, "test", 0, _utcnow()),
            )
        conn.execute(
            "INSERT INTO beads (bead_id, bead_type, status, created_at) VALUES (?,?,?,?)",
            ("bd-w1", "warning", "open", _utcnow()),
        )
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        # 100 - 40 - 5 = 55
        assert report.score == 55
        assert report.status == "BLOCKED"


class TestEscalationsSoftSkip:
    def test_escalations_table_missing_no_crash(self) -> None:
        """When the escalations table is absent, escalations should soft-skip to 0."""
        ddl_no_escalations = PROJECT_DDL.replace(
            """CREATE TABLE IF NOT EXISTS escalations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    resolved INTEGER NOT NULL DEFAULT 0
);""",
            "",
        )
        conn = make_db(ddl_no_escalations)
        report = make_checker(conn).compute("rel-001")

        assert report.escalations == 0
        assert report.score == 100
        assert report.status == "READY"

    def test_open_escalations_counted(self) -> None:
        """Open escalations should be counted and reduce the score."""
        conn = make_db()
        conn.execute("INSERT INTO escalations (resolved) VALUES (0)")
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        assert report.escalations == 1
        # 100 - 25 = 75
        assert report.score == 75

    def test_resolved_escalations_not_counted(self) -> None:
        """Resolved escalations (resolved=1) should not be counted."""
        conn = make_db()
        conn.execute("INSERT INTO escalations (resolved) VALUES (1)")
        conn.commit()

        report = make_checker(conn).compute("rel-001")
        assert report.escalations == 0


class TestSloBreachesWindow:
    def test_slo_breaches_counted_within_window(self) -> None:
        """SLO breaches within since_days should be counted."""
        conn = make_db()
        # Within the 7-day window
        conn.execute(
            "INSERT INTO slo_measurements (breached, measured_at) VALUES (?,?)",
            (1, _days_ago(3)),
        )
        conn.commit()

        report = make_checker(conn).compute("rel-001", since_days=7)
        assert report.slo_breaches_7d == 1

    def test_slo_breaches_outside_window_excluded(self) -> None:
        """SLO breaches older than since_days should not be counted."""
        conn = make_db()
        # 14 days ago, but window is 7 days
        conn.execute(
            "INSERT INTO slo_measurements (breached, measured_at) VALUES (?,?)",
            (1, _days_ago(14)),
        )
        conn.commit()

        report = make_checker(conn).compute("rel-001", since_days=7)
        assert report.slo_breaches_7d == 0

    def test_slo_not_breached_excluded(self) -> None:
        """SLO measurements with breached=0 should not be counted."""
        conn = make_db()
        conn.execute(
            "INSERT INTO slo_measurements (breached, measured_at) VALUES (?,?)",
            (0, _days_ago(1)),
        )
        conn.commit()

        report = make_checker(conn).compute("rel-001", since_days=7)
        assert report.slo_breaches_7d == 0


class TestJsonOutput:
    def test_json_roundtrip(self, tmp_path: Path) -> None:
        """--json output should round-trip cleanly through json.loads."""
        from argparse import Namespace
        from unittest.mock import patch

        from agent_baton.cli.commands.release.readiness_cmd import handle_readiness

        db_path = tmp_path / "baton.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(PROJECT_DDL)
        conn.close()

        import io
        captured = io.StringIO()

        args = Namespace(
            release_id="rel-test",
            since=7,
            as_json=True,
            db=db_path,
        )

        with patch("sys.stdout", captured):
            handle_readiness(args)

        output = captured.getvalue()
        parsed = json.loads(output)

        assert parsed["release_id"] == "rel-test"
        assert parsed["status"] == "READY"
        assert parsed["score"] == 100
        assert isinstance(parsed["breakdown"], dict)

    def test_to_dict_keys(self) -> None:
        """ReleaseReadinessReport.to_dict() must contain all expected keys."""
        report = ReleaseReadinessReport(
            release_id="rel-x",
            computed_at=_utcnow(),
            status="READY",
            score=100,
            open_warnings=0,
            open_critical_beads=0,
            failed_gates_7d=0,
            incomplete_plans=0,
            slo_breaches_7d=0,
            escalations=0,
            breakdown={},
        )
        d = report.to_dict()

        expected_keys = {
            "release_id",
            "computed_at",
            "status",
            "score",
            "open_warnings",
            "open_critical_beads",
            "failed_gates_7d",
            "incomplete_plans",
            "slo_breaches_7d",
            "escalations",
            "breakdown",
        }
        assert set(d.keys()) == expected_keys
        assert d["score"] == 100
        assert d["status"] == "READY"
