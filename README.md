# Agent Baton — Multi-Agent Orchestration for Claude Code

A multi-agent orchestration system with a Python engine, distributable agent
definitions, and a CLI toolkit. Intelligently decides what should be a subagent
(its own context window) vs a skill (inline procedure) vs a reference document
(shared knowledge).

---

## Design Philosophy

> **Core principle: Pay for context only when you need isolation.**

Every subagent costs a full 200K-token context window, startup latency, and
information loss when summarizing results back. These costs are justified only
when the work is substantial, independence matters, or the output would
overwhelm the caller's context.

See `references/decision-framework.md` for the full five-test decision flowchart.

---

## Architecture

```
agent-baton/
├── agent_baton/             ← Python package (orchestration engine)
│   ├── models/              ← Data models (dataclasses)
│   │   ├── enums.py         ← RiskLevel, TrustLevel, BudgetTier, ExecutionMode, ...
│   │   ├── agent.py         ← AgentDefinition
│   │   ├── plan.py          ← ExecutionPlan, Phase, AgentAssignment, QAGate
│   │   ├── usage.py         ← TaskUsageRecord, AgentUsageRecord
│   │   ├── retrospective.py ← Retrospective, AgentOutcome, KnowledgeGap
│   │   └── reference.py     ← ReferenceDocument
│   ├── core/                ← Business logic
│   │   ├── registry.py      ← AgentRegistry: load/query agent definitions
│   │   ├── router.py        ← AgentRouter: stack detection → flavor matching
│   │   ├── plan.py          ← PlanBuilder: execution plans + risk assessment
│   │   ├── context.py       ← ContextManager: team-context file I/O
│   │   ├── validator.py     ← AgentValidator: format correctness checks
│   │   ├── usage.py         ← UsageLogger: JSONL usage tracking
│   │   ├── retrospective.py ← RetrospectiveEngine: task retrospectives
│   │   ├── vcs.py           ← AgentVersionControl: changelog + backups
│   │   ├── scoring.py       ← PerformanceScorer: agent scorecards
│   │   └── dashboard.py     ← DashboardGenerator: usage dashboard
│   ├── cli/                 ← CLI interface (`baton` command)
│   │   └── main.py          ← 11 commands: agents, detect, route, status,
│   │                           install, validate, changelog, usage, scores,
│   │                           dashboard, retro
│   └── utils/
│       └── frontmatter.py   ← YAML frontmatter parser
│
├── agents/                  ← Distributable agent definitions (19 agents)
├── references/              ← Distributable reference docs (12 docs)
├── templates/               ← CLAUDE.md + settings.json for installation
├── scripts/                 ← Install scripts (Linux + Windows)
├── tests/                   ← pytest suite (329+ tests)
│
├── .claude/                 ← Project-specific orchestration setup
│   ├── agents/              ← 11 agents tailored for developing agent-baton
│   ├── knowledge/
│   │   ├── agent-baton/     ← Architecture, format, workflow docs
│   │   ├── ai-orchestration/← Multi-agent patterns, prompt engineering,
│   │   │                       context economics, evaluation frameworks
│   │   └── case-studies/    ← Framework comparisons, failure modes, scaling
│   ├── references/          ← Symlink → ../references/
│   └── settings.json        ← Project hooks
│
└── reference_files/         ← Input docs / roadmap
```

---

## Python Package (`agent_baton`)

### Core Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `AgentRegistry` | core.registry | Load + query agent definitions from disk |
| `AgentRouter` | core.router | Detect project stack, route to agent flavors |
| `PlanBuilder` | core.plan | Create execution plans with risk assessment |
| `ContextManager` | core.context | Manage team-context files (plan, context, mission log) |
| `AgentValidator` | core.validator | Validate agent .md files for format correctness |
| `UsageLogger` | core.usage | JSONL usage tracking per orchestrated task |
| `RetrospectiveEngine` | core.retrospective | Structured task retrospectives |
| `AgentVersionControl` | core.vcs | Agent prompt changelog + backups |
| `PerformanceScorer` | core.scoring | Per-agent scorecards from usage + retro data |
| `DashboardGenerator` | core.dashboard | Markdown usage dashboard |

### CLI Commands

```
baton agents                     # List available agents
baton detect                     # Detect project stack
baton route [ROLES]              # Route roles to agent flavors
baton status                     # Show team-context file status
baton install --scope user       # Install agents + references
baton validate agents/           # Validate agent definitions
baton usage                      # Usage statistics summary
baton usage --agent NAME         # Per-agent stats
baton scores                     # Agent performance scorecards
baton scores --write             # Write scorecard report to disk
baton dashboard                  # Generate usage dashboard
baton dashboard --write          # Write dashboard to disk
baton retro                      # List recent retrospectives
baton retro --search KEYWORD     # Search retrospectives
baton retro --recommendations    # Extract roster recommendations
baton changelog                  # Show agent change history
baton changelog --backups        # List backup files
```

---

## Project Agent Roster

Agents in `.claude/agents/` tailored for developing agent-baton:

| Agent | Role | Domain |
|-------|------|--------|
| `orchestrator` | Coordinate multi-step tasks | Project-aware |
| `backend-engineer--python` | Python implementation | Knows agent_baton package |
| `ai-systems-architect` | AI orchestration design | Multi-agent patterns, context economics |
| `prompt-engineer` | Agent prompt optimization | Prompt engineering research |
| `ai-product-strategist` | Product decisions | Value/cost analysis, adoption paths |
| `agent-definition-engineer` | Agent .md files | Format, conventions, decision framework |
| `architect` | Generic system design | Standard |
| `test-engineer` | Testing | Standard |
| `code-reviewer` | Quality review | Standard |
| `auditor` | Safety review | Standard |
| `talent-builder` | Create new agents | Standard |

## Knowledge Packs

### AI Orchestration (`.claude/knowledge/ai-orchestration/`)
- **multi-agent-patterns.md** — Supervisor, router, hierarchical, fan-out patterns
- **prompt-engineering-principles.md** — Effective agent prompts, anti-patterns
- **context-economics.md** — Token cost models, information loss curves
- **agent-evaluation.md** — Scoring frameworks, A/B testing, health ratings

### Case Studies (`.claude/knowledge/case-studies/`)
- **orchestration-frameworks.md** — LangGraph, CrewAI, AutoGen, Claude agents compared
- **failure-modes.md** — 10 failure modes with mitigations
- **scaling-patterns.md** — 3→15 agents, 1→many projects, 1→team users

### Agent Baton (`.claude/knowledge/agent-baton/`)
- **architecture.md** — Package layout, class responsibilities, data flow
- **agent-format.md** — YAML frontmatter format, field reference
- **development-workflow.md** — Setup, testing, commit conventions

---

## Roadmap Status

### Wave 0: Foundation — COMPLETE
- [x] 0.1 Usage Logger (JSONL tracking)
- [x] 0.2 Retrospective Engine (structured task retrospectives)
- [x] 0.3 Agent Prompt VCS (changelog + backups)
- [x] 0.4 Decision Journal (delegation prompt template update)

### Wave 1: Measure — COMPLETE
- [x] 1.1 Agent Performance Scoring (per-agent scorecards)
- [x] 1.2 Cost & Usage Dashboard (markdown dashboard)

### Wave 1: Deliver — COMPLETE
- [x] 1.5 Document Generation Pipeline (reference doc + templates)

### Wave 1: Govern — PLANNED
- [ ] 1.3 Sensitive Data Classification
- [ ] 1.4 Compliance Report Generator

### Wave 2: Optimize + Scale — PLANNED
- [ ] 2.1 Prompt Evolution
- [ ] 2.2 Cross-Project Knowledge Transfer
- [ ] 2.3 Multi-User Agent Sharing

---

## Setup

```bash
# Install Python package
pip install -e ".[dev]"

# Install agents globally
baton install --scope user

# Or use the interactive script
scripts/install.sh

# Verify
baton agents
baton validate agents/
```

---

## Quick Start

```bash
# In any project with Claude Code:
# 1. Describe a complex task
"Use the orchestrator to build a health check API with tests"

# 2. The orchestrator reads references, detects stack, plans, and delegates
# 3. Specialist agents implement, test, and review
# 4. Mission log tracks everything at .claude/team-context/mission-log.md
```
