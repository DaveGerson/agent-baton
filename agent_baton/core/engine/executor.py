"""Execution engine — state machine that drives orchestrated task execution.

The engine is called repeatedly by the driving session (Claude or CLI).
Each call either advances the state or returns an action for the caller to
perform.  State is persisted to disk between calls for crash recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.execution import (
    ActionType,
    ApprovalResult,
    ExecutionAction,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    TeamStepResult,
)
from agent_baton.models.events import Event
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent
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

    _DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")

    def __init__(
        self,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._root = (team_context_root or self._DEFAULT_CONTEXT_ROOT).resolve()
        self._persistence = StatePersistence(self._root)
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
        self._telemetry = AgentTelemetry(
            log_path=self._root / "telemetry.jsonl"
        )
        self._retro_engine = RetrospectiveEngine(
            retrospectives_dir=self._root / "retrospectives"
        )
        # Wire telemetry as a catch-all EventBus subscriber so every domain
        # event is captured in the telemetry log.
        if self._bus is not None:
            self._bus.subscribe("*", self._on_event_for_telemetry)
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

        try:
            self._telemetry.log_event(TelemetryEvent(
                timestamp=_utcnow(),
                agent_name="engine",
                event_type="execution_started",
                details=f"task_id={plan.task_id} risk={plan.risk_level}",
            ))
        except Exception:
            pass

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

        self._persistence.save(state)
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
        state = self._persistence.load()
        if state is None:
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message="No active execution state found. Call start() first.",
                summary="No execution state on disk.",
            )

        action = self._determine_action(state)
        self._persistence.save(state)
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
        state = self._persistence.load()
        if state is None:
            return []

        if state.status in ("complete", "failed", "gate_pending", "approval_pending"):
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

        state = self._persistence.load()
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

        # Log telemetry event for this step.
        try:
            tel_event_type = (
                "step_completed" if status == "complete" else "step_failed"
            )
            duration_ms = int(duration_seconds * 1000)
            file_path = files_changed[0] if files_changed else ""
            self._telemetry.log_event(TelemetryEvent(
                timestamp=_utcnow(),
                agent_name=agent_name,
                event_type=tel_event_type,
                duration_ms=duration_ms,
                file_path=file_path,
                details=f"step_id={step_id} outcome={outcome}" + (
                    f" error={error}" if error else ""
                ),
            ))
        except Exception:
            pass

        self._persistence.save(state)

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
        state = self._persistence.load()
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

        # Log telemetry event for this gate.
        try:
            self._telemetry.log_event(TelemetryEvent(
                timestamp=_utcnow(),
                agent_name="engine",
                event_type="gate_passed" if passed else "gate_failed",
                details=f"phase_id={phase_id} gate_type={gate_type}",
            ))
        except Exception:
            pass

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

        self._persistence.save(state)

    def complete(self) -> str:
        """Finalise execution.

        - Sets state to ``complete``.
        - Completes the trace via :class:`TraceRecorder`.
        - Writes a :class:`TaskUsageRecord` via :class:`UsageLogger`.
        - Generates and writes a retrospective via :class:`RetrospectiveEngine`.
        - Returns a human-readable completion summary string.
        """
        state = self._persistence.load()
        if state is None:
            return "No active execution state found."

        state.status = "complete"
        state.completed_at = _utcnow()
        self._persistence.save(state)

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

        try:
            self._telemetry.log_event(TelemetryEvent(
                timestamp=_utcnow(),
                agent_name="engine",
                event_type="execution_completed",
                duration_ms=int(elapsed * 1000),
                details=(
                    f"task_id={state.task_id} steps={steps_done}"
                    f" gates_passed={gates_passed}"
                ),
            ))
        except Exception:
            pass

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
        state = self._persistence.load()
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
        state = self._persistence.load()
        if state is None:
            return ExecutionAction(
                action_type=ActionType.FAILED,
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
        state = self._persistence.load()
        if state is None:
            return 0

        original_count = len(state.step_results)
        state.step_results = [
            r for r in state.step_results if r.status != "dispatched"
        ]
        recovered = original_count - len(state.step_results)

        if recovered > 0:
            self._persistence.save(state)

        return recovered

    # ── Approval, amendment, and team APIs ─────────────────────────────────

    def record_approval_result(
        self,
        phase_id: int,
        result: str,
        feedback: str = "",
    ) -> None:
        """Record a human approval decision for a phase.

        Args:
            phase_id: The phase_id requiring approval.
            result: One of ``"approve"``, ``"reject"``,
                ``"approve-with-feedback"``.
            feedback: Free-text feedback (used when result is
                ``"approve-with-feedback"`` to trigger a plan amendment).
        """
        _VALID_RESULTS = {"approve", "reject", "approve-with-feedback"}
        if result not in _VALID_RESULTS:
            raise ValueError(
                f"Invalid approval result '{result}'. Must be one of: {_VALID_RESULTS}"
            )

        state = self._persistence.load()
        if state is None:
            raise RuntimeError(
                "record_approval_result() called with no active execution state."
            )

        approval = ApprovalResult(
            phase_id=phase_id,
            result=result,
            feedback=feedback,
        )
        state.approval_results.append(approval)

        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "approval_result",
                agent_name=None,
                phase=phase_id,
                step=0,
                details={"result": result, "feedback": feedback},
            )

        if result == "reject":
            state.status = "failed"
        elif result == "approve":
            state.status = "running"
        elif result == "approve-with-feedback":
            # Insert a remediation phase after the current phase.
            # Save state first so amend_plan sees the approval result.
            self._persistence.save(state)
            self._amend_from_feedback(state, phase_id, feedback)
            # Reload state — amend_plan saved its own copy with the
            # amendment applied.  We must pick up those changes.
            state = self._persistence.load() or state
            state.status = "running"

        self._persistence.save(state)

    def amend_plan(
        self,
        description: str,
        new_phases: list[PlanPhase] | None = None,
        insert_after_phase: int | None = None,
        add_steps_to_phase: int | None = None,
        new_steps: list[PlanStep] | None = None,
        trigger: str = "manual",
        trigger_phase_id: int = 0,
        feedback: str = "",
    ) -> PlanAmendment:
        """Amend the running plan by adding phases or steps.

        The plan inside ``ExecutionState`` is mutated in place.  An audit
        record (:class:`PlanAmendment`) is appended to ``state.amendments``.

        Args:
            description: Human-readable explanation of the amendment.
            new_phases: New :class:`PlanPhase` objects to insert.
            insert_after_phase: Insert *new_phases* after this phase_id.
                If ``None``, appends after the current phase.
            add_steps_to_phase: Phase_id to add *new_steps* to.
            new_steps: New :class:`PlanStep` objects for an existing phase.
            trigger: What caused this amendment.
            trigger_phase_id: Which phase triggered it.
            feedback: Reviewer feedback text.

        Returns:
            The :class:`PlanAmendment` record.
        """
        state = self._persistence.load()
        if state is None:
            raise RuntimeError(
                "amend_plan() called with no active execution state."
            )

        amendment = PlanAmendment(
            amendment_id=f"amend-{len(state.amendments) + 1}",
            trigger=trigger,
            trigger_phase_id=trigger_phase_id,
            description=description,
            feedback=feedback,
        )

        if new_phases:
            # Determine insertion index.
            if insert_after_phase is not None:
                insert_idx = next(
                    (i + 1 for i, p in enumerate(state.plan.phases)
                     if p.phase_id == insert_after_phase),
                    len(state.plan.phases),
                )
            else:
                # Default: insert after the current phase.
                insert_idx = state.current_phase + 1

            for i, phase in enumerate(new_phases):
                state.plan.phases.insert(insert_idx + i, phase)
                amendment.phases_added.append(phase.phase_id)

            self._renumber_phases(state)

        if new_steps and add_steps_to_phase is not None:
            target = next(
                (p for p in state.plan.phases if p.phase_id == add_steps_to_phase),
                None,
            )
            if target is not None:
                for step in new_steps:
                    target.steps.append(step)
                    amendment.steps_added.append(step.step_id)

        state.amendments.append(amendment)

        if self._trace is not None:
            self._tracer.record_event(
                self._trace,
                "replan",
                agent_name=None,
                phase=trigger_phase_id,
                step=0,
                details={
                    "amendment_id": amendment.amendment_id,
                    "description": description,
                    "phases_added": amendment.phases_added,
                    "steps_added": amendment.steps_added,
                },
            )

        self._persistence.save(state)
        return amendment

    def record_team_member_result(
        self,
        step_id: str,
        member_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
    ) -> None:
        """Record the result of a single team member within a team step.

        When all members have completed, the parent step is automatically
        marked as complete.  If any member fails, the parent step fails.
        """
        state = self._persistence.load()
        if state is None:
            raise RuntimeError(
                "record_team_member_result() called with no active execution state."
            )

        # Find or create the parent StepResult for this team step.
        parent = state.get_step_result(step_id)
        if parent is None:
            parent = StepResult(
                step_id=step_id, agent_name="team", status="dispatched",
            )
            state.step_results.append(parent)

        member_result = TeamStepResult(
            member_id=member_id,
            agent_name=agent_name,
            status=status,
            outcome=outcome,
            files_changed=files_changed or [],
        )
        parent.member_results.append(member_result)

        # Check if all team members are done.
        plan_step = self._find_step(state, step_id)
        if plan_step and plan_step.team:
            all_member_ids = {m.member_id for m in plan_step.team}
            completed_ids = {
                m.member_id for m in parent.member_results
                if m.status == "complete"
            }
            failed_ids = {
                m.member_id for m in parent.member_results
                if m.status == "failed"
            }

            if failed_ids:
                parent.status = "failed"
                parent.error = f"Team member(s) failed: {', '.join(sorted(failed_ids))}"
                parent.completed_at = _utcnow()
            elif completed_ids >= all_member_ids:
                parent.status = "complete"
                parent.outcome = "; ".join(
                    m.outcome for m in parent.member_results if m.outcome
                )
                parent.files_changed = [
                    f for m in parent.member_results for f in m.files_changed
                ]
                parent.completed_at = _utcnow()

        self._persistence.save(state)

    # ── Internal helpers ────────────────────────────────────────────────────

    # Event ownership: Engine publishes task-level and phase-level events.
    # Step-level events (step.dispatched, step.completed, step.failed) are
    # published by the runtime layer (TaskWorker) to avoid duplication.

    def _on_event_for_telemetry(self, event: Event) -> None:
        """EventBus subscriber that mirrors every domain event to telemetry.

        Called synchronously by the bus during publish().  Wrapped in
        try/except so a logging failure never crashes execution.
        """
        try:
            agent_name = event.payload.get("agent_name") or "engine"
            self._telemetry.log_event(TelemetryEvent(
                timestamp=event.timestamp,
                agent_name=agent_name,
                event_type=event.topic,
                details=f"task_id={event.task_id} seq={event.sequence}",
            ))
        except Exception:
            pass

    def _publish(self, event: Event) -> None:
        """Publish an event if a bus is configured."""
        if self._bus is not None:
            self._bus.publish(event)

    # Backward-compatible shims — tests may call these directly.
    def _save_state(self, state: ExecutionState) -> Path:
        """Delegate to :class:`StatePersistence`."""
        self._persistence.save(state)
        return self._persistence.path

    def _load_state(self) -> ExecutionState | None:
        """Delegate to :class:`StatePersistence`."""
        return self._persistence.load()

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
                action_type=ActionType.COMPLETE,
                message=f"Task {state.task_id} is already complete.",
                summary=f"Task {state.task_id} completed.",
            )
        if state.status == "failed":
            failed_ids = list(state.failed_step_ids)
            msg = f"Execution failed. Failed step(s): {', '.join(failed_ids) or 'gate'}"
            return ExecutionAction(
                action_type=ActionType.FAILED,
                message=msg,
                summary=msg,
            )

        # approval_pending — waiting for human approval before proceeding.
        if state.status == "approval_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.approval_required:
                return self._approval_action(state, phase_obj)

        # gate_pending — a gate was requested but result not yet recorded.
        if state.status == "gate_pending":
            phase_obj = state.current_phase_obj
            if phase_obj and phase_obj.gate:
                return ExecutionAction(
                    action_type=ActionType.GATE,
                    message=f"Run gate '{phase_obj.gate.gate_type}' for phase {phase_obj.phase_id}.",
                    gate_type=phase_obj.gate.gate_type,
                    gate_command=phase_obj.gate.command,
                    phase_id=phase_obj.phase_id,
                )

        # No more phases — all done.
        if state.current_phase >= len(state.plan.phases):
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message=f"All phases of task {state.task_id} are complete.",
                summary=f"Task {state.task_id} completed successfully.",
            )

        phase_obj = state.current_phase_obj
        if phase_obj is None:
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message="No more phases.",
                summary=f"Task {state.task_id} completed.",
            )

        steps = phase_obj.steps

        # If phase has no steps, go straight to gate or next phase.
        if not steps:
            if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
                state.status = "gate_pending"
                return ExecutionAction(
                    action_type=ActionType.GATE,
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
                    action_type=ActionType.FAILED,
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
            if next_step.team:
                return self._team_dispatch_action(next_step, state)
            return self._dispatch_action(next_step, state)

        # If no step is dispatchable but some are still pending (dispatched or
        # blocked by dependencies), return WAIT.
        pending = {s.step_id for s in steps} - completed - state.failed_step_ids
        if pending:
            return ExecutionAction(
                action_type=ActionType.WAIT,
                message="Waiting for in-flight steps to complete before proceeding.",
                summary=f"Steps in flight or blocked: {', '.join(sorted(pending))}",
            )

        # All steps in this phase are complete.
        # Check approval requirement BEFORE gate.
        if (phase_obj.approval_required
                and not self._approval_passed_for_phase(state, phase_obj.phase_id)):
            state.status = "approval_pending"
            return self._approval_action(state, phase_obj)

        if phase_obj.gate and not self._gate_passed_for_phase(state, phase_obj.phase_id):
            state.status = "gate_pending"
            return ExecutionAction(
                action_type=ActionType.GATE,
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
        dispatcher = PromptDispatcher()

        # Find the most recent completed step (different step_id) for handoff.
        handoff = ""
        for result in reversed(state.step_results):
            if result.step_id != step.step_id and result.status == "complete" and result.outcome:
                handoff = result.outcome
                break

        prompt = dispatcher.build_delegation_prompt(
            step,
            shared_context=state.plan.shared_context,
            handoff_from=handoff,
            task_summary=state.plan.task_summary,
        )
        enforcement = PromptDispatcher.build_path_enforcement(step)
        return ExecutionAction(
            action_type=ActionType.DISPATCH,
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
    def _approval_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
        """Return True if an approval result (approve or approve-with-feedback) exists."""
        for a in state.approval_results:
            if a.phase_id == phase_id and a.result in ("approve", "approve-with-feedback"):
                return True
        return False

    def _approval_action(
        self, state: ExecutionState, phase_obj: PlanPhase,
    ) -> ExecutionAction:
        """Build an APPROVAL action for a phase requiring human review."""
        context = phase_obj.approval_description or self._build_approval_context(
            state, phase_obj,
        )
        return ExecutionAction(
            action_type=ActionType.APPROVAL,
            message=(
                f"Phase {phase_obj.phase_id} ({phase_obj.name}) "
                f"requires approval before proceeding."
            ),
            phase_id=phase_obj.phase_id,
            approval_context=context,
            approval_options=["approve", "reject", "approve-with-feedback"],
        )

    @staticmethod
    def _build_approval_context(
        state: ExecutionState, phase_obj: PlanPhase,
    ) -> str:
        """Build a markdown summary of phase output for the human reviewer."""
        lines = [
            f"## Phase {phase_obj.phase_id}: {phase_obj.name} — Review Summary",
            "",
        ]
        # Gather step results for this phase.
        phase_step_ids = {s.step_id for s in phase_obj.steps}
        for result in state.step_results:
            if result.step_id in phase_step_ids and result.status == "complete":
                lines.append(f"### Step {result.step_id}: {result.agent_name}")
                if result.outcome:
                    lines.append(result.outcome)
                if result.files_changed:
                    lines.append(f"**Files changed**: {', '.join(result.files_changed)}")
                lines.append("")
        return "\n".join(lines)

    def _team_dispatch_action(
        self, step: PlanStep, state: ExecutionState,
    ) -> ExecutionAction:
        """Build a DISPATCH action with parallel_actions for each team member."""
        dispatcher = PromptDispatcher()

        # Build team overview for context.
        team_overview = ", ".join(
            f"{m.agent_name} ({m.role})" for m in step.team
        )

        # Find completed member IDs (if any members already recorded).
        parent = state.get_step_result(step.step_id)
        completed_members = set()
        if parent:
            completed_members = {
                m.member_id for m in parent.member_results
                if m.status == "complete"
            }

        member_actions: list[ExecutionAction] = []
        for member in step.team:
            if member.member_id in completed_members:
                continue
            # Check member-level dependencies.
            if member.depends_on and not all(
                dep in completed_members for dep in member.depends_on
            ):
                continue

            prompt = dispatcher.build_team_delegation_prompt(
                step=step,
                member=member,
                shared_context=state.plan.shared_context,
                task_summary=state.plan.task_summary,
                team_overview=team_overview,
            )
            member_actions.append(ExecutionAction(
                action_type=ActionType.DISPATCH,
                message=f"Team member '{member.agent_name}' ({member.role}) for step {step.step_id}.",
                agent_name=member.agent_name,
                agent_model=member.model,
                delegation_prompt=prompt,
                step_id=member.member_id,
            ))

        if not member_actions:
            # All dispatchable members are blocked — WAIT.
            return ExecutionAction(
                action_type=ActionType.WAIT,
                message=f"Waiting for team members in step {step.step_id}.",
                summary=f"Team step {step.step_id} has members in flight.",
            )

        # Return the first member action with the rest as parallel_actions.
        first = member_actions[0]
        if len(member_actions) > 1:
            first.parallel_actions = member_actions[1:]
        return first

    def _amend_from_feedback(
        self, state: ExecutionState, phase_id: int, feedback: str,
    ) -> None:
        """Insert a remediation phase based on approval feedback.

        Creates a new phase with a single step assigned to the most
        appropriate agent, inserted after the current phase.
        """
        # Determine which agent should handle remediation.
        phase_obj = state.current_phase_obj
        if phase_obj and phase_obj.steps:
            agent = phase_obj.steps[0].agent_name
        else:
            agent = "backend-engineer"

        # Build a new phase_id (will be renumbered by amend_plan).
        new_phase = PlanPhase(
            phase_id=0,  # placeholder — renumbered in amend_plan
            name="Remediation",
            steps=[PlanStep(
                step_id="0.1",  # placeholder
                agent_name=agent,
                task_description=f"Address feedback from phase {phase_id} review: {feedback}",
            )],
        )
        self.amend_plan(
            description=f"Remediation from approval feedback on phase {phase_id}",
            new_phases=[new_phase],
            trigger="approval_feedback",
            trigger_phase_id=phase_id,
            feedback=feedback,
        )

    @staticmethod
    def _find_step(state: ExecutionState, step_id: str) -> PlanStep | None:
        """Locate a PlanStep by step_id in the plan."""
        for phase in state.plan.phases:
            for step in phase.steps:
                if step.step_id == step_id:
                    return step
        return None

    @staticmethod
    def _renumber_phases(state: ExecutionState) -> None:
        """Re-assign sequential phase_id values (1-based) after insertion.

        Also updates step_ids to match new phase numbering, and fixes
        references in gate_results and approval_results.
        """
        old_to_new: dict[int, int] = {}
        for idx, phase in enumerate(state.plan.phases):
            new_id = idx + 1
            old_to_new[phase.phase_id] = new_id
            phase.phase_id = new_id
            # Renumber step_ids within this phase.
            for si, step in enumerate(phase.steps):
                step.step_id = f"{new_id}.{si + 1}"
                # Renumber team member IDs if present.
                for mi, member in enumerate(step.team):
                    member.member_id = f"{new_id}.{si + 1}.{chr(97 + mi)}"

        # Update phase_id references in gate and approval results.
        for gr in state.gate_results:
            gr.phase_id = old_to_new.get(gr.phase_id, gr.phase_id)
        for ar in state.approval_results:
            ar.phase_id = old_to_new.get(ar.phase_id, ar.phase_id)

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
