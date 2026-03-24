# Agent Baton — Multi-Agent Orchestration for Claude Code

Give Claude Code a team of specialists. Describe a complex task, and the
orchestrator plans it, delegates to the right agents, runs QA checks between
phases, and delivers tested, reviewed code — all inside your normal Claude
Code session.

## What You Get

**19 specialist agents** — backend, frontend, architect, devops, testing,
data engineering, security review, code review, and more. Stack-aware:
ask for a backend engineer on a Node.js project, you get one that knows
Express and Prisma. On a Python project, you get FastAPI and SQLAlchemy.

**12 reference procedures** — the orchestrator reads these before every task.
They encode how to plan, what guardrails to apply, how agents communicate,
when to commit, and how to handle failures. You never see them; they make
the orchestrator smarter.

**Knowledge delivery** — curated knowledge packs are resolved at plan time
and injected into each agent's delegation prompt. Agents receive only the
knowledge relevant to their task, not a global dump. Agents can signal
knowledge gaps at runtime; the engine auto-resolves via registry or queues
for human review. A feedback loop learns which agents need what knowledge
for which task types, improving future plans automatically.

**Smart Forge** — AI-driven task planning with interactive refinement.
Propose tasks, get decomposed execution plans, and refine them before
committing. Integrates with the PMO UI for a visual plan review experience.

**Federated sync and cross-project queries** — execution data from all
your projects flows into a central read replica at `~/.baton/central.db`.
Query agent reliability, token costs, and knowledge gaps across every
project with `baton query`. The PMO reads from central so its status view
is always up to date, even across machines.

**External source adapters** — connect Azure DevOps, Jira, GitHub, or
Linear as external sources. Work items are pulled into `central.db` and
linked to baton plans. See which ADO features have orchestration plans,
and which plans have no external tracking.

**Risk-tiered safety** — simple changes run fast with minimal overhead.
Changes touching databases, production systems, or regulated data
automatically involve the auditor agent, which has independent veto power.

## Install (30 seconds)

```bash
cd /path/to/agent-baton
scripts/install.sh        # Choose user-level (all projects) or project-level
```

Or manually: copy `agents/*.md` into `.claude/agents/`, copy
`references/*.md` into `.claude/references/`, copy `templates/CLAUDE.md`
to your project root.

See [QUICKSTART.md](QUICKSTART.md) for detailed options and troubleshooting.

## Use

Describe a complex task naturally:

```
Use the orchestrator to build a health check API with tests and documentation
```

The orchestrator will:
1. Read the reference procedures
2. Detect your project stack (Node, Python, .NET, etc.)
3. Present an execution plan with phases, agents, and QA gates
4. Ask you to approve
5. Dispatch specialist agents (in parallel when independent)
6. Run build/test/lint checks between phases
7. Commit each agent's work separately

You can also invoke specialists directly:

```
Use the data-analyst to investigate our fleet utilization trends
Use the security-reviewer to audit our authentication flow
```

## How It Works

```
                    ┌─────────────────────────────┐
                    │        ORCHESTRATOR          │
                    │                              │
                    │  Reads references inline:    │
                    │  • Decision framework        │
                    │  • Research procedures        │
                    │  • Agent routing + stack      │
                    │  • Guardrail presets          │
                    │  • Cost/budget models         │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │   AUDITOR    │ │   SUBJECT    │ │   TALENT     │
     │ (independent │ │   MATTER     │ │   BUILDER    │
     │  veto power) │ │   EXPERT     │ │ (creates new │
     └──────────────┘ └──────────────┘ │  agents)     │
                                        └──────────────┘
     ┌─────────────────────────────────────────────┐
     │        SPECIALIST AGENTS                     │
     │  Backend · Frontend · Architect · DevOps     │
     │  Testing · Data Eng · Data Sci · Analyst     │
     │  Visualization · Security · Code Review      │
     └─────────────────────────────────────────────┘
```

### Design Philosophy

> **Core principle: Pay for context only when you need isolation.**

Every subagent costs a full context window, startup latency, and information
loss when summarizing results back. Agent Baton minimizes this:

- **Research, routing, and communication** run inline in the orchestrator's
  context — no subagent overhead for lookup work
- **Specialists** get their own context only when doing substantial,
  independent work (writing code, running tests, reviewing security)
- **Shared knowledge** lives in reference documents read by any agent
  that needs it, not duplicated across context windows

See `references/decision-framework.md` for the five-test flowchart that
decides: subagent, inline skill, or reference document.

### Agent Flavoring

Specialists have base and stack-specific variants. The orchestrator detects
your stack and routes automatically:

```
backend-engineer.md           ← Any backend
backend-engineer--node.md     ← Node.js / TypeScript
backend-engineer--python.md   ← Python / FastAPI / Django

frontend-engineer.md          ← Any frontend
frontend-engineer--react.md   ← React / Next.js
frontend-engineer--dotnet.md  ← Blazor / .NET
```

First time on a Go project? The `talent-builder` agent creates
`backend-engineer--go`. Next time, the routing table finds it.

### The Auditor's Dual Nature

- **LOW risk** (simple code changes, read-only analysis): the orchestrator
  applies guardrail presets inline. No subagent overhead.
- **MEDIUM+ risk** (multi-agent writes, database changes, production systems):
  the auditor runs as an independent subagent with veto authority, specifically
  so it can disagree with the orchestrator's plan.

## Agents

| Agent | Role |
|-------|------|
| `orchestrator` | Plans and coordinates multi-step tasks |
| `backend-engineer` | Server-side implementation (+ `--node`, `--python` flavors) |
| `frontend-engineer` | Client-side UI (+ `--react`, `--dotnet` flavors) |
| `architect` | System design and technical decisions |
| `ai-systems-architect` | Multi-agent orchestration design |
| `test-engineer` | Write and organize tests |
| `code-reviewer` | Code quality review |
| `security-reviewer` | Security audit (OWASP, auth, secrets) |
| `auditor` | Safety, compliance, governance (veto power) |
| `devops-engineer` | Infrastructure, CI/CD, Docker |
| `data-engineer` | Databases, ETL, data modeling |
| `data-analyst` | BI, reporting, SQL, KPI definition |
| `data-scientist` | ML, statistical analysis, modeling |
| `visualization-expert` | Charts, dashboards, visual storytelling |
| `subject-matter-expert` | Domain-specific business operations |
| `talent-builder` | Creates new agent definitions |

## References

| Document | What it does |
|----------|-------------|
| `decision-framework.md` | When to use subagent vs skill vs reference doc |
| `research-procedures.md` | 4 research modes the orchestrator runs inline |
| `agent-routing.md` | Stack detection + agent flavor matching |
| `guardrail-presets.md` | Risk triage + standard guardrail configs |
| `comms-protocols.md` | Delegation prompts, handoff briefs, logging |
| `task-sequencing.md` | Phase ordering + parallel dispatch rules |
| `git-strategy.md` | Commit conventions per risk level |
| `cost-budget.md` | Token cost models and budget tiers |
| `failure-handling.md` | What to do when agents fail or get stuck |
| `hooks-enforcement.md` | Mechanical guardrails via Claude Code hooks |
| `doc-generation.md` | Document generation pipeline |
| `knowledge-architecture.md` | Knowledge pack structure and conventions |

## Optional: Python CLI (`baton`)

The agents and references work without the Python package. But if you want
execution tracking, crash recovery, and operational tooling:

```bash
pip install -e ".[dev]"
```

This gives you the `baton` CLI:

```
# Core workflow
baton plan "task description" --save   # Create an execution plan
baton plan "task" --knowledge path/to/doc.md --knowledge-pack compliance \
                  --intervention medium  # Plan with explicit knowledge + escalation
baton execute start                    # Drive the plan through phases
baton execute next --all               # Get all parallel-dispatchable steps
baton execute resume                   # Resume after crash/interruption

# Operational visibility
baton usage                            # Usage statistics
baton scores                           # Agent performance scorecards
baton dashboard --write                # Generate usage dashboard
baton retro                            # Task retrospectives
baton trace                            # Execution traces

# Agent management
baton agents                           # List available agents
baton detect                           # Detect project stack
baton route [ROLES]                    # Route roles to agent flavors
baton validate agents/                 # Validate agent definitions
baton install --scope user --verify    # Install with health check

# Cross-project and federated sync
baton sync                             # Sync current project to central.db
baton sync --all                       # Sync all registered projects
baton query agents                     # Agent reliability across all projects
baton query "SELECT ..."               # Ad-hoc SQL against central.db
baton source add ado --org ORG ...     # Register an Azure DevOps source
baton source sync <id>                 # Pull latest items from external source
```

## Tips

- **The system self-expands.** First time on a Go project? The talent-builder
  creates `backend-engineer--go`. Next time, the routing table finds it.
- **Watch for agent sprawl.** Periodically review the roster against the
  decision framework. Can any agents be downgraded to reference docs?
- **3-5 specialists per task.** More than that and coordination overhead
  outweighs benefits.
- **Say "use the orchestrator"** explicitly for your first few runs so
  Claude Code routes to the right agent.

## Architecture

The system has three layers with two critical interface boundaries:

```
Human User ←→ Claude Code (natural language) ←→ baton CLI ←→ Python engine
```

Claude reads the orchestrator agent definition as its prompt, calls `baton`
CLI commands, and parses structured text output to drive execution. The Python
engine provides planning, state management, crash recovery, and observability.

For full details see:
- [`docs/architecture.md`](docs/architecture.md) — Package layout, dependency graph, key contracts
- [`docs/design-decisions.md`](docs/design-decisions.md) — Why the architecture looks the way it does
- [`docs/invariants.md`](docs/invariants.md) — Interface boundaries that must not change

## Project Structure

```
agents/            ← 19 agent definitions (markdown + YAML frontmatter)
references/        ← 12 reference procedures (shared knowledge)
templates/         ← CLAUDE.md + settings.json for target projects
scripts/           ← Install scripts (Linux + Windows)
docs/              ← Architecture documentation
agent_baton/       ← Optional Python package (CLI + execution engine)
tests/             ← ~3675 pytest tests
```

## Contributing

See [CLAUDE.md](CLAUDE.md) for development setup and conventions.
