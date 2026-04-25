"""Regression tests for bd-e201: step-ID format consistency across
``baton execute`` subcommands.

`baton execute next` emits team sub-step IDs in the ``N.N.x`` form (and
nested ``N.N.x.y`` form for nested teams).  The ``dispatched`` and
``record`` subcommands previously rejected those IDs with a regex that
only accepted ``N.N``.  This test suite asserts the inputs ``next``
emits are accepted by the recording subcommands, and that
``dispatched``/``record`` route team-member IDs to the same store as
``team-record``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.execution import execute as _mod
from agent_baton.cli.commands.execution._validators import (
    PLAIN_STEP_ID_RE,
    STEP_ID_RE,
    TEAM_MEMBER_ID_RE,
    is_plain_step_id,
    is_team_member_id,
    is_valid_step_id,
    parent_step_id,
    validate_step_id,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------


class TestStepIdValidator:
    @pytest.mark.parametrize("step_id", ["1.1", "7.3", "10.42", "1.1.a", "2.3.b",
                                           "10.2.aa", "1.1.a.b", "1.1.a.b.c"])
    def test_accepts_plain_and_team_member_ids(self, step_id: str) -> None:
        assert is_valid_step_id(step_id), f"{step_id!r} should be accepted"
        assert STEP_ID_RE.match(step_id) is not None

    @pytest.mark.parametrize("step_id", [
        "",
        "1",
        "1.1.A",         # uppercase suffix rejected
        "1.1.1",         # numeric suffix is not a team member id
        "1.1.a.B",       # mixed case in nested suffix
        "abc",
        "1.1-a",
        "1..1",
        "1.1.a ",        # trailing whitespace
    ])
    def test_rejects_invalid_ids(self, step_id: str) -> None:
        assert not is_valid_step_id(step_id), f"{step_id!r} should be rejected"

    def test_team_vs_plain_predicates(self) -> None:
        assert is_plain_step_id("1.1") and not is_team_member_id("1.1")
        assert is_team_member_id("1.1.a") and not is_plain_step_id("1.1.a")
        assert is_team_member_id("1.1.a.b")
        # Nested team member IDs are still team-member IDs.
        assert is_valid_step_id("1.1.a.b.c")

    def test_parent_step_id_truncates_to_two_segments(self) -> None:
        assert parent_step_id("1.1.a") == "1.1"
        assert parent_step_id("1.1.a.b.c") == "1.1"
        assert parent_step_id("7.3") == "7.3"
        assert parent_step_id("") == ""

    def test_validate_step_id_calls_error_fn_for_invalid(self) -> None:
        calls: list[str] = []
        def fake_validation_error(msg: str) -> None:
            calls.append(msg)
            raise SystemExit(2)
        with pytest.raises(SystemExit):
            validate_step_id("nope", fake_validation_error)
        assert calls and "invalid step ID" in calls[0]
        # Hint must mention both forms so the user sees the contract.
        assert "N.N" in calls[0] and "N.N.x" in calls[0]

    def test_validate_step_id_passes_for_valid(self) -> None:
        # Should not invoke the error callable.
        called: list[str] = []
        validate_step_id("1.1", lambda msg: called.append(msg))
        validate_step_id("1.1.a", lambda msg: called.append(msg))
        validate_step_id("1.1.a.b", lambda msg: called.append(msg))
        assert called == []


# ---------------------------------------------------------------------------
# Argparse integration: subcommand still accepts both forms after wiring
# ---------------------------------------------------------------------------


def _build_execute_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="cmd")
    _mod.register(sub)
    return root


class TestArgparseAcceptsBothForms:
    @pytest.mark.parametrize("cmd_form", ["1.1", "1.1.a", "3.2.c", "1.1.a.b"])
    def test_dispatched_step_argparse_accepts_both(self, cmd_form: str) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args(
            ["execute", "dispatched", "--step", cmd_form, "--agent", "be"]
        )
        assert args.subcommand == "dispatched"
        assert args.step_id == cmd_form

    @pytest.mark.parametrize("cmd_form", ["1.1", "1.1.a", "3.2.c"])
    def test_record_step_argparse_accepts_both(self, cmd_form: str) -> None:
        parser = _build_execute_parser()
        args = parser.parse_args(
            ["execute", "record", "--step", cmd_form, "--agent", "be"]
        )
        assert args.subcommand == "record"
        assert args.step_id == cmd_form


# ---------------------------------------------------------------------------
# Handler routing tests with a real engine
# ---------------------------------------------------------------------------


def _team_step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer--python",
        task_description="Team implementation",
        team=[
            TeamMember(member_id=f"{step_id}.a", agent_name="backend-engineer--python",
                        role="implementer", task_description="Build the service"),
            TeamMember(member_id=f"{step_id}.b", agent_name="test-engineer",
                        role="tester", task_description="Test the service"),
        ],
    )


def _plain_step(step_id: str = "7.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer--python",
        task_description="Plain single-agent step",
    )


def _make_engine_with_plan(tmp_path: Path, steps: list[PlanStep],
                             task_id: str) -> ExecutionEngine:
    plan = MachinePlan(
        task_id=task_id,
        task_summary="step-id format test",
        phases=[PlanPhase(phase_id=1, name="Work", steps=steps)],
    )
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    return engine


class _FakeStorage:
    def __init__(self, task_id: str) -> None:
        self._tid = task_id
    def get_active_task(self):  # pragma: no cover - trivial
        return self._tid
    def set_active_task(self, tid):  # pragma: no cover - trivial
        pass


def _ns(**kwargs) -> argparse.Namespace:
    """Build a Namespace defaulted to common args used by handler()."""
    defaults = dict(
        subcommand=None,
        task_id=None,
        output="json",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _patch_engine(engine: ExecutionEngine, task_id: str):
    """Context-manager bundle that patches handler() dependencies."""
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


class TestDispatchedSubstepIds:
    def test_dispatched_accepts_team_member_id_and_routes_to_team_store(
        self, tmp_path: Path
    ) -> None:
        """The bd-e201 bug: ``dispatched --step 3.1.a`` must succeed and write
        to the same place ``team-record --member-id 3.1.a`` writes."""
        tid = "test-dispatched-substep"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        args = _ns(
            subcommand="dispatched",
            step_id="1.1.a",
            agent="backend-engineer--python",
        )
        _run_handler(engine, tid, args)

        state = engine._load_execution()
        parent = state.get_step_result("1.1")
        assert parent is not None, "parent StepResult must be created"
        member_ids = [m.member_id for m in parent.member_results]
        assert "1.1.a" in member_ids
        match = next(m for m in parent.member_results if m.member_id == "1.1.a")
        assert match.status == "dispatched"
        assert match.agent_name == "backend-engineer--python"

    def test_dispatched_solo_step_still_works(self, tmp_path: Path) -> None:
        """Plain step IDs still flow through engine.mark_dispatched() — no
        regression for the non-team path."""
        tid = "test-dispatched-solo"
        engine = _make_engine_with_plan(tmp_path, [_plain_step("7.1")], tid)

        args = _ns(
            subcommand="dispatched",
            step_id="7.1",
            agent="backend-engineer--python",
        )
        _run_handler(engine, tid, args)

        state = engine._load_execution()
        sr = state.get_step_result("7.1")
        assert sr is not None
        assert sr.status == "dispatched"
        assert sr.agent_name == "backend-engineer--python"
        assert sr.member_results == []  # not a team step

    def test_dispatched_invalid_substep_char_rejected(
        self, tmp_path: Path, capsys
    ) -> None:
        """``dispatched --step 1.1.A`` (uppercase suffix) is rejected with a
        clear error message that mentions both accepted formats."""
        tid = "test-dispatched-bad"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        args = _ns(
            subcommand="dispatched",
            step_id="1.1.A",   # uppercase — not a valid team-member suffix
            agent="be",
        )
        with pytest.raises(SystemExit):
            patches = _patch_engine(engine, tid)
            for p in patches:
                p.start()
            try:
                _mod.handler(args)
            finally:
                for p in reversed(patches):
                    p.stop()
        err = capsys.readouterr().err
        assert "invalid step ID" in err
        assert "1.1.A" in err
        # New error must mention both N.N and N.N.x forms.
        assert "N.N" in err and "N.N.x" in err

    def test_dispatched_substep_matches_team_record(self, tmp_path: Path) -> None:
        """``dispatched --step X.Y.z`` and ``team-record --step X.Y --member-id
        X.Y.z`` should hit the same parent.member_results table."""
        tid = "test-symmetry"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        # Path A: dispatched with a team-member id
        _run_handler(engine, tid, _ns(
            subcommand="dispatched", step_id="1.1.a", agent="be",
        ))
        # Path B: team-record for the second member
        _run_handler(engine, tid, _ns(
            subcommand="team-record",
            step_id="1.1",
            member_id="1.1.b",
            agent="te",
            status="complete",
            outcome="ok",
            files="",
        ))

        state = engine._load_execution()
        parent = state.get_step_result("1.1")
        assert parent is not None
        ids = sorted(m.member_id for m in parent.member_results)
        assert ids == ["1.1.a", "1.1.b"]


class TestRecordSubstepIds:
    def test_record_accepts_team_member_id(self, tmp_path: Path) -> None:
        """``record --step 3.1.a`` must succeed for a team sub-step."""
        tid = "test-record-substep"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        args = _ns(
            subcommand="record",
            step_id="1.1.a",
            agent="backend-engineer--python",
            status="complete",
            outcome="implemented service",
            files="src/service.py",
            commit="",
            tokens=0,
            duration=0,
            error="",
        )
        _run_handler(engine, tid, args)

        state = engine._load_execution()
        parent = state.get_step_result("1.1")
        assert parent is not None
        member = next(
            (m for m in parent.member_results if m.member_id == "1.1.a"), None
        )
        assert member is not None
        assert member.status == "complete"
        assert member.outcome == "implemented service"
        assert "src/service.py" in member.files_changed

    def test_record_solo_step_unchanged(self, tmp_path: Path) -> None:
        """Plain step recording still calls record_step_result()."""
        tid = "test-record-solo"
        engine = _make_engine_with_plan(tmp_path, [_plain_step("7.1")], tid)

        args = _ns(
            subcommand="record",
            step_id="7.1",
            agent="backend-engineer--python",
            status="complete",
            outcome="done",
            files="",
            commit="",
            tokens=0,
            duration=0,
            error="",
        )
        _run_handler(engine, tid, args)

        state = engine._load_execution()
        sr = state.get_step_result("7.1")
        assert sr is not None
        assert sr.status == "complete"
        assert sr.outcome == "done"
        assert sr.member_results == []

    def test_record_invalid_substep_id_rejected(
        self, tmp_path: Path, capsys
    ) -> None:
        tid = "test-record-bad"
        engine = _make_engine_with_plan(tmp_path, [_team_step("1.1")], tid)

        args = _ns(
            subcommand="record",
            step_id="1.1.A",
            agent="be",
            status="complete",
            outcome="",
            files="",
            commit="",
            tokens=0,
            duration=0,
            error="",
        )
        with pytest.raises(SystemExit):
            patches = _patch_engine(engine, tid)
            for p in patches:
                p.start()
            try:
                _mod.handler(args)
            finally:
                for p in reversed(patches):
                    p.stop()
        err = capsys.readouterr().err
        assert "invalid step ID" in err and "1.1.A" in err
