# Agent Baton — Development Guide

This repo contains the source for Agent Baton, a multi-agent orchestration
system for Claude Code.

## Repository Structure

```
agent_baton/       ← Python package (orchestration engine)
  models/          ← Data models (16 modules: agent, plan, execution, events, decision, ...)
  core/            ← Business logic (9 sub-packages)
    engine/        ← Execution engine (planner, executor, dispatcher, gates)
    orchestration/ ← Context, plan, registry, router
    govern/        ← Classifier, compliance, escalation, policy, validation
    observe/       ← Trace, usage, dashboard, retrospective, telemetry, context profiler
    improve/       ← Evolution, scoring, VCS
    learn/         ← Pattern learner, budget tuner
    distribute/    ← Async dispatch, packaging, sharing, transfer, incident
    events/        ← Event bus, domain events, persistence, projections
    runtime/       ← Async worker, scheduler, launcher, decisions, supervisor
  cli/             ← CLI interface (35 commands via `baton`)
agents/            ← Distributable agent definitions (19 .md files)
references/        ← Distributable reference docs (13 .md files)
templates/         ← CLAUDE.md + settings.json installed to target projects
scripts/           ← Install scripts (Linux + Windows)
tests/             ← Test suite (1977 tests, pytest)
.claude/           ← Project-specific orchestration setup:
  agents/          ← Tailored agents for developing agent-baton (11)
  references/      ← Symlink → ../references/ (canonical source)
  knowledge/       ← Knowledge packs (3 packs, 10 docs)
  settings.json    ← Hooks for this project
```

## Key Rules

- `agents/` and `references/` are the **distributable** source of truth.
  Changes here affect all users who install agent-baton.
- `.claude/agents/` contains **project-specific** agents tailored for
  developing agent-baton. These are NOT distributed.
- `.claude/references/` is a symlink to `references/` — edits to canonical
  references are immediately available to the project's orchestrator.
- The `agent_baton` Python package reads agent definitions at runtime.
- `core/engine/` is the execution engine — changes here affect the runtime
  behavior of all orchestrated tasks.
- Backward-compatible shims exist at `core/*.py` for Epic 1 module paths.

## Agent Roster (for this project)

| Agent | Role |
|-------|------|
| `orchestrator` | Coordinate multi-step development tasks |
| `backend-engineer--python` | Python implementation in agent_baton/ |
| `architect` | Design decisions, module boundaries |
| `ai-systems-architect` | Multi-agent orchestration design |
| `test-engineer` | Write and organize pytest tests |
| `code-reviewer` | Quality review before commits |
| `auditor` | Safety review for guardrail/hook changes |
| `talent-builder` | Create new distributable agent definitions |
| `agent-definition-engineer` | Edit agent .md files, references, knowledge packs |
| `prompt-engineer` | Agent prompt optimization |
| `ai-product-strategist` | Product decisions, value/cost analysis |

## Development

```bash
pip install -e ".[dev]"    # Install in editable mode
pytest                     # Run tests (1977 tests)
scripts/install.sh         # Re-install globally after editing agents/references
```

## Orchestrator Usage

For complex tasks involving 3+ files across different layers, use the
orchestrator agent. For simple single-file changes, work directly.

Changes to distributable files (`agents/`, `references/`) are MEDIUM risk
and should involve the auditor when substantial.

Changes to `core/engine/` affect the execution runtime and should have
corresponding test coverage.
