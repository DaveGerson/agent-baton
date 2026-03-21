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

### Option D: Python package (for CLI tools)

```bash
cd /path/to/agent-baton
pip install -e ".[dev]"
```

This installs the `baton` CLI with 31 commands for planning, execution,
observability, governance, and distribution.

## Verify (30 seconds)

In Claude Code:
```
/agents
```

You should see ~19 agents listed including `orchestrator`, `auditor`,
`backend-engineer`, etc.

If you installed the Python package:
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

The orchestrator should:
1. Read the reference docs in `.claude/references/`
2. Research your codebase and detect the project stack
3. Create an execution plan with phases, agents, and QA gates
4. Ask you to approve before dispatching agents
5. Delegate to specialist agents (in parallel when independent)
6. Run QA gates (build, test, lint) between phases
7. Commit work and update the mission log as it goes
8. Write a trace, usage log, and retrospective on completion

If it skips steps 1-3 and jumps straight to coding, say:
"Stop. Read all files in .claude/references/ first, then present an
execution plan before doing any work."

## Engine-Driven Execution (Advanced)

If you installed the Python package, the orchestrator can use the execution
engine for persistent, crash-recoverable task execution:

```bash
# Create a plan
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
| Orchestrator doesn't read references | Say "Read all files in .claude/references/ first" |
| Orchestrator skips the plan | Say "Write an execution plan to .claude/team-context/plan.md before delegating" |
| Agent writes to wrong files | `git checkout -- [file]` to revert, re-delegate with stronger boundaries |
| Permission prompts on every action | Check `permissionMode: auto-edit` is in the agent's frontmatter |
| Rate limited mid-task | The mission log at `.claude/team-context/mission-log.md` has what completed. Start a new session and say "Resume the interrupted task — read .claude/team-context/" |
| Session crash during execution | Run `baton execute resume` — the engine recovers from saved state |
| `baton` command not found | Run `pip install -e ".[dev]"` in the agent-baton directory |
