"""Tests for ``baton finops attribution-coverage`` subcommand (bd-ebd8).

Fixtures
--------
* ``db_with_mixed_rows`` — 6 rows: 2 fully-tagged, 2 org-only, 2 all-default.
* ``empty_db`` — no rows at all.

Assertions
----------
* Coverage percentages match expectations per dimension.
* Empty DB returns 0% for every dimension without crashing.
* JSON output schema is well-formed.
* Human-readable table output contains the dimension names and % values.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from io import StringIO
from pathlib import Path

import pytest

from agent_baton.core.observability.attribution_coverage import (
    AttributionCoverageReport,
    CoverageScanner,
    DEFAULT_USER_IDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project_schema(conn: sqlite3.Connection) -> None:
    """Minimal per-project schema (no project_id column on usage_records)."""
    conn.executescript(
        """
        CREATE TABLE usage_records (
            task_id           TEXT PRIMARY KEY,
            timestamp         TEXT NOT NULL,
            total_agents      INTEGER NOT NULL DEFAULT 0,
            risk_level        TEXT NOT NULL DEFAULT 'LOW',
            sequencing_mode   TEXT NOT NULL DEFAULT 'phased_delivery',
            gates_passed      INTEGER NOT NULL DEFAULT 0,
            gates_failed      INTEGER NOT NULL DEFAULT 0,
            outcome           TEXT NOT NULL DEFAULT '',
            notes             TEXT NOT NULL DEFAULT '',
            org_id            TEXT NOT NULL DEFAULT 'default',
            team_id           TEXT NOT NULL DEFAULT 'default',
            user_id           TEXT NOT NULL DEFAULT 'local-user',
            spec_author_id    TEXT NOT NULL DEFAULT '',
            cost_center       TEXT NOT NULL DEFAULT ''
        );
        """
    )


def _insert_row(
    conn: sqlite3.Connection,
    task_id: str,
    org_id: str = "default",
    team_id: str = "default",
    user_id: str = "local-user",
    cost_center: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO usage_records
            (task_id, timestamp, org_id, team_id, user_id, cost_center)
        VALUES (?, '2026-04-01T00:00:00Z', ?, ?, ?, ?)
        """,
        (task_id, org_id, team_id, user_id, cost_center),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    """A baton.db with the schema but zero rows."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    _create_project_schema(conn)
    conn.close()
    return db


@pytest.fixture()
def db_with_mixed_rows(tmp_path: Path) -> Path:
    """A baton.db with 6 rows:

    - 2 fully tagged  (org=acme, team=eng, user=alice, cost_center=cc1)
    - 2 org-only      (org=acme, team=default, user=local-user, cost_center='')
    - 2 all-default   (org=default, team=default, user=local-user, cost_center='')

    Expected coverage:
      org_id:       4/6  ~  66.67%   (rows where org_id != 'default')
      team_id:      2/6  ~  33.33%   (rows where team_id != 'default')
      user_id:      2/6  ~  33.33%   (rows where user_id NOT IN defaults)
      cost_center:  2/6  ~  33.33%   (rows where cost_center != '')
    """
    db = tmp_path / "mixed.db"
    conn = sqlite3.connect(str(db))
    _create_project_schema(conn)

    # 2 fully tagged
    _insert_row(conn, "t1", org_id="acme", team_id="eng",     user_id="alice", cost_center="cc1")
    _insert_row(conn, "t2", org_id="acme", team_id="eng",     user_id="alice", cost_center="cc1")
    # 2 org-only
    _insert_row(conn, "t3", org_id="acme", team_id="default", user_id="local-user", cost_center="")
    _insert_row(conn, "t4", org_id="acme", team_id="default", user_id="local-user", cost_center="")
    # 2 all-default
    _insert_row(conn, "t5", org_id="default", team_id="default", user_id="local-user", cost_center="")
    _insert_row(conn, "t6", org_id="default", team_id="default", user_id="local-user", cost_center="")

    conn.close()
    return db


@pytest.fixture()
def db_with_alt_default_user(tmp_path: Path) -> Path:
    """One row with user_id='default' (another recognised default sentinel)."""
    db = tmp_path / "alt_default_user.db"
    conn = sqlite3.connect(str(db))
    _create_project_schema(conn)
    _insert_row(conn, "t1", user_id="default")
    _insert_row(conn, "t2", user_id="real-user")
    conn.close()
    return db


# ---------------------------------------------------------------------------
# CoverageScanner unit tests
# ---------------------------------------------------------------------------

class TestCoverageScanner:
    def test_empty_db_returns_zero_percent_no_crash(self, empty_db: Path) -> None:
        scanner = CoverageScanner(db_path=empty_db)
        report = scanner.scan()
        assert report.total_rows == 0
        assert report.org_id_pct == 0.0
        assert report.team_id_pct == 0.0
        assert report.user_id_pct == 0.0
        assert report.cost_center_pct == 0.0

    def test_mixed_rows_org_id_coverage(self, db_with_mixed_rows: Path) -> None:
        scanner = CoverageScanner(db_path=db_with_mixed_rows)
        report = scanner.scan()
        assert report.total_rows == 6
        # 4 rows have org_id != 'default'
        assert abs(report.org_id_pct - (4 / 6 * 100)) < 0.01

    def test_mixed_rows_team_id_coverage(self, db_with_mixed_rows: Path) -> None:
        scanner = CoverageScanner(db_path=db_with_mixed_rows)
        report = scanner.scan()
        # 2 rows have team_id != 'default'
        assert abs(report.team_id_pct - (2 / 6 * 100)) < 0.01

    def test_mixed_rows_user_id_coverage(self, db_with_mixed_rows: Path) -> None:
        scanner = CoverageScanner(db_path=db_with_mixed_rows)
        report = scanner.scan()
        # 2 rows have user_id NOT IN ('local-user', 'default')
        assert abs(report.user_id_pct - (2 / 6 * 100)) < 0.01

    def test_mixed_rows_cost_center_coverage(self, db_with_mixed_rows: Path) -> None:
        scanner = CoverageScanner(db_path=db_with_mixed_rows)
        report = scanner.scan()
        # 2 rows have non-empty cost_center
        assert abs(report.cost_center_pct - (2 / 6 * 100)) < 0.01

    def test_alt_default_user_sentinel_excluded(self, db_with_alt_default_user: Path) -> None:
        scanner = CoverageScanner(db_path=db_with_alt_default_user)
        report = scanner.scan()
        assert report.total_rows == 2
        # Only 'real-user' is above the default bucket
        assert abs(report.user_id_pct - 50.0) < 0.01

    def test_filenotfound_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            CoverageScanner(db_path=tmp_path / "nonexistent.db")


# ---------------------------------------------------------------------------
# AttributionCoverageReport output tests
# ---------------------------------------------------------------------------

class TestAttributionCoverageReport:
    def test_to_table_contains_dimension_names(self, db_with_mixed_rows: Path) -> None:
        report = CoverageScanner(db_path=db_with_mixed_rows).scan()
        table = report.to_table()
        for dim in ("org_id", "team_id", "user_id", "cost_center"):
            assert dim in table

    def test_to_table_contains_percentages(self, db_with_mixed_rows: Path) -> None:
        report = CoverageScanner(db_path=db_with_mixed_rows).scan()
        table = report.to_table()
        # org_id: 4/6 = 66.67%
        assert "66.67" in table or "66.7" in table

    def test_to_table_empty_shows_zero(self, empty_db: Path) -> None:
        report = CoverageScanner(db_path=empty_db).scan()
        table = report.to_table()
        assert "0 rows" in table.lower() or "0.00" in table or "total_rows: 0" in table.lower()

    def test_to_json_schema(self, db_with_mixed_rows: Path) -> None:
        report = CoverageScanner(db_path=db_with_mixed_rows).scan()
        raw = report.to_json()
        data = json.loads(raw)

        assert "total_rows" in data
        assert "dimensions" in data
        dims = data["dimensions"]
        assert isinstance(dims, list)

        dim_names = {d["dimension"] for d in dims}
        assert dim_names == {"org_id", "team_id", "user_id", "cost_center"}

        for dim in dims:
            assert "dimension" in dim
            assert "tagged_rows" in dim
            assert "total_rows" in dim
            assert "coverage_pct" in dim
            assert isinstance(dim["coverage_pct"], float)

    def test_to_json_empty_db(self, empty_db: Path) -> None:
        report = CoverageScanner(db_path=empty_db).scan()
        data = json.loads(report.to_json())
        assert data["total_rows"] == 0
        for dim in data["dimensions"]:
            assert dim["coverage_pct"] == 0.0
            assert dim["tagged_rows"] == 0

    def test_to_json_org_id_value(self, db_with_mixed_rows: Path) -> None:
        report = CoverageScanner(db_path=db_with_mixed_rows).scan()
        data = json.loads(report.to_json())
        org = next(d for d in data["dimensions"] if d["dimension"] == "org_id")
        assert org["tagged_rows"] == 4
        assert org["total_rows"] == 6
        assert abs(org["coverage_pct"] - 66.67) < 0.01


# ---------------------------------------------------------------------------
# DEFAULT_USER_IDS constant
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_user_ids_contains_sentinels(self) -> None:
        assert "local-user" in DEFAULT_USER_IDS
        assert "default" in DEFAULT_USER_IDS


# ---------------------------------------------------------------------------
# CLI handler integration test
# ---------------------------------------------------------------------------

class TestFinopsAttributionCoverageHandler:
    """Smoke-test the argparse handler wired through chargeback_cmd.handler."""

    def _invoke(self, argv: list[str], db_path: Path) -> tuple[str, int]:
        """Call handler via the argparse stack; capture stdout."""
        import argparse
        from agent_baton.cli.commands.finops import chargeback_cmd

        # Build top-level parser + finops subparser
        root = argparse.ArgumentParser(prog="baton")
        subs = root.add_subparsers(dest="cmd")
        chargeback_cmd.register(subs)

        args = root.parse_args(["finops", "attribution-coverage", "--db", str(db_path)] + argv)

        captured = StringIO()
        _real_stdout = sys.stdout
        sys.stdout = captured
        try:
            chargeback_cmd.handler(args)
        finally:
            sys.stdout = _real_stdout

        return captured.getvalue(), 0

    def test_table_output_default(self, db_with_mixed_rows: Path) -> None:
        out, _ = self._invoke([], db_with_mixed_rows)
        assert "org_id" in out
        assert "team_id" in out
        assert "user_id" in out
        assert "cost_center" in out

    def test_json_output(self, db_with_mixed_rows: Path) -> None:
        out, _ = self._invoke(["--output", "json"], db_with_mixed_rows)
        data = json.loads(out)
        assert "dimensions" in data
        assert data["total_rows"] == 6

    def test_empty_db_no_crash(self, empty_db: Path) -> None:
        out, _ = self._invoke([], empty_db)
        assert "org_id" in out  # table header still rendered

    def test_missing_db_exits_nonzero(self, tmp_path: Path) -> None:
        import argparse
        from agent_baton.cli.commands.finops import chargeback_cmd

        root = argparse.ArgumentParser(prog="baton")
        subs = root.add_subparsers(dest="cmd")
        chargeback_cmd.register(subs)

        missing = tmp_path / "no_such.db"
        args = root.parse_args(
            ["finops", "attribution-coverage", "--db", str(missing)]
        )
        with pytest.raises(SystemExit) as exc_info:
            chargeback_cmd.handler(args)
        assert exc_info.value.code != 0
