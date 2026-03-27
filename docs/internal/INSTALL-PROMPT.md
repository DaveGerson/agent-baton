# Agent Baton — Installation Prompt for Claude Code

Copy and paste the prompt below into a Claude Code session in your project
directory. Replace `[PATH]` with the actual path to the agent-baton repository.

---

## Prompt

```
I want to install the Agent Baton multi-agent orchestration system into this
project. The agent-baton source is at [PATH].

Please do the following:

1. Copy all .md files from [PATH]/agents/ into .claude/agents/
2. Copy all .md files from [PATH]/references/ into .claude/references/
3. Create the directory .claude/team-context/ if it doesn't exist
4. Copy [PATH]/templates/CLAUDE.md to my project root as CLAUDE.md
   — if CLAUDE.md already exists, merge the orchestrator rules into
   my existing file rather than replacing it
5. Copy [PATH]/templates/settings.json to .claude/settings.json
   — if .claude/settings.json already exists, merge only the "hooks"
   key from the source into my existing settings, preserving all my
   other keys (permissions, mcpServers, env, etc.)

After copying, verify the installation:
- Run /agents and confirm ~19 agents are listed
- Read .claude/references/decision-framework.md to confirm references
  are accessible
- Confirm .claude/team-context/ exists and is writable

Do not modify any of my existing source code. Only create/modify files
in .claude/ and the root CLAUDE.md.
```

---

## Alternative: Use the CLI installer

If you have Python 3.10+ available:

```bash
cd [PATH]
pip install -e ".[dev]"
baton install --scope project --source [PATH] --upgrade --verify
```

This handles settings merge, agent validation, and post-install verification
automatically.

---

## Alternative: Use the shell script

```bash
cd [PATH]
scripts/install.sh
```

Choose option 2 (project-level) when prompted. Pass `--upgrade` if you are
updating an existing installation.

---

## After installation

Test with a low-stakes task:

```
Use the orchestrator to [describe a real but low-stakes task for your project]
```

The orchestrator will read the reference procedures, research your codebase,
present a plan, and delegate to specialist agents. Say "Use the orchestrator"
explicitly for your first few runs so Claude Code routes to the right agent.
