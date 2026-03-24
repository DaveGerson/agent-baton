"""TaskWorker — async event loop that drives a single task's execution.

The worker wraps the existing ExecutionEngine (which remains the source of
truth for plan state) and adds async dispatch via StepScheduler.

Responsibilities:
1. Call engine.next_actions() to get all parallel-dispatchable work.
2. Mark steps as dispatched via engine.mark_dispatched().
3. Dispatch agents concurrently via StepScheduler.
4. Record results back via engine.record_step_result().
5. Auto-approve GATE actions (callers can override by subclassing).
6. Publish events via EventBus.
"""
from __future__ import annotations

import asyncio
import json

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.runtime.launcher import AgentLauncher, LaunchResult
from agent_baton.core.runtime.scheduler import StepScheduler, SchedulerConfig
from agent_baton.models.decision import DecisionRequest
from agent_baton.models.execution import ActionType


class TaskWorker:
    """Drives a single task's execution asynchronously.

    Wraps the existing :class:`ExecutionEngine` — the engine remains the
    source of truth for plan state.  The worker's job is to:

    1. Call ``engine.next_actions()`` to get parallel work.
    2. Dispatch agents via :class:`StepScheduler`.
    3. Record results back via ``engine.record_step_result()``.
    4. Publish events via :class:`EventBus`.
    5. Handle WAIT actions (sleep briefly then re-check).
    6. Auto-approve GATE actions (override :meth:`_handle_gate` to customise).

    Typical usage::

        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
        summary = await worker.run()
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        launcher: AgentLauncher,
        bus: EventBus | None = None,
        max_parallel: int = 3,
        decision_manager: DecisionManager | None = None,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        self._engine = engine
        self._launcher = launcher
        self._bus = bus or EventBus()
        self._scheduler = StepScheduler(SchedulerConfig(max_concurrent=max_parallel))
        self._running = False
        self._wait_event: asyncio.Event | None = None
        self._decision_manager = decision_manager
        self._shutdown_event = shutdown_event

    @property
    def is_running(self) -> bool:
        """True while :meth:`run` is executing."""
        return self._running

    @property
    def bus(self) -> EventBus:
        """The event bus used by this worker."""
        return self._bus

    async def run(self) -> str:
        """Main entry point.  Returns the completion summary string."""
        self._running = True
        try:
            return await self._execution_loop()
        finally:
            self._running = False

    # ── Internal loop ────────────────────────────────────────────────────────

    async def _execution_loop(self) -> str:
        """Core async loop — advances the engine until COMPLETE or FAILED."""
        while True:
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                return "Execution stopped: shutdown requested."

            action = self._engine.next_action()

            if action.action_type == ActionType.COMPLETE.value:
                summary = self._engine.complete()
                return summary

            if action.action_type == ActionType.FAILED.value:
                return action.message

            if action.action_type == ActionType.WAIT.value:
                # Parallel steps are still in-flight (from a previous
                # iteration).  Sleep briefly and re-check.
                await asyncio.sleep(0.5)
                continue

            if action.action_type == ActionType.GATE.value:
                await self._handle_gate(action)
                continue

            if action.action_type == ActionType.DISPATCH.value:
                # Collect ALL currently dispatchable steps so we can launch
                # them in parallel.
                actions = self._engine.next_actions()
                if not actions:
                    actions = [action]

                # Mark every step as dispatched so the engine does not
                # re-dispatch them while they are in-flight.
                for a in actions:
                    self._engine.mark_dispatched(a.step_id, a.agent_name)

                # Publish step.dispatched events.
                task_id = self._engine.status().get("task_id", "")
                for a in actions:
                    self._bus.publish(
                        evt.step_dispatched(
                            task_id=task_id,
                            step_id=a.step_id,
                            agent_name=a.agent_name,
                            model=a.agent_model,
                        )
                    )

                # Build step dicts for the scheduler.
                steps = [
                    {
                        "agent_name": a.agent_name,
                        "model": a.agent_model,
                        "prompt": a.delegation_prompt,
                        "step_id": a.step_id,
                    }
                    for a in actions
                ]

                results = await self._scheduler.dispatch_batch(steps, self._launcher)

                # Record all results back into the engine.
                for result in results:
                    if isinstance(result, Exception):
                        # Should not reach here with return_exceptions=False,
                        # but guard defensively.
                        self._engine.record_step_result(
                            step_id="unknown",
                            agent_name="unknown",
                            status="failed",
                            error=str(result),
                        )
                    else:
                        self._engine.record_step_result(
                            step_id=result.step_id,
                            agent_name=result.agent_name,
                            status=result.status,
                            outcome=result.outcome,
                            files_changed=result.files_changed,
                            commit_hash=result.commit_hash,
                            estimated_tokens=result.estimated_tokens,
                            duration_seconds=result.duration_seconds,
                            error=result.error,
                        )

                        # Publish step.completed or step.failed event.
                        if result.status == "complete":
                            self._bus.publish(
                                evt.step_completed(
                                    task_id=task_id,
                                    step_id=result.step_id,
                                    agent_name=result.agent_name,
                                    outcome=result.outcome,
                                    files_changed=result.files_changed,
                                    commit_hash=result.commit_hash,
                                    duration_seconds=result.duration_seconds,
                                    estimated_tokens=result.estimated_tokens,
                                )
                            )
                        else:
                            self._bus.publish(
                                evt.step_failed(
                                    task_id=task_id,
                                    step_id=result.step_id,
                                    agent_name=result.agent_name,
                                    error=result.error,
                                )
                            )

                continue

        # Unreachable, but satisfies static analysis.
        return "Execution loop exited unexpectedly."

    async def _handle_gate(self, action: object) -> None:  # action: ExecutionAction
        """Handle a GATE action.

        Programmatic gate types (``test``, ``build``, ``lint``, ``spec``) are
        auto-approved immediately.  Human-required gate types (``review``,
        ``approval``, or anything else) are routed through the
        :class:`DecisionManager` when one is configured; otherwise they fall
        back to auto-approval.
        """
        gate_type = getattr(action, "gate_type", "")
        phase_id = getattr(action, "phase_id", 0)

        # Auto-approve programmatic gates.
        if gate_type in ("test", "build", "lint", "spec"):
            self._engine.record_gate_result(
                phase_id=phase_id,
                passed=True,
                output=f"auto-approved ({gate_type})",
            )
            return

        # Human-required gate — use DecisionManager if available.
        if self._decision_manager is None:
            self._engine.record_gate_result(
                phase_id=phase_id,
                passed=True,
                output="auto-approved (no decision manager)",
            )
            return

        # Create decision request and persist it to disk.
        task_id = self._engine.status().get("task_id", "")
        req = DecisionRequest.create(
            task_id=task_id,
            decision_type="gate_approval",
            summary=getattr(action, "message", f"Gate '{gate_type}' requires approval"),
            options=["approve", "reject"],
        )
        self._decision_manager.request(req)

        # Poll filesystem for resolution.
        while True:
            resolved = self._decision_manager.get(req.request_id)
            if resolved is not None and resolved.status == "resolved":
                res_path = self._decision_manager._resolution_path(req.request_id)
                if res_path.exists():
                    res_data = json.loads(res_path.read_text(encoding="utf-8"))
                    passed = res_data.get("chosen_option") == "approve"
                else:
                    passed = True  # resolved without resolution file → treat as approve
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=passed,
                    output=f"Human decision: {resolved.status}",
                )
                return

            # Check shutdown before sleeping.
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=False,
                    output="Gate aborted: shutdown requested",
                )
                return

            await asyncio.sleep(2.0)

    def notify_resolution(self) -> None:
        """Signal that a pending decision has been resolved externally."""
        if self._wait_event is not None:
            self._wait_event.set()
