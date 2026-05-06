# agents/ — distributable agent definitions

33 agent definitions installed into user projects under `.claude/agents/`. Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## File format

Each agent is a Markdown file with YAML frontmatter:

```yaml
---
name: agent-name           # must match the filename (without .md)
description: When to invoke this agent. Used by the router.
tools: [Read, Write, ...]  # optional allowlist
model: opus | sonnet | haiku   # optional override
---
# Agent body — system prompt that runs when this agent is dispatched
```

The `description` field is what the orchestrator's router matches against — write it as a clear capability statement, not a marketing blurb.

## Conventions

- **Filename = `name` field.** The installer enforces this.
- One responsibility per agent. If you find yourself writing "this agent does X or Y", split it.
- Body is a system prompt — write it as instructions to the agent, not documentation about the agent. Documentation goes in `docs/agent-roster.md`.
- Tools allowlist: include only what's required. Read-only research agents should not have `Write`/`Edit`/`Bash`.
- Self-heal tier (`self-heal-haiku`/`-sonnet`/`-opus`) and immune-system agents (`immune-*`) follow established naming — match the pattern, don't invent new ones.

## Adding a new agent

1. Write `agents/<name>.md` following the pattern above.
2. Update [docs/agent-roster.md](../docs/agent-roster.md) with a one-line summary.
3. Run `scripts/sync_bundled_agents.sh` to mirror into `agent_baton/_bundled_agents/`.
4. Add a routing test under `tests/orchestration/` if the agent should be selected for specific task patterns.

## Removing an agent

1. Delete the file.
2. Re-run `scripts/sync_bundled_agents.sh`.
3. Remove from `docs/agent-roster.md`.
4. Search for references in `references/agent-routing.md` and other reference procedures.

## Roster

See [../docs/agent-roster.md](../docs/agent-roster.md) for the human-readable roster (capabilities, when to use which, tool grants).
