---
name: baton-help
description: |
  Show the Agent Baton CLI reference. Use when you need to understand
  baton commands, the execution loop, action types, or troubleshoot
  engine errors. Also auto-triggers when Claude is unsure what baton is
  or how to use it.
allowed-tools: Read, Glob, Grep, Bash
---

# Agent Baton — Quick Reference

Agent Baton is an **installed Python CLI tool** (`baton`) that orchestrates
multi-agent execution plans for Claude Code.

Read the full CLI reference for command details:

```bash
cat .claude/references/baton-engine.md
```

If the reference file is missing, fall back to inline help:

```bash
baton --help
baton plan --help
baton execute --help
```

## Core Workflow

```
baton plan "task description" --save --explain   # Generate execution plan
baton execute start                               # Begin execution
baton execute next                                # Get next action
baton execute record --step-id ID --agent NAME \
    --status complete --outcome "summary"          # Record agent result
baton execute gate --phase-id N --result pass      # Record gate result
baton execute approve --phase-id N --result approve # Record approval
baton execute complete                             # Finalize
baton execute resume                               # Recover crashed session
```

## Action Types

The engine returns these action types from `baton execute start` and
`baton execute next`:

| Action | What to do |
|--------|------------|
| **DISPATCH** | Spawn the named agent via the Agent tool with the delegation prompt. Record the result with `baton execute record`. |
| **GATE** | Run the gate command in Bash. Record pass/fail with `baton execute gate`. |
| **APPROVAL** | Present the context to the user. Record their decision with `baton execute approve`. |
| **COMPLETE** | Run `baton execute complete` to finalize the execution. |
| **FAILED** | Report the failure to the user. Do not call complete. |
| **WAIT** | Parallel steps are still running. Call `baton execute next` again after recording outstanding steps. |

## Key Flags

- `baton plan --model opus` — set default model for dispatched agents
- `baton plan --task-type TYPE` — override auto-detected task type
- `baton plan --agents "a,b,c"` — override agent selection
- `baton plan --knowledge PATH` — attach knowledge documents
- `baton plan --knowledge-pack NAME` — attach knowledge packs
- `baton plan --intervention low|medium|high` — escalation threshold
- `baton execute amend --description "why" --add-phase "name:agent"` — amend plan mid-execution

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `baton: command not found` | Run `pip install -e .` from the agent-baton repo, or check PATH |
| `FileExistsError` on Windows | Update to latest agent-baton (persistence fix) |
| Wrong gate field name | Engine accepts both `gate_type` and `type` |
| Plan uses wrong agents | Re-run with `--agents` or `--task-type` override |
| Session crashed | `baton execute resume` picks up where it left off |
