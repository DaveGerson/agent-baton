"""Tests for the agent-facing team tools in ``team_tools.py``.

The tools are Python-callable backings for the ``team_*`` agent tools
documented in ``references/team-messaging.md`` (legacy names) and
``docs/internal/team-runtime-contract.md`` (canonical ``team_list``,
``team_claim``, ``team_update``, ``team_send``, ``team_read`` +
``team_dispatch``). Tests exercise validation, authorization, optimistic
concurrency, idempotency, and end-to-end flows over the
:class:`TeamRegistry` + :class:`TeamBoard` stack.

Uses an in-memory fake bead store (:class:`_FakeBeadStore`) rather than the
real ``bd``-backed store so these tests stay hermetic per ``tests/CLAUDE.md``
(no dependency on the external ``bd`` binary being installed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.team_board import TeamBoardConflictError
from agent_baton.core.engine.team_tools import (
    TEAM_TOOL_NAMES,
    TeamAuthorizationError,
    TeamConcurrencyError,
    TeamToolError,
    advertised_team_tools_for_role,
    authorized_team_tools,
    team_add_task,
    team_claim,
    team_claim_task,
    team_complete_task,
    team_dispatch,
    team_list,
    team_read,
    team_send,
    team_send_message,
    team_update,
)
from agent_baton.models.bead import Bead
from agent_baton.models.execution import (
    SYNTHESIS_STATE_TRANSITIONS,
    MachinePlan, PlanPhase, PlanStep, SynthesisSpec, SynthesisState, TeamMember,
    is_valid_synthesis_transition,
)
from agent_baton.utils.time import utcnow_zulu as _utcnow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeBeadStore:
    """Minimal in-memory stand-in for ``BdBeadStore``.

    Implements just the surface :class:`TeamBoard` uses (``write``,
    ``read``, ``close``, ``query``) so team-tool tests don't require the
    external ``bd`` binary to be installed.
    """

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


def _engine_with_storage(tmp_path: Path) -> ExecutionEngine:
    from agent_baton.core.storage.sqlite_backend import SqliteStorage
    storage = SqliteStorage(tmp_path / "baton.db")
    engine = ExecutionEngine(team_context_root=tmp_path, storage=storage)
    # Hermetic bead store — see _FakeBeadStore docstring.
    engine._bead_store = _FakeBeadStore()  # type: ignore[attr-defined]
    return engine


def _two_team_plan() -> MachinePlan:
    """Plan with two parallel team steps, each with a lead + implementer."""
    return MachinePlan(
        task_id="task-tools",
        task_summary="parallel teams",
        phases=[PlanPhase(
            phase_id=1, name="impl",
            steps=[
                PlanStep(
                    step_id="1.1", agent_name="team",
                    task_description="team a",
                    team=[
                        TeamMember(member_id="1.1.a", agent_name="architect",
                                   role="lead"),
                        TeamMember(member_id="1.1.b", agent_name="be",
                                   role="implementer"),
                    ],
                ),
                PlanStep(
                    step_id="1.2", agent_name="team",
                    task_description="team b",
                    team=[
                        TeamMember(member_id="1.2.a", agent_name="architect",
                                   role="lead"),
                        TeamMember(member_id="1.2.b", agent_name="te",
                                   role="implementer"),
                    ],
                ),
            ],
        )],
    )


@pytest.fixture
def engine(tmp_path: Path) -> ExecutionEngine:
    eng = _engine_with_storage(tmp_path)
    eng.start(_two_team_plan())
    # next_actions() returns every dispatchable team step and registers
    # their teams in the registry.  Both teams are now discoverable.
    eng.next_actions()
    return eng


# ---------------------------------------------------------------------------
# Messaging + task tools
# ---------------------------------------------------------------------------


class TestTeamSendMessage:
    def test_cross_team_message(self, engine: ExecutionEngine) -> None:
        bead_id = team_send_message(
            engine,
            task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="heads up", body="schema change",
        )
        assert bead_id

    def test_missing_recipient_team_raises(
        self, engine: ExecutionEngine
    ) -> None:
        with pytest.raises(TeamToolError, match="Team 'team-missing'"):
            team_send_message(
                engine, task_id="task-tools",
                from_team="team-1.1", from_member="1.1.a",
                to_team="team-missing", to_member=None,
                subject="s", body="b",
            )

    def test_missing_sender_member_raises(
        self, engine: ExecutionEngine
    ) -> None:
        with pytest.raises(TeamToolError, match="Member 'nope'"):
            team_send_message(
                engine, task_id="task-tools",
                from_team="team-1.1", from_member="nope",
                to_team="team-1.2", to_member=None,
                subject="s", body="b",
            )


class TestTeamAddAndClaimTask:
    def test_add_and_claim_cycle(self, engine: ExecutionEngine) -> None:
        tid = team_add_task(
            engine, task_id="task-tools", team_id="team-1.1",
            author_member_id="1.1.a", title="t", detail="d",
        )
        assert tid
        team_claim_task(
            engine, task_id="task-tools",
            task_bead_id=tid, member_id="1.1.b",
        )
        team_complete_task(
            engine, task_id="task-tools",
            task_bead_id=tid, outcome="done",
        )


# ---------------------------------------------------------------------------
# team_dispatch — lead-only
# ---------------------------------------------------------------------------


class TestTeamDispatchRoleEnforcement:
    def test_non_lead_caller_raises(self, engine: ExecutionEngine) -> None:
        """An implementer calling team_dispatch gets a clear error."""
        with pytest.raises(TeamToolError, match="role='lead'"):
            team_dispatch(
                engine, task_id="task-tools",
                parent_team_id="team-1.1",
                caller_member_id="1.1.b",  # implementer, not lead
                members=[{"agent_name": "backend-engineer"}],
            )

    def test_lead_caller_creates_child_team(
        self, engine: ExecutionEngine
    ) -> None:
        child_id = team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[
                {"agent_name": "backend-engineer"},
                {"agent_name": "test-engineer"},
            ],
        )
        assert child_id == "1.1::1.1.a"
        # Registry records child team under the parent.
        children = engine._team_registry.child_teams("task-tools", "team-1.1")
        assert len(children) == 1
        assert children[0].team_id == child_id
        assert children[0].leader_agent == "architect"

    def test_subteam_members_appended_to_lead(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "backend-engineer"}],
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        assert lead is not None
        assert len(lead.sub_team) == 1
        assert lead.sub_team[0].agent_name == "backend-engineer"
        # Auto-generated member_id under the caller.
        assert lead.sub_team[0].member_id == "1.1.a.a"

    def test_custom_member_ids_honored(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[
                {"agent_name": "be", "member_id": "1.1.a.impl"},
                {"agent_name": "te", "member_id": "1.1.a.tst"},
            ],
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        ids = [m.member_id for m in lead.sub_team]
        assert ids == ["1.1.a.impl", "1.1.a.tst"]

    def test_synthesis_passed_through(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "be"}],
            synthesis={"strategy": "merge_files"},
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        assert lead.synthesis is not None
        assert lead.synthesis.strategy == "merge_files"


class TestTeamDispatchIntegrates:
    def test_next_dispatch_wave_includes_new_subteam(
        self, engine: ExecutionEngine
    ) -> None:
        """After team_dispatch, next_actions() includes the new sub-team members."""
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "backend-engineer"}],
        )
        # Record any already-dispatched members as done so the next wave
        # fires fresh for the new sub-member.
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect", status="complete", outcome="c")
        engine.record_team_member_result(
            "1.1", "1.1.b", "be", status="complete", outcome="b")
        actions = engine.next_actions()
        new_ids = {a.step_id for a in actions}
        # 1.1.a.a is the auto-generated sub-member_id.
        assert "1.1.a.a" in new_ids


# ---------------------------------------------------------------------------
# Authorization matrix — docs/internal/team-runtime-contract.md
# ---------------------------------------------------------------------------


class TestAuthorizationMatrix:
    def test_team_tool_names_are_exactly_six(self) -> None:
        assert TEAM_TOOL_NAMES == {
            "team_list", "team_claim", "team_update",
            "team_send", "team_read", "team_dispatch",
        }

    def test_lead_authorized_for_all_tools(self) -> None:
        assert authorized_team_tools("lead") == TEAM_TOOL_NAMES

    def test_implementer_not_authorized_for_dispatch(self) -> None:
        tools = authorized_team_tools("implementer")
        assert "team_dispatch" not in tools
        assert {"team_list", "team_claim", "team_update", "team_send", "team_read"} <= tools

    def test_unknown_role_falls_back_to_board_and_mailbox(self) -> None:
        tools = authorized_team_tools("some-custom-role")
        assert "team_dispatch" not in tools
        assert "team_list" in tools

    def test_advertised_team_tools_for_role_is_sorted(self) -> None:
        assert advertised_team_tools_for_role("lead") == sorted(TEAM_TOOL_NAMES)
        assert "team_dispatch" not in advertised_team_tools_for_role("reviewer")

    def test_implementer_calling_team_update_ok(
        self, engine: ExecutionEngine
    ) -> None:
        # Sanity: an authorized call does not raise TeamAuthorizationError.
        result = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.b", title="fix retry loop",
        )
        assert result["task_bead_id"]
        assert result["status"] == "open"


# ---------------------------------------------------------------------------
# Canonical tools: team_list / team_claim / team_update / team_send / team_read
# ---------------------------------------------------------------------------


class TestTeamList:
    def test_lists_open_tasks(self, engine: ExecutionEngine) -> None:
        team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t1", detail="d1",
        )
        tasks = team_list(engine, task_id="task-tools", team_id="team-1.1")
        assert len(tasks) == 1
        assert tasks[0]["title"] == "t1"
        assert tasks[0]["status"] == "open"
        assert tasks[0]["claimed_by"] is None

    def test_status_filter_claimed(self, engine: ExecutionEngine) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t2",
        )
        team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.b",
        )
        claimed = team_list(
            engine, task_id="task-tools", team_id="team-1.1", status="claimed",
        )
        assert len(claimed) == 1
        assert claimed[0]["claimed_by"] == "1.1.b"
        openn = team_list(
            engine, task_id="task-tools", team_id="team-1.1", status="open",
        )
        assert openn == []

    def test_status_filter_done(self, engine: ExecutionEngine) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t3",
        )
        team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", task_bead_id=created["task_bead_id"],
            status="complete", outcome="shipped",
        )
        done = team_list(
            engine, task_id="task-tools", team_id="team-1.1", status="done",
        )
        assert len(done) == 1
        assert done[0]["status"] == "done"

    def test_resource_teams_lists_child_teams(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools", parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "backend-engineer"}],
        )
        teams = team_list(
            engine, task_id="task-tools", team_id="team-1.1", resource="teams",
        )
        assert len(teams) == 1
        assert teams[0]["team_id"] == "1.1::1.1.a"

    def test_unsupported_resource_raises(self, engine: ExecutionEngine) -> None:
        with pytest.raises(TeamToolError, match="unsupported resource"):
            team_list(
                engine, task_id="task-tools", team_id="team-1.1",
                resource="bogus",
            )

    def test_missing_member_raises(self, engine: ExecutionEngine) -> None:
        with pytest.raises(TeamToolError, match="Member 'nope'"):
            team_list(
                engine, task_id="task-tools", team_id="team-1.1",
                member_id="nope",
            )


class TestTeamClaimConcurrency:
    def test_second_claim_by_different_member_raises(
        self, engine: ExecutionEngine
    ) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t",
        )
        team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.b",
        )
        with pytest.raises(TeamConcurrencyError, match="already claimed"):
            team_claim(
                engine, task_id="task-tools", team_id="team-1.1",
                task_bead_id=created["task_bead_id"], member_id="1.1.a",
            )

    def test_reclaim_by_same_member_is_idempotent(
        self, engine: ExecutionEngine
    ) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t",
        )
        team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.b",
        )
        # Same member re-claiming does not raise.
        result = team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.b",
        )
        assert result["claimed_by"] == "1.1.b"

    def test_allow_reassign_bypasses_conflict(
        self, engine: ExecutionEngine
    ) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t",
        )
        team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.b",
        )
        result = team_claim(
            engine, task_id="task-tools", team_id="team-1.1",
            task_bead_id=created["task_bead_id"], member_id="1.1.a",
            allow_reassign=True,
        )
        assert result["claimed_by"] == "1.1.a"

    def test_missing_task_bead_raises(self, engine: ExecutionEngine) -> None:
        with pytest.raises(TeamConcurrencyError, match="not found"):
            team_claim(
                engine, task_id="task-tools", team_id="team-1.1",
                task_bead_id="bd-does-not-exist", member_id="1.1.b",
            )

    def test_legacy_team_claim_task_stays_last_writer_wins(
        self, engine: ExecutionEngine
    ) -> None:
        """The legacy tool's behavior is unchanged: no conflict error."""
        tid = team_add_task(
            engine, task_id="task-tools", team_id="team-1.1",
            author_member_id="1.1.a", title="t",
        )
        team_claim_task(
            engine, task_id="task-tools", task_bead_id=tid, member_id="1.1.b",
        )
        # Different member reclaims — legacy behavior: silently replaces.
        team_claim_task(
            engine, task_id="task-tools", task_bead_id=tid, member_id="1.1.a",
        )


class TestTeamUpdateIdempotency:
    def test_repeated_create_with_same_key_returns_original_id(
        self, engine: ExecutionEngine
    ) -> None:
        first = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t", idempotency_key="retry-1",
        )
        second = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t (retried)", idempotency_key="retry-1",
        )
        assert first["task_bead_id"] == second["task_bead_id"]
        all_tasks = team_list(engine, task_id="task-tools", team_id="team-1.1")
        assert len(all_tasks) == 1

    def test_create_without_title_raises(self, engine: ExecutionEngine) -> None:
        with pytest.raises(TeamToolError, match="title"):
            team_update(
                engine, task_id="task-tools", team_id="team-1.1",
                member_id="1.1.a",
            )

    def test_complete_without_outcome_raises(
        self, engine: ExecutionEngine
    ) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t",
        )
        with pytest.raises(TeamToolError, match="outcome"):
            team_update(
                engine, task_id="task-tools", team_id="team-1.1",
                member_id="1.1.a", task_bead_id=created["task_bead_id"],
                status="complete",
            )

    def test_unsupported_transition_raises(
        self, engine: ExecutionEngine
    ) -> None:
        created = team_update(
            engine, task_id="task-tools", team_id="team-1.1",
            member_id="1.1.a", title="t",
        )
        with pytest.raises(TeamToolError, match="unsupported transition"):
            team_update(
                engine, task_id="task-tools", team_id="team-1.1",
                member_id="1.1.a", task_bead_id=created["task_bead_id"],
                status="blocked",
            )


class TestTeamSendCanonical:
    def test_team_send_matches_team_send_message(
        self, engine: ExecutionEngine
    ) -> None:
        result = team_send(
            engine, task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="s", body="b",
        )
        assert result["message_bead_id"]


class TestTeamReadPull:
    def test_read_returns_and_acks_by_default(
        self, engine: ExecutionEngine
    ) -> None:
        team_send(
            engine, task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="hello", body="world",
        )
        first = team_read(
            engine, task_id="task-tools", team_id="team-1.2", member_id="1.2.a",
        )
        assert len(first) == 1
        assert first[0]["subject"] == "hello"
        assert first[0]["body"] == "world"
        # Second read sees nothing new — already acked.
        second = team_read(
            engine, task_id="task-tools", team_id="team-1.2", member_id="1.2.a",
        )
        assert second == []

    def test_peek_without_ack_is_repeatable(
        self, engine: ExecutionEngine
    ) -> None:
        team_send(
            engine, task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="hello", body="world",
        )
        first = team_read(
            engine, task_id="task-tools", team_id="team-1.2", member_id="1.2.a",
            ack=False,
        )
        second = team_read(
            engine, task_id="task-tools", team_id="team-1.2", member_id="1.2.a",
            ack=False,
        )
        assert len(first) == 1
        assert len(second) == 1


# ---------------------------------------------------------------------------
# Bead-store unavailability — fail closed with a typed error, not an
# opaque AttributeError (phase 4 4.2 regression coverage).
# ---------------------------------------------------------------------------


class TestBeadStoreUnavailable:
    """``engine._bead_store is None`` (e.g. the ``bd`` binary is missing)
    must raise a clean :class:`TeamToolError` — never an ``AttributeError``
    from deep inside :class:`TeamBoard`."""

    def test_team_list_tasks_raises_team_tool_error(
        self, engine: ExecutionEngine
    ) -> None:
        engine._bead_store = None  # type: ignore[attr-defined]
        with pytest.raises(TeamToolError, match="bead store is unavailable"):
            team_list(engine, task_id="task-tools", team_id="team-1.1")

    def test_team_list_teams_resource_unaffected(
        self, engine: ExecutionEngine
    ) -> None:
        # resource="teams" never touches the bead store (TeamRegistry-only).
        engine._bead_store = None  # type: ignore[attr-defined]
        result = team_list(
            engine, task_id="task-tools", team_id="team-1.1", resource="teams",
        )
        assert result == []

    def test_team_claim_raises_team_tool_error(
        self, engine: ExecutionEngine
    ) -> None:
        engine._bead_store = None  # type: ignore[attr-defined]
        with pytest.raises(TeamToolError, match="bead store is unavailable"):
            team_claim(
                engine, task_id="task-tools", team_id="team-1.1",
                task_bead_id="bd-missing", member_id="1.1.b",
            )

    def test_team_update_raises_team_tool_error(
        self, engine: ExecutionEngine
    ) -> None:
        engine._bead_store = None  # type: ignore[attr-defined]
        with pytest.raises(TeamToolError, match="bead store is unavailable"):
            team_update(
                engine, task_id="task-tools", team_id="team-1.1",
                member_id="1.1.a", title="t",
            )

    def test_team_send_raises_team_tool_error(
        self, engine: ExecutionEngine
    ) -> None:
        engine._bead_store = None  # type: ignore[attr-defined]
        with pytest.raises(TeamToolError, match="bead store is unavailable"):
            team_send(
                engine, task_id="task-tools",
                from_team="team-1.1", from_member="1.1.a",
                to_team="team-1.2", subject="s", body="b",
            )

    def test_team_read_raises_team_tool_error(
        self, engine: ExecutionEngine
    ) -> None:
        engine._bead_store = None  # type: ignore[attr-defined]
        with pytest.raises(TeamToolError, match="bead store is unavailable"):
            team_read(engine, task_id="task-tools", team_id="team-1.1", member_id="1.1.a")


# ---------------------------------------------------------------------------
# Audit logging — every canonical tool call emits an always-on structured
# log line naming tool/task_id/member_id/outcome, independent of whether
# the call reached a bead write (docs/internal/team-runtime-contract.md
# §7.1; phase 4 4.2 closes the gap left by 4.1's architecture doc).
# ---------------------------------------------------------------------------


class TestAuditLogging:
    def test_successful_call_logs_success_outcome(
        self, engine: ExecutionEngine, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="agent_baton.core.engine.team_tools")
        team_send(
            engine, task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", subject="s", body="b",
        )
        records = [r for r in caplog.records if "team_send" in r.message]
        assert records, "expected an audit log line for team_send"
        assert "outcome=success" in records[-1].message
        assert "task_id=task-tools" in records[-1].message
        assert "member_id=1.1.a" in records[-1].message

    def test_failed_call_logs_failure_outcome(
        self, engine: ExecutionEngine, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level("INFO", logger="agent_baton.core.engine.team_tools")
        with pytest.raises(TeamToolError):
            team_claim(
                engine, task_id="task-tools", team_id="team-missing",
                task_bead_id="bd-x", member_id="1.1.a",
            )
        records = [r for r in caplog.records if "team_claim" in r.message]
        assert records, "expected an audit log line for the failed team_claim call"
        assert "outcome=failed" in records[-1].message

    def test_authorization_failure_is_still_audited(
        self, engine: ExecutionEngine, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A rejected call never reaches a bead write, but must still be
        # observable via the audit log (doc §7.1's explicit rationale).
        # team_dispatch's own role check raises TeamToolError (its
        # documented, non-TeamAuthorizationError guard — see the
        # module docstring on team_dispatch).
        caplog.set_level("INFO", logger="agent_baton.core.engine.team_tools")
        with pytest.raises(TeamToolError, match="role='lead'"):
            team_dispatch(
                engine, task_id="task-tools", parent_team_id="team-1.2",
                caller_member_id="1.2.b", members=[],
            )
        records = [r for r in caplog.records if "team_dispatch" in r.message]
        assert records, "expected an audit log line for the rejected team_dispatch call"
        assert "outcome=failed" in records[-1].message


# ---------------------------------------------------------------------------
# TeamBoardConflictError — low-level optimistic concurrency in TeamBoard
# ---------------------------------------------------------------------------


class TestTeamBoardClaimConcurrency:
    def test_expected_status_open_raises_on_conflict(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.core.engine.team_board import TeamBoard
        board = TeamBoard(engine._bead_store)
        tid = board.append_task(
            task_id="task-tools", team_id="team-1.1",
            author_member_id="1.1.a", title="t",
        )
        board.claim_task(
            task_id="task-tools", task_bead_id=tid, member_id="1.1.b",
            expected_status="open",
        )
        with pytest.raises(TeamBoardConflictError):
            board.claim_task(
                task_id="task-tools", task_bead_id=tid, member_id="1.1.a",
                expected_status="open",
            )

    def test_default_expected_status_none_is_legacy_last_writer_wins(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.core.engine.team_board import TeamBoard
        board = TeamBoard(engine._bead_store)
        tid = board.append_task(
            task_id="task-tools", team_id="team-1.1",
            author_member_id="1.1.a", title="t",
        )
        board.claim_task(task_id="task-tools", task_bead_id=tid, member_id="1.1.b")
        board.claim_task(task_id="task-tools", task_bead_id=tid, member_id="1.1.a")


# ---------------------------------------------------------------------------
# TeamRegistry.set_status_if — team-level optimistic concurrency
# ---------------------------------------------------------------------------


class TestTeamRegistrySetStatusIf:
    def test_matching_expected_status_succeeds(
        self, engine: ExecutionEngine
    ) -> None:
        reg = engine._team_registry
        assert reg.set_status_if(
            "task-tools", "team-1.1", expected_status="active", status="complete",
        )
        team = reg.get_team("task-tools", "team-1.1")
        assert team.status == "complete"

    def test_mismatched_expected_status_is_noop(
        self, engine: ExecutionEngine
    ) -> None:
        reg = engine._team_registry
        assert not reg.set_status_if(
            "task-tools", "team-1.1", expected_status="complete", status="failed",
        )
        team = reg.get_team("task-tools", "team-1.1")
        assert team.status == "active"  # unchanged


# ---------------------------------------------------------------------------
# SynthesisState — synthesis state machine (design artifact)
# ---------------------------------------------------------------------------


class TestSynthesisStateMachine:
    def test_pending_to_collecting_valid(self) -> None:
        assert is_valid_synthesis_transition(
            SynthesisState.PENDING, SynthesisState.COLLECTING,
        )

    def test_pending_to_synthesized_invalid(self) -> None:
        assert not is_valid_synthesis_transition(
            SynthesisState.PENDING, SynthesisState.SYNTHESIZED,
        )

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        assert SYNTHESIS_STATE_TRANSITIONS[SynthesisState.SYNTHESIZED] == frozenset()
        assert SYNTHESIS_STATE_TRANSITIONS[SynthesisState.FAILED] == frozenset()

    def test_escalated_can_resume_synthesizing_or_terminate_failed(self) -> None:
        assert is_valid_synthesis_transition(
            SynthesisState.ESCALATED, SynthesisState.SYNTHESIZING,
        )
        assert is_valid_synthesis_transition(
            SynthesisState.ESCALATED, SynthesisState.FAILED,
        )
        assert not is_valid_synthesis_transition(
            SynthesisState.ESCALATED, SynthesisState.SYNTHESIZED,
        )

    def test_every_state_reachable_and_covered(self) -> None:
        # Every SynthesisState value has an entry in the transition table
        # (even terminal states, mapped to an explicit empty set) so a
        # KeyError can never silently mean "anything goes".
        for state in SynthesisState:
            assert state in SYNTHESIS_STATE_TRANSITIONS
