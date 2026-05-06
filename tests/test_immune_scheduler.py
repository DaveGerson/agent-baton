"""Tests for Wave 6.2 Part B — SweepScheduler (bd-be76).

Covers:
- Queue ordering (oldest first, priority tie-break)
- mark_swept: no-issue defers 30 days, issue defers 7 days
- Persistence round-trip (write + read back)
- seed: INSERT OR IGNORE semantics
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.immune.scheduler import SweepScheduler, SweepTarget


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_target(
    conn: sqlite3.Connection,
    path: str,
    kind: str,
    last_swept_at: str,
    priority: float = 1.0,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO immune_queue (path, kind, last_swept_at, priority)
        VALUES (?, ?, ?, ?)
        """,
        (path, kind, last_swept_at, priority),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSweepSchedulerOrdering:
    def test_next_target_oldest_first(self, tmp_path: Path) -> None:
        """next_target returns the entry with the earliest last_swept_at."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)

        old = "2020-01-01T00:00:00Z"
        new = "2025-01-01T00:00:00Z"
        _insert_target(conn, "/a/old.py", "deprecated-api", old)
        _insert_target(conn, "/a/new.py", "deprecated-api", new)

        target = sched.next_target()
        assert target is not None
        assert str(target.path) == "/a/old.py"

    def test_next_target_priority_tiebreak(self, tmp_path: Path) -> None:
        """When last_swept_at is equal, higher priority wins."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)

        ts = "2024-06-01T00:00:00Z"
        _insert_target(conn, "/a/low.py", "stale-comment", ts, priority=0.5)
        _insert_target(conn, "/a/high.py", "stale-comment", ts, priority=2.0)

        target = sched.next_target()
        assert target is not None
        assert str(target.path) == "/a/high.py"

    def test_next_target_returns_none_when_empty(self, tmp_path: Path) -> None:
        """next_target returns None when the queue is empty."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        assert sched.next_target() is None


class TestMarkSwept:
    def test_no_issue_defers_30_days(self, tmp_path: Path) -> None:
        """mark_swept(found_issue=False) pushes last_swept_at ~30 days forward."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        _insert_target(conn, "/a/file.py", "deprecated-api", "2024-01-01T00:00:00Z")

        target = SweepTarget(
            path=Path("/a/file.py"),
            kind="deprecated-api",
            last_swept_at="2024-01-01T00:00:00Z",
            priority=1.0,
        )
        sched.mark_swept(target, found_issue=False)

        row = conn.execute(
            "SELECT last_swept_at FROM immune_queue WHERE path=? AND kind=?",
            ("/a/file.py", "deprecated-api"),
        ).fetchone()
        assert row is not None
        new_ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = new_ts - now
        # Should be deferred ~30 days (allow ±2 days for clock drift in tests)
        assert 28 <= delta.days <= 32

    def test_issue_defers_7_days(self, tmp_path: Path) -> None:
        """mark_swept(found_issue=True) pushes last_swept_at ~7 days forward."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        _insert_target(conn, "/a/file.py", "stale-comment", "2024-01-01T00:00:00Z")

        target = SweepTarget(
            path=Path("/a/file.py"),
            kind="stale-comment",
            last_swept_at="2024-01-01T00:00:00Z",
            priority=1.0,
        )
        sched.mark_swept(target, found_issue=True)

        row = conn.execute(
            "SELECT last_swept_at, found_issue_at FROM immune_queue WHERE path=? AND kind=?",
            ("/a/file.py", "stale-comment"),
        ).fetchone()
        assert row is not None
        new_ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = new_ts - now
        assert 5 <= delta.days <= 9
        # found_issue_at must be set
        assert row[1] is not None

    def test_mark_swept_no_issue_does_not_overwrite_found_issue_at(
        self, tmp_path: Path
    ) -> None:
        """A subsequent no-issue sweep does not erase a prior found_issue_at."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        _insert_target(conn, "/a/file.py", "doc-drift", "2024-01-01T00:00:00Z")

        target = SweepTarget(
            path=Path("/a/file.py"),
            kind="doc-drift",
            last_swept_at="2024-01-01T00:00:00Z",
            priority=1.0,
        )
        # First sweep finds issue.
        sched.mark_swept(target, found_issue=True)
        row1 = conn.execute(
            "SELECT found_issue_at FROM immune_queue WHERE path=? AND kind=?",
            ("/a/file.py", "doc-drift"),
        ).fetchone()
        first_found_at = row1[0]

        # Reload target from DB so last_swept_at reflects the update.
        target2 = sched.next_target()
        assert target2 is not None
        # Second sweep finds no issue.
        sched.mark_swept(target2, found_issue=False)
        row2 = conn.execute(
            "SELECT found_issue_at FROM immune_queue WHERE path=? AND kind=?",
            ("/a/file.py", "doc-drift"),
        ).fetchone()
        # found_issue_at must not have been erased.
        assert row2[0] == first_found_at


class TestSeed:
    def test_seed_inserts_missing_rows(self, tmp_path: Path) -> None:
        """seed() inserts new (path, kind) rows."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        paths = [Path("/a/one.py"), Path("/a/two.py")]
        sched.seed(paths, kind="deprecated-api")
        assert sched.queue_size() == 2

    def test_seed_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """seed() is idempotent: calling twice doesn't duplicate rows."""
        conn = _conn()
        sched = SweepScheduler(project_root=tmp_path, conn=conn)
        paths = [Path("/a/one.py")]
        sched.seed(paths, kind="deprecated-api")
        # First seed sets last_swept_at to now.
        row1 = conn.execute("SELECT last_swept_at FROM immune_queue").fetchone()
        ts1 = row1[0]
        sched.seed(paths, kind="deprecated-api")
        row2 = conn.execute("SELECT last_swept_at FROM immune_queue").fetchone()
        # Must not be changed by second seed.
        assert row2[0] == ts1
        assert sched.queue_size() == 1


class TestPersistenceRoundTrip:
    def test_roundtrip_queue_survives_reconnect(self, tmp_path: Path) -> None:
        """Data written via one connection is visible via a second connection."""
        db_path = tmp_path / "baton.db"
        conn1 = sqlite3.connect(str(db_path))
        conn1.row_factory = sqlite3.Row
        sched1 = SweepScheduler(project_root=tmp_path, conn=conn1)
        sched1.seed([Path("/a/file.py")], kind="deprecated-api")
        conn1.close()

        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        sched2 = SweepScheduler(project_root=tmp_path, conn=conn2)
        target = sched2.next_target()
        assert target is not None
        assert str(target.path) == "/a/file.py"
        conn2.close()
