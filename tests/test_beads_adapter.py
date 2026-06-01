"""Tests for the Beads interop adapter (`.beads/issues.jsonl` reader).

ADR-13a interop seam: read an external `bd` project's exported JSONL into
Baton's central.db via the ExternalSourceAdapter protocol — no Go binary,
no Dolt, no subprocess.

Covers:
- self-registration under source_type "beads"
- connect() path resolution (dir, direct file, missing-file error)
- fetch_items() normalisation, type/priority mapping, malformed-line skip,
  item_types + since filtering
- fetch_item() lookup
- source_cmd _add/_sync round-trip with --config beads_dir
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agent_baton.core.storage.adapters import AdapterRegistry, ExternalItem
from agent_baton.core.storage.adapters.beads import BeadsAdapter
from agent_baton.core.storage.central import CentralStore


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SAMPLE_ISSUES = [
    {
        "id": "bd-a1b2",
        "title": "Fix the login redirect loop",
        "description": "Users bounce between /login and /home.",
        "status": "open",
        "issue_type": "bug",
        "priority": 1,
        "assignee": "alice",
        "labels": ["auth", "regression"],
        "updated": "2026-05-20T10:00:00Z",
    },
    {
        "id": "bd-c3d4",
        "title": "Add dark mode",
        "status": "in_progress",
        "issue_type": "feature",
        "priority": "p2",
        "updated": "2026-05-22T12:00:00Z",
        "dependencies": [{"type": "parent-child", "target": "bd-epic1"}],
    },
    {
        "id": "bd-e5f6",
        "title": "Internal agent chatter",
        "issue_type": "convoy",  # unknown → task
        "updated": "2026-05-25T08:00:00Z",
    },
]


def _write_beads_dir(tmp_path: Path, issues=_SAMPLE_ISSUES) -> Path:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    jsonl = beads_dir / "issues.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for issue in issues:
            fh.write(json.dumps(issue) + "\n")
    return beads_dir


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_beads_registered_after_import():
    assert "beads" in AdapterRegistry.available()
    assert AdapterRegistry.get("beads") is BeadsAdapter


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


def test_connect_resolves_directory(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    assert adapter._issues_path == beads_dir / "issues.jsonl"
    assert adapter._source_id.startswith("beads-")


def test_connect_accepts_direct_file(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"path": str(beads_dir / "issues.jsonl")})
    assert adapter._issues_path == beads_dir / "issues.jsonl"


def test_connect_missing_file_raises(tmp_path):
    adapter = BeadsAdapter()
    with pytest.raises(ValueError, match="not found"):
        adapter.connect({"beads_dir": str(tmp_path / "nope")})


# ---------------------------------------------------------------------------
# fetch_items()
# ---------------------------------------------------------------------------


def test_fetch_items_normalises_all(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    items = adapter.fetch_items()
    assert len(items) == 3
    by_id = {i.external_id: i for i in items}

    bug = by_id["bd-a1b2"]
    assert bug.item_type == "bug"
    assert bug.state == "open"
    assert bug.priority == 1
    assert bug.assigned_to == "alice"
    assert set(bug.tags) == {"auth", "regression"}
    assert bug.updated_at == "2026-05-20T10:00:00Z"
    assert bug.raw_data["id"] == "bd-a1b2"

    feat = by_id["bd-c3d4"]
    assert feat.item_type == "feature"
    assert feat.priority == 2  # "p2" → 2
    assert feat.parent_id == "bd-epic1"  # derived from parent-child dep

    unknown = by_id["bd-e5f6"]
    assert unknown.item_type == "task"  # convoy falls back to task


def test_fetch_items_filters_by_type(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    items = adapter.fetch_items(item_types=["bug"])
    assert [i.external_id for i in items] == ["bd-a1b2"]


def test_fetch_items_filters_by_since(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    items = adapter.fetch_items(since="2026-05-22T00:00:00Z")
    assert {i.external_id for i in items} == {"bd-c3d4", "bd-e5f6"}


def test_fetch_items_skips_malformed_lines(tmp_path):
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text(
        '{"id": "bd-ok", "title": "good"}\n'
        "not json at all\n"
        '{"no_id": true}\n'
        "\n"
        '{"id": "bd-ok2", "title": "also good"}\n',
        encoding="utf-8",
    )
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    items = adapter.fetch_items()
    assert {i.external_id for i in items} == {"bd-ok", "bd-ok2"}


def test_fetch_item_found_and_missing(tmp_path):
    beads_dir = _write_beads_dir(tmp_path)
    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": str(beads_dir)})
    assert adapter.fetch_item("bd-a1b2").title == "Fix the login redirect loop"
    assert adapter.fetch_item("bd-zzzz") is None


def test_satisfies_external_source_protocol():
    from agent_baton.core.storage.adapters import ExternalSourceAdapter

    assert isinstance(BeadsAdapter(), ExternalSourceAdapter)


# ---------------------------------------------------------------------------
# priority coercion edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(0, 0), (3, 3), ("2", 2), ("p1", 1), ("P0", 0), (None, 0), ("high", 0), (True, 0)],
)
def test_priority_coercion(value, expected):
    assert BeadsAdapter._coerce_priority(value) == expected


# ---------------------------------------------------------------------------
# source_cmd _add / _sync round-trip
# ---------------------------------------------------------------------------


def test_source_add_and_sync_beads(tmp_path, monkeypatch, capsys):
    from agent_baton.cli.commands.source_cmd import _add, _sync

    beads_dir = _write_beads_dir(tmp_path)
    central_db = tmp_path / "central.db"
    monkeypatch.setattr(
        "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
        central_db,
    )

    add_args = argparse.Namespace(
        source_type="beads",
        name="Local Beads",
        org="",
        source_project="",
        pat_env="",
        url="",
        config_json=json.dumps({"beads_dir": str(beads_dir)}),
    )
    _add(add_args)

    store = CentralStore(central_db)
    rows = store.query("SELECT source_id, config FROM external_sources WHERE source_type = 'beads'")
    store.close()
    assert len(rows) == 1
    source_id = rows[0]["source_id"]
    assert json.loads(rows[0]["config"])["beads_dir"] == str(beads_dir)

    sync_args = argparse.Namespace(source_id=source_id, sync_all=False)
    _sync(sync_args)

    store2 = CentralStore(central_db)
    items = store2.query(
        "SELECT external_id, item_type FROM external_items WHERE source_id = ? ORDER BY external_id",
        (source_id,),
    )
    store2.close()
    assert {r["external_id"] for r in items} == {"bd-a1b2", "bd-c3d4", "bd-e5f6"}
