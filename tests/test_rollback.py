"""Tests for agent_baton.core.improve.rollback.RollbackManager."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.improve.rollback import RollbackEntry, RollbackManager
from agent_baton.core.improve.vcs import AgentVersionControl
from agent_baton.models.improvement import Recommendation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(
    rec_id: str = "rec-001",
    target: str = "architect",
    category: str = "budget_tier",
) -> Recommendation:
    return Recommendation(
        rec_id=rec_id,
        category=category,
        target=target,
        action="test action",
        description="test",
        confidence=0.9,
        risk="low",
        created_at="2026-03-24T00:00:00+00:00",
    )


def _mgr(tmp_path: Path) -> RollbackManager:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    vcs = AgentVersionControl(agents_dir)
    return RollbackManager(vcs=vcs, improvements_dir=tmp_path / "improvements")


# ---------------------------------------------------------------------------
# RollbackEntry
# ---------------------------------------------------------------------------

class TestRollbackEntry:
    def test_roundtrip(self):
        entry = RollbackEntry(
            rec_id="r1", agent_name="arch", reason="degraded"
        )
        d = entry.to_dict()
        restored = RollbackEntry.from_dict(d)
        assert restored.rec_id == "r1"
        assert restored.agent_name == "arch"
        assert restored.rolled_back_at != ""


# ---------------------------------------------------------------------------
# RollbackManager.rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_logs_entry(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        rec = _rec()
        entry = mgr.rollback(rec, "experiment degraded")
        assert entry.rec_id == "rec-001"
        assert entry.reason == "experiment degraded"

        loaded = mgr.load_all()
        assert len(loaded) == 1
        assert loaded[0].rec_id == "rec-001"

    def test_rollback_restores_agent_prompt(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        agent_file = agents_dir / "architect.md"
        agent_file.write_text("# Original Content\n", encoding="utf-8")

        vcs = AgentVersionControl(agents_dir)
        # Create a backup (simulating pre-modification state)
        vcs.backup_agent(agent_file)

        # Modify the agent
        agent_file.write_text("# Modified Content\n", encoding="utf-8")

        mgr = RollbackManager(vcs=vcs, improvements_dir=tmp_path / "improvements")
        rec = _rec(category="agent_prompt", target="architect")
        mgr.rollback(rec, "degraded")

        # Should have restored the original content
        content = agent_file.read_text(encoding="utf-8")
        assert "Original Content" in content

    def test_multiple_rollbacks_appended(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.rollback(_rec("r1"), "reason 1")
        mgr.rollback(_rec("r2"), "reason 2")
        assert len(mgr.load_all()) == 2


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_not_tripped_with_zero_rollbacks(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.circuit_breaker_tripped() is False

    def test_not_tripped_with_two_rollbacks(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.rollback(_rec("r1"), "reason 1")
        mgr.rollback(_rec("r2"), "reason 2")
        assert mgr.circuit_breaker_tripped() is False

    def test_tripped_with_three_rollbacks(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.rollback(_rec("r1"), "reason 1")
        mgr.rollback(_rec("r2"), "reason 2")
        mgr.rollback(_rec("r3"), "reason 3")
        assert mgr.circuit_breaker_tripped() is True

    def test_old_rollbacks_not_counted(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        # Manually write old entries (>7 days ago)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(
            timespec="seconds"
        )
        for i in range(3):
            entry = RollbackEntry(
                rec_id=f"old-{i}",
                agent_name="arch",
                reason="old",
                rolled_back_at=old_ts,
            )
            mgr._log_rollback(entry)

        assert mgr.circuit_breaker_tripped() is False

    def test_recent_rollbacks(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        mgr.rollback(_rec("r1"), "reason 1")
        recent = mgr.recent_rollbacks(days=7)
        assert len(recent) == 1


# ---------------------------------------------------------------------------
# load_all edge cases
# ---------------------------------------------------------------------------

class TestLoadAll:
    def test_empty_when_no_file(self, tmp_path: Path):
        mgr = _mgr(tmp_path)
        assert mgr.load_all() == []
