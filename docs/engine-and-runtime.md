# Execution Engine and Runtime

This document is the authoritative reference for Agent Baton's execution
engine (`agent_baton/core/engine/`) and runtime system
(`agent_baton/core/runtime/`). It covers the complete lifecycle of an
orchestrated task from plan creation through completion, including the
state machine, knowledge resolution, gate system, daemon mode, and crash
recovery.

---

## 1. Overview

The execution engine is the core of Agent Baton. It converts a
human-readable task description into a machine-readable execution plan,
then drives that plan through a state machine that coordinates multiple
AI agents, enforces quality gates, and persists state for crash recovery.

### Design Philosophy

**Separation of concerns.** The system is split into two layers:

- **Engine** (`core/engine/`) -- deterministic state machine. Owns plan
  state, decides what happens next, records results. Stateless between
  calls (all state lives on disk). Called repeatedly by the driving
  session.
- **Runtime** (`core/runtime/`) -- async execution layer. Launches
  agents, manages concurrency, handles signals, runs the daemon. Wraps
  the engine without replacing it.

**The engine is the source of truth.** The runtime calls into the engine
via the `ExecutionDriver` protocol. The engine never calls the runtime.
This means the engine can be driven by the CLI, the runtime, or tests
-- all through the same API.

**State is always on disk.** Every state mutation is persisted before
the engine returns. If the process crashes between calls, `baton execute
resume` picks up exactly where it left off.

### Component Map

```
core/engine/                          core/runtime/
  planner.py    IntelligentPlanner      worker.py       TaskWorker
  executor.py   ExecutionEngine         supervisor.py   WorkerSupervisor
  dispatcher.py PromptDispatcher        scheduler.py    StepScheduler
  gates.py      GateRunner              launcher.py     AgentLauncher (protocol)
  persistence.py StatePersistence       claude_launcher.py ClaudeCodeLauncher
  protocols.py  ExecutionDriver         context.py      ExecutionContext
  knowledge_resolver.py KnowledgeResolver  daemon.py    daemonize()
  knowledge_gap.py  parse/escalation    signals.py      SignalHandler
  plan_reviewer.py  PlanReviewer        decisions.py    DecisionManager
  consolidator.py   CommitConsolidator
```

### Relationship Between Engine and Runtime

```
                    CLI (baton execute next)
                           |
                           v
    +---------------------------------------------------+
    |              ExecutionEngine                       |
    |  (state machine, plan state, trace recording)     |
    +---------------------------------------------------+
         ^                                    ^
         |                                    |
    CLI-driven loop                    Runtime-driven loop
    (synchronous,                      (async, parallel,
     one action at a time)              daemon mode)
         |                                    |
         v                                    v
    Claude session                     TaskWorker
    reads _print_action()              calls engine.next_actions()
    spawns Agent tool                  dispatches via StepScheduler
    calls baton execute record         records results back
```

---

## 2. Execution Lifecycle

The complete flow from task description to completion:

```
baton plan "task" --save --explain
         |
         v
  IntelligentPlanner.create_plan()
         |
         v
  plan.json + plan.md written to .claude/team-context/
         |
         v
baton execute start
         |
         v
  ExecutionEngine.start(plan)
    - Creates ExecutionState
    - Starts trace
    - Saves state to disk
    - Returns first action
         |
         v
  +--> ExecutionEngine.next_action()
  |        |
  |        +-- DISPATCH --> caller spawns agent
  |        |                  baton execute dispatched
  |        |                  (agent works)
  |        |                  baton execute record
  |        |
  |        +-- GATE -----> caller runs gate command
  |        |                  baton execute gate --result pass|fail
  |        |
  |        +-- APPROVAL --> caller presents to human
  |        |                  baton execute approve
  |        |
  |        +-- WAIT -----> parallel steps in flight; retry
  |        |
  |        +-- FAILED ---> execution stops
  |        |
  |        +-- COMPLETE -> baton execute complete
  |                          - Writes trace
  +---<--- (loop)            - Writes usage log
                             - Generates retrospective
                             - Triggers improvement cycle
```

### ASCII Sequence Diagram: CLI-Driven Execution

```
Claude         CLI            Engine         Disk
  |              |               |              |
  |--plan------->|               |              |
  |              |--create_plan->|              |
  |              |<--MachinePlan-|              |
  |              |--save---------|------------->| plan.json
  |              |               |              |
  |--execute---->|               |              |
  |  start       |--start(plan)->|              |
  |              |               |--save------->| execution-state.json
  |              |<--DISPATCH----|              |
  |              |               |              |
  |--Agent tool->| (spawns agent)|              |
  |              |               |              |
  |--execute---->|               |              |
  |  record      |--record------>|              |
  |              |               |--save------->| execution-state.json
  |              |               |              |
  |--execute---->|               |              |
  |  next        |--next_action->|              |
  |              |<--GATE--------|              |
  |              |               |              |
  |--execute---->|               |              |
  |  gate        |--record_gate->|              |
  |              |               |--save------->| execution-state.json
  |              |               |              |
  |  ...         |  (repeats)    |              |
  |              |               |              |
  |--execute---->|               |              |
  |  complete    |--complete()--->|              |
  |              |               |--save------->| trace-YYYY.json
  |              |               |--save------->| usage-log.jsonl
  |              |               |--save------->| retrospective.json
  |              |<--summary-----|              |
```

---

## 3. Planning System

### IntelligentPlanner

**Source:** `agent_baton/core/engine/planner.py`

The planner transforms a task description into a `MachinePlan`. It is
data-driven: when historical data is available, plans are shaped by
learned patterns, agent performance scores, and budget recommendations.
When no history exists, it falls back to sensible defaults.

### Constructor

```python
IntelligentPlanner(
    team_context_root: Path | None = None,
    classifier: DataClassifier | None = None,
    policy_engine: PolicyEngine | None = None,
    retro_engine: RetroEngine | None = None,
    knowledge_registry: KnowledgeRegistry | None = None,
)
```

All dependencies are optional. The planner degrades gracefully:

| Dependency | When absent |
|------------|-------------|
| `PatternLearner` | Default phase templates used |
| `PerformanceScorer` | No score warnings |
| `BudgetTuner` | Agent-count heuristic |
| `DataClassifier` | Keyword-only risk assessment |
| `PolicyEngine` | No policy validation |
| `RetroEngine` | No retrospective feedback integration |
| `KnowledgeRegistry` | No knowledge resolution |

### create_plan() -- 15-Step Pipeline

```python
def create_plan(
    self,
    task_summary: str,
    *,
    task_type: str | None = None,
    project_root: Path | None = None,
    agents: list[str] | None = None,
    phases: list[dict] | None = None,
    explicit_knowledge_packs: list[str] | None = None,
    explicit_knowledge_docs: list[str] | None = None,
    intervention_level: str = "low",
) -> MachinePlan:
```

Steps executed in order:

1. **Generate task_id** -- format: `YYYY-MM-DD-<slug>-<8-char-uuid>`.
2. **Detect project stack** -- `AgentRouter.detect_stack(project_root)`.
3. **Infer task type** -- keyword matching against `_TASK_TYPE_KEYWORDS`
   in priority order: bug-fix, migration, refactor, data-analysis,
   new-feature, test, documentation. Falls back to `new-feature`.
4. **Pattern lookup** -- `PatternLearner.get_patterns_for_task()` with
   minimum confidence threshold of 0.7.
5. **Determine agents** -- explicit override, pattern recommendation,
   or `_DEFAULT_AGENTS` lookup by task type.
   - 5b. **Retrospective feedback** -- filter dropped agents, record
     preferences via `_apply_retro_feedback()`.
6. **Route agents** -- `AgentRouter.route()` maps base names to
   stack-specific flavors (e.g. `backend-engineer` to
   `backend-engineer--python`).
   - 6.5. Create `KnowledgeResolver` if registry is available.
7. **Classify sensitivity** -- `DataClassifier.classify()` if available.
8. **Assess risk** -- combines classifier output with keyword signals
   (`_RISK_SIGNALS`) and structural signals (agent count, sensitive
   agents, destructive verbs). Read-only dampening prevents false
   positives.
   - 8b. **Git strategy** -- `COMMIT_PER_AGENT` for LOW/MEDIUM,
     `BRANCH_PER_AGENT` for HIGH/CRITICAL.
9. **Build phases** -- from explicit dicts, pattern, or defaults.
   - 9b. **Enrich** -- cross-phase context references, default
     deliverables from `_AGENT_DELIVERABLES`.
   - 9.5. **Resolve knowledge** -- `KnowledgeResolver.resolve()` per
     step with explicit packs/docs.
   - 9.6. **Gap-suggested attachments** -- query pattern learner for
     prior gaps matching agent + task type.
10. **Score check** -- warn about low-health agents via
    `PerformanceScorer`.
11. **Budget tier** -- `BudgetTuner` recommendation or heuristic
    (lean/standard/full based on agent count).
    - 11b. **Policy validation** -- check agent assignments against
      active policy set. Violations are warnings, never hard-blocks.
12. **Add QA gates** -- default gates per phase name:
    - Implement/Fix: `build` gate (pytest)
    - Test: `test` gate (pytest --cov)
    - Review and others: no automated gate
    - 12b. **Approval gates** -- HIGH/CRITICAL risk plans get approval
      gates on Design/Research phases.
    - 12c. **Team consolidation** -- multi-agent Implement/Fix phases
      are merged into team steps.
13. **Context files** -- every step gets `CLAUDE.md`; file paths
    extracted from task summary are appended.
    - 13b. **Model inheritance** -- agent definition model preferences.
    - 13c. **Context richness** -- file path extraction from summary.
14. **Build shared context** -- mission summary, risk, governance
    results, retrospective insights.
15. Return `MachinePlan`.

### Task Type Inference

Keywords are matched in priority order (first match wins):

| Task Type | Keywords |
|-----------|----------|
| `bug-fix` | fix, bug, broken, error, crash, traceback, exception, patch |
| `migration` | migrate, migration, upgrade, move |
| `refactor` | refactor, clean, reorganize, restructure, rename, cleanup |
| `data-analysis` | analyze, report, dashboard, query, insight, metric |
| `new-feature` | add, build, create, implement, new, feature, develop |
| `test` | test, tests, testing, coverage, e2e, unit, integration |
| `documentation` | doc, docs, readme, spec, adr, document, wiki, review, summarize |

### Risk Assessment

Risk is computed by combining signals:

1. **Keyword signals** -- words like "production", "infrastructure",
   "deploy", "security" map to HIGH; "migration", "database" map to
   MEDIUM (via `_RISK_SIGNALS`).
2. **Structural signals** -- more than 5 agents raises to at least
   MEDIUM; sensitive agent types (security-reviewer, auditor, devops)
   raise to MEDIUM; destructive verbs raise to MEDIUM.
3. **Read-only dampening** -- if the first word is a read-only verb
   (review, analyze, inspect) and no sensitive agents are present, the
   score is capped at LOW.
4. **Classifier floor** -- if `DataClassifier` is available, its risk
   level is the floor (keyword signals can raise but not lower it).

### Agent Routing

The planner uses affinity-based assignment to distribute agents across
phases:

1. **Pass 1**: assign agents to their ideal phases based on
   `_PHASE_IDEAL_ROLES` (e.g. architect to Design, test-engineer to
   Test).
2. **Pass 2**: remaining agents assigned to remaining phases
   round-robin.
3. **Pass 3**: unassigned phases get the best-fit agent from the full
   pool.
4. **Pass 4**: leftover agents added to phases where they have
   affinity.
5. **Guarantee**: every phase has at least one step.

### Plan Structure

```
MachinePlan
  task_id: str
  task_summary: str
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  budget_tier: "lean" | "standard" | "full"
  git_strategy: "Commit-per-agent" | "Branch-per-agent"
  task_type: str
  intervention_level: "low" | "medium" | "high"
  phases: list[PlanPhase]
    phase_id: int (1-based)
    name: str
    approval_required: bool
    steps: list[PlanStep]
      step_id: str (e.g. "1.1")
      agent_name: str
      task_description: str
      model: str (default "sonnet")
      depends_on: list[str]
      context_files: list[str]
      knowledge: list[KnowledgeAttachment]
      team: list[TeamMember]  (non-empty = team step)
    gate: PlanGate | None
      gate_type: "build" | "test" | "lint" | "spec" | "review"
      command: str
      fail_on: list[str]
```

### explain_plan()

Returns a human-readable markdown explanation including:
- Pattern influence (pattern ID, confidence, success rate)
- Score warnings for low-health agents
- Agent routing decisions
- Data classification and guardrail preset
- Policy notes and violations
- Phase summary with agents and gates

---

## 4. Execution Engine

### ExecutionEngine

**Source:** `agent_baton/core/engine/executor.py`

The engine is a state machine that is called repeatedly by the driving
session. Each call reads state from disk, computes the next action, and
persists the updated state.

### Constructor

```python
ExecutionEngine(
    team_context_root: Path | None = None,
    bus: EventBus | None = None,
    task_id: str | None = None,
    storage=None,          # SqliteStorage | FileStorage | None
    knowledge_resolver=None,  # KnowledgeResolver | None
)
```

The engine supports two storage modes:

| Mode | Primary I/O | Dual-write |
|------|------------|------------|
| **Legacy file** | `StatePersistence` (JSON files) | N/A |
| **Storage backend** | `SqliteStorage` | File persistence for backward compat |

When a `storage` backend is provided, the engine writes to SQLite
first, then dual-writes to the file system so file-based readers
(scanner, list/switch) stay current during the transition.

### State Machine

```
                  start(plan)
                      |
                      v
              +-- RUNNING --+
              |              |
   +----------+----------+  |
   |          |           |  |
DISPATCH    GATE     APPROVAL|
   |          |           |  |
   v          v           v  |
record_   record_    record_ |
step_     gate_      approval|
result    result     _result |
   |          |           |  |
   +----------+---+-------+  |
              |   |          |
              v   v          |
         GATE_PENDING  APPROVAL_PENDING
              |               |
              v               v
         (re-enters RUNNING after result recorded)
              |
              v
          COMPLETE <-- all phases done
              |
              v
          FAILED <-- step failed or gate failed
```

### Status Values

| Status | Meaning |
|--------|---------|
| `running` | Normal execution in progress |
| `gate_pending` | Waiting for gate result |
| `approval_pending` | Waiting for human approval |
| `complete` | All phases finished successfully |
| `failed` | A step or gate failed |

### Action Types

| ActionType | Enum Value | When Returned |
|------------|-----------|---------------|
| `DISPATCH` | `"dispatch"` | Next step is ready for agent execution |
| `GATE` | `"gate"` | All steps in phase complete; run QA gate |
| `APPROVAL` | `"approval"` | Phase requires human approval before proceeding |
| `WAIT` | `"wait"` | Parallel steps still in flight |
| `COMPLETE` | `"complete"` | All phases exhausted; execution done |
| `FAILED` | `"failed"` | Unrecoverable failure |

### Public API

#### start(plan) -> ExecutionAction

Initialize execution from a `MachinePlan`:
- Creates `ExecutionState` with `status="running"`.
- Starts a trace via `TraceRecorder`.
- Sets the active task ID.
- Persists state.
- Returns the first action via `_determine_action()`.

#### next_action() -> ExecutionAction

Load state from disk and return the next action. The core state machine
logic (`_determine_action`) executes:

1. Terminal check: if `complete` or `failed`, return immediately.
2. `approval_pending`: return APPROVAL action.
3. `gate_pending`: return GATE action.
4. Phase exhaustion: all phases done -> COMPLETE.
5. Failed step check: any step failed -> FAILED.
6. Find next dispatchable step (not completed, failed, dispatched, or
   interrupted; all `depends_on` satisfied).
7. If found: return DISPATCH (or team DISPATCH for team steps).
8. If steps still pending (dispatched or dependency-blocked): WAIT.
9. All steps done: check approval -> gate -> advance to next phase.

#### next_actions() -> list[ExecutionAction]

Return ALL currently dispatchable actions for parallel execution.
Unlike `next_action()`, this returns every step whose dependencies are
satisfied. The caller can spawn all returned agents in parallel.

#### mark_dispatched(step_id, agent_name)

Record that a step is in-flight (status `"dispatched"`). Used by the
runtime to prevent the engine from re-dispatching steps during parallel
execution.

#### record_step_result(step_id, agent_name, status, outcome, ...)

Record an agent's result:
- Creates `StepResult` and appends to state.
- Extracts deviations from outcome text (sections headed
  `## Deviation`/`## Deviations`).
- Runs the knowledge gap protocol on the outcome.
- Emits trace events (`agent_complete` or `agent_failed`).
- Logs telemetry.
- Persists state.

Valid status values: `"complete"`, `"failed"`, `"dispatched"`,
`"interrupted"`.

#### record_gate_result(phase_id, passed, output)

Record a gate check result:
- Creates `GateResult` and appends to state.
- If failed: sets `status = "failed"`.
- If passed: advances `current_phase` and resets `current_step_index`.
- Emits trace events and domain events.

#### record_approval_result(phase_id, result, feedback)

Record a human approval decision:
- Valid results: `"approve"`, `"reject"`, `"approve-with-feedback"`.
- `"reject"`: sets status to `"failed"`.
- `"approve"`: sets status to `"running"`.
- `"approve-with-feedback"`: triggers `_amend_from_feedback()` which
  inserts a remediation phase after the current phase, then sets status
  to `"running"`.

#### amend_plan(description, new_phases, insert_after_phase, ...)

Amend the running plan mid-execution:
- Inserts new phases at the specified position (default: after current
  phase).
- Adds new steps to existing phases.
- Re-numbers all phase and step IDs via `_renumber_phases()`.
- Creates a `PlanAmendment` audit record.
- Emits a `replan` trace event.

#### record_team_member_result(step_id, member_id, agent_name, ...)

Record a single team member's work within a team step:
- Creates `TeamStepResult` and appends to the parent `StepResult`.
- When all members complete, the parent step auto-completes.
- If any member fails, the parent step fails.

#### complete() -> str

Finalize execution:
1. Sets `status = "complete"`.
2. Reconstructs trace from state if `self._trace` is None (CLI mode
   creates a fresh engine instance per call).
3. Completes the trace via `TraceRecorder`.
4. Builds and logs a `TaskUsageRecord`.
5. Generates a retrospective via `RetrospectiveEngine` with rich
   qualitative data (what worked, what didn't, knowledge gaps, roster
   recommendations, sequencing notes).
6. Triggers the improvement loop (`ImprovementLoop.run_cycle()`).
7. Returns a summary string.

#### status() -> dict

Returns: `task_id`, `status`, `current_phase`, `steps_complete`,
`steps_total`, `gates_passed`, `gates_failed`, `elapsed_seconds`.

#### resume() -> ExecutionAction

Crash recovery:
1. Loads state from disk.
2. Reconnects the in-memory trace (loads existing or starts fresh).
3. Returns the next action via `_determine_action()`.

#### recover_dispatched_steps() -> int

Clears stale `dispatched` step markers after a daemon crash, so the
engine will re-dispatch them on the next `next_action()` call. Returns
the number of recovered steps.

---

## 5. Dispatcher

### PromptDispatcher

**Source:** `agent_baton/core/engine/dispatcher.py`

Stateless class that generates delegation prompts for agent subagents.
Every method operates purely on its arguments.

### build_delegation_prompt()

```python
def build_delegation_prompt(
    self,
    step: PlanStep,
    *,
    shared_context: str = "",
    handoff_from: str = "",
    project_description: str = "",
    task_summary: str = "",
    task_type: str = "",
) -> str:
```

Produces a structured markdown prompt with these sections:

1. **Role line** -- "You are a {role} working on {project}."
2. **Shared Context** -- mission summary, risk, governance.
3. **Intent** -- user's original task summary (verbatim).
4. **Knowledge Context** -- inline knowledge documents.
5. **Knowledge References** -- referenced documents with retrieval
   hints.
6. **Your Task (Step X)** -- the step's task description.
7. **Success Criteria** -- selected by task type from
   `_SUCCESS_CRITERIA` (bug-fix, new-feature, refactor, test,
   documentation, migration, data-analysis).
8. **Files to Read** -- `step.context_files`.
9. **Deliverables** -- expected outputs.
10. **Boundaries** -- allowed/blocked paths.
11. **Knowledge Gaps** -- instructions for the agent to signal gaps.
12. **Previous Step Output** -- handoff from preceding step.
13. **Decision Logging** -- document non-obvious decisions.
14. **Deviations** -- document plan misfit.

### build_team_delegation_prompt()

Similar to `build_delegation_prompt` but tailored for team members:
- Includes team overview and member role.
- References dependencies on other team members.
- Knowledge is resolved at step level and shared across all members.

### build_gate_prompt()

For automated gates: returns the command string (with `{files}`
placeholder substitution).

For review gates (no command): returns a reviewer prompt with the gate
type, review task, fail criteria, and instructions.

### build_path_enforcement()

Generates a bash guard command for `PreToolUse` hooks that blocks
writes outside allowed paths or inside blocked paths. Returns `None`
when the step has no path restrictions.

### build_action()

Combines `build_delegation_prompt` + `build_path_enforcement` into a
complete `ExecutionAction` with `action_type=DISPATCH`.

### Knowledge Injection

The dispatcher builds knowledge sections from `KnowledgeAttachment`
objects:

- **Inline attachments**: full content loaded from `source_path` via
  `parse_frontmatter()`, rendered under `## Knowledge Context`.
- **Reference attachments**: path + summary + retrieval hint, rendered
  under `## Knowledge References`. Retrieval hint is either
  `Read <path>` or `query RAG server` depending on `retrieval` field.

### ContextHarvester (Wave 2.2)

**Source:** `agent_baton/core/intel/context_harvester.py`

Runs after every successful step (`record_step_result` →
`ContextHarvester.harvest`) and writes a compact 3-5 line summary into
the `agent_context` table keyed by `(agent_name, domain)`. Domain is
derived from `PlanStep.allowed_paths[0]` (or `files_changed[0]`),
falling back to `"general"`. On the next dispatch the executor reads
the row via `ContextHarvester.fetch_one` and passes it through
`build_delegation_prompt(prior_context_block=...)`, prepending a
`## Prior Context` block (capped at 400 chars) so the agent skips
cold-start re-discovery. Best-effort — disabled by
`BATON_HARVEST_CONTEXT=0`. Inspected via `baton agent-context show <agent>`.

---

## 6. Gate System

### GateRunner

**Source:** `agent_baton/core/engine/gates.py`

Stateless class that builds gate actions and evaluates gate results.

### Gate Types

| Type | Command | Evaluation Rule |
|------|---------|-----------------|
| `build` | `python -m py_compile {files}` | `passed = (exit_code == 0)` |
| `test` | `pytest --tb=short -q` | `passed = (exit_code == 0)` |
| `lint` | `python -m py_compile {files}` | `passed = (exit_code == 0 AND no error markers)` |
| `spec` | (varies) | Delegates to `SpecValidator.run_gate()` |
| `review` | (none) | Always passes (advisory) |
| `approval` | (none) | Human checkpoint |

### build_gate_action()

Builds an `ExecutionAction` with `action_type=GATE`:
- Populates `gate_type`, `gate_command` (with `{files}` substitution),
  and `phase_id`.

### evaluate_output()

Evaluates the output of a gate command and returns a `GateResult`:
- `test`/`build`: pass when `exit_code == 0`.
- `lint`: pass when `exit_code == 0` AND no lint error markers in
  output. Error markers: `": error:"`, `":E:"`, `" E "`, `"[E"`,
  `"Error:"`, `"ERROR"`, `"SyntaxError"`, `"error:"`.
- `spec`: delegates to `SpecValidator.run_gate()`.
- `review`: always passes (advisory).
- Unknown types: fall back to `exit_code == 0`.

### default_gates()

Returns a fresh dict of built-in gate definitions (build, test, lint,
review). Callers may mutate the returned values.

---

## 7. State Persistence

### StatePersistence

**Source:** `agent_baton/core/engine/persistence.py`

Handles reading and writing `ExecutionState` to disk with crash-safe
atomic writes (write to `.json.tmp`, then `rename()`).

### Storage Layout

```
.claude/team-context/
  executions/
    <task-id-1>/execution-state.json
    <task-id-2>/execution-state.json
  active-task-id.txt          <- points to default task
  execution-state.json        <- legacy flat file (backward compat)
```

When `task_id` is provided, state is stored under the namespaced path.
Without `task_id`, falls back to the legacy flat path.

### API

| Method | Description |
|--------|-------------|
| `save(state)` | Atomically write state (tmp + rename) |
| `load()` | Load state; returns `None` on missing or parse error |
| `exists()` | Check if state file exists |
| `clear()` | Remove state file |
| `set_active()` | Write `task_id` to `active-task-id.txt` |
| `get_active_task_id(root)` | Read active task ID (static) |
| `list_executions(root)` | List all namespaced task IDs (static) |
| `load_all(root)` | Load all states (namespaced + legacy) (static) |

### Task-ID Resolution Order

Every `baton execute` subcommand resolves a target task ID through:

```
--task-id flag  ->  BATON_TASK_ID env var  ->  active-task-id.txt  ->  None
```

See `docs/invariants.md` Invariant 1 for the full contract.

### execution-state.json Schema

```json
{
  "task_id": "2026-03-24-add-oauth-abc12345",
  "plan": { /* MachinePlan.to_dict() */ },
  "current_phase": 1,
  "current_step_index": 0,
  "status": "running",
  "step_results": [ /* StepResult.to_dict() */ ],
  "gate_results": [ /* GateResult.to_dict() */ ],
  "approval_results": [ /* ApprovalResult.to_dict() */ ],
  "amendments": [ /* PlanAmendment.to_dict() */ ],
  "started_at": "2026-03-24T10:00:00+00:00",
  "completed_at": "",
  "pending_gaps": [ /* KnowledgeGapSignal.to_dict() */ ],
  "resolved_decisions": [ /* ResolvedDecision.to_dict() */ ]
}
```

---

## 8. Knowledge Resolution

### KnowledgeResolver

**Source:** `agent_baton/core/engine/knowledge_resolver.py`

Resolves knowledge attachments for each plan step through a 4-layer
pipeline with deduplication. Produces `KnowledgeAttachment` objects
with inline/reference delivery decisions governed by token budgets.

### Constructor

```python
KnowledgeResolver(
    registry: KnowledgeRegistry,
    *,
    agent_registry: AgentRegistry | None = None,
    rag_available: bool = False,
    step_token_budget: int = 32_000,
    doc_token_cap: int = 8_000,
)
```

### 4-Layer Resolution Pipeline

```
Layer 1: Explicit         User-supplied --knowledge-pack / --knowledge flags
    |
Layer 2: Agent-declared   Packs listed in agent definition frontmatter
    |
Layer 3: Tag matching     Strict keyword/tag match against registry
    |
Layer 4: Relevance        TF-IDF search (only if Layer 3 returned nothing)
```

Each layer's results are deduplicated against earlier layers using a
key of `source_path` (preferred) or `pack_name::doc_name`.

Within each layer, documents are sorted by priority: high -> normal ->
low.

### Delivery Decision

For each resolved document, the resolver applies these rules:

| Condition | Delivery |
|-----------|----------|
| `token_estimate <= 0` | reference (unestimated) |
| `token_estimate > doc_token_cap (8K)` | reference (too large) |
| `token_estimate <= remaining_budget` | inline (fits budget, budget decremented) |
| Otherwise | reference (budget exhausted) |

Reference deliveries get `retrieval="mcp-rag"` when RAG is available,
otherwise `retrieval="file"`.

### KnowledgeGap -- Runtime Gap Detection

**Source:** `agent_baton/core/engine/knowledge_gap.py`

Handles parsing `KNOWLEDGE_GAP` signals from agent outcomes:

```
KNOWLEDGE_GAP: Need context on SOX audit trail requirements
CONFIDENCE: none
TYPE: contextual
```

#### parse_knowledge_gap()

Parses the structured signal block from outcome text. Returns
`KnowledgeGapSignal` or `None`. Defaults: confidence=`"low"`,
gap_type=`"factual"`.

#### determine_escalation()

Applies the escalation matrix:

| Gap Type | Resolution | Risk x Intervention | Action |
|----------|-----------|---------------------|--------|
| factual | match found | any | `auto-resolve` |
| factual | no match | LOW + low intervention | `best-effort` |
| factual | no match | LOW + medium/high | `queue-for-gate` |
| factual | no match | MEDIUM+ any | `queue-for-gate` |
| contextual | -- | any | `queue-for-gate` |

#### Engine Integration

When `record_step_result()` detects a knowledge gap:

1. Parse the signal from outcome text.
2. Attempt auto-resolution via `KnowledgeResolver` if available.
3. Call `determine_escalation()`.
4. **auto-resolve**: Record a `ResolvedDecision` on the state, amend
   the plan to insert a re-dispatch step for the same agent with the
   resolved knowledge attached. The interrupted step is marked
   `"interrupted"` and skipped by `_determine_action()`.
5. **best-effort**: Log and continue (no plan mutation).
6. **queue-for-gate**: Append the signal to `state.pending_gaps` where
   it surfaces at the next human approval gate.

---

## 9. Runtime System

### TaskWorker

**Source:** `agent_baton/core/runtime/worker.py`

Async event loop that drives a single task's execution. Wraps the
`ExecutionEngine` via the `ExecutionDriver` protocol.

```python
TaskWorker(
    engine: ExecutionDriver,
    launcher: AgentLauncher,
    bus: EventBus | None = None,
    max_parallel: int = 3,
    decision_manager: DecisionManager | None = None,
    shutdown_event: asyncio.Event | None = None,
    gate_poll_interval: float = 2.0,
)
```

#### Execution Loop

```python
async def run(self) -> str:
```

The core loop:

1. Call `engine.next_action()`.
2. **COMPLETE**: call `engine.complete()`, return summary.
3. **FAILED**: return error message.
4. **WAIT**: sleep 0.5s and retry.
5. **APPROVAL**: route through `DecisionManager` or auto-approve.
6. **GATE**: auto-approve programmatic gates (test, build, lint, spec);
   route human gates through `DecisionManager`.
7. **DISPATCH**: collect ALL dispatchable steps via
   `engine.next_actions()`, mark each dispatched, dispatch in parallel
   via `StepScheduler`, record all results.

#### Event Publishing

The worker publishes step-level events (`step.dispatched`,
`step.completed`, `step.failed`). Task-level and phase-level events are
published by the `ExecutionEngine` itself. This split avoids event
duplication.

### StepScheduler

**Source:** `agent_baton/core/runtime/scheduler.py`

Bounded-concurrency dispatcher using `asyncio.Semaphore`.

```python
@dataclass
class SchedulerConfig:
    max_concurrent: int = 3

class StepScheduler:
    async def dispatch(self, agent_name, model, prompt, step_id, launcher) -> LaunchResult
    async def dispatch_batch(self, steps: list[dict], launcher) -> list[LaunchResult]
```

`dispatch_batch()` starts all steps concurrently but at most
`max_concurrent` run simultaneously. Results are returned in the same
order as the input steps.

### AgentLauncher (Protocol)

**Source:** `agent_baton/core/runtime/launcher.py`

```python
class AgentLauncher(Protocol):
    async def launch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
    ) -> LaunchResult: ...

@dataclass
class LaunchResult:
    step_id: str
    agent_name: str
    status: str = "complete"   # "complete" or "failed"
    outcome: str = ""
    files_changed: list[str]
    commit_hash: str = ""
    estimated_tokens: int = 0
    duration_seconds: float = 0.0
    error: str = ""
```

Implementations:
- `DryRunLauncher` -- mock for testing; records launches, returns
  synthetic results. Supports pre-configured per-step results via
  `set_result()`.
- `ClaudeCodeLauncher` -- production launcher that invokes the `claude`
  CLI.

### ClaudeCodeLauncher

**Source:** `agent_baton/core/runtime/claude_launcher.py`

Invokes the `claude` CLI as an async subprocess with strict security
properties:

- **Environment whitelist**: only `PATH`, `HOME`, and explicitly
  listed variables (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_USE_BEDROCK`,
  `CLAUDE_CODE_USE_VERTEX`, `AWS_PROFILE`, `AWS_REGION`) are forwarded.
  `os.environ` is never copied wholesale.
- **No shell interpolation**: `asyncio.create_subprocess_exec` is used
  exclusively. The prompt is always a separate list element.
- **Binary validation**: the `claude` binary path is validated at
  construction time via `shutil.which()`.
- **API key redaction**: `sk-ant-*` patterns are stripped from error
  text.

#### Configuration

```python
@dataclass
class ClaudeCodeConfig:
    claude_path: str = "claude"
    working_directory: Path | None = None
    default_timeout_seconds: float = 600.0
    model_timeouts: dict = {"opus": 900, "sonnet": 600, "haiku": 300}
    max_retries: int = 3
    base_retry_delay: float = 5.0
    max_outcome_length: int = 4000
    prompt_file_threshold: int = 131_072  # 128 KB
    env_passthrough: list[str] = [...]
```

#### Launch Flow

1. Record pre-launch git HEAD.
2. Build command: `claude --print --model <model> --output-format json`.
   If agent definition exists, add `--system-prompt`, `--permission-mode`,
   `--allowedTools`.
3. If prompt exceeds 128 KB, deliver via stdin instead of `-p` flag.
4. Run subprocess with timeout.
5. On rate-limit error (429), retry with exponential backoff (up to
   `max_retries`).
6. Parse output: attempt JSON (structured), fall back to raw text.
7. If agent committed code, diff pre/post HEAD to populate
   `files_changed` and `commit_hash`.

### ExecutionContext

**Source:** `agent_baton/core/runtime/context.py`

Factory that guarantees `EventBus`, `ExecutionEngine`, and
`EventPersistence` are all wired to the same bus instance.

```python
@classmethod
def build(
    cls,
    *,
    launcher: AgentLauncher,
    team_context_root: Path | None = None,
    bus: EventBus | None = None,
    persist_events: bool = True,
    task_id: str | None = None,
) -> ExecutionContext:
```

The engine auto-wires `EventPersistence` as a bus subscriber when a bus
is provided. `ExecutionContext.build()` does NOT create a second
persistence instance to avoid duplicate JSONL writes.

### WorkerSupervisor

**Source:** `agent_baton/core/runtime/supervisor.py`

Lifecycle management for daemon-mode execution. Wraps `TaskWorker`
with PID file management, structured logging, and graceful shutdown.

#### Files Managed

| Namespaced (with task_id) | Legacy |
|--------------------------|--------|
| `executions/<task_id>/worker.pid` | `daemon.pid` |
| `executions/<task_id>/worker.log` | `daemon.log` |
| `executions/<task_id>/worker-status.json` | `daemon-status.json` |

#### start()

1. Write PID file with `flock()` exclusive lock (prevents double-start).
2. Configure rotating file logging (10 MB, 3 backups).
3. Build `ExecutionContext` with launcher, bus, task_id.
4. Call `engine.start(plan)` or `engine.resume()`.
5. Create `TaskWorker` and run via `asyncio.run()`.
6. Install signal handlers (SIGTERM, SIGINT) for graceful shutdown.
7. On completion/crash: write status snapshot, remove PID file.

#### Graceful Shutdown

```python
async def _run_with_signals(self, worker):
    handler = SignalHandler()
    handler.install()
    worker_task = asyncio.create_task(worker.run())
    signal_task = asyncio.create_task(handler.wait())
    done, pending = await asyncio.wait(
        {worker_task, signal_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if signal_task in done:
        worker_task.cancel()
        await asyncio.wait_for(worker_task, timeout=30.0)
```

When a signal arrives, the worker task is cancelled and the supervisor
waits up to 30 seconds for in-flight agents to drain.

#### stop()

Sends `SIGTERM` to the daemon PID and polls for exit up to a timeout.
Cleans up stale PID files.

#### list_workers()

Static method that scans all execution directories for running worker
processes. Checks liveness via `os.kill(pid, 0)`.

### Daemon Mode

**Source:** `agent_baton/core/runtime/daemon.py`

UNIX double-fork daemonization (`daemonize()`):

1. First fork -- parent exits (shell returns immediately).
2. `os.setsid()` -- child becomes session leader.
3. Second fork -- session leader exits (grandchild cannot reacquire
   terminal).
4. Redirect stdin/stdout/stderr to `/dev/null`. Higher FDs (logging,
   PID flock) are preserved.
5. Working directory is preserved (agent-baton uses relative paths).

Must be called BEFORE `asyncio.run()`. Not available on Windows.

### SignalHandler

**Source:** `agent_baton/core/runtime/signals.py`

```python
class SignalHandler:
    def install(self)     # Install SIGTERM + SIGINT handlers
    def uninstall(self)   # Restore original handlers
    async def wait(self)  # Block until signal received
    @property
    def shutdown_requested(self) -> bool
```

Uses `asyncio.get_running_loop().add_signal_handler()` to set a
shutdown event when SIGTERM or SIGINT is received.

### DecisionManager

**Source:** `agent_baton/core/runtime/decisions.py`

Manages human decision requests during async execution via filesystem
persistence.

```python
class DecisionManager:
    def request(req: DecisionRequest) -> Path    # Persist + publish event
    def resolve(request_id, chosen_option, ...)  # Mark resolved + publish
    def get(request_id) -> DecisionRequest | None
    def pending() -> list[DecisionRequest]
    def list_all() -> list[DecisionRequest]
    def get_resolution(request_id) -> dict | None
```

Each request produces:
- `<request_id>.json` -- machine-readable request.
- `<request_id>.md` -- human-readable summary with resolution
  instructions (`baton decide --resolve <id> --option <OPTION>`).

Resolution creates `<request_id>-resolution.json` and publishes a
`human_decision_resolved` event.

---

## 10. Protocols

### ExecutionDriver

**Source:** `agent_baton/core/engine/protocols.py`

The most critical contract in the system. Defines the interface between
the async `TaskWorker` and the synchronous `ExecutionEngine`.

```python
class ExecutionDriver(Protocol):
    def start(self, plan: MachinePlan) -> ExecutionAction: ...
    def next_action(self) -> ExecutionAction: ...
    def next_actions(self) -> list[ExecutionAction]: ...
    def mark_dispatched(self, step_id: str, agent_name: str) -> None: ...
    def record_step_result(
        self, step_id, agent_name, status, outcome,
        files_changed, commit_hash, estimated_tokens,
        duration_seconds, error,
    ) -> None: ...
    def record_gate_result(self, phase_id, passed, output) -> None: ...
    def record_approval_result(self, phase_id, result, feedback) -> None: ...
    def amend_plan(self, description, ...) -> PlanAmendment: ...
    def record_team_member_result(self, step_id, member_id, ...) -> None: ...
    def complete(self) -> str: ...
    def status(self) -> dict: ...
    def resume(self) -> ExecutionAction: ...
```

Any class implementing this protocol can serve as the engine for
orchestrated execution.

### AgentLauncher

**Source:** `agent_baton/core/runtime/launcher.py`

```python
class AgentLauncher(Protocol):
    async def launch(
        self, agent_name, model, prompt, step_id
    ) -> LaunchResult: ...
```

Implementations can be Claude Code subagents, subprocess calls, API
requests, or dry-run mocks.

---

## 11. Configuration

### Engine Configuration

The engine configuration is primarily structural -- determined by
constructor arguments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `team_context_root` | `.claude/team-context` | Root directory for all state files |
| `bus` | None | EventBus for domain event publishing |
| `task_id` | None | Namespaced execution (None = legacy flat file) |
| `storage` | None | SQLite backend (None = file-only mode) |
| `knowledge_resolver` | None | For runtime gap auto-resolution |

### Planner Configuration

The planner's behavior is driven by data sources, not configuration
flags:

| Data Source | Effect on Plans |
|-------------|----------------|
| `PatternLearner` (usage data) | Phase templates, agent selection |
| `PerformanceScorer` (score data) | Score warnings for unhealthy agents |
| `BudgetTuner` (usage data) | Budget tier recommendation |
| `DataClassifier` | Risk floor, guardrail preset |
| `PolicyEngine` | Agent assignment policy violations |
| `RetrospectiveEngine` | Agent drop/prefer recommendations |
| `KnowledgeRegistry` | Per-step knowledge attachments |

### Launcher Configuration

`ClaudeCodeConfig` is the primary runtime configuration point:

| Field | Default | Description |
|-------|---------|-------------|
| `claude_path` | `"claude"` | Path to claude binary |
| `default_timeout_seconds` | 600.0 | Timeout when no model-specific override |
| `model_timeouts` | opus:900, sonnet:600, haiku:300 | Per-model timeouts |
| `max_retries` | 3 | Retry attempts on rate-limit |
| `base_retry_delay` | 5.0 | Base delay for exponential backoff |
| `max_outcome_length` | 4000 | Max chars kept from agent outcome |
| `prompt_file_threshold` | 131072 | Bytes above which prompt goes via stdin |
| `env_passthrough` | ANTHROPIC_API_KEY, etc. | Environment variables forwarded |

---

## 12. Error Handling

### Step Failures

When a step fails (`status="failed"`):
- The `StepResult` is recorded with the error message.
- `_determine_action()` detects the failed step and returns
  `ActionType.FAILED`.
- The execution status becomes `"failed"`.
- A `step.failed` event is published.

### Gate Failures

When a gate fails:
- `record_gate_result(passed=False)` sets `status = "failed"`.
- A `gate.failed` event is published.
- Subsequent `next_action()` calls return `ActionType.FAILED`.

### Rate-Limit Retry

`ClaudeCodeLauncher` implements automatic retry with exponential
backoff:
- Detects rate-limit responses by checking for "rate limit" or "429"
  in error text.
- Retries up to `max_retries` times (default 3).
- Delay: `base_retry_delay * 2^(attempt-1)` seconds (5s, 10s, 20s).

### Timeout Handling

- Per-model timeouts are applied via `asyncio.wait_for()`.
- On timeout: the subprocess is killed, and a `LaunchResult` with
  `status="failed"` and `error="Agent timed out after Ns"` is returned.

### Crash Recovery

The entire system is designed for crash resilience:

1. **Atomic writes**: `StatePersistence` writes to `.json.tmp` then
   renames, preventing partial-write corruption.
2. **State on every mutation**: the engine saves state after every
   `record_*` call.
3. **Resume**: `baton execute resume` loads state from disk and
   continues from where execution left off.
4. **Dispatched recovery**: `recover_dispatched_steps()` clears stale
   dispatched markers after a daemon crash so steps are re-dispatched.
5. **PID file locking**: `WorkerSupervisor` uses `flock()` to prevent
   double-start. The lock is released automatically when the process
   exits.

### Approval Rejection

When `record_approval_result(result="reject")`:
- Status becomes `"failed"`.
- Execution stops. The caller must decide whether to amend and retry.

### Approval With Feedback

When `record_approval_result(result="approve-with-feedback")`:
- A remediation phase is inserted after the current phase via
  `_amend_from_feedback()`.
- The phase contains a single step assigned to the first agent in the
  current phase, with a task description derived from the feedback.
- Execution continues into the new remediation phase.

### Knowledge Gap Escalation

Gaps are handled without blocking execution when possible:
- **auto-resolve**: re-dispatch with resolved knowledge (no human
  intervention).
- **best-effort**: log and continue (LOW risk, low intervention).
- **queue-for-gate**: surface at next human approval gate (all other
  cases).

---

## 13. Team Collaboration

Agent Baton supports structured multi-agent collaboration within plan
steps via **team steps**.  A team step assigns multiple agents to a
single plan step, each with a defined role and dependency ordering.

### Team Step Structure

A `PlanStep` with a non-empty `team` list is a team step.  Each
`TeamMember` has a `member_id` (e.g. `"1.1.a"`), `role` (lead,
implementer, reviewer), and optional `depends_on` list for intra-step
sequencing.

### Synthesis Strategies

When all team members complete, their outputs are merged via the
step's `SynthesisSpec`:

| Strategy | Behavior |
|----------|----------|
| `concatenate` (default) | Join outcomes with `"; "`, collect all files |
| `merge_files` | Same as concatenate but deduplicate `files_changed` |
| `agent_synthesis` | Mark for synthesis agent dispatch (future) |

### Conflict Detection and Escalation

The engine detects conflicts when two or more team members modify the
same file.  The `conflict_handling` field on `SynthesisSpec` controls
the response:

| Mode | Behavior |
|------|----------|
| `auto_merge` (default) | Complete the step; record conflict in retrospective |
| `escalate` | Pause step, set state to `approval_pending`, surface both positions to human |
| `fail` | Fail the step if conflict detected |

Conflicts are recorded as `ConflictRecord` instances in the
retrospective, preserving each agent's position and evidence.

### Team Composition Tracking

After execution, `TeamCompositionRecord` entries are collected from
team steps and written to retrospective JSON sidecars.  These feed:

- `PatternLearner.analyze_team_patterns()` -- identifies effective
  team compositions across projects
- `PerformanceScorer.score_teams()` -- aggregates team scorecards
- `baton scores --teams` -- CLI report of team effectiveness
- `PatternLearner.get_team_cost_estimate()` -- historical token cost
  by team composition

### Selective MCP Pass-through

Team steps (and solo steps) can declare MCP server dependencies via
the `mcp_servers` field on `PlanStep`.  Only declared servers are
passed to the agent subprocess via `--mcp-config`, avoiding input
token bloat from unused MCP tool schemas.

Default: no MCP servers inherited.  This is a deliberate design choice
to prevent context window waste from tool schemas agents don't need.

## 14. Post-Execution: Commit Consolidation

### CommitConsolidator

**Source:** `agent_baton/core/engine/consolidator.py` (lazily imported)

After execution completes, the `CommitConsolidator` consolidates agent
commits into a clean, mergeable history via cherry-pick rebase.

**Process:**

1. Topological sort of agent commits by dependency ordering.
2. Cherry-pick each commit onto the feature branch in dependency order.
3. Compute per-file diff stats and agent attribution after each pick.
4. Detect conflicts and record them in the `ConsolidationResult`.

**`ConsolidationResult`** (`models/execution.py`):

| Field | Type | Description |
|-------|------|-------------|
| `status` | str | `success`, `partial`, `failed` |
| `attributions` | list[ChangelistFile] | Per-file diff stats with agent attribution |
| `conflicts` | list[str] | Files with merge conflicts |
| `error` | str | Error message if failed |

**`ChangelistFile`** (`models/execution.py`):

| Field | Type | Description |
|-------|------|-------------|
| `path` | str | File path relative to repo root |
| `agent_name` | str | Agent that modified the file |
| `insertions` | int | Lines added |
| `deletions` | int | Lines removed |
| `status` | str | added, modified, deleted |

The `ConsolidationResult` is stored on `ExecutionState.consolidation_result`
and consumed by the PMO changelist/merge/PR API endpoints.

### PlanReviewer

**Source:** `agent_baton/core/engine/plan_reviewer.py`

Post-generation plan quality review, wired into
`IntelligentPlanner.create_plan()` at step 12c.5. Two review strategies:

1. **Haiku review** -- cheap LLM call (~2000 tokens) analyzing plan
   structure and returning JSON recommendations. Used for medium+
   complexity plans when the Anthropic SDK is available.
2. **Heuristic review** -- deterministic fallback using file-path
   clustering and task-description analysis. Catches the most common
   case: single-step work phases spanning 4+ files across 3+ directories.

Recommendations include: step splitting, missing dependency edges, scope
imbalance warnings, and same-agent team suggestions for coupled concerns.

---

## 15. Async Sessions and Resource Management

### Session Persistence

`SessionState` wraps `ExecutionState` with multi-day workflow metadata:

- **Participants** -- agents and humans who contributed, with
  contribution counts
- **Checkpoints** -- snapshot points for safe resumption after daemon
  restart or manual pause
- **Lifecycle** -- active → paused → resumed → completed

### Multi-party Async Contributions

`ContributionRequest` extends the decision protocol for open-ended
multi-party input.  Unlike `DecisionRequest` (binary choice), a
contribution stays open until all named contributors respond.

API: `DecisionManager.request_contribution()`, `.contribute()`,
`.get_contribution()`, `.pending_contributions()`.

Events: `contribution.requested` and `contribution.ready` are
published via `EventBus` when all inputs arrive.

### Resource Constraints

`ResourceLimits` on `MachinePlan` governs execution resource usage:

| Limit | Default | Enforced By |
|-------|---------|-------------|
| `max_concurrent_agents` | 8 | `StepScheduler` semaphore |
| `max_concurrent_executions` | 3 | `WorkerSupervisor` |
| `max_tokens_per_minute` | 0 (unlimited) | Not yet enforced |

Token budget warnings: `_check_token_budget()` compares cumulative
`estimated_tokens` against tier thresholds (lean=50k, standard=500k,
full=2M).  Warnings are logged and appended to step `deviations` for
retrospective tracking.

## 16. Intelligence and Cost Prediction

### Team Cost Estimation

The planner consults `PatternLearner.get_team_cost_estimate()` for
team steps, surfacing historical token costs:

- In `explain_plan()` -- "Team Cost Estimates" section with per-step
  and total estimates
- In `shared_context` -- budget percentage so agents are aware of
  resource constraints

### Budget-Aware Planning

Team cost estimates are compared against the plan's `budget_tier`
threshold.  The shared context includes the cost as a percentage of
budget (e.g. "~45,000 tokens (90% of lean budget)") so dispatched
agents can self-regulate scope.

---

## Appendix: Data Models

### Key Models (agent_baton/models/execution.py)

| Class | Purpose |
|-------|---------|
| `MachinePlan` | Complete execution plan (task, phases, steps, gates) |
| `PlanPhase` | A phase with steps and an optional gate |
| `PlanStep` | A single agent assignment |
| `PlanGate` | A QA gate definition |
| `TeamMember` | A member of a team step |
| `PlanAmendment` | Audit record of a plan modification |
| `ExecutionState` | Persistent state of a running execution |
| `StepResult` | Outcome of a step execution |
| `TeamStepResult` | Outcome of a team member's work |
| `GateResult` | Outcome of a gate check |
| `ApprovalResult` | Outcome of a human approval |
| `ExecutionAction` | Instruction from engine to driving session |
| `ActionType` | Enum: DISPATCH, GATE, COMPLETE, FAILED, WAIT, APPROVAL |
| `SynthesisSpec` | Team output merge strategy (concatenate/merge_files/agent_synthesis) |
| `ConsolidationResult` | Outcome of cherry-pick rebase (status, attributions, conflicts) |
| `ChangelistFile` | Per-file diff stats with agent attribution |

### Team and Collaboration Models

| Class | Module | Purpose |
|-------|--------|---------|
| `TeamCompositionRecord` | `models/retrospective.py` | Records which agents worked as a team and outcome |
| `ConflictRecord` | `models/retrospective.py` | Structured disagreement between agents |
| `TeamPattern` | `models/pattern.py` | Recurring team composition pattern from usage logs |
| `TeamScorecard` | `core/improve/scoring.py` | Performance scorecard for a team composition |
| `SessionState` | `models/session.py` | Multi-day session wrapper with checkpoints |
| `SessionCheckpoint` | `models/session.py` | Snapshot point for safe resumption |
| `SessionParticipant` | `models/session.py` | Agent or human participant in a session |
| `ContributionRequest` | `models/decision.py` | Multi-party async input collection |
| `ResourceLimits` | `models/parallel.py` | Concurrency and token budget constraints |

### Key Enums (agent_baton/models/enums.py)

| Enum | Values |
|------|--------|
| `RiskLevel` | LOW, MEDIUM, HIGH, CRITICAL |
| `BudgetTier` | Lean (1-2), Standard (3-5), Full (6-8) |
| `GitStrategy` | Commit-per-agent, Branch-per-agent, None |
| `ExecutionMode` | Parallel Independent, Sequential Pipeline, Phased Delivery |
| `GateOutcome` | PASS, PASS WITH NOTES, FAIL |

### Decision Models (agent_baton/models/decision.py)

| Class | Purpose |
|-------|---------|
| `DecisionRequest` | Human decision request (pending/resolved/expired) |
| `DecisionResolution` | Resolution of a decision (option + rationale) |
| `ContributionRequest` | Multi-party async input with contributor tracking |
