# Team Runtime Contract — the callable boundary for `team_*` tools

**Status:** Draft
**Step:** Phase 4, 4.1 (architect) — agent-baton middle-manager hardening plan
**Scope:** `agent_baton/core/engine/{team_tools,team_registry,team_board,team_backends}.py`,
`agent_baton/models/execution.py`, `agents/team-lead.md`, `references/team-messaging.md`.
**Non-goals of this document:** it does not implement the CLI subcommands or wire the
advertised-tools invariant into `dispatcher.py`'s prompt building — see §9 (Follow-up work) for
the enumerated implementation steps this design hands off to.

---

## 1. The problem this document fixes

`agents/team-lead.md` and `references/team-messaging.md` currently document five
`team_*` "tools" — `team_send_message`, `team_add_task`, `team_claim_task`,
`team_complete_task`, `team_dispatch` — as if a dispatched Claude Code agent can
simply call them, e.g.:

```python
team_send_message(to_team="team-search", subject="Schema change", body="...")
```

**No such tool is registered anywhere.** `agents/team-lead.md`'s frontmatter grants
only `Read, Glob, Grep, Edit, Write, Bash`. The Python functions in
`agent_baton/core/engine/team_tools.py` are real and tested (`tests/test_team_tools.py`),
but nothing on the path from "Claude Code subprocess launched by `ClaudeCodeLauncher`"
to "those Python functions execute" exists. This is **prompt fiction**: the agent
reads an instruction it has no mechanical way to satisfy, and — because Claude
Code agents are generally capable of *simulating* a tool call in prose when no
real tool answers to that name — a team-lead transcript can *look* like
coordination happened when nothing was persisted.

This document specifies the fix: a concrete, testable exposure mechanism (§2),
the exact tool schemas and authorization rules (§4–§6), and the synthesis
state machine that governs how team-member outcomes become a step result
(§8). §9 lists the implementation steps this hands off (not all in scope for
this step — see the allowed-paths note in §9).

---

## 2. Exposure mechanism — decision

### 2.1 Options considered

| Option | How it would work | Why not chosen as primary |
|---|---|---|
| **Prompt fiction (status quo)** | Document the tool in the agent's system prompt; hope the model role-plays it correctly and the engine parses `TOOL_CALL:`-style text out of the outcome. | This is the defect being fixed. No persistence guarantee, no authorization, no failure signal distinguishable from "the agent decided not to". |
| **Local MCP server** | Ship a stdio MCP server (`baton-team-mcp`) exposing the five tools; launch it per-dispatch via `ClaudeCodeLauncher.launch(..., mcp_servers=[...])` → `--mcp-config`, which **already exists** in `claude_launcher.py` (`_build_command`, `dispatcher.py:1147` already threads `step.mcp_servers` through). | Two structural blockers specific to *this* codebase: (1) an agent must declare `mcpServers:` in its frontmatter for Claude Code to load a configured server for that agent, and `agents/CLAUDE.md` documents that Agent Teams (`ClaudeTeamsBackend`) **does not honor `mcpServers:` frontmatter for teammates at all** — so the same tool surface would work under the `worktree` backend and silently vanish under `claude-teams`, exactly the "advertised tools don't match capabilities" bug this document exists to prevent. (2) it adds a second long-lived subprocess per dispatched team member, with its own handshake/timeout/crash semantics to design and test, for no capability the CLI can't already provide. |
| **Structured Baton CLI, invoked via the already-granted `Bash` tool** *(chosen)* | `baton team <verb> --json ...` subcommands. `team-lead.md` already grants `Bash`; every team member agent that can write code already has `Bash`. No new tool grant, no new subprocess-per-dispatch, works identically under both `WorktreeTeamBackend` and `ClaudeTeamsBackend` (the CLI doesn't care how the parent process was spawned — it talks to the same `baton.db` via `BATON_DB_PATH`/`BATON_TASK_ID`, which `claude_launcher.py`'s `_DEFAULT_ENV_PASSTHROUGH` already forwards to every launched subprocess). | — |

### 2.2 Decision

**Primary and only in-scope mechanism: a structured Baton CLI surface (`baton team
list|claim|update|send|read`), invoked by the dispatched agent through the `Bash`
tool it is already granted.** Each subcommand:

- Accepts flags for every schema field in §4, plus `--json` for machine-readable
  output (default: human-readable table, for interactive debugging).
- Exits `0` on success (JSON result on stdout), a documented non-zero code on
  failure (§7.3) with a one-line error on stderr.
- Resolves `task_id` from `--task-id` → `$BATON_TASK_ID` → the active-task
  pointer, matching the existing resolution order `baton execute` already
  uses (`execute.py`'s `_RESUMABLE_STATUSES` active-task lookup) — a team
  member never needs to know or pass its own `task_id` explicitly.
- Resolves the caller's `member_id` from `$BATON_TEAM_MEMBER_ID` (new env var,
  §9.1) when `--member-id` is omitted, so a team member's prompt does not need
  to hand-transcribe its own ID into every call (a source of typos today).

MCP remains documented as a **future alternative**, not a competing mechanism:
if a later step adds first-class MCP support to `ClaudeTeamsBackend` (i.e. the
Anthropic-side gap in `agents/CLAUDE.md`'s teammate-safety note closes), the CLI
subcommands become trivial to also expose as MCP tool wrappers (`baton team
list` and an MCP `team_list` tool would call the identical Python functions in
§3) — the schemas and authorization matrix in this document do not change
either way. This is why the underlying Python implementation (§3) is kept
transport-agnostic: it never assumes CLI argv or MCP JSON-RPC, only typed
Python kwargs.

### 2.3 The "advertised tools exactly match capabilities" invariant

A dispatch prompt (or, later, CLI `--help` / MCP `tools/list`) must **never**
advertise a tool name the calling member cannot actually invoke. Two failure
modes this rules out:

1. **Advertising a tool with no backing implementation** (the current bug —
   `team-lead.md` names five tools; only the underlying Python functions
   existed, no callable surface).
2. **Advertising a tool the caller's role is not authorized for** — e.g. an
   `implementer` being told it can call `team_dispatch`.

The single source of truth for "what may this role call" is
`agent_baton.core.engine.team_tools.advertised_team_tools_for_role(role) ->
list[str]`, backed by `TEAM_TOOL_NAMES` (the closed set: `team_list`,
`team_claim`, `team_update`, `team_send`, `team_read`, `team_dispatch`) and
the authorization matrix in §5. Any future code that renders a dispatch
prompt's "Tools" section (`dispatcher.py`) or builds a CLI `--help` string
must call this function rather than hard-coding a tool list — that is the
concrete mechanism that keeps the advertised surface from drifting from the
implemented one. This function and its backing data are landed in this step
(`agent_baton/core/engine/team_tools.py`); wiring `dispatcher.py` to call it
is deferred (§9.1) because `dispatcher.py` is outside this step's allowed
paths.

---

## 3. Implementation layering

```
   Claude Code subprocess (team member)
            │  Bash tool
            ▼
   `baton team <verb> --json ...`            (CLI — §9.1, deferred)
            │
            ▼
   agent_baton.core.engine.team_tools         (THIS STEP — canonical Python
     team_list / team_claim / team_update /    tool functions; typed kwargs,
     team_send / team_read / team_dispatch     TeamToolError family)
            │
            ▼
   TeamBoard (agent_baton.core.engine.team_board)     TeamRegistry (…team_registry)
     — task/message beads over BeadStore                — team identity, status CAS
```

This step lands the bottom two layers (already-existing modules, extended)
plus the typed contract module (`team_tools.py`) a CLI or MCP layer will call
into unchanged. The legacy functions (`team_send_message`, `team_add_task`,
`team_claim_task`, `team_complete_task`) remain, byte-for-byte behavior
compatible, so no existing caller or test breaks; the canonical five-tool
surface is additive.

### 3.1 Why five tool names, not five-to-one mapping of the legacy functions

`team_list` / `team_claim` / `team_update` / `team_send` / `team_read` is a
resource-oriented collapse of the legacy per-action names:

| Canonical tool | Resource | Legacy equivalent(s) |
|---|---|---|
| `team_list` | task board (`resource="tasks"`, default) or team roster (`resource="teams"`) | *(new — no prior read path)* |
| `team_claim` | task board | `team_claim_task`, now with optimistic concurrency (§6) |
| `team_update` | task board | `team_add_task` (create mode) + `team_complete_task` (complete mode), unified |
| `team_send` | mailbox | `team_send_message` |
| `team_read` | mailbox | *(new — mailbox was previously push-only, injected at next dispatch)* |

The alternative reading — `team_list` enumerates team **members** — was
considered and rejected: roster information (`member_id`, `agent_name`,
`role`, `task_description`) is already present in full in every dispatched
member's own prompt (`PlanStep.team` flattened via
`_flatten_team_members`), so a runtime tool to re-fetch it would be pure
redundancy. `team_list(resource="teams")` still exposes *sub-team*
enumeration (child teams registered via `team_dispatch`) because that
**is** live, mutable state a member's static prompt cannot see.

---

## 4. Callable tool schemas

All five canonical tools are implemented in
`agent_baton/core/engine/team_tools.py`. Every call is validated in this
order: (1) team exists (`_require_team`), (2) member exists in the plan
(`_require_member`), (3) role is authorized for the tool (§5,
`authorize_team_tool`) — a call that fails step 1 or 2 raises a plain
`TeamToolError`; a call that fails step 3 raises `TeamAuthorizationError`
(a `TeamToolError` subclass, so existing `except TeamToolError` callers are
unaffected).

### 4.1 `team_list`

```
team_list(
    task_id: str, team_id: str,
    member_id: str | None = None,       # omit for lead/observer-wide view
    resource: "tasks" | "teams" = "tasks",
    status: "open" | "claimed" | "done" | None = None,   # tasks only
    limit: int = 100,
) -> list[dict]
```

- `resource="tasks"`, `member_id` given: unclaimed tasks + tasks claimed by
  `member_id` (peers' claims hidden) — matches `TeamBoard.open_tasks_for_team`.
- `resource="tasks"`, `status="done"`: closed tasks (`TeamBoard.done_tasks_for_team`,
  new in this step — `open_tasks_for_team` never returns closed beads, so
  "done" was previously unlistable via any board method).
- `resource="teams"`: child teams of `team_id` (`TeamRegistry.child_teams`).

Task row shape (`_task_bead_to_dict`):
```json
{"task_bead_id": "bd-a1b2", "team_id": "team-1.1", "author_member_id": "1.1.a",
 "title": "...", "detail": "...", "status": "open|claimed|done",
 "claimed_by": "1.1.b" | null, "created_at": "2026-07-10T12:00:00Z"}
```

### 4.2 `team_claim`

```
team_claim(
    task_id: str, team_id: str, task_bead_id: str, member_id: str,
    allow_reassign: bool = False,
) -> {"task_bead_id": str, "claimed_by": str}
```

Optimistic concurrency by default (§6.1). Raises `TeamConcurrencyError`
(`TeamToolError` subclass) when another member already holds the claim and
`allow_reassign=False`.

### 4.3 `team_update`

```
team_update(
    task_id: str, team_id: str, member_id: str,
    task_bead_id: str | None = None,     # None = create mode
    title: str | None = None, detail: str = "",
    status: str | None = None,           # "complete" for complete mode
    outcome: str = "",
    idempotency_key: str | None = None,  # create mode only
    parent_task_bead_id: str | None = None,
) -> {"task_bead_id": str, "status": "open" | "done"}
```

Two supported transitions only (§4.3.1 explains why): create
(`task_bead_id is None`, requires `title`) and complete (`task_bead_id`
given, `status="complete"`, requires `outcome`). Any other
`(task_bead_id, status)` combination raises `TeamToolError` — e.g.
`status="blocked"` is explicitly rejected, not silently accepted-and-ignored.

#### 4.3.1 Why only create/complete in this version

A general task-state-machine (`open → blocked → open → claimed → done`,
reassignment, reopening a done task) is real future work, but every
additional transition multiplies the conflict surface with `team_claim`'s
optimistic concurrency (what does "claim a blocked task" mean? does
completing an unclaimed task auto-claim it?). Scoping this version to the
two transitions the existing `team_add_task`/`team_complete_task` legacy
tools already cover keeps the contract's failure modes enumerable and
matches what `team-lead.md`'s documented usage patterns (§ "Coordinate via
the board") actually need today. Extending the transition set is explicit
follow-up work (§9), not a silent gap — the `TeamToolError` on an
unsupported transition is the fail-closed signal.

### 4.4 `team_send`

```
team_send(
    task_id: str, from_team: str, from_member: str,
    to_team: str, to_member: str | None,   # None = broadcast to to_team
    subject: str, body: str,
) -> {"message_bead_id": str}
```

Thin authorized wrapper over the legacy `team_send_message`. Delivery
semantics unchanged from `references/team-messaging.md` (§ "Delivery
timing") — see §6.2 for how `team_read` changes the read side without
touching the write side.

### 4.5 `team_read`

```
team_read(
    task_id: str, team_id: str, member_id: str,
    limit: int = 100, ack: bool = True,
) -> list[dict]
```

Message row shape (`_message_bead_to_dict`):
```json
{"message_bead_id": "bd-c4a2", "from_team": "team-1.1", "from_member": "1.1.a",
 "to_team": "team-1.2", "to_member": "1.2.a" | null,
 "subject": "...", "body": "...", "created_at": "..."}
```
`ack=True` (default) acks every returned message immediately — see §6.2 for
why this is the safe default. `ack=False` peeks without consuming, for a
lead that wants to preview its mailbox before deciding how to act.

### 4.6 `team_dispatch` (lead-only, unchanged)

Signature and behavior are unchanged from the existing implementation —
`team_dispatch(task_id, parent_team_id, caller_member_id, members, synthesis=None)
-> child_team_id`. Included in `TEAM_TOOL_NAMES` and the authorization matrix
for completeness; its own `role == "lead"` check (raising `TeamToolError`
with the exact message existing tests assert on) remains the authoritative
guard, not `authorize_team_tool` — two independent checks that must agree
(and do: `_ROLE_TOOL_AUTHORIZATION["lead"]` is the only role with
`team_dispatch`).

---

## 5. Authorization by task and member

Authorization is **role-based**, keyed off `TeamMember.role` — resolved via
`_member_role(engine, task_id, member_id)`, which walks the *current* plan
(`state.plan.phases[*].steps[*].team`, including nested `sub_team`), not a
snapshot taken at dispatch time. This matters: `team_dispatch` can add
sub-team members mid-execution, and `_member_role` sees them immediately
because it re-reads `state.plan` on every call.

| Role | `team_list` | `team_claim` | `team_update` | `team_send` | `team_read` | `team_dispatch` |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `lead` | Y | Y | Y | Y | Y | Y |
| `implementer` | Y | Y | Y | Y | Y | — |
| `reviewer` | Y | Y | Y | Y | Y | — |
| *(unrecognized/custom role string)* | Y | Y | Y | Y | Y | — |

**Design choice — fail-open on the board/mailbox tools, fail-closed only on
`team_dispatch`.** An unrecognized role string (a plan author's typo, or a
future role this document doesn't know about) still gets full board/mailbox
access. The alternative — fail-closed by default — would mean a single typo
in a plan's `role:` field silently locks a team member out of ALL
coordination with no error at authorization time (it would surface only
much later, as "why did this member never send a message"). `team_dispatch`
is the one tool where fail-closed is correct: standing up a sub-team is the
single highest-blast-radius operation in this surface (it mutates the plan
and registers new dispatch targets), so it alone is opt-in by an explicit,
recognized `role == "lead"`.

Membership (task ↔ member ↔ team) is checked **before** role authorization
on every tool: `_require_member` raises a plain `TeamToolError` ("Member
'X' not found") for an unregistered `member_id`, so a typo'd member ID never
reaches the authorization check and gets the misleading "not authorized"
message — it gets the more actionable "doesn't exist" message.

Authorization is enforced **once, at the tool-call boundary**
(`authorize_team_tool`), not re-derived per-field — there is no per-field
ACL (e.g. "implementer can `team_update` their own tasks but not others'");
task ownership is enforced structurally instead (§6.1's `claimed_by`
concurrency check is the closest thing to a per-resource ACL, and it is a
concurrency guard, not an authorization guard — anyone authorized for
`team_claim` can attempt to claim any open task).

---

## 6. Concurrency, mailbox delivery, and idempotency

### 6.1 Optimistic concurrency — task claims

`TeamBoard.claim_task(..., expected_status: str | None = None)`:

- `expected_status=None` (default at the `TeamBoard` layer, used by the
  **legacy** `team_claim_task`): last-writer-wins, unchanged from the
  pre-existing implementation. Kept as the default specifically so
  `tests/test_team_board.py::TestClaimTask::test_reclaim_replaces_previous_claim`
  (outside this step's allowed paths, and outside its scope to change)
  keeps passing — this document does not get to unilaterally redefine a
  tested legacy behavior.
- `expected_status="open"` (always passed by the **canonical** `team_claim`,
  unless `allow_reassign=True`): read-check-write — if the task is already
  claimed by a *different* member, raise `TeamBoardConflictError` (wrapped
  as `TeamConcurrencyError` at the `team_tools` boundary). Re-claiming your
  own existing claim is a no-op success (safe retry after a timed-out
  response whose actual outcome is unknown).

**Known race window:** the check-then-write is two round-trips against the
underlying `BeadStore` (`read()` then `write()`), not a single atomic
statement — the `bd`-backed store has no compare-and-swap primitive to build
on (confirmed: `BdBeadStore.write()` itself does its own internal
read-modify-write via `bd show` / `bd update`). Two members racing to claim
the same task within that window can both observe `expected_status="open"`
and both write a claim; the later `write()` wins, silently overriding the
earlier claim WITHOUT raising `TeamBoardConflictError` (the conflict check
ran before either write landed). This is an accepted limitation for the
current single-process-per-task execution model (`ExecutionEngine` serializes
tool calls through one Python process per task; concurrent *team members* are
separate Claude Code **subprocesses**, but the CLI-mediated calls into
`team_tools` do not currently run inside those subprocesses — they run
inside whichever process eventually shells out to `baton team claim`, which
under the `worktree` backend is one call at a time from the orchestrating
loop). A true fix needs either a SQLite-backed claim table with a real
`UPDATE ... WHERE status='open'` guard (the pattern used for
`TeamRegistry.set_status_if`, §6.3) or a `bd`-side CAS primitive — flagged
as follow-up work (§9.2), not silently glossed over.

### 6.2 Mailbox delivery

Two delivery modes, both live simultaneously:

1. **Push (existing, unchanged).** `BeadSelector.select_for_team_member`
   injects unread messages into the next dispatch prompt automatically —
   `references/team-messaging.md`'s "next dispatch, not real-time" semantics
   are unchanged by this document.
2. **Pull (new — `team_read`).** A member can call `team_read` mid-turn to
   check its mailbox without waiting for its next dispatch. `ack=True`
   (default) immediately acks every message it returns, using the same
   `message_ack` bead mechanism the push path already relies on
   (`TeamBoard.ack_message`) — so a message pulled via `team_read` is NOT
   re-injected into that member's next dispatch prompt. This is the correct
   default: without it, every pulled message would be delivered a second
   time via push, defeating the point of pulling early. `ack=False` exists
   for a lead that wants to preview without committing to "I've seen this."

No reply-threading, no interrupts — unchanged from the existing design.
Cross-task messaging remains unsupported (§ scope of
`references/team-messaging.md`).

### 6.3 Team-level optimistic concurrency

`TeamRegistry.set_status_if(task_id, team_id, expected_status, status) ->
bool` (new in this step) provides a real compare-and-swap, because
`teams` is a SQLite table this codebase owns directly (unlike beads, which
are mediated through the external `bd` CLI) — a single
`UPDATE teams SET status = ? WHERE task_id = ? AND team_id = ? AND status = ?`
statement is atomic within SQLite's own execution, no read-then-write race.
This is the guard the synthesis state machine (§8) uses to make sure two
concurrent synthesis drivers for the same team can't both "win" a state
transition.

### 6.4 Idempotency

- **`team_update` create mode**: `idempotency_key`, scoped to
  `(team_id, idempotency_key)` via a `idem=<key>` bead tag. A retried create
  call with the same key returns the ORIGINAL `task_bead_id` — no duplicate
  task is written. Scoped to create only; `team_update` complete mode is
  naturally idempotent already (`BeadStore.close()` on an already-closed
  bead is a safe no-op re-close).
- **`team_claim`**: re-claiming your own existing claim, §6.1.
- **`team_send` / `team_dispatch`**: NOT idempotent in this version — a
  retried `team_send` after an ambiguous failure (e.g. CLI process killed
  after the write landed but before the exit code was observed) writes a
  second, duplicate message bead. This mirrors messaging semantics most
  systems accept (at-least-once delivery, dedup is the reader's job) and is
  explicitly a **non-goal** here — see §9.2. `team_dispatch` doubles as
  idempotent in one narrow sense only: `TeamRegistry.create_team` already
  no-ops on an existing `(task_id, team_id)` row (pre-existing behavior,
  unchanged), but the `caller_member.sub_team.extend(new_members)` mutation
  is NOT deduplicated — calling `team_dispatch` twice with the same members
  list appends the roster twice. Flagged, not fixed, in this step.

---

## 7. Audit events, timeouts, and failure behavior

### 7.1 Audit events

Every write (`team_send`, `team_update`, `team_claim`) already produces a
durable, queryable bead — the append-only bead store IS the audit trail
(`team_board.py`'s module docstring: "everything is append-only to the audit
trail"). This document does not add a parallel audit log; it strengthens
the existing one:

- Every canonical tool call additionally emits a structured log line
  (`_log.info`) naming `tool`, `task_id`, `member_id`, and
  success/failure — a lightweight, always-on trace independent of whether
  the call resulted in a bead write (e.g. an authorization failure never
  reaches a bead write, but should still be observable).
- Follow-up work (§9.2): a dedicated `tool_call` bead type, so authorization
  failures and concurrency conflicts — not just successful writes — land in
  the same durable, queryable trail the rest of the system already uses.
  Out of scope here because it touches `models/bead.py`'s
  `KNOWN_BEAD_TYPES`/`TEAM_BOARD_BEAD_TYPES`, outside this step's allowed
  paths.

### 7.2 Timeouts

No transport-level timeout exists yet in this step (§9.1 — the CLI
subcommands themselves are follow-up work). The design commitment for that
follow-up: `baton team <verb>` inherits the same per-call SQLite/bd timeout
behavior the rest of the `baton` CLI already has (no bespoke timeout layer);
a caller-side timeout is the dispatched agent's own Bash-tool timeout
(already enforced by Claude Code itself), which is why `team_update`'s
idempotency key (§6.4) matters — a Bash-tool timeout on `team_update` leaves
the caller unsure whether the write landed, and the safe retry is "call
again with the same idempotency_key," not "assume it failed."

### 7.3 Failure behavior — exception/exit-code taxonomy

| Condition | Python exception | Planned CLI exit code |
|---|---|---|
| Unknown `team_id` / unregistered `member_id` / malformed `resource`/`status`/transition argument | `TeamToolError` | `2` (usage error) |
| Role not authorized for the requested tool | `TeamAuthorizationError` (`TeamToolError` subclass) | `3` (authorization error) |
| Optimistic-concurrency conflict (`team_claim`) | `TeamConcurrencyError` (`TeamToolError` subclass) | `4` (conflict — caller should re-`team_list` and retry against fresh state, not blindly retry the same claim) |
| Underlying store unavailable (`TeamRegistry`/`BeadStore` not configured — e.g. schema predates v15, or `bd` binary missing) | `TeamBackendUnavailableError` (`TeamToolError` subclass) | `5` (backend unavailable — distinct from `2` so a caller/monitor can tell "your call was wrong" from "the environment is broken") |

All four are `TeamToolError` subclasses (or `TeamToolError` itself) so a
caller that only wants "did this fail" can catch the base class; a caller
that wants to branch on *why* catches the specific subclass. No tool in
this surface fails silently — every rejected call raises; the days of
`claim_task` on a missing bead silently logging a warning and returning
`None` are over (§8.1 of the diff: `TeamBoard.claim_task` now raises
`TeamBoardConflictError` on a missing/wrong-type bead instead of warning
and no-op-returning — the prior behavior hid a caller bug behind a log line
only visible if someone was already looking).

---

## 8. Synthesis state machine

`SynthesisState` (new in `agent_baton/models/execution.py`, alongside
`SynthesisSpec`) is the typed vocabulary for how a team step's member
outcomes become the step's `StepResult`:

```
PENDING → COLLECTING → READY → SYNTHESIZING → VERIFYING → SYNTHESIZED   (success)
                                     │              │
                                     ├──► ESCALATED ─┤──► SYNTHESIZING   (resume after resolution)
                                     │              │
                                     ▼              ▼
                                   FAILED         FAILED                (terminal)
```

| State | Meaning | Entered when |
|---|---|---|
| `PENDING` | Team step dispatched; no member outcomes recorded yet. | Step transitions to `dispatched`. |
| `COLLECTING` | At least one, not all required, member outcomes recorded. | First `record_team_member_result()`. |
| `READY` | All required member outcomes in (respecting `depends_on`). | Last blocking member outcome recorded. |
| `SYNTHESIZING` | Merge strategy running. `concatenate`/`merge_files` are synchronous (this state is transient); `agent_synthesis` dispatches a synthesis agent and stays here until it completes. | All member outcomes `READY`. |
| `VERIFYING` | Merge output produced; routed through the **same scope/commit/evidence verification pipeline non-team steps already use** (Phase 3: `scope_contract.py`'s `path_within`/`paths_overlap`, `independent_worktree_diff`/`derive_scope_expansion_from_diff` from `manager_scope_signal.py`, evidence bundle checks) — a team synthesis output is not exempt from the controls a single-agent step's output already goes through. | Merge strategy produces output. |
| `SYNTHESIZED` | Terminal success — verified merge accepted as the step's `StepResult`. | Verification passes. |
| `ESCALATED` | `conflict_handling="escalate"` detected a conflict; waiting on an `APPROVAL` decision naming the resolution (reuses the existing `APPROVAL` `ActionType` and `DecisionManager` — no new `ActionType`, per the protocol-change discipline in `agent_baton/core/engine/CLAUDE.md`). | Conflict detected during `SYNTHESIZING` or a scope/commit/evidence check fails during `VERIFYING` with `conflict_handling="escalate"`. |
| `FAILED` | Terminal failure. | `conflict_handling="fail"` tripped, verification rejected the merge with no escalation configured, a member outcome was itself `"failed"` with no recovery path, or an `ESCALATED` conflict resolves to "abandon". |

`SYNTHESIS_STATE_TRANSITIONS` (a `dict[SynthesisState, frozenset[SynthesisState]]`)
and `is_valid_synthesis_transition(frm, to) -> bool` encode the edges above —
every state, including the two terminal ones, has an explicit (possibly
empty) entry, so a lookup miss can never silently mean "anything goes."

**Why this ties into the runtime-contract's tool surface:** `team_dispatch`
creates the sub-team whose outcomes this state machine collects;
`team_update`/`team_claim` on the sub-team's shared board are how members
coordinate *while* `COLLECTING`; a `team_send` broadcast is the natural way
a lead announces `ESCALATED` to its sub-team ("waiting on human input, hold
your commits"). The state machine and the tool surface are two views of the
same lifecycle, not independent designs.

**Scope of this step (updated by 4.3):** the enum, transition table, and
validity function landed with 4.1 (`agent_baton/models/execution.py`,
unit-tested in `tests/test_team_tools.py::TestSynthesisStateMachine`).
Step 4.3 subsequently wired the persisted field in: `StepResult` carries
`synthesis_state` + `synthesis_dispatched` (schema v48 migration), and
`executor.py`'s `agent_synthesis` path drives the `SYNTHESIZING`,
`ESCALATED` (resume edge), and terminal `SYNTHESIZED`/`FAILED` states via
`_apply_synthesis`/`_pending_synthesis_dispatch`/`record_step_result`.
The intermediate `PENDING`/`COLLECTING`/`READY`/`VERIFYING` states remain
design vocabulary (member collection is tracked by `member_results`
directly; verification happens inside `record_step_result` before the
terminal edge lands), and `TeamRegistry.set_status_if` is not yet called
by the executor — see §9.3 for what is still open.

---

## 9. Follow-up work (explicitly out of scope for this step)

### 9.1 Wire the CLI and the advertised-tools invariant

- Add `agent_baton/cli/commands/team.py` (`baton team list|claim|update|send|read`)
  calling the Python functions in §3/§4 with an argparse/Click layer + `--json`.
- Add `$BATON_TEAM_MEMBER_ID` to the launcher's env passthrough
  (`claude_launcher.py::_DEFAULT_ENV_PASSTHROUGH`) and have `TaskWorker`/the
  worktree dispatch path set it per member, so `--member-id` can be omitted.
- Update `agents/team-lead.md` and `references/team-messaging.md` to name the
  five canonical tools (CLI verbs) instead of the five legacy Python-looking
  names — and have `dispatcher.py`'s prompt builder call
  `advertised_team_tools_for_role(role)` (§2.3) rather than a hard-coded
  list, so the two can never drift again.
- Update `docs/engine-and-runtime.md` §18 (team backend comparison) to note
  the CLI surface works identically under both backends (§2.1's structural
  argument for why CLI was chosen over MCP).

### 9.2 Close the documented gaps

- `team_claim`'s read-check-write race window (§6.1) — needs a real
  compare-and-swap primitive at the bead-store layer or a dedicated
  claims table (mirroring `TeamRegistry.set_status_if`, §6.3).
- `team_send`/`team_dispatch` idempotency (§6.4).
- A `tool_call` bead type for authorization/concurrency-failure audit
  events, not just successful writes (§7.1).

### 9.3 Wire the synthesis state machine into the executor

Mostly DONE in step 4.3: `StepResult.synthesis_state`/`synthesis_dispatched`
persisted (schema v48), `agent_synthesis` transitions through
`SYNTHESIZING → (ESCALATED →) SYNTHESIZED|FAILED` with exactly-once,
restart-safe dispatch. Still open: surfacing the intermediate
`PENDING`/`COLLECTING`/`READY`/`VERIFYING` states as persisted values, and
calling `TeamRegistry.set_status_if` at the `COLLECTING → READY` and
`VERIFYING → SYNTHESIZED` boundaries (needed only once two independent
synthesis drivers can race — see §6.1's process model).

---

## 10. Test coverage landed with this step

`tests/test_team_tools.py` (hermetic — uses an in-memory `_FakeBeadStore`
instead of requiring the external `bd` binary, per `tests/CLAUDE.md`'s
hermeticity requirement):

- Authorization matrix: tool-name closure, role → tool-set, unknown-role
  fallback, `advertised_team_tools_for_role` sorting.
- `team_list`: open/claimed/done filters, `resource="teams"`, unsupported
  resource/status rejection, unknown-member rejection.
- `team_claim`: conflict on cross-member reclaim, same-member reclaim is a
  no-op, `allow_reassign` bypass, missing-bead rejection, legacy
  `team_claim_task` unaffected (still last-writer-wins).
- `team_update`: idempotent create-retry, missing-title/-outcome rejection,
  unsupported-transition rejection.
- `team_send` / `team_read`: canonical send matches legacy, read-and-ack
  suppresses redelivery, `ack=False` peek is repeatable.
- `TeamBoard.claim_task` optimistic-concurrency unit tests (both modes).
- `TeamRegistry.set_status_if` — matching/mismatched expected-status.
- `SynthesisState` transition-table completeness and specific edges
  (`PENDING→COLLECTING` valid, `PENDING→SYNTHESIZED` invalid, terminal
  states have no outgoing edges, `ESCALATED`'s two valid exits).
