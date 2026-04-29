# Proposal 005b: Core Engine & Planner Decomposition - Implementation Plan

**Status:** Proposed / Design Phase
**Epic:** Epic 3 (Stabilization & Usability)
**Target:** `agent_baton/core/engine/executor.py` and `agent_baton/core/engine/planner.py`

## 1. Executive Summary

This document outlines the phased implementation strategy for **Proposal 005b**. The goal is to safely decompose the two largest "God Objects" in the `agent-baton` codebase—`ExecutionEngine` (2,600+ lines) and `IntelligentPlanner` (1,700+ lines)—into a set of highly cohesive, loosely coupled components using established behavioral design patterns (State Pattern, Strategy Pattern, and Analyzer Pipeline).

Due to the critical nature of these files, the refactor will be executed in **four distinct phases**, allowing the test suite (3,900+ tests) to validate each architectural boundary before proceeding to the next.

---

## 2. Phase 1: Planning Strategy and Analyzers (Weeks 1-2)

**Goal:** Decouple LLM plan generation from business logic validation in the `IntelligentPlanner`. This enables the **Deep Decomposition Strategy** required to prevent subscale plans from falling back to inline dispatching.

### 2.1 Extract the Analyzer Pipeline
The current 14-step procedural validation inside the planner is rigid and untestable.
1.  Create `agent_baton/core/engine/analyzers.py`.
2.  Define an `Analyzer` protocol that accepts a `MachinePlan` and returns validation results.
3.  Implement discrete analyzers:
    *   `DependencyAnalyzer`: Validates step DAG ordering and cyclic dependencies.
    *   `RiskAnalyzer`: Flags steps requiring human approval.
    *   `CapabilityAnalyzer`: Matches steps to available agents (integrating with StackDetector).
    *   **`DepthAnalyzer` (New):** Rejects subscale plans. If a step contains multiple distinct actions (e.g., "research and write"), it fails the plan and forces the Strategy to recursively decompose it into a deeper, substance-level DAG (e.g., Step 1.1: Audit, Step 1.2: Design, Step 1.3: Write).

### 2.2 Implement Plan Strategies
1.  Create `agent_baton/core/engine/strategies.py`.
2.  Define a `PlanStrategy` protocol.
3.  Move the LLM prompt construction out of the planner and into specific strategies:
    *   `ZeroShotStrategy`: Generates plans from scratch.
    *   `TemplateStrategy`: Uses existing `.claude` playbook templates.
    *   `RefinementStrategy`: Amends an existing, partially executed plan.

### 2.3 Refactor `IntelligentPlanner`
The `IntelligentPlanner` class becomes a lightweight pipeline orchestrator:
```python
class IntelligentPlanner:
    def generate(self, objective, context) -> MachinePlan:
        strategy = self._select_strategy(objective)
        draft_plan = strategy.execute(objective, context)
        
        # Pipeline execution
        for analyzer in self.analyzers:
            draft_plan = analyzer.validate(draft_plan)
            
        return draft_plan
```

---

## 3. Phase 2: ActionResolver Extraction (Weeks 3-4)

**Goal:** Remove complex decision-making logic from the `ExecutionEngine`. The engine should simply hold context, not compute transitions.

### 3.1 Extract `ActionResolver`
1.  Create `agent_baton/core/engine/resolver.py`.
2.  Extract the massive `_determine_action()` method (and its helper logic) from `executor.py` into `ActionResolver.determine_next(state: ExecutionState) -> Action`.
3.  The Resolver becomes purely functional: it evaluates the current state, active phase, and completed steps, and computes the next `ActionType` (`WAIT`, `DISPATCH`, `APPROVE`, `GATE`, `COMPLETE`). It mutates nothing.

### 3.2 Engine Injection
1.  Inject `ActionResolver` into `ExecutionEngine`.
2.  Update `engine.next_action()` to simply call `self.resolver.determine_next(self.state)`.
3.  **Validation Gate:** At this point, all existing engine transition tests MUST pass without modification.

---

## 4. Phase 3: PhaseManager and the State Pattern (Weeks 5-6)

**Goal:** Encapsulate phase boundaries and state transitions.

### 4.1 Extract `PhaseManager`
1.  Create `agent_baton/core/engine/phase_manager.py`.
2.  Extract all logic related to phase progression:
    *   Checking if all steps in a phase are complete.
    *   Evaluating phase-level `[APPROVAL REQUIRED]` gates.
    *   Advancing the `active_phase_index`.

### 4.2 Implement the State Pattern
The `ExecutionEngine` currently uses massive `if/elif` blocks based on string statuses.
1.  Create `agent_baton/core/engine/states.py`.
2.  Define discrete state classes implementing an `ExecutionPhaseState` protocol:
    *   `PlanningState`
    *   `ExecutingPhaseState`
    *   `AwaitingApprovalState`
    *   `TerminalState` (Complete/Failed)
3.  Move the mutation logic (updating step results, handling gate failures) into these state objects.

---

## 5. Phase 4: Final Core Consolidation (Week 7)

**Goal:** Slim down the `ExecutionEngine` to a pure Facade/Context-Holder.

### 5.1 Consolidate Engine Interface
The `ExecutionEngine` will shrink from ~2,600 lines down to roughly ~300 lines of pure structural orchestration. Its primary responsibility will be coordinating the injected dependencies:

```python
class ExecutionEngine:
    def __init__(self, repository, resolver, phase_manager, bus):
        self.repository = repository
        self.resolver = resolver
        self.phase_manager = phase_manager
        self.bus = bus

    def next_action(self) -> Action:
        state = self.repository.get_execution_state()
        return self.resolver.determine_next(state)
        
    def record_step_result(self, step_id, result):
        with self.repository.transaction():
            self.repository.save_step_result(step_id, result)
            self.phase_manager.evaluate_progression(self.repository.get_state())
            self.bus.publish(StepCompletedEvent(step_id=step_id))
```

## 6. Risk Mitigation

1.  **Iterative Integration:** We are not rewriting the engine from scratch. We are *extracting* existing code into new files. The public API of `ExecutionEngine` and `IntelligentPlanner` remains strictly identical throughout the transition.
2.  **Test Coverage Assurance:** Because we maintain the public API, we will rely completely on the existing `tests/test_engine_planner.py` and `tests/test_executor.py` suites. We will not proceed to the next phase if any integration tests fail.