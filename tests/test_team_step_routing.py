"""Tests for team-step routing usability improvements.

Covers three changes introduced to reduce orchestrator confusion about
which record command to use:

1. ``_is_team_member_id()`` helper — recognises team-member IDs (N.N.x).
2. ``_print_action()`` — annotates DISPATCH output with Team-Step/Parent-Step
   lines when the step_id is a team-member ID.
3. ``ExecutionAction.to_dict()`` — sets ``is_team_member`` / ``parent_step_id``
   in the serialised dict for team-member dispatches.
4. ``team-record`` CLI handler — rejects plain step IDs with a helpful error
   that points the caller at ``baton execute record``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.cli.commands.execution.execute import _is_team_member_id, _print_action


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _member(member_id: str, agent_name: str = "backend-engineer") -> TeamMember:
    return TeamMember(
        member_id=member_id,
        agent_name=agent_name,
        role="implementer",
        task_description="Do the work",
    )


def _team_step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="Team implementation",
        team=[
            _member(f"{step_id}.a", "backend-engineer"),
            _member(f"{step_id}.b", "test-engineer"),
        ],
    )


def _plain_step(step_id: str = "7.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="Plain single-agent step",
    )


def _plan_with_plain_step(tmp_path: Path) -> tuple[ExecutionEngine, MachinePlan]:
    plan = MachinePlan(
        task_id="test-routing",
        task_summary="Routing test",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Work",
                steps=[_plain_step("7.1")],
            )
        ],
    )
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    return engine, plan


def _plan_with_team_step(tmp_path: Path) -> tuple[ExecutionEngine, MachinePlan]:
    plan = MachinePlan(
        task_id="test-team-routing",
        task_summary="Team routing test",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Work",
                steps=[_team_step("1.1")],
            )
        ],
    )
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    return engine, plan


# ===========================================================================
# TestIsTeamMemberIdHelper
# ===========================================================================

class TestIsTeamMemberIdHelper:
    """Unit tests for the _is_team_member_id() helper."""

    @pytest.mark.parametrize("step_id", [
        "1.1.a",
        "2.3.b",
        "10.2.c",
        "1.1.aa",   # multi-letter suffix (future-proof)
        "3.10.z",
    ])
    def test_returns_true_for_team_member_ids(self, step_id: str) -> None:
        assert _is_team_member_id(step_id) is True

    @pytest.mark.parametrize("step_id", [
        "1.1",
        "7.3",
        "10.2",
        "",
        "1",
        "1.1.1",    # numeric suffix — not a team member ID
        "1.1.A",    # uppercase — not matched
        "abc",
    ])
    def test_returns_false_for_plain_step_ids(self, step_id: str) -> None:
        assert _is_team_member_id(step_id) is False


# ===========================================================================
# TestPrintActionTeamAnnotation
# ===========================================================================

class TestPrintActionTeamAnnotation:
    """_print_action() emits Team-Step annotations for team-member dispatches."""

    def test_team_member_dispatch_annotated(self, capsys) -> None:
        """DISPATCH with a team-member step_id prints Team-Step: yes."""
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="Dispatch team member 1.1.a",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="Do the work.",
            step_id="1.1.a",
        )
        _print_action(action.to_dict())
        out = capsys.readouterr().out

        assert "ACTION: DISPATCH" in out
        assert "  Step:  1.1.a" in out
        assert "  Team-Step: yes" in out
        assert "  Parent-Step: 1.1" in out
        assert "  Record-With: baton execute team-record --step-id 1.1 --member-id 1.1.a" in out

    def test_plain_step_dispatch_not_annotated(self, capsys) -> None:
        """DISPATCH with a plain step_id does NOT add Team-Step lines."""
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="Dispatch step 7.1",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="Do the work.",
            step_id="7.1",
        )
        _print_action(action.to_dict())
        out = capsys.readouterr().out

        assert "ACTION: DISPATCH" in out
        assert "  Step:  7.1" in out
        assert "Team-Step" not in out
        assert "Parent-Step" not in out
        assert "Record-With" not in out

    def test_parent_step_id_derived_correctly(self, capsys) -> None:
        """Parent-Step is always the first two segments of the member ID."""
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="Dispatch member",
            agent_name="test-engineer",
            agent_model="sonnet",
            delegation_prompt="Review it.",
            step_id="2.3.b",
        )
        _print_action(action.to_dict())
        out = capsys.readouterr().out

        assert "  Parent-Step: 2.3" in out
        assert "--step-id 2.3 --member-id 2.3.b" in out

    def test_message_field_still_present_after_annotation(self, capsys) -> None:
        """The Message: field appears after the team annotation lines."""
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="Some task description",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="...",
            step_id="1.1.a",
        )
        _print_action(action.to_dict())
        out = capsys.readouterr().out

        assert "  Message: Some task description" in out
        # Message must still appear even when team annotations are present.
        lines = out.splitlines()
        msg_idx = next(i for i, l in enumerate(lines) if "  Message:" in l)
        team_idx = next(i for i, l in enumerate(lines) if "  Team-Step:" in l)
        assert team_idx < msg_idx  # annotation precedes message


# ===========================================================================
# TestExecutionActionToDict
# ===========================================================================

class TestExecutionActionToDict:
    """ExecutionAction.to_dict() sets is_team_member and parent_step_id."""

    def test_team_member_dispatch_sets_is_team_member_true(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="team dispatch",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="...",
            step_id="1.1.a",
        )
        d = action.to_dict()
        assert d["is_team_member"] is True

    def test_team_member_dispatch_includes_parent_step_id(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="team dispatch",
            agent_name="test-engineer",
            agent_model="sonnet",
            delegation_prompt="...",
            step_id="3.2.c",
        )
        d = action.to_dict()
        assert d["parent_step_id"] == "3.2"

    def test_plain_step_dispatch_sets_is_team_member_false(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="plain dispatch",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="...",
            step_id="7.1",
        )
        d = action.to_dict()
        assert d["is_team_member"] is False

    def test_plain_step_dispatch_has_no_parent_step_id(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            message="plain dispatch",
            agent_name="backend-engineer",
            agent_model="sonnet",
            delegation_prompt="...",
            step_id="7.1",
        )
        d = action.to_dict()
        assert "parent_step_id" not in d

    def test_non_dispatch_action_has_no_is_team_member(self) -> None:
        """Non-DISPATCH actions do not get is_team_member in their dict."""
        action = ExecutionAction(
            action_type=ActionType.COMPLETE,
            message="all done",
            summary="Execution complete.",
        )
        d = action.to_dict()
        assert "is_team_member" not in d

    def test_team_dispatch_from_engine_has_correct_flags(
        self, tmp_path: Path
    ) -> None:
        """Integration: engine-generated team-member DISPATCH has is_team_member=True."""
        engine, _ = _plan_with_team_step(tmp_path)
        # next_action returns the first member's DISPATCH
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        d = action.to_dict()
        assert d["is_team_member"] is True
        assert d["parent_step_id"] == "1.1"
        assert d["step_id"].startswith("1.1.")

    def test_plain_dispatch_from_engine_has_is_team_member_false(
        self, tmp_path: Path
    ) -> None:
        """Integration: engine-generated plain DISPATCH has is_team_member=False."""
        engine, _ = _plan_with_plain_step(tmp_path)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        d = action.to_dict()
        assert d["is_team_member"] is False
        assert "parent_step_id" not in d


# ===========================================================================
# TestTeamRecordGuard
# ===========================================================================

class TestTeamRecordGuard:
    """team-record handler rejects non-team step IDs with a helpful message."""

    def _make_args(
        self,
        step_id: str,
        member_id: str = "1.1.a",
        agent: str = "backend-engineer",
        status: str = "complete",
        outcome: str = "",
        files: str = "",
        output: str = "text",
        task_id: str | None = None,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            subcommand="team-record",
            step_id=step_id,
            member_id=member_id,
            agent=agent,
            status=status,
            outcome=outcome,
            files=files,
            output=output,
            task_id=task_id,
        )

    def test_plain_step_raises_user_error(self, tmp_path: Path) -> None:
        """Calling team-record on a plain step exits with an error message."""
        from agent_baton.cli.commands.execution import execute as execute_mod

        engine, _ = _plan_with_plain_step(tmp_path)
        args = self._make_args(step_id="7.1", member_id="7.1.a")

        # Patch the engine construction inside handler so it uses our engine.
        with patch.object(execute_mod, "ExecutionEngine", return_value=engine), \
             patch.object(execute_mod, "get_project_storage", return_value=MagicMock()), \
             patch.object(execute_mod, "EventBus", return_value=MagicMock()), \
             patch("os.environ.get", return_value=None), \
             patch.object(execute_mod.StatePersistence, "get_active_task_id", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            execute_mod.handler(args)

        assert exc_info.value.code != 0

    def test_plain_step_error_message_names_correct_command(
        self, tmp_path: Path
    ) -> None:
        """The error output tells the user to use 'baton execute record'."""
        from agent_baton.cli.commands.execution import execute as execute_mod

        engine, _ = _plan_with_plain_step(tmp_path)
        args = self._make_args(step_id="7.1", member_id="7.1.a")

        captured_msg: list[str] = []
        captured_hint: list[str] = []

        def fake_user_error(msg: str, *, hint: str = "", **_kw):  # type: ignore[override]
            captured_msg.append(msg)
            captured_hint.append(hint)
            raise SystemExit(1)

        with patch.object(execute_mod, "ExecutionEngine", return_value=engine), \
             patch.object(execute_mod, "get_project_storage", return_value=MagicMock()), \
             patch.object(execute_mod, "EventBus", return_value=MagicMock()), \
             patch("os.environ.get", return_value=None), \
             patch.object(execute_mod.StatePersistence, "get_active_task_id", return_value=None), \
             patch.object(execute_mod, "user_error", side_effect=fake_user_error), \
             pytest.raises(SystemExit):
            execute_mod.handler(args)

        assert captured_msg, "user_error was not called"
        assert "7.1" in captured_msg[0]
        assert "not a team step" in captured_msg[0]
        assert "baton execute record" in captured_hint[0]
        assert "7.1" in captured_hint[0]

    def test_team_step_does_not_trigger_guard(self, tmp_path: Path) -> None:
        """Calling team-record on a genuine team step proceeds without error.

        The guard checks plan_step.team; for a real team step the list is
        non-empty so the guard is skipped and record_team_member_result runs.
        """
        from agent_baton.cli.commands.execution import execute as execute_mod

        engine, _ = _plan_with_team_step(tmp_path)
        # No need to pre-mark anything — the guard only reads plan data, not
        # step results. record_team_member_result creates the parent on first call.

        args = self._make_args(
            step_id="1.1",
            member_id="1.1.a",
            agent="backend-engineer",
            status="complete",
            outcome="Done",
        )

        with patch.object(execute_mod, "ExecutionEngine", return_value=engine), \
             patch.object(execute_mod, "get_project_storage", return_value=MagicMock()), \
             patch.object(execute_mod, "EventBus", return_value=MagicMock()), \
             patch("os.environ.get", return_value=None), \
             patch.object(execute_mod.StatePersistence, "get_active_task_id", return_value=None), \
             patch.object(execute_mod, "ContextManager", return_value=MagicMock()):
            # Should not raise — guard passes and record proceeds.
            execute_mod.handler(args)

    def test_guard_is_skipped_when_no_active_state(self, tmp_path: Path) -> None:
        """When there is no active execution state, the guard falls through gracefully.

        With no state, _load_execution() returns None so the guard is skipped
        entirely.  record_team_member_result then raises RuntimeError (not a
        guard error), which propagates out of the handler.  The key assertion
        is that user_error was NOT called with a "not a team step" message —
        the guard must not fire when it cannot inspect the plan.
        """
        from agent_baton.cli.commands.execution import execute as execute_mod

        # Engine with no started execution — _load_execution returns None.
        engine = ExecutionEngine(team_context_root=tmp_path)

        args = self._make_args(step_id="7.1", member_id="7.1.a")

        captured_user_error_calls: list[str] = []

        def fake_user_error(msg: str, **_kw):
            captured_user_error_calls.append(msg)
            raise SystemExit(1)

        # record_team_member_result raises RuntimeError when there is no active
        # state. The handler does not catch RuntimeError, so it propagates.
        with patch.object(execute_mod, "ExecutionEngine", return_value=engine), \
             patch.object(execute_mod, "get_project_storage", return_value=MagicMock()), \
             patch.object(execute_mod, "EventBus", return_value=MagicMock()), \
             patch("os.environ.get", return_value=None), \
             patch.object(execute_mod.StatePersistence, "get_active_task_id", return_value=None), \
             patch.object(execute_mod, "user_error", side_effect=fake_user_error), \
             pytest.raises(RuntimeError, match="no active execution state"):
            execute_mod.handler(args)

        # The guard must NOT have fired (no state → guard skipped entirely).
        assert not any("not a team step" in m for m in captured_user_error_calls)
