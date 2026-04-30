---
name: baton-learn
description: |
  Learning pipeline — analyze execution patterns, detect recurring issues,
  apply fixes, run improvement cycles, and review agent performance. Use
  after completing executions to close the feedback loop and make the
  system improve over time.
allowed-tools: Read, Glob, Grep, Bash
---

# Agent Baton — Learning Pipeline

The learning pipeline analyzes execution history to detect patterns,
diagnose recurring issues, and apply fixes automatically. It's the
closed loop that makes agent orchestration improve over time.

For the full CLI reference: `.claude/references/baton-engine.md`

## Quick Start

After completing a few baton executions, run the learning cycle:

```bash
# See current learning status
baton learn status

# Run analysis — detects patterns, computes confidence, proposes fixes
baton learn analyze

# Apply all safe fixes (auto-apply threshold met)
baton learn apply --all-safe

# Or apply a specific fix
baton learn apply --issue ISS-001
```

## Issue Types

The analyzer detects these recurring issues:

| Type | What it catches |
|------|----------------|
| `routing_mismatch` | Wrong agent routed to a task type |
| `agent_degradation` | Agent quality declining over time |
| `knowledge_gap` | Missing domain knowledge causing failures |
| `roster_bloat` | Underused agents that should be consolidated |
| `gate_mismatch` | Gates too strict or too loose |
| `pattern_drift` | Execution patterns diverging from learned baselines |
| `prompt_evolution` | Agent prompts that need updating |

## Full Improvement Cycle

```bash
# Run the complete cycle: analyze → propose → apply → experiment
baton learn run-cycle [--run] [--dry-run]

# Or use the broader improvement command
baton learn improve --run
```

## Interactive Review

```bash
# Walk through issues one at a time with evidence
baton learn interview [--type routing_mismatch] [--severity high]

# View resolution history
baton learn history [--limit 20]

# Reopen a resolved issue
baton learn reset --issue ISS-001
```

## Performance Visibility

```bash
# Agent performance scorecards
baton scores [--agent architect] [--trends] [--teams]

# Execution retrospectives
baton retro [TASK_ID]

# Learned orchestration patterns
baton patterns [--refresh] [--recommendations]

# Token usage and cost
baton usage
```

## When to Use

| Trigger | Command |
|---------|---------|
| After 3-5 executions | `baton learn analyze` — enough data for patterns |
| After a failed execution | `baton learn status` — check if it's a known issue |
| Weekly maintenance | `baton learn run-cycle --run` — full improvement cycle |
| Agent seems to underperform | `baton scores --agent NAME --trends` — check trajectory |
| After applying fixes | `baton learn improve --experiments` — monitor experiments |

## How Fixes Are Applied

`baton learn apply` writes corrections to `learned-overrides.json`,
which the router and planner consume on the next `baton plan`. Fixes
are non-destructive — they override defaults without modifying agent
definitions.

## See Also

- `/baton-help` — Core CLI workflow reference
- `/baton-beads` — Bead memory (learning consumes bead data)
