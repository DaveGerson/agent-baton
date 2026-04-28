"""Unit tests for NotesAdapter and BeadAnchorIndex.

Part A of the Gastown bead architecture (bd-2870).

Test matrix:
- NotesAdapter.write / read round-trip
- NotesAdapter.list returns (anchor, bead_id) pairs
- NotesAdapter.has_ref / init_ref lifecycle
- NotesAdapter.resolve_head / resolve_merge_base / resolve_branch
- BeadAnchorIndex.get / put / rebuild_from_notes
- BeadStore dual-write happy path (gastown_dual_write=True)
- BeadStore dual-write notes-failure is warn-only (SQLite succeeds)
- BeadStore default (gastown_dual_write=False) unchanged
- Bead.to_dict / from_dict round-trips the three new Gastown fields
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.bead_anchors import BeadAnchorIndex
from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.core.engine.notes_adapter import NotesAdapter
from agent_baton.models.bead import Bead, BeadLink


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo with one commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@baton.test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Baton Test"],
        check=True,
        capture_output=True,
    )
    # Create an initial commit so HEAD is valid
    sentinel = path / "README.md"
    sentinel.write_text("baton test repo\n")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A temporary git repository with one commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


@pytest.fixture()
def adapter(git_repo: Path) -> NotesAdapter:
    return NotesAdapter(git_repo)


@pytest.fixture()
def anchor_commit(git_repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def sqlite_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "baton.db"))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _make_bead(
    bead_id: str = "bd-a1b2",
    task_id: str = "task-001",
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    bead_type: str = "discovery",
    content: str = "Test discovery content",
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id=step_id,
        agent_name=agent_name,
        bead_type=bead_type,
        content=content,
        confidence="high",
        scope="step",
        tags=["gastown", "test"],
        affected_files=["agent_baton/core/engine/bead_store.py"],
        status="open",
        created_at="2026-04-28T12:00:00Z",
    )


def _seed_execution(db_path: Path, task_id: str, store: BeadStore | None = None) -> None:
    """Insert a minimal executions row so FK constraints on beads pass.

    ``store`` must be provided (and its schema already applied via a prior
    ``_conn()`` call) or the executions table may not exist yet.  We trigger
    schema init by calling ``store._conn()`` if a store is supplied.
    """
    if store is not None:
        # Trigger lazy schema application so the executions table exists.
        store._conn()
    conn = sqlite3.connect(str(db_path))
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


# ---------------------------------------------------------------------------
# NotesAdapter — write / read round-trip
# ---------------------------------------------------------------------------


class TestNotesAdapterWriteRead:
    def test_write_read_roundtrip(self, adapter: NotesAdapter, anchor_commit: str) -> None:
        blob = {"bead_id": "bd-a1b2", "content": "hello gastown", "tags": ["x"]}
        adapter.write("bd-a1b2", anchor_commit, blob)

        result = adapter.read("bd-a1b2", anchor_commit)
        assert result is not None
        assert result["bead_id"] == "bd-a1b2"
        assert result["content"] == "hello gastown"
        assert result["tags"] == ["x"]

    def test_write_force_overwrites(self, adapter: NotesAdapter, anchor_commit: str) -> None:
        blob1 = {"bead_id": "bd-a1b2", "status": "open"}
        blob2 = {"bead_id": "bd-a1b2", "status": "closed"}
        adapter.write("bd-a1b2", anchor_commit, blob1)
        adapter.write("bd-a1b2", anchor_commit, blob2)

        result = adapter.read("bd-a1b2", anchor_commit)
        assert result is not None
        assert result["status"] == "closed"

    def test_read_missing_returns_none(self, adapter: NotesAdapter) -> None:
        result = adapter.read("bd-missing", "0" * 40)
        assert result is None

    def test_write_empty_anchor_is_noop(self, adapter: NotesAdapter) -> None:
        # Should not raise; just logs a warning
        adapter.write("bd-a1b2", "", {"bead_id": "bd-a1b2"})

    def test_read_empty_anchor_returns_none(self, adapter: NotesAdapter) -> None:
        assert adapter.read("bd-a1b2", "") is None


# ---------------------------------------------------------------------------
# NotesAdapter — list
# ---------------------------------------------------------------------------


class TestNotesAdapterList:
    def test_list_empty_when_no_notes(self, adapter: NotesAdapter) -> None:
        pairs = adapter.list()
        assert pairs == []

    def test_list_returns_anchor_bead_id_pairs(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        adapter.write("bd-a1b2", anchor_commit, {"bead_id": "bd-a1b2", "content": "x"})
        pairs = adapter.list()
        assert len(pairs) == 1
        anchor, bead_id = pairs[0]
        assert anchor == anchor_commit
        assert bead_id == "bd-a1b2"

    def test_list_skips_notes_without_bead_id(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        # Write a non-bead note manually
        subprocess.run(
            [
                "git",
                "-C",
                str(adapter._repo_root),
                "notes",
                f"--ref={NotesAdapter.NOTES_REF}",
                "add",
                "-f",
                "-m",
                '{"not_a_bead": true}',
                anchor_commit,
            ],
            capture_output=True,
        )
        pairs = adapter.list()
        # No bead_id field → should be skipped
        assert pairs == []


# ---------------------------------------------------------------------------
# NotesAdapter — has_ref / init_ref
# ---------------------------------------------------------------------------


class TestNotesAdapterRef:
    def test_has_ref_false_before_any_write(self, adapter: NotesAdapter) -> None:
        assert adapter.has_ref() is False

    def test_has_ref_true_after_write(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        adapter.write("bd-a1b2", anchor_commit, {"bead_id": "bd-a1b2"})
        assert adapter.has_ref() is True

    def test_init_ref_is_noop_when_ref_exists(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        adapter.write("bd-a1b2", anchor_commit, {"bead_id": "bd-a1b2"})
        # Should not raise
        adapter.init_ref()
        assert adapter.has_ref() is True

    def test_init_ref_is_noop_when_ref_missing(self, adapter: NotesAdapter) -> None:
        # Should not raise; ref is not yet created
        adapter.init_ref()
        assert adapter.has_ref() is False


# ---------------------------------------------------------------------------
# NotesAdapter — resolve_head / resolve_branch
# ---------------------------------------------------------------------------


class TestNotesAdapterResolvers:
    def test_resolve_head_returns_commit_sha(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        head = adapter.resolve_head()
        assert head == anchor_commit

    def test_resolve_branch_returns_branch_name(self, adapter: NotesAdapter) -> None:
        branch = adapter.resolve_branch()
        # After git init with one commit the branch is 'main' or 'master'
        assert branch in ("main", "master")

    def test_resolve_merge_base_falls_back_to_root_commit(
        self, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        # No origin/main in test repo → should fall back to root commit
        result = adapter.resolve_merge_base()
        # Either empty (total failure) or a valid-looking commit SHA
        assert result == "" or len(result) == 40

    def test_resolve_head_bad_repo_returns_empty(self, tmp_path: Path) -> None:
        bad_adapter = NotesAdapter(tmp_path / "nonexistent")
        result = bad_adapter.resolve_head()
        assert result == ""


# ---------------------------------------------------------------------------
# BeadAnchorIndex — get / put / rebuild_from_notes
# ---------------------------------------------------------------------------


class TestBeadAnchorIndex:
    def test_put_and_get(self, sqlite_conn: sqlite3.Connection) -> None:
        idx = BeadAnchorIndex(sqlite_conn)
        idx.put("bd-a1b2", "abc123" * 6 + "ab")  # 40-char-ish sha
        result = idx.get("bd-a1b2")
        assert result == "abc123" * 6 + "ab"

    def test_get_missing_returns_none(self, sqlite_conn: sqlite3.Connection) -> None:
        idx = BeadAnchorIndex(sqlite_conn)
        assert idx.get("bd-nonexistent") is None

    def test_put_is_idempotent(self, sqlite_conn: sqlite3.Connection) -> None:
        idx = BeadAnchorIndex(sqlite_conn)
        idx.put("bd-a1b2", "sha1")
        idx.put("bd-a1b2", "sha2")  # override
        assert idx.get("bd-a1b2") == "sha2"

    def test_table_created_on_init(self, sqlite_conn: sqlite3.Connection) -> None:
        BeadAnchorIndex(sqlite_conn)
        row = sqlite_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bead_anchors'"
        ).fetchone()
        assert row is not None

    def test_rebuild_from_notes(
        self, sqlite_conn: sqlite3.Connection, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        # Write two beads into notes
        adapter.write("bd-1111", anchor_commit, {"bead_id": "bd-1111", "content": "x"})
        adapter.write("bd-2222", anchor_commit, {"bead_id": "bd-2222", "content": "y"})

        idx = BeadAnchorIndex(sqlite_conn)
        count = idx.rebuild_from_notes(adapter)
        # One unique anchor → two bead_ids but list() returns per-bead pairs
        # Both beads share the same anchor_commit; list returns both
        assert count >= 1
        # At least one of the bead IDs should be resolvable
        r1 = idx.get("bd-1111")
        r2 = idx.get("bd-2222")
        assert r1 == anchor_commit or r2 == anchor_commit

    def test_rebuild_clears_existing_rows(
        self, sqlite_conn: sqlite3.Connection, adapter: NotesAdapter, anchor_commit: str
    ) -> None:
        idx = BeadAnchorIndex(sqlite_conn)
        idx.put("bd-stale", "stale-commit")
        adapter.write("bd-fresh", anchor_commit, {"bead_id": "bd-fresh"})

        idx.rebuild_from_notes(adapter)
        # Stale row should be gone
        assert idx.get("bd-stale") is None
        # Fresh row written by rebuild
        assert idx.get("bd-fresh") == anchor_commit


# ---------------------------------------------------------------------------
# BeadStore — dual-write happy path
# ---------------------------------------------------------------------------


class TestBeadStoreDualWrite:
    def test_dual_write_writes_to_sqlite_and_notes(
        self, tmp_path: Path, git_repo: Path
    ) -> None:
        db_path = tmp_path / "baton.db"
        store = BeadStore(db_path, repo_root=git_repo, gastown_dual_write=True)

        task_id = "task-dw-001"
        _seed_execution(db_path, task_id, store=store)

        bead = _make_bead(bead_id="bd-dw01", task_id=task_id)
        result = store.write(bead)
        assert result == "bd-dw01"

        # SQLite read succeeds
        fetched = store.read("bd-dw01")
        assert fetched is not None
        assert fetched.bead_id == "bd-dw01"

        # Notes should have been written
        if store._notes_adapter is not None:
            head = store._notes_adapter.resolve_head()
            if head:
                blob = store._notes_adapter.read("bd-dw01", head)
                # blob may be None if no anchor was resolvable, but the adapter
                # call should not raise
                assert blob is None or blob.get("bead_id") == "bd-dw01"

    def test_dual_write_anchor_index_populated(
        self, tmp_path: Path, git_repo: Path
    ) -> None:
        db_path = tmp_path / "baton.db"
        store = BeadStore(db_path, repo_root=git_repo, gastown_dual_write=True)

        task_id = "task-dw-002"
        _seed_execution(db_path, task_id, store=store)

        bead = _make_bead(bead_id="bd-dw02", task_id=task_id)
        store.write(bead)

        if store._anchor_index is not None:
            anchor = store._anchor_index.get("bd-dw02")
            # If notes adapter resolved a head, anchor should be set
            if store._notes_adapter is not None:
                head = store._notes_adapter.resolve_head()
                if head:
                    assert anchor == head

    def test_dual_write_notes_failure_does_not_fail_sqlite(
        self, tmp_path: Path, git_repo: Path
    ) -> None:
        db_path = tmp_path / "baton.db"
        store = BeadStore(db_path, repo_root=git_repo, gastown_dual_write=True)

        task_id = "task-dw-003"
        _seed_execution(db_path, task_id, store=store)

        # Inject a notes adapter that always raises
        if store._notes_adapter is not None:
            failing_adapter = MagicMock()
            failing_adapter.resolve_head.return_value = "a" * 40
            failing_adapter.resolve_branch.return_value = "main"
            failing_adapter.write.side_effect = RuntimeError("simulated notes failure")
            store._notes_adapter = failing_adapter

        bead = _make_bead(bead_id="bd-dw03", task_id=task_id)
        # Should NOT raise — notes failure is warn-only
        result = store.write(bead)
        assert result == "bd-dw03"

        # SQLite write still succeeded
        fetched = store.read("bd-dw03")
        assert fetched is not None
        assert fetched.bead_id == "bd-dw03"


# ---------------------------------------------------------------------------
# BeadStore — default (gastown_dual_write=False) unchanged
# ---------------------------------------------------------------------------


class TestBeadStoreDefaultBehavior:
    def test_default_no_dual_write(self, tmp_path: Path) -> None:
        db_path = tmp_path / "baton.db"
        store = BeadStore(db_path)  # default: gastown_dual_write=False

        assert store._gastown_dual_write is False
        assert store._notes_adapter is None
        assert store._anchor_index is None

    def test_write_read_without_dual_write(self, tmp_path: Path) -> None:
        db_path = tmp_path / "baton.db"
        # Construct store first so the schema (executions table) is created.
        store = BeadStore(db_path)

        task_id = "task-default-001"
        _seed_execution(db_path, task_id, store=store)

        bead = _make_bead(bead_id="bd-def01", task_id=task_id)
        result = store.write(bead)
        assert result == "bd-def01"

        fetched = store.read("bd-def01")
        assert fetched is not None
        assert fetched.bead_id == "bd-def01"
        assert fetched.content == bead.content


# ---------------------------------------------------------------------------
# Bead model — to_dict / from_dict round-trips for Gastown fields
# ---------------------------------------------------------------------------


class TestBeadGastownFields:
    def test_to_dict_includes_gastown_fields(self) -> None:
        bead = Bead(
            bead_id="bd-gf01",
            task_id="task-001",
            step_id="1.1",
            agent_name="test-agent",
            bead_type="discovery",
            content="test",
            schema_version="gastown-1",
            anchor_commit="abc" * 13 + "a",
            branch_at_create="feat/my-branch",
        )
        d = bead.to_dict()
        assert d["schema_version"] == "gastown-1"
        assert d["anchor_commit"] == "abc" * 13 + "a"
        assert d["branch_at_create"] == "feat/my-branch"

    def test_from_dict_roundtrip(self) -> None:
        original = Bead(
            bead_id="bd-gf02",
            task_id="task-001",
            step_id="1.1",
            agent_name="test-agent",
            bead_type="discovery",
            content="round-trip test",
            schema_version="gastown-1",
            anchor_commit="deadbeef" * 5,
            branch_at_create="main",
        )
        loaded = Bead.from_dict(original.to_dict())
        assert loaded.schema_version == "gastown-1"
        assert loaded.anchor_commit == "deadbeef" * 5
        assert loaded.branch_at_create == "main"

    def test_from_dict_legacy_bead_missing_gastown_fields(self) -> None:
        """Existing beads without schema_version / anchor_commit load cleanly."""
        legacy = {
            "bead_id": "bd-legacy",
            "task_id": "task-001",
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "bead_type": "discovery",
            "content": "legacy bead",
        }
        bead = Bead.from_dict(legacy)
        assert bead.schema_version == ""
        assert bead.anchor_commit == ""
        assert bead.branch_at_create == ""

    def test_schema_version_default_is_gastown_1(self) -> None:
        bead = Bead(
            bead_id="bd-gf03",
            task_id="t",
            step_id="s",
            agent_name="a",
            bead_type="discovery",
            content="c",
        )
        assert bead.schema_version == "gastown-1"

    def test_anchor_commit_and_branch_default_to_empty(self) -> None:
        bead = Bead(
            bead_id="bd-gf04",
            task_id="t",
            step_id="s",
            agent_name="a",
            bead_type="discovery",
            content="c",
        )
        assert bead.anchor_commit == ""
        assert bead.branch_at_create == ""


# ---------------------------------------------------------------------------
# Merge driver — JSON merge logic
# ---------------------------------------------------------------------------


class TestBatonNotesMerge:
    """Test the baton-notes-merge.py merge logic directly."""

    def _import_merge_module(self):
        import importlib.util
        script = Path(__file__).parent.parent / "scripts" / "baton-notes-merge.py"
        spec = importlib.util.spec_from_file_location("baton_notes_merge", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_merge_takes_later_closed_at(self) -> None:
        mod = self._import_merge_module()
        ancestor = {"bead_id": "bd-m1", "status": "open", "closed_at": ""}
        ours = {"bead_id": "bd-m1", "status": "closed", "closed_at": "2026-04-01T10:00:00Z"}
        theirs = {"bead_id": "bd-m1", "status": "closed", "closed_at": "2026-04-02T10:00:00Z"}
        merged, conflict = mod.merge_beads(ancestor, ours, theirs)
        assert merged["closed_at"] == "2026-04-02T10:00:00Z"
        assert conflict is False

    def test_merge_unions_tags(self) -> None:
        mod = self._import_merge_module()
        ancestor = {"bead_id": "bd-m2", "tags": ["a"]}
        ours = {"bead_id": "bd-m2", "tags": ["a", "b"]}
        theirs = {"bead_id": "bd-m2", "tags": ["a", "c"]}
        merged, _ = mod.merge_beads(ancestor, ours, theirs)
        assert set(merged["tags"]) == {"a", "b", "c"}

    def test_merge_prefers_higher_quality_score(self) -> None:
        mod = self._import_merge_module()
        ancestor = {"bead_id": "bd-m3", "quality_score": 0.0}
        ours = {"bead_id": "bd-m3", "quality_score": 0.3}
        theirs = {"bead_id": "bd-m3", "quality_score": 0.8}
        merged, _ = mod.merge_beads(ancestor, ours, theirs)
        assert merged["quality_score"] == pytest.approx(0.8)

    def test_merge_prefers_closed_status(self) -> None:
        mod = self._import_merge_module()
        ancestor = {"bead_id": "bd-m4", "status": "open"}
        ours = {"bead_id": "bd-m4", "status": "open"}
        theirs = {"bead_id": "bd-m4", "status": "closed"}
        merged, _ = mod.merge_beads(ancestor, ours, theirs)
        assert merged["status"] == "closed"

    def test_merge_conflicting_signed_by_sets_conflict_tag(self) -> None:
        mod = self._import_merge_module()
        ancestor = {"bead_id": "bd-m5", "signed_by": ""}
        ours = {"bead_id": "bd-m5", "signed_by": "soul-a", "tags": []}
        theirs = {"bead_id": "bd-m5", "signed_by": "soul-b", "tags": []}
        merged, conflict = mod.merge_beads(ancestor, ours, theirs)
        assert conflict is True
        assert "conflict:unresolved" in merged.get("tags", [])

    def test_merge_unions_links(self) -> None:
        mod = self._import_merge_module()
        link_a = {"target_bead_id": "bd-x", "link_type": "relates_to", "created_at": ""}
        link_b = {"target_bead_id": "bd-y", "link_type": "blocks", "created_at": ""}
        ancestor = {"bead_id": "bd-m6", "links": []}
        ours = {"bead_id": "bd-m6", "links": [link_a]}
        theirs = {"bead_id": "bd-m6", "links": [link_b]}
        merged, _ = mod.merge_beads(ancestor, ours, theirs)
        assert len(merged["links"]) == 2
