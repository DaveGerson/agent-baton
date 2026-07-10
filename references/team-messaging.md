# Team Messaging and Shared Tasks

This reference documents the team-coordination surface available to
agents running inside a team step — the `baton team` CLI verbs (see
"Tools" below) — and how messages and shared tasks flow between
members.

## Overview

Every team step creates a persistent `Team` registered in `teams` with a
stable `team_id`. Agents in that team can:

- **Send messages** to their peers or to other teams.
- **Share tasks** on a team board that survives crashes and resumes.

Sub-teams are predefined in the plan and dispatched by the engine;
there is **no callable tool** for standing one up mid-flight — see
"Standing up a sub-team" under Tools.

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
subsequent dispatches. An explicit `baton team read` acks what it
returns for the same reason (pass `--no-ack` to peek without
consuming). Each member's ack is scoped to that member —
broadcast messages can be acked by one recipient without suppressing
delivery to others.

## Scope

For MVP, all messaging is **scoped to a single `task_id`**. Cross-task
messaging is not supported — pending tasks wait for explicit
integration (Phase 6 of the spec).

## Tools

Agents coordinate through the **`baton team` CLI**, invoked via the
`Bash` tool. This is the only callable surface a dispatched team member
has — the Python-level functions in
`agent_baton.core.engine.team_tools` (`team_send_message`,
`team_add_task`, `team_claim_task`, `team_complete_task`) are internal
engine APIs, **not** tools an agent can call directly. Do not narrate a
`team_send_message(...)`-style call; shell out to the CLI instead. Full
contract (authorization, concurrency, idempotency, failure taxonomy):
`docs/internal/team-runtime-contract.md`.

Identity: pass `--member-id <your-member-id>` on every call
(`$BATON_TEAM_MEMBER_ID` is set automatically on daemon/worktree
dispatches, but the flag works unconditionally). `--task-id` can usually
be omitted (`$BATON_TASK_ID` is set). Add `--json` for parseable output.

### `baton team send`

Send a message to a team or a specific member. Prints the
`message_bead_id` of the new message.

```bash
baton team send --from-team team-billing --member-id 1.1.a \
  --to-team team-search --to-member 1.2.a \
  --subject "Schema change" \
  --body "I updated the Order.id type to UUID in 1.1.b. See bd-c4a2." --json
# omit --to-member for a broadcast to the whole --to-team
```

### `baton team update` (create mode)

Append a task to the team board. Prints the new `task_bead_id`.
Unclaimed tasks are visible to every member of the team; claimed tasks
hide from everyone except the claimer.

```bash
baton team update --team-id team-billing --member-id 1.1.a \
  --title "Write migration for Order.id UUID" \
  --detail "Affects billing + search; coordinate before deploy." --json
# retry-safe: add --idempotency-key <key> so an ambiguous failure can be
# retried without creating a duplicate task
```

### `baton team claim`

Claim an open task. Optimistic concurrency: fails with exit code `4` if
another member already holds the claim (re-run `baton team list`, then
retry against fresh state). Pass `--allow-reassign` to force a takeover
of a stalled task.

```bash
baton team claim --team-id team-billing --member-id 1.1.b \
  --task-bead-id bd-1234 --json
```

### `baton team update` (complete mode)

Close a task with an outcome summary. Once closed, the task is no
longer listed as open but remains in the audit trail.

```bash
baton team update --team-id team-billing --member-id 1.1.b \
  --task-bead-id bd-1234 --status complete --outcome "Migration shipped." --json
```

### `baton team list` / `baton team read`

Pull-based board and mailbox reads:

```bash
baton team list --team-id team-billing --member-id 1.1.b --json
# --status open|claimed|done to filter; --resource teams for child teams;
# --all for the unfiltered lead/observer-wide view

baton team read --team-id team-billing --member-id 1.1.b --json
# acks what it returns by default; --no-ack peeks without consuming
```

Exit codes are meaningful: `2` = bad input (check for a typo), `3` =
role not authorized, `4` = claim conflict (refresh and retry), `5` =
team backend not configured (stop and report — do not retry).

### Standing up a sub-team — NO callable tool

There is **no callable `team_dispatch` surface in this runtime** — no
CLI verb, no MCP tool. A lead cannot stand up a sub-team mid-flight; do
not narrate or simulate a `team_dispatch(...)` call. Sub-teams are
predefined in the plan (a `TeamMember` with a non-empty `sub_team`) and
dispatched by the engine automatically. If the work genuinely needs an
unplanned sub-team, say so explicitly in your outcome (e.g. a
`BEAD_WARNING:`) so a human or the planner can add it.

## Patterns

### Cross-team coordination

```bash
# team-billing lead (1.1.a):
baton team send --from-team team-billing --member-id 1.1.a \
  --to-team team-search --to-member 1.2.a \
  --subject "Order.id is UUID now" \
  --body "Please update the search index writer."
```

The message lands in `1.2.a`'s next dispatch prompt under
"Prior Discoveries & Messages" (or an explicit `baton team read`).
No interrupt, no blocking.

### Discover-and-delegate

```bash
# team-billing lead (1.1.a) investigates a timeout, then:
baton team update --team-id team-billing --member-id 1.1.a \
  --title "fix retry loop in auth" --detail "..."

# Any member of team-billing on their next dispatch (or via
# `baton team list`) sees the task and can take it on:
baton team claim --team-id team-billing --member-id 1.1.b \
  --task-bead-id <id>
```

### On-the-fly decomposition

Not available as a callable tool — see "Standing up a sub-team" above.
When a plan predefines a sub-team, the engine registers a child team
under the parent, dispatches the sub-members alongside the lead's own
work, and merges the outcomes via the enclosing step's synthesis on
completion.
