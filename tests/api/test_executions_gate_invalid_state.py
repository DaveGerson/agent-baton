"""RW-2.4 — ``InvalidGateState`` must surface as a client error, not a 500.

``POST /api/v1/executions/{task_id}/gate`` wraps every ``RuntimeError`` from
``ExecutionEngine.record_gate_result`` into HTTP 500.  Since the F002 guards
landed, the three (soon five) rejected pre-conditions are all
``InvalidGateState`` — a ``RuntimeError`` subclass — so a caller that records
a gate against the wrong phase, or before the phase's steps have run, is told
the *server* broke.  It didn't: the request was invalid and is safe to
correct and retry.  A 500 also trips alerting and retry-with-backoff logic in
API clients, which is exactly wrong for a permanent client-side mistake.

These tests assert on the response only — status class and a machine-usable
body — never on how the mapping is implemented.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.engine.errors import InvalidGateState  # noqa: E402
from agent_baton.models.execution import (  # noqa: E402
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


def _plan(task_id: str) -> MachinePlan:
    """Two gated phases so both "wrong phase" and "steps un-run" are reachable."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate rejection mapping fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest -q"),
            ),
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review it",
                    ),
                ],
                gate=PlanGate(gate_type="review", command="baton review"),
            ),
        ],
    )


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    return TestClient(app, raise_server_exceptions=False)


def _start(client: TestClient, task_id: str) -> None:
    r = client.post("/api/v1/executions", json={"plan": _plan(task_id).to_dict()})
    assert r.status_code == 201, r.text


class TestGateRejectionIsAClientError:
    @pytest.mark.parametrize(
        ("label", "phase_id"),
        [
            ("unknown_phase", 99),
            ("phase_mismatch", 2),
            ("steps_incomplete", 1),
        ],
    )
    def test_rejected_gate_recording_returns_4xx(
        self, client: TestClient, label: str, phase_id: int
    ) -> None:
        """Each rejected pre-condition is a client error, not a server fault.

        ``unknown_phase``   — phase 99 is not in the plan.
        ``phase_mismatch``  — phase 2 is not current (phase 1 is).
        ``steps_incomplete`` — phase 1 is current but step 1.1 never ran.
        """
        task_id = f"gate-4xx-{label}"
        _start(client, task_id)

        r = client.post(
            f"/api/v1/executions/{task_id}/gate",
            json={"phase_id": phase_id, "result": "pass"},
        )

        assert 400 <= r.status_code < 500, (
            f"A gate recording rejected as {label!r} is a caller mistake and "
            f"must map to a 4xx; got {r.status_code}: {r.text}"
        )

    def test_rejection_body_names_the_offending_phase(
        self, client: TestClient
    ) -> None:
        """The caller has to be able to tell what to fix without a traceback."""
        task_id = "gate-4xx-detail"
        _start(client, task_id)

        r = client.post(
            f"/api/v1/executions/{task_id}/gate",
            json={"phase_id": 2, "result": "pass"},
        )

        assert 400 <= r.status_code < 500, r.text
        body = r.json()
        detail = str(body.get("detail", ""))
        assert detail, f"the rejection must carry a detail message: {body}"
        assert "2" in detail, (
            "the rejection must identify the phase that was rejected; got "
            f"{detail!r}"
        )

    def test_a_valid_gate_pass_is_still_accepted(self, client: TestClient) -> None:
        """Guard against the mapping being implemented by rejecting everything."""
        task_id = "gate-4xx-happy"
        _start(client, task_id)
        rec = client.post(
            f"/api/v1/executions/{task_id}/record",
            json={"step_id": "1.1", "agent": "backend-engineer", "status": "complete"},
        )
        assert rec.status_code == 200, rec.text

        r = client.post(
            f"/api/v1/executions/{task_id}/gate",
            json={"phase_id": 1, "result": "pass"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["recorded"] is True

    def test_reason_tags_are_all_mapped_below_500(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``InvalidGateState`` reason may fall through to the 500 arm.

        Enumerates the class's own ``REASON_*`` surface so guards added later
        (the approval-pending and gateless-phase reasons) cannot quietly land
        back on 500 for want of a mapping entry.  The engine call is replaced
        with a raise so every tag reaches the endpoint's error handling, not
        only the ones a plan fixture can provoke.
        """
        from agent_baton.core.engine.executor import ExecutionEngine

        reasons = {
            value
            for name, value in vars(InvalidGateState).items()
            if name.startswith("REASON_") and isinstance(value, str)
        }
        assert reasons, "InvalidGateState must expose REASON_* tags"

        unmapped: list[str] = []
        for reason in sorted(reasons):
            task_id = f"gate-4xx-map-{reason}"
            _start(client, task_id)

            def _raise(self, *args, _reason=reason, **kwargs):
                raise InvalidGateState(
                    reason=_reason,
                    message=f"forced {_reason} for phase 1",
                    phase_id=1,
                    current_phase_id=1,
                )

            monkeypatch.setattr(
                ExecutionEngine, "record_gate_result", _raise, raising=True
            )
            r = client.post(
                f"/api/v1/executions/{task_id}/gate",
                json={"phase_id": 1, "result": "pass"},
            )
            monkeypatch.undo()

            if not (400 <= r.status_code < 500):
                unmapped.append(f"{reason} -> {r.status_code}")

        assert unmapped == [], (
            "every InvalidGateState reason must map to a 4xx; unmapped: "
            f"{unmapped}"
        )
