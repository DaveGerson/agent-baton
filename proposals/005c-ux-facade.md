# Proposal 005c: UX Facade & Autonomous Driver

## 1. Objective
Reduce CLI friction for individual developers by introducing a high-level `baton run` command that abstracts the manual state machine progression, powered by a unified structural Facade.

## 2. The `BatonRunner` Facade
Create `agent_baton/core/orchestration/runner.py`. This class orchestrates the `ExecutionEngine`, `TaskWorker`, and `EventBus`, completely hiding their interaction complexity from the CLI presentation layer.

```python
class BatonRunner:
    def __init__(self, engine: ExecutionEngine, worker: TaskWorker):
        self.engine = engine
        self.worker = worker
        
    def run_until_complete_or_gate(self, task_id: str):
        # The core autonomous loop
        while True:
            action = self.engine.next_action(task_id)
            if action.type == "wait":
                time.sleep(1) # Or wait for EventBus signal
            elif action.type == "execute":
                self.worker.dispatch(action.step_id)
            elif action.type == "approve":
                return self._handle_approval_gate(action)
            elif action.type == "complete":
                break
```

## 3. CLI Integration: `baton run`
- **Location:** `agent_baton/cli/commands/execution/run.py`
- Instantiates the DI container, sets up the `BatonRunner`, and initiates the autonomous loop.
- Completely replaces the need for users to manually step through `baton execute dispatched` and `baton execute record`.

## 4. Interactive Approvals
When the autonomous loop encounters an `[APPROVAL REQUIRED]` gate, `baton run` will pause execution and present a rich, interactive terminal prompt (via `rich.prompt` or `questionary`).

```
[GATE] The 'Design Architecture' phase requires approval.
Review the phase output at: docs/architecture.md

Proceed with execution? [y/N/amend]: 
```
If 'amend' is chosen, the CLI immediately delegates to a plan amendment sub-workflow, requesting user feedback and re-running the planner before continuing the loop.