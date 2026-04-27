"""Integration tests for worktree isolation in concurrent agent dispatch.

Covers Fix A (prompt-level Worktree Discipline + path relativization) and
Fix C (engine signals ``isolation="worktree"`` on parallel DISPATCH waves).
See ``proposals/worktree-isolation-fix.md`` for the diagnosis.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


_DISCIPLINE_HEADING = "## Worktree Discipline (MANDATORY)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    context_files: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    blocked_paths: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model="sonnet",
        context_files=context_files or [],
        allowed_paths=allowed_paths or [],
        blocked_paths=blocked_paths or [],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path / ".claude" / "team-context")


def _plan(steps: list[PlanStep]) -> MachinePlan:
    return MachinePlan(
        task_id="task-iso",
        task_summary="Worktree isolation regression",
        risk_level="LOW",
        phases=[PlanPhase(phase_id=1, name="Implementation", steps=steps)],
    )


# ---------------------------------------------------------------------------
# Fix A — prompt-level Worktree Discipline
# ---------------------------------------------------------------------------


class TestPromptDiscipline:
    def test_dispatch_prompt_includes_worktree_discipline_when_isolation_set(
        self,
    ) -> None:
        dispatcher = PromptDispatcher()
        step = _step()
        prompt = dispatcher.build_delegation_prompt(step, isolation="worktree")
        assert prompt.count(_DISCIPLINE_HEADING) == 1
        # Sanity: a couple of the operational clauses survive verbatim.
        assert "git rev-parse --show-toplevel" in prompt
        assert "Never `cd` out of your worktree" in prompt

    def test_dispatch_prompt_omits_worktree_discipline_by_default(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert _DISCIPLINE_HEADING not in prompt
        assert "Worktree Discipline" not in prompt


class TestPathRelativization:
    def test_absolute_paths_relativized_under_worktree_isolation(
        self, tmp_path: Path,
    ) -> None:
        dispatcher = PromptDispatcher()
        project_root = tmp_path / "proj"
        project_root.mkdir()
        abs_ctx = str(project_root / "agent_baton" / "foo.py")
        abs_allowed = str(project_root / "agent_baton" / "core")
        abs_blocked = str(project_root / "secrets")
        step = _step(
            context_files=[abs_ctx, "tests/test_foo.py"],
            allowed_paths=[abs_allowed],
            blocked_paths=[abs_blocked],
        )
        prompt = dispatcher.build_delegation_prompt(
            step,
            isolation="worktree",
            project_root=project_root,
        )
        # Relative form must appear, absolute form must NOT.
        assert "agent_baton/foo.py" in prompt
        assert abs_ctx not in prompt
        assert "agent_baton/core" in prompt
        assert abs_allowed not in prompt
        assert "secrets" in prompt
        assert abs_blocked not in prompt
        # Relative paths already supplied are preserved verbatim.
        assert "tests/test_foo.py" in prompt

    def test_paths_outside_project_root_flagged(self, tmp_path: Path) -> None:
        dispatcher = PromptDispatcher()
        project_root = tmp_path / "proj"
        project_root.mkdir()
        outside = "/etc/passwd"
        step = _step(context_files=[outside])
        prompt = dispatcher.build_delegation_prompt(
            step,
            isolation="worktree",
            project_root=project_root,
        )
        assert outside in prompt
        # Some kind of out-of-project warning marker is rendered next to it.
        assert "outside project root" in prompt.lower()


# ---------------------------------------------------------------------------
# Fix C — engine signals isolation on parallel DISPATCH waves
# ---------------------------------------------------------------------------


class TestEngineSignalsIsolation:
    def test_parallel_actions_marked_with_worktree_isolation(
        self, tmp_path: Path,
    ) -> None:
        engine = _engine(tmp_path)
        plan = _plan([
            _step(step_id="1.1", agent_name="a"),
            _step(step_id="1.2", agent_name="b"),
            _step(step_id="1.3", agent_name="c"),
        ])
        engine.start(plan)
        actions = engine.next_actions()
        assert len(actions) >= 2
        for action in actions:
            assert action.action_type == ActionType.DISPATCH
            assert action.isolation == "worktree", (
                f"step {action.step_id} missing isolation marker"
            )

    def test_singleton_dispatch_omits_isolation_field(
        self, tmp_path: Path,
    ) -> None:
        engine = _engine(tmp_path)
        plan = _plan([_step(step_id="1.1", agent_name="solo")])
        engine.start(plan)
        actions = engine.next_actions()
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DISPATCH
        assert actions[0].isolation == ""
        # to_dict() should not emit the field for singletons.
        assert "isolation" not in actions[0].to_dict()
