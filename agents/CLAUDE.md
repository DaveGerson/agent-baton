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
- Immune-system agents (`immune-*`) follow established naming — match the pattern, don't invent new ones.

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

## Teammate-safety: `skills` and `mcpServers` frontmatter (A1.e)

Claude Code's Agent Teams feature does **NOT** honor the `skills:` or
`mcpServers:` frontmatter fields when a subagent definition is used as a
teammate. From the [Agent Teams docs](https://code.claude.com/docs/en/agent-teams):

> The `skills` and `mcpServers` frontmatter fields in a subagent definition
> are not applied when that definition runs as a teammate. Teammates load
> skills and MCP servers from your project and user settings, the same as a
> regular session.

This is **enforced at dispatch time**, not just documented. When
`BATON_TEAMS_BACKEND=claude-teams` is active, `ClaudeTeamsBackend` audits
every agent in the team step (and any flattened sub-team members) and renders
a per-agent warning block into the generated `spawn.md`:

> WARNING: agent `X` declares skills/mcpServers — NOT honored as a teammate;
> capabilities will be missing.

The backend **degrades loudly, never blocks**: the team still dispatches, but
the lead is told to re-inject the missing context into that teammate's prompt,
or to run the team under the `worktree` backend (which honors full
frontmatter). The same audit warns when sub-teams are flattened (Agent Teams
cannot nest) and when a team exceeds the recommended ≤5 members.

The audit helper that powers this lives in
`agent_baton/core/engine/team_backends.py`:

```python
from agent_baton.core.engine.team_backends import audit_agents_for_teammate_safety
audit_agents_for_teammate_safety(Path("agents/"))  # → {agent_name: ["skills", "mcpServers"]}
```

Run it when adding a new agent you intend to use as a teammate, so you know
up front whether it needs context re-injection under `claude-teams`.
