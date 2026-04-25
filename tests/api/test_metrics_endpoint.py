"""Tests for the Prometheus ``GET /metrics`` endpoint (O1.4).

Covers:

- The endpoint returns a 200 with the Prometheus 0.0.4 content type.
- The response declares the expected ``baton_*`` metric families.
- Every emitted metric line parses against the basic Prometheus
  exposition grammar.
- Counter samples reflect the underlying SQLite state when rows are
  added.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.storage.schema import (  # noqa: E402
    PROJECT_SCHEMA_DDL,
    SCHEMA_VERSION,
)


# Prometheus 0.0.4 exposition format — one metric line per row.
# Matches: name{labels?} value
_LINE_RE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*"          # metric name
    r"(\{[^}]*\})?"                        # optional label set
    r"\s"                                  # exactly one space
    r"-?\d+(\.\d+)?([eE][-+]?\d+)?"        # numeric value
    r"$"
)

_EXPECTED_METRICS = {
    "baton_plans_total",
    "baton_steps_total",
    "baton_tokens_total",
    "baton_active_executions",
    "baton_open_beads",
    "baton_chain_length",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    """Create a baton.db with the canonical project schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(PROJECT_SCHEMA_DDL)
    if conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO _schema_version VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    conn.close()


def _seed_execution(db_path: Path, task_id: str, status: str = "running") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO executions "
        "(task_id, status, current_phase, current_step_index, started_at, "
        "created_at, updated_at) "
        "VALUES (?, ?, 0, 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (task_id, status),
    )
    conn.execute(
        "INSERT OR REPLACE INTO plans "
        "(task_id, task_summary, risk_level, budget_tier, execution_mode, "
        " git_strategy, shared_context, plan_markdown, created_at) "
        "VALUES (?, 'demo', 'LOW', 'standard', 'phased', "
        "'commit-per-agent', '', '', '2026-01-01T00:00:00Z')",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _seed_step_result(
    db_path: Path,
    task_id: str,
    step_id: str,
    agent: str,
    model: str,
    outcome: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO step_results "
        "(task_id, step_id, agent_name, status, outcome, completed_at, "
        " model_id, input_tokens, output_tokens) "
        "VALUES (?, ?, ?, ?, '', '2026-01-01T01:00:00Z', ?, ?, ?)",
        (task_id, step_id, agent, outcome, model, input_tokens, output_tokens),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests — basic shape
# ---------------------------------------------------------------------------


class TestMetricsEndpointBasics:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/metrics")
        assert r.status_code == 200

    def test_content_type_is_prometheus(self, client: TestClient) -> None:
        r = client.get("/metrics")
        # Starlette appends charset; we just need the prefix.
        assert r.headers["content-type"].startswith(
            "text/plain; version=0.0.4"
        )

    def test_emits_at_least_one_baton_metric_line(
        self, client: TestClient
    ) -> None:
        body = client.get("/metrics").text
        baton_lines = [
            ln for ln in body.splitlines()
            if ln and not ln.startswith("#") and ln.startswith("baton_")
        ]
        # On a fresh DB the active gauge always emits "baton_active_executions 0"
        # so we should always have at least one metric line.
        assert baton_lines, body

    def test_all_expected_families_are_declared(
        self, client: TestClient
    ) -> None:
        body = client.get("/metrics").text
        declared = {
            ln.split()[2] for ln in body.splitlines() if ln.startswith("# TYPE")
        }
        missing = _EXPECTED_METRICS - declared
        assert not missing, f"missing metric families: {missing}"

    def test_every_metric_line_parses(self, client: TestClient) -> None:
        body = client.get("/metrics").text
        bad: list[str] = []
        for line in body.splitlines():
            if not line or line.startswith("#"):
                continue
            if not _LINE_RE.match(line):
                bad.append(line)
        assert not bad, f"unparseable lines: {bad}"


# ---------------------------------------------------------------------------
# Tests — counters reflect DB state
# ---------------------------------------------------------------------------


class TestMetricsReflectDbState:
    def test_active_executions_increments_with_running_rows(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "baton.db"
        _init_db(db_path)

        app = create_app(team_context_root=tmp_path)
        client = TestClient(app)

        # Baseline — zero running tasks.
        first = client.get("/metrics").text
        assert "baton_active_executions 0" in first

        _seed_execution(db_path, "task-A", status="running")
        _seed_execution(db_path, "task-B", status="running")
        _seed_execution(db_path, "task-C", status="complete")

        second = client.get("/metrics").text
        assert "baton_active_executions 2" in second

    def test_steps_total_emits_per_label_combo(self, tmp_path: Path) -> None:
        db_path = tmp_path / "baton.db"
        _init_db(db_path)
        _seed_execution(db_path, "task-X", status="running")
        _seed_step_result(
            db_path,
            task_id="task-X",
            step_id="s1",
            agent="developer",
            model="claude-sonnet-4-6",
            outcome="complete",
            input_tokens=100,
            output_tokens=50,
        )

        app = create_app(team_context_root=tmp_path)
        client = TestClient(app)
        body = client.get("/metrics").text

        # The labelled line should appear exactly once with value 1.
        matches = [ln for ln in body.splitlines() if ln.startswith("baton_steps_total{")]
        assert any(
            'agent="developer"' in ln
            and 'model="claude-sonnet-4-6"' in ln
            and 'outcome="complete"' in ln
            and ln.rstrip().endswith(" 1")
            for ln in matches
        ), matches

    def test_tokens_total_sums_input_plus_output(self, tmp_path: Path) -> None:
        db_path = tmp_path / "baton.db"
        _init_db(db_path)
        _seed_execution(db_path, "task-T", status="running")
        _seed_step_result(
            db_path,
            task_id="task-T",
            step_id="s1",
            agent="developer",
            model="claude-sonnet-4-6",
            outcome="complete",
            input_tokens=100,
            output_tokens=25,
        )

        app = create_app(team_context_root=tmp_path)
        client = TestClient(app)
        body = client.get("/metrics").text

        token_lines = [
            ln for ln in body.splitlines()
            if ln.startswith("baton_tokens_total{")
        ]
        assert any(
            'model="claude-sonnet-4-6"' in ln and ln.rstrip().endswith(" 125")
            for ln in token_lines
        ), token_lines


# ---------------------------------------------------------------------------
# Tests — fresh / empty installs
# ---------------------------------------------------------------------------


class TestMetricsFreshInstall:
    def test_endpoint_works_when_db_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        # No baton.db file at all.
        app = create_app(team_context_root=tmp_path)
        client = TestClient(app)
        r = client.get("/metrics")

        assert r.status_code == 200
        # All declared families should still be present even with no DB.
        declared = {
            ln.split()[2] for ln in r.text.splitlines() if ln.startswith("# TYPE")
        }
        assert _EXPECTED_METRICS.issubset(declared)
