---
name: architecture
description: Package layout, key classes, data flow, file resolution precedence, and design principles for the agent-baton Python package
tags: [architecture, package-layout, design, data-flow, dependencies]
priority: high
---

# Agent Baton — Architecture

## Overview

Agent Baton is a multi-agent orchestration engine for Claude Code. The
Python package (`agent_baton`) implements a state machine that plans,
sequences, and tracks subagent execution. Claude reads the orchestrator
agent definition, calls the `baton` CLI, and parses structured output to
drive execution. The package also provides a FastAPI-based PMO API and a
React/Vite frontend for project management.

## Package Layout

```
agent_baton/
  models/             Foundation layer (18+ modules, no internal deps)
  |  execution.py     MachinePlan, PlanPhase, PlanStep, PlanGate,
  |                   ExecutionState, StepResult, GateResult,
  |                   ExecutionAction, ActionType, StepStatus
  |  enums.py         RiskLevel, BudgetTier, ExecutionMode, GitStrategy,
  |                   AgentCategory, FailureClass, GateOutcome
  |  agent.py         AgentDefinition (parsed from .md frontmatter)
  |  pmo.py           PmoProject, PmoCard, InterviewQuestion, InterviewAnswer
  |  knowledge.py     KnowledgePack, KnowledgeDocument, KnowledgeGapSignal
  |  events.py        Event model types
  |  decision.py      DecisionRecord
  |  parallel.py      ExecutionRecord (async/parallel dispatch)
  |  plan.py          MissionLogEntry
  |  ... (10+ other modules)
  core/               Business logic (11 sub-packages)
  |  orchestration/   AgentRegistry, AgentRouter, ContextManager,
  |  |                KnowledgeRegistry
  |  engine/          ExecutionEngine, IntelligentPlanner, PromptDispatcher,
  |  |                GateRunner, StatePersistence, ExecutionDriver protocol,
  |  |                KnowledgeResolver, KnowledgeGap handler
  |  runtime/         TaskWorker, WorkerSupervisor, StepScheduler,
  |  |                AgentLauncher, DecisionManager, ExecutionContext factory
  |  events/          EventBus, EventPersistence, domain events, projections
  |  pmo/             PmoStore, PmoScanner, ForgeSession (interview/regen)
  |  storage/         CentralStore, SyncEngine, PmoSqliteStore,
  |  |                ExternalSourceAdapter, AdoAdapter, AdapterRegistry
  |  govern/          DataClassifier, ComplianceReportGenerator, PolicyEngine,
  |  |                SpecValidator, AgentValidator, EscalationManager
  |  observe/         TraceRecorder, UsageLogger, RetrospectiveEngine,
  |  |                DashboardGenerator, AgentTelemetry, ContextProfiler
  |  improve/         PerformanceScorer, PromptEvolutionEngine, AgentVersionControl
  |  learn/           PatternLearner, BudgetTuner
  |  distribute/      PackageBuilder, RegistryClient
  |     experimental/ AsyncDispatcher, IncidentManager, ProjectTransfer
  api/                FastAPI application
  |  server.py        create_app() factory
  |  deps.py          Dependency injection (get_forge_session, get_pmo_store)
  |  routes/pmo.py    PMO endpoints (board, forge, signals, ADO search)
  |  models/          Pydantic request/response models
  cli/
     main.py          auto-discovers commands from commands/ subdirectories
     commands/
       execution/     execute.py, plan_cmd.py, status.py, daemon.py
       observe/       dashboard.py, trace.py, usage.py, telemetry.py
       govern/        classify.py, compliance.py, policy.py, validate.py
       improve/       scores.py, evolve.py, patterns.py, budget.py
       distribute/    package.py, publish.py, pull.py, install.py
       agents/        agents.py, route.py, events.py
       pmo_cmd.py     pmo serve, pmo status, pmo add, pmo health
       sync_cmd.py    baton sync, baton sync --all
       query_cmd.py   baton query (SQL against central.db)
```

## Key Classes

| Class | Module | Responsibility |
|-------|--------|----------------|
| `ExecutionEngine` | core.engine.executor | State machine: start → dispatch → record → gate → complete |
| `IntelligentPlanner` | core.engine.planner | Create MachinePlan from task description + stack detection |
| `StatePersistence` | core.engine.persistence | Atomic read/write of ExecutionState; task-scoped directories |
| `KnowledgeResolver` | core.engine.knowledge_resolver | Match knowledge packs to agent steps at plan time |
| `AgentRegistry` | core.orchestration.registry | Load agents from `~/.claude/agents/` + `.claude/agents/` |
| `AgentRouter` | core.orchestration.router | Detect project stack → match to agent flavors |
| `ContextManager` | core.orchestration.context | Read/write team-context files (plan.json, mission-log) |
| `KnowledgeRegistry` | core.orchestration.knowledge_registry | Load and index knowledge packs from `.claude/knowledge/` |
| `ForgeSession` | core.pmo.forge | Plan generation, interview questions, regeneration |
| `PmoStore` | core.pmo.store | Project/card/signal CRUD for PMO board |
| `SyncEngine` | core.storage.sync | Watermark-based incremental sync to central.db |
| `CentralStore` | core.storage.central | Read-only query interface for federated central.db |
| `EventBus` | core.events.bus | Pub/sub for domain events (step, phase, task lifecycle) |
| `TaskWorker` | core.runtime.worker | Async execution wrapper; owns step dispatch events |

## Dependency Order

Strict layered imports, no cycles:

```
models → events, observe, govern, learn, improve, distribute, orchestration
      → engine → runtime → CLI
```

| Category | Sub-packages | Role |
|----------|-------------|------|
| Execution core | `engine`, `runtime`, `orchestration`, `events` | Primary path. Always active. |
| Data layer | `storage`, `pmo` | Persistence, sync, PMO board/forge |
| Observability | `observe` | Trace, usage, dashboard, telemetry |
| Governance | `govern` | Classification, compliance, policy |
| Improvement | `improve`, `learn` | Scoring, evolution, pattern learning |
| Distribution | `distribute` | Packaging, registry, cross-project transfer |

## Data Flow

```
Agent .md files on disk
        │
        ▼
  AgentRegistry.load()        ← parses frontmatter + markdown body
        │
        ▼
  AgentRouter.route()         ← reads pyproject.toml/package.json → picks flavors
        │
        ▼
  IntelligentPlanner          ← creates MachinePlan with phases, steps, gates
        │                       KnowledgeResolver attaches knowledge to each step
        ▼
  ExecutionEngine.start()     ← initializes state, sets active task marker
        │
        ▼
  ExecutionEngine.next_action() → DISPATCH | GATE | APPROVAL | COMPLETE
        │
        ▼
  StatePersistence.save()     ← atomic write to executions/<task-id>/
        │
        ▼
  SyncEngine.sync()           ← incremental sync to ~/.baton/central.db
```

## File Resolution

Agents, references, and knowledge packs load from two locations:

| Location | Scope | Priority |
|----------|-------|----------|
| `.claude/agents/` | Project-specific | Higher (wins on name collision) |
| `~/.claude/agents/` | Global (all projects) | Lower (fallback) |

Same pattern for `references/` and `knowledge/`.

## Task-ID Resolution (Concurrent Execution)

```
--task-id flag → BATON_TASK_ID env var → active-task-id.txt → None
```

Each execution gets its own directory: `executions/<task-id>/`.

## Dependencies

- **Runtime**: `pyyaml>=6.0`, `fastapi`, `uvicorn`, `pydantic`
- **Dev**: `pytest`, `pytest-cov`
- **Python**: 3.10+
- **Frontend**: React 18, Vite, TypeScript (in `pmo-ui/`)

## Design Principles

1. **Dataclasses for models, Pydantic for API** — internal models use dataclasses with hand-written `to_dict()`/`from_dict()`; API layer uses Pydantic
2. **pathlib.Path everywhere** — no string path manipulation
3. **Canonical sub-package imports** — `from agent_baton.core.engine.executor import ExecutionEngine`, no shims
4. **ExecutionDriver protocol** — runtime depends on a Protocol, not the concrete engine
5. **Event-driven observability** — EventBus pub/sub, not direct coupling between engine and observers
6. **CLI output is the contract** — `_print_action()` output format is the interface Claude parses; treat as public API