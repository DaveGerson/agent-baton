"""Tests for the FinOps chargeback report builder (O1.2 / bd-91c7)."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.observability.chargeback import (
    CSV_COLUMNS,
    DEFAULT_LOOKBACK_DAYS,
    VALID_GROUP_BY,
    ChargebackBuilder,
    ChargebackReport,
    ChargebackRow,
)
from agent_baton.core.engine.cost_estimator import MODEL_PRICING


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Use a fixed "now" anchor so since-window tests are deterministic.
_NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_project_schema(conn: sqlite3.Connection) -> None:
    """Create just the usage_records + agent_usage tables we need.

    Mirrors the per-project DB shape (no project_id column on
    usage_records).  We only create the columns the chargeback report
    actually reads -- keeps the test schema lean and isolates from
    unrelated migration churn.
    """
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
        CREATE TABLE agent_usage (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id            TEXT NOT NULL,
            agent_name         TEXT NOT NULL,
            model              TEXT NOT NULL DEFAULT 'sonnet',
            steps              INTEGER NOT NULL DEFAULT 1,
            retries            INTEGER NOT NULL DEFAULT 0,
            gate_results       TEXT NOT NULL DEFAULT '[]',
            estimated_tokens   INTEGER NOT NULL DEFAULT 0,
            duration_seconds   REAL NOT NULL DEFAULT 0.0,
            agent_type         TEXT NOT NULL DEFAULT '',
            org_id             TEXT NOT NULL DEFAULT 'default',
            team_id            TEXT NOT NULL DEFAULT 'default',
            user_id            TEXT NOT NULL DEFAULT 'local-user',
            spec_author_id     TEXT NOT NULL DEFAULT '',
            cost_center        TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()


def _create_central_schema(conn: sqlite3.Connection) -> None:
    """Create the central.db variant (with project_id columns)."""
    conn.executescript(
        """
        CREATE TABLE usage_records (
            project_id        TEXT NOT NULL,
            task_id           TEXT NOT NULL,
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
            cost_center       TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (project_id, task_id)
        );
        CREATE TABLE agent_usage (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id         TEXT NOT NULL,
            task_id            TEXT NOT NULL,
            agent_name         TEXT NOT NULL,
            model              TEXT NOT NULL DEFAULT 'sonnet',
            steps              INTEGER NOT NULL DEFAULT 1,
            retries            INTEGER NOT NULL DEFAULT 0,
            gate_results       TEXT NOT NULL DEFAULT '[]',
            estimated_tokens   INTEGER NOT NULL DEFAULT 0,
            duration_seconds   REAL NOT NULL DEFAULT 0.0,
            agent_type         TEXT NOT NULL DEFAULT '',
            org_id             TEXT NOT NULL DEFAULT 'default',
            team_id            TEXT NOT NULL DEFAULT 'default',
            user_id            TEXT NOT NULL DEFAULT 'local-user',
            spec_author_id     TEXT NOT NULL DEFAULT '',
            cost_center        TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()


def _seed_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    timestamp: str,
    org: str = "acme",
    team: str = "platform",
    user: str = "alice",
    cost_center: str = "cc-eng",
    agents: list[tuple[str, str, int, int]] | None = None,
    project_id: str | None = None,
) -> None:
    """Insert one usage_record and its agent_usage rows.

    ``agents`` is a list of ``(agent_name, model, tokens, steps)``.
    Pass ``project_id`` only for the central-DB variant.
    """
    agents = agents or [("backend-engineer", "sonnet", 5_000, 1)]
    if project_id is None:
        conn.execute(
            "INSERT INTO usage_records "
            "(task_id, timestamp, org_id, team_id, user_id, cost_center) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, timestamp, org, team, user, cost_center),
        )
        for name, model, tokens, steps in agents:
            conn.execute(
                "INSERT INTO agent_usage "
                "(task_id, agent_name, model, estimated_tokens, steps, "
                " org_id, team_id, user_id, cost_center) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, name, model, tokens, steps,
                 org, team, user, cost_center),
            )
    else:
        conn.execute(
            "INSERT INTO usage_records "
            "(project_id, task_id, timestamp, org_id, team_id, user_id, cost_center) "
            "VALUES (?,?,?,?,?,?,?)",
            (project_id, task_id, timestamp, org, team, user, cost_center),
        )
        for name, model, tokens, steps in agents:
            conn.execute(
                "INSERT INTO agent_usage "
                "(project_id, task_id, agent_name, model, estimated_tokens, steps, "
                " org_id, team_id, user_id, cost_center) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project_id, task_id, name, model, tokens, steps,
                 org, team, user, cost_center),
            )
    conn.commit()


@pytest.fixture
def project_db(tmp_path: Path) -> Path:
    """A per-project baton.db seeded with three tasks across two teams."""
    db = tmp_path / "baton.db"
    with sqlite3.connect(str(db)) as conn:
        _create_project_schema(conn)
        # Two tasks for team "platform"
        _seed_task(
            conn,
            task_id="t1",
            timestamp=_iso(_NOW - timedelta(days=2)),
            team="platform",
            user="alice",
            agents=[("backend-engineer", "sonnet", 10_000, 2)],
        )
        _seed_task(
            conn,
            task_id="t2",
            timestamp=_iso(_NOW - timedelta(days=1)),
            team="platform",
            user="bob",
            agents=[
                ("architect", "opus", 8_000, 1),
                ("test-engineer", "sonnet", 5_000, 1),
            ],
        )
        # One task for team "data"
        _seed_task(
            conn,
            task_id="t3",
            timestamp=_iso(_NOW - timedelta(hours=6)),
            team="data",
            user="carol",
            cost_center="cc-data",
            agents=[("backend-engineer", "haiku", 4_000, 1)],
        )
        # An ANCIENT task that should fall outside the default 30-day window
        _seed_task(
            conn,
            task_id="t_old",
            timestamp=_iso(_NOW - timedelta(days=400)),
            team="platform",
            user="alice",
            agents=[("auditor", "opus", 6_000, 1)],
        )
    return db


@pytest.fixture
def central_db(tmp_path: Path) -> Path:
    """A central.db seeded with two projects."""
    db = tmp_path / "central.db"
    with sqlite3.connect(str(db)) as conn:
        _create_central_schema(conn)
        _seed_task(
            conn,
            task_id="t-a",
            project_id="proj-alpha",
            timestamp=_iso(_NOW - timedelta(days=2)),
            agents=[("backend-engineer", "sonnet", 10_000, 1)],
        )
        _seed_task(
            conn,
            task_id="t-b",
            project_id="proj-beta",
            timestamp=_iso(_NOW - timedelta(days=1)),
            agents=[("architect", "opus", 8_000, 1)],
        )
    return db


# ---------------------------------------------------------------------------
# Builder behaviour
# ---------------------------------------------------------------------------

class TestBuilder:
    def test_build_default_group_by_project(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
        )
        assert report.group_by == "project"
        # Per-project DB synthesises project_id='default' -> all rows
        # collapse onto one project, but split per model.  Models in
        # window: sonnet (15k), opus (8k), haiku (4k) => 3 rows.
        assert len(report.rows) == 3
        models = {r.model for r in report.rows}
        assert models == {"sonnet", "opus", "haiku"}
        for row in report.rows:
            assert row.project == "default"

    def test_build_group_by_team(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="team",
        )
        # platform: sonnet(15k) + opus(8k) -> 2 rows
        # data:     haiku(4k)              -> 1 row
        teams_to_models: dict[str, set[str]] = {}
        for row in report.rows:
            teams_to_models.setdefault(row.team, set()).add(row.model)
        assert teams_to_models == {
            "platform": {"sonnet", "opus"},
            "data": {"haiku"},
        }

    def test_build_group_by_user(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="user",
        )
        users = {r.user for r in report.rows}
        # alice: sonnet (t1)
        # bob:   opus + sonnet (t2)
        # carol: haiku (t3)
        assert users == {"alice", "bob", "carol"}

    def test_build_group_by_cost_center(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="cost_center",
        )
        ccs = {r.cost_center for r in report.rows}
        assert ccs == {"cc-eng", "cc-data"}

    def test_aggregation_sums_tokens(self, project_db: Path) -> None:
        """Two sonnet tasks for team=platform should sum to 15_000 tokens."""
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="team",
        )
        platform_sonnet = next(
            r for r in report.rows
            if r.team == "platform" and r.model == "sonnet"
        )
        # t1: 10_000 + t2's test-engineer: 5_000 = 15_000
        assert platform_sonnet.total_tokens == 15_000
        # Cost = 15_000 / 1_000_000 * MODEL_PRICING['sonnet']
        expected_cost = (15_000 / 1_000_000.0) * MODEL_PRICING["sonnet"]
        assert platform_sonnet.total_cost_usd == pytest.approx(expected_cost)
        # step_count = 2 (t1) + 1 (t2 test-engineer) = 3
        assert platform_sonnet.step_count == 3

    def test_since_filter_excludes_old_rows(self, project_db: Path) -> None:
        """The 400-day-old auditor/opus task must NOT appear by default."""
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=30),
            until=_NOW,
            group_by="team",
        )
        # Only the t1/t2/t3 tasks should contribute.  Specifically the
        # auditor row from t_old (opus 6_000) must NOT inflate the
        # platform/opus bucket beyond 8_000.
        platform_opus = next(
            r for r in report.rows
            if r.team == "platform" and r.model == "opus"
        )
        assert platform_opus.total_tokens == 8_000

    def test_since_filter_includes_old_rows_when_widened(
        self, project_db: Path
    ) -> None:
        """A 500-day window picks up the ancient row."""
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=500),
            until=_NOW,
            group_by="team",
        )
        platform_opus = next(
            r for r in report.rows
            if r.team == "platform" and r.model == "opus"
        )
        assert platform_opus.total_tokens == 8_000 + 6_000

    def test_multiple_models_per_scope_render_as_separate_rows(
        self, project_db: Path
    ) -> None:
        """team=platform spans sonnet + opus -> two distinct rows."""
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="team",
        )
        platform_rows = [r for r in report.rows if r.team == "platform"]
        assert len(platform_rows) == 2
        assert {r.model for r in platform_rows} == {"sonnet", "opus"}

    def test_invalid_group_by_raises(self, project_db: Path) -> None:
        with pytest.raises(ValueError):
            ChargebackBuilder(project_db).build(group_by="bogus")

    def test_missing_db_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ChargebackBuilder(tmp_path / "nope.db")

    def test_central_db_uses_real_project_id(self, central_db: Path) -> None:
        report = ChargebackBuilder(central_db).build(
            since=_NOW - timedelta(days=7),
            until=_NOW,
            group_by="project",
        )
        projects = {r.project for r in report.rows}
        assert projects == {"proj-alpha", "proj-beta"}

    def test_default_window_is_30_days(self, project_db: Path) -> None:
        """Calling build() without since uses DEFAULT_LOOKBACK_DAYS."""
        # We can't anchor "now" inside the production code, but we can
        # confirm the default produces a 30-day-shaped period_start.
        report = ChargebackBuilder(project_db).build(group_by="team")
        period_start = datetime.strptime(
            report.period_start, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        period_end = datetime.strptime(
            report.period_end, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        delta = period_end - period_start
        # Allow 1-second slack for the default-now drift.
        assert abs(delta - timedelta(days=DEFAULT_LOOKBACK_DAYS)) < timedelta(seconds=2)


# ---------------------------------------------------------------------------
# Output serialisation
# ---------------------------------------------------------------------------

class TestCsv:
    def test_csv_header_matches_schema(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7), until=_NOW,
        )
        text = report.to_csv()
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        assert tuple(header) == CSV_COLUMNS

    def test_csv_rows_round_trip_to_dicts(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7), until=_NOW, group_by="team",
        )
        text = report.to_csv()
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == len(report.rows)
        # Spot-check: every row has all canonical columns and no extras.
        for r in rows:
            assert set(r.keys()) == set(CSV_COLUMNS)


class TestJson:
    def test_json_round_trip(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7), until=_NOW, group_by="team",
        )
        text = report.to_json()
        decoded = json.loads(text)
        assert decoded["group_by"] == "team"
        assert decoded["period_start"] == report.period_start
        assert decoded["period_end"] == report.period_end
        assert isinstance(decoded["rows"], list)
        assert len(decoded["rows"]) == len(report.rows)
        # Round-trip to ChargebackRow equivalent.
        for src, decoded_row in zip(report.rows, decoded["rows"]):
            for col in CSV_COLUMNS:
                assert col in decoded_row
            assert decoded_row["total_tokens"] == src.total_tokens
            assert decoded_row["model"] == src.model

    def test_json_cost_uses_model_pricing_table(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=_NOW - timedelta(days=7), until=_NOW, group_by="team",
        )
        decoded = json.loads(report.to_json())
        for row in decoded["rows"]:
            rate = MODEL_PRICING[row["model"]]
            expected = round((row["total_tokens"] / 1_000_000.0) * rate, 6)
            assert row["total_cost_usd"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_db_returns_empty_report(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        with sqlite3.connect(str(db)) as conn:
            _create_project_schema(conn)
        report = ChargebackBuilder(db).build(
            since=_NOW - timedelta(days=30),
            until=_NOW,
        )
        assert report.rows == []
        assert report.to_csv().strip().split("\n")[0] == ",".join(CSV_COLUMNS)
        decoded = json.loads(report.to_json())
        assert decoded["rows"] == []

    def test_view_is_recreated_each_call(self, project_db: Path) -> None:
        """build() should idempotently CREATE the v_chargeback view.

        Calling twice must not raise even though the view already exists.
        """
        b = ChargebackBuilder(project_db)
        r1 = b.build(since=_NOW - timedelta(days=7), until=_NOW)
        r2 = b.build(since=_NOW - timedelta(days=7), until=_NOW)
        assert len(r1.rows) == len(r2.rows)

    def test_since_accepts_iso_date_string(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since="2026-04-01",
            until=_NOW,
            group_by="team",
        )
        # All in-window rows survive (the ancient one is from 2025-03).
        assert any(r.team == "platform" for r in report.rows)
        assert any(r.team == "data" for r in report.rows)

    def test_since_accepts_date_object(self, project_db: Path) -> None:
        report = ChargebackBuilder(project_db).build(
            since=date(2026, 4, 1),
            until=_NOW,
            group_by="team",
        )
        assert len(report.rows) > 0

    def test_valid_group_by_constant_is_complete(self) -> None:
        """Guard: every documented scope is in VALID_GROUP_BY."""
        assert set(VALID_GROUP_BY) == {
            "org", "team", "project", "user", "cost_center"
        }


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------

class TestRowDataclass:
    def test_to_dict_uses_canonical_columns(self) -> None:
        row = ChargebackRow(
            org="acme", team="platform", project="default",
            cost_center="cc-eng", user="alice", model="sonnet",
            total_tokens=1234, total_cost_usd=0.0074,
            step_count=2,
            period_start="2026-01-01T00:00:00Z",
            period_end="2026-02-01T00:00:00Z",
            last_activity="2026-01-30T12:00:00Z",
        )
        d = row.to_dict()
        assert tuple(d.keys()) == CSV_COLUMNS
        assert d["total_tokens"] == 1234
        assert d["total_cost_usd"] == 0.0074
