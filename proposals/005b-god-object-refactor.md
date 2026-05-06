# Proposal 005b: Core Engine & Planner Decomposition

## 1. Objective
Refactor the massive `ExecutionEngine` (~2600 lines) and `IntelligentPlanner` (~1700 lines) into maintainable, decoupled components using State and Strategy design patterns.

## 2. ExecutionEngine Decomposition
The Engine currently acts as a massive `switch` statement for state transitions, mixing I/O, validation, and routing. We will decompose this into three primary components:

### A. ActionResolver
Responsible purely for determining the next action based on the current state graph.
```python
class ActionResolver:
    def determine_next(self, state: ExecutionState) -> Action:
        # Evaluates current state, active phase, and incomplete steps
        # Returns Action.WAIT, Action.EXECUTE, Action.APPROVE, etc.
```

### B. PhaseManager
Handles the logic for phase boundaries:
- Validating that all step prerequisites of a phase are complete.
- Executing phase-level QA gates.
- Managing the transition of the `active_phase_index`.

### C. State Pattern Implementations
Instead of monolithic `if/elif` blocks, represent execution states as discrete objects adhering to an `ExecutionPhaseState` protocol. Each state object will manage its specific validation and transition logic, ensuring open-closed principle compliance.

## 3. IntelligentPlanner Decomposition
The Planner operates as a rigid 14-step procedural pipeline that is difficult to test or extend.

### A. Strategy Pattern
Define a `PlanStrategy` protocol to allow different planning approaches:
```python
class PlanStrategy(Protocol):
    def generate(self, objective: str, context: Context) -> MachinePlan: ...
```
Implementations will include `ZeroShotStrategy`, `TemplateStrategy` (based on prior playbooks), and `RefinementStrategy` (for amendments).

### B. Analyzer Pipeline
Break the 14-step pipeline into composable `Analyzer` components:
- `DependencyAnalyzer`: Validates step DAG ordering.
- `RiskAnalyzer`: Flags steps requiring human approval based on governance policies.
- `CapabilityAnalyzer`: Matches steps to agent capabilities.

The main `IntelligentPlanner` will simply instantiate the selected strategy and pipe the resultant draft plan through the registered analyzers for validation.

### C. Deep Decomposition Strategy (Addressing Subscale Plans)
A critical issue identified in live execution is that the planner often generates shallow plans that act only as "governance wrappers." This forces agents to perform inline dispatches for substantive work (research, audit, rewriting), effectively abandoning the orchestrator's visibility and control. 

To resolve this, the Planner must be upgraded to generate substance-level plans:
- **Recursive Task Breakdown:** Strategies must enforce a minimum depth or complexity threshold. If a step involves "research and rewrite," the strategy must decompose this into distinct, discrete steps (e.g., Step 1.1: Audit, Step 1.2: IA Design, Step 1.3: Draft, Step 1.4: Refine).
- **Sub-Agent Delegation at the Planner Level:** Instead of agents deciding to spawn sub-agents mid-execution, the `IntelligentPlanner` will statically map these discrete sub-tasks to specialized agents during the planning phase.
- **Decomposition Validation:** Introduce a `DepthAnalyzer` in the Analyzer Pipeline that rejects plans that are too shallow or broad, forcing the LLM to refine and break down the tasks further before the plan is approved for execution.