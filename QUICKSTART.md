# Quickstart — Do This Tomorrow

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

### Option B: PowerShell (Windows)

```powershell
cd path\to\orchestrator-v2
powershell -ExecutionPolicy Bypass -File install.ps1
```

Choose option 2 (project-level) when prompted.

### Option C: Bash (if available in your terminal)

```bash
cd /path/to/orchestrator-v2
chmod +x install.sh
./install.sh
```

Choose option 2 (project-level) when prompted.

## Verify (30 seconds)

In Claude Code:
```
/agents
```

You should see ~19 agents listed including `orchestrator`, `auditor`,
`backend-engineer`, etc.

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
2. Research your codebase
3. Present an execution plan with agents, steps, and boundaries
4. Ask you to approve before dispatching agents
5. Delegate to specialist agents one by one
6. Commit work and update the mission log as it goes

If it skips steps 1-3 and jumps straight to coding, say:
"Stop. Read all files in .claude/references/ first, then present an
execution plan before doing any work."

## If Something Goes Wrong

| Problem | Fix |
|---------|-----|
| Agents don't show up in `/agents` | Files aren't in `.claude/agents/` — check the path |
| Orchestrator doesn't read references | Say "Read all files in .claude/references/ first" |
| Orchestrator skips the plan | Say "Write an execution plan to .claude/team-context/plan.md before delegating" |
| Agent writes to wrong files | `git checkout -- [file]` to revert, re-delegate with stronger boundaries |
| Permission prompts on every action | Check `permissionMode: auto-edit` is in the agent's frontmatter |
| Rate limited mid-task | The mission log at `.claude/team-context/mission-log.md` has what completed. Start a new session and say "Resume the interrupted task — read .claude/team-context/" |
