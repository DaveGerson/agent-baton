"""Tests for :class:`TeamBoard` — messaging + shared tasks over the Bead store.

Covers send/ack message flow, broadcast vs direct delivery, task
append/claim/complete lifecycle, and the ``BeadSelector.select_for_team_member``
integration path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.bead_selector import BeadSelector
from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.core.engine.team_board import TeamBoard
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


@pytest.fixture
def bead_store(tmp_path: Path) -> BeadStore:
    """BeadStore backed by a fresh SQLite DB with seeded executions row."""
    from agent_baton.core.storage.connection import ConnectionManager
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = mgr.get_connection()
    from datetime import datetime, timezone
    conn.execute(
        "INSERT OR IGNORE INTO executions (task_id, status, started_at) "
        "VALUES (?, 'running', ?)",
        ("task-board", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return BeadStore(db_path)


@pytest.fixture
def board(bead_store: BeadStore) -> TeamBoard:
    return TeamBoard(bead_store)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_direct_message_visible_to_recipient(
        self, board: TeamBoard
    ) -> None:
        msg_id = board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="heads up",
            body="I changed the API contract.",
        )
        assert msg_id

        unread = board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.worker",
        )
        assert len(unread) == 1
        assert unread[0].bead_id == msg_id

    def test_direct_message_not_visible_to_other_team_member(
        self, board: TeamBoard
    ) -> None:
        board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="s", body="b",
        )
        unread = board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.other",
        )
        assert unread == []

    def test_broadcast_reaches_all_members_of_target_team(
        self, board: TeamBoard
    ) -> None:
        board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member=None,
            subject="all hands", body="attn team b",
        )
        for mid in ("b.lead", "b.worker", "b.other"):
            unread = board.unread_messages_for_member(
                task_id="task-board", team_id="team-b", member_id=mid,
            )
            assert len(unread) == 1


class TestAckMessage:
    def test_ack_suppresses_re_delivery(self, board: TeamBoard) -> None:
        msg_id = board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="s", body="b",
        )
        unread = board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.worker",
        )
        assert len(unread) == 1

        board.ack_message(
            task_id="task-board",
            message_bead_id=msg_id, recipient_member_id="b.worker",
        )
        unread2 = board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.worker",
        )
        assert unread2 == []

    def test_ack_by_one_member_does_not_suppress_broadcast_for_another(
        self, board: TeamBoard
    ) -> None:
        msg_id = board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member=None,
            subject="s", body="all",
        )
        board.ack_message(
            task_id="task-board",
            message_bead_id=msg_id, recipient_member_id="b.worker",
        )
        # Worker no longer sees it.
        assert not board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.worker",
        )
        # But b.other still does.
        still = board.unread_messages_for_member(
            task_id="task-board", team_id="team-b", member_id="b.other",
        )
        assert len(still) == 1


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TestAppendTask:
    def test_open_task_visible_to_all_team_members(
        self, board: TeamBoard
    ) -> None:
        tid = board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead",
            title="investigate timeout", detail="users seeing 502",
        )
        for mid in ("a.lead", "a.worker", "a.other"):
            tasks = board.open_tasks_for_team(
                task_id="task-board", team_id="team-a", member_id=mid,
            )
            assert len(tasks) == 1
            assert tasks[0].bead_id == tid


class TestClaimTask:
    def test_claimed_task_visible_only_to_claimer(
        self, board: TeamBoard
    ) -> None:
        tid = board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="t",
        )
        board.claim_task(
            task_id="task-board", task_bead_id=tid, member_id="a.worker",
        )
        visible_to_claimer = board.open_tasks_for_team(
            task_id="task-board", team_id="team-a", member_id="a.worker",
        )
        visible_to_other = board.open_tasks_for_team(
            task_id="task-board", team_id="team-a", member_id="a.other",
        )
        assert len(visible_to_claimer) == 1
        assert visible_to_other == []

    def test_reclaim_replaces_previous_claim(self, board: TeamBoard) -> None:
        tid = board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="t",
        )
        board.claim_task(task_id="task-board", task_bead_id=tid, member_id="a.worker")
        board.claim_task(task_id="task-board", task_bead_id=tid, member_id="a.other")
        for_worker = board.open_tasks_for_team(
            task_id="task-board", team_id="team-a", member_id="a.worker",
        )
        for_other = board.open_tasks_for_team(
            task_id="task-board", team_id="team-a", member_id="a.other",
        )
        assert for_worker == []
        assert len(for_other) == 1


class TestCompleteTask:
    def test_completed_task_no_longer_open(self, board: TeamBoard) -> None:
        tid = board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="t",
        )
        board.complete_task(
            task_id="task-board", task_bead_id=tid, outcome="done",
        )
        tasks = board.open_tasks_for_team(
            task_id="task-board", team_id="team-a",
        )
        assert tasks == []


# ---------------------------------------------------------------------------
# BeadSelector.select_for_team_member
# ---------------------------------------------------------------------------


class TestSelectForTeamMember:
    def _plan(self) -> MachinePlan:
        return MachinePlan(
            task_id="task-board",
            task_summary="t",
            phases=[PlanPhase(
                phase_id=1, name="impl",
                steps=[PlanStep(
                    step_id="1.1", agent_name="team",
                    task_description="work",
                )],
            )],
        )

    def test_messages_and_tasks_appear_in_selection(
        self, board: TeamBoard, bead_store: BeadStore,
    ) -> None:
        board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="s", body="hi",
        )
        board.append_task(
            task_id="task-board", team_id="team-b",
            author_member_id="b.lead", title="build",
        )

        plan = self._plan()
        result = BeadSelector().select_for_team_member(
            bead_store, plan.phases[0].steps[0], plan,
            team_id="team-b", member_id="b.worker",
        )
        types = {b.bead_type for b in result}
        assert "message" in types
        assert "task" in types

    def test_acked_message_suppressed_in_selection(
        self, board: TeamBoard, bead_store: BeadStore,
    ) -> None:
        mid = board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="s", body="hi",
        )
        board.ack_message(
            task_id="task-board", message_bead_id=mid,
            recipient_member_id="b.worker",
        )
        plan = self._plan()
        result = BeadSelector().select_for_team_member(
            bead_store, plan.phases[0].steps[0], plan,
            team_id="team-b", member_id="b.worker",
        )
        assert all(b.bead_type != "message" for b in result)

    def test_base_select_unchanged_by_team_extension(
        self, bead_store: BeadStore,
    ) -> None:
        """BeadSelector.select() still works identically — no call-site change."""
        plan = self._plan()
        # No team-board beads, empty result set.
        result = BeadSelector().select(
            bead_store, plan.phases[0].steps[0], plan,
        )
        assert result == []
