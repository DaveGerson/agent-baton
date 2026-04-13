"""Tests for agent_baton.core.engine.bead_store.BeadStore.

Coverage:
- write: persist bead and tags in a single transaction
- read: fetch by bead_id, return None for missing bead
- query: no filters, filter by task_id / agent_name / bead_type / status
- query: tag filter uses bead_tags table with AND semantics
- ready: returns only open beads with no blocking dependencies
- ready: bead with all blocked_by dependencies closed is included
- ready: bead with an open blocked_by dependency is excluded
- close: transitions status, sets closed_at, populates summary; idempotent
- link: appends typed BeadLink to source bead's links column
- link: silently ignores missing source bead
- decay: archives closed beads older than max_age_days
- decay: leaves open or recently-closed beads untouched
- decay: returns count of archived beads
- graceful degradation: all methods return safe values when table absent
- schema migration: v3 DB gains beads/bead_tags tables after BeadStore init
- sync integration: beads rows sync to central.db via SyncEngine
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.models.bead import Bead, BeadLink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_timestamp(days: int) -> str:
    """Return an ISO 8601 UTC timestamp *days* in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_execution(db_path: Path, task_id: str) -> None:
    """Insert a minimal executions row so FK constraints on beads pass.

    The beads table has ``FOREIGN KEY (task_id) REFERENCES executions(task_id)``
    so every write() call will fail unless a matching execution row exists.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, started_at, "
            " created_at, updated_at) "
            "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


_DEFAULT_TASK_ID = "task-001"


def _make_bead(
    bead_id: str = "bd-a1b2",
    task_id: str = _DEFAULT_TASK_ID,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer--python",
    bead_type: str = "discovery",
    content: str = "The auth module uses JWT with RS256.",
    tags: list[str] | None = None,
    status: str = "open",
    created_at: str = "",
    **kwargs,
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id=step_id,
        agent_name=agent_name,
        bead_type=bead_type,
        content=content,
        tags=tags or [],
        status=status,
        created_at=created_at or _utcnow(),
        **kwargs,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Absolute path to a temporary baton.db with schema initialised."""
    return tmp_path / "baton.db"


@pytest.fixture
def store(db_path: Path) -> BeadStore:
    """A fresh BeadStore backed by a temporary SQLite database.

    Seeds a default execution row (task_id='task-001') so that
    bead writes pass the beads.task_id FK constraint.
    """
    s = BeadStore(db_path)
    # Force schema application by calling _table_exists(), which calls _conn()
    # triggering ConnectionManager._ensure_schema() and committing DDL to disk.
    # Without this, _seed_execution() opens a separate connection that sees an
    # empty file because the schema is applied lazily on first get_connection().
    s._table_exists()
    _seed_execution(db_path, _DEFAULT_TASK_ID)
    return s


@pytest.fixture
def populated_store(db_path: Path) -> tuple[BeadStore, list[Bead]]:
    """A BeadStore pre-populated with three beads for query/filter tests.

    Uses three beads across two task IDs to support filter tests.
    Both task IDs are seeded into executions first.
    """
    # Seed both task IDs required by the beads below.
    s = BeadStore(db_path)
    s._table_exists()  # force schema to disk before seeding
    _seed_execution(db_path, "task-A")
    _seed_execution(db_path, "task-B")

    beads = [
        _make_bead("bd-0001", task_id="task-A", bead_type="discovery",
                   agent_name="backend-engineer--python", tags=["auth", "jwt"]),
        _make_bead("bd-0002", task_id="task-A", bead_type="warning",
                   agent_name="test-engineer", tags=["auth"]),
        _make_bead("bd-0003", task_id="task-B", bead_type="decision",
                   agent_name="backend-engineer--python", tags=["db"]),
    ]
    for b in beads:
        s.write(b)
    return s, beads


# ---------------------------------------------------------------------------
# BeadStore.write / read — basic CRUD
# ---------------------------------------------------------------------------


class TestWriteAndRead:
    def test_write_returns_bead_id(self, store: BeadStore) -> None:
        bead = _make_bead()
        result = store.write(bead)
        assert result == bead.bead_id

    def test_read_returns_written_bead(self, store: BeadStore) -> None:
        bead = _make_bead(content="JWT RS256 discovery")
        store.write(bead)
        fetched = store.read(bead.bead_id)
        assert fetched is not None
        assert fetched.bead_id == bead.bead_id
        assert fetched.content == "JWT RS256 discovery"

    def test_read_missing_bead_returns_none(self, store: BeadStore) -> None:
        result = store.read("bd-doesnotexist")
        assert result is None

    def test_write_persists_all_scalar_fields(self, store: BeadStore) -> None:
        # Uses the default task_id which already has a seeded execution row.
        bead = _make_bead(
            bead_id="bd-full",
            step_id="2.3",
            agent_name="test-engineer",
            bead_type="warning",
            content="Port 5433 may conflict",
            confidence="high",
            scope="phase",
            status="open",
            source="agent-signal",
            token_estimate=99,
        )
        store.write(bead)
        fetched = store.read("bd-full")
        assert fetched is not None
        assert fetched.task_id == _DEFAULT_TASK_ID
        assert fetched.step_id == "2.3"
        assert fetched.agent_name == "test-engineer"
        assert fetched.bead_type == "warning"
        assert fetched.confidence == "high"
        assert fetched.scope == "phase"
        assert fetched.source == "agent-signal"
        assert fetched.token_estimate == 99

    def test_write_persists_tags(self, store: BeadStore) -> None:
        bead = _make_bead(tags=["auth", "jwt", "security"])
        store.write(bead)
        fetched = store.read(bead.bead_id)
        assert fetched is not None
        assert sorted(fetched.tags) == ["auth", "jwt", "security"]

    def test_write_persists_affected_files(self, store: BeadStore) -> None:
        bead = _make_bead(affected_files=["auth.py", "tests/test_auth.py"])
        store.write(bead)
        fetched = store.read(bead.bead_id)
        assert fetched is not None
        assert sorted(fetched.affected_files) == ["auth.py", "tests/test_auth.py"]

    def test_write_persists_links(self, store: BeadStore) -> None:
        link = BeadLink(target_bead_id="bd-zz00", link_type="relates_to",
                        created_at="2026-01-01T00:00:00Z")
        bead = _make_bead(links=[link])
        store.write(bead)
        fetched = store.read(bead.bead_id)
        assert fetched is not None
        assert len(fetched.links) == 1
        assert fetched.links[0].target_bead_id == "bd-zz00"
        assert fetched.links[0].link_type == "relates_to"

    def test_write_inserts_tags_into_bead_tags_table(
        self, store: BeadStore, db_path: Path
    ) -> None:
        """Tags must also appear in the normalised bead_tags table."""
        bead = _make_bead(bead_id="bd-tags-check", tags=["alpha", "beta"])
        store.write(bead)
        # Inspect bead_tags directly via raw sqlite
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT tag FROM bead_tags WHERE bead_id = ? ORDER BY tag",
            ("bd-tags-check",),
        ).fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["alpha", "beta"]

    def test_write_replaces_stale_tags_on_update(
        self, store: BeadStore, db_path: Path
    ) -> None:
        """Second write with different tags must remove stale tags from bead_tags."""
        bead = _make_bead(bead_id="bd-stale-tags", tags=["old-tag"])
        store.write(bead)

        updated = _make_bead(bead_id="bd-stale-tags", tags=["new-tag"])
        store.write(updated)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT tag FROM bead_tags WHERE bead_id = ?",
            ("bd-stale-tags",),
        ).fetchall()
        conn.close()
        tags = [r[0] for r in rows]
        assert "old-tag" not in tags
        assert "new-tag" in tags

    def test_write_replace_overwrites_existing_bead(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-replace", content="original content")
        store.write(bead)

        updated = _make_bead(bead_id="bd-replace", content="updated content")
        store.write(updated)

        fetched = store.read("bd-replace")
        assert fetched is not None
        assert fetched.content == "updated content"

    def test_write_empty_tags_list(self, store: BeadStore) -> None:
        bead = _make_bead(tags=[])
        store.write(bead)
        fetched = store.read(bead.bead_id)
        assert fetched is not None
        assert fetched.tags == []


# ---------------------------------------------------------------------------
# BeadStore.query — filtered search
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query_no_filters_returns_all_beads(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, beads = populated_store
        results = store.query()
        assert len(results) == 3

    def test_query_by_task_id(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(task_id="task-A")
        assert len(results) == 2
        assert all(b.task_id == "task-A" for b in results)

    def test_query_by_bead_type(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(bead_type="warning")
        assert len(results) == 1
        assert results[0].bead_id == "bd-0002"

    def test_query_by_agent_name(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(agent_name="backend-engineer--python")
        assert len(results) == 2

    def test_query_by_status(self, store: BeadStore) -> None:
        open_bead = _make_bead(bead_id="bd-open", status="open")
        store.write(open_bead)
        store.close("bd-open", "done")
        results = store.query(status="closed")
        assert len(results) == 1
        assert results[0].bead_id == "bd-open"

    def test_query_returns_newest_first(self, store: BeadStore) -> None:
        """Results ordered by created_at DESC — use explicit timestamps to avoid
        sub-second collision when _utcnow() has 1-second resolution."""
        b1 = _make_bead(bead_id="bd-older", content="older",
                        created_at="2026-01-01T00:00:00Z")
        b2 = _make_bead(bead_id="bd-newer", content="newer",
                        created_at="2026-01-02T00:00:00Z")
        store.write(b1)
        store.write(b2)
        results = store.query()
        assert results[0].bead_id == "bd-newer"

    def test_query_limit_respected(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(limit=2)
        assert len(results) == 2

    def test_query_empty_result_returns_empty_list(self, store: BeadStore) -> None:
        results = store.query(task_id="nonexistent-task")
        assert results == []

    def test_query_by_single_tag(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(tags=["auth"])
        # bd-0001 has ["auth", "jwt"], bd-0002 has ["auth"] — both match
        assert len(results) == 2
        ids = {b.bead_id for b in results}
        assert "bd-0001" in ids
        assert "bd-0002" in ids

    def test_query_by_multiple_tags_uses_and_semantics(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        # Only bd-0001 has BOTH "auth" AND "jwt"
        results = store.query(tags=["auth", "jwt"])
        assert len(results) == 1
        assert results[0].bead_id == "bd-0001"

    def test_query_tag_filter_no_matches_returns_empty_list(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(tags=["nonexistent-tag"])
        assert results == []

    def test_query_combined_filters(
        self, populated_store: tuple[BeadStore, list[Bead]]
    ) -> None:
        store, _ = populated_store
        results = store.query(task_id="task-A", bead_type="discovery")
        assert len(results) == 1
        assert results[0].bead_id == "bd-0001"


# ---------------------------------------------------------------------------
# BeadStore.ready — unblocked open beads
# ---------------------------------------------------------------------------


class TestReady:
    def test_ready_returns_open_beads_with_no_links(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-r001", status="open")
        store.write(bead)
        results = store.ready(_DEFAULT_TASK_ID)
        assert len(results) == 1
        assert results[0].bead_id == "bd-r001"

    def test_ready_excludes_closed_beads(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-r002", status="open")
        store.write(bead)
        store.close("bd-r002", "summary")
        results = store.ready(_DEFAULT_TASK_ID)
        assert results == []

    def test_ready_returns_bead_when_blocked_by_dependency_is_closed(
        self, store: BeadStore
    ) -> None:
        """A bead blocked_by a closed bead is considered ready."""
        blocker = _make_bead(bead_id="bd-blocker", status="open")
        store.write(blocker)
        store.close("bd-blocker", "blocker done")

        blocked = _make_bead(bead_id="bd-blocked", status="open",
                             links=[BeadLink(target_bead_id="bd-blocker",
                                            link_type="blocked_by")])
        store.write(blocked)

        results = store.ready(_DEFAULT_TASK_ID)
        assert any(b.bead_id == "bd-blocked" for b in results)

    def test_ready_excludes_bead_when_blocked_by_dependency_is_open(
        self, store: BeadStore
    ) -> None:
        """A bead blocked_by an open bead must NOT be in the ready list."""
        blocker = _make_bead(bead_id="bd-block-open", status="open")
        store.write(blocker)

        blocked = _make_bead(bead_id="bd-blocked-open", status="open",
                             links=[BeadLink(target_bead_id="bd-block-open",
                                            link_type="blocked_by")])
        store.write(blocked)

        results = store.ready(_DEFAULT_TASK_ID)
        blocked_ids = {b.bead_id for b in results}
        assert "bd-blocked-open" not in blocked_ids

    def test_ready_ignores_non_blocked_by_links(self, store: BeadStore) -> None:
        """'relates_to' links should not affect ready status."""
        sibling = _make_bead(bead_id="bd-sibling", status="open")
        store.write(sibling)

        bead = _make_bead(bead_id="bd-with-relates-link", status="open",
                          links=[BeadLink(target_bead_id="bd-sibling",
                                         link_type="relates_to")])
        store.write(bead)

        results = store.ready(_DEFAULT_TASK_ID)
        ids = {b.bead_id for b in results}
        assert "bd-with-relates-link" in ids

    def test_ready_scoped_to_task_id(self, db_path: Path) -> None:
        """ready() must only return beads for the specified task."""
        s = BeadStore(db_path)
        s._table_exists()  # force schema to disk before seeding
        _seed_execution(db_path, "task-alpha")
        _seed_execution(db_path, "task-beta")

        bead_a = _make_bead(bead_id="bd-ta", task_id="task-alpha")
        bead_b = _make_bead(bead_id="bd-tb", task_id="task-beta")
        s.write(bead_a)
        s.write(bead_b)

        results = s.ready("task-alpha")
        assert all(b.task_id == "task-alpha" for b in results)
        assert not any(b.task_id == "task-beta" for b in results)

    def test_ready_empty_task_returns_empty_list(self, store: BeadStore) -> None:
        results = store.ready("nonexistent-task")
        assert results == []


# ---------------------------------------------------------------------------
# BeadStore.close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_sets_status_to_closed(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-cl01", status="open")
        store.write(bead)
        store.close("bd-cl01", "done")
        fetched = store.read("bd-cl01")
        assert fetched is not None
        assert fetched.status == "closed"

    def test_close_sets_closed_at(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-cl02", status="open")
        store.write(bead)
        store.close("bd-cl02", "done")
        fetched = store.read("bd-cl02")
        assert fetched is not None
        assert fetched.closed_at != ""

    def test_close_stores_summary(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-cl03", status="open")
        store.write(bead)
        store.close("bd-cl03", "JWT RS256 confirmed")
        fetched = store.read("bd-cl03")
        assert fetched is not None
        assert fetched.summary == "JWT RS256 confirmed"

    def test_close_is_idempotent_when_already_closed(self, store: BeadStore) -> None:
        """Closing an already-closed bead must not raise and must not change it."""
        bead = _make_bead(bead_id="bd-cl04", status="open")
        store.write(bead)
        store.close("bd-cl04", "first close")
        first_closed_at = store.read("bd-cl04").closed_at  # type: ignore[union-attr]

        # Second close — should be a no-op (WHERE status = 'open' will match 0 rows)
        store.close("bd-cl04", "second close")
        fetched = store.read("bd-cl04")
        assert fetched is not None
        assert fetched.status == "closed"
        assert fetched.closed_at == first_closed_at  # unchanged
        assert fetched.summary == "first close"  # not overwritten

    def test_close_missing_bead_does_not_raise(self, store: BeadStore) -> None:
        store.close("bd-nonexistent", "summary")  # must not raise

    def test_close_does_not_affect_other_beads(self, store: BeadStore) -> None:
        b1 = _make_bead(bead_id="bd-cl-a", status="open")
        b2 = _make_bead(bead_id="bd-cl-b", status="open")
        store.write(b1)
        store.write(b2)
        store.close("bd-cl-a", "done")
        b2_fetched = store.read("bd-cl-b")
        assert b2_fetched is not None
        assert b2_fetched.status == "open"


# ---------------------------------------------------------------------------
# BeadStore.link
# ---------------------------------------------------------------------------


class TestLink:
    def test_link_adds_typed_link_to_source_bead(self, store: BeadStore) -> None:
        b1 = _make_bead(bead_id="bd-lnk-src")
        b2 = _make_bead(bead_id="bd-lnk-tgt")
        store.write(b1)
        store.write(b2)
        store.link("bd-lnk-src", "bd-lnk-tgt", "relates_to")

        fetched = store.read("bd-lnk-src")
        assert fetched is not None
        assert len(fetched.links) == 1
        assert fetched.links[0].target_bead_id == "bd-lnk-tgt"
        assert fetched.links[0].link_type == "relates_to"

    def test_link_appends_to_existing_links(self, store: BeadStore) -> None:
        b1 = _make_bead(bead_id="bd-multi-src",
                        links=[BeadLink(target_bead_id="bd-prior", link_type="extends")])
        b2 = _make_bead(bead_id="bd-multi-tgt")
        store.write(b1)
        store.write(b2)
        store.link("bd-multi-src", "bd-multi-tgt", "contradicts")

        fetched = store.read("bd-multi-src")
        assert fetched is not None
        assert len(fetched.links) == 2
        link_types = {lnk.link_type for lnk in fetched.links}
        assert "extends" in link_types
        assert "contradicts" in link_types

    def test_link_sets_created_at_on_new_link(self, store: BeadStore) -> None:
        b1 = _make_bead(bead_id="bd-ts-src")
        b2 = _make_bead(bead_id="bd-ts-tgt")
        store.write(b1)
        store.write(b2)
        store.link("bd-ts-src", "bd-ts-tgt", "validates")

        fetched = store.read("bd-ts-src")
        assert fetched is not None
        assert fetched.links[0].created_at != ""

    def test_link_missing_source_bead_does_not_raise(self, store: BeadStore) -> None:
        """link() with a non-existent source bead must silently do nothing."""
        b2 = _make_bead(bead_id="bd-exists")
        store.write(b2)
        store.link("bd-ghost", "bd-exists", "relates_to")  # must not raise

    def test_link_all_supported_types(self, store: BeadStore) -> None:
        for i, link_type in enumerate(
            ("blocks", "blocked_by", "relates_to", "discovered_from",
             "validates", "contradicts", "extends")
        ):
            src = _make_bead(bead_id=f"bd-lt-{i:02d}-src")
            tgt = _make_bead(bead_id=f"bd-lt-{i:02d}-tgt")
            store.write(src)
            store.write(tgt)
            store.link(src.bead_id, tgt.bead_id, link_type)
            fetched = store.read(src.bead_id)
            assert fetched is not None
            assert fetched.links[0].link_type == link_type


# ---------------------------------------------------------------------------
# BeadStore.decay
# ---------------------------------------------------------------------------


class TestDecay:
    def test_decay_archives_old_closed_beads(self, store: BeadStore, db_path: Path) -> None:
        bead = _make_bead(bead_id="bd-old", status="open")
        store.write(bead)
        # Manually set closed_at to 10 days ago via direct SQL
        old_ts = _past_timestamp(10)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE beads SET status='closed', closed_at=? WHERE bead_id=?",
            (old_ts, "bd-old"),
        )
        conn.commit()
        conn.close()

        count = store.decay(max_age_days=7)
        assert count == 1

        fetched = store.read("bd-old")
        assert fetched is not None
        assert fetched.status == "archived"

    def test_decay_replaces_content_with_archival_marker(
        self, store: BeadStore, db_path: Path
    ) -> None:
        bead = _make_bead(bead_id="bd-arch-content", content="original verbose content")
        store.write(bead)
        old_ts = _past_timestamp(10)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE beads SET status='closed', closed_at=? WHERE bead_id=?",
            (old_ts, "bd-arch-content"),
        )
        conn.commit()
        conn.close()

        store.decay(max_age_days=7)
        fetched = store.read("bd-arch-content")
        assert fetched is not None
        assert "archived" in fetched.content.lower()

    def test_decay_leaves_open_beads_untouched(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-open-safe", status="open")
        store.write(bead)

        count = store.decay(max_age_days=0)
        assert count == 0
        fetched = store.read("bd-open-safe")
        assert fetched is not None
        assert fetched.status == "open"

    def test_decay_leaves_recently_closed_beads_untouched(self, store: BeadStore) -> None:
        bead = _make_bead(bead_id="bd-recent", status="open")
        store.write(bead)
        store.close("bd-recent", "done recently")

        count = store.decay(max_age_days=7)
        assert count == 0
        fetched = store.read("bd-recent")
        assert fetched is not None
        assert fetched.status == "closed"

    def test_decay_returns_count_of_archived_beads(
        self, store: BeadStore, db_path: Path
    ) -> None:
        old_ts = _past_timestamp(30)
        for i in range(3):
            bead = _make_bead(bead_id=f"bd-decay-{i:02d}")
            store.write(bead)

        conn = sqlite3.connect(str(db_path))
        for i in range(3):
            conn.execute(
                "UPDATE beads SET status='closed', closed_at=? WHERE bead_id=?",
                (old_ts, f"bd-decay-{i:02d}"),
            )
        conn.commit()
        conn.close()

        count = store.decay(max_age_days=7)
        assert count == 3

    def test_decay_scoped_by_task_id(self, db_path: Path) -> None:
        s = BeadStore(db_path)
        s._table_exists()  # force schema to disk before seeding
        _seed_execution(db_path, "task-alpha")
        _seed_execution(db_path, "task-beta")

        old_ts = _past_timestamp(10)
        b_a = _make_bead(bead_id="bd-da", task_id="task-alpha")
        b_b = _make_bead(bead_id="bd-db", task_id="task-beta")
        s.write(b_a)
        s.write(b_b)

        conn = sqlite3.connect(str(db_path))
        for bead_id in ("bd-da", "bd-db"):
            conn.execute(
                "UPDATE beads SET status='closed', closed_at=? WHERE bead_id=?",
                (old_ts, bead_id),
            )
        conn.commit()
        conn.close()

        count = s.decay(max_age_days=7, task_id="task-alpha")
        assert count == 1
        assert s.read("bd-da").status == "archived"  # type: ignore[union-attr]
        assert s.read("bd-db").status == "closed"  # type: ignore[union-attr]

    def test_decay_already_archived_beads_not_counted_again(
        self, store: BeadStore, db_path: Path
    ) -> None:
        bead = _make_bead(bead_id="bd-already-arch", status="open")
        store.write(bead)
        old_ts = _past_timestamp(10)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE beads SET status='archived', closed_at=? WHERE bead_id=?",
            (old_ts, "bd-already-arch"),
        )
        conn.commit()
        conn.close()

        count = store.decay(max_age_days=7)
        # archived status != 'closed', so WHERE status = 'closed' won't match
        assert count == 0


# ---------------------------------------------------------------------------
# Graceful degradation — methods return safe values when table absent
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """BeadStore must degrade silently when beads table does not exist."""

    @pytest.fixture
    def empty_db(self, tmp_path: Path) -> Path:
        """A SQLite DB with only _schema_version — no beads table."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO _schema_version VALUES (1)")
        conn.commit()
        conn.close()
        return db_path

    def _make_store_without_schema(self, db_path: Path) -> BeadStore:
        """Build a BeadStore without applying the v4 schema (beads table absent)."""
        from agent_baton.core.storage.connection import ConnectionManager
        store = BeadStore.__new__(BeadStore)
        store._conn_mgr = ConnectionManager(db_path)
        # configure_schema intentionally omitted so beads table is never created
        return store

    def test_write_returns_empty_string_when_table_absent(
        self, empty_db: Path
    ) -> None:
        store = self._make_store_without_schema(empty_db)
        bead = _make_bead()
        result = store.write(bead)
        assert result == ""

    def test_read_returns_none_when_table_absent(self, empty_db: Path) -> None:
        store = self._make_store_without_schema(empty_db)
        result = store.read("bd-anything")
        assert result is None

    def test_query_returns_empty_list_when_table_absent(self, empty_db: Path) -> None:
        store = self._make_store_without_schema(empty_db)
        result = store.query()
        assert result == []

    def test_ready_returns_empty_list_when_table_absent(self, empty_db: Path) -> None:
        store = self._make_store_without_schema(empty_db)
        result = store.ready("task-x")
        assert result == []

    def test_decay_returns_zero_when_table_absent(self, empty_db: Path) -> None:
        store = self._make_store_without_schema(empty_db)
        result = store.decay(max_age_days=7)
        assert result == 0


# ---------------------------------------------------------------------------
# Schema migration: v3 → v4 creates beads and bead_tags tables
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def _create_v3_db(self, db_path: Path) -> None:
        """Create a minimal v3 database (no beads table) at db_path."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS executions (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'running',
                current_phase INTEGER NOT NULL DEFAULT 0,
                current_step_index INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                pending_gaps TEXT NOT NULL DEFAULT '[]',
                resolved_decisions TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS step_results (
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete',
                outcome TEXT NOT NULL DEFAULT '',
                files_changed TEXT NOT NULL DEFAULT '[]',
                commit_hash TEXT NOT NULL DEFAULT '',
                estimated_tokens INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0.0,
                retries INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                deviations TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (task_id, step_id)
            );
        """)
        conn.execute("INSERT INTO _schema_version VALUES (3)")
        conn.commit()
        conn.close()

    def test_v3_database_gets_beads_table_after_init(self, tmp_path: Path) -> None:
        """Opening a v3 database with BeadStore must apply the v4 migration."""
        db_path = tmp_path / "v3_baton.db"
        self._create_v3_db(db_path)

        # Now initialise BeadStore against this v3 database.
        # The ConnectionManager should detect version 3 < 4 and apply MIGRATIONS[4].
        store = BeadStore(db_path)

        # Verify that the beads table now exists
        assert store._table_exists()

    def test_v3_to_v4_migration_beads_table_is_writable(self, tmp_path: Path) -> None:
        """After migration from v3, BeadStore.write() must succeed."""
        db_path = tmp_path / "v3_migrate.db"
        self._create_v3_db(db_path)

        store = BeadStore(db_path)
        # Force schema migration to run by calling a method that triggers get_connection()
        store._table_exists()

        # Seed an execution row (the beads FK requires one — schema is now on disk)
        _seed_execution(db_path, "task-migrated")

        bead = _make_bead(bead_id="bd-migrated", task_id="task-migrated")
        result = store.write(bead)
        assert result == "bd-migrated"

    def test_v3_to_v4_migration_schema_version_updated(self, tmp_path: Path) -> None:
        """After migration the _schema_version table must record version 4."""
        db_path = tmp_path / "v3_ver.db"
        self._create_v3_db(db_path)

        store = BeadStore(db_path)
        # Force migration by triggering get_connection()
        store._table_exists()

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT version FROM _schema_version").fetchone()
        conn.close()
        assert row[0] == 4


# ---------------------------------------------------------------------------
# Sync integration: beads sync to central.db via SyncEngine
# ---------------------------------------------------------------------------


class TestSyncIntegration:
    def _make_sqlite_store_with_execution(
        self, db_path: Path, task_id: str
    ):
        """Create a SqliteStorage, save a minimal execution, close it."""
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        from agent_baton.models.execution import (
            ExecutionState,
            MachinePlan,
            PlanPhase,
            PlanStep,
        )
        sqlite_store = SqliteStorage(db_path)
        step = PlanStep(step_id="1.1", agent_name="test-engineer",
                        task_description="Write tests")
        phase = PlanPhase(phase_id=1, name="Test", steps=[step], gate=None,
                          approval_required=False)
        plan = MachinePlan(task_id=task_id, task_summary="Sync test",
                           risk_level="LOW", phases=[phase])
        state = ExecutionState(
            task_id=task_id,
            plan=plan,
            status="complete",
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
        )
        sqlite_store.save_execution(state)
        sqlite_store.close()

    def test_beads_appear_in_central_db_after_push(self, tmp_path: Path) -> None:
        """Bead rows written to baton.db must sync to central.db via SyncEngine."""
        from agent_baton.core.storage.central import CentralStore
        from agent_baton.core.storage.sync import SyncEngine

        db_path = tmp_path / "proj" / "baton.db"
        db_path.parent.mkdir(parents=True)

        self._make_sqlite_store_with_execution(db_path, "task-sync-test")

        # Write beads directly via BeadStore
        bead_store = BeadStore(db_path)
        bead = _make_bead(
            bead_id="bd-sync-01",
            task_id="task-sync-test",
            content="Sync test discovery",
            tags=["sync", "test"],
        )
        bead_store.write(bead)

        # Push to central
        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("proj-sync", db_path)
        assert result.success, result.errors

        # Verify bead appears in central.db
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT bead_id, content FROM beads "
            "WHERE project_id = ? AND bead_id = ?",
            ("proj-sync", "bd-sync-01"),
        )
        assert len(rows) == 1
        assert rows[0]["bead_id"] == "bd-sync-01"
        assert rows[0]["content"] == "Sync test discovery"
        central.close()

    def test_bead_tags_appear_in_central_db_after_push(self, tmp_path: Path) -> None:
        """bead_tags rows must also sync to central.db after SyncEngine.push()."""
        from agent_baton.core.storage.central import CentralStore
        from agent_baton.core.storage.sync import SyncEngine

        db_path = tmp_path / "proj2" / "baton.db"
        db_path.parent.mkdir(parents=True)

        self._make_sqlite_store_with_execution(db_path, "task-tags-sync")

        bead_store = BeadStore(db_path)
        bead = _make_bead(
            bead_id="bd-tag-sync",
            task_id="task-tags-sync",
            tags=["production", "auth"],
        )
        bead_store.write(bead)

        central_path = tmp_path / "central2.db"
        engine = SyncEngine(central_path)
        result = engine.push("proj-tags", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT tag FROM bead_tags WHERE project_id = ? AND bead_id = ? ORDER BY tag",
            ("proj-tags", "bd-tag-sync"),
        )
        tags = [r["tag"] for r in rows]
        assert "auth" in tags
        assert "production" in tags
        central.close()
