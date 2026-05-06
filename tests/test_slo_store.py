"""SLOStore CRUD + migration tests (O1.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import (
    DEFAULT_SLOS,
    ErrorBudgetBurn,
    SLODefinition,
    SLOMeasurement,
)


@pytest.fixture()
def store(tmp_path: Path) -> SLOStore:
    db = tmp_path / "baton.db"
    return SLOStore(db)


def _measurement(
    *,
    name: str = "dispatch_success_rate",
    sli_value: float = 0.991,
    target: float = 0.99,
    is_meeting: bool = True,
    budget: float = 0.1,
    computed_at: str = "2026-04-29T12:00:00Z",
) -> SLOMeasurement:
    return SLOMeasurement(
        slo_name=name,
        window_start="2026-04-01T00:00:00Z",
        window_end=computed_at,
        sli_value=sli_value,
        target=target,
        is_meeting=is_meeting,
        error_budget_remaining_pct=budget,
        computed_at=computed_at,
        sample_size=100,
    )


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


class TestStoreInitialisation:
    def test_fresh_db_creates_slo_tables(self, tmp_path: Path) -> None:
        store = SLOStore(tmp_path / "fresh.db")
        # Should not raise -- a definition write proves the table exists.
        store.upsert_definition(DEFAULT_SLOS[0])
        assert store.get_definition(DEFAULT_SLOS[0].name) == DEFAULT_SLOS[0]

    def test_migration_applies_to_existing_v15_db(self, tmp_path: Path) -> None:
        """If a v15 db already exists, opening with the new SLOStore must
        upgrade the schema and create the SLO tables."""
        import sqlite3

        db = tmp_path / "old.db"
        # Create a minimal "v15" db with just the version table.
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO _schema_version (version) VALUES (15)")
        conn.commit()
        conn.close()

        store = SLOStore(db)
        store.upsert_definition(DEFAULT_SLOS[0])
        assert store.list_definitions() == [DEFAULT_SLOS[0]]


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


class TestDefinitions:
    def test_upsert_then_get(self, store: SLOStore) -> None:
        store.upsert_definition(DEFAULT_SLOS[0])
        got = store.get_definition(DEFAULT_SLOS[0].name)
        assert got == DEFAULT_SLOS[0]

    def test_upsert_replaces_on_same_name(self, store: SLOStore) -> None:
        store.upsert_definition(DEFAULT_SLOS[0])
        updated = SLODefinition(
            name=DEFAULT_SLOS[0].name,
            sli_query="dispatch_success_rate",
            target=0.995,
            window_days=14,
            description="tightened",
        )
        store.upsert_definition(updated)
        assert store.get_definition(updated.name) == updated

    def test_list_definitions_alphabetical(self, store: SLOStore) -> None:
        for slo in DEFAULT_SLOS:
            store.upsert_definition(slo)
        names = [d.name for d in store.list_definitions()]
        assert names == sorted(names)

    def test_get_definition_missing_returns_none(self, store: SLOStore) -> None:
        assert store.get_definition("nope") is None

    def test_delete_definition(self, store: SLOStore) -> None:
        store.upsert_definition(DEFAULT_SLOS[0])
        store.delete_definition(DEFAULT_SLOS[0].name)
        assert store.get_definition(DEFAULT_SLOS[0].name) is None


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


class TestMeasurements:
    def test_insert_and_list(self, store: SLOStore) -> None:
        store.upsert_definition(DEFAULT_SLOS[0])
        m = _measurement()
        rid = store.insert_measurement(m)
        assert rid > 0
        rows = store.list_measurements()
        assert len(rows) == 1
        assert rows[0].sli_value == pytest.approx(0.991)
        assert rows[0].is_meeting is True

    def test_filter_by_name(self, store: SLOStore) -> None:
        store.insert_measurement(_measurement(name="dispatch_success_rate"))
        store.insert_measurement(_measurement(name="gate_pass_rate"))
        rows = store.list_measurements(slo_name="gate_pass_rate")
        assert [r.slo_name for r in rows] == ["gate_pass_rate"]

    def test_latest_measurement_returns_newest(self, store: SLOStore) -> None:
        store.insert_measurement(_measurement(computed_at="2026-04-28T12:00:00Z"))
        store.insert_measurement(_measurement(computed_at="2026-04-29T12:00:00Z"))
        latest = store.latest_measurement("dispatch_success_rate")
        assert latest is not None
        assert latest.computed_at == "2026-04-29T12:00:00Z"


# ---------------------------------------------------------------------------
# Burns
# ---------------------------------------------------------------------------


class TestBurns:
    def test_insert_assigns_id(self, store: SLOStore) -> None:
        burn = ErrorBudgetBurn(
            slo_name="dispatch_success_rate",
            burn_rate=0.1,
            budget_consumed_pct=0.2,
            started_at="2026-04-29T10:00:00Z",
            ended_at="2026-04-29T12:00:00Z",
            incident_id="inc-1",
        )
        rid = store.insert_burn(burn)
        assert rid > 0
        assert burn.id == rid

    def test_filter_by_name_and_since(self, store: SLOStore) -> None:
        store.insert_burn(
            ErrorBudgetBurn(
                slo_name="dispatch_success_rate",
                burn_rate=0.1,
                budget_consumed_pct=0.2,
                started_at="2026-04-20T10:00:00Z",
            )
        )
        store.insert_burn(
            ErrorBudgetBurn(
                slo_name="dispatch_success_rate",
                burn_rate=0.05,
                budget_consumed_pct=0.1,
                started_at="2026-04-29T10:00:00Z",
            )
        )
        store.insert_burn(
            ErrorBudgetBurn(
                slo_name="gate_pass_rate",
                burn_rate=0.2,
                budget_consumed_pct=0.3,
                started_at="2026-04-29T11:00:00Z",
            )
        )
        recent = store.list_burns(
            slo_name="dispatch_success_rate", since="2026-04-25T00:00:00Z"
        )
        assert len(recent) == 1
        assert recent[0].started_at == "2026-04-29T10:00:00Z"

    def test_close_burn_sets_ended_at(self, store: SLOStore) -> None:
        rid = store.insert_burn(
            ErrorBudgetBurn(
                slo_name="x",
                burn_rate=0.1,
                budget_consumed_pct=0.2,
                started_at="2026-04-29T10:00:00Z",
            )
        )
        store.close_burn(rid, "2026-04-29T12:00:00Z")
        burns = store.list_burns(slo_name="x")
        assert burns[0].ended_at == "2026-04-29T12:00:00Z"
