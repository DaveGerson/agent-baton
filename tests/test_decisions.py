"""Tests for agent_baton.models.decision and agent_baton.core.runtime.decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.decision import DecisionRequest, DecisionResolution
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.decisions import (
    DecisionManager,
    apply_decision_resolution,
    deterministic_decision_id,
    parse_decision_id,
    resume_task_headless,
)
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
# DECISION: Removed test_required_fields (trivial field storage) and
# test_deadline_optional (trivial None-default check).
# Kept: roundtrip, from_dict_defaults (missing-key edge case), factory tests
# (factory_method and factory_unique_ids test non-trivial ID generation),
# and created_at auto-populate (non-obvious side-effect on construction).
# ===========================================================================

class TestDecisionRequest:
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


# ===========================================================================
# DecisionResolution model
# DECISION: Removed test_required_fields (trivial field storage).
# Kept: roundtrip, from_dict_defaults (missing-key edge case), and
# resolved_at auto-populate (non-obvious side-effect).
# ===========================================================================

class TestDecisionResolution:
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
# DECISION: Merged test_empty_dir_returns_empty and test_missing_dir_returns_empty
# into a single parametrized test. Listing behaviour tests kept separate
# because they require state setup (request + resolve).
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

    @pytest.mark.parametrize("use_nonexistent", [False, True])
    def test_empty_listing(self, tmp_path: Path, use_nonexistent: bool) -> None:
        decisions_dir = tmp_path / "nonexistent" if use_nonexistent else tmp_path
        mgr = DecisionManager(decisions_dir=decisions_dir)
        assert mgr.pending() == []
        assert mgr.list_all() == []


# ===========================================================================
# DecisionManager — resolve()
# DECISION: Merged test_resolve_updates_status and test_resolve_with_rationale
# (both test that get() returns a resolved request, just with different extra
# fields checked — unified into test_resolve_updates_status which now also
# verifies the status). test_resolve_missing_returns_false and
# test_resolve_already_resolved_returns_false merged into parametrized test.
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

    @pytest.mark.parametrize("setup,resolve_id,expected", [
        # missing request
        (False, "nonexistent", False),
        # already resolved
        (True, "r1", False),
    ])
    def test_resolve_returns_false_on_failure(
        self, tmp_path: Path, setup: bool, resolve_id: str, expected: bool
    ) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        if setup:
            mgr.request(_req(request_id="r1"))
            mgr.resolve("r1", "approve")
        assert mgr.resolve(resolve_id, "reject") is expected


# ===========================================================================
# DecisionManager — get_resolution()  (TODO-1 fix verification)
# ===========================================================================

class TestDecisionManagerGetResolution:
    """Verify get_resolution() is public and returns the correct resolution data."""

    def test_returns_none_before_resolution(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        assert mgr.get_resolution("r1") is None

    def test_returns_resolution_dict_after_resolve(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r1"))
        mgr.resolve("r1", "approve", rationale="LGTM")
        data = mgr.get_resolution("r1")
        assert data is not None
        assert data["chosen_option"] == "approve"
        assert data["rationale"] == "LGTM"

    def test_returns_none_for_unknown_request(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        assert mgr.get_resolution("nonexistent") is None

    def test_reject_option_preserved(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request(_req(request_id="r2"))
        mgr.resolve("r2", "reject")
        data = mgr.get_resolution("r2")
        assert data is not None
        assert data["chosen_option"] == "reject"


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


# ===========================================================================
# deterministic_decision_id / parse_decision_id
#
# Regression coverage for the "one shared lifecycle" contract: CLI, daemon,
# and the REST API must converge on the same durable decision record for the
# same logical human decision, and the REST API must be able to recover
# (task_id, kind, parts) from a request_id alone (no structured field exists
# on DecisionRequest for this).
# ===========================================================================

class TestDeterministicDecisionId:
    def test_round_trips_task_kind_and_parts(self) -> None:
        request_id = deterministic_decision_id("task-1", "approval", 3)
        assert parse_decision_id(request_id) == ("task-1", "approval", ["3"])

    def test_multiple_parts_round_trip(self) -> None:
        request_id = deterministic_decision_id("task-1", "feedback", 2, "q1")
        assert parse_decision_id(request_id) == ("task-1", "feedback", ["2", "q1"])

    def test_no_parts_round_trips(self) -> None:
        request_id = deterministic_decision_id("task-1", "interact")
        assert parse_decision_id(request_id) == ("task-1", "interact", [])

    def test_same_inputs_produce_the_same_id(self) -> None:
        assert deterministic_decision_id("t", "gate", 1) == deterministic_decision_id("t", "gate", 1)

    def test_different_phase_ids_produce_different_ids(self) -> None:
        assert deterministic_decision_id("t", "gate", 1) != deterministic_decision_id("t", "gate", 2)

    def test_legacy_random_uuid_id_is_not_parseable(self) -> None:
        legacy = DecisionRequest.create("t1", "gate_approval", "review").request_id
        assert parse_decision_id(legacy) is None

    def test_empty_string_is_not_parseable(self) -> None:
        assert parse_decision_id("") is None


# ===========================================================================
# apply_decision_resolution / resume_task_headless
# ===========================================================================

class TestApplyDecisionResolution:
    def _plan(self, task_id: str):
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
        return MachinePlan(
            task_id=task_id, task_summary="test",
            phases=[
                PlanPhase(
                    phase_id=1, name="P1", approval_required=True,
                    steps=[PlanStep(step_id="1.1", agent_name="backend", task_description="x")],
                ),
                PlanPhase(
                    phase_id=2, name="P2",
                    steps=[PlanStep(step_id="2.1", agent_name="backend", task_description="y")],
                ),
            ],
        )

    def test_applies_approval_to_engine(self, tmp_path: Path) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine

        plan = self._plan("apply-task-1")
        engine = ExecutionEngine(team_context_root=tmp_path, task_id=plan.task_id)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        assert engine.next_action().action_type.value == "approval"

        applied = apply_decision_resolution(
            team_context_root=tmp_path, task_id=plan.task_id, kind="approval",
            parts=["1"], chosen_option="approve",
        )
        assert applied is True

        status = ExecutionEngine(team_context_root=tmp_path, task_id=plan.task_id).status()
        assert status["status"] != "approval_pending"

    def test_unknown_kind_returns_false(self, tmp_path: Path) -> None:
        plan = self._plan("apply-task-2")
        from agent_baton.core.engine.executor import ExecutionEngine
        ExecutionEngine(team_context_root=tmp_path, task_id=plan.task_id).start(plan)

        applied = apply_decision_resolution(
            team_context_root=tmp_path, task_id=plan.task_id, kind="not-a-real-kind",
            parts=[], chosen_option="approve",
        )
        assert applied is False

    def test_reapplying_an_already_applied_approval_is_a_no_op_not_an_error(
        self, tmp_path: Path,
    ) -> None:
        """Idempotency: a second apply for a decision already applied (e.g.
        by a live daemon worker) must return False, not raise."""
        from agent_baton.core.engine.executor import ExecutionEngine

        plan = self._plan("apply-task-3")
        engine = ExecutionEngine(team_context_root=tmp_path, task_id=plan.task_id)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        engine.next_action()
        engine.record_approval_result(phase_id=1, result="approve")

        # The phase is no longer awaiting approval -- a second attempt to
        # apply the same decision must not raise.
        applied_again = apply_decision_resolution(
            team_context_root=tmp_path, task_id=plan.task_id, kind="approval",
            parts=["1"], chosen_option="approve",
        )
        assert applied_again is False


class TestResumeTaskHeadless:
    def test_spawns_headless_runner_when_no_worker_pid(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from unittest.mock import MagicMock

        calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: calls.append((cmd, kw)) or MagicMock(pid=1),
        )
        result = resume_task_headless(team_context_root=tmp_path, task_id="resume-me")
        assert result is True
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert "--task-id" in cmd and "resume-me" in cmd
        assert kwargs.get("cwd") == str(tmp_path.parent.parent)

    def test_does_not_spawn_when_worker_pid_is_alive(self, tmp_path: Path, monkeypatch) -> None:
        import os
        from unittest.mock import MagicMock

        exec_dir = tmp_path / "executions" / "resume-me-2"
        exec_dir.mkdir(parents=True)
        (exec_dir / "worker.pid").write_text(str(os.getpid()))

        calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: calls.append((cmd, kw)) or MagicMock(pid=1),
        )
        result = resume_task_headless(team_context_root=tmp_path, task_id="resume-me-2")
        assert result is True
        assert calls == []

    def test_spawns_when_worker_pid_is_stale(self, tmp_path: Path, monkeypatch) -> None:
        from unittest.mock import MagicMock

        # A PID that is very unlikely to be alive.
        exec_dir = tmp_path / "executions" / "resume-me-3"
        exec_dir.mkdir(parents=True)
        (exec_dir / "worker.pid").write_text("999999999")

        calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kw: calls.append((cmd, kw)) or MagicMock(pid=1),
        )
        result = resume_task_headless(team_context_root=tmp_path, task_id="resume-me-3")
        assert result is True
        assert len(calls) == 1
