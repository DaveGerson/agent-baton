"""Behavioural regression tests for three ExecutionEngine defects.

Workstream WS-F001-F002-F098 — all three defects live in
``agent_baton/core/engine/executor.py``.

F001 — ``resume()`` loads state object A, calls ``recover_dispatched_steps()``
       (which loads/saves a *different* object B), then saves the stale object
       A back over B.  The crash-recovery documented in the root ``CLAUDE.md``
       ("If the session crashes, ``baton execute resume`` picks up from saved
       state") therefore never takes effect: the ``dispatched`` marker is
       re-persisted and the resolver parks the run on ``WAIT`` forever.

F002 — ``record_gate_result()`` validates nothing.  A gate pass recorded for a
       phase that is not current, or for a phase that does not exist at all, or
       for the current phase while its steps have not run, is accepted and
       advances the phase pointer — silently discarding planned work and
       forging the assurance trail.

F098 — the daemon entry point (``ExecutionContext.build``) constructs an
       engine with no storage backend, so usage lands in JSONL, while the CLI
       entry point injects SQLite storage, so usage lands in ``baton.db``.
       The two sinks are disjoint and the learning consumers read only one of
       them, so half of every project's usage history is invisible.

These tests assert observable behaviour only — no assertions on source text.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.improve.scoring import PerformanceScorer
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.runtime.context import ExecutionContext
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.storage import get_project_storage
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------

def _two_step_plan(task_id: str) -> MachinePlan:
    """One phase, two steps, no gate."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Crash recovery fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="test-engineer",
                        task_description="Test it",
                    ),
                ],
            )
        ],
    )


def _gated_two_phase_plan(task_id: str) -> MachinePlan:
    """Two gated phases; phase 1 has two steps, phase 2 has one.

    The two gates deliberately carry *different* ``gate_type`` values so a
    ``GateResult`` stamped from the wrong phase is detectable.
    """
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate validation fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="test-engineer",
                        task_description="Test it",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest -q"),
            ),
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review it",
                    ),
                ],
                gate=PlanGate(gate_type="review", command="baton review"),
            ),
        ],
    )


def _single_step_plan(task_id: str) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Usage sink fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                    ),
                ],
            )
        ],
    )


def _state_fingerprint(state) -> tuple:
    """Fields a rejected gate recording must leave untouched."""
    return (
        state.status,
        state.current_phase,
        tuple(
            (g.phase_id, g.gate_type, g.passed) for g in state.gate_results
        ),
        tuple(sorted((r.step_id, r.status) for r in state.step_results)),
    )


# ---------------------------------------------------------------------------
# F001 — resume() must not re-persist the pre-recovery state
# ---------------------------------------------------------------------------

class TestResumeCrashRecoveryIsDurable:
    """``resume()`` must return a re-dispatch action AND persist the recovery."""

    def test_resume_returns_dispatch_for_the_crashed_step(
        self, tmp_path: Path
    ) -> None:
        """A step stuck in ``dispatched`` must be re-dispatched by ``resume()``.

        ``resume()``'s own return value is the action the orchestrator acts on
        after a crash.  Returning ``WAIT`` here means the run is parked
        forever, because nothing will ever complete the dead step.
        """
        plan = _two_step_plan("f001-resume-return")
        engine = ExecutionEngine(team_context_root=tmp_path)
        assert engine.start(plan).action_type == ActionType.DISPATCH

        # Crash simulation: the step was dispatched, the agent process died,
        # and no result was ever recorded.
        engine.mark_dispatched(step_id="1.1", agent_name="backend-engineer")
        engine.mark_dispatched(step_id="1.2", agent_name="test-engineer")

        resumed = ExecutionEngine(team_context_root=tmp_path)
        resumed._task_id = plan.task_id
        action = resumed.resume()

        assert action.action_type == ActionType.DISPATCH, (
            "resume() must hand back a DISPATCH action for a step whose agent "
            f"died mid-flight, got {action.action_type} "
            f"({action.message!r}). The run is parked forever otherwise."
        )
        assert action.step_id in {"1.1", "1.2"}

    def test_resume_persists_the_recovery_it_performed(
        self, tmp_path: Path
    ) -> None:
        """The cleared ``dispatched`` markers must survive ``resume()``'s save.

        A third, independent engine reads the state from disk — if ``resume()``
        writes a pre-recovery snapshot back over the recovered one, the stale
        ``dispatched`` marker reappears here and every later ``resume`` repeats
        the same corruption.
        """
        plan = _two_step_plan("f001-resume-persist")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        engine.mark_dispatched(step_id="1.2", agent_name="test-engineer")

        resumed = ExecutionEngine(team_context_root=tmp_path)
        resumed._task_id = plan.task_id
        resumed.resume()

        reader = ExecutionEngine(team_context_root=tmp_path)
        reader._task_id = plan.task_id
        persisted = reader._load_state()
        assert persisted is not None

        stuck = {
            r.step_id for r in persisted.step_results if r.status == "dispatched"
        }
        assert stuck == set(), (
            "resume() must durably clear stale 'dispatched' markers; found "
            f"{sorted(stuck)} still persisted after resume()."
        )
        completed = {
            r.step_id for r in persisted.step_results if r.status == "complete"
        }
        assert "1.1" in completed, (
            "Crash recovery must not discard already-completed step results."
        )

        # And the recovered step is offered for dispatch again.
        follow_up = reader.next_action()
        assert follow_up.action_type == ActionType.DISPATCH
        assert follow_up.step_id == "1.2"


# ---------------------------------------------------------------------------
# F002 — record_gate_result() must validate before mutating
# ---------------------------------------------------------------------------

class TestGateResultValidation:
    """A gate pass must be rejected unless it genuinely applies."""

    def _started(self, tmp_path: Path, task_id: str):
        plan = _gated_two_phase_plan(task_id)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        return engine

    def test_gate_pass_for_unknown_phase_is_rejected(
        self, tmp_path: Path
    ) -> None:
        engine = self._started(tmp_path, "f002-unknown-phase")
        before = _state_fingerprint(engine._load_state())

        with pytest.raises(RuntimeError):
            engine.record_gate_result(phase_id=99, passed=True)

        assert _state_fingerprint(engine._load_state()) == before, (
            "A gate recorded against a phase_id that is not in the plan must "
            "leave status, current_phase, gate_results and step_results "
            "unchanged."
        )

    def test_gate_pass_for_non_current_phase_is_rejected(
        self, tmp_path: Path
    ) -> None:
        engine = self._started(tmp_path, "f002-wrong-phase")
        before = _state_fingerprint(engine._load_state())

        # Phase 1 is current; recording phase 2's gate must not be accepted.
        with pytest.raises(RuntimeError):
            engine.record_gate_result(phase_id=2, passed=True)

        assert _state_fingerprint(engine._load_state()) == before, (
            "A gate recorded against a phase other than the current one must "
            "not mutate execution state."
        )

    def test_gate_pass_cannot_skip_unrun_steps(self, tmp_path: Path) -> None:
        """A pass for the current phase while its steps are un-run is invalid.

        This is the accident the engine exists to prevent: one mistyped
        ``baton execute gate --phase-id 1 --result pass`` immediately after
        start would otherwise discard steps 1.1 and 1.2 permanently.
        """
        engine = self._started(tmp_path, "f002-skip-steps")
        before = _state_fingerprint(engine._load_state())

        with pytest.raises(RuntimeError):
            engine.record_gate_result(phase_id=1, passed=True)

        after_state = engine._load_state()
        assert _state_fingerprint(after_state) == before, (
            "Passing the gate of a phase whose steps have not run must not "
            "advance the phase pointer or record a GateResult."
        )
        # The un-run steps must still be offered for dispatch.
        assert engine.next_action().action_type == ActionType.DISPATCH

    def test_gate_pass_after_all_steps_complete_advances_one_phase(
        self, tmp_path: Path
    ) -> None:
        """The happy path must keep working, with provenance from that phase."""
        engine = self._started(tmp_path, "f002-happy-path")
        for step_id, agent in (("1.1", "backend-engineer"), ("1.2", "test-engineer")):
            engine.record_step_result(
                step_id=step_id,
                agent_name=agent,
                status="complete",
                outcome="done",
            )

        gate_action = engine.next_action()
        assert gate_action.action_type == ActionType.GATE

        before_phase = engine._load_state().current_phase
        engine.record_gate_result(phase_id=1, passed=True, exit_code=0)

        state = engine._load_state()
        assert state.current_phase == before_phase + 1, (
            "A valid gate pass must advance exactly one phase."
        )
        assert len(state.gate_results) == 1
        recorded = state.gate_results[0]
        assert recorded.phase_id == 1
        assert recorded.passed is True
        assert recorded.gate_type == "test", (
            "GateResult.gate_type must come from the phase that was recorded, "
            f"not from whatever phase happens to be current; got "
            f"{recorded.gate_type!r} (phase 2's gate_type is 'review')."
        )
        assert engine.next_action().step_id == "2.1"


# ---------------------------------------------------------------------------
# F098 — one team-context root must have one usage history
# ---------------------------------------------------------------------------

def _run_task_to_completion(engine: ExecutionEngine, plan: MachinePlan) -> None:
    engine.start(plan)
    engine.record_step_result(
        step_id="1.1",
        agent_name="backend-engineer",
        status="complete",
        outcome="done",
        estimated_tokens=12_000,
    )
    engine.complete()


class TestUsageSinksAreNotSplit:
    """Daemon-path and CLI-path runs must share one observable usage history."""

    def test_daemon_built_engine_records_usage_in_project_storage(
        self, tmp_path: Path
    ) -> None:
        """A run driven through ``ExecutionContext.build`` must reach SQLite.

        ``baton chargeback``, ``baton aibom``, attribution coverage and
        ``/api/v1/metrics`` all read ``usage_records`` from ``baton.db``.  A
        daemon-driven run that never lands there is billed work that reports
        as zero cost.
        """
        root = tmp_path / "team-context"
        root.mkdir(parents=True)

        ctx = ExecutionContext.build(
            launcher=DryRunLauncher(),
            team_context_root=root,
            task_id="daemon-task",
        )
        _run_task_to_completion(ctx.engine, _single_step_plan("daemon-task"))

        stored = {r.task_id for r in get_project_storage(root).read_usage()}
        assert "daemon-task" in stored, (
            "A task completed through the daemon entry point is missing from "
            f"the project's usage_records table; found {sorted(stored)}."
        )

    def test_learning_consumers_see_both_entry_points(
        self, tmp_path: Path
    ) -> None:
        """One agent used by a daemon run and a CLI run must count twice.

        ``PerformanceScorer`` (and its siblings ``BudgetTuner`` /
        ``PatternLearner``) is the learn/improve pillar's only view of usage.
        If it sees only the entry point that happened to write its sink, the
        whole loop learns from half the data without any error.
        """
        root = tmp_path / "team-context"
        root.mkdir(parents=True)

        # Entry point 1: the daemon / worker supervisor path.
        ctx = ExecutionContext.build(
            launcher=DryRunLauncher(),
            team_context_root=root,
            task_id="daemon-task",
        )
        _run_task_to_completion(ctx.engine, _single_step_plan("daemon-task"))

        # Entry point 2: the CLI path, which injects the SQLite backend.
        cli_engine = ExecutionEngine(
            team_context_root=root,
            task_id="cli-task",
            storage=get_project_storage(root),
        )
        _run_task_to_completion(cli_engine, _single_step_plan("cli-task"))

        scorer = PerformanceScorer(
            usage_logger=UsageLogger(root / "usage-log.jsonl"),
            retro_engine=RetrospectiveEngine(
                retrospectives_dir=root / "retrospectives"
            ),
        )
        card = scorer.score_agent("backend-engineer")
        assert card.times_used == 2, (
            "backend-engineer ran once via the daemon entry point and once via "
            "the CLI entry point against the same team-context root, but the "
            f"learning consumer only sees {card.times_used} of those runs."
        )
