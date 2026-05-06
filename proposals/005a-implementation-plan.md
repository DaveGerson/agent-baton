# Proposal 005a: Persistence Integrity - Implementation Plan

**Status:** Proposed / Design Phase
**Epic:** Epic 3 (Stabilization & Usability)
**Target:** `agent_baton/core/storage/` and `agent_baton/core/engine/executor.py`

## 1. Executive Summary

This document outlines the phased implementation strategy for **Proposal 005a**. The goal is to eliminate split-brain data synchronization bugs (e.g., BUG-001, BUG-002) by migrating the `ExecutionEngine` to a strict Repository Pattern. The system will use SQLite as the absolute single source of truth for all execution states and step results, utilizing atomic `ON CONFLICT DO UPDATE` upserts to prevent data corruption or locking during parallel agent operations.

---

## 2. Phase 1: Define Interfaces (Week 1)

**Goal:** Establish the boundary between the engine and its storage logic.

1.  **Create Protocol:** Create `agent_baton/core/engine/protocols.py` (or define it in the new `resolver.py` or existing models context).
2.  **Define `StateRepository`:**
    ```python
    from typing import Protocol, Optional
    from agent_baton.models.execution import ExecutionState, StepResult

    class StateRepository(Protocol):
        def save_step_result(self, task_id: str, step_id: str, result: StepResult) -> None: ...
        def get_step_result(self, task_id: str, step_id: str) -> Optional[StepResult]: ...
        def update_execution_state(self, state: ExecutionState) -> None: ...
        def get_execution_state(self, task_id: str) -> Optional[ExecutionState]: ...
        def transaction(self): ... # Context manager for atomic operations
    ```

---

## 3. Phase 2: SQLite Repository Implementation (Weeks 2-3)

**Goal:** Implement the resilient SQLite backing store.

1.  **Create Repository:** Create `agent_baton/core/storage/sqlite_repository.py`.
2.  **Atomic Upserts:** Implement all mutating operations using `ON CONFLICT DO UPDATE` (upserts) to ensure idempotency.
    *   *Example:*
        ```sql
        INSERT INTO step_results (task_id, step_id, status, output)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(task_id, step_id) DO UPDATE SET
            status=excluded.status,
            output=excluded.output;
        ```
3.  **Transaction Management:** Implement the `transaction()` context manager utilizing Python's native `sqlite3` isolation levels to group updates (e.g., updating a step result AND advancing the phase index) into a single atomic commit.

---

## 4. Phase 3: Engine Integration (Week 4)

**Goal:** Migrate `ExecutionEngine` to use the repository.

1.  **Inject Dependency:** Pass the `StateRepository` into the `ExecutionEngine` constructor.
2.  **Replace Direct DB Calls:** Replace all raw `sqlite3` cursor executions and JSON file reads/writes inside `executor.py` with calls to `self.repository`.
3.  **Validation:** Run the complete test suite. The abstraction shouldn't change the engine's behavior, but it will isolate it from storage failures.

---

## 5. Phase 4: Deprecation of JSON Fallback (Week 5)

**Goal:** Remove the dual-write architecture causing split-brain bugs.

1.  **Remove Fallback Logic:** Strip out all `json_state_fallback` logic from the engine's write path. `execution-state.json` is no longer the source of truth.
2.  **Add Export Tooling:** To preserve observability for debugging and UI tools, introduce a new CLI command: `baton observe export-state <task_id>`. This will read purely from the SQLite source of truth and emit a static JSON representation.
