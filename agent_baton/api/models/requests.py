"""Pydantic request models for the Agent Baton API.

Each model validates incoming JSON payloads and their field descriptions
are surfaced in the auto-generated OpenAPI schema.  These models form
the inbound contract for the API -- all request bodies are validated
against these schemas before reaching route handler logic.

The models are organized into groups:

- **Core execution**: ``CreatePlanRequest``, ``StartExecutionRequest``,
  ``RecordStepRequest``, ``RecordGateRequest``
- **Decisions**: ``ResolveDecisionRequest``
- **Webhooks**: ``RegisterWebhookRequest``
- **PMO**: ``RegisterProjectRequest``, ``CreateForgeRequest``,
  ``ApproveForgeRequest``, ``CreateSignalRequest``,
  ``BatchResolveRequest``
- **Forge interview**: ``InterviewRequest``, ``InterviewAnswerPayload``,
  ``RegenerateRequest``
- **Learning**: ``ApplyLearningFixRequest``, ``UpdateLearningIssueRequest``
- **Feedback**: ``RecordFeedbackRequest``
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CreatePlanRequest(BaseModel):
    """Request body for ``POST /api/v1/plans`` -- generate an execution plan.

    The only required field is ``description``.  All other fields are
    optional overrides that let the caller influence agent selection,
    task classification, or project scoping.
    """

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
    """Request body for ``POST /api/v1/executions`` -- begin executing a plan.

    Supply *either* ``plan_id`` (referencing a previously created plan) or
    ``plan`` (an inline plan dict).  Providing both or neither is rejected
    by the ``_exactly_one_plan_source`` model validator.
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
        """Ensure exactly one plan source is provided.

        Raises:
            ValueError: If both ``plan_id`` and ``plan`` are set, or if
                neither is set.
        """
        if self.plan_id and self.plan:
            raise ValueError("Provide plan_id or plan, not both.")
        if not self.plan_id and not self.plan:
            raise ValueError("Either plan_id or plan is required.")
        return self


class RecordStepRequest(BaseModel):
    """Request body for ``POST /api/v1/executions/{task_id}/record``.

    Records the outcome of a subagent step.  The ``step_id``, ``agent``,
    and ``status`` fields are required; the remaining fields capture
    optional telemetry (summary, token usage, duration).
    """

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
    """Request body for ``POST /api/v1/executions/{task_id}/gate``.

    Records the outcome of a QA gate check.  The ``result`` field uses
    a literal type to restrict values to ``pass``, ``fail``, or
    ``pass_with_notes``.
    """

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
    """Request body for ``POST /api/v1/decisions/{request_id}/resolve``.

    The ``option`` must be one of the choices listed in the
    ``DecisionRequest.options`` list.  An optional ``rationale``
    captures the human reasoning behind the choice.
    """

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
    """Request body for ``POST /api/v1/webhooks`` -- subscribe to event notifications.

    Event patterns use glob-style matching (e.g. ``step.*`` matches
    ``step.completed`` and ``step.failed``).  At least one event
    pattern is required.
    """

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
    """Request body for ``POST /api/v1/pmo/projects`` -- register a project.

    All string fields require a minimum length of 1 to prevent
    accidental empty registrations.  Re-registration with the same
    ``project_id`` overwrites the existing entry.
    """

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
    """Request body for ``POST /api/v1/pmo/forge/plan``.

    Creates a plan via ``IntelligentPlanner`` for the specified project.
    The ``priority`` field maps to: 0=normal, 1=high, 2=critical.
    """

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
    """Request body for ``POST /api/v1/pmo/forge/approve``.

    Saves an approved (and possibly user-edited) plan to the target
    project's team-context directory.  The ``plan`` dict must conform
    to ``MachinePlan.to_dict()`` shape.
    """

    plan: dict = Field(
        ...,
        description="Plan dict (same shape as MachinePlan.to_dict()).",
    )
    project_id: str = Field(
        ...,
        min_length=1,
        description="ID of the registered project that will receive the plan.",
    )


class ForgeSignalRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/signals/{signal_id}/forge``.

    Dedicated model for signal-to-forge triage — only requires project_id.
    Fixes F-AF-1 (Pydantic 422 when reusing ApproveForgeRequest).
    """

    project_id: str = Field(
        ...,
        min_length=1,
        description="ID of the registered project to forge a plan for.",
    )


class CreateSignalRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/signals``.

    Creates a signal (bug report, escalation, or blocker) in the PMO
    Signals Bar.  The ``signal_type`` and ``severity`` fields are
    validated against fixed pattern sets.
    """

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


class ExecuteCardRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/execute/{card_id}``.

    Launches headless execution for a queued card.  The ``model``
    field overrides the default agent model for all dispatched steps.
    """

    model: str = Field(
        default="sonnet",
        description="Default model for dispatched agents (e.g. 'opus', 'sonnet').",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, print actions without executing them.",
    )
    max_steps: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Safety limit: maximum steps before aborting.",
    )


class BatchResolveRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/signals/batch/resolve``.

    Resolves multiple signals in a single round-trip.  Each signal ID in
    the list is marked as ``"resolved"``; IDs that do not match any known
    signal are silently skipped (reported in the ``not_found`` list of the
    response).
    """

    signal_ids: list[str] = Field(
        ...,
        min_length=1,
        description="IDs of signals to resolve.",
    )


# ---------------------------------------------------------------------------
# Forge interview / regeneration requests
# ---------------------------------------------------------------------------


class InterviewRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/forge/interview``.

    Submits a plan for analysis and receives structured interview
    questions that help refine the plan based on identified ambiguities
    or missing context.
    """

    plan: dict = Field(
        ...,
        description="Current plan dict (MachinePlan.to_dict() shape).",
    )
    feedback: Optional[str] = Field(
        default=None,
        description="Optional user feedback on what to change.",
    )


class InterviewAnswerPayload(BaseModel):
    """A single answered interview question.

    Used as a nested element within ``RegenerateRequest.answers``.
    The ``question_id`` must correspond to a question returned by
    the interview endpoint.
    """

    question_id: str = Field(..., description="ID of the question being answered.")
    answer: str = Field(..., description="User's answer (selected choice or free text).")


class RegenerateRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/forge/regenerate``.

    Regenerates a plan incorporating the user's interview answers.
    The ``original_plan`` is provided as context so the planner can
    understand what was previously generated and refine it based on
    the new information from ``answers``.
    """

    project_id: str = Field(..., min_length=1, description="Target project ID.")
    description: str = Field(..., min_length=1, description="Original task description.")
    task_type: Optional[str] = Field(default=None, description="Task type hint.")
    priority: int = Field(default=0, ge=0, le=2, description="Priority: 0-2.")
    original_plan: dict = Field(..., description="Current plan to refine.")
    answers: list[InterviewAnswerPayload] = Field(
        ...,
        description="Answered interview questions.",
    )


# ---------------------------------------------------------------------------
# Gate approval requests
# ---------------------------------------------------------------------------


class GateApproveRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/gates/{task_id}/approve``.

    Approves a pending gate so execution can advance to the next phase.
    The ``phase_id`` must match the phase currently awaiting approval.
    """

    phase_id: int = Field(..., description="Phase ID that requires approval.")
    notes: Optional[str] = Field(
        default=None,
        description="Optional reviewer notes captured alongside the approval.",
    )


class GateRejectRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/gates/{task_id}/reject``.

    Rejects a pending gate, terminating the execution with a failed status.
    A non-empty ``reason`` is required so the rejection is self-documenting.
    """

    phase_id: int = Field(..., description="Phase ID that requires approval.")
    reason: str = Field(
        ...,
        min_length=1,
        description="Mandatory explanation for the rejection.",
    )


# ---------------------------------------------------------------------------
# Learning requests
# ---------------------------------------------------------------------------


class ApplyLearningFixRequest(BaseModel):
    """Request body for ``POST /api/v1/learn/issues/{issue_id}/apply``.

    Triggers the type-specific resolver for a learning issue and marks it
    as ``"applied"`` in the ledger.  The ``resolution_type`` controls how
    the resolution is attributed.
    """

    resolution_type: str = Field(
        default="human",
        pattern="^(auto|human|interview)$",
        description="How the fix is being applied: auto, human, or interview.",
    )


class UpdateLearningIssueRequest(BaseModel):
    """Request body for ``PATCH /api/v1/learn/issues/{issue_id}``.

    Partially updates a learning issue's lifecycle fields.  All fields are
    optional; only non-None values are written.
    """

    status: str = Field(
        ...,
        description=(
            "New lifecycle status: open, investigating, proposed, "
            "applied, resolved, or wontfix."
        ),
    )
    resolution: Optional[str] = Field(
        default=None,
        description="Description of how the issue was resolved.",
    )
    resolution_type: Optional[str] = Field(
        default=None,
        pattern="^(auto|human|interview)$",
        description="How it was resolved: auto, human, or interview.",
    )
    proposed_fix: Optional[str] = Field(
        default=None,
        description="Proposed remediation description.",
    )


# ---------------------------------------------------------------------------
# Feedback recording request
# ---------------------------------------------------------------------------


class RecordFeedbackRequest(BaseModel):
    """Request body for ``POST /api/v1/executions/{task_id}/feedback``.

    Records the user's answer to a feedback question gate and amends the
    plan with a new dispatch step based on the chosen option.
    """

    phase_id: int = Field(
        ...,
        description="Phase ID that presented the feedback questions.",
    )
    question_id: str = Field(
        ...,
        min_length=1,
        description="ID of the feedback question being answered.",
    )
    chosen_index: int = Field(
        ...,
        ge=0,
        description="Zero-based index into the question's options list.",
    )


# ---------------------------------------------------------------------------
# Changelist / merge / PR requests
# ---------------------------------------------------------------------------


class MergeCardRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/cards/{card_id}/merge``.

    Performs a fast-forward merge of the consolidated commits onto the
    base branch.  The cherry-picks already landed the commits during
    consolidation, so this operation is typically a no-op merge commit
    followed by worktree cleanup.

    The ``force`` flag bypasses the ``status == 'success'`` consolidation
    guard, useful for operator-level overrides when the UI shows a stale
    state.
    """

    force: bool = Field(
        default=False,
        description=(
            "When True, bypass the consolidation-status guard and attempt the "
            "merge even if the consolidation result is not 'success'."
        ),
    )


class CreatePrRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/cards/{card_id}/create-pr``.

    Invokes ``gh pr create`` to open a pull request for the consolidated
    branch.  The description is built from the plan summary and step
    outcomes; the caller may override any field.
    """

    title: str = Field(
        ...,
        min_length=1,
        description="Pull request title.",
    )
    body: str = Field(
        default="",
        description=(
            "PR body markdown.  When empty the engine generates a description "
            "from the plan summary and step outcomes."
        ),
    )
    base_branch: str = Field(
        default="main",
        min_length=1,
        description="Target branch the PR will merge into.",
    )


# ---------------------------------------------------------------------------
# Approval / review requests
# ---------------------------------------------------------------------------


class RequestReviewRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/cards/{card_id}/request-review``.

    Submits a card for peer review before it can be approved.  The optional
    ``reviewer_id`` targets a specific PMO user; omitting it broadcasts to
    all users with the ``reviewer`` or ``approver`` role.
    """

    reviewer_id: Optional[str] = Field(
        default=None,
        description="PMO user_id of the intended reviewer.  Omit to broadcast.",
    )
    notes: str = Field(
        default="",
        description="Context or instructions for the reviewer.",
    )


# ---------------------------------------------------------------------------
# Execution interrupt / step control requests
# ---------------------------------------------------------------------------


class RetryStepRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/execute/{card_id}/retry-step``.

    Resets a failed step back to ``"pending"`` so the execution engine
    will re-dispatch it on its next loop iteration.  Use this when
    execution has stopped due to a failed step and you want to give the
    step another chance without restarting the entire task.
    """

    step_id: str = Field(
        ...,
        min_length=1,
        description="Step ID to reset (e.g. '1.2').  Must currently be in 'failed' status.",
    )


class SkipStepRequest(BaseModel):
    """Request body for ``POST /api/v1/pmo/execute/{card_id}/skip-step``.

    Marks a failed step as ``"skipped"`` so execution can advance past it
    without retrying.  A mandatory ``reason`` is captured in the step
    result so the skip is self-documenting in the audit log.
    """

    step_id: str = Field(
        ...,
        min_length=1,
        description="Step ID to skip (e.g. '1.2').  Must currently be in 'failed' or 'dispatched' status.",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation for why this step is being skipped.",
    )
