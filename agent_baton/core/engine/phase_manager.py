"""PhaseManager — encapsulating phase boundaries and state transitions.

Extracts all logic related to phase progression:
- Checking if all steps in a phase are complete.
- Evaluating phase-level [APPROVAL REQUIRED] gates.
- Advancing the active_phase_index.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from agent_baton.models.execution import ExecutionState, PlanPhase

logger = logging.getLogger(__name__)

class PhaseManagerProtocol(Protocol):
    """Protocol for checking phase completions and evaluating progression."""
    
    def evaluate_progression(self, state: ExecutionState) -> None:
        """Evaluate if the current phase is complete and advance if necessary."""
        ...

class PhaseManager(PhaseManagerProtocol):
    """Manages phase transitions within an execution state."""

    def evaluate_progression(self, state: ExecutionState) -> None:
        """Evaluate if the current phase is complete.
        
        If all steps in the current phase are completed successfully,
        it evaluates gates or approvals, and transitions to the next phase.
        This mutates the execution state.
        """
        # Skeleton code placeholder. The actual implementation will be migrated
        # from ExecutionEngine to handle advancing phase indices and setting
        # up approvals/gates.
        pass

    def is_phase_complete(self, phase: PlanPhase, state: ExecutionState) -> bool:
        """Check if all steps in the given phase are complete."""
        # Skeleton check
        for step in phase.steps:
            if step.step_id not in state.completed_steps:
                return False
        return True
