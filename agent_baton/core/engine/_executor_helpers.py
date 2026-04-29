"""Pure read-only helpers shared by ExecutionEngine and ActionResolver.

This module owns nothing — it is a stateless utility surface. Functions
here MUST:
  - take only data inputs (ExecutionState, PlanStep, primitives)
  - return only data outputs (bool, float, int, PlanStep | None)
  - never mutate inputs
  - never perform I/O beyond `logger.debug` / `logger.warning`

Importing rules:
  - This module imports from `agent_baton.models.*` and stdlib only.
  - This module MUST NOT import from `agent_baton.core.engine.executor`
    (would be a circular dependency through resolver.py).
  - The resolver and the engine both import from here.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from agent_baton.models.execution import ExecutionState, PlanStep

logger = logging.getLogger(__name__)


def elapsed_seconds(started_at: str) -> float:
    """Return elapsed wall-clock seconds since *started_at* (ISO 8601 string).

    Consolidated from the duplicate implementations that previously lived in
    ``executor.py`` and ``resolver.py`` (bd-8083 sub-item 4).  Both copies
    have been removed; this is now the single source of truth.

    Resolution:
    - Returns ``0.0`` when *started_at* is empty or ``None``.
    - Returns ``0.0`` when *started_at* cannot be parsed (ValueError/TypeError).
    - Normalises a naive datetime to UTC before computing the delta.
    - Always returns a non-negative value (``max(0.0, delta)``).

    Args:
        started_at: ISO 8601 timestamp string (e.g. ``"2026-04-28T10:00:00+00:00"``).

    Returns:
        Elapsed seconds as a float; ``0.0`` on any error or empty input.
    """
    if not started_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        now = datetime.now(tz=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0.0, (now - start).total_seconds())
    except (ValueError, TypeError):
        return 0.0


def find_step(state: ExecutionState, step_id: str) -> PlanStep | None:
    """Locate a PlanStep by step_id in the plan.

    Searches all phases in *state.plan.phases* for a step whose
    ``step_id`` matches *step_id*.

    Args:
        state: Current execution state (not mutated).
        step_id: The step identifier to look up.

    Returns:
        The matching :class:`~agent_baton.models.execution.PlanStep`, or
        ``None`` when not found.
    """
    for phase in state.plan.phases:
        for step in phase.steps:
            if step.step_id == step_id:
                return step
    return None


def effective_timeout(plan_step: PlanStep) -> int:
    """Return the effective timeout in seconds for *plan_step*.

    Resolution order:
    1. ``plan_step.timeout_seconds`` when non-zero (explicit per-step
       override).
    2. ``BATON_DEFAULT_STEP_TIMEOUT_S`` env var when set to a positive int.
    3. 0 (no timeout) — default, fully backward-compatible.

    Args:
        plan_step: The plan step to evaluate.

    Returns:
        Effective timeout in seconds; ``0`` means no timeout enforced.
    """
    if plan_step.timeout_seconds > 0:
        return plan_step.timeout_seconds
    env_val = os.environ.get("BATON_DEFAULT_STEP_TIMEOUT_S", "")
    if env_val:
        try:
            parsed = int(env_val)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return 0


def gate_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
    """Return True if a passing gate result exists for *phase_id*.

    Args:
        state: Current execution state (not mutated).
        phase_id: The phase whose gate results to inspect.

    Returns:
        ``True`` when at least one gate result for this phase has
        ``passed=True``; ``False`` otherwise.
    """
    for g in state.gate_results:
        if g.phase_id == phase_id and g.passed:
            return True
    return False


def approval_passed_for_phase(state: ExecutionState, phase_id: int) -> bool:
    """Return True if an approval result (approve or approve-with-feedback) exists.

    Args:
        state: Current execution state (not mutated).
        phase_id: The phase whose approval results to inspect.

    Returns:
        ``True`` when at least one approval result for this phase carries
        a result of ``"approve"`` or ``"approve-with-feedback"``; ``False``
        otherwise.
    """
    for a in state.approval_results:
        if a.phase_id == phase_id and a.result in ("approve", "approve-with-feedback"):
            return True
    return False


def feedback_resolved_for_phase(state: ExecutionState, phase_id: int) -> bool:
    """Return True if all feedback questions for *phase_id* have been answered.

    Looks up the phase in the plan by *phase_id* (not by the engine's
    ``current_phase`` index) so that the result is correct even when the
    engine has already advanced ``state.current_phase`` past the phase being
    checked.

    Fix for bd-f4e3: the previous implementation read ``state.current_phase_obj``
    (index-based) instead of looking up the phase by its ``phase_id`` field.
    When ``current_phase`` advanced past the queried phase, the wrong phase's
    feedback questions were consulted, causing the function to return ``True``
    prematurely (the new current phase typically has no questions).

    Args:
        state: Current execution state (not mutated).
        phase_id: The phase whose feedback questions to inspect.

    Returns:
        ``True`` when every question in the phase's feedback gate has a
        recorded answer, or when the phase has no feedback questions.
        ``True`` is also returned when *phase_id* is not found in the plan
        (nothing to block on).
    """
    phase_obj = next(
        (p for p in state.plan.phases if p.phase_id == phase_id), None
    )
    if phase_obj is None:
        return True
    question_ids = {q.question_id for q in phase_obj.feedback_questions}
    answered_ids = {
        r.question_id for r in state.feedback_results
        if r.phase_id == phase_id
    }
    return question_ids <= answered_ids


def is_phase_complete(state: ExecutionState, phase_id: int) -> bool:
    """Return True when all steps in *phase_id* have a terminal result.

    A step is terminal when its step_id appears in one of the three
    terminal sets: completed_step_ids, failed_step_ids, or
    interrupted_step_ids. In-flight statuses (dispatched, interacting,
    interact_dispatched) do NOT satisfy completion.

    An empty phase (phase.steps == []) trivially returns True because
    all() over an empty sequence is vacuously true.

    Returns False when the phase_id is not found in the plan.
    """
    phase = next((p for p in state.plan.phases if p.phase_id == phase_id), None)
    if phase is None:
        return False
    terminal = (
        state.completed_step_ids
        | state.failed_step_ids
        | state.interrupted_step_ids
    )
    return all(s.step_id in terminal for s in phase.steps)
