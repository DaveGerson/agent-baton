"""Schema-shape regression for the G1.f PMO execution overlay.

We don't spin up a full FastAPI test client here — the overlay is best-
effort and gated behind try/except in the handler. This test pins the
response-model shape so a future refactor can't silently drop the
goal-loop fields the PMO UI depends on.
"""
from __future__ import annotations

from agent_baton.api.routes.pmo import _ExecutionDetailResponse, _GoalOverlay


def test_goal_overlay_defaults() -> None:
    overlay = _GoalOverlay()
    assert overlay.completion_condition is None
    assert overlay.goal_status == ""
    assert overlay.amend_cycles_used == 0
    assert overlay.max_amend_cycles == 0
    assert overlay.checks_count == 0
    assert overlay.last_check_met is None


def test_execution_detail_response_has_goal_and_turn_count() -> None:
    """The response model carries the G1.f overlay fields, with safe
    defaults so older backends don't break the UI."""
    resp = _ExecutionDetailResponse(
        task_id="t1",
        status="running",
        current_phase="Implement",
        steps=[],
        started_at="2026-05-12T00:00:00Z",
        elapsed_seconds=42.0,
    )
    assert resp.turn_count == 0
    assert resp.tokens_used_usd == 0.0
    assert isinstance(resp.goal, _GoalOverlay)
    assert resp.goal.completion_condition is None


def test_execution_detail_response_with_goal() -> None:
    resp = _ExecutionDetailResponse(
        task_id="t1",
        status="running",
        current_phase="Round-out 1",
        steps=[],
        started_at="2026-05-12T00:00:00Z",
        elapsed_seconds=42.0,
        turn_count=7,
        tokens_used_usd=4.23,
        goal=_GoalOverlay(
            completion_condition="all integration tests pass",
            goal_status="active",
            amend_cycles_used=1,
            max_amend_cycles=3,
            checks_count=2,
            last_check_met=False,
        ),
    )
    assert resp.turn_count == 7
    assert resp.tokens_used_usd == 4.23
    assert resp.goal.goal_status == "active"
    assert resp.goal.amend_cycles_used == 1
    assert resp.goal.last_check_met is False

    payload = resp.model_dump()
    assert "turn_count" in payload
    assert "tokens_used_usd" in payload
    assert payload["tokens_used_usd"] == 4.23
    assert payload["goal"]["completion_condition"] == "all integration tests pass"
    assert payload["goal"]["max_amend_cycles"] == 3
