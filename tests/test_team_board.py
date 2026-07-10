"""Tests for :class:`TeamBoard` — messaging + shared tasks over the Bead store.

Covers send/ack message flow, broadcast vs direct delivery, task
append/claim/complete lifecycle, and the ``BeadSelector.select_for_team_member``
integration path.

ADR-13b WP-G: BeadStore (SQLite) removed; uses BdBeadStore via make_bead_store().
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.bead_selector import BeadSelector
from agent_baton.core.engine.team_board import TeamBoard, TeamBoardConflictError
from agent_baton.models.bead import Bead
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.utils.time import utcnow_zulu as _utcnow


@pytest.fixture
def bead_store(tmp_path: Path):
    """BdBeadStore backed by bd for testing TeamBoard messaging + tasks."""
    from agent_baton.core.engine.bead_backend import make_bead_store
    db_path = tmp_path / "baton.db"
    db_path.touch()
    return make_bead_store(db_path, repo_root=tmp_path)


@pytest.fixture
def board(bead_store) -> TeamBoard:
    return TeamBoard(bead_store)


# ---------------------------------------------------------------------------
# Hermetic in-memory bead store — for TeamBoard behavior that does not need
# the real `bd`-backed store, so these tests run without the external `bd`
# binary (per tests/CLAUDE.md's hermeticity requirement; mirrors the
# established pattern in tests/test_team_tools.py's ``_FakeBeadStore``).
# ---------------------------------------------------------------------------


class _FakeBeadStore:
    def __init__(self) -> None:
        self._beads: dict[str, Bead] = {}

    def write(self, bead: Bead) -> str:
        self._beads[bead.bead_id] = bead
        return bead.bead_id

    def read(self, bead_id: str) -> Bead | None:
        return self._beads.get(bead_id)

    def close(self, bead_id: str, summary: str) -> None:
        bead = self._beads.get(bead_id)
        if bead is None:
            return
        bead.status = "closed"
        bead.closed_at = _utcnow()

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_name: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Bead]:
        out: list[Bead] = []
        for bead in self._beads.values():
            if task_id is not None and bead.task_id != task_id:
                continue
            if agent_name is not None and bead.agent_name != agent_name:
                continue
            if bead_type is not None and bead.bead_type != bead_type:
                continue
            if status is not None and bead.status != status:
                continue
            if tags and not set(tags).issubset(set(bead.tags or [])):
                continue
            out.append(bead)
        out.sort(key=lambda b: b.created_at, reverse=True)
        return out[:limit]


@pytest.fixture
def fake_board() -> TeamBoard:
    return TeamBoard(_FakeBeadStore())


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
    # BEAD_WARNING: BdBeadStore.query() cannot retrieve closed beads when
    # label/type filters are applied (bd list --label X omits closed issues).
    # _acked_message_ids queries message_ack beads which have status=closed, so
    # acks are never found and acked messages always reappear.  These tests are
    # xfail until BdBeadStore.query() is updated to pass --status=all when no
    # status is given.

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
        self, board: TeamBoard, bead_store,
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
        self, board: TeamBoard, bead_store,
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
        self, bead_store,
    ) -> None:
        """BeadSelector.select() still works identically — no call-site change."""
        plan = self._plan()
        # No team-board beads, empty result set.
        result = BeadSelector().select(
            bead_store, plan.phases[0].steps[0], plan,
        )
        assert result == []


# ---------------------------------------------------------------------------
# Idempotent task creation, malformed claim targets — hermetic (_FakeBeadStore)
# ---------------------------------------------------------------------------


class TestIdempotentTaskCreate:
    def test_repeated_create_with_same_key_returns_original_bead_id(
        self, fake_board: TeamBoard,
    ) -> None:
        first = fake_board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="t", idempotency_key="retry-1",
        )
        second = fake_board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="t (retried wording)",
            idempotency_key="retry-1",
        )
        assert first == second
        # Only one task actually persisted.
        tasks = fake_board.open_tasks_for_team(task_id="task-board", team_id="team-a")
        assert len(tasks) == 1

    def test_idempotency_key_scoped_to_team(
        self, fake_board: TeamBoard,
    ) -> None:
        """The same idempotency_key under a DIFFERENT team_id creates a
        distinct task — scoping is (team_id, idempotency_key), not the key
        alone. Titles differ too so the (deterministic, content-hashed)
        bead id can't coincidentally collide and mask the scoping bug."""
        a_id = fake_board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="team a's task",
            idempotency_key="shared-key",
        )
        b_id = fake_board.append_task(
            task_id="task-board", team_id="team-b",
            author_member_id="b.lead", title="team b's task",
            idempotency_key="shared-key",
        )
        assert a_id != b_id
        assert len(fake_board.open_tasks_for_team(task_id="task-board", team_id="team-a")) == 1
        assert len(fake_board.open_tasks_for_team(task_id="task-board", team_id="team-b")) == 1

    def test_no_idempotency_key_always_creates_new_task(
        self, fake_board: TeamBoard,
    ) -> None:
        fake_board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="first task",
        )
        fake_board.append_task(
            task_id="task-board", team_id="team-a",
            author_member_id="a.lead", title="second task",
        )
        tasks = fake_board.open_tasks_for_team(task_id="task-board", team_id="team-a")
        assert len(tasks) == 2


class TestMalformedClaimTargets:
    def test_claim_missing_bead_raises_conflict_error(
        self, fake_board: TeamBoard,
    ) -> None:
        with pytest.raises(TeamBoardConflictError):
            fake_board.claim_task(
                task_id="task-board", task_bead_id="bd-does-not-exist",
                member_id="a.worker", expected_status="open",
            )

    def test_claim_non_task_bead_raises_conflict_error(
        self, fake_board: TeamBoard,
    ) -> None:
        """Claiming a message bead's id (not a task) must fail closed, not
        silently tag the wrong bead type as claimed."""
        msg_id = fake_board.send_message(
            task_id="task-board",
            from_team="team-a", from_member="a.lead",
            to_team="team-b", to_member="b.worker",
            subject="s", body="b",
        )
        with pytest.raises(TeamBoardConflictError):
            fake_board.claim_task(
                task_id="task-board", task_bead_id=msg_id,
                member_id="a.worker", expected_status="open",
            )

    def test_complete_missing_bead_does_not_raise(
        self, fake_board: TeamBoard,
    ) -> None:
        """complete_task delegates to BeadStore.close(), which is a safe
        no-op on a missing id — asserting this stays true so a malformed
        task_bead_id from a caller degrades quietly rather than crashing
        the dispatch loop."""
        fake_board.complete_task(
            task_id="task-board", task_bead_id="bd-does-not-exist",
            outcome="n/a",
        )
