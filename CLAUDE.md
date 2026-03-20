# Agent Baton — Development Guide

This repo contains the source for Agent Baton, a multi-agent orchestration
system for Claude Code.

## Repository Structure

```
agent_baton/       ← Python package (orchestration engine)
agents/            ← Distributable agent definitions (19 .md files)
references/        ← Distributable reference docs (11 .md files)
templates/         ← CLAUDE.md + settings.json installed to target projects
scripts/           ← Install scripts (Linux + Windows)
tests/             ← Test suite (pytest)
.claude/           ← Project-specific orchestration setup:
  agents/          ← Tailored agents for developing agent-baton
  references/      ← Symlink → ../references/ (canonical source)
  knowledge/       ← Knowledge packs about agent-baton itself
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

## Agent Roster (for this project)

| Agent | Role |
|-------|------|
| `orchestrator` | Coordinate multi-step development tasks |
| `backend-engineer--python` | Python implementation in agent_baton/ |
| `architect` | Design decisions, module boundaries |
| `test-engineer` | Write and organize pytest tests |
| `code-reviewer` | Quality review before commits |
| `auditor` | Safety review for guardrail/hook changes |
| `talent-builder` | Create new distributable agent definitions |
| `agent-definition-engineer` | Edit agent .md files, references, knowledge packs |

## Development

```bash
pip install -e ".[dev]"    # Install in editable mode
pytest                     # Run tests
scripts/install.sh         # Re-install globally after editing agents/references
```

## Orchestrator Usage

For complex tasks involving 3+ files across different layers, use the
orchestrator agent. For simple single-file changes, work directly.

Changes to distributable files (`agents/`, `references/`) are MEDIUM risk
and should involve the auditor when substantial.
