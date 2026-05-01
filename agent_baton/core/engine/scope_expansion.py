"""Scope expansion processing for adaptive replanning.

When agents discover work outside their step scope, they emit a
``SCOPE_EXPANSION: <description>`` signal.  The executor queues these
and processes them at phase boundaries via ``_process_pending_expansions``.

This module provides guardrail checking and deterministic expansion
step generation (no LLM calls).
"""
from __future__ import annotations

import re
import uuid
import logging
from dataclasses import field as _field
from typing import TYPE_CHECKING

from agent_baton.models.execution import PlanGate, PlanPhase, PlanStep

if TYPE_CHECKING:
    from agent_baton.models.execution import ExecutionState, MachinePlan

_log = logging.getLogger(__name__)

MAX_EXPANSIONS_PER_EXECUTION: int = 3
MAX_STEP_MULTIPLIER: float = 2.0

_AGENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("test-engineer", ["test", "spec", "coverage", "pytest", "unittest"]),
    ("backend-engineer", ["api", "route", "endpoint", "server", "middleware", "auth"]),
    ("frontend-engineer", ["ui", "component", "page", "css", "react", "template"]),
    ("data-engineer", ["schema", "migration", "data", "database", "sql", "etl"]),
    ("documentation-architect", ["doc", "readme", "documentation", "adr"]),
    ("security-reviewer", ["security", "vulnerability", "cve", "auth", "rbac"]),
    ("devops-engineer", ["deploy", "ci", "docker", "pipeline", "infra"]),
]


def check_expansion_guardrails(
    state: ExecutionState,
    original_step_count: int,
) -> str | None:
    """Return a blocking reason string, or None if expansion is allowed."""
    applied = getattr(state, "scope_expansions_applied", 0)
    if applied >= MAX_EXPANSIONS_PER_EXECUTION:
        return (
            f"Maximum scope expansions reached "
            f"({applied}/{MAX_EXPANSIONS_PER_EXECUTION})"
        )

    current_steps = sum(len(p.steps) for p in state.plan.phases)
    ceiling = int(original_step_count * MAX_STEP_MULTIPLIER)
    if current_steps + 1 > ceiling:
        return (
            f"Expansion would exceed step count ceiling "
            f"({current_steps + 1} > {ceiling})"
        )

    return None


def _select_agent(description: str) -> str:
    """Select an agent for the expansion via keyword matching."""
    lower = description.lower()
    best_agent = "backend-engineer"
    best_score = 0
    for agent, keywords in _AGENT_KEYWORDS:
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score:
            best_score = score
            best_agent = agent
    return best_agent


def generate_expansion_phase(
    description: str,
    plan: MachinePlan,
    trigger_phase_id: int,
) -> PlanPhase:
    """Generate a new PlanPhase from a scope expansion description."""
    max_phase_id = max(
        (p.phase_id for p in plan.phases),
        default=0,
    )
    new_phase_id = max_phase_id + 1

    agent = _select_agent(description)
    step_id = f"{new_phase_id}.1"

    name_short = description[:60].strip()
    if len(description) > 60:
        name_short += "..."

    step = PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description=description,
        context_files=[],
        deliverables=[f"Complete: {name_short}"],
    )

    gate = PlanGate(
        gate_type="build",
        command='python -c "import agent_baton; print(\'ok\')"',
        description="Import smoke check for expansion phase.",
        fail_on=["import error"],
    )

    return PlanPhase(
        phase_id=new_phase_id,
        name=f"Expansion: {name_short}",
        steps=[step],
        gate=gate,
    )
