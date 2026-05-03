# references/ — distributable reference procedures

18 reference procedures installed into user projects under `.claude/references/`. Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## What a reference is

A reference is a **procedural document** an agent loads on demand. Unlike an
agent definition (system prompt) or a doc page (human-facing explainer), a
reference is a runbook the agent reads while working — protocols, decision
frameworks, escalation chains, formatting standards.

## Files in this directory

| Topic | File |
|-------|------|
| Engine protocol from the agent side | `baton-engine.md` |
| Common Baton patterns | `baton-patterns.md` |
| Adaptive execution heuristics | `adaptive-execution.md` |
| Agent routing rules | `agent-routing.md` |
| Communication protocols | `comms-protocols.md` |
| Compliance & audit chain | `compliance-audit-chain.md` |
| Cost / budget rules | `cost-budget.md` |
| Decision framework | `decision-framework.md` |
| Doc generation conventions | `doc-generation.md` |
| Failure handling | `failure-handling.md` |
| Git strategy | `git-strategy.md` |
| Guardrail presets | `guardrail-presets.md` |
| Hooks enforcement | `hooks-enforcement.md` |
| Knowledge architecture | `knowledge-architecture.md` |
| Planning taxonomy | `planning-taxonomy.md` |
| Research procedures | `research-procedures.md` |
| Task sequencing | `task-sequencing.md` |
| Team messaging | `team-messaging.md` |

## Conventions

- One topic per file. If a reference grows past ~300 lines, split it.
- Procedures use numbered steps with explicit pre/post-conditions, not narrative prose.
- Cross-links use relative paths; references can link to other references and to `docs/`.
- Don't duplicate content from agent definitions in `agents/` — link instead.

## Adding a reference

1. Write `references/<topic>.md`. Title is `# <Topic>` and the file is procedural.
2. If an agent should always load it, mention it in the relevant `agents/<name>.md` body.
3. Link from any related reference and from `docs/orchestrator-usage.md` if relevant.
