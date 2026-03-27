# Agent Baton -- Quickstart Guide

This guide takes you from zero to your first orchestrated task. Allow
15-20 minutes for a complete walkthrough.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Step 1: Install](#step-1-install)
- [Step 2: Verify](#step-2-verify)
- [Step 3: Understand What Got Installed](#step-3-understand-what-got-installed)
- [Step 4: Your First Orchestrated Task](#step-4-your-first-orchestrated-task)
- [Step 5: Using Agents Directly](#step-5-using-agents-directly)
- [Step 6: Set Up the PMO UI](#step-6-set-up-the-pmo-ui)
- [Common Questions](#common-questions)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** installed and working in your terminal
- **Python 3.10+** (for the execution engine and CLI)
- **Git** (the orchestrator creates branches and commits per agent)

> **Which install scope should I choose?** Pick **user-level** (`~/.claude/`)
> if you work on multiple projects -- agents will be available everywhere.
> Pick **project-level** (`.claude/`) to keep agents scoped to one repo.

---

## Step 1: Install

There are two layers to install: the **agent definitions** (required) and
the **Python engine** (recommended).

### 1a. Install Agent Definitions

#### Option A: Bash (Linux/macOS) -- recommended

```bash
cd /path/to/agent-baton
chmod +x scripts/install.sh
scripts/install.sh
```

The installer asks two questions:

1. **Install location** -- choose user-level (`~/.claude/`, all projects)
   or project-level (`.claude/`, current project only). User-level is
   recommended for most setups.

2. **Knowledge infrastructure** -- choose "knowledge packs" (local files,
   no extra infrastructure) unless you have a specific reason for RAG.

The script also creates `~/.baton/` for the central database.

#### Option B: PowerShell (Windows)

```powershell
cd path\to\agent-baton
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Same prompts as the bash installer.

#### Option C: Let Claude Code do it

Start a Claude Code session in your project directory:

```
I have Agent Baton at /path/to/agent-baton. Please copy all .md files
from its agents/ directory into .claude/agents/, all .md files from its
references/ directory into .claude/references/, create a
.claude/team-context/ directory, and copy templates/CLAUDE.md to my
project root. Then run /agents to verify they loaded.
```

#### Option D: Manual copy

```bash
mkdir -p .claude/agents .claude/references .claude/team-context
cp /path/to/agent-baton/agents/*.md .claude/agents/
cp /path/to/agent-baton/references/*.md .claude/references/
cp /path/to/agent-baton/templates/CLAUDE.md ./CLAUDE.md
cp /path/to/agent-baton/templates/settings.json .claude/settings.json
```

### 1b. Install the Python Engine

```bash
cd /path/to/agent-baton
pip install -e ".[dev]"
```

This gives you the `baton` CLI (45+ commands) for planning, execution
tracking, crash recovery, observability, governance, and distribution.

Want the REST API too?

```bash
pip install -e ".[dev,api]"
```

---

## Step 2: Verify

### Verify agents loaded in Claude Code

Start a Claude Code session and run:

```
/agents
```

You should see approximately 19 agents listed, including `orchestrator`,
`auditor`, `backend-engineer`, `test-engineer`, etc.

### Verify the CLI

```bash
baton agents                  # List agents from the Python registry
baton validate agents/        # Validate all agent definitions
baton detect                  # Detect your project's stack
```

If `baton` is not found, ensure the pip install completed and the script
entry point is on your PATH:

```bash
pip install -e ".[dev]"
which baton
```

### Verification Checklist

Run through this to confirm everything is working:

- [ ] `/agents` in Claude Code lists ~19 agents
- [ ] `baton agents` works in the terminal
- [ ] `baton plan --help` shows plan options
- [ ] `CLAUDE.md` exists in your project root
- [ ] `.claude/settings.json` exists with hook configuration
- [ ] `.claude/agents/` contains agent markdown files
- [ ] `.claude/references/` contains reference markdown files

---

## Step 3: Understand What Got Installed

### Files

| Location | Contents | Purpose |
|----------|----------|---------|
| `.claude/agents/*.md` | 19 agent definitions | Tell Claude Code what each specialist can do |
| `.claude/references/*.md` | 15 reference procedures | Shared knowledge (planning, routing, guardrails, etc.) |
| `.claude/team-context/` | Empty directory | Runtime workspace for plans, traces, logs |
| `.claude/knowledge/` | Empty directory | Knowledge packs for domain-specific context |
| `.claude/settings.json` | Hook configuration | Hooks that fire on subagent start/stop and file writes |
| `CLAUDE.md` | Project orchestration rules | Tells the orchestrator how to drive the execution engine |
| `~/.baton/` | Central database directory | `central.db` for cross-project queries and PMO |

### How the pieces fit together

```
You type a task in Claude Code
       |
       v
Claude reads the orchestrator agent definition (.claude/agents/orchestrator.md)
       |
       v
Orchestrator reads reference procedures (.claude/references/*.md)
       |
       v
Orchestrator calls `baton plan` to create a plan
       |
       v
Engine classifies risk, selects agents, resolves knowledge, sequences phases
       |
       v
Orchestrator calls `baton execute start` to begin
       |
       v
Engine returns DISPATCH actions -> Claude spawns specialist agents
Engine returns GATE actions    -> Claude runs test/lint/build checks
Engine returns APPROVAL actions -> Claude asks you for a decision
Engine returns COMPLETE        -> done, traces and retro written
```

### The settings.json hooks

The installed `settings.json` adds four hooks:

- **SubagentStart (orchestrator)** -- reminds the orchestrator to use
  `baton plan` + `baton execute`
- **SubagentStart (all)** -- creates the `team-context/` directory
- **SubagentStop** -- logs agent completion to `mission-log.md`
- **PreToolUse (Write/Edit)** -- blocks writes to `.env`, secrets, keys,
  and credentials

These hooks fire automatically. You do not need to configure them.

---

## Step 4: Your First Orchestrated Task

### Pick a task

Choose something real but low-stakes. Good first tasks:

- "Add a health check endpoint with tests"
- "Refactor [module] to extract shared utilities"
- "Create a data analysis script for [dataset]"
- "Add input validation to [API endpoint]"

### Run it

In a Claude Code session, type:

```
Use the orchestrator to add a health check endpoint with tests
```

**Say "use the orchestrator" explicitly** so Claude routes to the right
agent. After a few successful runs, Claude will learn to route
automatically.

### What happens next

The orchestrator will drive the task through the engine. Here is the
sequence you will see:

**1. Planning**

```
$ baton plan "Add a health check endpoint with tests" --save --explain
```

The engine outputs a plan with phases, agents, gates, risk level, and
budget. The orchestrator presents a summary and asks you to approve.

**2. Execution start**

```
$ baton execute start
```

The engine initializes state, creates a feature branch, and returns the
first action.

**3. Dispatch loop**

For each DISPATCH action, the orchestrator spawns a specialist agent:

```
ACTION: DISPATCH
  Agent: backend-engineer
  Step:  1.1
  Message: Implement health check endpoint

--- Delegation Prompt ---
[detailed instructions for the agent]
--- End Prompt ---
```

The orchestrator spawns the agent, waits for it to complete, records the
result:

```
$ baton execute record --step-id 1.1 --agent backend-engineer \
    --status complete --outcome "Added /health endpoint" \
    --files "app/routes/health.py"
```

**4. Gates**

Between phases, the engine runs QA checks:

```
ACTION: GATE
  Type: test
  Phase: 2
  Command: pytest tests/ -x --tb=short
```

The orchestrator runs the command and records the result:

```
$ baton execute gate --phase-id 2 --result pass
```

**5. Completion**

```
$ baton execute complete
```

The engine writes:
- Execution trace (`.claude/team-context/traces/`)
- Usage log (`.claude/team-context/usage-log.jsonl`)
- Retrospective (`.claude/team-context/retrospectives/`)

### Follow along

While the orchestrator works, you will see it:
- Present the plan summary for approval
- Create a feature branch
- Dispatch each agent with a detailed prompt
- Run gate checks between phases
- Commit after each agent completes
- Finalize with a summary of what was done

You can intervene at any time by typing instructions. The orchestrator
responds to course corrections mid-execution.

---

## Step 5: Using Agents Directly

Not every task needs the full orchestrator. For single-domain work, invoke
agents directly:

```
Use the test-engineer to add unit tests for the payment module
```

```
Use the code-reviewer to review the changes in this branch
```

```
Use the data-analyst to investigate our fleet utilization trends
```

```
Use the security-reviewer to audit our authentication flow
```

```
Use the architect to design the caching layer for the API
```

Agents invoked directly use their full agent definition but skip the
execution engine. No plan, no gates, no traces. Good for focused,
single-concern work.

### When to use the orchestrator vs. direct agents

| Situation | Approach |
|-----------|----------|
| Touches 3+ files across different domains | Use the orchestrator |
| Needs coordination between multiple specialists | Use the orchestrator |
| Requires QA gates between phases | Use the orchestrator |
| Needs traceability and audit trail | Use the orchestrator |
| Single file or single domain | Direct agent or work yourself |
| Quick question or analysis | Direct agent |

---

## Step 6: Set Up the PMO UI

The PMO (Portfolio Management Overlay) gives you a visual Kanban board
across all your baton-managed projects. It requires the API extras.

### Install API dependencies

```bash
pip install -e ".[api]"
```

### Register a project

```bash
baton pmo add \
    --id myproject \
    --name "My Project" \
    --path /path/to/project \
    --program core
```

### Sync data to central database

```bash
baton sync                     # Sync current project
baton sync --all               # Sync all registered projects
```

### Start the PMO server

```bash
baton pmo serve                # Starts on localhost:8741
```

Open `http://localhost:8741/pmo/` in your browser.

### Terminal-only alternative

```bash
baton pmo status               # Kanban board in the terminal
baton pmo health               # Program health bar summary
```

---

## Common Questions

### How do I see what the engine learned from my tasks?

```bash
baton patterns                  # Learned orchestration patterns
baton scores                    # Agent performance scorecards
baton budget --recommend        # Budget tier recommendations
baton retro                     # Task retrospectives
```

### How do I query across all my projects?

First sync, then query:

```bash
baton sync --all
baton cquery agents             # Agent reliability across projects
baton cquery costs              # Token costs by task type
baton cquery gaps               # Recurring knowledge gaps
baton cquery failures           # Project failure rates
```

You can also run ad-hoc SQL:

```bash
baton cquery "SELECT agent_name, COUNT(*) FROM step_results GROUP BY agent_name"
```

### How do I add domain knowledge for my agents?

Create a knowledge pack directory under `.claude/knowledge/`:

```
.claude/knowledge/
  my-domain/
    overview.md
    api-contracts.md
    business-rules.md
```

Then reference it in your plan:

```bash
baton plan "task" --save --knowledge-pack my-domain
```

Or have the talent-builder create one from your documentation:

```
Spawn the talent-builder agent. Onboard [DOMAIN] as a domain.
```

### How do I connect Azure DevOps work items?

```bash
baton source add ado \
    --name "My ADO" \
    --org myorg \
    --project MyProject \
    --pat-env ADO_PAT

baton source sync <source-id>   # Pull items into central.db
```

### How do I check the risk level of a task before running it?

```bash
baton classify "Migrate the production database schema"
```

The classifier returns the risk level (LOW/MEDIUM/HIGH), sensitivity
flags, and the guardrail preset that will be applied.

### Can I run multiple tasks at the same time?

Yes. Each `baton execute start` prints an `export BATON_TASK_ID=...` line.
Run that export in each terminal to bind it to its execution:

```bash
# Terminal 1
baton plan "task A" --save
baton execute start
export BATON_TASK_ID=<task-id-a>

# Terminal 2
baton plan "task B" --save
baton execute start
export BATON_TASK_ID=<task-id-b>
```

Resolution order: `--task-id` flag > `BATON_TASK_ID` env var >
`active-task-id.txt`.

### What if I want to skip the engine for a quick task?

Use `/gsd:quick` for fast ad-hoc tasks, or just describe the task
without saying "use the orchestrator":

```
Fix the typo in the login error message
```

Claude will handle it directly without planning or execution tracking.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Agents do not show up in `/agents` | Files are not in `.claude/agents/`. Check the path matches your install scope (user-level = `~/.claude/agents/`, project-level = `.claude/agents/`). |
| `baton` command not found | Run `pip install -e ".[dev]"` in the agent-baton directory. Verify with `which baton`. |
| Orchestrator skips the plan and starts coding | Say: "Stop. Run `baton plan` first, then present the plan before doing any work." |
| Agent writes to wrong files | `git checkout -- [file]` to revert. Re-delegate with stronger boundaries in the prompt. |
| Permission prompts on every action | Check that `permissionMode: auto-edit` is in the agent's YAML frontmatter and that `.claude/settings.json` has the hooks installed. |
| Session crashes during execution | Start a new Claude Code session and run `baton execute resume`. The engine recovers from saved state. |
| Rate limited mid-task | Start a new session, run `baton execute resume` to pick up where it left off. |
| `baton serve` fails with import error | Install API dependencies: `pip install -e ".[api]"` |
| Plan picks wrong agents for the task | Override with: `baton plan "task" --save --agents "agent1,agent2"` |
| Gate fails and blocks execution | Check the gate command output. Fix the issue, then record: `baton execute gate --phase-id N --result pass` |
| `central.db` is empty | Run `baton sync` to populate it from your project's `baton.db`. |

---

## Next Steps

Once you have completed a few orchestrated tasks:

1. **Review agent performance**: `baton scores` shows how each agent
   performed. `baton evolve` proposes prompt improvements for weak spots.

2. **Learn patterns**: `baton patterns --refresh` analyzes your usage
   history and identifies recurring orchestration patterns.

3. **Tune budgets**: `baton budget --recommend` adjusts budget tiers
   based on actual token usage.

4. **Set up cross-project visibility**: Sync all projects with
   `baton sync --all` and query with `baton cquery`.

5. **Create domain knowledge**: Use the talent-builder to create
   knowledge packs that make agents smarter about your specific domain.

6. **Read the architecture docs**: See `docs/architecture.md` for the
   full package layout and `docs/design-decisions.md` for the reasoning
   behind the architecture.
