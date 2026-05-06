# Proposal 005: Core Stabilization, UX Streamlining, and Architectural Refactoring

**Status:** Proposed
**Date:** 2026-04-28
**Author:** Agent Baton Architecture Team
**Target:** Epic 3 (Stabilization & Usability)

## 1. Executive Summary

Despite a robust set of features and rigorous data models, the `agent-baton` codebase suffers from architectural bottlenecks that hinder maintainability, cause critical runtime errors, and degrade developer experience. This specification outlines a comprehensive architectural overhaul targeting four major weaknesses: state synchronization brittleness, monolithic "God Objects" in the core engine, heuristic misfires in agent routing, and a high-friction manual CLI workflow. 

The goal is to introduce resilient design patterns that reduce system brittleness, ensure strict state integrity, and provide a developer-friendly execution interface.

---

## 2. Weakness 1: State Synchronization Brittleness & "Split-Brain" Bugs

### Current State
The system utilizes a dual-write persistence model (SQLite via `sqlite_backend.py` and a JSON file fallback). Bugs like `BUG-001` and `BUG-002` demonstrate that these stores frequently fall out of sync. `INSERT OR IGNORE` semantics in SQLite lead to dropped state updates, while the engine's read path unpredictably favors one store over the other, causing execution deadlocks (`ACTION: wait`).

### Target Architecture: Single Source of Truth with Repository Pattern
To eliminate split-brain issues, the engine must rely on a single, authoritative persistence interface governed by the **Repository Pattern**.

#### Design Specifications
1.  **Deprecate Dual-Write:** Remove the JSON fallback completely from the core execution read/write path. JSON should only be exported for observability/debugging, not as an operational state store.
2.  **Strict SQLite Operations:** Update the SQLite schema and queries for the `step_results` and `execution_state` tables to utilize `INSERT OR REPLACE` or `ON CONFLICT DO UPDATE`. This ensures idempotency.
3.  **Transactional Integrity:** Wrap state transitions (e.g., dispatching a step, recording a result, updating `active_phase_index`) in explicit SQLite transactions to guarantee atomicity.
4.  **Interface Standard:** Implement a `StateRepository` interface. `SQLiteStateRepository` will be the sole production implementation, injected into the `ExecutionEngine`.

```python
class StateRepository(Protocol):
    def save_step_result(self, task_id: str, step_id: str, result: StepResult) -> None: ...
    def get_step_result(self, task_id: str, step_id: str) -> Optional[StepResult]: ...
    def update_execution_state(self, state: ExecutionState) -> None: ...
```

---

## 3. Weakness 2: "God Object" Anti-Patterns in Core

### Current State
`ExecutionEngine` (`executor.py`, ~2,600 lines) and `IntelligentPlanner` (`planner.py`, ~1,700 lines) have absorbed too many responsibilities. The engine mixes state transition logic, gate evaluation, and side-effect dispatching. The planner mixes parsing, capability checking, and plan mutation.

### Target Architecture: State Pattern & Strategy Pattern
Decompose these monolithic classes using established behavioral patterns to isolate responsibilities.

#### Design Specifications for `ExecutionEngine`
1.  **Extract `ActionResolver`:** Move the complex logic for determining `next_action()` into a dedicated `ActionResolver` class. The engine simply queries the resolver based on the current state.
2.  **Extract `PhaseManager`:** Isolate the logic for advancing `active_phase_index`, validating phase prerequisites, and handling phase-level gates.
3.  **State Pattern:** Represent the execution states (e.g., `PlanningState`, `ExecutingPhaseState`, `AwaitingApprovalState`) as discrete classes that handle their own specific transitions, rather than massive `if/elif` blocks in a single `_determine_action` method.

#### Design Specifications for `IntelligentPlanner`
1.  **Strategy Pattern for Plan Generation:** Isolate different planning approaches (e.g., `FromScratchStrategy`, `TemplateBasedStrategy`, `RefinementStrategy`).
2.  **Extract Analyzers:** Move the 14-step pipeline into distinct, composable "Analyzer" components (e.g., `DependencyAnalyzer`, `RiskAnalyzer`). The planner simply acts as a pipeline orchestrator.

---

## 4. Weakness 3: High-Friction Manual UX

### Current State
End-users must manually drive the state machine using a series of low-level CLI commands (`baton execute dispatched`, `baton execute record --status complete`, `baton execute next`). This is tedious and prone to user error, discouraging individual developer adoption.

### Target Architecture: Facade Pattern & Autonomous Driver
Abstract the complex execution loop behind a simple, unified interface for standard workflows, utilizing the existing `TaskWorker` as an autonomous driver.

#### Design Specifications
1.  **The `baton run` Command:** Introduce a new top-level CLI command (`cli/commands/execution/run.py`).
2.  **Autonomous Event Loop:** `baton run` will initialize the `ExecutionEngine`, `TaskWorker`, and `EventBus`. It will automatically query `next_action()`, execute the required step via `TaskWorker`, record the result, and loop until the plan is complete or an explicit human intervention gate is reached.
3.  **Facade Implementation:** Create a `BatonFacade` or `BatonRunner` class that hides the orchestration of the internal components (Engine, Worker, Bus, Router) from the CLI layer.
4.  **Interactive Prompts:** When the loop halts for `[APPROVAL REQUIRED]`, utilize a rich interactive prompt (e.g., `inquirer` or `rich` prompts) directly in the terminal, allowing the user to approve/reject without opening a separate CLI command.

---

## 5. Weakness 4: Heuristic Misfires in Agent Routing

### Current State
`AgentRouter.detect_stack()` uses a naive heuristic. If a Python backend project contains a `pmo-ui/package.json`, it might arbitrarily classify the whole project as Node.js and assign a `backend-engineer--node` agent, which lacks the necessary context.

### Target Architecture: Weighted Scoring & Component-Level Resolution
Move from naive file detection to a weighted scoring system, and allow fine-grained agent assignments within a single project.

#### Design Specifications
1.  **Weighted Manifest Detection:** Implement a `StackDetector` that assigns weights to indicators. 
    *   Root-level `pyproject.toml` = +10 Python. 
    *   Root-level `package.json` = +10 Node. 
    *   Nested `package.json` (depth > 1) = +2 Node.
2.  **Phase-Level Stack Awareness:** Modify the `IntelligentPlanner` to generate plans with stack requirements *per phase* or *per step*, rather than a monolithic project-level stack.
3.  **Multi-Agent Routing:** If a project is a polyglot monorepo, the router should support assigning the `backend-engineer--python` to the API phase and `frontend-engineer--react` to the UI phase within the same execution plan.

---

## 6. Implementation Strategy & Rollout

1.  **Phase 1: Persistence Integrity (Weeks 1-2)**
    *   Implement `StateRepository`.
    *   Migrate `sqlite_backend.py` to robust upserts.
    *   Disable and remove JSON state persistence.
2.  **Phase 2: UX Facade (Weeks 3-4)**
    *   Implement `BatonRunner`.
    *   Ship `baton run` as an experimental CLI command alongside existing granular commands.
3.  **Phase 3: Router Enhancements (Weeks 4-5)**
    *   Implement weighted stack scoring.
    *   Update planner to support multi-agent assignment.
4.  **Phase 4: Core Engine Decomposition (Weeks 6-8)**
    *   Incrementally extract `ActionResolver` and `PhaseManager` from `ExecutionEngine`.
    *   Ensure all 3,900+ tests remain green throughout the decomposition.