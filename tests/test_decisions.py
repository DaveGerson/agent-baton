"""Tests for agent_baton.models.decision and agent_baton.core.runtime.decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.decision import DecisionRequest, DecisionResolution
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.models.events import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(
    request_id: str = "req-001",
    task_id: str = "t1",
    decision_type: str = "gate_approval",
    summary: str = "Review phase 1",
    options: list[str] | None = None,
) -> DecisionRequest:
    return DecisionRequest(
        request_id=request_id,
        task_id=task_id,
        decision_type=decision_type,
        summary=summary,
        options=options or ["approve", "reject"],
    )


# ===========================================================================
# DecisionRequest model
# ===========================================================================

class TestDecisionRequest:
    def test_required_fields(self) -> None:
        r = _req()
        assert r.request_id == "req-001"
        assert r.task_id == "t1"
        assert r.status == "pending"

    def test_created_at_auto_populates(self) -> None:
        r = _req()
        assert r.created_at  # non-empty

    def test_to_dict_roundtrip(self) -> None:
        r = _req(options=["a", "b", "c"])
        restored = DecisionRequest.from_dict(r.to_dict())
        assert restored.request_id == r.request_id
        assert restored.options == ["a", "b", "c"]
        assert restored.status == "pending"

    def test_from_dict_defaults(self) -> None:
        r = DecisionRequest.from_dict({"request_id": "x", "task_id": "t"})
        assert r.decision_type == ""
        assert r.options == []
        assert r.status == "pending"

    def test_factory_method(self) -> None:
        r = DecisionRequest.create(
            task_id="t1", decision_type="gate_approval", summary="check"
        )
        assert len(r.request_id) == 12
        assert r.options == ["approve", "reject"]  # default

    def test_factory_unique_ids(self) -> None:
        r1 = DecisionRequest.create("t1", "gate", "s1")
        r2 = DecisionRequest.create("t1", "gate", "s2")
        assert r1.request_id != r2.request_id

    def test_custom_options(self) -> None:
        r = DecisionRequest.create("t1", "plan_review", "s", options=["go", "stop", "modify"])
        assert r.options == ["go", "stop", "modify"]

    def test_deadline_optional(self) -> None:
        r = _req()
        assert r.deadline is None


# ===========================================================================
# DecisionResolution model
# ===========================================================================

class TestDecisionResolution:
    def test_required_fields(self) -> None:
        r = DecisionResolution(request_id="req-001", chosen_option="approve")
        assert r.chosen_option == "approve"
        assert r.resolved_by == "human"

    def test_resolved_at_auto_populates(self) -> None:
        r = DecisionResolution(request_id="r", chosen_option="ok")
        assert r.resolved_at  # non-empty

    def test_to_dict_roundtrip(self) -> None:
        r = DecisionResolution(
            request_id="r", chosen_option="reject",
            rationale="not ready", resolved_by="auto_policy",
        )
        restored = DecisionResolution.from_dict(r.to_dict())
        assert restored.chosen_option == "reject"
        assert restored.rationale == "not ready"
        assert restored.resolved_by == "auto_policy"

    def test_from_dict_defaults(self) -> None:
        r = DecisionResolution.from_dict({"request_id": "r", "chosen_option": "ok"})
        assert r.resolved_by == "human"
        assert r.rationale is None


# ===========================================================================
# DecisionManager — request()
# ===========================================================================

class TestDecisionManagerRequest:
    def test_creates_json_file(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        path = mgr.request(_req())
        assert path.exists()
        assert path.suffix == ".json"

    def test_creates_md_summary(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req())
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "Decision Required" in content
        assert "baton decide --resolve" in content

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path / "deep" / "decisions")
        path = mgr.request(_req())
        assert path.exists()

    def test_publishes_event_with_bus(self, tmp_path: Path) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("human.*", received.append)
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)
        mgr.request(_req())
        assert len(received) == 1
        assert received[0].topic == "human.decision_needed"
        assert received[0].payload["request_id"] == "req-001"

    def test_no_event_without_bus(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req())  # should not raise


# ===========================================================================
# DecisionManager — get()
# ===========================================================================

class TestDecisionManagerGet:
    def test_returns_request(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        r = mgr.get("r1")
        assert r is not None
        assert r.request_id == "r1"

    def test_returns_none_for_missing(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        assert mgr.get("nonexistent") is None


# ===========================================================================
# DecisionManager — pending() and list_all()
# ===========================================================================

class TestDecisionManagerListing:
    def test_pending_returns_only_pending(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.request(_req(request_id="r2"))
        mgr.resolve("r1", "approve")
        pending = mgr.pending()
        ids = {r.request_id for r in pending}
        assert ids == {"r2"}

    def test_list_all_includes_all(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.request(_req(request_id="r2"))
        mgr.resolve("r1", "approve")
        all_reqs = mgr.list_all()
        ids = {r.request_id for r in all_reqs}
        assert ids == {"r1", "r2"}

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        assert mgr.pending() == []
        assert mgr.list_all() == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path / "nonexistent")
        assert mgr.list_all() == []


# ===========================================================================
# DecisionManager — resolve()
# ===========================================================================

class TestDecisionManagerResolve:
    def test_resolve_updates_status(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        ok = mgr.resolve("r1", "approve")
        assert ok is True
        r = mgr.get("r1")
        assert r is not None
        assert r.status == "resolved"

    def test_resolve_writes_resolution_file(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.resolve("r1", "approve", rationale="LGTM")
        res_path = tmp_path / "r1-resolution.json"
        assert res_path.exists()
        data = json.loads(res_path.read_text())
        assert data["chosen_option"] == "approve"
        assert data["rationale"] == "LGTM"

    def test_resolve_publishes_event(self, tmp_path: Path) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("human.decision_resolved", received.append)
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)
        mgr.request(_req(request_id="r1"))
        mgr.resolve("r1", "approve")
        assert len(received) == 1
        assert received[0].payload["chosen_option"] == "approve"

    def test_resolve_missing_returns_false(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        assert mgr.resolve("nonexistent", "approve") is False

    def test_resolve_already_resolved_returns_false(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.resolve("r1", "approve")
        assert mgr.resolve("r1", "reject") is False

    def test_resolve_with_rationale(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.resolve("r1", "reject", rationale="needs rework")
        r = mgr.get("r1")
        assert r is not None
        assert r.status == "resolved"


# ===========================================================================
# Integration: full lifecycle
# ===========================================================================

class TestDecisionLifecycle:
    def test_request_pending_resolve(self, tmp_path: Path) -> None:
        bus = EventBus()
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)

        # Create
        req = DecisionRequest.create("task-1", "gate_approval", "Review PR")
        mgr.request(req)
        assert len(mgr.pending()) == 1

        # Check
        loaded = mgr.get(req.request_id)
        assert loaded is not None
        assert loaded.status == "pending"

        # Resolve
        mgr.resolve(req.request_id, "approve", rationale="LGTM")
        assert len(mgr.pending()) == 0
        assert len(mgr.list_all()) == 1

        # Verify events
        events = bus.replay("task-1")
        topics = [e.topic for e in events]
        assert "human.decision_needed" in topics
        assert "human.decision_resolved" in topics
