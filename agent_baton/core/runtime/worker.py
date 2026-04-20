"""TaskWorker -- async event loop that drives a single task's execution.

The worker wraps the synchronous ``ExecutionEngine`` (which remains the
single source of truth for plan state) and adds async dispatch via
``StepScheduler``.  It is the primary consumer of the ``ExecutionDriver``
protocol.

Responsibilities:

1. Call ``engine.next_actions()`` to get all parallel-dispatchable work.
2. Mark steps as dispatched via ``engine.mark_dispatched()``.
3. Dispatch agents concurrently via ``StepScheduler``.
4. Record results back via ``engine.record_step_result()``.
5. Handle GATE actions: auto-approve programmatic gates (test/build/lint);
   route human-required gates through ``DecisionManager``.
6. Handle APPROVAL actions: route through ``DecisionManager`` or auto-approve.
7. Publish step-level domain events via ``EventBus`` (the engine handles
   task-level and phase-level events).

Event ownership split:
    - **Engine** publishes: ``task.started``, ``task.completed``,
      ``phase.started``, ``phase.completed``, ``gate.passed``, ``gate.failed``.
    - **Worker** publishes: ``step.pre_dispatch``, ``step.dispatched``,
      ``step.completed``, ``step.failed``, ``gate.pre_check``.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from agent_baton.core.engine.protocols import ExecutionDriver
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events import events as evt
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.runtime.launcher import AgentLauncher
from agent_baton.core.runtime.scheduler import StepScheduler, SchedulerConfig
from agent_baton.models.decision import DecisionRequest
from agent_baton.models.execution import ActionType


class TaskWorker:
    """Drives a single task's execution asynchronously.

    Wraps the synchronous ``ExecutionEngine`` -- the engine remains the
    single source of truth for plan state.  The worker's job is to:

    1. Call ``engine.next_actions()`` to get parallel work.
    2. Dispatch agents via ``StepScheduler``.
    3. Record results back via ``engine.record_step_result()``.
    4. Publish step-level events via ``EventBus``.
    5. Handle WAIT actions (sleep briefly then re-check).
    6. Handle GATE/APPROVAL actions (auto-approve or route to
       ``DecisionManager``).

    Typical usage::

        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
        summary = await worker.run()

    Attributes:
        _engine: The execution engine implementing ``ExecutionDriver``.
        _launcher: Agent launcher for spawning agents.
        _bus: Event bus for publishing step-level domain events.
        _scheduler: Bounded-concurrency scheduler for parallel dispatch.
        _running: True while ``run()`` is executing.
        _decision_manager: Optional manager for human decision routing.
            When None, gates and approvals are auto-approved.
        _shutdown_event: Optional asyncio.Event for graceful shutdown.
            When set, the worker drains and exits.
        _gate_poll_interval: Seconds between polls when waiting for a
            human decision on a gate or approval.
    """

    def __init__(
        self,
        engine: ExecutionDriver,
        launcher: AgentLauncher,
        bus: EventBus | None = None,
        max_parallel: int = 3,
        decision_manager: DecisionManager | None = None,
        shutdown_event: asyncio.Event | None = None,
        gate_poll_interval: float = 2.0,
        max_steps: int | None = None,
        max_gate_retries: int = 1,
    ) -> None:
        self._engine = engine
        self._launcher = launcher
        self._bus = bus or EventBus()
        self._scheduler = StepScheduler(SchedulerConfig(max_concurrent=max_parallel))
        self._running = False
        self._wait_event: asyncio.Event | None = None
        self._decision_manager = decision_manager
        self._shutdown_event = shutdown_event
        self._gate_poll_interval = gate_poll_interval
        # B2: optional step ceiling — stop cleanly after N steps.
        # None / 0 means unlimited.
        self._max_steps: int | None = max_steps if max_steps else None
        self._steps_executed: int = 0
        # Issue 1: gate retry ceiling — tracks consecutive failures per phase_id.
        self._max_gate_retries: int = max_gate_retries
        self._gate_retry_counts: dict[int, int] = {}

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

            # B2: enforce --max-steps ceiling before asking for the next action.
            if self._max_steps is not None and self._steps_executed >= self._max_steps:
                return (
                    f"Execution stopped: reached max-steps limit "
                    f"({self._max_steps}). "
                    "Run 'baton daemon start --resume' to continue."
                )

            action = self._engine.next_action()

            if action.action_type == ActionType.COMPLETE:
                summary = self._engine.complete()
                return summary

            if action.action_type == ActionType.FAILED:
                return action.message

            if action.action_type == ActionType.WAIT:
                # Parallel steps are still in-flight (from a previous
                # iteration).  Sleep briefly and re-check.
                await asyncio.sleep(0.5)
                continue

            if action.action_type == ActionType.APPROVAL:
                await self._handle_approval(action)
                continue

            if action.action_type == ActionType.GATE:
                await self._handle_gate(action)
                continue

            if action.action_type == ActionType.DISPATCH:
                # Collect ALL currently dispatchable steps so we can launch
                # them in parallel.
                actions = self._engine.next_actions()
                if not actions:
                    actions = [action]

                # ── Separate automation from agent-dispatched steps ───────────
                automation_actions = [a for a in actions if getattr(a, "step_type", "") == "automation"]
                agent_actions = [a for a in actions if getattr(a, "step_type", "") != "automation"]

                # Mark ALL steps (including automation) as dispatched so the
                # engine does not re-dispatch them while they are in-flight.
                for a in actions:
                    self._engine.mark_dispatched(
                        a.step_id,
                        a.agent_name or "automation",
                    )

                # Event ownership: Worker publishes step-level events.
                # Task-level and phase-level events are published by ExecutionEngine.

                # Publish step.pre_dispatch and step.dispatched events.
                task_id = self._engine.status().get("task_id", "")
                for a in actions:
                    self._bus.publish(
                        evt.step_pre_dispatch(
                            task_id=task_id,
                            step_id=a.step_id,
                            agent_name=a.agent_name,
                            model=a.agent_model,
                            delegation_prompt=a.delegation_prompt,
                        )
                    )
                    self._bus.publish(
                        evt.step_dispatched(
                            task_id=task_id,
                            step_id=a.step_id,
                            agent_name=a.agent_name or "automation",
                            model=a.agent_model,
                        )
                    )

                # ── Automation: run commands directly, no LLM ────────────────
                for a in automation_actions:
                    try:
                        proc = await self._run_automation(a)
                        succeeded = proc.returncode == 0
                        self._engine.record_step_result(
                            step_id=a.step_id,
                            agent_name="automation",
                            status="complete" if succeeded else "failed",
                            outcome=proc.stdout,
                            error=proc.stderr if not succeeded else "",
                        )
                        event_status = "complete" if succeeded else "failed"
                        if event_status == "complete":
                            self._bus.publish(
                                evt.step_completed(
                                    task_id=task_id,
                                    step_id=a.step_id,
                                    agent_name="automation",
                                    outcome=proc.stdout,
                                    files_changed=[],
                                    commit_hash="",
                                    duration_seconds=0.0,
                                    estimated_tokens=0,
                                )
                            )
                        else:
                            self._bus.publish(
                                evt.step_failed(
                                    task_id=task_id,
                                    step_id=a.step_id,
                                    agent_name="automation",
                                    error=proc.stderr,
                                )
                            )
                    except subprocess.TimeoutExpired:
                        self._engine.record_step_result(
                            step_id=a.step_id,
                            agent_name="automation",
                            status="failed",
                            error=f"Automation command timed out after 300s: {a.command}",
                        )
                        self._bus.publish(
                            evt.step_failed(
                                task_id=task_id,
                                step_id=a.step_id,
                                agent_name="automation",
                                error=f"Automation command timed out after 300s: {a.command}",
                            )
                        )

                # ── Agent steps: existing scheduler path ─────────────────────
                if agent_actions:
                    steps = [
                        {
                            "agent_name": a.agent_name,
                            "model": a.agent_model,
                            "prompt": a.delegation_prompt,
                            "step_id": a.step_id,
                            "mcp_servers": a.mcp_servers,
                        }
                        for a in agent_actions
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

                # B2: count each dispatched batch as one step toward the ceiling.
                # Automation and agent batches both count — one increment per loop
                # iteration that included at least one dispatched action.
                self._steps_executed += len(actions)

                continue

        # Unreachable, but satisfies static analysis.
        return "Execution loop exited unexpectedly."

    async def _run_automation(self, action: object) -> subprocess.CompletedProcess:
        """Run an automation step's shell command in a thread pool.

        Uses ``asyncio.to_thread`` so the event loop stays unblocked while the
        subprocess runs.  A 5-minute timeout is enforced — callers must handle
        ``subprocess.TimeoutExpired``.

        The working directory is the project root (``Path.cwd()`` at the time of
        invocation) rather than the engine's internal ``_root``, matching the
        behaviour documented in the spec.

        Args:
            action: An :class:`~agent_baton.models.execution.ExecutionAction`
                with ``step_type="automation"`` and a ``command`` attribute.

        Returns:
            A :class:`subprocess.CompletedProcess` with ``returncode``,
            ``stdout``, and ``stderr`` populated.
        """
        command = getattr(action, "command", "")
        return await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(Path.cwd()),
        )

    async def _handle_gate(self, action: object) -> None:  # action: ExecutionAction
        """Handle a GATE action.

        Programmatic gate types (``test``, ``build``, ``lint``, ``spec``) are
        executed via an async subprocess using the gate command from the action.
        If no command is present on the action, the gate is auto-approved as a
        fallback.  The ``ci`` gate type dispatches a GitHub Actions workflow via
        :func:`~agent_baton.core.engine.gates.run_github_actions_gate` (run in a
        thread executor so the event loop stays unblocked during the long poll).
        Human-required gate types (``review``, ``approval``, or anything else)
        are routed through the :class:`DecisionManager` when one is configured;
        otherwise they fall back to auto-approval.
        """
        gate_type = getattr(action, "gate_type", "")
        phase_id = getattr(action, "phase_id", 0)

        # Publish gate.pre_check event.
        task_id = self._engine.status().get("task_id", "")
        self._bus.publish(
            evt.gate_pre_check(
                task_id=task_id,
                phase_id=phase_id,
                gate_type=gate_type,
                command=getattr(action, "gate_command", ""),
            )
        )

        # CI gate: dispatch GitHub Actions workflow and poll until completion.
        # run_github_actions_gate is synchronous (uses time.sleep polling), so
        # we run it in a thread executor to avoid blocking the event loop.
        if gate_type == "ci":
            from agent_baton.core.engine.gates import run_github_actions_gate

            gate_command = getattr(action, "gate_command", "")
            # gate_command carries the workflow name/file (e.g. "ci.yml").
            # Fall back to a sensible default when the plan left it empty.
            workflow_name = gate_command.strip() or "ci.yml"
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, run_github_actions_gate, workflow_name
                )
            except Exception as exc:
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=False,
                    output=f"[escalate] CI gate raised an unexpected error: {exc}",
                    command=f"gh workflow run {workflow_name}",
                )
                return
            self._engine.record_gate_result(
                phase_id=phase_id,
                passed=result.passed,
                output=result.output,
                command=f"gh workflow run {workflow_name}",
                exit_code=None,
            )
            return

        # Execute programmatic gates via subprocess — mirrors the CLI path in
        # execute.py so daemon and CLI behaviour are identical (bug 0.1).
        if gate_type in ("test", "build", "lint", "spec"):
            gate_command = getattr(action, "gate_command", "")
            if not gate_command:
                # No command specified — fall back to auto-approve.
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=True,
                    output=f"auto-approved ({gate_type}): no command specified",
                )
                return

            async def _run_gate_subprocess(cmd: str) -> tuple[int, bytes, bytes]:
                _proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(Path.cwd()),
                )
                _stdout, _stderr = await _proc.communicate()
                return (_proc.returncode or 0), _stdout, _stderr

            try:
                rc, stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    _run_gate_subprocess(gate_command),
                    timeout=300,
                )
                stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
                stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
                passed = rc == 0
                output = stdout[-2000:] if stdout else ""
                if not passed and stderr:
                    output += f"\n--- stderr ---\n{stderr[-1000:]}"
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=passed,
                    output=output,
                    command=gate_command,
                    exit_code=rc,
                )
                # Reset retry counter on pass; increment and possibly escalate on fail.
                if passed:
                    self._gate_retry_counts.pop(phase_id, None)
                else:
                    self._gate_retry_counts[phase_id] = (
                        self._gate_retry_counts.get(phase_id, 0) + 1
                    )
                    if self._gate_retry_counts[phase_id] > self._max_gate_retries:
                        self._gate_retry_counts.pop(phase_id, None)
                        if self._decision_manager is not None:
                            _task_id = self._engine.status().get("task_id", "")
                            _dr = DecisionRequest.create(
                                task_id=_task_id,
                                decision_type="gate_escalation",
                                summary=(
                                    f"Gate '{gate_type}' for phase {phase_id} has "
                                    f"exceeded the retry ceiling "
                                    f"(max_gate_retries={self._max_gate_retries}). "
                                    "Manual intervention required."
                                ),
                                options=["retry", "fail"],
                            )
                            self._decision_manager.request(_dr)
                        else:
                            _state = self._engine._load_execution()
                            if _state is not None:
                                _state.status = "failed"
                                self._engine._save_execution(_state)
            except asyncio.TimeoutError:
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=False,
                    output=f"Gate timed out after 300s: {gate_command}",
                    command=gate_command,
                    exit_code=-1,
                )
                self._gate_retry_counts[phase_id] = (
                    self._gate_retry_counts.get(phase_id, 0) + 1
                )
                if self._gate_retry_counts[phase_id] > self._max_gate_retries:
                    self._gate_retry_counts.pop(phase_id, None)
                    _state = self._engine._load_execution()
                    if _state is not None:
                        _state.status = "failed"
                        self._engine._save_execution(_state)
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
                res_data = self._decision_manager.get_resolution(req.request_id)
                if res_data is not None:
                    passed = res_data.get("chosen_option") == "approve"
                else:
                    passed = True  # resolved without resolution file → treat as approve
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=passed,
                    output=f"Human decision: {resolved.status}",
                )
                # A human rejection is final — don't allow engine retry loop.
                if not passed:
                    try:
                        self._engine.fail_gate(phase_id)
                    except Exception:
                        pass
                return

            # Check shutdown before sleeping.
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                self._engine.record_gate_result(
                    phase_id=phase_id,
                    passed=False,
                    output="Gate aborted: shutdown requested",
                )
                return

            await asyncio.sleep(self._gate_poll_interval)

    async def _handle_approval(self, action: object) -> None:
        """Handle an APPROVAL action.

        Routes through the :class:`DecisionManager` when configured;
        otherwise auto-approves.
        """
        phase_id = getattr(action, "phase_id", 0)

        if self._decision_manager is None:
            self._engine.record_approval_result(
                phase_id=phase_id,
                result="approve",
                feedback="auto-approved (no decision manager)",
            )
            return

        task_id = self._engine.status().get("task_id", "")
        req = DecisionRequest.create(
            task_id=task_id,
            decision_type="phase_approval",
            summary=getattr(action, "message", f"Phase {phase_id} requires approval"),
            options=["approve", "reject", "approve-with-feedback"],
        )
        self._decision_manager.request(req)

        while True:
            resolved = self._decision_manager.get(req.request_id)
            if resolved is not None and resolved.status == "resolved":
                res_data = self._decision_manager.get_resolution(req.request_id)
                if res_data is not None:
                    result = res_data.get("chosen_option", "approve")
                    feedback = res_data.get("rationale", "")
                else:
                    result = "approve"
                    feedback = ""
                self._engine.record_approval_result(
                    phase_id=phase_id,
                    result=result,
                    feedback=feedback,
                )
                return

            if self._shutdown_event is not None and self._shutdown_event.is_set():
                self._engine.record_approval_result(
                    phase_id=phase_id,
                    result="reject",
                    feedback="Approval aborted: shutdown requested",
                )
                return

            await asyncio.sleep(self._gate_poll_interval)

    def notify_resolution(self) -> None:
        """Signal that a pending decision has been resolved externally."""
        if self._wait_event is not None:
            self._wait_event.set()
