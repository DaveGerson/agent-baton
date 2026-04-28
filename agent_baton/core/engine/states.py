"""Execution states mapping to the State Pattern for phase transitions.

Replaces the massive `if/elif` string checks in `ExecutionEngine` with discrete
state classes that encapsulate the mutation logic for updating step results and
handling gate failures.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from agent_baton.models.execution import ExecutionState, StepResult

logger = logging.getLogger(__name__)

class ExecutionPhaseStateProtocol(Protocol):
    """State pattern protocol for handling phase transitions and step results."""
    
    def handle_step_result(
        self, state: ExecutionState, step_id: str, result: StepResult
    ) -> None:
        """Process a completed step and mutate the state accordingly."""
        ...
        
    def evaluate_gates(self, state: ExecutionState) -> None:
        """Evaluate any gates for the current phase or execution state."""
        ...

class PlanningState(ExecutionPhaseStateProtocol):
    """Initial state when plan generation and setup are occurring."""
    
    def handle_step_result(
        self, state: ExecutionState, step_id: str, result: StepResult
    ) -> None:
        pass
        
    def evaluate_gates(self, state: ExecutionState) -> None:
        pass

class ExecutingPhaseState(ExecutionPhaseStateProtocol):
    """State when the system is actively dispatching agents for the current phase."""

    def handle_step_result(
        self, state: ExecutionState, step_id: str, result: StepResult
    ) -> None:
        state.completed_steps.append(step_id)
        # Store results...
        pass
        
    def evaluate_gates(self, state: ExecutionState) -> None:
        pass

class AwaitingApprovalState(ExecutionPhaseStateProtocol):
    """State when the system is blocked waiting for user approval on a phase."""

    def handle_step_result(
        self, state: ExecutionState, step_id: str, result: StepResult
    ) -> None:
        raise ValueError("Cannot handle step result while waiting for approval.")
        
    def evaluate_gates(self, state: ExecutionState) -> None:
        pass

class TerminalState(ExecutionPhaseStateProtocol):
    """Final state (Complete or Failed)."""

    def handle_step_result(
        self, state: ExecutionState, step_id: str, result: StepResult
    ) -> None:
        raise ValueError("Cannot handle step result when execution is terminal.")
        
    def evaluate_gates(self, state: ExecutionState) -> None:
        pass
