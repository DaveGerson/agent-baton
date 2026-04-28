# Agent Baton

📖 **Docs:** <https://davegerson.github.io/agent-baton/>

A multi-agent orchestration engine for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Describe a task in plain language; Baton plans the work, dispatches specialist agents, enforces QA gates between phases, and persists state for crash recovery.

```
You:  "Use the orchestrator to add input validation to the API
       with tests and security review"

Baton: Plans 3 phases (implement, test, review)
       Dispatches backend-engineer, test-engineer, security-reviewer
       Runs pytest gate between phases
       Commits each agent's work separately
       Writes trace, usage log, and retrospective
```

Local-only. No external services. The only required dependency is Claude Code itself.

---

## Why use it

Long Claude Code sessions on cross-cutting tasks tend to lose context, miss test coverage, and leave no audit trail. Baton adds a project management layer that breaks work into phases, scopes each phase to one specialist agent, enforces automated gates, and writes everything down.

| Without Baton | With Baton |
|---------------|------------|
| One long conversation does everything | Phases with specialist agents |
| Manual "did you run the tests?" | Automated pytest/lint gates between phases |
| No record of what happened | Full traces, usage logs, retrospectives |
| Hope the model remembers context | Scoped prompts per agent |
| Single point of failure | Crash recovery via `baton execute resume` |

---

## Get started in 5 minutes

### 1. Install agent definitions

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
scripts/install.sh          # Linux/macOS
# or scripts/install.ps1    # Windows
```

The installer prompts for scope (`~/.claude/` user-level vs project-level) and copies agent definitions, reference procedures, a template `CLAUDE.md`, and `settings.json` hooks.

### 2. Install the Python engine

```bash
pip install -e ".[dev]"     # From this checkout, with dev extras
```

### 3. Verify

```bash
baton --version
baton agents                # List agents from Python registry
baton detect                # Detect your project's stack
```

### 4. Run your first task

Walk through the [first-run tutorial](docs/examples/first-run.md). It runs end-to-end: install → plan → execute → trace inspection. Every command is verified.

---

## What's in the box

- **33 distributable agent definitions** in `agents/` — orchestrator, specialists (backend, frontend, data, security, devops), reviewers (code, security, auditor), domain experts, swarm/immune system agents.
- **18 reference procedures** in `references/` — engine protocol, routing logic, guardrail presets, knowledge architecture.
- **Python engine** (`agent_baton/`) — planner, executor, dispatcher, gate enforcement, persistence, tracing, learning automation.
- **CLI** (`baton ...`) — `plan`, `execute`, `trace`, `retro`, `usage`, `learn`, `sync`, `pmo`, more. See `baton --help` or [docs/cli-reference.md](docs/cli-reference.md).
- **REST API + PMO UI** (optional) — FastAPI app + React frontend at `/pmo/`. See [docs/api-reference.md](docs/api-reference.md).

---

## How it works (one minute)

1. You describe a task. The planner classifies risk, picks specialist agents, and writes `plan.json` + `plan.md` to `.claude/team-context/`.
2. `baton execute start` initializes tracing and returns the first action: DISPATCH (spawn an agent), GATE (run a check), APPROVAL (wait for sign-off), or INTERACT (multi-turn dialogue).
3. The orchestrator loop drives actions until COMPLETE. State persists between every step so a crashed session resumes cleanly.
4. On completion, the engine writes a trace, usage log, and retrospective. Patterns feed the learning pipeline that proposes config improvements over time.

For the design philosophy, the three load-bearing invariants, and the Claude↔engine contract, see [docs/architecture.md](docs/architecture.md).

---

## Documentation map

| You want… | Go to |
|-----------|-------|
| A guaranteed-success walkthrough | [Tutorial: first-run](docs/examples/first-run.md) |
| "How do I X?" recipes | [How-to: orchestrator usage](docs/orchestrator-usage.md) |
| CLI flag lookup | [Reference: CLI](docs/cli-reference.md) |
| REST API lookup | [Reference: API](docs/api-reference.md) |
| Agent roster | [Reference: agents](docs/agent-roster.md) |
| Why Baton works the way it does | [Explanation: architecture](docs/architecture.md) |
| Things going wrong | [How-to: troubleshooting](docs/troubleshooting.md) |

Agents working inside the codebase: start with `CLAUDE.md` (project-root rules) and `llms.txt` (machine-readable index per [llmstxt.org](https://llmstxt.org/)).

---

## Status

Active development. Tests pass; the engine is in regular use against this repo (dogfooding). The PMO UI, federated sync, and learning automation are functional but young — expect rough edges.

For known issues see [docs/baton-engine-bugs.md](docs/baton-engine-bugs.md).

---

## License

License pending. Contact the maintainers for terms.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The repo dogfoods Baton: contributions go through `baton plan` → `baton execute` like any other task.
