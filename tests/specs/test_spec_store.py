"""Tests for F0.1 SpecStore — CRUD + lifecycle."""
from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path

from agent_baton.models.spec import Spec, SPEC_STATES, _hash_content
from agent_baton.core.specs.store import SpecStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Return a path to a fresh SQLite DB with the v16 schema applied."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specs (
            spec_id      TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL DEFAULT 'default',
            author_id    TEXT NOT NULL DEFAULT 'local-user',
            task_type    TEXT NOT NULL DEFAULT '',
            template_id  TEXT NOT NULL DEFAULT '',
            title        TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'draft',
            content      TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            score_json   TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL DEFAULT '',
            updated_at   TEXT NOT NULL DEFAULT '',
            approved_at  TEXT NOT NULL DEFAULT '',
            approved_by  TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS spec_plan_links (
            spec_id    TEXT NOT NULL,
            task_id    TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT 'default',
            linked_at  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (spec_id, task_id)
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def store(db: Path) -> SpecStore:
    return SpecStore(db_path=db)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_create_returns_draft_spec(store: SpecStore) -> None:
    spec = store.create(title="My Feature", task_type="feature")
    assert spec.state == "draft"
    assert spec.title == "My Feature"
    assert spec.task_type == "feature"
    assert spec.spec_id  # non-empty UUID


def test_create_hashes_content(store: SpecStore) -> None:
    spec = store.create(title="T", content="hello world")
    assert spec.content_hash == _hash_content("hello world")


def test_create_empty_content_empty_hash(store: SpecStore) -> None:
    spec = store.create(title="T")
    assert spec.content == ""
    assert spec.content_hash == ""


def test_create_with_explicit_id(store: SpecStore) -> None:
    spec = store.create(title="T", spec_id="my-explicit-id")
    assert spec.spec_id == "my-explicit-id"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def test_get_returns_created_spec(store: SpecStore) -> None:
    created = store.create(title="Read Test", content="body")
    fetched = store.get(created.spec_id)
    assert fetched is not None
    assert fetched.spec_id == created.spec_id
    assert fetched.title == "Read Test"
    assert fetched.content == "body"


def test_get_nonexistent_returns_none(store: SpecStore) -> None:
    assert store.get("does-not-exist") is None


def test_list_returns_created_specs(store: SpecStore) -> None:
    store.create(title="A")
    store.create(title="B")
    specs = store.list()
    assert len(specs) >= 2


def test_list_filters_by_state(store: SpecStore) -> None:
    store.create(title="Draft1")
    spec2 = store.create(title="ToApprove")
    store.update_state(spec2.spec_id, "reviewed")
    store.update_state(spec2.spec_id, "approved")
    drafts = store.list(state="draft")
    approved = store.list(state="approved")
    assert all(s.state == "draft" for s in drafts)
    assert all(s.state == "approved" for s in approved)


def test_list_filters_by_project(store: SpecStore) -> None:
    store.create(title="P1", project_id="proj-a")
    store.create(title="P2", project_id="proj-b")
    pa = store.list(project_id="proj-a")
    pb = store.list(project_id="proj-b")
    assert all(s.project_id == "proj-a" for s in pa)
    assert all(s.project_id == "proj-b" for s in pb)


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def test_update_state_draft_to_reviewed(store: SpecStore) -> None:
    spec = store.create(title="Lifecycle")
    updated = store.update_state(spec.spec_id, "reviewed")
    assert updated.state == "reviewed"


def test_update_state_reviewed_to_approved(store: SpecStore) -> None:
    spec = store.create(title="Lifecycle")
    store.update_state(spec.spec_id, "reviewed")
    updated = store.update_state(spec.spec_id, "approved", actor="reviewer-1")
    assert updated.state == "approved"
    assert updated.approved_by == "reviewer-1"
    assert updated.approved_at != ""


def test_update_state_invalid_transition_raises(store: SpecStore) -> None:
    spec = store.create(title="Invalid")
    with pytest.raises(ValueError, match="Cannot transition"):
        store.update_state(spec.spec_id, "completed")  # draft→completed invalid


def test_update_state_unknown_state_raises(store: SpecStore) -> None:
    spec = store.create(title="Bad")
    with pytest.raises(ValueError, match="Unknown state"):
        store.update_state(spec.spec_id, "nonexistent")


def test_update_state_nonexistent_spec_raises(store: SpecStore) -> None:
    with pytest.raises(ValueError, match="not found"):
        store.update_state("no-such-id", "reviewed")


# ---------------------------------------------------------------------------
# Content update
# ---------------------------------------------------------------------------

def test_update_content_refreshes_hash(store: SpecStore) -> None:
    spec = store.create(title="C", content="original")
    updated = store.update_content(spec.spec_id, "revised")
    assert updated.content == "revised"
    assert updated.content_hash == _hash_content("revised")
    assert updated.updated_at >= spec.updated_at


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_score_stores_scorecard(store: SpecStore) -> None:
    spec = store.create(title="Score Me")
    scored = store.score(spec.spec_id, {"clarity": 0.9, "testability": 0.7})
    assert scored.score() == {"clarity": 0.9, "testability": 0.7}


# ---------------------------------------------------------------------------
# Plan linking
# ---------------------------------------------------------------------------

def test_link_to_plan(store: SpecStore) -> None:
    spec = store.create(title="Link Test")
    store.link_to_plan(spec.spec_id, "task-abc-123")
    fetched = store.get(spec.spec_id)
    assert "task-abc-123" in fetched.linked_plan_ids


def test_link_idempotent(store: SpecStore) -> None:
    spec = store.create(title="Idempotent Link")
    store.link_to_plan(spec.spec_id, "task-xyz")
    store.link_to_plan(spec.spec_id, "task-xyz")  # second call must not error
    fetched = store.get(spec.spec_id)
    assert fetched.linked_plan_ids.count("task-xyz") == 1


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------

def test_export_import_roundtrip(store: SpecStore) -> None:
    spec = store.create(title="Export Me", content="yaml body")
    json_str = store.export_json(spec.spec_id)
    data = json.loads(json_str)
    assert data["spec_id"] == spec.spec_id
    assert data["title"] == "Export Me"

    # Import into a second store
    store2 = SpecStore(db_path=store._db_path)
    imported = store2.import_json(json_str)
    assert imported.spec_id == spec.spec_id
    assert imported.title == "Export Me"


def test_import_no_overwrite_returns_existing(store: SpecStore) -> None:
    spec = store.create(title="Original")
    json_str = store.export_json(spec.spec_id)
    # Modify the JSON to have a different title
    data = json.loads(json_str)
    data["title"] = "Modified"
    result = store.import_json(json.dumps(data), overwrite=False)
    assert result.title == "Original"  # not overwritten


def test_import_overwrite_replaces(store: SpecStore) -> None:
    spec = store.create(title="Original")
    json_str = store.export_json(spec.spec_id)
    data = json.loads(json_str)
    data["title"] = "Modified"
    result = store.import_json(json.dumps(data), overwrite=True)
    assert result.title == "Modified"
