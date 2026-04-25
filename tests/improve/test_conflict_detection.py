"""Tests for L2.4 (bd-362f) -- recommendation conflict detection.

Covers:
- Direct contradiction -> severity HIGH.
- Same-key disagreement -> severity MEDIUM.
- Adjacent disagreement -> severity LOW.
- Independent recommendations produce no conflicts.
- Window filter excludes recs older than ``window_days``.
- ``ConflictStore.acknowledge`` persists ``acknowledged_at``.
- ``ConflictStore.list`` filters by status.

The detector is stdlib-only; the store hits a real SQLite file inside the
pytest tmp_path so we exercise the v16 migration end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.improve.conflict_detection import (
    DEFAULT_WINDOW_DAYS,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    Conflict,
    ConflictDetector,
)
from agent_baton.core.storage.conflict_store import ConflictStore
from agent_baton.models.improvement import Recommendation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_rec(
    rec_id: str,
    *,
    category: str = "budget_tier",
    target: str = "research-agent",
    action: str = "downgrade budget",
    proposed_change: dict | None = None,
    created_at: datetime | None = None,
) -> Recommendation:
    when = created_at or datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
    return Recommendation(
        rec_id=rec_id,
        category=category,
        target=target,
        action=action,
        description="test",
        proposed_change=proposed_change or {},
        created_at=_iso(when),
    )


# ---------------------------------------------------------------------------
# Detector tests
# ---------------------------------------------------------------------------


def test_direct_contradiction_high_severity() -> None:
    """Two recs that swap from/to on the same target -> HIGH."""
    r1 = _make_rec(
        "rec-1",
        proposed_change={"from": "haiku", "to": "sonnet"},
        action="upgrade budget",
    )
    r2 = _make_rec(
        "rec-2",
        proposed_change={"from": "sonnet", "to": "haiku"},
        action="downgrade budget",
    )

    conflicts = ConflictDetector().detect([r1, r2])

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.severity == SEVERITY_HIGH
    assert sorted(c.rec_ids) == ["rec-1", "rec-2"]
    assert "direct contradiction" in c.reason
    assert c.conflict_id.startswith("cf-")


def test_same_key_disagreement_medium_severity() -> None:
    """Same category+target but different ``to`` values -> MEDIUM."""
    r1 = _make_rec(
        "rec-a",
        proposed_change={"from": "haiku", "to": "sonnet"},
        action="upgrade budget",
    )
    r2 = _make_rec(
        "rec-b",
        proposed_change={"from": "haiku", "to": "opus"},
        action="upgrade budget",
    )

    conflicts = ConflictDetector().detect([r1, r2])

    assert len(conflicts) == 1
    assert conflicts[0].severity == SEVERITY_MEDIUM
    assert "same-key disagreement" in conflicts[0].reason


def test_adjacent_disagreement_low_severity() -> None:
    """Same category, different targets, opposite direction verbs -> LOW."""
    r1 = _make_rec(
        "rec-x",
        target="research-agent",
        action="downgrade budget tier",
        proposed_change={"to": "haiku"},
    )
    r2 = _make_rec(
        "rec-y",
        target="auditor",
        action="upgrade budget tier",
        proposed_change={"to": "opus"},
    )

    conflicts = ConflictDetector().detect([r1, r2])

    assert len(conflicts) == 1
    assert conflicts[0].severity == SEVERITY_LOW
    assert "adjacent disagreement" in conflicts[0].reason


def test_independent_recs_yield_no_conflicts() -> None:
    """Recs with disjoint categories AND targets do not conflict."""
    r1 = _make_rec(
        "rec-i1",
        category="budget_tier",
        target="research-agent",
        action="downgrade budget",
        proposed_change={"from": "sonnet", "to": "haiku"},
    )
    r2 = _make_rec(
        "rec-i2",
        category="routing",
        target="planner",
        action="adjust routing",
        proposed_change={"from": "fast-lane", "to": "deep-lane"},
    )

    conflicts = ConflictDetector().detect([r1, r2])

    assert conflicts == []


def test_window_filter_excludes_far_apart_recs() -> None:
    """Recs separated by more than ``window_days`` are not paired."""
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    r1 = _make_rec(
        "rec-old",
        proposed_change={"from": "haiku", "to": "sonnet"},
        action="upgrade budget",
        created_at=base,
    )
    # 30 days later -- well outside the default 7-day window.
    r2 = _make_rec(
        "rec-new",
        proposed_change={"from": "sonnet", "to": "haiku"},
        action="downgrade budget",
        created_at=base + timedelta(days=30),
    )

    detector = ConflictDetector(window_days=DEFAULT_WINDOW_DAYS)
    assert detector.detect([r1, r2]) == []

    # And confirm a wider window picks up the same pair (sanity check).
    wide = ConflictDetector(window_days=60)
    assert len(wide.detect([r1, r2])) == 1


def test_filter_out_conflicting_excludes_flagged_recs() -> None:
    """``filter_out_conflicting`` drops every rec referenced by a conflict."""
    r1 = _make_rec(
        "rec-1",
        proposed_change={"from": "haiku", "to": "sonnet"},
        action="upgrade budget",
    )
    r2 = _make_rec(
        "rec-2",
        proposed_change={"from": "sonnet", "to": "haiku"},
        action="downgrade budget",
    )
    r3 = _make_rec(
        "rec-3",
        category="routing",
        target="planner",
        action="adjust routing",
        proposed_change={"to": "deep-lane"},
    )
    conflicts = ConflictDetector().detect([r1, r2, r3])
    survivors = ConflictDetector.filter_out_conflicting([r1, r2, r3], conflicts)
    assert [r.rec_id for r in survivors] == ["rec-3"]


# ---------------------------------------------------------------------------
# Store tests (real SQLite under tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> ConflictStore:
    db_path = tmp_path / "baton.db"
    return ConflictStore(db_path)


def test_acknowledge_updates_sql_row(store: ConflictStore) -> None:
    """``acknowledge`` flips ``acknowledged_at`` and persists across reads."""
    c = Conflict(
        rec_ids=["rec-1", "rec-2"],
        reason="direct contradiction on 'agent-x'",
        severity=SEVERITY_HIGH,
    )
    cid = store.record(c)
    assert cid == c.conflict_id

    fetched = store.get(cid)
    assert fetched is not None
    assert fetched.acknowledged_at == ""

    assert store.acknowledge(cid, when="2026-04-25T12:00:00Z") is True

    after = store.get(cid)
    assert after is not None
    assert after.acknowledged_at == "2026-04-25T12:00:00Z"
    assert after.severity == SEVERITY_HIGH
    assert sorted(after.rec_ids) == ["rec-1", "rec-2"]

    # Acknowledging an unknown id is a no-op (False, not an exception).
    assert store.acknowledge("cf-does-not-exist") is False


def test_list_filter_by_status(store: ConflictStore) -> None:
    """``list(status=...)`` partitions active vs resolved conflicts."""
    base_ts = "2026-04-25T11:"
    c_active_1 = Conflict(
        conflict_id="cf-active-01",
        rec_ids=["a", "b"],
        reason="med",
        severity=SEVERITY_MEDIUM,
        detected_at=base_ts + "00:00Z",
    )
    c_active_2 = Conflict(
        conflict_id="cf-active-02",
        rec_ids=["c", "d"],
        reason="low",
        severity=SEVERITY_LOW,
        detected_at=base_ts + "10:00Z",
    )
    c_resolved = Conflict(
        conflict_id="cf-resolved-01",
        rec_ids=["e", "f"],
        reason="high",
        severity=SEVERITY_HIGH,
        detected_at=base_ts + "20:00Z",
    )
    store.record_many([c_active_1, c_active_2, c_resolved])
    assert store.acknowledge("cf-resolved-01", when="2026-04-25T13:00:00Z") is True

    active = store.list(status="active")
    resolved = store.list(status="resolved")
    everything = store.list(status="all")

    assert {c.conflict_id for c in active} == {"cf-active-01", "cf-active-02"}
    assert {c.conflict_id for c in resolved} == {"cf-resolved-01"}
    assert {c.conflict_id for c in everything} == {
        "cf-active-01",
        "cf-active-02",
        "cf-resolved-01",
    }
