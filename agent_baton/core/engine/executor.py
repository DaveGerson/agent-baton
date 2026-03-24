"""Execution engine — state machine that drives orchestrated task execution.

The engine is called repeatedly by the driving session (Claude or CLI).
Each call either advances the state or returns an action for the caller to
perform.  State is persisted to disk between calls for crash recovery.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanStep,
    StepResult,
)
from agent_baton.models.events import Event
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _elapsed_seconds(started_at: str) -> float:
    """Return elapsed wall-clock seconds since started_at (ISO string)."""
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.now(tz=timezone.utc)
        # Make start timezone-aware if it isn't already.
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0.0, (now - start).total_seconds())
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# ExecutionEngine
# ---------------------------------------------------------------------------

class ExecutionEngine:
    """State machine that drives orchestrated task execution.

    The engine is called repeatedly by the driving session (Claude or CLI).
    Each call either advances the state or returns an action for the caller
    to perform.  State is persisted to disk between calls for crash recovery.

    Typical lifecycle::

        engine = ExecutionEngine(team_context_root=Path(".claude/team-context"))
        action = engine.start(plan)           # ActionType.DISPATCH

        while True:
            if action.action_type == ActionType.DISPATCH.value:
                # caller spawns agent, then:
                engine.record_step_result(action.step_id, action.agent_name, ...)
                action = engine.next_action()
            elif action.action_type == ActionType.GATE.value:
                # caller runs gate check, then:
                engine.record_gate_result(action.phase_id, passed=True)
                action = engine.next_action()
            elif action.action_type == ActionType.COMPLETE.value:
                summary = engine.complete()
                break
            elif action.action_type == ActionType.FAILED.value:
                break
    """

    _STATE_FILENAME = "execution-state.json"
    _DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")

    def __init__(
        self,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._root = team_context_root or self._DEFAULT_CONTEXT_ROOT
        self._bus = bus
        # If bus provided, auto-wire persistence as a subscriber.
        if self._bus is not None:
            self._event_persistence = EventPersistence(
                events_dir=self._root / "events"
            )
            self._bus.subscribe("*", self._event_persistence.append)
        else:
            self._event_persistence = None
        self._tracer = TraceRecorder(team_context_root=self._root)
        self._usage_logger = UsageLogger(
            log_path=self._root / "usage-log.jsonl"
        )
        self._retro_engine = RetrospectiveEngine(
            retrospectives_dir=self._root / "retrospectives"
        )
        # In-memory trace object, populated during start() / resume().
        self._trace = None

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, plan: MachinePlan) -> ExecutionAction:
        """Initialize execution from a *plan*.

        - Creates :class:`ExecutionState`
        - Starts a trace via :class:`TraceRecorder`
        - Saves state to disk
        - Returns the first action (DISPATCH for the first step, or COMPLETE
          if the plan has no phases/steps)
        """
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            current_phase=0,
            current_step_index=0,
            status="running",
        )

        # Initialise trace (in-memory; committed to disk on complete()).
        self._trace = self._tracer.start_trace(
            task_id=plan.task_id,
            plan_snapshot=plan.to_dict(),
        )

        self._publish(evt.task_started(
            task_id=plan.task_id,
            task_summary=plan.task_summary,
            risk_level=plan.risk_level,
            total_steps=plan.total_steps,
        ))
        if plan.phases:
            first_phase = plan.phases[0]
            self._publish(evt.phase_started(
                task_id=plan.task_id,
                phase_id=first_phase.phase_id,
                phase_name=first_phase.name,
                step_count=len(first_phase.steps),
            ))

        self._save_state(state)
        return self._determine_action(state)

    def next_action(self) -> ExecutionAction:
        """Determine and return the next action based on current state.

        Logic (in priority order):

        1. Load state from disk.
        2. If status is already *failed* or *complete*, return the
           corresponding terminal action immediately.
        3. If status is *gate_pending*, return a GATE action for the current
           phase.
        4. Walk the current phase:
           a. If a step failed → return FAILED.
           b. If all steps are complete and there is a gate → return GATE.
           c. If all steps are complete and gate passed (or no gate) →
              advance to next phase.
           d. If steps remain → return DISPATCH for the next pending step.
        5. If all phases are exhausted → return COMPLETE.
        6. Save state before returning any mutable action.
        """
        state = self._load_state()
        if state is None:
            return ExecutionAction(
                action_type=ActionType.FAILED.value,
                message="No active execution state found. Call start() first.",
                summary="No execution state on disk.",
            )

        action = self._determine_action(state)
        self._save_state(state)
        return action

    def next_actions(self) -> list[ExecutionAction]:
        """Return ALL currently dispatchable actions for parallel execution.

        Unlike :meth:`next_action` which returns a single action, this method
        returns every step whose dependencies are satisfied and that has not
        yet been dispatched, completed, or failed.  The caller can spawn all
        returned agents in parallel.

        Returns an empty list if no steps are dispatchable (caller should
        check :meth:`next_action` for WAIT / GATE / COMPLETE / FAILED).
        """
        state = self._load_state()
        if state is None:
            return []

        if state.status in ("complete", "failed", "gate_pending"):
            return []

        if state.current_phase >= len(state.plan.phases):
            return []

        phase_obj = state.current_phase_obj
        if phase_obj is None or not phase_obj.steps:
            return []

        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        occupied = completed | state.failed_step_ids | dispatched

        actions: list[ExecutionAction] = []
        for step in phase_obj.steps:
            if step.step_id in occupied:
                continue
            if step.depends_on and not all(
                dep in completed for dep in step.depends_on
            ):
                continue
            actions.append(self._dispatch_action(step, state))

        return actions

    def record_step_result(
        self,
        step_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
        commit_hash: str = "",
        estimated_tokens: int = 0,
        duration_seconds: float = 0.0,
        error: str = "",
    ) -> None:
        """Record the result of a step execution.

        - Creates :class:`StepResult` and appends to state.
        - Emits trace events (``agent_complete`` or ``agent_failed``).
        - Saves state to disk.
        """
        _VALID_STEP_STATUSES = {"complete", "failed", "dispatched"}
        if status not in _VALID_STEP_STATUSES:
            raise ValueError(
                f"Invalid step status '{status}'. Must be one of: {_VALID_STEP_STATUSES}"
            )

        state = self._load_state()
        if state is None:
            raise RuntimeError(
                "record_step_result() called with no active execution state."
            )

        result = StepResult(
            step_id=step_id,
            agent_name=agent_name,
            status=status,
            outcome=outcome,
            files_changed=files_changed or [],
            commit_hash=commit_hash,
            estimated_tokens=estimated_tokens,
            duration_seconds=duration_seconds,
            error=error,
            completed_at=_utcnow(),
        )
        state.step_results.append(result)

        # Determine phase + step index for trace context.
        phase_idx, step_idx = self._locate_step(state, step_id)

        # Emit trace event.
        if self._trace is not None:
            event_type = "agent_complete" if status == "complete" else "agent_failed"
            self._tracer.record_event(
                self._trace,
                event_type,
                agent_name=agent_name,
                phase=phase_idx + 1,
                step=step_idx + 1,
                details={
                    "step_id": step_id,
                    "outcome": outcome,
                    "commit_hash": commit_hash,
                    "files_changed": files_changed or [],
                    "error": error,
                },
                duration_seconds=duration_seconds if duration_seconds else None,
            )

        self._save_state(state)

    def mark_dispatched(self, step_id: str, agent_name: str) -> None:
        """Record that a step has been dispatched (in-flight, not yet complete).

        This allows the engine to track which steps are currently running
        so it can correctly determine what to dispatch next in parallel
        execution scenarios.
        """
        self.record_step_result(
            step_id=step_id,
            agent_name=agent_name,
            status="dispatched",
        )

    def record_gate_result(
        self,
        phase_id: int,
        passed: bool,
        output: str = "",
    ) -> None:
        """Record the result of a QA gate check.

        - Creates :class:`GateResult` and appends to state.
        - Emits a ``gate_result`` trace event.
        - If *failed*: sets state status to ``failed``.
        - If *passed*: advances the phase pointer and resets step index.
        - Saves state.
        """
        state = self._load_state()
        if state is None:
            raise RuntimeError(
                "record_gate_result() called with no active execution state."
            )

        phase_obj = state.current_phase_obj
        gate_type = phase_obj.gate.gate_type if (phase_obj and phase_obj.gate) else "unknown"

        gate_result = GateResult(
            phase_id=phase_id,
            gate_type=gate_type,
            passed=passed,
            output=output,
            checked_at=_utcnow(),
        )
        state.gate_results.append(gate_result)

        # Emit trace event.
        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "gate_result",
                agent_name=None,
                phase=phase_id + 1,
                step=0,
                details={
                    "gate_type": gate_type,
                    "result": "PASS" if passed else "FAIL",
                    "output": output,
                },
            )

        if not passed:
            self._publish(evt.gate_failed(
                task_id=state.task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                output=output,
            ))
            state.status = "failed"
        else:
            self._publish(evt.gate_passed(
                task_id=state.task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                output=output,
            ))
            # Advance to next phase.  current_phase is a 0-based index into
            # plan.phases, whereas phase_id is a 1-based identifier — so we
            # must increment the index, not derive it from phase_id.
            state.current_phase += 1
            state.current_step_index = 0
            state.status = "running"

        self._save_state(state)

    def complete(self) -> str:
        """Finalise execution.

        - Sets state to ``complete``.
        - Completes the trace via :class:`TraceRecorder`.
        - Writes a :class:`TaskUsageRecord` via :class:`UsageLogger`.
        - Generates and writes a retrospective via :class:`RetrospectiveEngine`.
        - Returns a human-readable completion summary string.
        """
        state = self._load_state()
        if state is None:
            return "No active execution state found."

        state.status = "complete"
        state.completed_at = _utcnow()
        self._save_state(state)

        # Finalise trace.
        trace_path: Path | None = None
        if self._trace is not None:
            trace_path = self._tracer.complete_trace(self._trace, outcome="SHIP")
            self._trace = None

        # Build and log usage record.
        usage_record = self._build_usage_record(state)
        self._usage_logger.log(usage_record)

        # Build and save retrospective.
        retro_data = self._build_retrospective_data(state)
        retro = self._retro_engine.generate_from_usage(
            usage=usage_record,
            task_name=retro_data.get("task_name", state.plan.task_summary),
        )
        retro_path = self._retro_engine.save(retro)

        # Compose summary string.
        steps_done = len(state.completed_step_ids)
        gates_passed = sum(1 for g in state.gate_results if g.passed)
        elapsed = _elapsed_seconds(state.started_at)

        self._publish(evt.task_completed(
            task_id=state.task_id,
            steps_completed=steps_done,
            gates_passed=gates_passed,
            elapsed_seconds=elapsed,
        ))

        summary_lines = [
            f"Task {state.task_id} completed.",
            f"Steps: {steps_done}/{state.plan.total_steps}",
            f"Gates passed: {gates_passed}",
            f"Elapsed: {int(elapsed)}s",
        ]
        if trace_path:
            summary_lines.append(f"Trace: {trace_path}")
        summary_lines.append(f"Retrospective: {retro_path}")
        return "\n".join(summary_lines)

    def status(self) -> dict:
        """Return current execution status as a dict.

        Keys: ``task_id``, ``status``, ``current_phase``, ``steps_complete``,
        ``steps_total``, ``gates_passed``, ``gates_failed``,
        ``elapsed_seconds``.
        """
        state = self._load_state()
        if state is None:
            return {"status": "no_active_execution"}

        gates_passed = sum(1 for g in state.gate_results if g.passed)
        gates_failed = sum(1 for g in state.gate_results if not g.passed)

        return {
            "task_id": state.task_id,
            "status": state.status,
            "current_phase": state.current_phase,
            "steps_complete": len(state.completed_step_ids),
            "steps_total": state.plan.total_steps,
            "gates_passed": gates_passed,
            "gates_failed": gates_failed,
            "elapsed_seconds": _elapsed_seconds(state.started_at),
        }

    def resume(self) -> ExecutionAction:
        """Resume from a saved state (crash recovery).

        - Loads state from disk.
        - Determines where execution left off.
        - Returns the appropriate next action.
        """
        state = self._load_state()
        if state is None:
            return ExecutionAction(
                action_type=ActionType.FAILED.value,
                message="No execution state found on disk. Cannot resume.",
                summary="No execution state on disk.",
            )

        # Reconnect the in-memory trace if one exists on disk; otherwise
        # start a fresh trace continuation so subsequent events are recorded.
        if self._trace is None:
            existing = self._tracer.load_trace(state.task_id)
            if existing is not None:
                self._trace = existing
            else:
                self._trace = self._tracer.start_trace(
                    task_id=state.task_id,
                    plan_snapshot=state.plan.to_dict(),
                )

        return self._determine_action(state)

    def recover_dispatched_steps(self) -> int:
        """Clear stale dispatched-step markers for crash recovery.

        After a daemon crash, steps in ``dispatched`` status have no running
        agent process.  This method removes their ``StepResult`` entries so
        the engine will re-dispatch them on the next ``next_action()`` call.

        Returns the number of recovered (re-dispatchable) steps.
        """
        state = self._load_state()
        if state is None:
            return 0

        original_count = len(state.step_results)
        state.step_results = [
            r for r in state.step_results if r.status != "dispatched"
        ]
        recovered = original_count - len(state.step_results)

        if recovered > 0:
            self._save_state(state)

        return recovered

    # ── Internal helpers ────────────────────────────────────────────────────

    # Event ownership: Engine publishes task-level and phase-level events.
    # Step-level events (step.dispatched, step.completed, step.failed) are
    # published by the runtime layer (TaskWorker) to avoid duplication.

    def _publish(self, event: Event) -> None:
        """Publish an event if a bus is configured."""
        if self._bus is not None:
            self._bus.publish(event)

    def _save_state(self, state: ExecutionState) -> Path:
        """Write *state* to ``.claude/team-context/execution-state.json``.

        Uses a tmp+rename pattern so a crash mid-write cannot corrupt the
        state file.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / self._STATE_FILENAME
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.rename(str(tmp_path), str(path))
        return path

    def _load_state(self) -> ExecutionState | None:
        """Read state from disk; return ``None`` if no active execution."""
        path = self._root / self._STATE_FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExecutionState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def _build_usage_record(self, state: ExecutionState) -> TaskUsageRecord:
        """Convert *state* into a :class:`TaskUsageRecord` for the usage logger."""
        # Aggregate per-agent metrics from step results.
        agent_map: dict[str, AgentUsageRecord] = {}

        for result in state.step_results:
            name = result.agent_name
            if name not in agent_map:
                # Determine model from the plan step if available.
                model = _model_for_step(state.plan, result.step_id)
                agent_map[name] = AgentUsageRecord(
                    name=name,
                    model=model,
                    steps=0,
                    retries=0,
                    gate_results=[],
                    estimated_tokens=0,
                    duration_seconds=0.0,
                )
            rec = agent_map[name]
            rec.steps += 1
            rec.estimated_tokens += result.estimated_tokens
            rec.duration_seconds += result.duration_seconds
            rec.retries += result.retries

        # Attach gate results to agents — associate gates with the agents in
        # the corresponding phase (best-effort; use gate PASS/FAIL strings).
        for gate in state.gate_results:
            gate_str = "PASS" if gate.passed else "FAIL"
            phase_agents = _agents_in_phase(state.plan, gate.phase_id)
            for agent_name in phase_agents:
                if agent_name in agent_map:
                    agent_map[agent_name].gate_results.append(gate_str)

        gates_passed = sum(1 for g in state.gate_results if g.passed)
        gates_failed = sum(1 for g in state.gate_results if not g.passed)
        outcome = "SHIP" if state.status == "complete" else (
            "BLOCK" if state.status == "failed" else ""
        )

        return TaskUsageRecord(
            task_id=state.task_id,
            timestamp=state.completed_at or _utcnow(),
            agents_used=list(agent_map.values()),
            total_agents=len(agent_map),
            risk_level=state.plan.risk_level,
            sequencing_mode=state.plan.execution_mode,
            gates_passed=gates_passed,
            gates_failed=gates_failed,
            outcome=outcome,
        )

    def _build_retrospective_data(self, state: ExecutionState) -> dict:
        """Build a data dict used when generating a retrospective."""
        return {
            "task_name": state.plan.task_summary,
            "task_id": state.task_id,
            "status": state.status,
        }

    # ── State machine logic ─────────────────────────────────────────────────

    def _determine_action(self, state: ExecutionState) -> ExecutionAction:
        """Core state machine — inspect *state* and return the next action.

        This method is the single source of truth for what comes next.
        It does NOT mutate *state* itself; callers are responsible for saving.
        """
        # Terminal states — report immediately.
        if state.status == "complete":
            return ExecutionAction(
                action_type=ActionType.COMPLETE.value,
                message=f"Task {state.task_id} is already complete.",
                summary=f"Task {state.task_id} completed.",
            )
        if state.status == "failed":
            failed_ids = list(state.failed_step_ids)
            msg = f"Execution failed. Failed step(s): {', '.join(failed_ids) or 'gate'}"
            return ExecutionAction(
                action_type=ActionType.FAILED.value,
                message=msg,
                summary=msg,
            )

        # gate_pending — a gate was requested but result not yet recorded.
        if state.status == "gate_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                return ExecutionAction(
                    action_type=ActionType.GATE.value,
                    message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )

        # No more phases — all done.
        if state.current_phase >= len(state.plan.phases):
            return ExecutionAction(
                action_type=ActionType.COMPLETE.value,
                message=f"All phases of task {state.task_id} are complete.",
                summary=f"Task {state.task_id} completed successfully.",
            )

        phase_obj = state.current_phase_obj
        if phase_obj is None:
            return ExecutionAction(
                action_type=ActionType.COMPLETE.value,
                message="No more phases.",
                summary=f"Task {state.task_id} completed.",
            )

        steps = phase_obj.steps

        # If phase has no steps, go straight to gate or next phase.
        if not steps:
            if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
                state.status = "gate_pending"
                return ExecutionAction(
                    action_type=ActionType.GATE.value,
                    message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )
            # Advance past empty phase with no gate (or gate already done).
            self._publish(evt.phase_completed(
                task_id=state.task_id,
                phase_id=phase_obj.phase_id,
                phase_name=phase_obj.name,
            ))
            state.current_phase += 1
            state.current_step_index = 0
            if state.current_phase < len(state.plan.phases):
                next_phase = state.plan.phases[state.current_phase]
                self._publish(evt.phase_started(
                    task_id=state.task_id,
                    phase_id=next_phase.phase_id,
                    phase_name=next_phase.name,
                    step_count=len(next_phase.steps),
                ))
            return self._determine_action(state)

        # Check for any failed steps in this phase.
        for step in steps:
            if step.step_id in state.failed_step_ids:
                state.status = "failed"
                msg = f"Step {step.step_id} failed."
                return ExecutionAction(
                    action_type=ActionType.FAILED.value,
                    message=msg,
                    summary=msg,
                )

        # Find the next dispatchable step — must not be completed, failed, or
        # already dispatched, and all dependencies must be satisfied.
        completed = state.completed_step_ids
        dispatched = state.dispatched_step_ids
        occupied = completed | state.failed_step_ids | dispatched

        next_step: PlanStep | None = None
        for step in steps:
            if step.step_id in occupied:
                continue
            # Check dependency satisfaction: all depends_on must be completed.
            if step.depends_on and not all(
                dep in completed for dep in step.depends_on
            ):
                continue
            next_step = step
            break

        if next_step is not None:
            # There is still work to do in this phase.
            return self._dispatch_action(next_step, state)

        # If no step is dispatchable but some are still pending (dispatched or
        # blocked by dependencies), return WAIT.
        pending = {s.step_id for s in steps} - completed - state.failed_step_ids
        if pending:
            return ExecutionAction(
                action_type=ActionType.WAIT.value,
                message="Waiting for in-flight steps to complete before proceeding.",
                summary=f"Steps in flight or blocked: {', '.join(sorted(pending))}",
            )

        # All steps in this phase are complete.
        if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
            state.status = "gate_pending"
            return ExecutionAction(
                action_type=ActionType.GATE.value,
                message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                gate_type=phase_obj.gate.gate_type,
                gate_command=phase_obj.gate.command,
                phase_id=phase_obj.phase_id,
            )

        # Gate passed (or no gate) — move to next phase.
        self._publish(evt.phase_completed(
            task_id=state.task_id,
            phase_id=phase_obj.phase_id,
            phase_name=phase_obj.name,
        ))
        state.current_phase += 1
        state.current_step_index = 0
        state.status = "running"
        if state.current_phase < len(state.plan.phases):
            next_phase = state.plan.phases[state.current_phase]
            self._publish(evt.phase_started(
                task_id=state.task_id,
                phase_id=next_phase.phase_id,
                phase_name=next_phase.name,
                step_count=len(next_phase.steps),
            ))
        return self._determine_action(state)

    def _dispatch_action(self, step: PlanStep, state: ExecutionState) -> ExecutionAction:
        """Build a DISPATCH action for *step*."""
        prompt = _build_delegation_prompt(step, state.plan)
        enforcement = PromptDispatcher.build_path_enforcement(step)
        return ExecutionAction(
            action_type=ActionType.DISPATCH.value,
            message=f"Dispatch agent '{step.agent_name}' for step {step.step_id}.",
            agent_name=step.agent_name,
            agent_model=step.model,
            delegation_prompt=prompt,
            step_id=step.step_id,
            path_enforcement=enforcement or "",
        )

    @staticmethod
    def _gate_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
        """Return True if a passing gate result exists for *phase_id*."""
        for g in state.gate_results:
            if g.phase_id == phase_id and g.passed:
                return True
        return False

    @staticmethod
    def _locate_step(state: ExecutionState, step_id: str) -> tuple[int, int]:
        """Return (phase_index, step_index) for *step_id* in the plan.

        Returns (-1, -1) if not found.
        """
        for pi, phase in enumerate(state.plan.phases):
            for si, step in enumerate(phase.steps):
                if step.step_id == step_id:
                    return pi, si
        return -1, -1


# ---------------------------------------------------------------------------
# Private utilities (module-level to keep the class lean)
# ---------------------------------------------------------------------------

def _build_delegation_prompt(step: PlanStep, plan: MachinePlan) -> str:
    """Build a minimal delegation prompt for a plan step."""
    lines = [
        f"# Agent Task: {step.step_id}",
        "",
        f"**Task**: {step.task_description}",
    ]
    if plan.shared_context:
        lines += ["", "## Shared Context", plan.shared_context]
    if step.context_files:
        lines += ["", "## Read these files first"]
        for cf in step.context_files:
            lines.append(f"- {cf}")
    if step.deliverables:
        lines += ["", "## Deliverables"]
        for d in step.deliverables:
            lines.append(f"- {d}")
    if step.allowed_paths:
        lines += ["", f"**Allowed paths**: {', '.join(step.allowed_paths)}"]
    if step.blocked_paths:
        lines += [f"**Blocked paths**: {', '.join(step.blocked_paths)}"]
    lines += [
        "",
        "Read `.claude/team-context/context.md` for shared project context.",
    ]
    return "\n".join(lines)


def _model_for_step(plan: MachinePlan, step_id: str) -> str:
    """Look up the model declared for *step_id* in *plan*."""
    for phase in plan.phases:
        for step in phase.steps:
            if step.step_id == step_id:
                return step.model
    return "sonnet"


def _agents_in_phase(plan: MachinePlan, phase_id: int) -> list[str]:
    """Return unique agent names for steps in *phase_id*."""
    seen: set[str] = set()
    result: list[str] = []
    for phase in plan.phases:
        if phase.phase_id == phase_id:
            for step in phase.steps:
                if step.agent_name not in seen:
                    result.append(step.agent_name)
                    seen.add(step.agent_name)
    return result
