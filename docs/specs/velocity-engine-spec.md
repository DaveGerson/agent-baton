# Executable Specification: Velocity & Quality (Engine-First)

**Version:** 1.0 — April 2026
**Implementation Status:** READY
**Target Branch:** `feature/engine-velocity`

## 1. Automated Git Worktree Isolation (Wave 1)

### 1.1 `WorktreeManager` Implementation
*   **File:** `agent_baton/core/runtime/worktree.py` (NEW)
*   **Logic:**
    *   `create_worktree(task_id, step_id)`: Executing `git worktree add .claude/worktrees/{task_id}/{step_id} -b baton-{task_id}-{step_id}`.
    *   `cleanup_worktree(task_id, step_id)`: Executing `git worktree remove --force`.
*   **Integration Point:** `agent_baton/core/runtime/supervisor.py`. Modify `WorkerSupervisor.start()` and `TaskWorker.run()` to wrap agent dispatches in worktree contexts when `max_parallel > 1`.

### 1.2 `ExecutionAction` Extension
*   **File:** `agent_baton/models/execution.py`.
*   **Change:** Ensure `ExecutionAction` carries an `isolation_path` field so the orchestrator knows where to run the subagent.

---

## 2. Knowledge Graph & Context Profiles (Wave 2)

### 2.1 Schema Update (Migration v6)
*   **File:** `agent_baton/core/storage/schema.py`.
*   **Add Table:**
    ```sql
    CREATE TABLE IF NOT EXISTS agent_context (
        agent_name TEXT,
        project_root TEXT,
        expertise_summary TEXT, -- JSON blob of files/patterns mastered
        successful_strategies TEXT, -- JSON blob
        last_updated TEXT,
        PRIMARY KEY (agent_name, project_root)
    );
    ```

### 2.2 `BeadSynthesizer` Implementation
*   **File:** `agent_baton/core/engine/bead_analyzer.py`.
*   **Process:**
    1.  Fetch all `open` beads for the current `task_id`.
    2.  Call `HaikuClassifier` to identify semantic relationships.
    3.  Call `BeadStore.link()` to write the inferred edges.
*   **Integration Point:** `ExecutionEngine.complete_phase()`.

### 2.3 `ContextHarvester` Implementation
*   **File:** `agent_baton/core/learn/harvester.py` (NEW).
*   **Process:**
    1.  Scan `StepResult` for `files_changed` and `outcome`.
    2.  Summarize into an "Institutional Memory" fragment.
    3.  Upsert into `agent_context` table.
*   **Integration Point:** `ExecutionEngine.complete_task()`.

---

## 3. Precision Verification (Wave 3)

### 3.1 `PlanStep` Data Model Update
*   **File:** `agent_baton/models/execution.py`.
*   **Change:** Add `expected_outcome: str = ""` to `PlanStep` dataclass.
*   **Integration Point:** Update `IntelligentPlanner._create_step()` in `planner.py` to generate these using a dedicated prompt fragment.

### 3.2 Automated Handoff Synthesis
*   **File:** `agent_baton/core/engine/dispatcher.py`.
*   **Change:** Modify `_build_delegation_prompt()` to check if the `depends_on` steps have `StepResult.outcome`. If so, prepend a "PREVIOUS WORK" section to the new agent's prompt.

---

## 4. Execution Guardrails
*   **Safety:** Worktrees must be created under `.claude/worktrees/` which MUST be added to `.gitignore` automatically by the `WorktreeManager`.
*   **State Integrity:** All `agent_context` and `bead` links must be written inside transactions to prevent database corruption during parallel runs.
