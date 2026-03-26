# Agent Baton

**Multi-agent orchestration for Claude Code.**

Describe a complex task in plain language. Agent Baton plans it, routes it
to the right specialist agents, enforces QA gates between phases, and
delivers tested, reviewed code -- all inside your normal Claude Code
session. No external services. No API keys beyond Claude. Everything runs
locally.

## Quick Start

```bash
# Install
scripts/install.sh    # Linux/macOS
# or
scripts/install.ps1   # Windows (no admin required)

# Plan a task
baton plan "Add request logging middleware" --save --explain

# Execute it
baton execute start
baton execute next     # get next action
# ... dispatch agents, record results ...
baton execute complete
```

See [docs/examples/first-run.md](docs/examples/first-run.md) for a complete walkthrough.

## What You Get

```
You:  "Use the orchestrator to add input validation to the API
       with tests and security review"

Baton: Plans 3 phases (implement, test, review)
       Dispatches backend-engineer, test-engineer, security-reviewer
       Runs pytest gate between phases
       Commits each agent's work separately
       Writes trace, usage log, and retrospective
```

---

## Table of Contents

- [Features](#features)
- [Install](#install)
- [Usage](#usage)
- [Architecture](#architecture)
- [Agents](#agents)
- [References](#references)
- [CLI Reference](#cli-reference)
- [REST API](#rest-api)
- [PMO UI](#pmo-ui)
- [Project Structure](#project-structure)
- [For Developers](#for-developers)
- [Project Status](#project-status)

---

## Features

### Orchestration Engine

The core execution engine plans, sequences, and tracks multi-agent tasks
through a deterministic state machine. It handles:

- **Intelligent planning** -- auto-detects your project stack, selects the
  right agents, classifies risk, assigns budget tiers, and sequences phases
  with dependency awareness
- **Execution loop** -- drives DISPATCH / GATE / APPROVAL / COMPLETE
  actions with full state persistence and crash recovery
  (`baton execute resume`)
- **Concurrent execution** -- run multiple plans in parallel across
  terminals, each bound by `BATON_TASK_ID`
- **Plan amendments** -- add phases or steps mid-execution without
  restarting
- **Team steps** -- dispatch multiple agents to a single step for
  coordinated parallel work

### 19 Specialist Agents

Stack-aware agents with base and variant flavors. The orchestrator detects
your stack and routes automatically.

| Category | Agents |
|----------|--------|
| Backend | `backend-engineer`, `--python`, `--node` |
| Frontend | `frontend-engineer`, `--react`, `--dotnet` |
| Architecture | `architect` |
| Quality | `test-engineer`, `code-reviewer`, `security-reviewer` |
| Governance | `auditor` (independent veto power) |
| Data | `data-engineer`, `data-analyst`, `data-scientist` |
| Visualization | `visualization-expert` |
| Operations | `devops-engineer` |
| Domain | `subject-matter-expert` |
| Meta | `talent-builder` (creates new agent definitions) |
| Coordination | `orchestrator` |

The system self-expands: first time on a Go project, the `talent-builder`
creates `backend-engineer--go`. Next time, the routing table finds it.

### 15 Reference Procedures

Shared knowledge documents the orchestrator reads before every task. They
encode planning strategy, guardrail rules, communication protocols,
failure handling, and cost models. Agents get the knowledge they need
without duplicating it across context windows.

### Knowledge Delivery

Curated knowledge packs are resolved at plan time and injected into each
agent's delegation prompt. Agents receive only the knowledge relevant to
their task. Runtime knowledge gaps are auto-resolved via registry or
queued for human review. A feedback loop learns which agents need what
knowledge for which task types, improving future plans.

### Risk-Tiered Safety

Every task is classified by risk level:

- **LOW** -- guardrail presets applied inline, no subagent overhead
- **MEDIUM** -- auditor reviews the plan before execution
- **HIGH** -- auditor runs as independent subagent with veto authority;
  regulated domain rules require subject-matter-expert involvement

### Smart Forge

AI-driven task planning with interactive refinement. Propose tasks, get
decomposed execution plans, and refine them before committing. Integrates
with the PMO UI for visual plan review.

### Federated Sync and Cross-Project Queries

Execution data from all projects flows into a central read replica at
`~/.baton/central.db`. Query agent reliability, token costs, knowledge
gaps, and failure rates across every project with `baton cquery`. The PMO
reads from central so its status view is always current, even across
machines.

### External Source Adapters

Connect Azure DevOps (implemented), Jira, GitHub, or Linear as external
work-item sources. Items are pulled into `central.db` and linked to baton
plans. The adapter protocol (`ExternalSourceAdapter`) is extensible --
add new sources by implementing a single protocol class.

### Event System

A domain event bus carries structured signals (`task.started`,
`step.completed`, `gate.failed`, etc.) across the engine. Events are
persisted and projected into queryable views for debugging and analytics.

### REST API

A FastAPI server (`baton serve`) exposes the full engine over HTTP:
plans, executions, agents, events, decisions, observability, webhooks,
and PMO data. Bearer token auth and CORS are built in. Requires
`pip install agent-baton[api]`.

### Pattern Learning and Budget Tuning

The engine learns from past executions:

- **Pattern learner** -- identifies recurring agent combinations,
  sequencing strategies, and failure modes across task types
- **Budget tuner** -- recommends budget tier adjustments based on
  actual token usage vs. predictions
- **Prompt evolution** -- proposes prompt improvements for
  underperforming agents
- **Anomaly detection** -- surfaces system anomalies and trigger
  readiness

### Telemetry and Observability

Full execution visibility without leaving the terminal:

- Execution traces with step-level timing
- Token usage reports and cost breakdowns
- Agent performance scorecards with trend analysis
- Context efficiency profiling
- Auto-generated retrospectives
- Compliance reports for audited tasks
- Artifact cleanup with configurable retention

---

## Install

### Quick Install (agents + references only)

```bash
cd /path/to/agent-baton
scripts/install.sh
```

Choose user-level (`~/.claude/`) for all projects, or project-level
(`.claude/`) for one project. The script installs 19 agents, 15
references, a CLAUDE.md template, settings.json with hooks, and
initializes `~/.baton/` for the central database.

### Python Package (full engine)

```bash
pip install -e ".[dev]"       # Core engine + test deps
pip install -e ".[api]"       # Add REST API server
pip install -e ".[dev,api]"   # Everything
```

Requires Python 3.10+. The only runtime dependency is PyYAML.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

### Verify

```bash
# In Claude Code
/agents                       # Should list ~19 agents

# CLI verification
baton agents                  # List agents from Python registry
baton validate agents/        # Validate all agent definitions
```

See [QUICKSTART.md](QUICKSTART.md) for a detailed walkthrough.

---

## Usage

### Orchestrated Tasks (recommended for complex work)

Describe the task naturally. Say "use the orchestrator" explicitly for
your first few runs:

```
Use the orchestrator to build a health check API with tests and documentation
```

The orchestrator will:
1. Detect your project stack
2. Create a plan via `baton plan` with phases, agents, and gates
3. Present the plan for your approval
4. Dispatch specialist agents (in parallel when independent)
5. Run build/test/lint checks between phases
6. Commit each agent's work separately
7. Write traces and retrospectives

### Engine-Driven Workflow (explicit control)

```bash
# Plan
baton plan "Add input validation to the API" --save --explain

# Optional: attach knowledge and set escalation threshold
baton plan "task" --save \
    --knowledge path/to/doc.md \
    --knowledge-pack compliance \
    --intervention medium

# Execute
baton execute start
baton execute next              # Single next action
baton execute next --all        # All parallel-dispatchable actions
baton execute record --step-id 1.1 --agent backend-engineer --status complete
baton execute gate --phase-id 2 --result pass
baton execute complete

# Concurrent execution
export BATON_TASK_ID=<task-id>  # Bind terminal to specific execution
baton execute list              # List all running executions
baton execute switch <task-id>  # Switch active execution

# Crash recovery
baton execute resume            # Picks up from saved state
```

### Direct Agent Invocation (simple tasks)

```
Use the data-analyst to investigate our fleet utilization trends
Use the security-reviewer to audit our authentication flow
Use the test-engineer to add unit tests for the payment module
```

---

## Architecture

```
                    ┌────────────────────────────────────────────────────────┐
                    │                    ORCHESTRATOR                        │
                    │                                                        │
                    │  Reads 15 reference procedures inline:                 │
                    │  Decision framework, research procedures,              │
                    │  agent routing, guardrail presets, cost models,         │
                    │  task sequencing, failure handling, git strategy ...    │
                    └───────────────────────┬────────────────────────────────┘
                                            │
                         baton plan ────────┤──────── baton execute
                                            │
              ┌─────────────────────────────┼──────────────────────────────┐
              │                             │                              │
              ▼                             ▼                              ▼
    ┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
    │     AUDITOR      │          │   SPECIALIST      │          │   TALENT         │
    │  (independent    │          │   AGENTS           │          │   BUILDER        │
    │   veto power)    │          │                    │          │  (creates new    │
    └──────────────────┘          │  Backend           │          │   agents)        │
                                  │  Frontend          │          └──────────────────┘
                                  │  Architect          │
                                  │  Test Engineer      │
                                  │  DevOps             │
                                  │  Data Eng/Sci/Anal  │
                                  │  Security Reviewer  │
                                  │  Code Reviewer      │
                                  │  Visualization      │
                                  │  Subject Matter     │
                                  └──────────────────────┘

    ┌──────────────────────────────────────────────────────────────────────┐
    │                     EXECUTION ENGINE (Python)                        │
    │                                                                      │
    │  Planner ──▶ Executor ──▶ Dispatcher ──▶ Gates ──▶ Persistence      │
    │                                                                      │
    │  Events  ──▶ Telemetry ──▶ Traces ──▶ Retrospectives               │
    │                                                                      │
    │  Pattern Learner ──▶ Budget Tuner ──▶ Prompt Evolution              │
    │                                                                      │
    │  Federated Sync ──▶ Central DB ──▶ Cross-Project Queries            │
    │                                                                      │
    │  PMO Store ──▶ Smart Forge ──▶ REST API ──▶ PMO UI                  │
    └──────────────────────────────────────────────────────────────────────┘
```

### Interaction Chain

```
Human  <-->  Claude Code  <-->  baton CLI  <-->  Python engine
        (natural language)  (structured text)  (state machine)
```

Claude never imports the Python package. It reads structured text output
from `baton` commands and acts on it. The CLI output format is the only
contract between Claude and the engine.

### Design Principles

**Pay for context only when you need isolation.** Every subagent costs a
full context window, startup latency, and information loss when
summarizing results. Agent Baton minimizes this:

- Research, routing, and communication run inline in the orchestrator
- Specialists get their own context only for substantial independent work
- Shared knowledge lives in reference documents, not duplicated across
  context windows

### Agent Flavoring

Specialists have base and stack-specific variants. The orchestrator
detects your stack and routes automatically:

```
backend-engineer.md           <- Any backend stack
backend-engineer--node.md     <- Node.js / TypeScript
backend-engineer--python.md   <- Python / FastAPI / Django

frontend-engineer.md          <- Any frontend stack
frontend-engineer--react.md   <- React / Next.js
frontend-engineer--dotnet.md  <- Blazor / .NET
```

---

## Agents

| Agent | Role |
|-------|------|
| `orchestrator` | Plans and coordinates multi-step tasks |
| `backend-engineer` | Server-side implementation (+ `--node`, `--python` flavors) |
| `frontend-engineer` | Client-side UI (+ `--react`, `--dotnet` flavors) |
| `architect` | System design and technical decisions |
| `test-engineer` | Write and organize tests |
| `code-reviewer` | Code quality review |
| `security-reviewer` | Security audit (OWASP, auth, secrets) |
| `auditor` | Safety, compliance, governance (independent veto power) |
| `devops-engineer` | Infrastructure, CI/CD, Docker |
| `data-engineer` | Databases, ETL, data modeling |
| `data-analyst` | BI, reporting, SQL, KPI definition |
| `data-scientist` | ML, statistical analysis, modeling |
| `visualization-expert` | Charts, dashboards, visual storytelling |
| `subject-matter-expert` | Domain-specific business operations |
| `talent-builder` | Creates new agent definitions |

Agent definitions are markdown files with YAML frontmatter. They live in
`agents/` and are copied to `.claude/agents/` on install.

---

## References

| Document | Purpose |
|----------|---------|
| `decision-framework.md` | When to use subagent vs. skill vs. reference doc |
| `research-procedures.md` | 4 research modes the orchestrator runs inline |
| `adaptive-execution.md` | Engagement level classification (Level 1/2/3) |
| `agent-routing.md` | Stack detection and agent flavor matching |
| `baton-engine.md` | Full CLI reference for the execution engine |
| `baton-patterns.md` | Execution plan design patterns catalog |
| `guardrail-presets.md` | Risk triage and standard guardrail configs |
| `comms-protocols.md` | Delegation prompts, handoff briefs, logging |
| `task-sequencing.md` | Phase ordering and parallel dispatch rules |
| `git-strategy.md` | Commit conventions per risk level |
| `cost-budget.md` | Token cost models and budget tiers |
| `failure-handling.md` | Recovery when agents fail or get stuck |
| `hooks-enforcement.md` | Mechanical guardrails via Claude Code hooks |
| `doc-generation.md` | Document generation pipeline |
| `knowledge-architecture.md` | Knowledge pack structure and conventions |

References are shared knowledge read by any agent that needs them. They
live in `references/` and are copied to `.claude/references/` on install.

---

## CLI Reference

The `baton` CLI provides 45+ commands organized into six groups. Install
with `pip install -e ".[dev]"`.

### Execution

| Command | Description |
|---------|-------------|
| `baton plan` | Create a data-driven execution plan |
| `baton execute start` | Start execution from a saved plan |
| `baton execute next [--all]` | Get next action(s) to perform |
| `baton execute record` | Record a step completion |
| `baton execute dispatched` | Mark a step as in-flight |
| `baton execute gate` | Record a QA gate result |
| `baton execute approve` | Record a human approval decision |
| `baton execute amend` | Add phases or steps mid-execution |
| `baton execute team-record` | Record team member completions |
| `baton execute complete` | Finalize execution (writes traces, usage, retro) |
| `baton execute status` | Show current execution state |
| `baton execute resume` | Resume after crash or interruption |
| `baton execute list` | List all executions |
| `baton execute switch` | Switch active execution |
| `baton status` | Show team-context file status |
| `baton daemon start/stop` | Background execution management |
| `baton async` | Dispatch and track asynchronous tasks |
| `baton decide` | Manage human decision requests |

### Observability

| Command | Description |
|---------|-------------|
| `baton usage` | Token usage statistics |
| `baton dashboard [--write]` | Generate usage dashboard |
| `baton trace` | Execution traces |
| `baton retro` | Task retrospectives |
| `baton telemetry` | Agent telemetry events |
| `baton context-profile` | Agent context efficiency profiles |
| `baton context` | Situational awareness (current task, briefings, gaps) |
| `baton query` | Typed and ad-hoc SQL queries against baton.db |
| `baton cleanup` | Archive old execution artifacts |
| `baton migrate-storage` | Migrate JSON flat files to SQLite |

### Governance

| Command | Description |
|---------|-------------|
| `baton classify` | Classify task sensitivity and select guardrail preset |
| `baton compliance` | Show compliance reports |
| `baton policy` | List or evaluate guardrail policy presets |
| `baton escalations` | Show or resolve agent escalations |
| `baton validate` | Validate agent definitions |
| `baton spec-check` | Validate agent output against a spec |
| `baton detect` | Detect project stack |

### Improvement

| Command | Description |
|---------|-------------|
| `baton scores` | Agent performance scorecards |
| `baton evolve` | Propose prompt improvements for underperforming agents |
| `baton patterns` | Show or refresh learned orchestration patterns |
| `baton budget` | Budget tier recommendations based on usage |
| `baton changelog` | Agent changelog and backup management |
| `baton anomalies` | Detect and display system anomalies |
| `baton experiment` | Manage improvement experiments |
| `baton improve` | Run the full improvement loop |

### Distribution

| Command | Description |
|---------|-------------|
| `baton package` | Create or install agent-baton package archives |
| `baton publish` | Publish a package to a local registry |
| `baton pull` | Pull a package from a registry |
| `baton verify-package` | Verify a package archive |
| `baton install` | Install agents and references to a project |
| `baton transfer` | Transfer agents/knowledge/references between projects |

### Agents and Events

| Command | Description |
|---------|-------------|
| `baton agents` | List available agents |
| `baton route [ROLES]` | Route roles to agent flavors |
| `baton events` | Query the event log for a task |
| `baton incident` | Manage incident response workflows |

### Cross-Project

| Command | Description |
|---------|-------------|
| `baton sync [--all]` | Sync project data to `~/.baton/central.db` |
| `baton cquery` | Cross-project SQL queries against central.db |
| `baton source add/list/sync/remove/map` | Manage external source connections |
| `baton pmo serve/status/add/health` | Portfolio management overlay |
| `baton serve` | Start the HTTP API server |

---

## REST API

The optional REST API (`pip install agent-baton[api]`) exposes the full
engine over HTTP on port 8741:

```bash
baton serve                     # Start on localhost:8741
baton serve --port 9000         # Custom port
baton serve --token SECRET      # Enable bearer token auth
```

API route groups:

| Prefix | Routes |
|--------|--------|
| `/api/v1/health` | Health check |
| `/api/v1/plans` | Plan creation and retrieval |
| `/api/v1/executions` | Execution lifecycle |
| `/api/v1/agents` | Agent registry and routing |
| `/api/v1/observe` | Traces, usage, dashboards |
| `/api/v1/decisions` | Human decision requests |
| `/api/v1/events` | Event log queries, SSE streaming |
| `/api/v1/webhooks` | Webhook registration and delivery |
| `/api/v1/pmo` | PMO projects, board, health |

Interactive API docs available at `http://localhost:8741/docs` when the
server is running.

---

## PMO UI

A React/Vite frontend for portfolio management served at `/pmo/` by the
API server. Shows a Kanban board of projects, program health, and task
status across all your baton-managed work.

```bash
# Start the API server (serves PMO UI at /pmo/)
baton pmo serve

# Terminal-only Kanban summary
baton pmo status

# Register a project
baton pmo add --id myproject --name "My Project" --path /path/to/project --program core

# Program health overview
baton pmo health
```

---

## Project Structure

```
agents/            <- 19 agent definitions (markdown + YAML frontmatter)
references/        <- 15 reference procedures (shared knowledge)
templates/         <- CLAUDE.md + settings.json for target projects
scripts/           <- Install scripts (Linux + Windows)
docs/              <- Architecture documentation
agent_baton/       <- Python package (orchestration engine)
  models/          <- Data models (18 modules)
  core/            <- Business logic (10 sub-packages)
    engine/        <- Planner, executor, dispatcher, gates, persistence
    orchestration/ <- Agent registry, router, context manager
    pmo/           <- PMO store, scanner, Smart Forge
    storage/       <- Central DB, federated sync, external adapters
    govern/        <- Classification, compliance, policy, escalation
    observe/       <- Tracing, usage, dashboard, telemetry, retrospective
    improve/       <- Scoring, evolution, VCS, experiments
    learn/         <- Pattern learner, budget tuner
    distribute/    <- Packaging, sharing, registry client
    events/        <- Event bus, domain events, persistence, projections
    runtime/       <- Async worker, supervisor, launcher, decisions
  api/             <- FastAPI REST API server
    routes/        <- 9 route modules (plans, executions, agents, etc.)
    middleware/    <- CORS, bearer token auth
    webhooks/      <- Webhook delivery
  cli/             <- CLI interface (45+ commands via `baton`)
tests/             <- Test suite (~3907 tests, pytest)
pmo-ui/            <- React/Vite PMO frontend
```

---

## For Developers

See [CLAUDE.md](CLAUDE.md) for the full development guide, including:

- Repository structure and key rules
- Agent roster details
- Orchestrator usage patterns
- Documentation maintenance requirements

### Development Setup

```bash
git clone <repo-url>
cd agent-baton
pip install -e ".[dev]"
pytest                         # Run ~3907 tests
```

### Key Documentation

| Document | Contents |
|----------|----------|
| [`CLAUDE.md`](CLAUDE.md) | Development guide and conventions |
| [`docs/architecture.md`](docs/architecture.md) | Package layout, dependency graph, key contracts |
| [`docs/design-decisions.md`](docs/design-decisions.md) | ADR log -- why the architecture looks the way it does |
| [`docs/invariants.md`](docs/invariants.md) | Interface boundaries that must not change |
| [`QUICKSTART.md`](QUICKSTART.md) | Getting started guide for new users |

---

## Project Status

Agent Baton is in active development (v0.1.0). The orchestration engine,
all 19 agents, 15 references, knowledge delivery, PMO subsystem, REST
API, federated sync, external source adapters, event system, and the
improvement pipeline are implemented and tested.

- **Python**: 3.10+
- **Runtime dependency**: PyYAML only
- **Optional dependencies**: FastAPI + uvicorn (for REST API)
- **Test suite**: ~3907 tests (pytest)
- **External adapters**: Azure DevOps implemented; Jira, GitHub, Linear
  adapter protocol defined

---

## Tips

- **Say "use the orchestrator"** explicitly for your first few runs so
  Claude Code routes to the right agent.
- **3-5 specialists per task.** More than that and coordination overhead
  outweighs benefits.
- **Run plans in parallel.** Each `baton execute start` prints an
  `export BATON_TASK_ID=...` line. Run that in each terminal.
- **Watch for agent sprawl.** Periodically review the roster against the
  decision framework. Can any agents be downgraded to reference docs?
- **Crash recovery is automatic.** If a session dies mid-task, start a
  new session and run `baton execute resume`.

---

## License

See [LICENSE](LICENSE) for details.
