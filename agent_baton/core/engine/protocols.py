"""Protocols defining the contracts between the execution engine and its callers.

The ``ExecutionDriver`` protocol is the most critical interface boundary in
the system.  It decouples the async runtime layer (``TaskWorker``,
``WorkerSupervisor``) from the synchronous execution engine, enabling
alternative engine implementations for testing or alternative runtimes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_baton.models.execution import (
        ExecutionAction,
        MachinePlan,
        PlanAmendment,
        PlanPhase,
        PlanStep,
    )


class ExecutionDriver(Protocol):
    """Interface contract between the async worker layer and the execution engine.

    This is the most critical contract in the system — it defines how the async
    TaskWorker drives the synchronous state machine. Any class implementing this
    protocol can serve as the engine for orchestrated execution.
    """

    def start(self, plan: MachinePlan) -> ExecutionAction:
        """Initialize execution from a plan and return the first action.

        Creates execution state, starts tracing, and persists state to
        disk for crash recovery.  The returned action is typically a
        DISPATCH for the first step, or COMPLETE if the plan is empty.

        Args:
            plan: The fully-constructed execution plan.

        Returns:
            The first action the caller should perform.
        """
        ...

    def next_action(self) -> ExecutionAction:
        """Advance the state machine and return the next action.

        Loads state from disk, determines what comes next (DISPATCH,
        GATE, APPROVAL, WAIT, COMPLETE, or FAILED), and saves the
        updated state.

        Returns:
            The action the caller should perform next.
        """
        ...

    def next_actions(self) -> list[ExecutionAction]:
        """Return all currently dispatchable actions for parallel execution.

        Unlike ``next_action`` which returns a single action, this method
        returns every step whose dependencies are satisfied and that has
        not yet been dispatched, completed, or failed.  The caller can
        launch all returned agents concurrently.

        Returns:
            List of DISPATCH actions, or empty list if nothing is
            dispatchable (caller should fall back to ``next_action``).
        """
        ...

    def mark_dispatched(self, step_id: str, agent_name: str) -> None:
        """Record that a step has been dispatched (in-flight).

        Prevents ``next_actions`` from re-dispatching a step that
        already has a running agent process.

        Args:
            step_id: The step that was dispatched.
            agent_name: The agent that was launched.
        """
        ...

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
        """Record the outcome of a dispatched step.

        Creates a ``StepResult``, inspects the outcome for knowledge gap
        signals, emits trace and telemetry events, and persists state.

        Args:
            step_id: The step that completed or failed.
            agent_name: The agent that executed the step.
            status: One of ``complete``, ``failed``, ``dispatched``,
                or ``interrupted``.
            outcome: Free-text summary of the agent's work.
            files_changed: Files modified by the agent.
            commit_hash: Git commit hash if the agent committed.
            estimated_tokens: Token count reported by the agent.
            duration_seconds: Wall-clock execution time.
            error: Error message when status is ``failed``.
        """
        ...

    def record_gate_result(
        self,
        phase_id: int,
        passed: bool,
        output: str = "",
    ) -> None:
        """Record the result of a QA gate check.

        On failure, sets execution status to ``failed``.  On success,
        advances the phase pointer to the next phase.

        Args:
            phase_id: The phase whose gate was checked.
            passed: Whether the gate check succeeded.
            output: Gate command output or reviewer feedback.
        """
        ...

    def record_approval_result(
        self,
        phase_id: int,
        result: str,
        feedback: str = "",
    ) -> None:
        """Record a human approval decision for a phase.

        ``approve`` resumes execution.  ``reject`` fails it.
        ``approve-with-feedback`` inserts a remediation phase.

        Args:
            phase_id: The phase requiring approval.
            result: One of ``approve``, ``reject``, or
                ``approve-with-feedback``.
            feedback: Free-text feedback for remediation.
        """
        ...

    def record_feedback_result(
        self,
        phase_id: int,
        question_id: str,
        chosen_index: int,
    ) -> None:
        """Record a user's answer to a feedback question.

        Looks up the chosen option's mapped agent and prompt, inserts
        a new dispatch step via plan amendment, and resumes execution
        once all questions are answered.

        Args:
            phase_id: The phase presenting the feedback gate.
            question_id: Which question was answered.
            chosen_index: Zero-based index into the question's options.
        """
        ...

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
        """Amend the running plan by inserting phases or steps.

        Mutates the plan in ``ExecutionState`` in place and records an
        audit trail via ``PlanAmendment``.  Phase IDs are renumbered
        after insertion to maintain sequential ordering.

        Args:
            description: Human-readable reason for the amendment.
            new_phases: Phases to insert into the plan.
            insert_after_phase: Insert after this phase_id (default:
                after the current phase).
            add_steps_to_phase: Phase_id to append new_steps to.
            new_steps: Steps to add to an existing phase.
            trigger: What caused the amendment (``manual``,
                ``approval_feedback``, ``knowledge_gap``).
            trigger_phase_id: The phase that triggered it.
            feedback: Reviewer feedback text.

        Returns:
            The ``PlanAmendment`` audit record.
        """
        ...

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

        When all members complete, the parent step is automatically
        marked as complete.  If any member fails, the parent step fails.

        Args:
            step_id: The parent team step ID.
            member_id: The individual member's ID within the team.
            agent_name: The agent that executed this member's work.
            status: ``complete`` or ``failed``.
            outcome: Summary of the member's work.
            files_changed: Files modified by this member.
        """
        ...

    def complete(self) -> str:
        """Finalize execution and return a summary string.

        Sets state to ``complete``, writes trace data, logs usage
        records, generates a retrospective, and triggers the
        improvement loop.

        Returns:
            Human-readable completion summary.
        """
        ...

    def status(self) -> dict:
        """Return current execution status as a dictionary.

        Returns:
            Dict with keys: ``task_id``, ``status``, ``current_phase``,
            ``steps_complete``, ``steps_total``, ``gates_passed``,
            ``gates_failed``, ``elapsed_seconds``.
        """
        ...

    def resume(self) -> ExecutionAction:
        """Resume execution from persisted state after a crash.

        Loads state from disk, reconnects the in-memory trace, and
        determines the next action from where execution left off.

        Returns:
            The next action to perform.
        """
        ...

    def provide_interact_input(self, step_id: str, input_text: str) -> None:
        """Record human input for an interactive step awaiting a response.

        Appends a human turn to the step's interaction history and sets the
        step status to ``interact_dispatched`` so the next
        ``_determine_action()`` call returns a DISPATCH continuation.

        Args:
            step_id: The step ID currently in ``interacting`` status.
            input_text: Human-provided text for the next agent turn.
        """
        ...

    def complete_interaction(self, step_id: str) -> None:
        """Promote an interacting step to ``complete`` using its last agent output.

        Called when the human decides the multi-turn exchange is finished
        (``baton execute interact --step-id X --done``) without the agent
        emitting the ``INTERACT_COMPLETE`` signal.

        Args:
            step_id: The step ID currently in ``interacting`` status.
        """
        ...
