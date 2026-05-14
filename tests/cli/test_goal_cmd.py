"""Tests for `baton goal` CLI surface (G1.e).

The goal command is a thin wrapper that delegates to `baton plan`'s
handler with --goal preset. We test argument parsing and the delegation
shape; the underlying planner behavior is covered by the plan_cmd
tests and the engine-level wrap tests.
"""
from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import goal_cmd


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    goal_cmd.register(sub)
    return parser.parse_args(argv)


class TestGoalArgParsing:
    def test_minimal_invocation(self) -> None:
        ns = _parse(["goal", "all integration tests pass"])
        assert ns.cmd == "goal"
        assert ns.condition == "all integration tests pass"
        assert ns.max_amend_cycles == 3  # default

    def test_full_invocation(self) -> None:
        ns = _parse([
            "goal", "do thing",
            "--max-amend-cycles", "7",
            "--model", "opus",
            "--gate-scope", "smoke",
            "--explain",
            "--verbose",
            "--no-execute",
            "--knowledge", "/path/a",
            "--knowledge", "/path/b",
            "--knowledge-pack", "security",
        ])
        assert ns.max_amend_cycles == 7
        assert ns.model == "opus"
        assert ns.gate_scope == "smoke"
        assert ns.explain
        assert ns.verbose
        assert ns.no_execute
        assert ns.knowledge == ["/path/a", "/path/b"]
        assert ns.knowledge_pack == ["security"]

    def test_missing_condition_errors(self) -> None:
        with pytest.raises(SystemExit):
            _parse(["goal"])


class TestGoalDelegation:
    def test_empty_condition_exits(self, capsys) -> None:
        ns = _parse(["goal", "   "])
        with pytest.raises(SystemExit) as exc_info:
            goal_cmd.handler(ns)
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "non-empty" in err

    def test_delegates_to_plan_handler_with_goal_set(self) -> None:
        ns = _parse(["goal", "all tests pass", "--max-amend-cycles", "5", "--no-execute"])
        with patch(
            "agent_baton.cli.commands.goal_cmd.plan_cmd.handler"
        ) as mock_plan:
            goal_cmd.handler(ns)
        assert mock_plan.call_count == 1
        delegated = mock_plan.call_args.args[0]
        assert delegated.goal == "all tests pass"
        assert delegated.max_amend_cycles == 5
        assert delegated.save is True
        assert delegated.summary == "all tests pass"
        assert delegated.dry_run is False

    def test_prints_next_step_when_executing(self, capsys) -> None:
        ns = _parse(["goal", "ship the feature"])
        with patch("agent_baton.cli.commands.goal_cmd.plan_cmd.handler"):
            goal_cmd.handler(ns)
        out = capsys.readouterr().out
        assert "Goal set" in out
        assert "ship the feature" in out
        assert "baton execute start" in out

    def test_no_next_step_when_no_execute(self, capsys) -> None:
        ns = _parse(["goal", "ship", "--no-execute"])
        with patch("agent_baton.cli.commands.goal_cmd.plan_cmd.handler"):
            goal_cmd.handler(ns)
        out = capsys.readouterr().out
        assert "baton execute start" not in out
