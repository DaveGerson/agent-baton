"""Pydantic request models for the Agent Baton API.

Each model validates incoming JSON payloads.  Field descriptions are
surfaced in the auto-generated OpenAPI schema.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CreatePlanRequest(BaseModel):
    """Request body for POST /plans — generate an execution plan."""

    description: str = Field(
        ...,
        min_length=1,
        description="Natural-language description of the task to plan.",
    )
    task_type: Optional[str] = Field(
        default=None,
        description="Optional task classifier hint (e.g. 'feature', 'bugfix', 'refactor').",
    )
    agents: Optional[list[str]] = Field(
        default=None,
        description="Explicit agent roster override.  If omitted the planner selects agents.",
    )
    project_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the target project.  Defaults to the daemon's working directory.",
    )


class StartExecutionRequest(BaseModel):
    """Request body for POST /executions — begin executing a plan.

    Supply *either* ``plan_id`` (referencing a previously created plan) or
    ``plan`` (an inline plan dict).  Providing both or neither is an error.
    """

    plan_id: Optional[str] = Field(
        default=None,
        description="ID of a previously created plan to execute.",
    )
    plan: Optional[dict] = Field(
        default=None,
        description="Inline plan dict (same shape as MachinePlan.to_dict()).",
    )

    @model_validator(mode="after")
    def _exactly_one_plan_source(self) -> StartExecutionRequest:
        if self.plan_id and self.plan:
            raise ValueError("Provide plan_id or plan, not both.")
        if not self.plan_id and not self.plan:
            raise ValueError("Either plan_id or plan is required.")
        return self


class RecordStepRequest(BaseModel):
    """Request body for POST /executions/{task_id}/record — record a step outcome."""

    step_id: str = Field(..., description="Step identifier (e.g. '1.1').")
    agent: str = Field(..., description="Name of the agent that executed the step.")
    status: str = Field(
        ...,
        description="Outcome status: 'complete', 'failed', or 'dispatched'.",
    )
    output_summary: Optional[str] = Field(
        default=None,
        description="Free-text summary of what the step produced.",
    )
    tokens: Optional[int] = Field(
        default=None,
        ge=0,
        description="Estimated token usage for this step.",
    )
    duration_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Wall-clock duration in milliseconds.",
    )


class RecordGateRequest(BaseModel):
    """Request body for POST /executions/{task_id}/gate — record a gate result."""

    phase_id: int = Field(..., description="Phase index the gate belongs to.")
    result: Literal["pass", "fail", "pass_with_notes"] = Field(
        ...,
        description="Gate outcome.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Reviewer notes or command output.",
    )


class ResolveDecisionRequest(BaseModel):
    """Request body for POST /decisions/{request_id}/resolve."""

    option: str = Field(
        ...,
        min_length=1,
        description="The chosen option (must be one of the decision's listed options).",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Human rationale for the choice.",
    )
    resolved_by: Optional[str] = Field(
        default=None,
        description="Who resolved this (defaults to 'human').",
    )


class RegisterWebhookRequest(BaseModel):
    """Request body for POST /webhooks — subscribe to event notifications."""

    url: str = Field(
        ...,
        description="HTTPS endpoint that will receive POST callbacks.",
    )
    events: list[str] = Field(
        ...,
        min_length=1,
        description="Event topics to subscribe to (e.g. ['step.completed', 'gate.required']).",
    )
    secret: Optional[str] = Field(
        default=None,
        description="Shared secret for HMAC signature verification of payloads.",
    )


# ---------------------------------------------------------------------------
# PMO requests
# ---------------------------------------------------------------------------


class RegisterProjectRequest(BaseModel):
    """Request body for POST /pmo/projects — register a project with the PMO."""

    project_id: str = Field(
        ...,
        min_length=1,
        description="Unique project slug (e.g. 'nds').",
    )
    name: str = Field(
        ...,
        min_length=1,
        description="Human-readable project name.",
    )
    path: str = Field(
        ...,
        min_length=1,
        description="Absolute filesystem path to the project root.",
    )
    program: str = Field(
        ...,
        min_length=1,
        description="Program code this project belongs to (e.g. 'NDS', 'ATL').",
    )
    color: str = Field(
        default="",
        description="Display color for the PMO board (e.g. '#4A90E2').",
    )
    description: str = Field(
        default="",
        description="Optional free-text description of the project.",
    )


class CreateForgeRequest(BaseModel):
    """Request body for POST /pmo/forge/plan — create a plan via IntelligentPlanner."""

    description: str = Field(
        ...,
        min_length=1,
        description="Natural-language task description (the PRD).",
    )
    program: str = Field(
        ...,
        min_length=1,
        description="Program code for context (e.g. 'NDS').",
    )
    project_id: str = Field(
        ...,
        min_length=1,
        description="ID of the registered project to scope the plan to.",
    )
    task_type: str | None = Field(
        default=None,
        description="Optional task type hint (e.g. 'new-feature', 'bug-fix', 'refactor').",
    )
    priority: int = Field(
        default=0,
        ge=0,
        le=2,
        description="Plan priority: 0=normal, 1=high, 2=critical.",
    )


class ApproveForgeRequest(BaseModel):
    """Request body for POST /pmo/forge/approve — save an approved plan to a project."""

    plan: dict = Field(
        ...,
        description="Plan dict (same shape as MachinePlan.to_dict()).",
    )
    project_id: str = Field(
        ...,
        min_length=1,
        description="ID of the registered project that will receive the plan.",
    )


class CreateSignalRequest(BaseModel):
    """Request body for POST /pmo/signals — create a signal in the Signals Bar."""

    signal_id: str = Field(
        ...,
        min_length=1,
        description="Unique signal identifier.",
    )
    signal_type: str = Field(
        ...,
        pattern="^(bug|escalation|blocker)$",
        description="Signal category: bug, escalation, or blocker.",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Short, human-readable signal title.",
    )
    description: str = Field(
        default="",
        description="Additional context or reproduction steps.",
    )
    source_project_id: str = Field(
        default="",
        description="Project ID that generated this signal, if known.",
    )
    severity: str = Field(
        default="medium",
        pattern="^(low|medium|high|critical)$",
        description="Signal severity: low, medium, high, or critical.",
    )


# ---------------------------------------------------------------------------
# Forge interview / regeneration requests
# ---------------------------------------------------------------------------


class InterviewRequest(BaseModel):
    """Request body for POST /pmo/forge/interview."""

    plan: dict = Field(
        ...,
        description="Current plan dict (MachinePlan.to_dict() shape).",
    )
    feedback: Optional[str] = Field(
        default=None,
        description="Optional user feedback on what to change.",
    )


class InterviewAnswerPayload(BaseModel):
    """A single answered interview question."""

    question_id: str = Field(..., description="ID of the question being answered.")
    answer: str = Field(..., description="User's answer (selected choice or free text).")


class RegenerateRequest(BaseModel):
    """Request body for POST /pmo/forge/regenerate."""

    project_id: str = Field(..., min_length=1, description="Target project ID.")
    description: str = Field(..., min_length=1, description="Original task description.")
    task_type: Optional[str] = Field(default=None, description="Task type hint.")
    priority: int = Field(default=0, ge=0, le=2, description="Priority: 0-2.")
    original_plan: dict = Field(..., description="Current plan to refine.")
    answers: list[InterviewAnswerPayload] = Field(
        ...,
        description="Answered interview questions.",
    )
