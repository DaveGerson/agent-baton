---
name: architecture
description: Package layout, key classes, data flow, file resolution precedence, and design principles for the agent-baton Python package
tags: [architecture, package-layout, design, data-flow, dependencies]
priority: high
---

# Agent Baton — Architecture

## Overview

Agent Baton is a multi-agent orchestration system for Claude Code. It provides:
1. A Python package (`agent_baton`) implementing the orchestration engine
2. Distributable agent definitions (markdown files with YAML frontmatter)
3. Reference documents defining orchestration procedures
4. Install scripts for deploying to `~/.claude/` or `.claude/`

## Package Layout

```
agent_baton/
├── models/          ← Data structures (dataclasses)
│   ├── enums.py     ← RiskLevel, TrustLevel, BudgetTier, ExecutionMode,
│   │                   GateOutcome, FailureClass, GitStrategy, AgentCategory
│   ├── agent.py     ← AgentDefinition (parsed from .md frontmatter)
│   ├── plan.py      ← ExecutionPlan, Phase, AgentAssignment, QAGate, MissionLogEntry
│   └── reference.py ← ReferenceDocument
├── core/            ← Business logic
│   ├── registry.py  ← AgentRegistry: load, search, query agents from disk
│   ├── router.py    ← AgentRouter: detect stack → pick agent flavor
│   ├── plan.py      ← PlanBuilder: create + serialize execution plans
│   └── context.py   ← ContextManager: shared context, mission log, codebase profile
├── cli/             ← CLI interface
│   └── main.py      ← `baton` command entry point
└── utils/           ← Shared utilities
    └── frontmatter.py ← YAML frontmatter parser
```

## Key Classes

| Class | Module | Responsibility |
|-------|--------|----------------|
| `AgentDefinition` | models.agent | Parsed agent: name, model, tools, instructions |
| `ExecutionPlan` | models.plan | Task plan with phases, gates, agent assignments |
| `AgentRegistry` | core.registry | Load agents from `~/.claude/agents/` + `.claude/agents/` |
| `AgentRouter` | core.router | Detect project stack → match to agent flavors |
| `PlanBuilder` | core.plan | Construct plans from task descriptions |
| `ContextManager` | core.context | Read/write team-context files |

## Data Flow

```
Agent .md files on disk
        │
        ▼
  AgentRegistry.load()     ← parses frontmatter + markdown body
        │
        ▼
  AgentRouter.route()      ← reads pyproject.toml/package.json → picks flavors
        │
        ▼
  PlanBuilder.build()      ← creates ExecutionPlan with phases + gates
        │
        ▼
  ContextManager.write()   ← writes plan.md, context.md, mission-log.md to disk
```

## File Resolution

Agents and references are loaded from two locations, project taking precedence:

| Location | Scope | Priority |
|----------|-------|----------|
| `.claude/agents/` | Project-specific | Higher (wins on name collision) |
| `~/.claude/agents/` | Global (all projects) | Lower (fallback) |

Same pattern for references, knowledge packs.

## Dependencies

- **Runtime**: `pyyaml>=6.0` (frontmatter parsing)
- **Dev**: `pytest`, `pytest-cov`
- **Python**: 3.11+
- **No framework dependencies** — pure library, no FastAPI/Django/Flask

## Design Principles

1. **Dataclasses over Pydantic** — minimal dependencies
2. **pathlib.Path everywhere** — no string path manipulation
3. **Read-only by default** — registry and router never write files
4. **Markdown in, markdown out** — plans and logs render to .md
5. **No network calls** — everything is local filesystem
