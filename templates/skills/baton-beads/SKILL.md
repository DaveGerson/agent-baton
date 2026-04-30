---
name: baton-beads
description: |
  Bead memory workflows — create, query, link, close, promote, and graph
  agent discoveries, decisions, and warnings. Use when you need to persist
  structured knowledge across steps or tasks, track decision chains, or
  promote findings to permanent knowledge packs.
allowed-tools: Read, Glob, Grep, Bash
---

# Agent Baton — Bead Memory

Beads are structured agent memories that persist across steps and tasks.
They capture discoveries, decisions, warnings, and outcomes so future
agents build on past work instead of rediscovering it.

For the full CLI reference: `.claude/references/baton-engine.md`

## Bead Types

| Type | When to use |
|------|------------|
| `discovery` | Found something important (pattern, dependency, constraint) |
| `decision` | Made an architectural or design choice worth preserving |
| `warning` | Risk, bug, or concern that future steps should know about |
| `outcome` | Result of a completed step or gate |
| `planning` | Planning-phase insight that informs execution |

## Core Commands

```bash
# Create a bead (anchored to a task and step if in an execution)
baton beads create --type discovery \
    --content "Found circular dep between auth and user modules" \
    --agent architect --file auth.py --file user.py \
    --tag architecture --tag risk \
    [--task-id TASK_ID] [--step-id STEP_ID] [--confidence high]

# List beads (with filters)
baton beads list [--type warning] [--status open] [--task TASK_ID] [--tag TAG]

# Show a specific bead
baton beads show bd-a1b2

# Close a bead (mark as resolved)
baton beads close bd-a1b2 --summary "Resolved by extracting shared types"

# Check which beads are actionable (dependencies satisfied)
baton beads ready [--task TASK_ID]
```

## Linking Beads

Beads form a knowledge graph. Link them to show relationships:

```bash
baton beads link bd-a1b2 --relates-to bd-c3d4
baton beads link bd-a1b2 --contradicts bd-e5f6
baton beads link bd-a1b2 --extends bd-g7h8
baton beads link bd-a1b2 --blocks bd-i9j0
baton beads link bd-a1b2 --validates bd-k1l2
```

## Visualize and Maintain

```bash
# Show the bead dependency graph for a task
baton beads graph TASK_ID

# Promote a high-value bead to permanent knowledge
baton beads promote bd-a1b2 --pack project-context

# Archive old closed beads (default: 7 days)
baton beads cleanup [--ttl HOURS] [--dry-run]
```

## Anchoring Beads to Git and Sessions

Beads are most valuable when they're traceable to the commit and step
that produced them. The engine does this automatically when you're inside
a `baton execute` loop (task ID and step ID are inherited). Outside of
execution, anchor manually:

```bash
# Tie a bead to a specific task and step
baton beads create --type warning \
    --content "Migration 0042 is not idempotent — will fail on re-run" \
    --task-id 2026-04-30-auth-refactor-a1b2 --step-id 2.1 \
    --agent backend-engineer --file migrations/0042_user_schema.sql

# Record the commit that resolved a bead in its close summary
baton beads close bd-a1b2 --summary "Fixed in commit abc1234; added IF NOT EXISTS guard"
```

**After recording a step result** (`baton execute record --commit HASH`),
the commit is linked to the step. Beads created during that step are
transitively traceable: bead -> step -> commit -> diff.

**Git-bead hygiene:**
- Include commit hashes in close summaries so beads stay traceable after branch merges
- Use `--file` flags on creation so `baton beads list` can filter by affected path
- Run `baton beads cleanup --dry-run` before major merges to identify stale beads
- After rebases, bead git-notes survive if `notes.rewriteRef` is configured (install.sh Step 6 sets this with `--gastown`)

## Post-Interaction Context

Beads go stale when they capture the "what" but not the "so what."
After an agent interacts with a bead (acts on it, discovers it's wrong,
finds new context), enrich it immediately:

```bash
# Quick annotation — append a note without changing status (preferred for small updates)
baton beads annotate bd-a1b2 --note "Only triggered under lazy-loading; eager imports are safe"
baton beads annotate bd-a1b2 --note "Verified in load test" --agent auditor

# When closing, always include a substantive summary
baton beads close bd-a1b2 --summary "Resolved: extracted UserIdentity to shared_types.py; \
    lazy-loading path verified safe after import reorder in auth/__init__.py"

# For major corrections, create a linked bead to preserve the audit trail
baton beads create --type decision \
    --content "Original assessment was incorrect — the dep is intentional for cycle detection"
baton beads link bd-NEW --contradicts bd-a1b2
```

**When to annotate vs. extend:**
- `annotate` — quick context additions, corrections, status notes (lightweight, no new bead)
- `create` + `link --extends` — significant new findings that deserve their own bead ID
- `create` + `link --contradicts` — the original bead was wrong (preserves audit trail)

**Why this matters:** Beads without post-interaction notes become noise
after 2-3 tasks. Annotations and links keep the knowledge graph
accurate. The learning pipeline (`baton learn analyze`) weights
beads with richer context higher.

## When to Bead

- **Always bead** incidents and failures (CLAUDE.md mandates this)
- **Bead decisions** that would be expensive to re-derive
- **Bead discoveries** that affect downstream steps
- **Always enrich** beads with post-interaction context (`annotate`, extends, contradicts, close with summary)
- **Promote** beads that represent permanent project knowledge

## See Also

- `/baton-help` — Core CLI workflow reference
- `/baton-learn` — Learning pipeline (consumes bead data)
