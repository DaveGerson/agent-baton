# Team Messaging and Shared Tasks

This reference documents the `team_*` tools available to agents running
inside a team step, and how messages and shared tasks flow between
members.

## Overview

Every team step creates a persistent `Team` registered in `teams` with a
stable `team_id`. Agents in that team can:

- **Send messages** to their peers or to other teams.
- **Share tasks** on a team board that survives crashes and resumes.
- **Stand up sub-teams** (leads only) when the work needs further
  decomposition.

Messages and tasks ride on the existing Bead store using new `bead_type`
values (`message`, `task`, `message_ack`). There is no new table and no
new runtime channel — everything is append-only to the audit trail.

## Addressing

All addressing is expressed as tags on the underlying bead. Callers use
the high-level tool APIs; the tag encoding is an internal detail.

| Tag | Meaning |
|-----|---------|
| `team=<team_id>` | Scopes a bead to a team. |
| `to_member=<member_id>` | Direct message to one member. |
| `to_team=<team_id>` | Broadcast to every member of a team. |
| `from_member=<member_id>` | Author of a message or task. |
| `from_team=<team_id>` | Source team for cross-team messages. |
| `claimed_by=<member_id>` | Marks a task as claimed. |
| `ack_of=<message_bead_id>` | On a `message_ack` bead, the message it acknowledges. |

## Delivery timing

**Next dispatch, not real-time.** A message sent to member `X` is not an
interrupt — `X` continues running to the end of their current turn. When
`X` is next dispatched (either as part of a new step, or as part of a
re-dispatch on the same step), the engine injects unread messages into
the prompt via `BeadSelector.select_for_team_member`.

Implications:

- A long-running agent will not see a message until it finishes and is
  re-dispatched.
- Messages are first-class durable state — they survive engine restarts
  and are available in the UI and audit trail.
- There is no reply threading. Messages are a flat stream; if you need a
  conversation, open a task instead.

## Ack semantics

When a member receives a message, the engine records a `message_ack`
bead on their next outcome so the message is not re-delivered in
subsequent dispatches. Each member's ack is scoped to that member —
broadcast messages can be acked by one recipient without suppressing
delivery to others.

## Scope

For MVP, all messaging is **scoped to a single `task_id`**. Cross-task
messaging is not supported — pending tasks wait for explicit
integration (Phase 6 of the spec).

## Tools

### `team_send_message(to_team, to_member?, subject, body)`

Send a message to a team or a specific member. Returns the `bead_id`
of the new message.

```python
team_send_message(
    to_team="team-search",
    to_member="1.2.a",           # omit or None for broadcast
    subject="Schema change",
    body="I updated the Order.id type to UUID in 1.1.b. See bd-c4a2.",
)
```

### `team_add_task(title, detail?)`

Append a task to the caller's team board. Returns the `bead_id`.
Unclaimed tasks are visible to every member of the team; claimed tasks
hide from everyone except the claimer.

```python
team_add_task(
    title="Write migration for Order.id UUID",
    detail="Affects billing + search; coordinate before deploy.",
)
```

### `team_claim_task(task_bead_id)`

Claim an open task. Replaces any existing claim — last-writer-wins.

### `team_complete_task(task_bead_id, outcome)`

Close a task with an outcome summary. Once closed, the task is no
longer listed as open but remains in the audit trail.

### `team_dispatch(members, synthesis?)` — LEAD-ONLY

Stand up a sub-team under the caller. Non-lead callers receive a
`TeamToolError`. The new sub-team members are dispatched on the next
engine call; the caller's own outcome plus the sub-team's outcomes are
merged by `synthesis`.

```python
team_dispatch(
    members=[
        {"agent_name": "backend-engineer", "task_description": "write adapter"},
        {"agent_name": "test-engineer",   "task_description": "write tests"},
    ],
    synthesis={"strategy": "merge_files"},
)
```

## Patterns

### Cross-team coordination

```
team-billing (lead 1.1.a) → team_send_message(
    to_team="team-search", to_member="1.2.a",
    subject="Order.id is UUID now",
    body="Please update the search index writer.",
)
```

The message lands in `1.2.a`'s next dispatch prompt under
"Prior Discoveries & Messages". No interrupt, no blocking.

### Discover-and-delegate

```
team-billing lead (1.1.a):
  — investigate timeout
  — team_add_task("fix retry loop in auth", detail="...")

Any member of team-billing on their next dispatch sees the task and can
team_claim_task(<id>) to take it on.
```

### On-the-fly decomposition

```
team-billing lead (1.1.a) discovers the work is three distinct pieces:
  team_dispatch(members=[
      {"agent_name": "backend-engineer", "member_id": "1.1.a.api"},
      {"agent_name": "backend-engineer", "member_id": "1.1.a.db"},
      {"agent_name": "test-engineer",    "member_id": "1.1.a.test"},
  ])

The engine registers a child team under team-billing, dispatches the
three sub-members alongside the lead's own work, and merges the
outcomes via the enclosing step's synthesis on completion.
```
