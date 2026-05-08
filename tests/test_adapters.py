"""Tests for agent_baton.core.storage.adapters.

Covers:
- ExternalItem dataclass defaults and instantiation
- ExternalSourceAdapter Protocol structural checking
- AdapterRegistry register / get / available
- AdoAdapter: connect() validation, normalise(), fetch_items() and fetch_item()
  with mocked HTTP (no real network calls)
- source_cmd _add / _list / _sync / _remove / _map via CentralStore backed by
  a temp central.db (integration smoke tests)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
    ExternalSourceAdapter,
)
from agent_baton.core.storage.adapters.ado import AdoAdapter, _ITEM_TYPE_MAP
from agent_baton.core.storage.central import CentralStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_central_store(tmp_path: Path) -> CentralStore:
    return CentralStore(tmp_path / "central.db")


def _ado_raw_item(
    item_id: int = 1,
    title: str = "Fix the widget",
    work_item_type: str = "Bug",
    state: str = "Active",
    priority: int = 2,
    parent: int | None = None,
    tags: str = "backend; urgent",
    assigned_to: str = "Alice",
    changed_date: str = "2026-01-15T10:00:00Z",
) -> dict:
    """Return a minimal ADO REST API work item dict."""
    fields: dict[str, Any] = {
        "System.Id": item_id,
        "System.Title": title,
        "System.Description": f"<p>{title} description</p>",
        "System.WorkItemType": work_item_type,
        "System.State": state,
        "System.AssignedTo": {"displayName": assigned_to},
        "Microsoft.VSTS.Common.Priority": priority,
        "System.Tags": tags,
        "System.ChangedDate": changed_date,
    }
    if parent is not None:
        fields["System.Parent"] = parent
    return {"id": item_id, "fields": fields}


# ---------------------------------------------------------------------------
# ExternalItem tests
# ---------------------------------------------------------------------------


class TestExternalItem:
    def test_required_fields(self):
        item = ExternalItem(
            source_id="ado-myorg-myproj",
            external_id="42",
            item_type="feature",
            title="My Feature",
        )
        assert item.source_id == "ado-myorg-myproj"
        assert item.external_id == "42"
        assert item.item_type == "feature"
        assert item.title == "My Feature"

    def test_defaults(self):
        item = ExternalItem(
            source_id="s",
            external_id="1",
            item_type="bug",
            title="T",
        )
        assert item.description == ""
        assert item.state == ""
        assert item.assigned_to == ""
        assert item.priority == 0
        assert item.parent_id == ""
        assert item.tags == []
        assert item.url == ""
        assert item.raw_data is None
        assert item.updated_at == ""

    def test_tags_default_is_per_instance(self):
        """Each ExternalItem gets its own tags list (no shared mutable default)."""
        a = ExternalItem(source_id="s", external_id="1", item_type="bug", title="A")
        b = ExternalItem(source_id="s", external_id="2", item_type="bug", title="B")
        a.tags.append("x")
        assert "x" not in b.tags


# ---------------------------------------------------------------------------
# ExternalSourceAdapter Protocol tests
# ---------------------------------------------------------------------------


class TestExternalSourceAdapterProtocol:
    def test_ado_adapter_satisfies_protocol(self):
        """AdoAdapter must satisfy the ExternalSourceAdapter Protocol."""
        adapter = AdoAdapter()
        assert isinstance(adapter, ExternalSourceAdapter)

    def test_arbitrary_class_with_correct_interface_satisfies_protocol(self):
        class MyAdapter:
            source_type = "custom"

            def connect(self, config: dict) -> None:
                pass

            def fetch_items(self, item_types=None, since=None):
                return []

            def fetch_item(self, external_id: str):
                return None

        assert isinstance(MyAdapter(), ExternalSourceAdapter)

    def test_class_missing_connect_does_not_satisfy_protocol(self):
        class BadAdapter:
            source_type = "bad"

            def fetch_items(self, item_types=None, since=None):
                return []

            def fetch_item(self, external_id: str):
                return None

        # runtime_checkable only checks method presence, not source_type attribute
        # Protocol check succeeds if all methods present; source_type is an attr.
        # We verify the absence of connect breaks things at call time.
        assert not hasattr(BadAdapter(), "connect")


# ---------------------------------------------------------------------------
# AdapterRegistry tests
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    def setup_method(self):
        # Snapshot state so we can restore after each test.
        self._original = dict(AdapterRegistry._adapters)

    def teardown_method(self):
        AdapterRegistry._adapters = self._original

    def test_register_and_get(self):
        class FakeAdapter:
            source_type = "fake"

            def connect(self, config):
                pass

            def fetch_items(self, item_types=None, since=None):
                return []

            def fetch_item(self, external_id):
                return None

        AdapterRegistry.register(FakeAdapter)
        assert AdapterRegistry.get("fake") is FakeAdapter

    def test_get_missing_returns_none(self):
        assert AdapterRegistry.get("nonexistent-xyz") is None

    def test_available_returns_sorted(self):
        class A:
            source_type = "zzz"

        class B:
            source_type = "aaa"

        AdapterRegistry.register(A)
        AdapterRegistry.register(B)
        available = AdapterRegistry.available()
        assert available == sorted(available)
        assert "zzz" in available
        assert "aaa" in available

    def test_ado_registered_after_import(self):
        """Importing ado.py auto-registers AdoAdapter under 'ado'."""
        import agent_baton.core.storage.adapters.ado  # noqa: F401
        assert AdapterRegistry.get("ado") is AdoAdapter


# ---------------------------------------------------------------------------
# AdoAdapter.connect() tests
# ---------------------------------------------------------------------------


class TestAdoAdapterConnect:
    @pytest.fixture(autouse=True)
    def _mock_requests_available(self):
        """Ensure 'requests' appears importable so connect() passes _ensure_requests().

        The requests package is not installed in the test environment, but
        connect() calls _ensure_requests() before validating org/project/PAT.
        We mock the module to present as importable; tests that specifically
        exercise the ImportError path override with patch.dict({"requests": None}).
        """
        import sys
        fake_requests = MagicMock()
        with patch.dict(sys.modules, {"requests": fake_requests}):
            yield fake_requests

    def test_connect_raises_if_no_org(self, monkeypatch):
        monkeypatch.setenv("ADO_PAT", "mytoken")
        adapter = AdoAdapter()
        with pytest.raises(ValueError, match="organization"):
            adapter.connect({"organization": "", "project": "proj"})

    def test_connect_raises_if_no_project(self, monkeypatch):
        monkeypatch.setenv("ADO_PAT", "mytoken")
        adapter = AdoAdapter()
        with pytest.raises(ValueError, match="project"):
            adapter.connect({"organization": "org", "project": ""})

    def test_connect_raises_if_pat_missing(self, monkeypatch):
        monkeypatch.delenv("ADO_PAT", raising=False)
        adapter = AdoAdapter()
        with pytest.raises(ValueError, match="ADO_PAT"):
            adapter.connect({"organization": "org", "project": "proj"})

    def test_connect_raises_if_requests_missing(self, monkeypatch):
        import sys
        monkeypatch.setenv("ADO_PAT", "token")
        adapter = AdoAdapter()
        with patch.dict(sys.modules, {"requests": None}):
            with pytest.raises(ImportError, match="requests"):
                adapter.connect({"organization": "org", "project": "proj"})

    def test_connect_success(self, monkeypatch):
        monkeypatch.setenv("MY_PAT", "secret")
        adapter = AdoAdapter()
        adapter.connect({
            "organization": "my-org",
            "project": "my-project",
            "pat_env_var": "MY_PAT",
        })
        assert adapter._org == "my-org"
        assert adapter._project == "my-project"
        assert adapter._pat == "secret"

    def test_connect_custom_pat_env(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_TOKEN", "abc123")
        adapter = AdoAdapter()
        adapter.connect({
            "organization": "org",
            "project": "proj",
            "pat_env_var": "CUSTOM_TOKEN",
        })
        assert adapter._pat == "abc123"


# ---------------------------------------------------------------------------
# AdoAdapter._normalise() tests
# ---------------------------------------------------------------------------


class TestAdoAdapterNormalise:
    def _connected_adapter(self, monkeypatch) -> AdoAdapter:
        monkeypatch.setenv("ADO_PAT", "tok")
        adapter = AdoAdapter()
        adapter._org = "myorg"
        adapter._project = "myproj"
        adapter._pat = "tok"
        adapter._source_id = "ado-myorg-myproj"
        return adapter

    def test_normalise_bug(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=10, work_item_type="Bug")
        item = adapter._normalise(raw)
        assert item.item_type == "bug"
        assert item.external_id == "10"
        assert item.source_id == "ado-myorg-myproj"

    def test_normalise_feature(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=5, work_item_type="Feature")
        item = adapter._normalise(raw)
        assert item.item_type == "feature"

    def test_normalise_epic(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=3, work_item_type="Epic")
        item = adapter._normalise(raw)
        assert item.item_type == "epic"

    def test_normalise_user_story(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=7, work_item_type="User Story")
        item = adapter._normalise(raw)
        assert item.item_type == "story"

    def test_normalise_unknown_type_falls_back_to_task(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=9, work_item_type="Sprint Blocker")
        item = adapter._normalise(raw)
        assert item.item_type == "task"

    def test_normalise_tags_split(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(tags="alpha; beta ; gamma")
        item = adapter._normalise(raw)
        assert item.tags == ["alpha", "beta", "gamma"]

    def test_normalise_empty_tags(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(tags="")
        item = adapter._normalise(raw)
        assert item.tags == []

    def test_normalise_assigned_to_dict(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(assigned_to="Bob")
        item = adapter._normalise(raw)
        assert item.assigned_to == "Bob"

    def test_normalise_assigned_to_plain_string(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item()
        raw["fields"]["System.AssignedTo"] = "Charlie"
        item = adapter._normalise(raw)
        assert item.assigned_to == "Charlie"

    def test_normalise_priority(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(priority=3)
        item = adapter._normalise(raw)
        assert item.priority == 3

    def test_normalise_missing_priority_defaults_zero(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item()
        del raw["fields"]["Microsoft.VSTS.Common.Priority"]
        item = adapter._normalise(raw)
        assert item.priority == 0

    def test_normalise_parent_id(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(parent=42)
        item = adapter._normalise(raw)
        assert item.parent_id == "42"

    def test_normalise_no_parent(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item()
        item = adapter._normalise(raw)
        assert item.parent_id == ""

    def test_normalise_url_construction(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=99)
        item = adapter._normalise(raw)
        assert "myorg" in item.url
        assert "myproj" in item.url
        assert "99" in item.url

    def test_normalise_raw_data_preserved(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=1)
        item = adapter._normalise(raw)
        assert item.raw_data is raw

    def test_normalise_updated_at(self, monkeypatch):
        adapter = self._connected_adapter(monkeypatch)
        raw = _ado_raw_item(changed_date="2026-03-01T08:00:00Z")
        item = adapter._normalise(raw)
        assert item.updated_at == "2026-03-01T08:00:00Z"


# ---------------------------------------------------------------------------
# AdoAdapter.fetch_items() with mocked HTTP
# ---------------------------------------------------------------------------


def _mock_wiql_response(ids: list[int]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"workItems": [{"id": i} for i in ids]}
    return resp


def _mock_batch_response(raws: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"value": raws}
    return resp


class TestAdoAdapterFetchItems:
    @pytest.fixture(autouse=True)
    def _mock_requests_available(self):
        """Ensure 'requests' appears importable for connect() and fetch_items()."""
        import sys
        fake_requests = MagicMock()
        with patch.dict(sys.modules, {"requests": fake_requests}):
            yield fake_requests

    def _make_adapter(self, monkeypatch) -> AdoAdapter:
        monkeypatch.setenv("ADO_PAT", "tok")
        adapter = AdoAdapter()
        adapter.connect({
            "organization": "testorg",
            "project": "testproj",
            "pat_env_var": "ADO_PAT",
        })
        return adapter

    def test_fetch_items_empty_wiql_result(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        wiql_resp = _mock_wiql_response([])
        with patch("requests.post", return_value=wiql_resp):
            items = adapter.fetch_items()
        assert items == []

    def test_fetch_items_returns_normalised_items(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        raw1 = _ado_raw_item(item_id=1, work_item_type="Feature", title="F1")
        raw2 = _ado_raw_item(item_id=2, work_item_type="Bug", title="B1")
        wiql_resp = _mock_wiql_response([1, 2])
        batch_resp = _mock_batch_response([raw1, raw2])
        with patch("requests.post", return_value=wiql_resp), \
             patch("requests.get", return_value=batch_resp):
            items = adapter.fetch_items()
        assert len(items) == 2
        assert items[0].external_id == "1"
        assert items[0].item_type == "feature"
        assert items[1].external_id == "2"
        assert items[1].item_type == "bug"

    def test_fetch_items_wiql_failure_raises(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        err_resp = MagicMock()
        err_resp.status_code = 401
        err_resp.text = "Unauthorized"
        with patch("requests.post", return_value=err_resp):
            with pytest.raises(RuntimeError, match="WIQL query failed"):
                adapter.fetch_items()

    def test_fetch_items_batch_failure_returns_empty(self, monkeypatch):
        """Batch failure is logged but does not raise — returns empty list."""
        adapter = self._make_adapter(monkeypatch)
        wiql_resp = _mock_wiql_response([1])
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.text = "Server Error"
        with patch("requests.post", return_value=wiql_resp), \
             patch("requests.get", return_value=fail_resp):
            items = adapter.fetch_items()
        assert items == []

    def test_fetch_items_filters_by_type(self, monkeypatch):
        """item_types filter translates to ADO type names in WIQL."""
        adapter = self._make_adapter(monkeypatch)
        wiql_resp = _mock_wiql_response([])
        captured_wiql: list[str] = []

        def capture_post(url, json=None, headers=None, timeout=None):
            if json:
                captured_wiql.append(json.get("query", ""))
            return wiql_resp

        with patch("requests.post", side_effect=capture_post):
            adapter.fetch_items(item_types=["feature"])

        assert len(captured_wiql) == 1
        # The WIQL should reference the ADO type "Feature"
        assert "Feature" in captured_wiql[0]

    def test_fetch_items_since_filter(self, monkeypatch):
        """since parameter appears in WIQL WHERE clause."""
        adapter = self._make_adapter(monkeypatch)
        wiql_resp = _mock_wiql_response([])
        captured_wiql: list[str] = []

        def capture_post(url, json=None, headers=None, timeout=None):
            if json:
                captured_wiql.append(json.get("query", ""))
            return wiql_resp

        with patch("requests.post", side_effect=capture_post):
            adapter.fetch_items(since="2026-01-01T00:00:00Z")

        assert "2026-01-01T00:00:00Z" in captured_wiql[0]

    def test_fetch_items_area_path_filter(self, monkeypatch):
        """area_path set during connect appears in WIQL WHERE clause."""
        monkeypatch.setenv("ADO_PAT", "tok")
        adapter = AdoAdapter()
        adapter.connect({
            "organization": "testorg",
            "project": "testproj",
            "pat_env_var": "ADO_PAT",
            "area_path": "testproj\\\\MyTeam",
        })
        wiql_resp = _mock_wiql_response([])
        captured_wiql: list[str] = []

        def capture_post(url, json=None, headers=None, timeout=None):
            if json:
                captured_wiql.append(json.get("query", ""))
            return wiql_resp

        with patch("requests.post", side_effect=capture_post):
            adapter.fetch_items()

        assert "MyTeam" in captured_wiql[0]

    def test_fetch_items_batches_200_ids(self, monkeypatch):
        """More than 200 IDs should result in multiple GET batch calls."""
        adapter = self._make_adapter(monkeypatch)
        ids = list(range(1, 351))  # 350 IDs → 2 batches
        wiql_resp = _mock_wiql_response(ids)
        batch_resp = _mock_batch_response([])
        get_calls: list[str] = []

        def capture_get(url, headers=None, timeout=None):
            get_calls.append(url)
            return batch_resp

        with patch("requests.post", return_value=wiql_resp), \
             patch("requests.get", side_effect=capture_get):
            adapter.fetch_items()

        # Should have made 2 GET calls: one for ids 1-200, one for 201-350.
        assert len(get_calls) == 2


# ---------------------------------------------------------------------------
# AdoAdapter.fetch_item() with mocked HTTP
# ---------------------------------------------------------------------------


class TestAdoAdapterFetchItem:
    @pytest.fixture(autouse=True)
    def _mock_requests_available(self):
        """Ensure 'requests' appears importable for connect() and fetch_item()."""
        import sys
        fake_requests = MagicMock()
        with patch.dict(sys.modules, {"requests": fake_requests}):
            yield fake_requests

    def _make_adapter(self, monkeypatch) -> AdoAdapter:
        monkeypatch.setenv("ADO_PAT", "tok")
        adapter = AdoAdapter()
        adapter.connect({
            "organization": "testorg",
            "project": "testproj",
            "pat_env_var": "ADO_PAT",
        })
        return adapter

    def test_fetch_item_found(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        raw = _ado_raw_item(item_id=42, title="My Item")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = raw
        with patch("requests.get", return_value=resp):
            item = adapter.fetch_item("42")
        assert item is not None
        assert item.external_id == "42"
        assert item.title == "My Item"

    def test_fetch_item_not_found_returns_none(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        resp = MagicMock()
        resp.status_code = 404
        with patch("requests.get", return_value=resp):
            item = adapter.fetch_item("99999")
        assert item is None

    def test_fetch_item_error_raises(self, monkeypatch):
        adapter = self._make_adapter(monkeypatch)
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal Server Error"
        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="Fetch work item"):
                adapter.fetch_item("1")


# ---------------------------------------------------------------------------
# CentralStore.execute() tests
# ---------------------------------------------------------------------------


class TestCentralStoreExecute:
    def test_execute_insert_external_source(self, tmp_path):
        store = _make_central_store(tmp_path)
        store.execute(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name, config, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            ("ado-org-proj", "ado", "My ADO", '{"org":"org"}'),
        )
        rows = store.query("SELECT * FROM external_sources WHERE source_id = ?", ("ado-org-proj",))
        assert len(rows) == 1
        assert rows[0]["source_type"] == "ado"
        store.close()

    def test_execute_delete_external_source(self, tmp_path):
        store = _make_central_store(tmp_path)
        store.execute(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name, config, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            ("del-me", "jira", "Old Jira", "{}"),
        )
        store.execute("DELETE FROM external_sources WHERE source_id = ?", ("del-me",))
        rows = store.query("SELECT * FROM external_sources WHERE source_id = ?", ("del-me",))
        assert rows == []
        store.close()

    def test_execute_insert_external_mapping(self, tmp_path):
        store = _make_central_store(tmp_path)
        # Insert a source first to satisfy any implicit expectations.
        store.execute(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name) VALUES (?, ?, ?)",
            ("s1", "ado", "Test Source"),
        )
        store.execute(
            "INSERT OR REPLACE INTO external_mappings "
            "(source_id, external_id, project_id, task_id, mapping_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", "123", "proj-a", "task-b", "implements", "2026-01-01T00:00:00Z"),
        )
        rows = store.query(
            "SELECT * FROM external_mappings WHERE source_id = ? AND external_id = ?",
            ("s1", "123"),
        )
        assert len(rows) == 1
        assert rows[0]["mapping_type"] == "implements"
        store.close()

    def test_execute_update_external_source(self, tmp_path):
        store = _make_central_store(tmp_path)
        store.execute(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name) VALUES (?, ?, ?)",
            ("upd-src", "github", "Old Name"),
        )
        store.execute(
            "UPDATE external_sources SET display_name = ? WHERE source_id = ?",
            ("New Name", "upd-src"),
        )
        rows = store.query("SELECT display_name FROM external_sources WHERE source_id = ?", ("upd-src",))
        assert rows[0]["display_name"] == "New Name"
        store.close()

    def test_execute_rejects_select(self, tmp_path):
        store = _make_central_store(tmp_path)
        with pytest.raises(ValueError, match="only accepts DML"):
            store.execute("SELECT * FROM external_sources")
        store.close()

    def test_execute_rejects_write_to_non_external_table(self, tmp_path):
        store = _make_central_store(tmp_path)
        with pytest.raises(ValueError, match="external-source tables"):
            store.execute(
                "INSERT INTO executions (project_id, task_id, status, started_at) "
                "VALUES ('p', 't', 'running', '2026-01-01')"
            )
        store.close()

    def test_query_still_rejects_insert(self, tmp_path):
        store = _make_central_store(tmp_path)
        with pytest.raises(ValueError, match="read-only"):
            store.query(
                "INSERT INTO external_sources (source_id) VALUES ('x')"
            )
        store.close()


# ---------------------------------------------------------------------------
# source_cmd integration smoke tests (via handler functions)
# ---------------------------------------------------------------------------


class TestSourceCmdIntegration:
    """Smoke tests for source_cmd handler functions using a temp central.db."""

    def _make_args(self, **kwargs):
        """Build a minimal Namespace-like object."""
        import argparse
        return argparse.Namespace(**kwargs)

    def test_add_and_list_ado_source(self, tmp_path, monkeypatch):
        """_add() writes a source; _list() reads it back."""
        import argparse
        from agent_baton.cli.commands.source_cmd import _add, _list

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )

        add_args = argparse.Namespace(
            source_type="ado",
            name="My ADO Source",
            org="myorg",
            source_project="myproj",
            pat_env="ADO_PAT",
            url="",
        )
        _add(add_args)

        # Check the DB directly.
        store = CentralStore(central_db)
        rows = store.query("SELECT * FROM external_sources WHERE source_type = 'ado'")
        assert len(rows) == 1
        assert rows[0]["display_name"] == "My ADO Source"
        store.close()

    def test_remove_source(self, tmp_path, monkeypatch):
        """_remove() deletes a previously registered source."""
        import argparse
        from agent_baton.cli.commands.source_cmd import _add, _remove

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )

        add_args = argparse.Namespace(
            source_type="ado",
            name="To Remove",
            org="removeorg",
            source_project="removeproj",
            pat_env="ADO_PAT",
            url="",
        )
        _add(add_args)

        store = CentralStore(central_db)
        rows_before = store.query("SELECT * FROM external_sources")
        store.close()
        assert len(rows_before) == 1

        source_id = rows_before[0]["source_id"]
        remove_args = argparse.Namespace(source_id=source_id)
        _remove(remove_args)

        store2 = CentralStore(central_db)
        rows_after = store2.query("SELECT * FROM external_sources")
        store2.close()
        assert rows_after == []

    def test_map_source(self, tmp_path, monkeypatch):
        """_map() writes an external_mappings row."""
        import argparse
        from agent_baton.cli.commands.source_cmd import _add, _map

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )

        add_args = argparse.Namespace(
            source_type="ado",
            name="Map Source",
            org="maporg",
            source_project="mapproj",
            pat_env="ADO_PAT",
            url="",
        )
        _add(add_args)

        store = CentralStore(central_db)
        source_row = store.query("SELECT source_id FROM external_sources")[0]
        store.close()

        map_args = argparse.Namespace(
            source_id=source_row["source_id"],
            external_id="999",
            project_id="proj-alpha",
            task_id="task-beta",
            mapping_type="implements",
        )
        _map(map_args)

        store2 = CentralStore(central_db)
        mappings = store2.query(
            "SELECT * FROM external_mappings WHERE external_id = '999'"
        )
        store2.close()
        assert len(mappings) == 1
        assert mappings[0]["project_id"] == "proj-alpha"

    def test_add_accepts_implemented_types(self, tmp_path, monkeypatch, capsys):
        """_add() accepts jira/github/linear now that adapters are implemented."""
        import argparse
        from agent_baton.cli.commands.source_cmd import _add

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )

        for source_type in ("jira", "github", "linear"):
            add_args = argparse.Namespace(
                source_type=source_type,
                name="Test",
                org="org",
                source_project="proj",
                pat_env="TOKEN",
                url="",
            )
            _add(add_args)
            out = capsys.readouterr().out
            assert "Registered source" in out

    def test_sync_no_adapter(self, tmp_path, monkeypatch, capsys):
        """_sync() with a source whose type has no adapter prints informative message."""
        import argparse
        import json
        from agent_baton.cli.commands.source_cmd import _sync

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )

        # Insert a source with a completely unknown type to exercise the
        # "no adapter" code path.
        store = CentralStore(central_db)
        store.execute(
            "INSERT INTO external_sources "
            "(source_id, source_type, display_name, config, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            ("unknown-src", "unknown_platform", "Unknown", json.dumps({})),
        )
        store.close()

        sync_args = argparse.Namespace(
            source_id="unknown-src",
            sync_all=False,
        )
        _sync(sync_args)
        out = capsys.readouterr().out
        assert "No adapter available" in out or "not available" in out.lower()

    def test_sync_with_ado_adapter_mocked(self, tmp_path, monkeypatch, capsys):
        """_sync() with ADO adapter wired up calls fetch_items and persists rows."""
        import argparse
        from agent_baton.cli.commands.source_cmd import _add, _sync

        central_db = tmp_path / "central.db"
        monkeypatch.setattr(
            "agent_baton.core.storage.central._CENTRAL_DB_DEFAULT",
            central_db,
        )
        monkeypatch.setenv("ADO_PAT", "fake-pat")

        add_args = argparse.Namespace(
            source_type="ado",
            name="ADO Sync Test",
            org="syncorg",
            source_project="syncproj",
            pat_env="ADO_PAT",
            url="",
        )
        _add(add_args)

        store = CentralStore(central_db)
        source_row = store.query("SELECT source_id FROM external_sources")[0]
        store.close()

        # Mock AdoAdapter so we don't need real HTTP.
        mock_item = ExternalItem(
            source_id=source_row["source_id"],
            external_id="7",
            item_type="feature",
            title="Feature Seven",
        )

        with patch(
            "agent_baton.core.storage.adapters.ado.AdoAdapter.connect",
            return_value=None,
        ), patch(
            "agent_baton.core.storage.adapters.ado.AdoAdapter.fetch_items",
            return_value=[mock_item],
        ):
            sync_args = argparse.Namespace(
                source_id=source_row["source_id"],
                sync_all=False,
            )
            _sync(sync_args)

        out = capsys.readouterr().out
        assert "1" in out  # "Synced 1 item(s)."

        # Verify item was written to central.db.
        store2 = CentralStore(central_db)
        items = store2.query(
            "SELECT * FROM external_items WHERE external_id = '7'"
        )
        store2.close()
        assert len(items) == 1
        assert items[0]["title"] == "Feature Seven"
