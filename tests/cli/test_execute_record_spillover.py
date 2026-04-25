"""Regression tests for bd-1eee: ``baton execute record`` (and ``team-record``)
must explicitly pass ``--outcome-spillover-path`` through to the engine when
provided, while leaving the auto-detect-from-breadcrumb fallback intact for
callers that omit the flag.

The outcome-spillover feature stores the FULL agent output in a sidecar file
when the inline outcome is too large.  ``record_step_result`` accepts an
explicit ``outcome_spillover_path`` argument and falls back to parsing the
``[TRUNCATED — full output: <path> (<N> bytes total)]`` breadcrumb embedded
in ``outcome``.  Prior to this fix the CLI ``record`` handler always relied
on the breadcrumb fallback because it never plumbed the new flag through.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.execution import execute as _mod
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_execute_step_id_format.py style)
# ---------------------------------------------------------------------------


def _build_execute_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="cmd")
    _mod.register(sub)
    return root


def _plain_step(step_id: str = "7.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer--python",
        task_description="Plain single-agent step",
    )


def _team_step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer--python",
        task_description="Team implementation",
        team=[
            TeamMember(member_id=f"{step_id}.a",
                       agent_name="backend-engineer--python",
                       role="implementer",
                       task_description="Build the service"),
            TeamMember(member_id=f"{step_id}.b",
                       agent_name="test-engineer",
                       role="tester",
                       task_description="Test the service"),
        ],
    )


def _make_engine_with_plan(tmp_path: Path, steps: list[PlanStep],
                           task_id: str) -> ExecutionEngine:
    plan = MachinePlan(
        task_id=task_id,
        task_summary="spillover plumbing test",
        phases=[PlanPhase(phase_id=1, name="Work", steps=steps)],
    )
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    return engine


class _FakeStorage:
    def __init__(self, task_id: str) -> None:
        self._tid = task_id

    def get_active_task(self):
        return self._tid

    def set_active_task(self, tid):
        pass


def _ns(**kwargs) -> argparse.Namespace:
    defaults = dict(subcommand=None, task_id=None, output="json")
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _patch_engine(engine: ExecutionEngine, task_id: str):
    storage = _FakeStorage(task_id)
    return [
        patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
        patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=engine),
        patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
    ]


def _run_handler(engine: ExecutionEngine, task_id: str,
                 args: argparse.Namespace) -> None:
    patches = _patch_engine(engine, task_id)
    for p in patches:
        p.start()
    try:
        try:
            _mod.handler(args)
        except SystemExit:
            pass
    finally:
        for p in reversed(patches):
            p.stop()


# ---------------------------------------------------------------------------
# CLI surface: argparse exposes the new flag
# ---------------------------------------------------------------------------


class TestArgparseExposesSpilloverFlag:
    def test_record_accepts_outcome_spillover_path(self) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args([
            "execute", "record",
            "--step", "7.1",
            "--agent", "be",
            "--outcome-spillover-path", "outcome-spillover/step-7.1-X.md",
        ])
        assert args.outcome_spillover_path == "outcome-spillover/step-7.1-X.md"

    def test_record_default_spillover_path_is_empty(self) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args([
            "execute", "record", "--step", "7.1", "--agent", "be",
        ])
        assert args.outcome_spillover_path == ""

    def test_team_record_accepts_outcome_spillover_path(self) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args([
            "execute", "team-record",
            "--step-id", "1.1",
            "--member-id", "1.1.a",
            "--agent", "be",
            "--outcome-spillover-path", "outcome-spillover/step-1.1.a-Y.md",
        ])
        assert args.outcome_spillover_path == "outcome-spillover/step-1.1.a-Y.md"

    def test_team_record_default_spillover_path_is_empty(self) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args([
            "execute", "team-record",
            "--step-id", "1.1",
            "--member-id", "1.1.a",
            "--agent", "be",
        ])
        assert args.outcome_spillover_path == ""


# ---------------------------------------------------------------------------
# Handler plumbing: explicit pass + auto-detect fallback
# ---------------------------------------------------------------------------


class TestRecordHandlerPlumbsSpilloverPath:
    def test_explicit_flag_is_passed_through(self, tmp_path: Path) -> None:
        """When the operator supplies ``--outcome-spillover-path X``, the engine
        records X verbatim on the StepResult — independent of any breadcrumb."""
        tid = "test-record-spillover-explicit"
        engine = _make_engine_with_plan(tmp_path, [_plain_step("7.1")], tid)
        explicit = "outcome-spillover/step-7.1-explicit.md"

        args = _ns(
            subcommand="record",
            step_id="7.1",
            agent="backend-engineer--python",
            status="complete",
            outcome="short summary without breadcrumb",
            files="",
            commit="",
            tokens=0,
            duration=0,
            error="",
            outcome_spillover_path=explicit,
        )
        _run_handler(engine, tid, args)

        sr = engine._load_execution().get_step_result("7.1")
        assert sr is not None
        assert sr.outcome_spillover_path == explicit

    def test_auto_detect_breadcrumb_still_works_without_flag(
        self, tmp_path: Path
    ) -> None:
        """Regression: when the flag is omitted, the engine must still parse
        the breadcrumb prefix from --outcome and populate the field."""
        tid = "test-record-spillover-autodetect"
        engine = _make_engine_with_plan(tmp_path, [_plain_step("7.1")], tid)
        # Path captured by the breadcrumb regex in executor.py
        crumb_path = "outcome-spillover/step-7.1-2026-04-25T01-23-45Z.md"
        outcome_with_crumb = (
            f"[TRUNCATED — full output: {crumb_path} (12345 bytes total)]\n\n"
            "--- First 500 chars ---\nlorem ipsum"
        )
        args = _ns(
            subcommand="record",
            step_id="7.1",
            agent="backend-engineer--python",
            status="complete",
            outcome=outcome_with_crumb,
            files="",
            commit="",
            tokens=0,
            duration=0,
            error="",
            # No --outcome-spillover-path supplied: defaults to ""
            outcome_spillover_path="",
        )
        _run_handler(engine, tid, args)

        sr = engine._load_execution().get_step_result("7.1")
        assert sr is not None
        assert sr.outcome_spillover_path == crumb_path

    def test_no_flag_no_breadcrumb_leaves_field_empty(
        self, tmp_path: Path
    ) -> None:
        """Sanity: when neither the flag nor a breadcrumb is present, the
        spillover field stays empty (no false positives)."""
        tid = "test-record-spillover-empty"
        engine = _make_engine_with_plan(tmp_path, [_plain_step("7.1")], tid)

        args = _ns(
            subcommand="record",
            step_id="7.1",
            agent="be",
            status="complete",
            outcome="ordinary outcome with no truncation",
            files="",
            commit="",
            tokens=0,
            duration=0,
            error="",
            outcome_spillover_path="",
        )
        _run_handler(engine, tid, args)

        sr = engine._load_execution().get_step_result("7.1")
        assert sr is not None
        assert sr.outcome_spillover_path == ""


class TestTeamRecordHandlerPlumbsSpilloverPath:
    def test_team_record_explicit_flag_bubbles_to_parent(
        self, tmp_path: Path
    ) -> None:
        """``team-record --outcome-spillover-path X`` plumbs X into the engine,
        which mirrors it onto the parent StepResult so handoff assembly can
        recover the full member output."""
        tid = "test-teamrecord-spillover-explicit"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)
        explicit = "outcome-spillover/step-1.1.a-explicit.md"

        args = _ns(
            subcommand="team-record",
            step_id="1.1",
            member_id="1.1.a",
            agent="backend-engineer--python",
            status="complete",
            outcome="short member summary",
            files="",
            outcome_spillover_path=explicit,
        )
        _run_handler(engine, tid, args)

        parent = engine._load_execution().get_step_result("1.1")
        assert parent is not None
        assert parent.outcome_spillover_path == explicit
        # And the member result itself exists with the recorded outcome.
        member = next(
            (m for m in parent.member_results if m.member_id == "1.1.a"), None
        )
        assert member is not None
        assert member.status == "complete"

    def test_team_record_auto_detect_breadcrumb(self, tmp_path: Path) -> None:
        """Without the flag, the engine still extracts the spillover path from
        the truncation breadcrumb in --outcome and bubbles it onto the parent."""
        tid = "test-teamrecord-spillover-autodetect"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)
        crumb_path = "outcome-spillover/step-1.1.b-2026-04-25T02-00-00Z.md"
        outcome_with_crumb = (
            f"[TRUNCATED — full output: {crumb_path} (98765 bytes total)]\n\n"
            "--- First 500 chars ---\nmember work output"
        )
        args = _ns(
            subcommand="team-record",
            step_id="1.1",
            member_id="1.1.b",
            agent="test-engineer",
            status="complete",
            outcome=outcome_with_crumb,
            files="",
            outcome_spillover_path="",
        )
        _run_handler(engine, tid, args)

        parent = engine._load_execution().get_step_result("1.1")
        assert parent is not None
        assert parent.outcome_spillover_path == crumb_path

    def test_team_record_no_flag_no_breadcrumb_leaves_parent_empty(
        self, tmp_path: Path
    ) -> None:
        tid = "test-teamrecord-spillover-empty"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        args = _ns(
            subcommand="team-record",
            step_id="1.1",
            member_id="1.1.a",
            agent="be",
            status="complete",
            outcome="plain outcome",
            files="",
            outcome_spillover_path="",
        )
        _run_handler(engine, tid, args)

        parent = engine._load_execution().get_step_result("1.1")
        assert parent is not None
        assert parent.outcome_spillover_path == ""
