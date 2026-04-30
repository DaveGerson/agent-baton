"""Pinned regression tests for the 005b engine decomposition follow-ups.

These tests target the ``ExecutionEngine._drive_resolver_loop`` and
``ExecutionEngine._apply_resolver_decision`` methods introduced in the
005b refactor (step 2.3).  They pin three specific behaviours that were
identified as follow-up regression risks after the cutover:

- bd-8083 sub-item 1: bead-conflict pre-loop warning fires on EVERY call
  to ``_drive_resolver_loop``, including when ``state.status`` is already
  a terminal status (previously the check was guarded behind a status
  check; the cutover changed that).
- bd-8083 sub-item 2: the ``max_iter`` guard raises ``RuntimeError`` when
  ``_apply_resolver_decision`` never returns a non-``None`` action (i.e.
  the loop would otherwise spin forever).
- bd-8083 sub-item 3: all 22 ``DecisionKind`` arms are covered by
  ``_apply_resolver_decision`` — no silent ``else: pass`` branch.

All tests are pure unit tests: no disk I/O, no real LLM calls, no external
processes.  Engine instances use ``tmp_path`` for state persistence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Skip the entire module when the 005b decomposition modules are not present
# so the test file itself can be committed to any branch and will simply be
# collected as "deselected" on pre-005b checkouts.
pytest.importorskip(
    "agent_baton.core.engine.resolver",
    reason="005b decomposition not yet present on this branch",
)

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.resolver import DecisionKind, ResolverDecision
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Shared fixtures / factories
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="do work",
    )


def _phase(phase_id: int = 0, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=steps if steps is not None else [_step()],
    )


def _plan(
    task_id: str = "test-task",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="test task",
        phases=phases if phases is not None else [_phase()],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    """Return a fresh engine backed by *tmp_path*."""
    return ExecutionEngine(team_context_root=tmp_path)


def _started_state(
    tmp_path: Path,
    *,
    status: str = "running",
    phases: list[PlanPhase] | None = None,
) -> tuple[ExecutionEngine, ExecutionState]:
    """Create an engine + ExecutionState with the given *status*."""
    engine = _engine(tmp_path)
    plan = _plan(phases=phases)
    engine.start(plan)
    state = engine._load_state()
    assert state is not None
    state.status = status
    return engine, state


# ---------------------------------------------------------------------------
# Test 1 (bd-8083 sub-item 1) — bead-conflict pre-loop warning
#
# Phase 2 moved the bead-conflict check to the TOP of _drive_resolver_loop,
# BEFORE the resolver is invoked.  Legacy code guarded it behind the
# non-terminal status path.  Pin both branches:
#   a) state.status == "complete" (terminal) → warning emitted, loop
#      returns the COMPLETE action without crashing.
#   b) state.status == "running" → warning emitted, loop continues to
#      the resolver and returns a DISPATCH action.
# ---------------------------------------------------------------------------


class TestBeadConflictPreLoopCheck:
    """bd-8083 sub-item 1: bead-conflict warning fires at top of resolver loop."""

    @staticmethod
    def _wire_bead_store(engine: ExecutionEngine) -> MagicMock:
        """Inject a mock BeadStore that always reports an unresolved conflict."""
        mock_store = MagicMock()
        mock_store.has_unresolved_conflicts.return_value = True
        engine._bead_store = mock_store
        return mock_store

    def test_bead_conflict_warning_on_terminal_state(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A terminal (complete) state still triggers the bead-conflict check.

        Prior to 005b Phase 2, the check was inside the ``running`` branch of
        ``_determine_action``, so it silently skipped terminal statuses.  The
        cutover moved it to the top of ``_drive_resolver_loop`` so it fires
        unconditionally.  Verify: warning is emitted AND the loop returns a
        COMPLETE action (no crash, no spin).
        """
        engine, state = _started_state(tmp_path, status="complete")
        self._wire_bead_store(engine)

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.executor"):
            action = engine._drive_resolver_loop(state)

        # The loop must return the terminal action without crashing.
        assert action.action_type in (ActionType.COMPLETE, ActionType.COMPLETE.value), (
            f"Expected COMPLETE action for terminal state, got {action.action_type}"
        )

        # The warning must have been emitted.
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Bead conflict" in msg for msg in warning_msgs), (
            f"Expected 'Bead conflict' warning in log; got: {warning_msgs}"
        )

        # has_unresolved_conflicts must have been called with the task_id.
        engine._bead_store.has_unresolved_conflicts.assert_called_once_with(state.task_id)

    def test_bead_conflict_warning_on_running_state(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A running state triggers the bead-conflict check and the loop continues.

        The loop should emit the warning AND then proceed to dispatch the first
        pending step (i.e. the warning is non-blocking).
        """
        engine, state = _started_state(tmp_path, status="running")
        self._wire_bead_store(engine)

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.executor"):
            action = engine._drive_resolver_loop(state)

        # The loop must return a dispatch action (step is still pending).
        assert action.action_type in (ActionType.DISPATCH, ActionType.DISPATCH.value), (
            f"Expected DISPATCH action for running state with pending step, "
            f"got {action.action_type}"
        )

        # Warning must have been emitted before the resolver ran.
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Bead conflict" in msg for msg in warning_msgs), (
            f"Expected 'Bead conflict' warning in log; got: {warning_msgs}"
        )


# ---------------------------------------------------------------------------
# Test 2 (bd-8083 sub-item 2) — recursion bound on _drive_resolver_loop
#
# ``_drive_resolver_loop`` caps iterations at ``len(state.plan.phases) + 4``.
# When _apply_resolver_decision always returns None (infinite transitive loop),
# the cap MUST raise RuntimeError rather than spinning forever.
# ---------------------------------------------------------------------------


class TestResolverLoopRecursionBound:
    """bd-8083 sub-item 2: RuntimeError raised after max_iter iterations."""

    def test_runtime_error_after_max_iter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monkeypatching _apply_resolver_decision to always return None
        must cause _drive_resolver_loop to raise RuntimeError after
        ``len(state.plan.phases) + 4`` calls, not spin forever.
        """
        num_phases = 2
        phases = [_phase(phase_id=i) for i in range(num_phases)]
        engine, state = _started_state(tmp_path, phases=phases)

        max_iter = num_phases + 4
        call_count = []

        def _always_none(st: ExecutionState, decision: ResolverDecision):  # noqa: ANN201
            call_count.append(1)
            return None

        monkeypatch.setattr(engine, "_apply_resolver_decision", _always_none)

        with pytest.raises(RuntimeError, match="_drive_resolver_loop exceeded"):
            engine._drive_resolver_loop(state)

        # The loop must have run exactly max_iter times before raising.
        assert len(call_count) == max_iter, (
            f"Expected exactly {max_iter} iterations before RuntimeError, "
            f"got {len(call_count)}"
        )

    def test_max_iter_scales_with_phase_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """max_iter = len(phases) + 4: a plan with more phases has a higher cap."""
        for num_phases in (1, 5, 10):
            phases = [_phase(phase_id=i) for i in range(num_phases)]
            engine, state = _started_state(tmp_path, phases=phases)

            call_count = []

            def _always_none(st: ExecutionState, decision: ResolverDecision):  # noqa: ANN201
                call_count.append(1)
                return None

            monkeypatch.setattr(engine, "_apply_resolver_decision", _always_none)

            with pytest.raises(RuntimeError):
                engine._drive_resolver_loop(state)

            expected = num_phases + 4
            assert len(call_count) == expected, (
                f"For {num_phases} phases: expected {expected} iterations, "
                f"got {len(call_count)}"
            )
            call_count.clear()


# ---------------------------------------------------------------------------
# Test 3 (bd-8083 sub-item 3) — 22-arm dispatch table coverage
#
# _apply_resolver_decision handles all 22 DecisionKind values via an
# if/elif chain.  Assert that every enum member is handled — no silent
# NotImplementedError or unhandled fallthrough.
#
# Strategy: inspect the source of _apply_resolver_decision for explicit
# ``if kind == DecisionKind.X:`` patterns and compare the set against the
# full DecisionKind enum.  This is a static-analysis assertion that
# catches arms added to the enum but not yet wired up in the dispatch
# method.
# ---------------------------------------------------------------------------


class TestApplyResolverDecisionCoversAllKinds:
    """bd-8083 sub-item 3: all 22 DecisionKind values have dispatch arms."""

    def test_dispatch_arms_cover_all_decision_kinds(self) -> None:
        """Every DecisionKind value must appear in _apply_resolver_decision.

        This assertion is intentionally static-analysis style: it reads the
        source of _apply_resolver_decision and extracts the ``DecisionKind.X``
        literals from the ``if kind ==`` arms.  If the enum grows a new member
        that the dispatch method doesn't handle, this test fails before any
        runtime behaviour can be observed.
        """
        import inspect
        import re

        method_src = inspect.getsource(ExecutionEngine._apply_resolver_decision)

        # Extract every ``DecisionKind.SOME_VALUE`` reference in the method body.
        covered = {
            m.group(1)
            for m in re.finditer(r"DecisionKind\.([A-Z_]+)", method_src)
        }

        all_kinds = {member.name for member in DecisionKind}
        missing = all_kinds - covered

        assert not missing, (
            f"_apply_resolver_decision is missing dispatch arms for: {missing!r}. "
            "Each DecisionKind value must have an explicit ``if kind == "
            "DecisionKind.X:`` branch (or be explicitly documented as "
            "intentionally absent via the defensive RuntimeError at the end)."
        )

    def test_decision_kind_has_exactly_23_members(self) -> None:
        """DecisionKind must have exactly 23 members.

        Originally 22 (bd-8083); RETRY_PHASE added for the investigative
        archetype.  If this number changes, the test fails fast so the change
        is reviewed intentionally rather than silently landing an uncovered
        arm.
        """
        assert len(DecisionKind) == 23, (
            f"Expected 23 DecisionKind members, found {len(DecisionKind)}: "
            f"{[d.name for d in DecisionKind]}"
        )
