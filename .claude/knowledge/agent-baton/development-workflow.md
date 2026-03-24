---
name: development-workflow
description: Setup, testing commands, directory responsibilities, and commit conventions for developing agent-baton
tags: [development, workflow, testing, conventions, git]
priority: normal
---

# Agent Baton — Development Workflow

## Setup

```bash
cd /path/to/agent-baton
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest                    # all tests
pytest tests/test_foo.py  # single file
pytest -x                 # stop on first failure
pytest --cov=agent_baton  # with coverage
```

## Directory Responsibilities

| You're editing... | Directory | Impact |
|-------------------|-----------|--------|
| Python package code | `agent_baton/` | Local only until released |
| Distributable agent definitions | `agents/` | Affects all users on install |
| Distributable reference docs | `references/` | Affects all users on install |
| Project-local agents | `.claude/agents/` | This repo only |
| Project knowledge | `.claude/knowledge/` | This repo only |
| Install templates | `templates/` | What users get on install |
| Install scripts | `scripts/` | How users install |

## Testing Changes to Agents/References

After editing files in `agents/` or `references/`:

1. **Test locally** — `.claude/references/` is symlinked to `references/`,
   so changes are immediately visible to the project's orchestrator
2. **Re-install globally** — `scripts/install.sh` (option 1) copies to `~/.claude/`
3. **Test in another project** — invoke the orchestrator there to verify

## Adding a New Distributable Agent

1. Create `agents/new-agent.md` with frontmatter + instructions
2. Apply the decision framework (references/decision-framework.md)
3. Update agent routing table if needed (references/agent-routing.md)
4. Run `scripts/install.sh` to deploy globally
5. Test with `/agents` in Claude Code to verify it loads

## Adding a New Reference Document

1. Create `references/new-reference.md`
2. Update the orchestrator agent (`agents/orchestrator.md`) to list it
   in the "Read ALL reference files" step
3. The symlink ensures it's immediately available in `.claude/references/`

## Commit Conventions

```
[component]: imperative summary

component = agent_baton | agents | references | scripts | templates | tests
```

Examples:
- `agent_baton: add AgentRegistry with frontmatter parsing`
- `agents: create backend-engineer--go flavor`
- `references: add cost-budget guidance for model selection`
- `tests: add registry unit tests`
