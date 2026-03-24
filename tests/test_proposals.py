"""Tests for agent_baton.core.improve.proposals.ProposalManager."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.improve.proposals import ProposalManager
from agent_baton.models.improvement import Recommendation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(rec_id: str = "rec-001", status: str = "proposed") -> Recommendation:
    return Recommendation(
        rec_id=rec_id,
        category="budget_tier",
        target="phased_delivery",
        action="downgrade budget",
        description="test recommendation",
        confidence=0.9,
        risk="low",
        auto_applicable=True,
        created_at="2026-03-24T00:00:00+00:00",
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProposalManager:
    def test_record_and_load(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        rec = _rec()
        mgr.record(rec)
        loaded = mgr.load_all()
        assert len(loaded) == 1
        assert loaded[0].rec_id == "rec-001"

    def test_record_many(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        recs = [_rec(f"rec-{i}") for i in range(3)]
        mgr.record_many(recs)
        assert len(mgr.load_all()) == 3

    def test_record_many_empty(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record_many([])
        assert mgr.load_all() == []

    def test_get_by_id(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001"))
        mgr.record(_rec("rec-002"))
        result = mgr.get("rec-002")
        assert result is not None
        assert result.rec_id == "rec-002"

    def test_get_missing_returns_none(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        assert mgr.get("nonexistent") is None

    def test_update_status(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001"))
        assert mgr.update_status("rec-001", "applied") is True
        loaded = mgr.get("rec-001")
        assert loaded is not None
        assert loaded.status == "applied"

    def test_update_status_missing_returns_false(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        assert mgr.update_status("nonexistent", "applied") is False

    def test_get_by_status(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001", status="proposed"))
        mgr.record(_rec("rec-002", status="applied"))
        mgr.record(_rec("rec-003", status="proposed"))
        proposed = mgr.get_by_status("proposed")
        assert len(proposed) == 2

    def test_get_applied(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001", status="applied"))
        mgr.record(_rec("rec-002", status="proposed"))
        assert len(mgr.get_applied()) == 1

    def test_get_proposed(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001", status="proposed"))
        assert len(mgr.get_proposed()) == 1

    def test_load_empty_when_no_file(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        assert mgr.load_all() == []

    def test_append_preserves_existing(self, tmp_path: Path):
        mgr = ProposalManager(tmp_path / "improvements")
        mgr.record(_rec("rec-001"))
        mgr.record(_rec("rec-002"))
        all_recs = mgr.load_all()
        assert len(all_recs) == 2
        assert all_recs[0].rec_id == "rec-001"
        assert all_recs[1].rec_id == "rec-002"
