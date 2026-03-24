# Agent Baton — Quickstart

## Install (5 minutes)

### Option A: Let Claude Code do it for you

Start a Claude Code session in your project directory and say:

```
I have an orchestrator skill package at [path to extracted folder].
Please copy all .md files from its agents/ directory into .claude/agents/,
all .md files from its references/ directory into .claude/references/,
create a .claude/team-context/ directory, and copy the CLAUDE.md file to
my project root. Then run /agents to verify they loaded.
```

Claude Code will do the file operations for you. No scripts needed.

### Option B: Bash (Linux/macOS)

```bash
cd /path/to/agent-baton
chmod +x scripts/install.sh
scripts/install.sh
```

Choose option 1 (user-level) for global install, or option 2 (project-level)
for a single project.

### Option C: PowerShell (Windows)

```powershell
cd path\to\agent-baton
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Choose option 1 (user-level) for global install, or option 2 (project-level)
for a single project.

### Option D: Python package (required for engine-driven execution)

```bash
cd /path/to/agent-baton
pip install -e ".[dev]"
```

This installs the `baton` CLI with 31 commands for planning, execution,
observability, governance, and distribution. The orchestrator uses the
`baton` CLI to drive tasks through the execution engine.

## Verify (30 seconds)

In Claude Code:
```
/agents
```

You should see ~19 agents listed including `orchestrator`, `auditor`,
`backend-engineer`, etc.

Verify the CLI:
```bash
baton agents
baton validate agents/
```

## First Test Run

Pick something real but low-stakes. Examples:

- "Use the orchestrator to build a simple health check API endpoint
  with tests and documentation"
- "Use the orchestrator to create a data analysis script that summarizes
  [some dataset you have]"
- "Use the orchestrator to refactor [a module you've been meaning to clean up]"

**Say "Use the orchestrator" explicitly** for your first few runs so it
definitely routes to the right agent.

## What to Watch For

The orchestrator drives tasks through the execution engine. For complex
tasks (Level 3), the workflow is:

1. Plan via `baton plan "task description" --save --explain`
2. Start execution via `baton execute start`
3. Drive the loop: the engine returns DISPATCH, GATE, or COMPLETE actions
4. For DISPATCH: spawn the specialist agent with the provided prompt
5. Record results via `baton execute record`
6. Run QA gates between phases via `baton execute gate`
7. Finalize via `baton execute complete`
8. The engine writes traces, usage logs, and retrospectives automatically

For simpler tasks (Level 1-2), the orchestrator dispatches agents directly
without the engine — no ceremony needed for small changes.

If the orchestrator jumps straight to coding without planning, say:
"Stop. Run `baton plan` first, then present the plan before doing any work."

## Engine CLI Reference

```bash
# Create a plan (engine handles agent routing, risk, budget, sequencing)
baton plan "Add input validation to the API" --save --explain

# Start execution (returns first DISPATCH action)
baton execute start

# Get all dispatchable actions for parallel execution
baton execute next --all

# Mark steps as in-flight before spawning agents
baton execute dispatched --step-id 1.1 --agent backend-engineer

# After each agent completes, record the result
baton execute record --step-id 1.1 --agent backend-engineer --status complete

# When a gate is reached, run it and record the result
baton execute gate --phase-id 2 --result pass

# Get the next action (single step)
baton execute next

# If your session crashes, resume where you left off
baton execute resume

# When all phases complete, finalize
baton execute complete
```

The engine automatically writes traces, usage logs, and retrospectives that
feed the learning pipeline. Future plans improve based on past execution data.

## If Something Goes Wrong

| Problem | Fix |
|---------|-----|
| Agents don't show up in `/agents` | Files aren't in `.claude/agents/` — check the path |
| `baton` command not found | Run `pip install -e ".[dev]"` in the agent-baton directory |
| Orchestrator skips the plan | Say "Run `baton plan` first, then present the plan before doing any work" |
| Agent writes to wrong files | `git checkout -- [file]` to revert, re-delegate with stronger boundaries |
| Permission prompts on every action | Check `permissionMode: auto-edit` is in the agent's frontmatter |
| Session crash during execution | Run `baton execute resume` — the engine recovers from saved state |
| Rate limited mid-task | Start a new session, run `baton execute resume` to pick up where it left off |
