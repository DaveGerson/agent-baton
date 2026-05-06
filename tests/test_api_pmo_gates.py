"""HTTP-level tests for PMO gate approval endpoints.

Endpoints covered (all prefixed with /api/v1):

  GET  /pmo/gates/pending               — list executions awaiting gate approval
  POST /pmo/gates/{task_id}/approve     — approve a pending gate
  POST /pmo/gates/{task_id}/reject      — reject a pending gate

Strategy:
- PmoScanner is replaced with a stub that returns controlled PmoCard lists.
- PmoStore is replaced with a tmp-backed store so project lookups work.
- ExecutionEngine.record_approval_result is patched to avoid real filesystem
  state machine work while still verifying the call contract.
- The EventBus dependency is overridden so SSE publish calls do not raise.
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
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.scanner import PmoScanner  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep  # noqa: E402
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmp_store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _minimal_plan(task_id: str = "gate-task-001") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate test plan",
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


def _awaiting_card(
    task_id: str = "gate-task-001",
    project_id: str = "proj-gate",
) -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=project_id,
        program="GATE",
        title="Gate test task",
        column="awaiting_human",
        risk_level="LOW",
        priority=0,
        agents=["backend-engineer--python"],
        steps_completed=1,
        steps_total=1,
        gates_passed=0,
        current_phase="Implementation",
    )


def _executing_card(
    task_id: str = "running-task-001",
    project_id: str = "proj-gate",
) -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=project_id,
        program="GATE",
        title="Running task",
        column="executing",
        risk_level="LOW",
        priority=0,
        agents=["backend-engineer--python"],
    )


class _StubScanner:
    """Lightweight PmoScanner replacement that returns controlled card lists."""

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> PmoStore:
    return _make_tmp_store(tmp_path)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj-gate"
    root.mkdir()
    return root


@pytest.fixture()
def registered_store(store: PmoStore, project_root: Path) -> PmoStore:
    """Store with a registered project pointing at a real tmp directory."""
    store.register_project(
        PmoProject(
            project_id="proj-gate",
            name="Gate Project",
            path=str(project_root),
            program="GATE",
        )
    )
    return store


def _make_app(
    tmp_path: Path,
    store: PmoStore,
    cards: list[PmoCard],
) -> TestClient:
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
# GET /api/v1/pmo/gates/pending
# ===========================================================================


class TestListPendingGates:
    def test_returns_200_with_empty_list_when_no_awaiting_cards(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        client = _make_app(tmp_path, store, [_executing_card()])
        r = client.get("/api/v1/pmo/gates/pending")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_pending_entry_for_awaiting_human_card(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.get("/api/v1/pmo/gates/pending")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["task_id"] == card.card_id

    def test_pending_entry_has_required_fields(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        body = client.get("/api/v1/pmo/gates/pending").json()
        entry = body[0]
        for field in ("task_id", "project_id", "phase_id", "phase_name",
                      "approval_context", "approval_options", "task_summary",
                      "current_phase_name"):
            assert field in entry, f"missing field: {field}"

    def test_skips_non_awaiting_cards(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        awaiting = _awaiting_card(task_id="wait-001")
        running = _executing_card(task_id="run-001")
        client = _make_app(tmp_path, registered_store, [awaiting, running])
        body = client.get("/api/v1/pmo/gates/pending").json()
        assert len(body) == 1
        assert body[0]["task_id"] == "wait-001"

    def test_returns_stub_entry_when_project_path_unresolvable(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        # Store has no registered project for this card's project_id.
        card = _awaiting_card(project_id="unknown-proj")
        client = _make_app(tmp_path, store, [card])
        r = client.get("/api/v1/pmo/gates/pending")
        assert r.status_code == 200
        # Should return a stub entry rather than 500.
        body = r.json()
        assert len(body) == 1
        assert body[0]["task_id"] == card.card_id

    def test_multiple_pending_cards_all_returned(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        cards = [
            _awaiting_card(task_id="t1"),
            _awaiting_card(task_id="t2"),
            _awaiting_card(task_id="t3"),
        ]
        client = _make_app(tmp_path, registered_store, cards)
        body = client.get("/api/v1/pmo/gates/pending").json()
        assert len(body) == 3
        returned_ids = {e["task_id"] for e in body}
        assert returned_ids == {"t1", "t2", "t3"}


# ===========================================================================
# POST /api/v1/pmo/gates/{task_id}/approve
# ===========================================================================


class TestApproveGate:
    def test_returns_404_for_unknown_task(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/gates/no-such-task/approve",
            json={"phase_id": 1},
        )
        assert r.status_code == 404

    def test_returns_409_when_card_not_awaiting_human(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _executing_card(task_id="run-002")
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            "/api/v1/pmo/gates/run-002/approve",
            json={"phase_id": 1},
        )
        assert r.status_code == 409

    def test_returns_422_when_phase_id_missing(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/gates/{card.card_id}/approve",
            json={},  # phase_id required
        )
        assert r.status_code == 422

    def test_records_approve_via_engine(
        self, tmp_path: Path, registered_store: PmoStore, project_root: Path
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        # ExecutionEngine is imported inside the route function body, so we
        # patch it in the executor module where it is defined.
        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1, "notes": "Looks good"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "approve"
        assert body["recorded"] is True
        assert body["task_id"] == card.card_id
        assert body["phase_id"] == 1

        mock_record.assert_called_once_with(
            phase_id=1,
            result="approve",
            feedback="Looks good",
        )

    def test_approve_without_notes_uses_empty_string_feedback(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 2},
            )

        assert r.status_code == 200
        mock_record.assert_called_once_with(
            phase_id=2,
            result="approve",
            feedback="",
        )

    def test_returns_500_when_engine_raises_runtime_error(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=RuntimeError("state not found"),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 500
        assert "state not found" in r.json()["detail"]

    def test_404_when_project_path_unresolvable(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        # Card's project_id is not registered and not a real directory.
        card = _awaiting_card(project_id="ghost-proj")
        client = _make_app(tmp_path, store, [card])
        r = client.post(
            f"/api/v1/pmo/gates/{card.card_id}/approve",
            json={"phase_id": 1},
        )
        assert r.status_code == 404


# ===========================================================================
# POST /api/v1/pmo/gates/{task_id}/reject
# ===========================================================================


class TestRejectGate:
    def test_returns_404_for_unknown_task(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/gates/no-such-task/reject",
            json={"phase_id": 1, "reason": "Not ready"},
        )
        assert r.status_code == 404

    def test_returns_409_when_card_not_awaiting_human(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _executing_card(task_id="run-003")
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            "/api/v1/pmo/gates/run-003/reject",
            json={"phase_id": 1, "reason": "Not ready"},
        )
        assert r.status_code == 409

    def test_returns_422_when_reason_missing(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/gates/{card.card_id}/reject",
            json={"phase_id": 1},  # reason required
        )
        assert r.status_code == 422

    def test_returns_422_when_reason_is_empty_string(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        r = client.post(
            f"/api/v1/pmo/gates/{card.card_id}/reject",
            json={"phase_id": 1, "reason": ""},
        )
        assert r.status_code == 422

    def test_records_reject_via_engine(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Quality gates not met"},
            )

        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "reject"
        assert body["recorded"] is True
        assert body["task_id"] == card.card_id
        assert body["phase_id"] == 1

        mock_record.assert_called_once_with(
            phase_id=1,
            result="reject",
            feedback="Quality gates not met",
        )

    def test_returns_500_when_engine_raises_value_error(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=ValueError("invalid result"),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Bad output"},
            )

        assert r.status_code == 500

    def test_404_when_project_path_unresolvable(
        self, tmp_path: Path, store: PmoStore
    ) -> None:
        card = _awaiting_card(project_id="ghost-proj")
        client = _make_app(tmp_path, store, [card])
        r = client.post(
            f"/api/v1/pmo/gates/{card.card_id}/reject",
            json={"phase_id": 1, "reason": "Unresolvable project"},
        )
        assert r.status_code == 404

    def test_reason_is_passed_as_feedback_to_engine(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        """Verify the rejection reason becomes the feedback kwarg on the engine call."""
        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])
        rejection_reason = "Tests are failing in CI"

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result"
        ) as mock_record:
            client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 3, "reason": rejection_reason},
            )

        _, kwargs = mock_record.call_args
        assert kwargs.get("feedback") == rejection_reason


# ===========================================================================
# SSE topic inclusion
# ===========================================================================


class TestGateSseTopics:
    """gate.approved and gate.rejected must be in the SSE board topic set."""

    def test_gate_approved_in_pmo_board_topics(self) -> None:
        from agent_baton.api.routes.pmo import _PMO_BOARD_TOPICS

        assert "gate.approved" in _PMO_BOARD_TOPICS

    def test_gate_rejected_in_pmo_board_topics(self) -> None:
        from agent_baton.api.routes.pmo import _PMO_BOARD_TOPICS

        assert "gate.rejected" in _PMO_BOARD_TOPICS


# ===========================================================================
# InvalidApprovalState, ComplianceWriteError, ExecutionStateInconsistency
# ===========================================================================


class TestApprovalExceptionMapping:
    """Engine approval exceptions produce correct HTTP status codes and body shapes."""

    def test_not_approval_pending_approve_returns_409(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_NOT_PENDING,
                message="not pending",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 409
        body = r.json()
        assert body["error"] == "InvalidApprovalState"
        assert body["reason"] == "not_approval_pending"
        assert "message" in body
        assert "details" in body

    def test_phase_mismatch_approve_returns_409(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_PHASE_MISMATCH,
                message="phase mismatch",
                phase_id=2,
                current_status="approval_pending",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 2},
            )

        assert r.status_code == 409
        body = r.json()
        assert body["reason"] == "phase_mismatch"
        assert body["details"]["phase_id"] == 2
        assert body["details"]["current_status"] == "approval_pending"

    def test_no_approval_requested_approve_returns_409(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_NO_APPROVAL_REQUESTED,
                message="no approval requested",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 409
        body = r.json()
        assert body["reason"] == "no_approval_requested"

    def test_self_approval_rejected_approve_returns_403(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_SELF_APPROVAL,
                message="self approval rejected",
                actor="alice@example.com",
                requester="alice@example.com",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "InvalidApprovalState"
        assert body["reason"] == "self_approval_rejected"
        assert body["details"]["actor"] == "alice@example.com"
        assert body["details"]["requester"] == "alice@example.com"

    def test_not_approval_pending_reject_returns_409(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_NOT_PENDING,
                message="not pending",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Test"},
            )

        assert r.status_code == 409
        body = r.json()
        assert body["reason"] == "not_approval_pending"

    def test_self_approval_rejected_reject_returns_403(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_SELF_APPROVAL,
                message="self approval rejected",
                actor="bob@example.com",
                requester="bob@example.com",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Policy violation test"},
            )

        assert r.status_code == 403
        body = r.json()
        assert body["reason"] == "self_approval_rejected"

    def test_compliance_write_error_approve_returns_503(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import ComplianceWriteError

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=ComplianceWriteError(
                underlying=OSError("disk full"),
                log_path="/var/audit/log",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "ComplianceWriteError"
        assert body["reason"] == "ComplianceWriteError"
        assert body["details"]["log_path"] == "/var/audit/log"

    def test_compliance_write_error_reject_returns_503(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import ComplianceWriteError

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=ComplianceWriteError(
                underlying=OSError("io error"),
                log_path="",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Bad output"},
            )

        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "ComplianceWriteError"

    def test_execution_state_inconsistency_approve_returns_500(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import ExecutionStateInconsistency

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=ExecutionStateInconsistency(
                task_id="gate-task-001",
                context="approve-with-feedback",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 1},
            )

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "ExecutionStateInconsistency"
        assert body["reason"] == "ExecutionStateInconsistency"
        assert body["details"]["task_id"] == "gate-task-001"
        assert body["details"]["context"] == "approve-with-feedback"

    def test_execution_state_inconsistency_reject_returns_500(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        from agent_baton.core.engine.errors import ExecutionStateInconsistency

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=ExecutionStateInconsistency(
                task_id="t-002",
                context="reject",
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/reject",
                json={"phase_id": 1, "reason": "Bad output"},
            )

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "ExecutionStateInconsistency"

    def test_error_response_always_has_required_fields(
        self, tmp_path: Path, registered_store: PmoStore
    ) -> None:
        """Every approval error response includes error, reason, message, details."""
        from agent_baton.core.engine.errors import InvalidApprovalState

        card = _awaiting_card()
        client = _make_app(tmp_path, registered_store, [card])

        with patch(
            "agent_baton.core.engine.executor.ExecutionEngine.record_approval_result",
            side_effect=InvalidApprovalState(
                reason=InvalidApprovalState.REASON_PHASE_MISMATCH,
                message="phase mismatch",
                phase_id=5,
            ),
        ):
            r = client.post(
                f"/api/v1/pmo/gates/{card.card_id}/approve",
                json={"phase_id": 5},
            )

        body = r.json()
        for field in ("error", "reason", "message", "details"):
            assert field in body, f"Missing required field: {field}"
        assert isinstance(body["message"], str) and body["message"]
        assert isinstance(body["details"], dict)
