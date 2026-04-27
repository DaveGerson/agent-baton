"""SLOComputer tests (O1.5).

Exercises raw SLI math, the budget formula, full measurement
construction, and burn detection -- all against a real SQLite db
populated with synthetic step_results / gate_results / executions rows.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.observe.slo_computer import SLOComputer
from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import DEFAULT_SLOS, SLODefinition, SLOMeasurement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    # Touching SLOStore creates the schema (incl. step_results / gate_results /
    # executions via PROJECT_SCHEMA_DDL).  Schema is applied lazily on the
    # first connection acquisition, so we force one with a list call before
    # tearing down.
    p = tmp_path / "baton.db"
    s = SLOStore(p)
    s.list_definitions()  # forces _ensure_schema()
    s.close()
    return p


def _insert_execution(db: Path, task_id: str, *, status: str, started_at: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO executions (task_id, status, started_at) VALUES (?, ?, ?)",
        (task_id, status, started_at),
    )
    conn.commit()
    conn.close()


def _insert_step_result(
    db: Path,
    task_id: str,
    step_id: str,
    *,
    status: str,
    completed_at: str,
) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO step_results
            (task_id, step_id, agent_name, status, completed_at)
        VALUES (?, ?, 'a', ?, ?)
        """,
        (task_id, step_id, status, completed_at),
    )
    conn.commit()
    conn.close()


def _insert_gate(db: Path, task_id: str, *, passed: int, checked_at: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO gate_results
            (task_id, phase_id, gate_type, passed, checked_at)
        VALUES (?, 1, 'tests', ?, ?)
        """,
        (task_id, passed, checked_at),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Budget formula
# ---------------------------------------------------------------------------


class TestBudgetFormula:
    @pytest.mark.parametrize(
        "target,sli,expected",
        [
            (0.99, 1.00, 1.0),  # perfect SLI -> full budget
            (0.99, 0.99, 0.0),  # exactly meeting -> zero remaining
            (0.99, 0.98, 0.0),  # below target -> clipped to 0
            (0.99, 0.995, pytest.approx(0.5, rel=1e-6)),  # half budget left
            (0.95, 0.975, pytest.approx(0.5, rel=1e-6)),
        ],
    )
    def test_remaining_formula(self, target: float, sli: float, expected) -> None:
        assert SLOComputer.compute_error_budget_remaining(target, sli) == expected

    def test_unattainable_target_edge_case(self) -> None:
        # target == 1.0 means "no failures allowed".
        assert SLOComputer.compute_error_budget_remaining(1.0, 1.0) == 1.0
        assert SLOComputer.compute_error_budget_remaining(1.0, 0.9999) == 0.0


# ---------------------------------------------------------------------------
# Raw SLI computations
# ---------------------------------------------------------------------------


class TestSLIComputations:
    def test_dispatch_success_rate_no_data_returns_one(self, db_path: Path) -> None:
        c = SLOComputer(db_path)
        result = c.compute_dispatch_success_rate(window_days=7)
        assert result.value == 1.0
        assert result.sample_size == 0

    def test_dispatch_success_rate_basic(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        for i in range(8):
            _insert_step_result(
                db_path, "t1", f"s{i}", status="complete", completed_at=recent
            )
        for i in range(2):
            _insert_step_result(
                db_path, "t1", f"f{i}", status="failed", completed_at=recent
            )
        c = SLOComputer(db_path)
        result = c.compute_dispatch_success_rate(window_days=7)
        assert result.sample_size == 10
        assert result.value == pytest.approx(0.8)

    def test_dispatch_success_rate_skipped_excluded(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        _insert_step_result(db_path, "t1", "s0", status="complete", completed_at=recent)
        _insert_step_result(db_path, "t1", "s1", status="skipped", completed_at=recent)
        c = SLOComputer(db_path)
        result = c.compute_dispatch_success_rate(window_days=7)
        # skipped is non-terminal -> excluded; only the complete one remains.
        assert result.sample_size == 1
        assert result.value == pytest.approx(1.0)

    def test_window_filter_excludes_old(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        old = _iso(now - timedelta(days=30))
        _insert_step_result(db_path, "t1", "s0", status="failed", completed_at=old)
        _insert_step_result(db_path, "t1", "s1", status="complete", completed_at=recent)
        c = SLOComputer(db_path)
        result = c.compute_dispatch_success_rate(window_days=7)
        assert result.sample_size == 1
        assert result.value == 1.0

    def test_gate_pass_rate(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        for _ in range(9):
            _insert_gate(db_path, "t1", passed=1, checked_at=recent)
        _insert_gate(db_path, "t1", passed=0, checked_at=recent)
        c = SLOComputer(db_path)
        result = c.compute_gate_pass_rate(window_days=7)
        assert result.value == pytest.approx(0.9)

    def test_engine_uptime(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        _insert_execution(db_path, "t1", status="complete", started_at=recent)
        _insert_execution(db_path, "t2", status="running", started_at=recent)
        _insert_execution(db_path, "t3", status="failed", started_at=recent)
        c = SLOComputer(db_path)
        result = c.compute_engine_uptime(window_days=7)
        assert result.sample_size == 3
        assert result.value == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# compute_measurement
# ---------------------------------------------------------------------------


class TestComputeMeasurement:
    def test_measurement_for_dispatch(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        for _ in range(99):
            _insert_step_result(
                db_path, "t1", f"ok{_}", status="complete", completed_at=recent
            )
        _insert_step_result(db_path, "t1", "bad", status="failed", completed_at=recent)
        c = SLOComputer(db_path)
        m = c.compute_measurement(DEFAULT_SLOS[0])  # dispatch_success_rate@0.99
        assert m.slo_name == "dispatch_success_rate"
        assert m.sli_value == pytest.approx(0.99)
        assert m.is_meeting is True
        # Exactly at target -> zero budget remaining.
        assert m.error_budget_remaining_pct == pytest.approx(0.0)
        assert m.sample_size == 100

    def test_measurement_breach(self, db_path: Path) -> None:
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        for _ in range(8):
            _insert_step_result(
                db_path, "t1", f"ok{_}", status="complete", completed_at=recent
            )
        for _ in range(2):
            _insert_step_result(
                db_path, "t1", f"bad{_}", status="failed", completed_at=recent
            )
        c = SLOComputer(db_path)
        m = c.compute_measurement(DEFAULT_SLOS[0])
        assert m.is_meeting is False
        assert m.error_budget_remaining_pct == 0.0

    def test_unknown_sli_query_raises(self, db_path: Path) -> None:
        c = SLOComputer(db_path)
        bogus = SLODefinition(
            name="bogus", sli_query="not_a_real_sli", target=0.99, window_days=7
        )
        with pytest.raises(ValueError, match="Unknown sli_query"):
            c.compute_measurement(bogus)


# ---------------------------------------------------------------------------
# Burn detection
# ---------------------------------------------------------------------------


class TestBurnDetection:
    def test_first_measurement_no_burn(self, db_path: Path) -> None:
        c = SLOComputer(db_path)
        c.store.upsert_definition(DEFAULT_SLOS[0])
        m, burn = c.measure_and_persist(DEFAULT_SLOS[0])
        assert burn is None
        assert m is not None

    def test_burn_recorded_when_budget_drops_quickly(self, db_path: Path) -> None:
        """Manually seed a "previous" measurement with full budget, then
        compute a new one with depleted budget -- the formula should detect
        a burn above the threshold and persist it."""
        c = SLOComputer(db_path, burn_threshold_per_hour=0.01)
        c.store.upsert_definition(DEFAULT_SLOS[0])

        prev_time = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        c.store.insert_measurement(
            SLOMeasurement(
                slo_name=DEFAULT_SLOS[0].name,
                window_start=prev_time,
                window_end=prev_time,
                sli_value=1.0,
                target=DEFAULT_SLOS[0].target,
                is_meeting=True,
                error_budget_remaining_pct=1.0,
                computed_at=prev_time,
                sample_size=100,
            )
        )

        # Now seed step_results to force a low SLI.
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(minutes=5))
        for i in range(8):
            _insert_step_result(
                db_path, "t", f"ok{i}", status="complete", completed_at=recent
            )
        for i in range(2):
            _insert_step_result(
                db_path, "t", f"bad{i}", status="failed", completed_at=recent
            )

        m, burn = c.measure_and_persist(DEFAULT_SLOS[0])
        assert burn is not None
        assert burn.budget_consumed_pct == pytest.approx(1.0, abs=1e-3)
        # burn_rate = 1.0 / ~1h ~ 1.0 / hour, well above the 0.01 threshold
        assert burn.burn_rate > 0.01

    def test_no_burn_when_budget_recovers(self, db_path: Path) -> None:
        c = SLOComputer(db_path)
        c.store.upsert_definition(DEFAULT_SLOS[0])
        prev_time = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        c.store.insert_measurement(
            SLOMeasurement(
                slo_name=DEFAULT_SLOS[0].name,
                window_start=prev_time,
                window_end=prev_time,
                sli_value=0.95,
                target=DEFAULT_SLOS[0].target,
                is_meeting=False,
                error_budget_remaining_pct=0.0,
                computed_at=prev_time,
                sample_size=100,
            )
        )
        # Force perfect SLI now -> budget goes UP, not down.
        m, burn = c.measure_and_persist(DEFAULT_SLOS[0])
        assert burn is None
