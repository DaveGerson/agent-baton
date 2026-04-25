"""Tests for ``agent_baton.core.knowledge.effectiveness`` (K2.2).

Uses an in-memory ``KnowledgeTelemetryStore`` test-double so the suite
neither mutates real telemetry nor depends on the F0.4 store landing first.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from agent_baton.core.knowledge.effectiveness import (
    AttachmentRecord,
    DocEffectiveness,
    StaleDoc,
    compute_effectiveness,
    find_stale_docs,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeStore:
    """Minimal stand-in implementing the ``KnowledgeTelemetryStore`` protocol."""

    def __init__(self, records: Iterable[AttachmentRecord]) -> None:
        self._records = list(records)

    def iter_attachments(self) -> Iterable[AttachmentRecord]:
        return iter(self._records)


def _iso(days_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()


def _make(
    pack: str | None,
    doc: str,
    *,
    outcome: str = "complete",
    status: str | None = None,
    tokens: int = 100,
    days_ago: int = 1,
) -> AttachmentRecord:
    # When ``status`` is not explicitly set, mirror the outcome so a
    # "rejected" outcome does not get treated as a success because the
    # status defaulted to "complete".  Real telemetry typically writes
    # both fields consistently.
    return AttachmentRecord(
        pack_name=pack,
        document_name=doc,
        token_estimate=tokens,
        outcome=outcome,
        status=status if status is not None else outcome,
        completed_at=_iso(days_ago),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_telemetry_returns_empty_result() -> None:
    """No attachments recorded means no rows surfaced."""
    rows = compute_effectiveness(store=FakeStore([]))
    assert rows == []

    stale = find_stale_docs(store=FakeStore([]))
    assert stale == []


def test_seeded_data_computes_correct_success_rate() -> None:
    """Mixed successes / failures roll up into the expected ratio."""
    records = [
        _make("validation", "doc-a", outcome="complete", tokens=200),
        _make("validation", "doc-a", outcome="complete", tokens=200),
        _make("validation", "doc-a", outcome="complete", tokens=200),
        _make("validation", "doc-a", outcome="rejected", tokens=200),
    ]
    rows = compute_effectiveness(store=FakeStore(records))
    assert len(rows) == 1
    eff = rows[0]
    assert isinstance(eff, DocEffectiveness)
    assert eff.attachments == 4
    assert eff.successes == 3
    assert eff.failures == 1
    assert eff.effectiveness_score == 0.75
    assert eff.tokens_consumed == 800


def test_roi_accounts_for_tokens() -> None:
    """A high-token doc with the same successes earns a lower ROI."""
    cheap = [
        _make("p", "cheap", outcome="complete", tokens=100),
        _make("p", "cheap", outcome="complete", tokens=100),
    ]
    expensive = [
        _make("p", "expensive", outcome="complete", tokens=10_000),
        _make("p", "expensive", outcome="complete", tokens=10_000),
    ]
    rows = compute_effectiveness(store=FakeStore(cheap + expensive))
    by_name = {r.document_name: r for r in rows}
    # cheap: 2 successes / 0.2 k-tok = 10 ROI
    # expensive: 2 successes / 20 k-tok = 0.1 ROI
    assert by_name["cheap"].roi_score == pytest.approx(10.0, rel=1e-3)
    assert by_name["expensive"].roi_score == pytest.approx(0.1, rel=1e-3)
    # Sort order: highest ROI first.
    assert rows[0].document_name == "cheap"


def test_pack_filter_restricts_to_one_pack() -> None:
    """``pack=`` only returns matching attachments."""
    records = [
        _make("alpha", "x", outcome="complete"),
        _make("beta", "y", outcome="complete"),
    ]
    rows = compute_effectiveness(pack="alpha", store=FakeStore(records))
    assert {r.pack_name for r in rows} == {"alpha"}
    assert {r.document_name for r in rows} == {"x"}


def test_since_days_filters_old_attachments() -> None:
    """A 7-day window drops a 30-day-old attachment."""
    records = [
        _make("p", "fresh", outcome="complete", days_ago=2),
        _make("p", "old", outcome="complete", days_ago=30),
    ]
    rows = compute_effectiveness(since_days=7, store=FakeStore(records))
    docs = {r.document_name for r in rows}
    assert "fresh" in docs
    assert "old" not in docs


def test_stale_detection_by_age_threshold() -> None:
    """Docs whose last_used predates the threshold are flagged as 'age'."""
    records = [
        _make("p", "ancient", outcome="complete", days_ago=200),
        _make("p", "recent", outcome="complete", days_ago=2),
    ]
    stale = find_stale_docs(threshold_days=90, store=FakeStore(records))
    names = {s.document_name: s for s in stale}
    assert "ancient" in names
    assert "recent" not in names
    assert "age" in names["ancient"].reasons


def test_stale_detection_by_low_effectiveness_with_sufficient_sample() -> None:
    """Low-effectiveness flag fires only with >= min_attachments samples."""
    # 12 attachments, 1 success, 11 failures -> ~8% effectiveness, recent.
    losers = [
        _make("p", "loser", outcome="complete", days_ago=1),
    ] + [
        _make("p", "loser", outcome="rejected", days_ago=1) for _ in range(11)
    ]
    # 5 attachments with the same low rate but below the sample floor —
    # should NOT be flagged as low_effectiveness.
    new_doc = [
        _make("p", "new", outcome="rejected", days_ago=1) for _ in range(5)
    ]
    stale = find_stale_docs(
        threshold_days=0,  # disable age signal so we isolate low_effectiveness
        store=FakeStore(losers + new_doc),
    )
    by_name = {s.document_name: s for s in stale}
    assert "loser" in by_name
    assert "low_effectiveness" in by_name["loser"].reasons
    assert "new" not in by_name


def test_json_format_round_trips() -> None:
    """``DocEffectiveness.to_dict`` serialises losslessly to JSON."""
    records = [
        _make("p", "doc", outcome="complete", tokens=500),
        _make("p", "doc", outcome="rejected", tokens=500),
    ]
    rows = compute_effectiveness(store=FakeStore(records))
    payload = json.dumps([r.to_dict() for r in rows])
    decoded = json.loads(payload)
    assert decoded[0]["pack_name"] == "p"
    assert decoded[0]["document_name"] == "doc"
    assert decoded[0]["attachments"] == 2
    assert decoded[0]["successes"] == 1
    assert decoded[0]["failures"] == 1
    assert decoded[0]["effectiveness_score"] == 0.5
    assert decoded[0]["tokens_consumed"] == 1000


def test_failure_outcomes_are_recognised() -> None:
    """Multiple failure outcome strings count as failures, not successes."""
    records = [
        _make("p", "x", outcome="complete"),
        _make("p", "x", outcome="rejected"),
        _make("p", "x", outcome="blocked"),
        _make("p", "x", outcome="error"),
    ]
    rows = compute_effectiveness(store=FakeStore(records))
    eff = rows[0]
    assert eff.successes == 1
    assert eff.failures == 3


def test_zero_token_cost_does_not_divide_by_zero() -> None:
    """Docs with no recorded tokens still produce a valid ROI score."""
    records = [
        _make("p", "free", outcome="complete", tokens=0),
        _make("p", "free", outcome="rejected", tokens=0),
    ]
    rows = compute_effectiveness(store=FakeStore(records))
    eff = rows[0]
    # successes (1) - 0.3 * failures (1) = 0.7
    assert eff.roi_score == pytest.approx(0.7, rel=1e-3)
    assert eff.tokens_consumed == 0


def test_last_used_picks_most_recent() -> None:
    """``last_used`` is the latest recorded ``completed_at`` per doc."""
    records = [
        _make("p", "doc", outcome="complete", days_ago=20),
        _make("p", "doc", outcome="complete", days_ago=2),
        _make("p", "doc", outcome="complete", days_ago=10),
    ]
    rows = compute_effectiveness(store=FakeStore(records))
    last = rows[0].last_used
    # Latest record was 2 days ago -> ISO string greater than the others.
    assert last == max(r.completed_at for r in records)


def test_stale_doc_to_dict_serialises_reasons() -> None:
    """``StaleDoc`` survives a JSON round-trip with its reasons preserved."""
    sd = StaleDoc(
        pack_name="p",
        document_name="d",
        last_used="2024-01-01T00:00:00+00:00",
        attachments=3,
        effectiveness_score=0.1,
        reasons=["age", "low_effectiveness"],
    )
    payload = json.dumps(sd.to_dict())
    decoded = json.loads(payload)
    assert decoded["reasons"] == ["age", "low_effectiveness"]
    assert decoded["pack_name"] == "p"
