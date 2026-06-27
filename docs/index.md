# Agent Baton

**Turn one prompt into a coordinated team of AI specialists.**

Agent Baton is a project manager for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Describe a complex effort in plain language — Baton plans it with foresight, composes the right specialist agents, dispatches each to the right problem at the right time, and keeps the work on track with checks and balances. It delivers tested, reviewed code. No external services. No API keys beyond Claude. Everything runs locally.

```
You:  "Use the orchestrator to add input validation to the API
       with tests and security review"

Baton: Plans 3 phases (implement, test, review)
       Dispatches backend-engineer, test-engineer, security-reviewer
       Runs pytest gate between phases
       Commits each agent's work separately
       Writes trace, usage log, and retrospective
```

## What Baton is for

Four high-level goals, in priority order:

1. **Plan with foresight** — break the effort down, classify risk, and forecast cost *before* execution, so you see where it will break up front.
2. **Compose the right team** — create bespoke, narrowly-scoped specialists (via `talent-builder`) so no generalist drowns in whole-codebase context.
3. **Right agent, right problem, right time** — a deterministic engine dispatches each phase to the specialist that fits it, with QA gates between.
4. **Checks & balances** — an independent `auditor` (and a `subject-matter-expert` for regulated work) verifies the result is *functionally* right, not just that it lints.

## Why Agent Baton?

Claude Code is powerful, but a complex effort — one that spans many files, needs different kinds of expertise, and has to actually be *correct* — is hard to run as one long conversation. You get context rot, missed coverage, no foresight into what's coming, and no record of what happened.

Agent Baton gives Claude Code a project-management layer. You stay in control while the agents do the heavy lifting.

| Without Baton | With Baton |
|---------------|------------|
| One long conversation doing everything | A planned effort, phased and sequenced |
| One generalist drowning in whole-codebase context | Bespoke specialists tuned to each problem |
| No idea what's coming or what might break | Up-front plan, risk classification, cost forecast |
| Manual "did you run the tests?" | Automated gates + domain-expert checks between phases |
| Hope the AI got it *right* | Independent auditor / SME verification on risky work |
| No record of what happened | Full traces, usage logs, retrospectives |

## Where to go next

- **[Orchestrator Usage](orchestrator-usage.md)** — how to drive a task end-to-end through the engine
- **[Agent Roster](agent-roster.md)** — the 30 specialist agents Baton can dispatch
- **[Architecture Overview](architecture.md)** — the orchestration engine, storage, and supporting subsystems
- **[CLI Reference](cli-reference.md)** — every `baton` subcommand
- **[Examples](examples/first-run.md)** — first-run walkthrough and knowledge-pack samples

---

> Source: <https://github.com/DaveGerson/agent-baton>
