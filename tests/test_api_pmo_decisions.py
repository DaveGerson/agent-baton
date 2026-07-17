"""HTTP-level tests for the PMO per-card decision inbox.

Endpoints covered (all prefixed with /api/v1):

  GET  /pmo/execute/{card_id}/decisions
  POST /pmo/execute/{card_id}/decisions/{request_id}/resolve

These are the PMO-scoped counterpart of the generic /decisions API
(api/routes/decisions.py, which is bound to a single team_context_root at
server startup and therefore cannot see a per-card project root chosen at
request time).  Regression coverage for the "PMO paused-task resume path"
and "idempotent decision and event handling" deliverables.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import get_bus, get_forge_session, get_pmo_scanner, get_pmo_store  # noqa: E402
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.engine.executor import ExecutionEngine  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.core.runtime.decisions import DecisionManager, deterministic_decision_id  # noqa: E402
from agent_baton.models.decision import DecisionRequest  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep  # noqa: E402
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _awaiting_card(task_id: str = "pmo-decision-task", project_id: str = "proj-dec") -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=project_id,
        program="DEC",
        title="Decision test task",
        column="awaiting_human",
        risk_level="LOW",
        priority=0,
        agents=["backend-engineer--python"],
        steps_completed=1,
        steps_total=2,
        gates_passed=0,
        current_phase="Implementation",
    )


class _StubScanner:
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


@pytest.fixture()
def store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj-dec"
    root.mkdir()
    return root


@pytest.fixture()
def registered_store(store: PmoStore, project_root: Path) -> PmoStore:
    store.register_project(
        PmoProject(
            project_id="proj-dec", name="Decision Project",
            path=str(project_root), program="DEC",
        )
    )
    return store


def _context_root(project_root: Path) -> Path:
    return project_root / ".claude" / "team-context"


def _seed_awaiting_approval_execution(project_root: Path, task_id: str) -> MachinePlan:
    """Start a two-phase execution and drive it to approval_pending for
    phase 1, mirroring a headless ``baton execute run`` subprocess that
    already recorded step 1.1 and then paused."""
    plan = MachinePlan(
        task_id=task_id,
        task_summary="Decision test plan",
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
    context_root = _context_root(project_root)
    engine = ExecutionEngine(team_context_root=context_root, task_id=task_id)
    engine.start(plan)
    engine.record_step_result("1.1", "backend", status="complete")
    action = engine.next_action()
    assert action.action_type.value == "approval", action.action_type
    return plan


def _make_app(tmp_path: Path, store: PmoStore, cards: list[PmoCard]) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    scanner = _StubScanner(cards)
    forge_stub = MagicMock()
    bus = EventBus()
    app.dependency_overrides[get_pmo_store] = lambda: store
    app.dependency_overrides[get_pmo_scanner] = lambda: scanner
    app.dependency_overrides[get_forge_session] = lambda: forge_stub
    app.dependency_overrides[get_bus] = lambda: bus
    return TestClient(app)


# ===========================================================================
# GET /api/v1/pmo/execute/{card_id}/decisions
# ===========================================================================


class TestListCardDecisions:
    def test_returns_404_for_unknown_card(self, tmp_path: Path, store: PmoStore) -> None:
        client = _make_app(tmp_path, store, [])
        r = client.get("/api/v1/pmo/execute/no-such-card/decisions")
        assert r.status_code == 404

    def test_returns_empty_list_when_no_decisions_recorded(
        self, tmp_path: Path, registered_store: PmoStore,
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.get(f"/api/v1/pmo/execute/{card.card_id}/decisions")
        assert r.status_code == 200
        assert r.json()["decisions"] == []

    def test_returns_pending_decision_recorded_by_headless_run(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path,
    ) -> None:
        card = _awaiting_card()
        dm = DecisionManager(decisions_dir=_context_root(project_root) / "decisions")
        request_id = deterministic_decision_id(card.card_id, "approval", 1)
        dm.request(DecisionRequest(
            request_id=request_id, task_id=card.card_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        client = _make_app(tmp_path, registered_store, [card])
        body = client.get(f"/api/v1/pmo/execute/{card.card_id}/decisions").json()
        assert body["count"] == 1
        assert body["decisions"][0]["request_id"] == request_id
        assert body["decisions"][0]["status"] == "pending"

    def test_excludes_decisions_for_a_different_task(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path,
    ) -> None:
        card = _awaiting_card()
        dm = DecisionManager(decisions_dir=_context_root(project_root) / "decisions")
        dm.request(DecisionRequest(
            request_id=deterministic_decision_id("other-task", "approval", 1),
            task_id="other-task", decision_type="phase_approval",
            summary="unrelated", options=["approve", "reject"],
        ))
        client = _make_app(tmp_path, registered_store, [card])
        body = client.get(f"/api/v1/pmo/execute/{card.card_id}/decisions").json()
        assert body["decisions"] == []


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/decisions/{request_id}/resolve
# ===========================================================================


class TestResolveCardDecision:
    def test_returns_404_for_unknown_decision(
        self, tmp_path: Path, registered_store: PmoStore,
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/execute/{card.card_id}/decisions/no-such-id/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 404

    def test_returns_404_when_decision_belongs_to_a_different_card(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path,
    ) -> None:
        card = _awaiting_card()
        dm = DecisionManager(decisions_dir=_context_root(project_root) / "decisions")
        other_request_id = deterministic_decision_id("other-task", "approval", 1)
        dm.request(DecisionRequest(
            request_id=other_request_id, task_id="other-task",
            decision_type="phase_approval", summary="unrelated",
            options=["approve", "reject"],
        ))
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/execute/{card.card_id}/decisions/{other_request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 404

    def test_resolve_applies_to_engine_and_triggers_resume(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path, monkeypatch,
    ) -> None:
        card = _awaiting_card()
        plan = _seed_awaiting_approval_execution(project_root, card.card_id)

        request_id = deterministic_decision_id(card.card_id, "approval", 1)
        dm = DecisionManager(decisions_dir=_context_root(project_root) / "decisions")
        dm.request(DecisionRequest(
            request_id=request_id, task_id=card.card_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        popen_calls: list = []

        def _fake_popen(cmd, **kwargs):
            popen_calls.append({"cmd": cmd, "kwargs": kwargs})
            return MagicMock(pid=999)

        monkeypatch.setattr("subprocess.Popen", _fake_popen)

        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/execute/{card.card_id}/decisions/{request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is True
        assert body["execution_resumed"] is True, body

        status = ExecutionEngine(
            team_context_root=_context_root(project_root), task_id=card.card_id,
        ).status()
        assert status["status"] != "approval_pending"

        resume_calls = [c for c in popen_calls if any("agent_baton" in str(a) for a in c["cmd"])]
        assert resume_calls, "expected a headless resume subprocess for this card"
        assert str(project_root) == resume_calls[0]["kwargs"].get("cwd")

    def test_resolve_is_idempotent_second_call_returns_400(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path,
    ) -> None:
        card = _awaiting_card()
        _seed_awaiting_approval_execution(project_root, card.card_id)
        request_id = deterministic_decision_id(card.card_id, "approval", 1)
        dm = DecisionManager(decisions_dir=_context_root(project_root) / "decisions")
        dm.request(DecisionRequest(
            request_id=request_id, task_id=card.card_id,
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))

        client = _make_app(tmp_path, registered_store, [card])
        first = client.post(
            f"/api/v1/pmo/execute/{card.card_id}/decisions/{request_id}/resolve",
            json={"option": "approve"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/pmo/execute/{card.card_id}/decisions/{request_id}/resolve",
            json={"option": "reject"},
        )
        assert second.status_code == 400
