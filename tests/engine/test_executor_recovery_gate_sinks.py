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


# ===========================================================================
# RW-2 — bounded rework for WS-F001-F002-F098
#
# The three F002 pre-conditions that landed (unknown phase / non-current
# phase / un-run steps) are correct but incomplete, and one of them broke a
# legitimate caller.  The cases below pin the remaining requirements:
#
#   RW-2.1  ``resume_from_takeover`` must still be able to record the gate
#           pass for a step a human finished by hand.
#   RW-2.2  A gate recorded while the engine is parked on ``approval_pending``
#           must be rejected cleanly — today it writes an ExecutionState that
#           violates the I1 invariant, so the state file cannot be re-loaded
#           at all.
#   RW-2.3  A phase with no ``gate`` cannot have a gate row forged onto it.
#
# All assertions below are behavioural: return values, persisted state,
# reloadability, and the next action the orchestrator is handed.
# ===========================================================================

import subprocess

from agent_baton.core.engine.errors import InvalidGateState


def _approval_and_gate_plan(task_id: str) -> MachinePlan:
    """Two phases; phase 1 requires approval *and* carries a test gate."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Approval-pending gate fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                approval_required=True,
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
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


def _gateless_first_phase_plan(task_id: str) -> MachinePlan:
    """Two phases; phase 1 deliberately has **no** gate."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Gateless phase fixture",
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


def _takeover_gate_plan(task_id: str) -> MachinePlan:
    """Two gated phases; phase 1's gate command is a real no-op that exits 0.

    ``true`` lets ``resume_from_takeover`` re-run a genuinely passing gate
    without any subprocess patching, so the test exercises the real code
    path end to end.
    """
    return MachinePlan(
        task_id=task_id,
        task_summary="Takeover resume fixture",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                        model="sonnet",
                        step_type="implementation",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="true"),
            ),
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review it",
                        model="sonnet",
                    ),
                ],
                gate=PlanGate(gate_type="review", command="true"),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# RW-2.2 — a gate recorded while parked on approval_pending
# ---------------------------------------------------------------------------

class TestGateRecordingWhileApprovalPending:
    """``record_gate_result`` must not be able to corrupt the state file.

    Phase 1 both requires approval and carries a test gate, so the engine
    parks on ``APPROVAL`` (status ``approval_pending``,
    ``pending_approval_request`` stamped).  A gate recording arriving in that
    window satisfies all three landed pre-conditions — the phase exists, it
    is current, and its steps are complete — and then advances the phase with
    a raw ``status = "running"`` write that leaves
    ``pending_approval_request`` behind.  That combination violates the I1
    invariant, so the ExecutionState written to disk can never be loaded
    again: the execution is unrecoverable, not merely mis-recorded.
    """

    def _parked_on_approval(self, tmp_path: Path, task_id: str) -> ExecutionEngine:
        plan = _approval_and_gate_plan(task_id)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.APPROVAL, (
            "fixture precondition: the engine must be parked on APPROVAL, got "
            f"{action.action_type}"
        )
        state = engine._load_state()
        assert state.status == "approval_pending"
        assert state.pending_approval_request is not None
        return engine

    def test_gate_pass_while_approval_pending_is_rejected(
        self, tmp_path: Path
    ) -> None:
        engine = self._parked_on_approval(tmp_path, "rw2-approval-pending-pass")

        with pytest.raises(InvalidGateState):
            engine.record_gate_result(phase_id=1, passed=True, exit_code=0)

    def test_gate_pass_while_approval_pending_leaves_state_loadable(
        self, tmp_path: Path
    ) -> None:
        """The damage this guard prevents: an unreadable execution-state file.

        A fresh engine — the next ``baton execute ...`` invocation — must be
        able to load the state after the rejected call.  Today the write
        happens before anything notices, and every later load raises the I1
        ValidationError.
        """
        engine = self._parked_on_approval(
            tmp_path, "rw2-approval-pending-loadable"
        )

        with pytest.raises(InvalidGateState):
            engine.record_gate_result(phase_id=1, passed=True, exit_code=0)

        reader = ExecutionEngine(team_context_root=tmp_path)
        reader._task_id = "rw2-approval-pending-loadable"
        reloaded = reader._load_state()

        assert reloaded is not None
        assert reloaded.status == "approval_pending", (
            "A rejected gate recording must leave the engine parked on the "
            f"approval it was actually waiting for; got {reloaded.status!r}."
        )
        assert reloaded.pending_approval_request is not None
        assert reloaded.gate_results == [], (
            "No GateResult may be recorded for a phase that is still waiting "
            "on its approval."
        )
        assert reloaded.current_phase == 0, (
            "The phase pointer must not advance past a phase whose approval "
            "was never granted."
        )
        assert reader.next_action().action_type == ActionType.APPROVAL, (
            "The pending approval must still be the next thing the "
            "orchestrator is asked to do."
        )

    def test_approval_pending_rejection_uses_its_own_reason_tag(
        self, tmp_path: Path
    ) -> None:
        """Callers map ``reason`` to a status code, so it must be distinct.

        Mirrors ``InvalidApprovalState.REASON_NOT_PENDING``: the API and CLI
        need to tell "you are waiting on an approval, not a gate" apart from
        the three existing gate rejections.
        """
        engine = self._parked_on_approval(tmp_path, "rw2-approval-pending-reason")

        with pytest.raises(InvalidGateState) as exc_info:
            engine.record_gate_result(phase_id=1, passed=True, exit_code=0)

        reason = exc_info.value.reason
        assert reason == InvalidGateState.REASON_NOT_PENDING
        assert reason not in {
            InvalidGateState.REASON_UNKNOWN_PHASE,
            InvalidGateState.REASON_PHASE_MISMATCH,
            InvalidGateState.REASON_STEPS_INCOMPLETE,
        }


# ---------------------------------------------------------------------------
# RW-2.3 — no gate row may be forged onto a gateless phase
# ---------------------------------------------------------------------------

class TestGateRecordingForGatelessPhase:
    """A phase the planner gave no gate has no gate result to record.

    Today the recording is accepted and a ``GateResult`` with
    ``gate_type="unknown"`` is appended — an assurance row for a check that
    was never planned and never ran.  On ``passed=True`` it also advances the
    phase; on ``passed=False`` it fails the whole execution over a gate that
    does not exist.
    """

    def _at_gateless_phase(self, tmp_path: Path, task_id: str) -> ExecutionEngine:
        plan = _gateless_first_phase_plan(task_id)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        state = engine._load_state()
        assert state.current_phase == 0
        assert state.plan.phases[0].gate is None, (
            "fixture precondition: phase 1 must have no gate"
        )
        return engine

    @pytest.mark.parametrize("passed", [True, False])
    def test_gate_recording_for_gateless_phase_is_rejected(
        self, tmp_path: Path, passed: bool
    ) -> None:
        engine = self._at_gateless_phase(
            tmp_path, f"rw2-no-gate-{'pass' if passed else 'fail'}"
        )
        before = _state_fingerprint(engine._load_state())

        with pytest.raises(InvalidGateState):
            engine.record_gate_result(phase_id=1, passed=passed)

        after = engine._load_state()
        assert _state_fingerprint(after) == before, (
            "Recording a gate against a phase that has no gate must leave "
            "status, current_phase, gate_results and step_results untouched."
        )
        assert after.gate_results == [], (
            "A gate row must never be forged onto a phase the plan gave no "
            f"gate; found {[(g.phase_id, g.gate_type) for g in after.gate_results]}."
        )

    def test_gateless_rejection_is_distinguishable_from_the_others(
        self, tmp_path: Path
    ) -> None:
        """The reason tag must not be reused from an existing pre-condition."""
        engine = self._at_gateless_phase(tmp_path, "rw2-no-gate-reason")

        with pytest.raises(InvalidGateState) as exc_info:
            engine.record_gate_result(phase_id=1, passed=True)

        assert exc_info.value.reason not in {
            InvalidGateState.REASON_UNKNOWN_PHASE,
            InvalidGateState.REASON_PHASE_MISMATCH,
            InvalidGateState.REASON_STEPS_INCOMPLETE,
        }, (
            "A gateless phase is its own failure mode — the phase exists, is "
            "current, and its steps are complete."
        )
        assert exc_info.value.phase_id == 1


# ---------------------------------------------------------------------------
# RW-2.1 — the takeover regression the F002 guards introduced
# ---------------------------------------------------------------------------

@pytest.fixture()
def takeover_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit; returns the repo root."""
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


class TestTakeoverResumeCanRecordItsGate:
    """A human who finishes a step by hand must be able to hand it back.

    ``baton execute takeover`` parks the run on ``paused-takeover`` with the
    step still marked ``dispatched`` — that is the whole point: no agent
    completed it.  ``resume_from_takeover`` then re-runs the phase gate and,
    on a pass, records it with ``decision_source="takeover"``.

    The F002 ``steps_incomplete`` pre-condition now rejects that recording,
    so the exception escapes ``resume_from_takeover`` and the run is parked
    on ``WAIT`` with no way forward.  Human takeover is the engine's
    documented escape hatch (``BATON_TAKEOVER_ENABLED``); it must not be the
    one caller the guard breaks.
    """

    def _paused_on_takeover(
        self, ctx_root: Path, task_id: str
    ) -> tuple[ExecutionEngine, Path]:
        engine = ExecutionEngine(team_context_root=ctx_root)
        engine.start(_takeover_gate_plan(task_id))
        engine.mark_dispatched("1.1", "backend-engineer")

        # The agent's attempt failed its gate — the operator takes over.
        state = engine._load_execution()
        assert state is not None
        state.status = "gate_failed"
        engine._save_execution(state)

        record = engine.start_takeover("1.1", reason="human finishes it", pid=0)
        assert record is not None, "fixture precondition: takeover must start"

        state = engine._load_execution()
        assert state.status == "paused-takeover"
        handle = dict(getattr(state, "step_worktrees", {})).get("1.1")
        assert handle is not None, (
            "fixture precondition: takeover needs a real worktree"
        )
        return engine, Path(handle["path"])

    @staticmethod
    def _human_commit(worktree: Path) -> None:
        (worktree / "hand_fix.py").write_text("# fixed by a human\n")
        subprocess.run(
            ["git", "add", "hand_fix.py"], cwd=worktree, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "developer fix"],
            cwd=worktree, check=True, capture_output=True,
        )

    def test_resume_from_takeover_records_the_gate_and_unblocks_the_run(
        self, takeover_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
        monkeypatch.setenv("BATON_TAKEOVER_ENABLED", "1")
        ctx_root = takeover_repo / ".claude" / "team-context"
        ctx_root.mkdir(parents=True, exist_ok=True)

        engine, worktree = self._paused_on_takeover(ctx_root, "rw2-takeover")
        self._human_commit(worktree)

        resumed = engine.resume_from_takeover(
            "1.1", rerun_gate=True, abort=False
        )
        assert resumed is True, (
            "resume_from_takeover must report success when the re-run gate "
            "exits 0 — the human's commit fixed the step."
        )

        state = engine._load_execution()
        assert state is not None
        assert state.status != "paused-takeover", (
            "Once the gate passes the run must leave paused-takeover."
        )

        takeover_gates = [
            g for g in state.gate_results
            if g.phase_id == 1 and g.decision_source == "takeover"
        ]
        assert len(takeover_gates) == 1, (
            "The re-run gate must be recorded against phase 1 with "
            "decision_source='takeover'; got "
            f"{[(g.phase_id, g.passed, g.decision_source) for g in state.gate_results]}."
        )
        assert takeover_gates[0].passed is True
        assert takeover_gates[0].gate_type == "test"

        assert state.current_phase == 1, (
            "A passing takeover gate must advance the phase pointer exactly "
            f"once; current_phase={state.current_phase}."
        )

        follow_up = engine.next_action()
        assert follow_up.action_type not in {ActionType.WAIT, ActionType.FAILED}, (
            "After a successful takeover the run must move forward, not park "
            f"on {follow_up.action_type} ({follow_up.message!r})."
        )

    def test_takeover_record_is_resolved_after_a_passing_resume(
        self, takeover_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The active takeover row must be closed, and the state reloadable.

        ``status == "paused-takeover"`` ⇔ an active record (I9).  If the
        resume aborts partway through, the row stays open while status has
        already moved — a shape the model validator rejects on load.
        """
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
        monkeypatch.setenv("BATON_TAKEOVER_ENABLED", "1")
        ctx_root = takeover_repo / ".claude" / "team-context"
        ctx_root.mkdir(parents=True, exist_ok=True)

        engine, worktree = self._paused_on_takeover(
            ctx_root, "rw2-takeover-record"
        )
        self._human_commit(worktree)

        assert engine.resume_from_takeover("1.1", rerun_gate=True, abort=False) is True

        reader = ExecutionEngine(team_context_root=ctx_root)
        reader._task_id = "rw2-takeover-record"
        reloaded = reader._load_execution()
        assert reloaded is not None

        rows = [
            r for r in reloaded.takeover_records
            if isinstance(r, dict) and r.get("step_id") == "1.1"
        ]
        assert rows, "the takeover row for 1.1 must still be on record"
        assert all(r.get("resumed_at") for r in rows), (
            "A resumed takeover must have its record closed (resumed_at "
            f"stamped); got {rows}."
        )
