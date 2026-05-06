# agent_baton/core/orchestration/ — agent selection and run coordination

One level above the engine in the call stack: this layer chooses *which* agent and *what* knowledge to attach, then hands off to `engine/`. Inherits: [../../../CLAUDE.md](../../../CLAUDE.md), [../CLAUDE.md](../CLAUDE.md).

## Files

| File | Role |
|------|------|
| `registry.py` | Agent registry — discovery, capabilities, tool grants |
| `knowledge_registry.py` | Knowledge-pack registry; resolves pack names to content |
| `router.py` | Maps a task description to one or more agents |
| `runner.py` | Drives a single orchestrated run end-to-end |
| `context.py` | Per-run context object (task, plan, knowledge, scratchpad) |

## Conventions

- **The registry is the source of truth for agent capabilities.** Don't cache router decisions or hard-code agent names anywhere else in `core/`.
- **Routing is deterministic for a given (task, registry) pair** — no randomness, no time-based behavior. This makes runs reproducible and tests stable.
- **Knowledge packs are immutable per run.** Resolve at run-start, freeze the resolved set into `context.py`, and don't re-resolve mid-run.
- **The runner drives; the engine acts.** `runner.py` orchestrates the action loop using the public `ExecutionDriver` protocol from `engine/protocols.py`. Don't reach into engine internals.

## When you change routing

1. Update `references/agent-routing.md` if the routing rules are visible to agents.
2. Add a routing test under `tests/orchestration/` — at minimum, one test per new routing branch.
3. If a new agent is selectable, ensure it's registered in `agents/<name>.md` and re-run `scripts/sync_bundled_agents.sh`.

## Don'ts

- Don't import from `engine/` internals — use `engine/protocols.py` only.
- Don't put governance decisions here. Risk classification and guardrails live in `core/govern/`.
- Don't introduce a separate "router cache" — the registry is already the cache.
