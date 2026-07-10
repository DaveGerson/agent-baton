"""End-to-end test: two-leader plan with cross-team messaging + nested team.

This exercises the full multi-team stack — TeamRegistry, TeamBoard,
team_tools, BeadSelector.select_for_team_member, and the nested-team
dispatch path — through a single execution against an in-memory fixture.

The scenario:

- Phase 1 has two independent team steps (1.1 and 1.2) dispatched in
  parallel.  Each has a lead + one implementer.
- 1.1's lead has a sub_team (1.1.a.b, 1.1.a.c) so the parent step is
  nested.
- 1.1's lead sends a cross-team message to 1.2's lead mid-flight.
- 1.2's lead sees the message on their next dispatch prompt.
- All members record results; both team steps complete; the phase
  completes without approval/gate interaction.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_baton.core.engine.bead_selector import BeadSelector
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.team_board import TeamBoard
from agent_baton.core.engine.team_tools import team_dispatch, team_send_message
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    TeamMember,
)


def _two_leader_plan_with_nested_team() -> MachinePlan:
    """Phase 1: two parallel team steps. 1.1 has a nested sub-team."""
    return MachinePlan(
        task_id="task-e2e",
        task_summary="Ship parallel billing + search features",
        phases=[PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                # Team Billing — lead has nested sub_team of 2 implementers.
                PlanStep(
                    step_id="1.1",
                    agent_name="team",
                    task_description="Ship billing changes",
                    team=[
                        TeamMember(
                            member_id="1.1.a",
                            agent_name="architect",
                            role="lead",
                            task_description="integration + delegation",
                            sub_team=[
                                TeamMember(
                                    member_id="1.1.a.b",
                                    agent_name="backend-engineer",
                                    role="implementer",
                                    task_description="build API",
                                ),
                                TeamMember(
                                    member_id="1.1.a.c",
                                    agent_name="test-engineer",
                                    role="implementer",
                                    task_description="write tests",
                                ),
                            ],
                            synthesis=SynthesisSpec(strategy="merge_files"),
                        ),
                        TeamMember(
                            member_id="1.1.b",
                            agent_name="code-reviewer",
                            role="reviewer",
                            task_description="review billing changes",
                        ),
                    ],
                    synthesis=SynthesisSpec(strategy="merge_files"),
                ),
                # Team Search — flat team (lead + implementer).
                PlanStep(
                    step_id="1.2",
                    agent_name="team",
                    task_description="Ship search changes",
                    team=[
                        TeamMember(
                            member_id="1.2.a",
                            agent_name="architect",
                            role="lead",
                            task_description="coordinate search",
                        ),
                        TeamMember(
                            member_id="1.2.b",
                            agent_name="backend-engineer",
                            role="implementer",
                            task_description="update index",
                        ),
                    ],
                    synthesis=SynthesisSpec(strategy="merge_files"),
                ),
            ],
        )],
    )


class _FakeBeadStore:
    """In-memory stand-in for ``BdBeadStore``.

    Implements just the surface :class:`TeamBoard` /
    :class:`BeadSelector` use (``write``, ``read``, ``close``, ``query``)
    so this end-to-end test doesn't require the external ``bd`` binary to
    be installed — tests must stay hermetic per ``tests/CLAUDE.md``. Mirrors
    the established pattern in ``tests/test_team_tools.py``'s
    ``_FakeBeadStore``.
    """

    def __init__(self) -> None:
        self._beads: dict = {}

    def write(self, bead) -> str:
        self._beads[bead.bead_id] = bead
        return bead.bead_id

    def read(self, bead_id: str):
        return self._beads.get(bead_id)

    def close(self, bead_id: str, summary: str) -> None:
        bead = self._beads.get(bead_id)
        if bead is None:
            return
        bead.status = "closed"

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_name: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        tags: list | None = None,
        limit: int = 100,
    ) -> list:
        out = []
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
def engine(tmp_path: Path) -> ExecutionEngine:
    storage = SqliteStorage(tmp_path / "baton.db")
    eng = ExecutionEngine(team_context_root=tmp_path, storage=storage)
    # Hermetic bead store — see _FakeBeadStore docstring. Without this,
    # engine._bead_store stays None whenever the external `bd` binary is
    # not on PATH (ExecutionEngine's make_bead_store() call degrades
    # gracefully), which then makes every team_tools call that needs the
    # bead store (e.g. team_send_message) raise TeamToolError instead of
    # exercising the actual messaging/selection behavior under test.
    eng._bead_store = _FakeBeadStore()  # type: ignore[attr-defined]
    eng.start(_two_leader_plan_with_nested_team())
    return eng


# ---------------------------------------------------------------------------
# End-to-end scenario
# ---------------------------------------------------------------------------


class TestMultiTeamE2E:
    def test_two_teams_dispatch_in_parallel(self, engine: ExecutionEngine) -> None:
        """next_actions() returns BOTH team steps in the same wave."""
        actions = engine.next_actions()
        # Top-level action per team step.  Each team step packs its own
        # members into parallel_actions.
        top_ids = {a.step_id for a in actions}
        # Nested team 1.1 dispatches lead first (1.1.a); team 1.2 dispatches
        # its lead first (1.2.a).  Both top-level actions are team members.
        assert "1.1.a" in top_ids
        assert "1.2.a" in top_ids

    def test_both_teams_registered(self, engine: ExecutionEngine) -> None:
        """Registry shows parent teams for both team steps AND a child team
        under team-1.1 (the nested sub-team)."""
        engine.next_actions()  # trigger dispatch-time registration
        reg = engine._team_registry
        assert reg is not None
        teams = reg.list_teams("task-e2e")
        team_ids = {t.team_id for t in teams}
        assert "team-1.1" in team_ids
        assert "team-1.2" in team_ids
        assert "1.1::1.1.a" in team_ids  # nested child team

    def test_shared_leader_agent_across_teams(
        self, engine: ExecutionEngine
    ) -> None:
        """Both teams have the same leader_agent (architect) — the registry
        must support this without contention."""
        engine.next_actions()
        reg = engine._team_registry
        arch_teams = reg.list_teams("task-e2e", leader_agent="architect")
        # team-1.1, team-1.2, and 1.1::1.1.a (nested child) all have
        # architect as leader.
        arch_ids = {t.team_id for t in arch_teams}
        assert {"team-1.1", "team-1.2", "1.1::1.1.a"} <= arch_ids

    def test_cross_team_message_delivered_at_next_dispatch(
        self, engine: ExecutionEngine, tmp_path: Path,
    ) -> None:
        """Billing lead messages search lead; search lead sees the message
        in their next dispatch prompt via select_for_team_member."""
        # Trigger dispatch-time team registration.
        engine.next_actions()

        # Team-billing lead (1.1.a) sends a cross-team message.
        bead_id = team_send_message(
            engine,
            task_id="task-e2e",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="Schema drift",
            body="Order.id is now UUID — please update the index writer.",
        )
        assert bead_id

        # Simulate: search lead (1.2.a) is about to be re-dispatched.
        # The selector should surface the message for them.
        state = engine._load_execution()
        search_step = state.plan.phases[0].steps[1]  # step 1.2
        selector = BeadSelector()
        beads = selector.select_for_team_member(
            engine._bead_store, search_step, state.plan,
            team_id="team-1.2", member_id="1.2.a",
        )
        messages = [b for b in beads if b.bead_type == "message"]
        assert len(messages) == 1
        assert messages[0].bead_id == bead_id
        assert "Order.id is now UUID" in messages[0].content

    def test_nested_team_completes_via_synthesis_after_all_members_record(
        self, engine: ExecutionEngine
    ) -> None:
        """Record all five members of team-1.1 (lead + 2 sub + 1 reviewer)
        and verify the parent step completes with merged files."""
        engine.next_actions()
        # Record lead first (it was dispatched on start).
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect",
            status="complete", outcome="integration done",
            files_changed=["src/billing/core.py"],
        )
        # Sub-team members.
        engine.record_team_member_result(
            "1.1", "1.1.a.b", "backend-engineer",
            status="complete", outcome="api built",
            files_changed=["src/billing/api.py", "src/billing/core.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.c", "test-engineer",
            status="complete", outcome="tests green",
            files_changed=["tests/test_billing.py"],
        )
        # Reviewer at top-level.
        engine.record_team_member_result(
            "1.1", "1.1.b", "code-reviewer",
            status="complete", outcome="LGTM",
            files_changed=[],
        )
        state = engine._load_execution()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "complete"
        # merge_files dedupes "src/billing/core.py".
        assert set(parent.files_changed) == {
            "src/billing/core.py",
            "src/billing/api.py",
            "tests/test_billing.py",
        }

    def test_full_phase_completes_when_both_teams_done(
        self, engine: ExecutionEngine
    ) -> None:
        """Both team steps complete → phase advances."""
        engine.next_actions()

        # Team 1.1 — full roster.
        for mid, agent in [
            ("1.1.a", "architect"),
            ("1.1.a.b", "backend-engineer"),
            ("1.1.a.c", "test-engineer"),
            ("1.1.b", "code-reviewer"),
        ]:
            engine.record_team_member_result(
                "1.1", mid, agent,
                status="complete", outcome="done", files_changed=[],
            )

        # Team 1.2 — lead + implementer.
        for mid, agent in [
            ("1.2.a", "architect"),
            ("1.2.b", "backend-engineer"),
        ]:
            engine.record_team_member_result(
                "1.2", mid, agent,
                status="complete", outcome="done", files_changed=[],
            )

        state = engine._load_execution()
        assert state.get_step_result("1.1").status == "complete"
        assert state.get_step_result("1.2").status == "complete"

        # Next action should advance past phase 1 (no more team steps).
        action = engine.next_action()
        # Could be COMPLETE (no more phases), or a GATE, but never a
        # DISPATCH on phase 1 since all its steps are done.
        assert action.action_type != ActionType.DISPATCH or action.step_id not in (
            "1.1", "1.2", "1.1.a", "1.1.a.b", "1.1.a.c", "1.1.b", "1.2.a", "1.2.b",
        )


# ---------------------------------------------------------------------------
# Real local team tool boundary — a deterministic "team member process" is
# a genuine OS subprocess invoking the ACTUAL installed `baton team` console
# script (not a mocked handler call, not a Python function call in-process).
# This is the exact boundary a dispatched agent's Bash tool crosses per
# docs/internal/team-runtime-contract.md §2.2/§9.1.
#
# Scope: the read-only `resource="teams"` path and the CLI's own usage/
# authorization validation never touch the bead store, so they run for real
# here without requiring the external `bd` binary. Verb calls that DO need
# a bead-store write (team update/claim/send/read) exercise the documented
# fail-closed contract (§7.3 exit code 5) against this sandbox's actual
# environment — which genuinely has no `bd` on PATH — rather than mocking
# that reality away; see tests/test_team_tools.py for restart-persistence
# coverage of the bead-backed verbs against a hermetic in-process store.
# ---------------------------------------------------------------------------


class TestRealCliBoundarySubprocess:
    """Deterministic member processes against the real `baton team` CLI."""

    @staticmethod
    def _context_root(tmp_path: Path) -> Path:
        root = tmp_path / ".claude" / "team-context"
        root.mkdir(parents=True)
        return root

    @staticmethod
    def _bootstrap_nested_teams(context_root: Path) -> None:
        """Start a plan, register team-1.1/team-1.2, and stand up a nested
        child team under 1.1's lead — all via the real engine, in-process
        (team_dispatch has no CLI surface yet, by design; see the doc)."""
        storage = SqliteStorage(context_root / "baton.db")
        engine = ExecutionEngine(team_context_root=context_root, storage=storage)
        engine.start(_two_leader_plan_with_nested_team())
        engine.next_actions()  # registers team-1.1 / team-1.2 (+ nested 1.1::1.1.a)
        team_dispatch(
            engine, task_id="task-e2e", parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "docs-writer", "member_id": "1.1.a.z"}],
        )

    @staticmethod
    def _run_baton_team(
        *args: str, context_root: Path, extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["BATON_TEAM_CONTEXT_ROOT"] = str(context_root)
        env.pop("BATON_TASK_ID", None)
        env.pop("BATON_TEAM_MEMBER_ID", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["baton", "team", *args],
            capture_output=True, text=True, timeout=30, env=env,
        )

    def test_real_subprocess_sees_nested_team_after_restart(
        self, tmp_path: Path,
    ) -> None:
        """A brand-new OS process — a fresh interpreter, sharing nothing but
        the persisted SQLite db, exactly what a dispatched team member's
        Bash tool spawns — sees the nested child team a PRIOR process
        registered. Restart persistence AND nested-team visibility,
        exercised through the actual `baton` executable, not a mock."""
        context_root = self._context_root(tmp_path)
        self._bootstrap_nested_teams(context_root)

        result = self._run_baton_team(
            "list", "--task-id", "task-e2e", "--team-id", "team-1.1",
            "--resource", "teams", "--json",
            context_root=context_root,
        )
        assert result.returncode == 0, result.stderr
        teams = json.loads(result.stdout)
        assert [t["team_id"] for t in teams] == ["1.1::1.1.a"]
        assert teams[0]["leader_member_id"] == "1.1.a"

    def test_real_subprocess_unknown_team_id_exits_usage(
        self, tmp_path: Path,
    ) -> None:
        context_root = self._context_root(tmp_path)
        self._bootstrap_nested_teams(context_root)

        result = self._run_baton_team(
            "list", "--task-id", "task-e2e", "--team-id", "team-does-not-exist",
            "--member-id", "1.1.a", "--json",
            context_root=context_root,
        )
        assert result.returncode == 2, result.stdout
        assert "team-does-not-exist" in result.stderr

    def test_real_subprocess_missing_member_id_exits_usage(
        self, tmp_path: Path,
    ) -> None:
        """Malformed call: no --member-id and no $BATON_TEAM_MEMBER_ID."""
        context_root = self._context_root(tmp_path)
        self._bootstrap_nested_teams(context_root)

        result = self._run_baton_team(
            "claim", "--task-id", "task-e2e", "--team-id", "team-1.1",
            "--task-bead-id", "bd-x", "--json",
            context_root=context_root,
        )
        assert result.returncode == 2, result.stdout
        assert "member_id" in result.stderr

    def test_real_subprocess_write_verb_fails_closed_without_bd(
        self, tmp_path: Path,
    ) -> None:
        """A real `baton team update` subprocess in an environment with no
        `bd` on PATH must exit with the documented backend-unavailable
        code — never a raw traceback, never a silent no-op success."""
        context_root = self._context_root(tmp_path)
        self._bootstrap_nested_teams(context_root)

        result = self._run_baton_team(
            "update", "--task-id", "task-e2e", "--team-id", "team-1.1",
            "--member-id", "1.1.a", "--title", "t", "--json",
            context_root=context_root,
        )
        # If a real `bd` binary happens to be on PATH in some other
        # environment this test runs in, the write would succeed (0)
        # instead — either way the process must not crash uncontrolled.
        assert result.returncode in (0, 5), result.stderr
        if result.returncode == 5:
            assert "unavailable" in result.stderr.lower()

    def test_real_subprocess_member_id_resolved_from_env(
        self, tmp_path: Path,
    ) -> None:
        """$BATON_TEAM_MEMBER_ID (the launcher-injected env var) is honored
        by the real subprocess exactly as documented — the CLI reaches the
        authorization/usage checks rather than bailing on 'member_id is
        required', proving the env-var resolution path actually works
        end-to-end and not just at the unit level."""
        context_root = self._context_root(tmp_path)
        self._bootstrap_nested_teams(context_root)

        result = self._run_baton_team(
            "list", "--task-id", "task-e2e", "--team-id", "team-1.1", "--json",
            context_root=context_root,
            extra_env={"BATON_TEAM_MEMBER_ID": "1.1.a"},
        )
        # Reaches the bead-store-backed "tasks" resource (member_id resolved
        # from env, not omitted) — fails closed at 5 (no bd), not at usage.
        assert result.returncode in (0, 5), result.stderr
