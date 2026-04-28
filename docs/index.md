# Agent Baton

**Turn one prompt into a coordinated team of AI specialists.**

Agent Baton is a multi-agent orchestration system for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Describe a complex task in plain language — Baton plans it, routes it to the right specialist agents, enforces QA gates between phases, and delivers tested, reviewed code. No external services. No API keys beyond Claude. Everything runs locally.

```
You:  "Use the orchestrator to add input validation to the API
       with tests and security review"

Baton: Plans 3 phases (implement, test, review)
       Dispatches backend-engineer, test-engineer, security-reviewer
       Runs pytest gate between phases
       Commits each agent's work separately
       Writes trace, usage log, and retrospective
```

## Why Agent Baton?

Claude Code is powerful, but complex tasks — the ones that touch multiple files, need testing, and require different expertise — benefit from structure. Without it, you get context bloat, missed test coverage, and no audit trail.

Agent Baton gives Claude Code a project management layer. It breaks work into phases, assigns each phase to a specialist agent, runs automated QA gates between them, and tracks everything. You stay in control while the agents do the heavy lifting.

| Without Baton | With Baton |
|---------------|------------|
| One long conversation doing everything | Phases with specialist agents |
| Manual "did you run the tests?" | Automated pytest/lint gates between phases |
| No record of what happened | Full traces, usage logs, retrospectives |
| Hope the AI remembers context | Scoped delegation prompts per agent |
| Single point of failure | Crash recovery via `baton execute resume` |

## Where to go next

- **[Orchestrator Usage](orchestrator-usage.md)** — how to drive a task end-to-end through the engine
- **[Agent Roster](agent-roster.md)** — the 47 specialist agents Baton can dispatch
- **[Architecture Overview](architecture.md)** — the engine, storage, governance, and learning subsystems
- **[CLI Reference](cli-reference.md)** — every `baton` subcommand
- **[Examples](examples/first-run.md)** — first-run walkthrough and knowledge-pack samples

---

> Source: <https://github.com/DaveGerson/agent-baton>
