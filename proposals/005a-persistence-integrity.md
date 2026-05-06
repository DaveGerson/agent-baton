# Proposal 005a: Persistence Integrity & Repository Pattern

## 1. Objective
Eliminate split-brain state synchronization bugs (e.g., BUG-001, BUG-002) by establishing a single source of truth for the execution state machine using the Repository Pattern.

## 2. Interface Design
Introduce `StateRepository` in `agent_baton/core/engine/protocols.py`. This decouples the engine's state logic from the underlying storage mechanism.

```python
from typing import Protocol, Optional
from agent_baton.models.execution import ExecutionState, StepResult

class StateRepository(Protocol):
    def save_step_result(self, task_id: str, step_id: str, result: StepResult) -> None: ...
    def get_step_result(self, task_id: str, step_id: str) -> Optional[StepResult]: ...
    def update_execution_state(self, state: ExecutionState) -> None: ...
    def get_execution_state(self, task_id: str) -> Optional[ExecutionState]: ...
```

## 3. Implementation Details: `SQLiteStateRepository`
- **Location:** `agent_baton/core/storage/sqlite_repository.py`
- **Atomic Upserts:** All state-mutating operations must use SQLite upserts to ensure idempotency and prevent `UNIQUE constraint failed` errors that were causing the engine to hang.
  
  *Example SQL for step results:*
  ```sql
  INSERT INTO step_results (task_id, step_id, status, output)
  VALUES (?, ?, ?, ?)
  ON CONFLICT(task_id, step_id) DO UPDATE SET
      status=excluded.status,
      output=excluded.output;
  ```
- **Transaction Boundaries:** Implement context managers for database transactions to ensure that multi-table updates (e.g., updating step status AND updating the `active_phase_index` in `execution_state`) are atomic.

## 4. Deprecation of JSON Fallback
- Remove all references to `json_state_fallback` in the engine write path.
- `execution-state.json` will no longer be treated as an authoritative operational datastore.
- Introduce a new command `baton observe export-state` to generate the JSON on demand for debugging or UI integration, strictly reading from the SQLite source of truth.