"""ActionResolver — stateless decision logic for execution phase progression.

Extracts the complex decision-making logic out of ExecutionEngine.
Evaluates the current state, active phase, and completed steps to compute
the next ActionType (WAIT, DISPATCH, APPROVE, GATE, COMPLETE).
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from agent_baton.models.execution import ExecutionAction, ExecutionState, ActionType

logger = logging.getLogger(__name__)

class ActionResolverProtocol(Protocol):
    """Protocol for determining the next action from the current state."""
    
    def determine_next(self, state: ExecutionState) -> ExecutionAction:
        """Core state machine logic — inspect state and return the next action.
        
        This method must NOT mutate state itself.
        """
        ...

class ActionResolver(ActionResolverProtocol):
    """Stateless evaluator computing phase transitions and actions."""

    def determine_next(self, state: ExecutionState) -> ExecutionAction:
        """Determine the next step based on the execution state."""
        # For now, this is a skeleton class that will later be populated by
        # migrating the massive `_determine_action` logic from ExecutionEngine.
        
        # Skeleton check: if state is complete or failed, return COMPLETE.
        if state.status in ("complete", "failed"):
            return ExecutionAction(
                action_type=ActionType.COMPLETE,
                message=f"Execution already {state.status}."
            )

        # This will be replaced with the actual logic from _determine_action
        raise NotImplementedError(
            "ActionResolver is a skeleton; the logic from `ExecutionEngine._determine_action` "
            "must be injected or fully migrated here."
        )
