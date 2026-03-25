"""Pydantic API models -- request validation and response serialization.

These models form the contract between the FastAPI HTTP layer and external
clients.  They are intentionally decoupled from the internal dataclass-based
models in ``agent_baton.models`` to allow the API schema to evolve
independently from internal data structures.

Request models validate incoming JSON payloads and are defined in
:mod:`~agent_baton.api.models.requests`.  Response models serialize
outgoing data and provide ``from_dataclass`` classmethods for
conversion from internal dataclasses; they are defined in
:mod:`~agent_baton.api.models.responses`.
"""

from agent_baton.api.models.requests import (
    CreatePlanRequest,
    StartExecutionRequest,
    RecordStepRequest,
    RecordGateRequest,
    ResolveDecisionRequest,
    RegisterWebhookRequest,
)
from agent_baton.api.models.responses import (
    PlanStepResponse,
    PlanGateResponse,
    PlanPhaseResponse,
    PlanResponse,
    ExecutionResponse,
    StepResultResponse,
    ActionResponse,
    DecisionResponse,
    DecisionListResponse,
    ResolveResponse,
    EventResponse,
    AgentResponse,
    AgentListResponse,
    DashboardResponse,
    TraceEventResponse,
    TraceResponse,
    AgentUsageResponse,
    TaskUsageResponse,
    UsageResponse,
    HealthResponse,
    ReadyResponse,
    WebhookResponse,
    ErrorResponse,
)

__all__ = [
    # Requests
    "CreatePlanRequest",
    "StartExecutionRequest",
    "RecordStepRequest",
    "RecordGateRequest",
    "ResolveDecisionRequest",
    "RegisterWebhookRequest",
    # Responses
    "PlanStepResponse",
    "PlanGateResponse",
    "PlanPhaseResponse",
    "PlanResponse",
    "ExecutionResponse",
    "StepResultResponse",
    "ActionResponse",
    "DecisionResponse",
    "DecisionListResponse",
    "ResolveResponse",
    "EventResponse",
    "AgentResponse",
    "AgentListResponse",
    "DashboardResponse",
    "TraceEventResponse",
    "TraceResponse",
    "AgentUsageResponse",
    "TaskUsageResponse",
    "UsageResponse",
    "HealthResponse",
    "ReadyResponse",
    "WebhookResponse",
    "ErrorResponse",
]
