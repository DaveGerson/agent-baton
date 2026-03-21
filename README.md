# Agent Baton — Multi-Agent Orchestration for Claude Code

A production-grade multi-agent orchestration system with a Python execution
engine, distributable agent definitions, and a CLI toolkit. Provides
intelligent planning, phased execution with QA gates, crash recovery,
observability, and a learning pipeline that improves over time.

---

## Design Philosophy

> **Core principle: Pay for context only when you need isolation.**

Every subagent costs a full context window, startup latency, and information
loss when summarizing results back. These costs are justified only when the
work is substantial, independence matters, or the output would overwhelm the
caller's context.

See `references/decision-framework.md` for the full five-test decision flowchart.

---

## Architecture

```
agent-baton/
├── agent_baton/             ← Python package (orchestration engine)
│   ├── models/              ← Data models (14 modules)
│   │   ├── enums.py         ← RiskLevel, TrustLevel, BudgetTier, ExecutionMode
│   │   ├── agent.py         ← AgentDefinition
│   │   ├── plan.py          ← ExecutionPlan, Phase, AgentAssignment, QAGate
│   │   ├── execution.py     ← MachinePlan, ExecutionState, ExecutionAction
│   │   ├── usage.py         ← TaskUsageRecord, AgentUsageRecord
│   │   ├── retrospective.py ← Retrospective, AgentOutcome, KnowledgeGap
│   │   ├── pattern.py       ← OrchestrationPattern
│   │   ├── budget.py        ← BudgetRecommendation
│   │   ├── trace.py         ← TraceEvent, TaskTrace
│   │   ├── context_profile.py ← ContextProfile
│   │   ├── escalation.py    ← AgentEscalation
│   │   ├── registry.py      ← RegistryManifest, PackageMeta
│   │   └── reference.py     ← ReferenceDocument
│   │
│   ├── core/                ← Business logic (6 sub-packages)
│   │   ├── engine/          ← Execution engine (plan → dispatch → gate → complete)
│   │   │   ├── planner.py   ← IntelligentPlanner: data-driven plan creation
│   │   │   ├── executor.py  ← ExecutionEngine: state machine + crash recovery
│   │   │   ├── dispatcher.py← PromptDispatcher: delegation prompt builder
│   │   │   └── gates.py     ← GateRunner: QA gates between phases
│   │   ├── orchestration/   ← Context, plan, registry, router
│   │   ├── govern/          ← Classifier, compliance, escalation, policy, validation
│   │   ├── observe/         ← Trace, usage, dashboard, retrospective, telemetry
│   │   ├── improve/         ← Evolution, scoring, VCS
│   │   ├── learn/           ← Pattern learner, budget tuner
│   │   └── distribute/      ← Async dispatch, packaging, sharing, transfer
│   │
│   ├── cli/                 ← CLI interface (`baton` command, 31 commands)
│   └── utils/
│       └── frontmatter.py   ← YAML frontmatter parser
│
├── agents/                  ← Distributable agent definitions (19 agents)
├── references/              ← Distributable reference docs (12 docs)
├── templates/               ← CLAUDE.md + settings.json for installation
├── scripts/                 ← Install scripts (Linux + Windows)
├── tests/                   ← pytest suite (1730 tests)
│
├── .claude/                 ← Project-specific orchestration setup
│   ├── agents/              ← Agents tailored for developing agent-baton
│   ├── knowledge/           ← Knowledge packs (architecture, patterns, case studies)
│   ├── references/          ← Symlink → ../references/
│   └── settings.json        ← Project hooks
│
└── reference_files/         ← Input docs / roadmap
```

---

## Execution Engine

The execution engine is the core runtime. It turns a task description into
a phased plan, drives it through dispatch-gate-complete cycles, and learns
from every execution.

### Lifecycle

```
baton plan "Add input validation"   →  MachinePlan (phases, steps, gates)
       ↓
baton execute start                 →  First DISPATCH action
       ↓
  ┌─ DISPATCH → spawn agent → record result ─┐
  │  GATE     → run QA check → record result  │  (loop)
  └────────────────────────────────────────────┘
       ↓
baton execute complete              →  Trace + usage log + retrospective
```

### Key Capabilities

| Capability | Description |
|------------|-------------|
| Intelligent planning | Task type inference, risk assessment, agent routing, budget tiers |
| Phased execution | Steps grouped into phases with QA gates between them |
| Parallel dispatch | Independent steps within a phase run concurrently via `next_actions()` |
| Dependency tracking | `depends_on` per step — blocked steps wait, unblocked steps dispatch |
| State persistence | Full state saved to disk — survives crashes and session interrupts |
| Crash recovery | `baton execute resume` picks up exactly where it left off |
| QA gates | Build, test, lint, spec, and review gates between phases |
| Observability | Structured traces, usage logs, and retrospectives for every task |
| Learning pipeline | Pattern learner and budget tuner optimize future plans from history |

---

## Python Package (`agent_baton`)

### Core Classes

| Class | Module | Purpose |
|-------|--------|---------|
| **Engine** | | |
| `IntelligentPlanner` | core.engine.planner | Data-driven plan creation with pattern learning |
| `ExecutionEngine` | core.engine.executor | State machine driving phased execution |
| `PromptDispatcher` | core.engine.dispatcher | Delegation prompt generation |
| `GateRunner` | core.engine.gates | QA gate evaluation |
| **Orchestration** | | |
| `AgentRegistry` | core.registry | Load + query agent definitions from disk |
| `AgentRouter` | core.router | Detect project stack, route to agent flavors |
| `PlanBuilder` | core.plan | Execution plans + risk assessment |
| `ContextManager` | core.context | Team-context file I/O |
| **Governance** | | |
| `AgentValidator` | core.validator | Agent .md format correctness |
| `TaskClassifier` | core.classifier | Sensitivity classification + guardrail presets |
| `PolicyEngine` | core.policy | Guardrail policy evaluation |
| `ComplianceReporter` | core.compliance | Compliance report generation |
| `SpecValidator` | core.spec_validator | Agent output validation against specs |
| **Observability** | | |
| `UsageLogger` | core.usage | JSONL usage tracking per task |
| `TraceRecorder` | core.observe.trace | Structured execution traces |
| `RetrospectiveEngine` | core.retrospective | Task retrospectives |
| `DashboardGenerator` | core.dashboard | Markdown usage dashboard |
| `TelemetryCollector` | core.telemetry | Agent telemetry events |
| **Improvement** | | |
| `PerformanceScorer` | core.scoring | Per-agent scorecards |
| `AgentVersionControl` | core.vcs | Agent prompt changelog + backups |
| `PromptEvolution` | core.evolution | Prompt improvement proposals |
| `PatternLearner` | core.learn.pattern_learner | Learn orchestration patterns from history |
| `BudgetTuner` | core.learn.budget_tuner | Optimize budget tiers from usage data |
| **Distribution** | | |
| `PackageBuilder` | core.sharing | Create distributable packages |
| `ProjectTransfer` | core.transfer | Transfer agents/knowledge between projects |

### CLI Commands

```
baton agents                         # List available agents
baton detect                         # Detect project stack
baton route [ROLES]                  # Route roles to agent flavors
baton status                         # Show team-context file status
baton install --scope user           # Install agents + references globally
baton install --scope user --upgrade # Upgrade agents/refs, merge settings
baton install --scope user --verify  # Run post-install health check

# Execution engine
baton plan "task description"        # Create a data-driven execution plan
baton plan "task" --save --explain   # Save plan to disk with explanation
baton execute start                  # Start execution from saved plan
baton execute next                   # Get the next action
baton execute next --all             # Get ALL dispatchable actions (parallel)
baton execute dispatched --step-id ID # Mark step as in-flight
baton execute record --step-id ID    # Record step completion
baton execute gate --phase-id N      # Record QA gate result
baton execute complete               # Finalize (writes trace + usage + retro)
baton execute status                 # Show current execution state
baton execute resume                 # Resume after crash/interruption

# Observability
baton usage                          # Usage statistics summary
baton usage --agent NAME             # Per-agent stats
baton scores                         # Agent performance scorecards
baton scores --write                 # Write scorecard report to disk
baton dashboard                      # Generate usage dashboard
baton dashboard --write              # Write dashboard to disk
baton retro                          # List recent retrospectives
baton retro --search KEYWORD         # Search retrospectives
baton retro --recommendations        # Extract roster recommendations
baton trace                          # List execution traces
baton telemetry                      # Show telemetry events
baton patterns                       # Show learned orchestration patterns
baton budget                         # Show budget tier recommendations

# Governance
baton validate agents/               # Validate agent definitions
baton classify "task description"    # Classify task sensitivity
baton policy                         # List guardrail policy presets
baton compliance                     # Show compliance reports
baton spec-check                     # Validate agent output against spec
baton escalations                    # Show/resolve agent escalations
baton changelog                      # Show agent change history
baton changelog --backups            # List backup files

# Distribution
baton package --output ./dist        # Create distributable package
baton verify-package archive.tar.gz  # Verify package before distribution
baton publish archive.tar.gz         # Publish to local registry
baton pull PACKAGE                   # Install from local registry
baton transfer --to /path/to/project # Transfer to another project
baton context-profile                # Show agent context efficiency
baton evolve                         # Propose prompt improvements
baton incident                       # Manage incident workflows
baton async                          # Dispatch/track async tasks
```

---

## Distributable Agents (19)

Installed to `.claude/agents/` in target projects:

| Agent | Role |
|-------|------|
| `orchestrator` | Coordinate multi-step tasks across specialist agents |
| `backend-engineer` | Server-side implementation (generic) |
| `backend-engineer--node` | Node.js/TypeScript backend specialist |
| `backend-engineer--python` | Python backend specialist |
| `frontend-engineer` | Client-side UI implementation |
| `frontend-engineer--react` | React/Next.js specialist |
| `frontend-engineer--dotnet` | Blazor/.NET frontend specialist |
| `architect` | System design and technical decisions |
| `ai-systems-architect` | Multi-agent orchestration design |
| `test-engineer` | Write and organize tests |
| `code-reviewer` | Code quality review |
| `security-reviewer` | Security audit (OWASP, auth, secrets) |
| `auditor` | Safety, compliance, and governance review |
| `devops-engineer` | Infrastructure, CI/CD, Docker |
| `data-engineer` | Databases, ETL, data modeling |
| `data-analyst` | BI, reporting, SQL, KPI definition |
| `data-scientist` | ML, statistical analysis, modeling |
| `visualization-expert` | Charts, dashboards, visual storytelling |
| `subject-matter-expert` | Domain-specific business operations |

---

## Setup

```bash
# Install Python package (editable mode with dev dependencies)
pip install -e ".[dev]"

# Install agents globally (all projects)
baton install --scope user

# Or use the interactive installer
scripts/install.sh

# Verify installation
baton agents
baton validate agents/

# Run tests
pytest
```

---

## Quick Start

```bash
# In any project with Claude Code:

# 1. Describe a complex task
"Use the orchestrator to build a health check API with tests"

# 2. The orchestrator creates a plan, dispatches agents, runs gates
# 3. Specialist agents implement, test, and review
# 4. Execution trace, usage log, and retrospective are written automatically
# 5. Learned patterns improve future plans
```

See [QUICKSTART.md](QUICKSTART.md) for detailed installation and first-run instructions.

---

## Roadmap Status

### Epic 1: Foundation + Measure + Deliver — COMPLETE
- [x] Usage Logger, Retrospective Engine, Agent Prompt VCS, Decision Journal
- [x] Performance Scoring, Cost & Usage Dashboard
- [x] Document Generation Pipeline

### Epic 2: Observe + Learn + Package — COMPLETE
- [x] Trace Recorder, Context Profiler, Telemetry Collector
- [x] Pattern Learner, Budget Tuner
- [x] Package Builder, Registry Client, Project Transfer

### Epic 2: Execution Engine — COMPLETE
- [x] Intelligent Planner (data-driven plans from history + heuristics)
- [x] Execution Engine (state machine with crash recovery)
- [x] Prompt Dispatcher (structured delegation prompts)
- [x] Gate Runner (build, test, lint, spec, review gates)
- [x] Full observability integration (traces, usage, retrospectives)

### Epic 2: Governance — COMPLETE
- [x] Task Classifier, Policy Engine, Compliance Reporter
- [x] Spec Validator, Escalation Manager

### Parallel Execution + Install Path + Security Hardening — COMPLETE
- [x] Parallel step dispatch within phases (`next_actions()`, `mark_dispatched()`)
- [x] Dependency-aware scheduling (`depends_on`, `WAIT` action type)
- [x] Install upgrade mode with settings merge (`--upgrade`, `--verify`)
- [x] Tarfile path traversal protection (`_safe_extractall`)
- [x] ID sanitization, hook regex hardening, secrets gitignore, version pinning

### Future
- [ ] Cross-project knowledge federation
- [ ] Remote registry (beyond local directory)
