"""Pydantic response models for the Agent Baton API.

Each response model includes a ``from_dataclass`` classmethod that converts
from the corresponding internal dataclass.  This keeps the conversion logic
co-located with the schema definition.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Plan responses (mirrors execution.PlanStep / PlanGate / PlanPhase / MachinePlan)
# ---------------------------------------------------------------------------


class PlanStepResponse(BaseModel):
    """A single step within a plan phase."""

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
    """A QA gate attached to a plan phase."""

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
    """A phase grouping steps and an optional gate."""

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
    """Full plan as returned from the planning endpoint."""

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
    """Outcome of a completed step."""

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
    """Current state of a running or completed execution."""

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
        """Convert from ``agent_baton.models.execution.ExecutionState``."""
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
    """An instruction from the execution engine to the driving session."""

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

        Omits internal-only fields: ``delegation_prompt``, ``path_enforcement``.
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
    """A pending or resolved human decision."""

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
    """A single event from the execution event stream."""

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
    """An agent definition available in the registry."""

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
        agents = [AgentResponse.from_dataclass(a) for a in items]
        return cls(count=len(agents), agents=agents)


# ---------------------------------------------------------------------------
# Dashboard / Trace / Usage responses
# ---------------------------------------------------------------------------


class DashboardResponse(BaseModel):
    """Dashboard data — pre-rendered markdown plus structured metrics."""

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
    """Complete structured trace for a task execution."""

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
    """Aggregated usage data with summary statistics."""

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
