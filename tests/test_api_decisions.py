"""Tests for decision management endpoints:

GET  /api/v1/decisions
GET  /api/v1/decisions/{request_id}
POST /api/v1/decisions/{request_id}/resolve
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.runtime.decisions import DecisionManager  # noqa: E402
from agent_baton.models.decision import DecisionRequest  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def app(tmp_root: Path):
    return create_app(team_context_root=tmp_root)


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def decision_manager(tmp_root: Path) -> DecisionManager:
    """Return a DecisionManager scoped to the same tmp directory as the app."""
    return DecisionManager(decisions_dir=tmp_root / "decisions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_decision(
    mgr: DecisionManager,
    task_id: str = "t1",
    decision_type: str = "gate_approval",
    summary: str = "Review phase 1",
) -> DecisionRequest:
    req = DecisionRequest.create(task_id=task_id, decision_type=decision_type, summary=summary)
    mgr.request(req)
    return req


# ===========================================================================
# GET /api/v1/decisions — list decisions
# ===========================================================================


class TestListDecisions:
    def test_empty_list_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions")
        assert r.status_code == 200

    def test_empty_list_count_is_zero(self, client: TestClient) -> None:
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == 0
        assert body["decisions"] == []

    def test_returns_created_decision(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager)
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == 1

    def test_count_matches_decisions_list_length(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="t1")
        _create_decision(decision_manager, task_id="t2")
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == len(body["decisions"])

    def test_status_pending_filter_returns_only_pending(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        _create_decision(decision_manager, task_id="t2")  # second pending
        # Resolve the first one.
        decision_manager.resolve(req.request_id, "approve")

        body = client.get("/api/v1/decisions?status=pending").json()
        for d in body["decisions"]:
            assert d["status"] == "pending"

    def test_status_resolved_filter_returns_only_resolved(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        _create_decision(decision_manager, task_id="t2")  # remains pending
        decision_manager.resolve(req.request_id, "approve")

        body = client.get("/api/v1/decisions?status=resolved").json()
        assert body["count"] == 1
        assert body["decisions"][0]["status"] == "resolved"

    def test_task_id_filter_returns_only_matching_task(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="task-a")
        _create_decision(decision_manager, task_id="task-b")

        body = client.get("/api/v1/decisions?task_id=task-a").json()
        assert body["count"] == 1
        assert body["decisions"][0]["task_id"] == "task-a"

    def test_task_id_filter_no_match_returns_empty(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="real-task")
        body = client.get("/api/v1/decisions?task_id=nonexistent-task").json()
        assert body["count"] == 0


# ===========================================================================
# GET /api/v1/decisions/{request_id}
# ===========================================================================


class TestGetDecision:
    def test_returns_200_for_existing_decision(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.get(f"/api/v1/decisions/{req.request_id}")
        assert r.status_code == 200

    def test_returns_correct_decision_fields(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager, task_id="t1", summary="Check this")
        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert body["request_id"] == req.request_id
        assert body["task_id"] == "t1"
        assert body["summary"] == "Check this"
        assert body["status"] == "pending"

    def test_returns_options_list(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert isinstance(body["options"], list)
        assert len(body["options"]) > 0

    def test_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions/no-such-id")
        assert r.status_code == 404

    def test_404_detail_mentions_request_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions/missing-req-id")
        assert "missing-req-id" in r.json()["detail"]


# ===========================================================================
# Path traversal guard on context_files (bd-90c4)
# ===========================================================================


class TestContextFilesPathTraversal:
    """Regression coverage for bd-90c4.

    The GET /decisions/{id} route enriches responses with the inline contents
    of every path listed in ``context_files``. Prior to bd-90c4 those reads
    were unconstrained, so any caller that could place an absolute or
    ``..``-prefixed path into the decision payload could read arbitrary
    files from the API host. The fix constrains reads to paths that resolve
    inside ``DecisionManager.safe_read_root`` (the team-context root).
    """

    def test_absolute_path_outside_root_is_silently_omitted(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "secret.txt"  # one level above safe_root
        outside.write_text("TOP-SECRET", encoding="utf-8")
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(outside)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert "TOP-SECRET" not in (body.get("context_file_contents") or {}).values()
        assert str(outside) not in (body.get("context_file_contents") or {})

    def test_etc_passwd_style_absolute_read_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=["/etc/passwd"],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "/etc/passwd" not in contents
        assert all("root:" not in v for v in contents.values())

    def test_dotdot_traversal_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "leak.txt"
        outside.write_text("LEAKED", encoding="utf-8")
        traversal = str(tmp_path / ".." / outside.name)
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[traversal],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "LEAKED" not in contents.values()

    def test_symlink_escaping_root_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "secret-target.txt"
        outside.write_text("CLASSIFIED", encoding="utf-8")
        link = tmp_path / "evil-symlink"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported on this platform")

        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(link)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "CLASSIFIED" not in contents.values()

    def test_path_inside_root_is_returned(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        # Sanity: legitimate reads inside the root still work.
        legit = tmp_path / "context.md"
        legit.write_text("hello world", encoding="utf-8")

        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(legit)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert contents.get(str(legit)) == "hello world"

    def test_manager_with_no_safe_root_refuses_all_reads(
        self, tmp_path: Path
    ) -> None:
        # If a caller forgets to wire safe_read_root, the route MUST refuse
        # every read rather than fall back to the old unconstrained behaviour.
        from agent_baton.api.routes.decisions import _safe_read_context_file

        legit = tmp_path / "in.txt"
        legit.write_text("inside", encoding="utf-8")

        assert _safe_read_context_file(str(legit), safe_root=None) is None


# ===========================================================================
# POST /api/v1/decisions/{request_id}/resolve
# ===========================================================================


class TestResolveDecision:
    def test_resolve_returns_200(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 200

    def test_resolve_returns_resolved_true(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        body = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        ).json()
        assert body["resolved"] is True

    def test_resolve_with_rationale_succeeds(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject", "rationale": "Not ready yet"},
        )
        assert r.status_code == 200

    def test_resolve_with_custom_resolved_by(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve", "resolved_by": "automated-policy"},
        )
        assert r.status_code == 200

    def test_resolve_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/decisions/no-such-id/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 404

    def test_resolve_already_resolved_returns_400(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        # First resolution.
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        # Second resolution — should be rejected.
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )
        assert r.status_code == 400

    def test_resolve_already_resolved_detail_mentions_status(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )
        assert "resolved" in r.json()["detail"]

    def test_resolve_updates_decision_status(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        updated = decision_manager.get(req.request_id)
        assert updated is not None
        assert updated.status == "resolved"

    def test_missing_option_returns_422(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={},
        )
        assert r.status_code == 422


# ===========================================================================
# POST /api/v1/decisions/{request_id}/resolve — atomic apply + resume
#
# Regression coverage for: "When the API records a response, atomically
# resume that same task with idempotency guards."  Resolving a decision
# whose request_id follows the deterministic scheme
# (task_id::kind::parts...) must apply the decision straight to the
# execution engine and launch a headless resume, not just flip the
# DecisionManager's on-disk status and publish an event nobody may be
# listening to.
# ===========================================================================


class TestResolveDecisionAppliesAndResumes:
    def _build_two_phase_execution_awaiting_approval(self, tmp_root: Path):
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        plan = MachinePlan(
            task_id="resume-task-1",
            task_summary="test resume",
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
        engine = ExecutionEngine(team_context_root=tmp_root, task_id=plan.task_id)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        # next_action() is what actually evaluates the completed phase's
        # approval_required flag and transitions status -> approval_pending.
        action = engine.next_action()
        assert action.action_type.value == "approval", action.action_type
        return plan

    def test_deterministic_approval_id_applies_to_engine_and_triggers_resume(
        self,
        client: TestClient,
        decision_manager: DecisionManager,
        tmp_root: Path,
        monkeypatch,
    ) -> None:
        from unittest.mock import MagicMock

        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.core.runtime.decisions import deterministic_decision_id
        from agent_baton.models.decision import DecisionRequest

        plan = self._build_two_phase_execution_awaiting_approval(tmp_root)

        request_id = deterministic_decision_id(plan.task_id, "approval", 1)
        decision_manager.request(DecisionRequest(
            request_id=request_id, task_id=plan.task_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        popen_calls: list = []

        def _fake_popen(cmd, **kwargs):
            popen_calls.append({"cmd": cmd, "kwargs": kwargs})
            return MagicMock(pid=12345)

        monkeypatch.setattr("subprocess.Popen", _fake_popen)

        r = client.post(
            f"/api/v1/decisions/{request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is True
        assert body["execution_resumed"] is True, body

        # The approval must actually have been applied to the engine.
        status = ExecutionEngine(team_context_root=tmp_root, task_id=plan.task_id).status()
        assert status["status"] != "approval_pending"

        resume_calls = [c for c in popen_calls if any("agent_baton" in str(a) for a in c["cmd"])]
        assert resume_calls, "expected a headless resume subprocess to be spawned"
        spawned_cmd = resume_calls[0]["cmd"]
        assert "--task-id" in spawned_cmd
        assert plan.task_id in spawned_cmd
        assert "execute" in spawned_cmd and "run" in spawned_cmd

    def test_resume_is_not_spawned_twice_when_worker_already_alive(
        self,
        client: TestClient,
        decision_manager: DecisionManager,
        tmp_root: Path,
        monkeypatch,
    ) -> None:
        """Idempotency guard: a live worker.pid must prevent a duplicate
        headless resume subprocess from being spawned."""
        import os
        from unittest.mock import MagicMock

        from agent_baton.core.runtime.decisions import deterministic_decision_id
        from agent_baton.models.decision import DecisionRequest

        plan = self._build_two_phase_execution_awaiting_approval(tmp_root)

        # Simulate a live worker for this task: a worker.pid pointing at
        # our own process (guaranteed to be "alive" for os.kill(pid, 0)).
        exec_dir = tmp_root / "executions" / plan.task_id
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "worker.pid").write_text(str(os.getpid()))

        request_id = deterministic_decision_id(plan.task_id, "approval", 1)
        decision_manager.request(DecisionRequest(
            request_id=request_id, task_id=plan.task_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        spawn_calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *a, **k: spawn_calls.append((a, k)) or MagicMock(pid=1),
        )

        r = client.post(
            f"/api/v1/decisions/{request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 200
        assert r.json()["execution_resumed"] is True
        resume_spawns = [
            call for call in spawn_calls
            if any("agent_baton" in str(arg) for arg in call[0][0])
        ]
        assert resume_spawns == [], (
            "must not spawn a second headless resume process for an "
            "already-alive worker"
        )

    def test_legacy_random_id_still_resolves_without_apply_or_resume(
        self, client: TestClient, decision_manager: DecisionManager,
    ) -> None:
        """A pre-existing random-UUID DecisionRequest (not the deterministic
        scheme) must still resolve successfully -- parse_decision_id()
        returning None is a graceful skip, not an error."""
        req = _create_decision(decision_manager, decision_type="gate_escalation")
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "retry"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is True
        assert body["execution_resumed"] is False


# ===========================================================================
# Decision resolve — idempotency of side effects
#
# Characterization tests for docs/internal/execution-runtime-contract.md §4
# ("idempotency semantics", DecisionManager.resolve() row) and §8
# (state-transition test matrix, stage 6 "Record decision"). The rejected
# second call must be a true no-op: it must not re-publish
# human_decision_resolved (downstream consumers — the async worker's poll
# loop, SSE, webhooks — must see the resolution exactly once).
# ===========================================================================


class TestDecisionResolveIdempotency:
    def test_double_resolve_emits_the_resolved_event_exactly_once(
        self, tmp_root: Path
    ) -> None:
        from agent_baton.core.events.bus import EventBus

        bus = EventBus()
        app = create_app(team_context_root=tmp_root, bus=bus)
        client = TestClient(app)
        dm = DecisionManager(decisions_dir=tmp_root / "decisions", bus=bus)
        req = _create_decision(dm)

        resolved_events: list[dict] = []
        bus.subscribe(
            "human.decision_resolved",
            lambda event: resolved_events.append(event.payload),
        )

        first = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        second = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )

        assert first.status_code == 200
        assert second.status_code == 400
        assert len(resolved_events) == 1
        # The winning resolution (the first call) is the one recorded.
        assert resolved_events[0]["chosen_option"] == "approve"

    def test_double_resolve_does_not_change_the_persisted_resolution(
        self, tmp_root: Path
    ) -> None:
        app = create_app(team_context_root=tmp_root)
        client = TestClient(app)
        dm = DecisionManager(decisions_dir=tmp_root / "decisions")
        req = _create_decision(dm)

        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )

        resolution = dm.get_resolution(req.request_id)
        assert resolution is not None
        assert resolution["chosen_option"] == "approve"


# ===========================================================================
# Duplicate approval submission against a real, engine-backed decision
#
# TestDecisionResolveIdempotency (above) proves the DecisionManager-level
# double-resolve guard in isolation. This class proves the guard holds for
# the actual production shape: a decision tied to a real ExecutionEngine
# via the deterministic request_id (§4's "apply + resume" path), where a
# duplicate submission racing behind the first must be rejected BEFORE the
# apply-to-engine / spawn-headless-resume side effects run a second time --
# not just before the DecisionManager resolution file is overwritten.
# ===========================================================================


class TestDuplicateApprovalSubmissionAgainstEngine:
    def _build_single_phase_execution_awaiting_approval(self, tmp_root: Path):
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        plan = MachinePlan(
            task_id="dup-approval-task",
            task_summary="duplicate approval submission test",
            phases=[
                PlanPhase(
                    phase_id=1, name="P1", approval_required=True,
                    steps=[PlanStep(step_id="1.1", agent_name="backend", task_description="x")],
                ),
            ],
        )
        engine = ExecutionEngine(team_context_root=tmp_root, task_id=plan.task_id)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        action = engine.next_action()
        assert action.action_type.value == "approval", action.action_type
        return plan

    def test_duplicate_submission_rejected_before_reapplying_to_engine_or_respawning(
        self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from unittest.mock import MagicMock

        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.core.events.bus import EventBus
        from agent_baton.core.runtime.decisions import deterministic_decision_id
        from agent_baton.models.decision import DecisionRequest

        bus = EventBus()
        app = create_app(team_context_root=tmp_root, bus=bus)
        client = TestClient(app)
        dm = DecisionManager(decisions_dir=tmp_root / "decisions", bus=bus)

        plan = self._build_single_phase_execution_awaiting_approval(tmp_root)
        request_id = deterministic_decision_id(plan.task_id, "approval", 1)
        dm.request(DecisionRequest(
            request_id=request_id, task_id=plan.task_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        resolved_events: list[dict] = []
        bus.subscribe(
            "human.decision_resolved",
            lambda event: resolved_events.append(event.payload),
        )

        popen_calls: list = []

        def _fake_popen(cmd, **kwargs):
            popen_calls.append({"cmd": cmd, "kwargs": kwargs})
            return MagicMock(pid=999)

        monkeypatch.setattr("subprocess.Popen", _fake_popen)

        # --- First submission (the real reviewer): applies to the engine
        # and spawns a headless resume. ---
        first = client.post(
            f"/api/v1/decisions/{request_id}/resolve",
            json={"option": "approve", "resolved_by": "reviewer-a"},
        )
        assert first.status_code == 200
        assert first.json()["execution_resumed"] is True

        engine = ExecutionEngine(team_context_root=tmp_root, task_id=plan.task_id)
        status_after_first = engine.status()
        assert status_after_first["status"] != "approval_pending"
        state_after_first = engine._load_execution()
        assert state_after_first is not None
        approvals_after_first = list(state_after_first.approval_results)
        assert len(approvals_after_first) == 1
        assert approvals_after_first[0].result == "approve"

        resume_spawns_after_first = [
            c for c in popen_calls if any("agent_baton" in str(a) for a in c["cmd"])
        ]
        assert len(resume_spawns_after_first) == 1

        # --- Second, duplicate submission (a racing second reviewer, or a
        # retried client request) with a DIFFERENT decision: must be
        # rejected outright. ---
        second = client.post(
            f"/api/v1/decisions/{request_id}/resolve",
            json={"option": "reject", "resolved_by": "reviewer-b"},
        )
        assert second.status_code == 400

        # The engine must not have been touched a second time: no new
        # ApprovalResult, no status regression, no second headless resume
        # subprocess spawned.
        state_after_second = engine._load_execution()
        assert state_after_second is not None
        assert state_after_second.approval_results == approvals_after_first

        resume_spawns_after_second = [
            c for c in popen_calls if any("agent_baton" in str(a) for a in c["cmd"])
        ]
        assert len(resume_spawns_after_second) == 1, (
            "duplicate submission must not spawn a second headless resume"
        )

        # The human_decision_resolved event fired exactly once, carrying
        # the WINNING (first) resolution.
        assert len(resolved_events) == 1
        assert resolved_events[0]["chosen_option"] == "approve"
        assert resolved_events[0]["resolved_by"] == "reviewer-a"
