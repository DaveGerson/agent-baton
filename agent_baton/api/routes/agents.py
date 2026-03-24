"""Agent registry endpoints for the Agent Baton API.

GET /agents        — list agents, with optional category/stack filtering.
GET /agents/{name} — retrieve a single agent definition by name.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_baton.api.deps import get_registry
from agent_baton.api.models.responses import AgentListResponse, AgentResponse
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.enums import AgentCategory

router = APIRouter()


@router.get("/agents", response_model=AgentListResponse)
async def list_agents(
    category: Optional[str] = Query(default=None, description="Filter by agent category."),
    stack: Optional[str] = Query(default=None, description="Filter by stack flavor (e.g. 'python')."),
    registry: AgentRegistry = Depends(get_registry),
) -> AgentListResponse:
    """List all available agents from the registry.

    Supports optional filtering:
    - ``category``: matches the AgentCategory enum value (case-insensitive).
    - ``stack``: filters to agents whose flavor matches the given stack string.

    DECISION: The ``stack`` filter is a simple substring match on
    ``agent.flavor`` — it is intentionally lenient to avoid 404s when
    the caller provides a partial stack name (e.g. "py" matching "python").
    Strict equality would be more correct but less ergonomic for exploratory
    API usage.
    """
    if category is not None:
        # Try to resolve the string to an AgentCategory enum value.
        try:
            cat_enum = AgentCategory(category)
        except ValueError:
            # Also try case-insensitive match.
            cat_enum = _resolve_category(category)
            if cat_enum is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown category '{category}'. "
                           f"Valid values: {[c.value for c in AgentCategory]}",
                )
        agents = registry.by_category(cat_enum)
    else:
        agents = list(registry.agents.values())

    # Apply optional stack / flavor filter.
    if stack is not None:
        stack_lower = stack.lower()
        agents = [
            a for a in agents
            if a.flavor is not None and stack_lower in a.flavor.lower()
        ]

    return AgentListResponse.from_dataclass_list(agents)


@router.get("/agents/{name}", response_model=AgentResponse)
async def get_agent(
    name: str,
    registry: AgentRegistry = Depends(get_registry),
) -> AgentResponse:
    """Return a single agent definition by name."""
    agent = registry.get(name)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{name}' not found in registry.",
        )
    return AgentResponse.from_dataclass(agent)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_category(value: str) -> AgentCategory | None:
    """Case-insensitive lookup of AgentCategory by value string."""
    value_lower = value.lower()
    for cat in AgentCategory:
        if cat.value.lower() == value_lower:
            return cat
    return None
