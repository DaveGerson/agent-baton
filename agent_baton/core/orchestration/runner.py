from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_baton.cli.colors import error as color_error, warning as color_warning
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.decision import DecisionRequest, DecisionResolution


class InteractiveDecisionManager(DecisionManager):
    """An interactive decision manager that prompts the user in the terminal
    instead of writing JSON files and waiting for an out-of-band resolution.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolutions: dict[str, DecisionResolution] = {}
        self._resolution_data: dict[str, dict[str, Any]] = {}
        
    def request(self, req: DecisionRequest) -> Path:
        """Present a prompt to the user and immediately record their decision."""
        print(color_warning(f"\n[GATE] {req.summary}"))
        if req.context_files:
            print("Review the phase output in the relevant files.")
            
        # We pause and wait for the user
        options = req.options or ["approve", "reject"]
        prompt_text = f"Proceed with execution? [{'/'.join(options)}]: "
        
        while True:
            try:
                choice = input(prompt_text).strip().lower()
            except EOFError:
                choice = "reject"
                feedback = "Aborted via EOF"
                break
            
            # Map common short answers
            if choice in ("y", "yes", "approve"):
                chosen = "approve"
                feedback = "Approved via interactive prompt."
                break
            elif choice in ("n", "no", "reject"):
                chosen = "reject"
                feedback = input("Reason for rejection: ").strip()
                break
            elif choice in ("amend", "approve-with-feedback", "feedback"):
                chosen = "approve-with-feedback"
                feedback = input("Enter feedback/amendments: ").strip()
                break
            elif choice in options:
                chosen = choice
                feedback = input("Additional notes (optional): ").strip()
                break
            else:
                print(color_error(f"Invalid choice. Must be one of: {', '.join(options)}"))
                
        # Store the resolution synchronously
        self._resolutions[req.request_id] = DecisionResolution(
            request_id=req.request_id,
            status="resolved",
            chosen_option=chosen,
            feedback=feedback,
        )
        self._resolution_data[req.request_id] = {
            "chosen_option": chosen,
            "feedback": feedback
        }
        
        # Return a dummy path since we didn't write to disk
        return self._dir / f"{req.request_id}.json"

    def get(self, request_id: str) -> DecisionResolution | None:
        """Return the synchronously collected resolution."""
        return self._resolutions.get(request_id)
        
    def get_resolution(self, request_id: str) -> dict[str, Any] | None:
        """Return the resolution data payload."""
        return self._resolution_data.get(request_id)


class BatonRunner:
    """Facade for running the orchestrator autonomously.
    
    Hides the complex initialization of the engine, worker, and event bus,
    and provides a simple run_until_complete interface for the CLI.
    """
    
    def __init__(self, engine: ExecutionEngine, worker: TaskWorker):
        self.engine = engine
        self.worker = worker
        
        # Inject the interactive decision manager into the worker
        # if it isn't already set up for interactivity.
        if getattr(self.worker, "_decision_manager", None) is None:
            self.worker._decision_manager = InteractiveDecisionManager()
        elif not isinstance(self.worker._decision_manager, InteractiveDecisionManager):
            # Wrap or replace the existing one with interactive
            # while preserving the decisions dir.
            old_dm = self.worker._decision_manager
            self.worker._decision_manager = InteractiveDecisionManager(
                decisions_dir=old_dm.decisions_dir,
                bus=old_dm._bus,
                safe_read_root=old_dm.safe_read_root
            )
            
    async def run_until_complete_or_gate(self, task_id: str | None = None) -> str:
        """Run the core autonomous loop using the underlying TaskWorker.
        
        The InteractiveDecisionManager handles pausing for gates, so we can 
        just delegate to the worker's execution loop.
        """
        # The worker handles next_action() loop, task parallelization, and events.
        # It pauses implicitly because InteractiveDecisionManager blocks inside request().
        summary = await self.worker.run()
        return summary
