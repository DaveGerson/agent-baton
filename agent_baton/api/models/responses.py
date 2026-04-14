"""Pydantic response models for the Agent Baton API.

Each response model includes a ``from_dataclass`` classmethod that converts
from the corresponding internal dataclass.  This keeps the conversion logic
co-located with the schema definition and ensures that the API contract is
decoupled from internal data representations.

The models are organized into groups:

- **Plan**: ``PlanStepResponse``, ``PlanGateResponse``,
  ``PlanPhaseResponse``, ``PlanResponse``
- **Execution**: ``StepResultResponse``, ``ExecutionResponse``,
  ``ActionResponse``
- **Decisions**: ``DecisionResponse``, ``DecisionListResponse``,
  ``ResolveResponse``
- **Events**: ``EventResponse``
- **Agents**: ``AgentResponse``, ``AgentListResponse``
- **Observability**: ``DashboardResponse``, ``TraceEventResponse``,
  ``TraceResponse``, ``AgentUsageResponse``, ``TaskUsageResponse``,
  ``UsageResponse``
- **System**: ``HealthResponse``, ``ReadyResponse``, ``WebhookResponse``,
  ``ErrorResponse``
- **PMO**: ``PmoProjectResponse``, ``PmoCardResponse``,
  ``PmoCardDetailResponse``, ``PmoSignalResponse``,
  ``ProgramHealthResponse``, ``PmoBoardResponse``
- **Forge/ADO**: ``InterviewQuestionResponse``, ``InterviewResponse``,
  ``AdoWorkItemResponse``, ``AdoSearchResponse``
- **Forge actions**: ``ForgeApproveResponse``, ``ExecuteCardResponse``
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Plan responses (mirrors execution.PlanStep / PlanGate / PlanPhase / MachinePlan)
# ---------------------------------------------------------------------------


class PlanStepResponse(BaseModel):
    """A single step within a plan phase.

    Maps to ``agent_baton.models.execution.PlanStep``.  Each step
    assigns a specific agent to a task with optional dependency
    ordering, path enforcement, and context file hints.
    """

    model_config = ConfigDict(from_attributes=True)

    step_id: str = Field(..., description="Step identifier (e.g. '1.1').")
    agent_name: str = Field(..., description="Agent assigned to this step.")
    task_description: str = Field(..., description="What the agent should do.")
    model: str = Field(default="sonnet", description="LLM model tier.")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Step IDs that must complete before this step.",
    )
    deliverables: list[str] = Field(
        default_factory=list,
        description="Expected output files or artifacts.",
    )
    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Filesystem paths this step may write to.",
    )
    blocked_paths: list[str] = Field(
        default_factory=list,
        description="Filesystem paths this step must not touch.",
    )
    context_files: list[str] = Field(
        default_factory=list,
        description="Files the agent should read before starting.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> PlanStepResponse:
        """Convert from ``agent_baton.models.execution.PlanStep``."""
        return cls.model_validate(obj, from_attributes=True)


class PlanGateResponse(BaseModel):
    """A QA gate attached to a plan phase.

    Maps to ``agent_baton.models.execution.PlanGate``.  Gates run
    after all steps in a phase complete and must pass before the
    engine advances to the next phase.
    """

    model_config = ConfigDict(from_attributes=True)

    gate_type: str = Field(..., description="Gate category (build, test, lint, spec, review).")
    command: str = Field(default="", description="Shell command to execute for the gate.")
    description: str = Field(default="", description="Human-readable gate description.")
    fail_on: list[str] = Field(
        default_factory=list,
        description="Criteria that cause the gate to fail.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> PlanGateResponse:
        """Convert from ``agent_baton.models.execution.PlanGate``."""
        return cls.model_validate(obj, from_attributes=True)


class PlanPhaseResponse(BaseModel):
    """A phase grouping steps and an optional gate.

    Maps to ``agent_baton.models.execution.PlanPhase``.  Phases are
    executed sequentially; all steps within a phase may run in
    parallel if they have no inter-step dependencies.
    """

    phase_id: int = Field(..., description="Phase index (0-based).")
    name: str = Field(..., description="Phase name.")
    steps: list[PlanStepResponse] = Field(
        default_factory=list,
        description="Steps in this phase.",
    )
    gate: Optional[PlanGateResponse] = Field(
        default=None,
        description="QA gate at the end of this phase, if any.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> PlanPhaseResponse:
        """Convert from ``agent_baton.models.execution.PlanPhase``."""
        steps = [PlanStepResponse.from_dataclass(s) for s in obj.steps]  # type: ignore[attr-defined]
        gate = PlanGateResponse.from_dataclass(obj.gate) if obj.gate else None  # type: ignore[attr-defined]
        return cls(
            phase_id=obj.phase_id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            steps=steps,
            gate=gate,
        )


class PlanResponse(BaseModel):
    """Full plan as returned from the planning endpoint.

    Maps to ``agent_baton.models.execution.MachinePlan``.  Contains
    the complete phase/step/gate hierarchy, risk classification,
    budget tier, and agent roster.
    """

    plan_id: str = Field(..., description="Unique task/plan identifier.")
    task_summary: str = Field(..., description="Human-readable task description.")
    risk_level: str = Field(default="LOW", description="Risk classification (LOW, MEDIUM, HIGH, CRITICAL).")
    budget_tier: str = Field(default="standard", description="Budget tier (lean, standard, full).")
    execution_mode: str = Field(default="phased", description="Execution strategy.")
    git_strategy: str = Field(default="commit-per-agent", description="Git commit strategy.")
    phases: list[PlanPhaseResponse] = Field(default_factory=list, description="Ordered execution phases.")
    total_steps: int = Field(..., description="Total number of steps across all phases.")
    agents: list[str] = Field(default_factory=list, description="All agent names used in the plan.")
    pattern_source: Optional[str] = Field(default=None, description="Learned pattern that influenced this plan.")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp.")

    @classmethod
    def from_dataclass(cls, obj: object) -> PlanResponse:
        """Convert from ``agent_baton.models.execution.MachinePlan``."""
        phases = [PlanPhaseResponse.from_dataclass(p) for p in obj.phases]  # type: ignore[attr-defined]
        return cls(
            plan_id=obj.task_id,  # type: ignore[attr-defined]
            task_summary=obj.task_summary,  # type: ignore[attr-defined]
            risk_level=obj.risk_level,  # type: ignore[attr-defined]
            budget_tier=obj.budget_tier,  # type: ignore[attr-defined]
            execution_mode=obj.execution_mode,  # type: ignore[attr-defined]
            git_strategy=obj.git_strategy,  # type: ignore[attr-defined]
            phases=phases,
            total_steps=obj.total_steps,  # type: ignore[attr-defined]
            agents=obj.all_agents,  # type: ignore[attr-defined]
            pattern_source=obj.pattern_source,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
        )


# ---------------------------------------------------------------------------
# Execution responses
# ---------------------------------------------------------------------------


class StepResultResponse(BaseModel):
    """Outcome of a completed step.

    Maps to ``agent_baton.models.execution.StepResult``.  Contains
    the agent's output, files changed, token usage, duration, and
    any error information from failed steps.
    """

    model_config = ConfigDict(from_attributes=True)

    step_id: str = Field(..., description="Step identifier.")
    agent_name: str = Field(..., description="Agent that executed the step.")
    status: str = Field(..., description="Outcome: complete, failed, dispatched.")
    outcome: str = Field(default="", description="Free-text result summary.")
    files_changed: list[str] = Field(default_factory=list, description="Files modified by this step.")
    commit_hash: str = Field(default="", description="Git commit hash, if any.")
    estimated_tokens: int = Field(default=0, description="Estimated token consumption.")
    duration_seconds: float = Field(default=0.0, description="Wall-clock duration in seconds.")
    retries: int = Field(default=0, description="Number of retry attempts.")
    error: str = Field(default="", description="Error message if the step failed.")
    completed_at: str = Field(default="", description="ISO 8601 completion timestamp.")

    @classmethod
    def from_dataclass(cls, obj: object) -> StepResultResponse:
        """Convert from ``agent_baton.models.execution.StepResult``."""
        return cls.model_validate(obj, from_attributes=True)


class ExecutionResponse(BaseModel):
    """Current state of a running or completed execution.

    Maps to ``agent_baton.models.execution.ExecutionState``.  Provides
    progress counters (steps completed/remaining/failed, gates passed),
    the full list of step and gate results, and the pending decision
    count for human-in-the-loop awareness.
    """

    task_id: str = Field(..., description="Execution/task identifier.")
    status: str = Field(..., description="Current status: running, gate_pending, complete, failed.")
    current_phase: int = Field(default=0, description="Index of the active phase.")
    current_step_index: int = Field(default=0, description="Index of the active step within the phase.")
    steps_completed: int = Field(default=0, description="Number of steps finished successfully.")
    steps_remaining: int = Field(default=0, description="Number of steps not yet started or in progress.")
    steps_failed: int = Field(default=0, description="Number of steps that failed.")
    gates_passed: int = Field(default=0, description="Number of QA gates passed.")
    pending_decisions: int = Field(default=0, description="Number of unresolved human decisions.")
    step_results: list[StepResultResponse] = Field(default_factory=list, description="Results for completed steps.")
    gate_results: list[dict] = Field(default_factory=list, description="Results for completed gates.")
    plan_id: str = Field(default="", description="ID of the plan being executed.")
    started_at: str = Field(default="", description="ISO 8601 start timestamp.")
    completed_at: str = Field(default="", description="ISO 8601 completion timestamp (empty if still running).")

    @classmethod
    def from_dataclass(
        cls,
        obj: object,
        pending_decisions: int = 0,
    ) -> ExecutionResponse:
        """Convert from ``agent_baton.models.execution.ExecutionState``.

        Computes derived counters (steps_completed, steps_remaining,
        steps_failed, gates_passed) from the raw step and gate result
        lists rather than trusting any pre-computed values on the state.

        Args:
            obj: An ``ExecutionState`` dataclass instance.
            pending_decisions: Number of unresolved human decisions
                (computed externally by the route handler).

        Returns:
            A fully populated ``ExecutionResponse``.
        """
        step_results = [
            StepResultResponse.from_dataclass(r) for r in obj.step_results  # type: ignore[attr-defined]
        ]
        gate_results = [g.to_dict() for g in obj.gate_results]  # type: ignore[attr-defined]

        completed_ids = {r.step_id for r in step_results if r.status == "complete"}
        failed_ids = {r.step_id for r in step_results if r.status == "failed"}
        total_steps = obj.plan.total_steps  # type: ignore[attr-defined]
        gates_passed = sum(1 for g in obj.gate_results if g.passed)  # type: ignore[attr-defined]

        return cls(
            task_id=obj.task_id,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            current_phase=obj.current_phase,  # type: ignore[attr-defined]
            current_step_index=obj.current_step_index,  # type: ignore[attr-defined]
            steps_completed=len(completed_ids),
            steps_remaining=max(0, total_steps - len(completed_ids) - len(failed_ids)),
            steps_failed=len(failed_ids),
            gates_passed=gates_passed,
            pending_decisions=pending_decisions,
            step_results=step_results,
            gate_results=gate_results,
            plan_id=obj.plan.task_id,  # type: ignore[attr-defined]
            started_at=obj.started_at,  # type: ignore[attr-defined]
            completed_at=obj.completed_at,  # type: ignore[attr-defined]
        )


class ActionResponse(BaseModel):
    """An instruction from the execution engine to the driving session.

    Maps to ``agent_baton.models.execution.ExecutionAction``.  The
    ``action_type`` determines which fields are populated:

    - ``dispatch``: ``agent_name``, ``agent_model``, ``step_id``
    - ``gate``: ``gate_type``, ``gate_command``, ``phase_id``
    - ``complete``/``failed``: ``summary``
    - ``wait``: no additional fields (waiting for external input)

    Internal-only fields (``delegation_prompt``, ``path_enforcement``)
    are intentionally omitted from the API response.
    """

    action_type: str = Field(..., description="Action kind: dispatch, gate, complete, failed, wait.")
    message: str = Field(default="", description="Human-readable description.")
    agent_name: Optional[str] = Field(default=None, description="Agent to dispatch (dispatch actions).")
    agent_model: Optional[str] = Field(default=None, description="Model tier for the agent.")
    step_id: Optional[str] = Field(default=None, description="Step being dispatched.")
    gate_type: Optional[str] = Field(default=None, description="Gate category (gate actions).")
    gate_command: Optional[str] = Field(default=None, description="Shell command for the gate.")
    phase_id: Optional[int] = Field(default=None, description="Phase index (gate actions).")
    summary: Optional[str] = Field(default=None, description="Final summary (complete/failed actions).")
    parallel_actions: list[ActionResponse] = Field(
        default_factory=list,
        description="Sub-actions for parallel dispatch.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> ActionResponse:
        """Convert from ``agent_baton.models.execution.ExecutionAction``.

        Omits internal-only fields (``delegation_prompt``,
        ``path_enforcement``) that should not be exposed to API clients.
        Recursively converts any ``parallel_actions``.

        Args:
            obj: An ``ExecutionAction`` dataclass instance.

        Returns:
            An ``ActionResponse`` with the action type's relevant
            fields populated.
        """
        parallel = [ActionResponse.from_dataclass(a) for a in obj.parallel_actions]  # type: ignore[attr-defined]
        return cls(
            action_type=obj.action_type.value,  # type: ignore[attr-defined]
            message=obj.message,  # type: ignore[attr-defined]
            agent_name=obj.agent_name or None,  # type: ignore[attr-defined]
            agent_model=obj.agent_model or None,  # type: ignore[attr-defined]
            step_id=obj.step_id or None,  # type: ignore[attr-defined]
            gate_type=obj.gate_type or None,  # type: ignore[attr-defined]
            gate_command=obj.gate_command or None,  # type: ignore[attr-defined]
            phase_id=obj.phase_id if obj.phase_id else None,  # type: ignore[attr-defined]
            summary=obj.summary or None,  # type: ignore[attr-defined]
            parallel_actions=parallel,
        )


# ---------------------------------------------------------------------------
# Decision responses
# ---------------------------------------------------------------------------


class DecisionResponse(BaseModel):
    """A pending or resolved human decision.

    Maps to ``agent_baton.models.decision.DecisionRequest``.  When
    fetched via the detail endpoint (``GET /decisions/{request_id}``),
    the ``context_file_contents`` field is populated with inline file
    contents so remote UIs don't need filesystem access.
    """

    request_id: str = Field(..., description="Unique decision request identifier.")
    task_id: str = Field(..., description="Task this decision belongs to.")
    decision_type: str = Field(..., description="Category: gate_approval, escalation, plan_review.")
    summary: str = Field(..., description="Human-readable context for the decision.")
    options: list[str] = Field(default_factory=list, description="Available choices.")
    deadline: Optional[str] = Field(default=None, description="ISO 8601 expiry timestamp.")
    context_files: list[str] = Field(
        default_factory=list,
        description="Paths to context files the reviewer should read.",
    )
    created_at: str = Field(default="", description="ISO 8601 creation timestamp.")
    status: str = Field(default="pending", description="Current status: pending, resolved, expired.")
    context_file_contents: Optional[dict[str, str]] = Field(
        default=None,
        description="Inline contents of context files (populated by the detail endpoint).",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> DecisionResponse:
        """Convert from ``agent_baton.models.decision.DecisionRequest``."""
        return cls(
            request_id=obj.request_id,  # type: ignore[attr-defined]
            task_id=obj.task_id,  # type: ignore[attr-defined]
            decision_type=obj.decision_type,  # type: ignore[attr-defined]
            summary=obj.summary,  # type: ignore[attr-defined]
            options=list(obj.options),  # type: ignore[attr-defined]
            deadline=obj.deadline,  # type: ignore[attr-defined]
            context_files=list(obj.context_files),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
        )


class DecisionListResponse(BaseModel):
    """Wrapper for a list of decisions."""

    count: int = Field(..., description="Number of decisions in the list.")
    decisions: list[DecisionResponse] = Field(default_factory=list)

    @classmethod
    def from_dataclass_list(cls, items: list) -> DecisionListResponse:
        """Convert a list of ``DecisionRequest`` dataclasses.

        Args:
            items: List of ``DecisionRequest`` dataclass instances.

        Returns:
            A ``DecisionListResponse`` with the converted decisions
            and an accurate count.
        """
        decisions = [DecisionResponse.from_dataclass(d) for d in items]
        return cls(count=len(decisions), decisions=decisions)


class ResolveResponse(BaseModel):
    """Confirmation that a decision was resolved."""

    resolved: bool = Field(..., description="Whether the decision was successfully resolved.")
    execution_resumed: bool = Field(
        default=False,
        description="Whether execution automatically resumed after resolution.",
    )


# ---------------------------------------------------------------------------
# Event responses
# ---------------------------------------------------------------------------


class EventResponse(BaseModel):
    """A single event from the execution event stream.

    Maps to ``agent_baton.models.events.Event``.  Events are delivered
    via the SSE endpoint (``GET /events/{task_id}``) or via outbound
    webhooks.  The ``topic`` field uses a dot-separated namespace
    (e.g. ``step.completed``, ``gate.required``).
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: str = Field(..., description="Unique event identifier.")
    timestamp: str = Field(..., description="ISO 8601 event timestamp.")
    topic: str = Field(..., description="Event topic (e.g. 'step.completed').")
    task_id: str = Field(..., description="Task this event belongs to.")
    sequence: int = Field(default=0, description="Monotonic sequence number within the task.")
    payload: dict = Field(default_factory=dict, description="Event-specific data.")

    @classmethod
    def from_dataclass(cls, obj: object) -> EventResponse:
        """Convert from ``agent_baton.models.events.Event``."""
        return cls.model_validate(obj, from_attributes=True)


# ---------------------------------------------------------------------------
# Agent responses
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """An agent definition available in the registry.

    Maps to ``agent_baton.models.agent.AgentDefinition``.  The full
    ``instructions`` markdown body and ``source_path`` are intentionally
    omitted to keep response payloads compact.  Use the agent definition
    files on disk for the full content.
    """

    name: str = Field(..., description="Agent identifier (e.g. 'backend-engineer--python').")
    description: str = Field(..., description="What this agent does.")
    model: str = Field(default="sonnet", description="Default LLM model tier.")
    permission_mode: str = Field(default="default", description="Tool permission mode.")
    color: Optional[str] = Field(default=None, description="Display color for dashboards.")
    tools: list[str] = Field(default_factory=list, description="Tools this agent may use.")
    category: str = Field(default="Engineering", description="Agent category.")
    base_name: str = Field(default="", description="Name without flavor suffix.")
    flavor: Optional[str] = Field(default=None, description="Flavor suffix, if any.")

    @classmethod
    def from_dataclass(cls, obj: object) -> AgentResponse:
        """Convert from ``agent_baton.models.agent.AgentDefinition``.

        Omits ``source_path`` and ``instructions`` (large markdown body).
        """
        return cls(
            name=obj.name,  # type: ignore[attr-defined]
            description=obj.description,  # type: ignore[attr-defined]
            model=obj.model,  # type: ignore[attr-defined]
            permission_mode=obj.permission_mode,  # type: ignore[attr-defined]
            color=obj.color,  # type: ignore[attr-defined]
            tools=list(obj.tools),  # type: ignore[attr-defined]
            category=obj.category.value,  # type: ignore[attr-defined]
            base_name=obj.base_name,  # type: ignore[attr-defined]
            flavor=obj.flavor,  # type: ignore[attr-defined]
        )


class AgentListResponse(BaseModel):
    """Wrapper for a list of agents."""

    count: int = Field(..., description="Number of agents in the list.")
    agents: list[AgentResponse] = Field(default_factory=list)

    @classmethod
    def from_dataclass_list(cls, items: list) -> AgentListResponse:
        """Convert a list of ``AgentDefinition`` dataclasses.

        Args:
            items: List of ``AgentDefinition`` dataclass instances.

        Returns:
            An ``AgentListResponse`` with the converted agents and
            an accurate count.
        """
        agents = [AgentResponse.from_dataclass(a) for a in items]
        return cls(count=len(agents), agents=agents)


# ---------------------------------------------------------------------------
# Dashboard / Trace / Usage responses
# ---------------------------------------------------------------------------


class DashboardResponse(BaseModel):
    """Dashboard data -- pre-rendered markdown plus structured metrics.

    The ``dashboard_markdown`` field contains a fully rendered dashboard
    suitable for direct display.  The ``metrics`` dict is reserved for
    future structured metric delivery (currently empty).
    """

    dashboard_markdown: str = Field(..., description="Pre-rendered markdown dashboard content.")
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured metrics (keys vary by dashboard type).",
    )


class TraceEventResponse(BaseModel):
    """A single event within a task trace."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: str = Field(..., description="ISO 8601 event timestamp.")
    event_type: str = Field(..., description="Event category (agent_start, gate_check, etc.).")
    agent_name: Optional[str] = Field(default=None, description="Agent involved, if any.")
    phase: int = Field(default=0, description="Phase index.")
    step: int = Field(default=0, description="Step index.")
    details: dict = Field(default_factory=dict, description="Event-specific details.")
    duration_seconds: Optional[float] = Field(default=None, description="Duration in seconds, if measured.")

    @classmethod
    def from_dataclass(cls, obj: object) -> TraceEventResponse:
        """Convert from ``agent_baton.models.trace.TraceEvent``."""
        return cls.model_validate(obj, from_attributes=True)


class TraceResponse(BaseModel):
    """Complete structured trace for a task execution.

    Maps to ``agent_baton.models.trace.TaskTrace``.  Contains a
    snapshot of the plan as it existed at execution start and a
    chronologically ordered list of trace events covering the full
    lifecycle (dispatches, gate checks, completions, failures).
    """

    task_id: str = Field(..., description="Task identifier.")
    plan_snapshot: dict = Field(default_factory=dict, description="Snapshot of the plan at execution start.")
    events: list[TraceEventResponse] = Field(default_factory=list, description="Ordered trace events.")
    started_at: str = Field(default="", description="ISO 8601 start timestamp.")
    completed_at: Optional[str] = Field(default=None, description="ISO 8601 completion timestamp.")
    outcome: Optional[str] = Field(default=None, description="Final outcome (SHIP, REVISE, BLOCK, etc.).")

    @classmethod
    def from_dataclass(cls, obj: object) -> TraceResponse:
        """Convert from ``agent_baton.models.trace.TaskTrace``."""
        events = [TraceEventResponse.from_dataclass(e) for e in obj.events]  # type: ignore[attr-defined]
        return cls(
            task_id=obj.task_id,  # type: ignore[attr-defined]
            plan_snapshot=obj.plan_snapshot,  # type: ignore[attr-defined]
            events=events,
            started_at=obj.started_at,  # type: ignore[attr-defined]
            completed_at=obj.completed_at,  # type: ignore[attr-defined]
            outcome=obj.outcome,  # type: ignore[attr-defined]
        )


class AgentUsageResponse(BaseModel):
    """Usage record for a single agent within a task."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="Agent name.")
    model: str = Field(default="sonnet", description="Model tier used.")
    steps: int = Field(default=1, description="Number of steps executed.")
    retries: int = Field(default=0, description="Retry count.")
    gate_results: list[str] = Field(default_factory=list, description="Gate outcomes for this agent's work.")
    estimated_tokens: int = Field(default=0, description="Estimated token consumption.")
    duration_seconds: float = Field(default=0.0, description="Total wall-clock time in seconds.")

    @classmethod
    def from_dataclass(cls, obj: object) -> AgentUsageResponse:
        """Convert from ``agent_baton.models.usage.AgentUsageRecord``."""
        return cls.model_validate(obj, from_attributes=True)


class TaskUsageResponse(BaseModel):
    """Usage record for a complete task."""

    task_id: str = Field(..., description="Task identifier.")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the usage record.")
    agents_used: list[AgentUsageResponse] = Field(default_factory=list, description="Per-agent usage breakdown.")
    total_agents: int = Field(default=0, description="Number of agents involved.")
    risk_level: str = Field(default="LOW", description="Risk classification.")
    sequencing_mode: str = Field(default="phased_delivery", description="Execution sequencing mode.")
    gates_passed: int = Field(default=0, description="Number of gates passed.")
    gates_failed: int = Field(default=0, description="Number of gates failed.")
    outcome: str = Field(default="", description="Final outcome (SHIP, REVISE, BLOCK, etc.).")

    @classmethod
    def from_dataclass(cls, obj: object) -> TaskUsageResponse:
        """Convert from ``agent_baton.models.usage.TaskUsageRecord``."""
        agents = [AgentUsageResponse.from_dataclass(a) for a in obj.agents_used]  # type: ignore[attr-defined]
        return cls(
            task_id=obj.task_id,  # type: ignore[attr-defined]
            timestamp=obj.timestamp,  # type: ignore[attr-defined]
            agents_used=agents,
            total_agents=obj.total_agents,  # type: ignore[attr-defined]
            risk_level=obj.risk_level,  # type: ignore[attr-defined]
            sequencing_mode=obj.sequencing_mode,  # type: ignore[attr-defined]
            gates_passed=obj.gates_passed,  # type: ignore[attr-defined]
            gates_failed=obj.gates_failed,  # type: ignore[attr-defined]
            outcome=obj.outcome,  # type: ignore[attr-defined]
        )


class UsageResponse(BaseModel):
    """Aggregated usage data with summary statistics.

    The ``records`` list contains individual task usage entries, and the
    ``summary`` dict provides aggregated metrics across all records
    including ``total_tasks``, ``total_tokens``, ``total_agents``, and
    ``outcome_counts``.
    """

    records: list[TaskUsageResponse] = Field(default_factory=list, description="Individual task usage records.")
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated summary (total_tasks, total_tokens, avg_duration, etc.).",
    )


# ---------------------------------------------------------------------------
# System responses
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Service status (e.g. 'healthy').")
    version: str = Field(..., description="Agent Baton version string.")
    uptime_seconds: float = Field(..., description="Seconds since the service started.")


class ReadyResponse(BaseModel):
    """Readiness probe response."""

    ready: bool = Field(..., description="Whether the service is ready to accept work.")
    daemon_running: bool = Field(..., description="Whether the background daemon is active.")
    pending_decisions: int = Field(
        default=0,
        description="Number of unresolved human decisions blocking execution.",
    )


class WebhookResponse(BaseModel):
    """Confirmation of a registered webhook."""

    webhook_id: str = Field(..., description="Unique webhook registration identifier.")
    url: str = Field(..., description="Registered callback URL.")
    events: list[str] = Field(default_factory=list, description="Subscribed event topics.")
    created: str = Field(..., description="ISO 8601 registration timestamp.")


class ErrorResponse(BaseModel):
    """Standard error response body."""

    error: str = Field(..., description="Short error classification.")
    detail: Optional[str] = Field(
        default=None,
        description="Additional context about the error.",
    )


# ---------------------------------------------------------------------------
# PMO responses
# ---------------------------------------------------------------------------


class PmoProjectResponse(BaseModel):
    """A project registered with the PMO."""

    project_id: str = Field(..., description="Unique project slug.")
    name: str = Field(..., description="Human-readable project name.")
    path: str = Field(..., description="Absolute filesystem path to the project root.")
    program: str = Field(..., description="Program code (e.g. 'NDS', 'ATL').")
    color: str = Field(default="", description="Display color for the PMO board.")
    description: str = Field(default="", description="Optional project description.")
    registered_at: str = Field(default="", description="ISO 8601 registration timestamp.")
    ado_project: str = Field(default="", description="Azure DevOps project name (reserved for future use).")


class PmoCardResponse(BaseModel):
    """A Kanban card representing a plan's lifecycle state."""

    card_id: str = Field(..., description="Task ID from the underlying MachinePlan.")
    project_id: str = Field(..., description="Owning project ID.")
    program: str = Field(..., description="Program code.")
    title: str = Field(..., description="Plan task summary used as the card title.")
    column: str = Field(..., description="Kanban column (queued, planning, executing, etc.).")
    risk_level: str = Field(default="LOW", description="Risk classification (LOW, MEDIUM, HIGH, CRITICAL).")
    priority: int = Field(default=0, description="Priority: 0=normal, 1=high, 2=critical.")
    agents: list[str] = Field(default_factory=list, description="Agent names used in the plan.")
    steps_completed: int = Field(default=0, description="Number of steps completed.")
    steps_total: int = Field(default=0, description="Total steps in the plan.")
    gates_passed: int = Field(default=0, description="Number of QA gates passed.")
    current_phase: str = Field(default="", description="Name of the currently active phase.")
    error: str = Field(default="", description="Last failure error message, if any.")
    created_at: str = Field(default="", description="ISO 8601 plan creation timestamp.")
    updated_at: str = Field(default="", description="ISO 8601 last-updated timestamp.")
    external_id: str = Field(default="", description="Azure DevOps work item ID (reserved for future use).")


class PmoCardDetailResponse(PmoCardResponse):
    """Extended card detail response, including the raw plan dict when available.

    Returned by ``GET /pmo/cards/{card_id}``.  Extends ``PmoCardResponse``
    with the ``plan`` field containing the full serialised ``MachinePlan``
    dict when the plan file can be found on disk.
    """

    plan: Optional[dict] = Field(
        default=None,
        description="Full plan dict (MachinePlan.to_dict() shape) when available.",
    )


class PmoSignalResponse(BaseModel):
    """A signal (bug, escalation, blocker) from the Signals Bar."""

    signal_id: str = Field(..., description="Unique signal identifier.")
    signal_type: str = Field(..., description="Signal category: bug, escalation, or blocker.")
    title: str = Field(..., description="Short signal title.")
    description: str = Field(default="", description="Additional signal context.")
    source_project_id: str = Field(default="", description="Project that generated this signal.")
    severity: str = Field(default="medium", description="Severity: low, medium, high, or critical.")
    status: str = Field(default="open", description="Signal status: open, triaged, or resolved.")
    created_at: str = Field(default="", description="ISO 8601 creation timestamp.")
    forge_task_id: str = Field(default="", description="Plan task ID if this signal was triaged by Forge.")


class ResolveSignalResponse(PmoSignalResponse):
    """Response for POST /pmo/signals/{signal_id}/resolve.

    Extends ``PmoSignalResponse`` with a ``resolved`` flag so callers
    can confirm the operation succeeded and update their local state
    with the full, authoritative signal object in a single round trip.
    """

    resolved: bool = Field(default=True, description="Always True on a successful resolve.")


class ProgramHealthResponse(BaseModel):
    """Aggregate health metrics for a program."""

    program: str = Field(..., description="Program code.")
    total_plans: int = Field(default=0, description="Total number of tracked plans.")
    active: int = Field(default=0, description="Plans currently in progress.")
    completed: int = Field(default=0, description="Plans in the deployed column.")
    blocked: int = Field(default=0, description="Plans awaiting human input.")
    failed: int = Field(default=0, description="Plans with a failure error set.")
    completion_pct: float = Field(default=0.0, description="Percentage of plans completed.")


class PmoBoardResponse(BaseModel):
    """Full Kanban board state: all cards plus per-program health.

    Returned by ``GET /pmo/board`` and ``GET /pmo/board/{program}``.
    The ``cards`` list includes every tracked plan across all registered
    projects, and ``health`` provides aggregate metrics per program code.
    """

    cards: list[PmoCardResponse] = Field(
        default_factory=list,
        description="All Kanban cards across all registered projects.",
    )
    health: dict[str, ProgramHealthResponse] = Field(
        default_factory=dict,
        description="Per-program health metrics keyed by program code.",
    )


# ---------------------------------------------------------------------------
# Forge interview / ADO responses
# ---------------------------------------------------------------------------


class InterviewQuestionResponse(BaseModel):
    """A single structured interview question."""

    id: str = Field(..., description="Question identifier.")
    question: str = Field(..., description="The question text.")
    context: str = Field(default="", description="Why this question matters.")
    answer_type: str = Field(..., description="'choice' or 'text'.")
    choices: Optional[list[str]] = Field(default=None, description="Options for choice type.")


class InterviewResponse(BaseModel):
    """Response from POST /pmo/forge/interview."""

    questions: list[InterviewQuestionResponse] = Field(
        ...,
        description="3-5 structured interview questions.",
    )


class AdoWorkItemResponse(BaseModel):
    """An Azure DevOps work item (placeholder)."""

    id: str = Field(..., description="Work item ID (e.g. 'F-4203').")
    title: str = Field(..., description="Work item title.")
    type: str = Field(..., description="Feature, Bug, or Story.")
    program: str = Field(..., description="Program code.")
    owner: str = Field(..., description="Assigned owner.")
    priority: str = Field(..., description="Priority level.")
    description: str = Field(default="", description="Work item description / PRD.")


class AdoSearchResponse(BaseModel):
    """Response from GET /pmo/ado/search."""

    items: list[AdoWorkItemResponse] = Field(
        default_factory=list,
        description="Matching ADO work items.",
    )
    message: str = Field(
        default="",
        description="Status message (e.g. configuration guidance when ADO is not connected).",
    )


# ---------------------------------------------------------------------------
# Forge action responses
# ---------------------------------------------------------------------------


class ForgeApproveResponse(BaseModel):
    """Response from POST /pmo/forge/approve.

    Confirms that an approved plan dict has been persisted to the
    project's ``team-context`` directory.
    """

    saved: bool = Field(..., description="True when the plan was written successfully.")
    path: str = Field(..., description="Absolute path to the saved plan.json file.")


class ExecuteCardResponse(BaseModel):
    """Response from POST /pmo/execute/{card_id}.

    Returned immediately after a headless execution subprocess is
    spawned.  The execution continues asynchronously in the background.
    """

    task_id: str = Field(..., description="Card/task ID that was launched.")
    pid: int = Field(..., description="OS process ID of the spawned subprocess.")
    status: str = Field(
        default="launched",
        description="Always 'launched' on success.",
    )
    model: str = Field(..., description="LLM model tier used for execution.")
    dry_run: bool = Field(
        default=False,
        description="When True, the subprocess runs in dry-run mode without making changes.",
    )


# ---------------------------------------------------------------------------
# External items responses
# ---------------------------------------------------------------------------


class ExternalItemResponse(BaseModel):
    """A work item fetched from an external source (ADO, GitHub, Jira, Linear).

    Rows come from the ``external_items`` table in central.db.  Only
    fields that are useful for display in the PMO dashboard are surfaced
    here; the full raw payload is omitted.
    """

    id: int = Field(..., description="Row ID in external_items.")
    source_id: str = Field(..., description="Baton source ID (external_sources.source_id).")
    external_id: str = Field(..., description="ID in the source system (e.g. 'JIRA-42', 'GH-99').")
    item_type: str = Field(default="", description="Canonical type: feature, bug, epic, story, task.")
    title: str = Field(default="", description="Short human-readable title.")
    description: str = Field(default="", description="Full description or body text.")
    state: str = Field(default="", description="Workflow state from the source system.")
    assigned_to: str = Field(default="", description="Current assignee display name.")
    priority: str = Field(default="", description="Priority string from the source system.")
    tags: list[str] = Field(default_factory=list, description="Label/tag list.")
    url: str = Field(default="", description="Link to the item in the source system's web UI.")
    updated_at: str = Field(default="", description="ISO-8601 timestamp of last update in source.")
    source_type: str = Field(default="", description="Source adapter type: ado, github, jira, linear.")


class ExternalMappingResponse(BaseModel):
    """A mapping between an external work item and a baton plan/execution.

    Rows come from the ``external_mappings`` table in central.db joined
    with ``external_items`` so the caller does not need a second request.
    """

    id: int = Field(..., description="Row ID in external_mappings.")
    source_id: str = Field(..., description="Baton source ID.")
    external_id: str = Field(..., description="ID in the source system.")
    project_id: str = Field(..., description="Baton project ID.")
    task_id: str = Field(default="", description="Plan task ID this item is mapped to.")
    mapping_type: str = Field(default="", description="Relationship type (e.g. 'implements', 'tracks').")
    created_at: str = Field(default="", description="ISO-8601 timestamp.")
    item: Optional[ExternalItemResponse] = Field(
        default=None,
        description="Linked external item details when available.",
    )


# ---------------------------------------------------------------------------
# Gate approval responses
# ---------------------------------------------------------------------------


class PendingGateResponse(BaseModel):
    """A single execution currently paused and waiting for gate approval.

    Returned as an element of ``GET /pmo/gates/pending``.  The
    ``approval_context`` field contains the markdown review summary
    built by the execution engine so the approver can make an informed
    decision without leaving the PMO UI.
    """

    task_id: str = Field(..., description="Task ID of the paused execution.")
    project_id: str = Field(..., description="Project that owns this execution.")
    phase_id: int = Field(..., description="Phase ID awaiting approval.")
    phase_name: str = Field(default="", description="Human-readable name of the phase.")
    approval_context: str = Field(
        default="",
        description="Markdown review summary produced by the execution engine.",
    )
    approval_options: list[str] = Field(
        default_factory=list,
        description="Choices available to the reviewer (approve, reject, approve-with-feedback).",
    )
    task_summary: str = Field(default="", description="Top-level task description.")
    current_phase_name: str = Field(
        default="",
        description="Name of the phase currently awaiting approval.",
    )


class GateActionResponse(BaseModel):
    """Confirmation that a gate approval or rejection was recorded.

    Returned by ``POST /pmo/gates/{task_id}/approve`` and
    ``POST /pmo/gates/{task_id}/reject``.
    """

    task_id: str = Field(..., description="Task ID the decision was recorded against.")
    phase_id: int = Field(..., description="Phase ID the decision applies to.")
    result: str = Field(
        ...,
        description="The recorded result: 'approve', 'approve-with-feedback', or 'reject'.",
    )
    recorded: bool = Field(default=True, description="Always True on success.")


# ---------------------------------------------------------------------------
# Learning responses
# ---------------------------------------------------------------------------


class LearningIssueResponse(BaseModel):
    """A learning issue record from the ledger (summary view, no evidence).

    Returned by ``GET /api/v1/learn/issues`` and mutation endpoints.
    Maps to ``agent_baton.models.learning.LearningIssue`` with the
    ``evidence`` list omitted for compactness.
    """

    issue_id: str = Field(..., description="UUID identifying this issue.")
    issue_type: str = Field(
        ...,
        description=(
            "Category: routing_mismatch, agent_degradation, knowledge_gap, "
            "roster_bloat, gate_mismatch, pattern_drift, or prompt_evolution."
        ),
    )
    severity: str = Field(
        ...,
        description="Impact rating: low, medium, high, or critical.",
    )
    status: str = Field(
        ...,
        description=(
            "Lifecycle state: open, investigating, proposed, "
            "applied, resolved, or wontfix."
        ),
    )
    title: str = Field(..., description="Human-readable summary of the issue.")
    target: str = Field(
        ...,
        description="Subject of the issue (agent name, flavor key, etc.).",
    )
    evidence_count: int = Field(
        default=0,
        description="Number of evidence entries accumulated so far.",
    )
    first_seen: str = Field(default="", description="ISO 8601 timestamp of first observation.")
    last_seen: str = Field(default="", description="ISO 8601 timestamp of most recent observation.")
    occurrence_count: int = Field(
        default=1,
        description="Total times this signal has been observed.",
    )
    proposed_fix: Optional[str] = Field(
        default=None,
        description="Recommended remediation description, if any.",
    )
    resolution: Optional[str] = Field(
        default=None,
        description="How the issue was resolved, if resolved.",
    )
    resolution_type: Optional[str] = Field(
        default=None,
        description="Resolution attribution: auto, human, or interview.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> LearningIssueResponse:
        """Convert from ``agent_baton.models.learning.LearningIssue``.

        Args:
            obj: A ``LearningIssue`` dataclass instance.

        Returns:
            A ``LearningIssueResponse`` with ``evidence_count`` derived
            from the length of the evidence list.
        """
        from agent_baton.models.learning import LearningIssue  # local import to avoid circularity
        issue: LearningIssue = obj  # type: ignore[assignment]
        return cls(
            issue_id=issue.issue_id,
            issue_type=issue.issue_type,
            severity=issue.severity,
            status=issue.status,
            title=issue.title,
            target=issue.target,
            evidence_count=len(issue.evidence),
            first_seen=issue.first_seen,
            last_seen=issue.last_seen,
            occurrence_count=issue.occurrence_count,
            proposed_fix=issue.proposed_fix,
            resolution=issue.resolution,
            resolution_type=issue.resolution_type,
        )


class LearningEvidenceResponse(BaseModel):
    """A single piece of evidence contributing to a learning issue."""

    timestamp: str = Field(..., description="ISO 8601 timestamp of the observation.")
    source_task_id: str = Field(..., description="Execution task_id that produced this signal.")
    detail: str = Field(..., description="Human-readable description of what was observed.")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured payload (agent names, scores, gate commands, etc.).",
    )


class LearningIssueDetailResponse(LearningIssueResponse):
    """Full learning issue including all evidence entries.

    Returned by ``GET /api/v1/learn/issues/{issue_id}``.
    Extends ``LearningIssueResponse`` with the complete ``evidence`` list.
    """

    evidence: list[LearningEvidenceResponse] = Field(
        default_factory=list,
        description="Chronological list of evidence entries.",
    )

    @classmethod
    def from_dataclass(cls, obj: object) -> LearningIssueDetailResponse:  # type: ignore[override]
        """Convert from ``agent_baton.models.learning.LearningIssue`` with evidence.

        Args:
            obj: A ``LearningIssue`` dataclass instance.

        Returns:
            A ``LearningIssueDetailResponse`` with the full evidence list
            and all summary fields populated.
        """
        from agent_baton.models.learning import LearningIssue  # local import
        issue: LearningIssue = obj  # type: ignore[assignment]
        evidence = [
            LearningEvidenceResponse(
                timestamp=e.timestamp,
                source_task_id=e.source_task_id,
                detail=e.detail,
                data=e.data,
            )
            for e in issue.evidence
        ]
        return cls(
            issue_id=issue.issue_id,
            issue_type=issue.issue_type,
            severity=issue.severity,
            status=issue.status,
            title=issue.title,
            target=issue.target,
            evidence_count=len(evidence),
            first_seen=issue.first_seen,
            last_seen=issue.last_seen,
            occurrence_count=issue.occurrence_count,
            proposed_fix=issue.proposed_fix,
            resolution=issue.resolution,
            resolution_type=issue.resolution_type,
            evidence=evidence,
        )


class LearningAnalyzeResponse(BaseModel):
    """Result of a ``POST /api/v1/learn/analyze`` cycle.

    Lists all candidate issues (open issues with confidence computed)
    and a count of those that were promoted to ``"proposed"`` status.
    """

    candidates: list[LearningIssueResponse] = Field(
        default_factory=list,
        description="Issues reviewed during the analysis cycle.",
    )
    proposed_count: int = Field(
        default=0,
        description=(
            "Number of issues that crossed the auto-apply threshold "
            "and were promoted to 'proposed' status."
        ),
    )


class ApplyLearningFixResponse(BaseModel):
    """Result of ``POST /api/v1/learn/issues/{issue_id}/apply``.

    Confirms the resolver ran and reports the issue's new state.
    """

    issue_id: str = Field(..., description="The issue that was resolved.")
    resolution: str = Field(..., description="Human-readable description of the fix applied.")
    status: str = Field(
        default="applied",
        description="The issue's new lifecycle status after the fix.",
    )


class RecordFeedbackResponse(BaseModel):
    """Result of ``POST /api/v1/executions/{task_id}/feedback``.

    Confirms the feedback answer was recorded and returns the next
    actions the orchestrator should dispatch.
    """

    recorded: bool = Field(default=True, description="Always True on success.")
    next_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Next batch of dispatchable actions after the plan amendment.",
    )
