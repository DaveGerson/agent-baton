"""Tests for `baton plan` strict-resumability mode (A1.d).

Default mode emits a warning when claude-teams + long-running + team
phases collide. Strict mode (BATON_TEAMS_STRICT_RESUMABILITY=1) treats
the warning as a refusal (exit 2).
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


def _team_plan() -> MachinePlan:
    return MachinePlan(
        task_id="t1",
        task_summary="exercise strict mode",
        budget_tier="long-running",
        phases=[PlanPhase(
            phase_id=1, name="Team work",
            steps=[PlanStep(
                step_id="1.1", agent_name="team",
                task_description="impl + review",
                model="sonnet",
                team=[
                    TeamMember(
                        member_id="1.1.a", agent_name="backend-engineer",
                        role="implementer", task_description="x",
                        model="sonnet",
                    ),
                ],
            )],
        )],
    )


def _args(condition_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        summary="exercise strict mode", task_type=None, agents=None,
        project=None, json=False, save=True, explain=False,
        knowledge=[], knowledge_pack=[], intervention="low",
        model=None, complexity=None, import_path=None, template=False,
        save_as_template=None, from_template=None, skip_init=True,
        verbose=False, dry_run=False, release_id=None,
        gate_scope="focused", goal=None, max_amend_cycles=3,
    )


class TestStrictResumability:
    def test_warning_only_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        monkeypatch.delenv("BATON_TEAMS_STRICT_RESUMABILITY", raising=False)
        monkeypatch.chdir(tmp_path)

        plan = _team_plan()
        with patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner"
        ) as MP:
            MP.return_value.create_plan.return_value = plan
            MP.return_value.explain_plan.return_value = "x"
            # Should NOT raise SystemExit when not strict.
            plan_cmd.handler(_args(tmp_path))
        err = capsys.readouterr().err
        assert "warning:" in err
        assert "cannot resume" in err

    def test_strict_mode_refuses_with_exit_2(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        monkeypatch.setenv("BATON_TEAMS_STRICT_RESUMABILITY", "1")
        monkeypatch.chdir(tmp_path)

        plan = _team_plan()
        with patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner"
        ) as MP:
            MP.return_value.create_plan.return_value = plan
            MP.return_value.explain_plan.return_value = "x"
            with pytest.raises(SystemExit) as exc_info:
                plan_cmd.handler(_args(tmp_path))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "refusing to save" in err

    def test_strict_mode_passes_when_no_warnings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Strict mode is a no-op when the plan is safe (worktree backend
        or no team phases)."""
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        monkeypatch.setenv("BATON_TEAMS_STRICT_RESUMABILITY", "1")
        monkeypatch.chdir(tmp_path)

        plan = _team_plan()
        with patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner"
        ) as MP:
            MP.return_value.create_plan.return_value = plan
            MP.return_value.explain_plan.return_value = "x"
            plan_cmd.handler(_args(tmp_path))  # no SystemExit
