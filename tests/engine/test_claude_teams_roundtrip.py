"""End-to-end round-trip test for the claude-teams backend (A1).

Closes the "paper integration" gap: confirms that the spawn-prompt
artifact, the hook-command string, and the
``baton execute team-record --hook-source claude-teams`` bridge all
plug together. We don't spawn a real Claude Code session — we simulate
the hook firing by invoking the CLI handler directly with the same
arguments the rendered command would supply.

What this pins:

1. ``ClaudeTeamsBackend.on_team_dispatched`` writes a parseable spawn
   prompt to ``.claude/team-context/teams/team-{step_id}/spawn.md``.
2. The hook command rendered by the backend, when parsed, produces
   the same CLI invocation pattern the team-record handler expects.
3. Running that handler with ``--hook-source claude-teams`` against
   the engine records the member result AND emits a
   ``teammate_message`` mailbox event tagged ``hook_source=claude-teams``.
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.execution import execute as execute_cmd
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.mailbox import TeamMailbox
from agent_baton.core.engine.team_backends import ClaudeTeamsBackend
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


def _team_plan() -> MachinePlan:
    return MachinePlan(
        task_id="t-roundtrip",
        task_summary="claude-teams roundtrip",
        phases=[PlanPhase(
            phase_id=1, name="Build",
            steps=[PlanStep(
                step_id="1.1", agent_name="team",
                task_description="implement and review",
                model="sonnet",
                team=[
                    TeamMember(
                        member_id="1.1.a", agent_name="backend-engineer",
                        role="implementer",
                        task_description="write the service",
                        model="sonnet",
                    ),
                    TeamMember(
                        member_id="1.1.b", agent_name="code-reviewer",
                        role="reviewer", task_description="security review",
                        model="sonnet",
                    ),
                ],
            )],
        )],
    )


class TestClaudeTeamsRoundtrip:
    def test_spawn_prompt_contains_parseable_hook_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The hook command in the spawn prompt must be a real CLI
        invocation that our argparse can parse."""
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        plan = _team_plan()
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        spawn = tmp_path / "teams" / "team-1.1" / "spawn.md"
        text = spawn.read_text(encoding="utf-8")

        # Pull the example hook command out of the prompt.
        cmd_line = next(
            line for line in text.splitlines()
            if line.strip().startswith("baton execute team-record")
        )
        # Substitute the placeholder so we can argparse-validate the shape.
        rendered = cmd_line.replace("<MEMBER_ID>", "1.1.a")
        tokens = shlex.split(rendered)
        # Tokens should be [baton, execute, team-record, --task-id, X, --step-id, Y, --member-id, Z, --hook-source, claude-teams]
        assert tokens[0:3] == ["baton", "execute", "team-record"]
        assert "--task-id" in tokens
        assert "--step-id" in tokens
        assert "--member-id" in tokens
        assert tokens[tokens.index("--member-id") + 1] == "1.1.a"
        assert "--hook-source" in tokens
        assert tokens[tokens.index("--hook-source") + 1] == "claude-teams"

    def _setup_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[ExecutionEngine, MachinePlan]:
        """Chdir to a project root, init the engine + start the team,
        return (engine, plan).

        The CLI handler will build its own ExecutionEngine pointed at
        the cwd's ``.claude/team-context/``. We anchor both engines to
        the same task by setting BATON_TASK_ID, and both backends agree
        because no SQLite db exists (file backend on both sides).
        """
        ctx_root = tmp_path / ".claude" / "team-context"
        ctx_root.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        plan = _team_plan()
        monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

        engine = ExecutionEngine(
            team_context_root=ctx_root,
            task_id=plan.task_id,
        )
        engine.start(plan)
        # Drive the first dispatch action so the team is registered.
        engine.next_action()
        return engine, plan

    def test_hook_invocation_records_member_and_tags_mailbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate the TaskCompleted hook firing: invoke the
        team-record handler with the same args the rendered command
        would supply. Confirm the engine records the member result AND
        the mailbox carries a teammate_message tagged
        ``hook_source=claude-teams``."""
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        engine, plan = self._setup_context(tmp_path, monkeypatch)

        ns = argparse.Namespace(
            subcommand="team-record",
            task_id=plan.task_id,
            step_id="1.1", member_id="1.1.a",
            agent="backend-engineer", status="complete",
            outcome="impl landed", files="src/service.py",
            outcome_spillover_path="", hook_source="claude-teams",
            output="text",
        )
        # The handler builds its own ExecutionEngine pointed at the cwd's
        # `.claude/team-context/`. Same backing storage as our test
        # `engine`, so its writes land where we read.
        execute_cmd.handler(ns)

        # Engine state recorded the member.
        state = engine._load_execution()
        assert state is not None
        parent = state.get_step_result("1.1")
        assert parent is not None
        statuses = {mr.member_id: mr.status for mr in parent.member_results}
        assert statuses.get("1.1.a") == "complete"

        # Mailbox carries task_created + task_completed + a hook-tagged
        # teammate_message.
        mb = TeamMailbox(tmp_path / ".claude" / "team-context", "team-1.1")
        events = mb.read_all()
        kinds = [e.event_type for e in events]
        assert "task_created" in kinds
        assert "task_completed" in kinds
        assert "teammate_message" in kinds

        msgs = [
            e for e in events
            if e.event_type == "teammate_message"
            and e.payload.get("hook_source") == "claude-teams"
        ]
        assert len(msgs) == 1
        assert msgs[0].from_member == "1.1.a"

    def test_hook_invocation_without_source_does_not_tag_mailbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When --hook-source is unset (the local CLI path), no
        teammate_message tag fires — only task_completed."""
        _, plan = self._setup_context(tmp_path, monkeypatch)

        ns = argparse.Namespace(
            subcommand="team-record",
            task_id=plan.task_id,
            step_id="1.1", member_id="1.1.a",
            agent="backend-engineer", status="complete",
            outcome="impl landed", files="",
            outcome_spillover_path="", hook_source="",
            output="text",
        )
        execute_cmd.handler(ns)

        mb = TeamMailbox(tmp_path / ".claude" / "team-context", "team-1.1")
        events = mb.read_all()
        kinds = [e.event_type for e in events]
        assert "task_completed" in kinds
        msgs = [e for e in events if e.event_type == "teammate_message"]
        assert msgs == []
