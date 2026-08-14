"""Caller-identity plumbing for the PMO gate approve/reject endpoints.

The PMO API is the UI path for recording a human approval decision on a
paused execution.  ``UserIdentityMiddleware`` already resolves the HTTP
caller into ``request.state.user_id`` (``X-Baton-User`` header, then
``Authorization: Bearer <token>``, then a local-mode fallback).  These
tests pin the contract that the resolved identity actually reaches the
execution engine:

* ``ExecutionEngine.record_approval_result`` must be called with
  ``actor=<the HTTP caller>`` and ``decision_source="api"`` — not left to
  default, which makes the engine self-populate ``$USER@$HOSTNAME`` of the
  **server process**.
* Consequently, in ``BATON_APPROVAL_MODE=team`` the engine's self-approval
  guard must compare the HTTP caller against the recorded requester, so a
  requester cannot approve their own gate through the API while a
  different reviewer can.
* The ``ApprovalResult`` persisted into execution state (and therefore
  into evidence bundles) must carry the human's identity, matching the
  ``approval_log`` row rather than contradicting it.

Everything here asserts observable behaviour: mock call kwargs, HTTP
status/body, and the persisted ``ExecutionState``.  Nothing inspects
source text.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import (  # noqa: E402
    get_bus,
    get_forge_session,
    get_pmo_scanner,
    get_pmo_store,
)
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.engine.executor import ExecutionEngine  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.core.storage import detect_backend, get_project_storage  # noqa: E402
from agent_baton.models.execution import (  # noqa: E402
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


TASK_ID = "gate-identity-001"
PROJECT_ID = "proj-gate-identity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubScanner:
    """Lightweight PmoScanner replacement returning a fixed card list."""

    def __init__(self, cards: list[PmoCard]) -> None:
        self._cards = cards

    def scan_all(self) -> list[PmoCard]:
        return list(self._cards)

    def program_health(self, cards=None):
        return {}

    def find_card(self, card_id: str):
        for c in self._cards:
            if c.card_id == card_id:
                return c, None
        raise KeyError(card_id)


def _awaiting_card(task_id: str = TASK_ID) -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=PROJECT_ID,
        program="GATE",
        title="Gate identity task",
        column="awaiting_human",
        risk_level="HIGH",
        priority=0,
        agents=["backend-engineer--python"],
        steps_completed=1,
        steps_total=1,
        gates_passed=0,
        current_phase="Implementation",
    )


def _plan(task_id: str = TASK_ID) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate identity plan",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement the feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
                approval_required=True,
            )
        ],
    )


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    return root


@pytest.fixture()
def registered_store(tmp_path: Path, project_root: Path) -> PmoStore:
    store = PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )
    store.register_project(
        PmoProject(
            project_id=PROJECT_ID,
            name="Gate Identity Project",
            path=str(project_root),
            program="GATE",
        )
    )
    return store


@pytest.fixture()
def client(tmp_path: Path, registered_store: PmoStore) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    app.dependency_overrides[get_pmo_store] = lambda: registered_store
    app.dependency_overrides[get_pmo_scanner] = lambda: _StubScanner([_awaiting_card()])
    app.dependency_overrides[get_forge_session] = lambda: MagicMock()
    app.dependency_overrides[get_bus] = lambda: EventBus()
    return TestClient(app)


def _reach_approval_pending(project_root: Path, requester: str) -> Path:
    """Drive a real execution to ``approval_pending`` inside ``project_root``.

    The pending-approval audit row is stamped with ``requester`` so the
    team-mode self-approval guard has something to compare against.

    Returns the ``.claude/team-context`` root the API route will use.
    """
    context_root = project_root / ".claude" / "team-context"
    context_root.mkdir(parents=True, exist_ok=True)

    # The engine stamps the requester from its CLI actor helper; force it to
    # a known value so the test does not depend on the OS user.
    with patch(
        "agent_baton.core.engine.executor._cli_actor", return_value=requester
    ):
        engine = ExecutionEngine(team_context_root=context_root, task_id=TASK_ID)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer--python")
        engine.next_action()  # consumes APPROVAL → status=approval_pending

    return context_root


def _load_state(context_root: Path):
    storage = get_project_storage(context_root, backend=detect_backend(context_root))
    return storage.load_execution(TASK_ID)


# ===========================================================================
# 1. The resolved HTTP caller is handed to the engine
# ===========================================================================


class TestCallerIdentityReachesEngine:
    def test_approve_passes_header_identity_as_actor(self, client: TestClient) -> None:
        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{TASK_ID}/approve",
                json={"phase_id": 1, "notes": "Looks good"},
                headers={"X-Baton-User": "carol"},
            )

        assert r.status_code == 200
        kwargs = mock_record.call_args.kwargs
        assert kwargs["phase_id"] == 1
        assert kwargs["result"] == "approve"
        assert kwargs["feedback"] == "Looks good"
        assert kwargs["actor"] == "carol"
        assert kwargs["decision_source"] == "api"

    def test_reject_passes_header_identity_as_actor(self, client: TestClient) -> None:
        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{TASK_ID}/reject",
                json={"phase_id": 1, "reason": "Quality gates not met"},
                headers={"X-Baton-User": "carol"},
            )

        assert r.status_code == 200
        kwargs = mock_record.call_args.kwargs
        assert kwargs["phase_id"] == 1
        assert kwargs["result"] == "reject"
        assert kwargs["feedback"] == "Quality gates not met"
        assert kwargs["actor"] == "carol"
        assert kwargs["decision_source"] == "api"

    def test_approve_identity_comes_from_middleware_not_server_process(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Bearer token resolves to an identity via the same middleware.

        The server process user must never leak into ``actor``.
        """
        monkeypatch.setenv("USER", "server-daemon")

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{TASK_ID}/approve",
                json={"phase_id": 1},
                headers={"Authorization": "Bearer dana"},
            )

        assert r.status_code == 200
        actor = mock_record.call_args.kwargs["actor"]
        assert actor == "dana"
        assert "server-daemon" not in actor


# ===========================================================================
# 2. End-to-end: team-mode separation of duties over HTTP
# ===========================================================================


class TestTeamModeSeparationOfDutiesOverHttp:
    def test_requester_cannot_self_approve_through_the_api(
        self,
        client: TestClient,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _reach_approval_pending(project_root, requester="alice")
        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")

        r = client.post(
            f"/api/v1/pmo/gates/{TASK_ID}/approve",
            json={"phase_id": 1, "notes": "lgtm"},
            headers={"X-Baton-User": "alice"},
        )

        assert r.status_code == 403, r.text
        body = r.json()
        assert body["reason"] == "self_approval_rejected"

    def test_different_reviewer_can_approve_and_is_persisted_as_actor(
        self,
        client: TestClient,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        context_root = _reach_approval_pending(project_root, requester="alice")
        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")
        # Unrelated OS user: the persisted actor must not come from here.
        monkeypatch.setenv("USER", "server-daemon")

        r = client.post(
            f"/api/v1/pmo/gates/{TASK_ID}/approve",
            json={"phase_id": 1, "notes": "reviewed"},
            headers={"X-Baton-User": "bob"},
        )

        assert r.status_code == 200, r.text

        state = _load_state(context_root)
        assert state is not None
        assert state.approval_results, "approval was not persisted"
        recorded = state.approval_results[-1]
        assert recorded.actor == "bob"
        assert "server-daemon" not in recorded.actor
        assert recorded.decision_source == "api"

    def test_local_mode_approval_still_persists_the_http_caller(
        self,
        client: TestClient,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        context_root = _reach_approval_pending(project_root, requester="alice")
        monkeypatch.setenv("BATON_APPROVAL_MODE", "local")
        monkeypatch.setenv("USER", "server-daemon")

        r = client.post(
            f"/api/v1/pmo/gates/{TASK_ID}/approve",
            json={"phase_id": 1},
            headers={"X-Baton-User": "alice"},
        )

        assert r.status_code == 200, r.text

        state = _load_state(context_root)
        assert state is not None
        recorded = state.approval_results[-1]
        assert recorded.actor == "alice"
        assert recorded.decision_source == "api"

    def test_reject_persists_the_http_caller_as_actor(
        self,
        client: TestClient,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        context_root = _reach_approval_pending(project_root, requester="alice")
        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")
        monkeypatch.setenv("USER", "server-daemon")

        r = client.post(
            f"/api/v1/pmo/gates/{TASK_ID}/reject",
            json={"phase_id": 1, "reason": "Coverage regression"},
            headers={"X-Baton-User": "bob"},
        )

        assert r.status_code == 200, r.text

        state = _load_state(context_root)
        assert state is not None
        assert state.approval_results, "rejection was not persisted"
        recorded = state.approval_results[-1]
        assert recorded.result == "reject"
        assert recorded.actor == "bob"
        assert "server-daemon" not in recorded.actor
        assert recorded.decision_source == "api"

    def test_requester_cannot_self_reject_through_the_api(
        self,
        client: TestClient,
        project_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _reach_approval_pending(project_root, requester="alice")
        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")

        r = client.post(
            f"/api/v1/pmo/gates/{TASK_ID}/reject",
            json={"phase_id": 1, "reason": "changed my mind"},
            headers={"X-Baton-User": "alice"},
        )

        assert r.status_code == 403, r.text
        assert r.json()["reason"] == "self_approval_rejected"
