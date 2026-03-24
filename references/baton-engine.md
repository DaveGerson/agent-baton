---
name: baton-engine
description: |
  CLI reference for the baton execution engine. Read this to understand
  every execution-related command, the orchestration loop, action types,
  common errors, and the files the engine reads and writes. Read before
  driving any multi-agent task through the engine.
---

# Baton Engine — CLI Reference

The `baton` CLI is the control interface for the agent-baton execution
engine.  The orchestrator uses it to plan tasks, drive agents through
phased execution, record results, pass QA gates, and finalize runs.  The
engine persists all state to `.claude/team-context/` so a crashed session
can resume without losing progress.

---

## Commands Reference

### `baton plan`

Create a data-driven execution plan for a task.

```
baton plan "description" --save --explain \
    [--task-type TYPE] \
    [--agents LIST] \
    [--project PATH] \
    [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `"description"` | Yes | Natural-language task summary passed to the planner |
| `--save` | No | Write `plan.json` and `plan.md` to `.claude/team-context/` |
| `--explain` | No | Print a human-readable explanation of the plan choices |
| `--task-type TYPE` | No | Override auto-detected type: `new-feature`, `bug-fix`, `refactor`, `data-analysis`, `documentation`, `migration`, `test` |
| `--agents LIST` | No | Comma-separated agent names; bypasses auto-selection |
| `--project PATH` | No | Project root for stack detection (default: cwd) |
| `--json` | No | Output the plan as JSON instead of markdown |

**Typical usage:**

```
baton plan "Add JWT auth middleware and integration tests" --save --explain
```

**Output (with `--explain`):** Prints the markdown plan followed by a
paragraph explaining why specific agents, risk level, and execution mode
were chosen.

**Output (with `--json`):** Prints the `MachinePlan` serialised as JSON.
Use this to inspect the machine-readable plan before starting execution.

**Notes:**
- Always pass `--save` before running `baton execute start`; `start`
  reads `plan.json` from disk.
- If `--explain` is used together with `--save`, the plan is saved first
  and then the explanation is printed.
- The planner reads agent definitions from `~/.claude/agents/` and
  `.claude/agents/` and detects the project stack (language, framework)
  from config files in `--project`.  If stack detection returns `unknown`,
  pass `--project` explicitly pointing to the directory that contains
  `package.json`, `pyproject.toml`, etc.

---

### `baton execute start`

Initialise execution state from a saved plan and return the first action.

```
baton execute start [--plan PATH]
```

| Argument | Required | Default |
|----------|----------|---------|
| `--plan PATH` | No | `.claude/team-context/plan.json` |

**Output:** Prints the first `ExecutionAction` in human-readable format
(see "Action Types" below).

**Side effects:**
- Creates `.claude/team-context/execution-state.json`
- Starts an in-memory trace (committed to disk on `baton execute complete`)
- Publishes a `task.started` event

**Error:** If `plan.json` does not exist, prints an error and exits 1.
Run `baton plan --save "..."` first.

---

### `baton execute next`

Return the next action the orchestrator should perform.

```
baton execute next [--all]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--all` | No | Return all currently dispatchable actions as a JSON array (for parallel dispatch) |

**Without `--all`:** Prints the next single action in human-readable
format.  This is the standard form used in sequential execution loops.

**With `--all`:** Prints a JSON array of all steps whose dependencies are
satisfied and that have not yet been dispatched, completed, or failed.
When no parallel steps are available, falls back to a single-item array
containing the result of `next_action()`.  Use this for phases whose
steps have no `depends_on` constraints between them.

**State transitions triggered:**
- If all steps in a phase are complete and the phase has a gate, the
  engine sets `status = gate_pending` and returns a GATE action.
- If all steps in a phase are complete and the gate has passed (or there
  is no gate), the engine advances `current_phase` and returns the first
  DISPATCH of the next phase.
- If all phases are exhausted, returns COMPLETE.

---

### `baton execute dispatched`

Mark a step as in-flight (dispatched but not yet complete).

```
baton execute dispatched --step-id ID --agent NAME
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--step-id ID` | Yes | Step identifier, e.g. `1.1` |
| `--agent NAME` | Yes | Agent name, e.g. `backend-engineer--python` |

**Output:** `{"status": "dispatched", "step_id": "1.1"}`

**When to use:** Call this immediately after spawning a subagent (before
`baton execute record`) so the engine knows the step is in-flight.  This
matters for parallel execution — the engine will not dispatch a second
step that depends on a `dispatched` step.

**Note:** `baton execute record` also accepts `--status dispatched` but
`baton execute dispatched` is the idiomatic shorthand.

---

### `baton execute record`

Record the outcome of a completed (or failed) step.

```
baton execute record \
    --step-id ID \
    --agent NAME \
    [--status complete|failed] \
    [--outcome TEXT] \
    [--files LIST] \
    [--commit HASH] \
    [--tokens N] \
    [--duration N] \
    [--error TEXT]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--step-id ID` | Yes | — | Step identifier, e.g. `1.1` |
| `--agent NAME` | Yes | — | Agent name |
| `--status` | No | `complete` | `complete` or `failed` — no other values |
| `--outcome TEXT` | No | `""` | Free-text summary of what the agent did |
| `--files LIST` | No | `""` | Comma-separated list of files changed |
| `--commit HASH` | No | `""` | Git commit hash if the agent committed work |
| `--tokens N` | No | `0` | Estimated token count (for usage logging) |
| `--duration N` | No | `0.0` | Duration in seconds (float) |
| `--error TEXT` | No | `""` | Error detail if `--status failed` |

**Output:** `Recorded: step 1.1 (backend-engineer--python) — complete`

**IMPORTANT:** `--status` only accepts `complete` or `failed`.  Passing
`pass`, `done`, `success`, or any other value raises a `ValueError` and
exits with an error.

**After recording:** Call `baton execute next` to get the next action.

---

### `baton execute gate`

Record the result of a QA gate check.

```
baton execute gate \
    --phase-id N \
    --result pass|fail \
    [--output TEXT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--phase-id N` | Yes | Integer phase ID (matches `phase_id` in the plan, 1-based) |
| `--result pass\|fail` | Yes | Gate outcome |
| `--output TEXT` | No | Command output or reviewer notes |

**Output:** `Gate recorded: phase 1 — PASS`

**State transitions:**
- `pass` — engine advances `current_phase` and resets step index; next
  call to `baton execute next` returns the first DISPATCH of the next
  phase (or COMPLETE if no phases remain).
- `fail` — engine sets `status = failed`; next call returns FAILED.

**When to call:** Only after receiving a GATE action from `baton execute
next` or `baton execute start`.  The GATE action output includes the
`phase_id` and the optional `gate_command` to run.

---

### `baton execute approve`

Record a human approval decision for a phase that has `approval_required=True`.

```
baton execute approve \
    --phase-id N \
    --result approve|reject|approve-with-feedback \
    [--feedback TEXT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--phase-id N` | Yes | Phase ID requiring approval |
| `--result` | Yes | Decision: `approve`, `reject`, or `approve-with-feedback` |
| `--feedback TEXT` | No | Feedback text (required when `--result approve-with-feedback`) |

**Output:** `Approval recorded: phase 1 — APPROVED`

**State transitions:**
- `approve` — execution continues to the gate (if any) or next phase.
- `reject` — engine sets `status = failed`; next call to `baton execute next`
  returns FAILED.
- `approve-with-feedback` — engine inserts a remediation phase immediately
  after the current phase and continues; the feedback text is injected into
  the remediation step's delegation prompt.

**When to call:** Only after receiving an APPROVAL action from
`baton execute next`.  The APPROVAL action output includes the `phase_id`
and a context summary for the reviewer.

---

### `baton execute amend`

Amend the running plan by adding phases or steps during execution.

```
baton execute amend \
    --description TEXT \
    [--add-phase NAME:AGENT] \
    [--after-phase N] \
    [--add-step PHASE_ID:AGENT:DESCRIPTION]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--description TEXT` | Yes | Why this amendment is needed (written to the amendment audit log) |
| `--add-phase NAME:AGENT` | No | Add a phase as `NAME:AGENT`; repeatable for multiple phases |
| `--after-phase N` | No | Insert new phases after this phase ID (default: append at end) |
| `--add-step PHASE_ID:AGENT:DESCRIPTION` | No | Add a step to an existing phase; repeatable |

**Output:** `Plan amended: added 1 phase(s), 0 step(s). Amendment recorded.`

**Notes:**
- Amendments are written to `execution-state.json` under `amendments[]` so
  the audit trail is preserved even on crash recovery.
- Use `--add-phase` when an unexpected dependency or gap requires a new
  agent block.  Use `--add-step` to insert a single task into an existing
  phase without restructuring the plan.
- `--add-phase` and `--add-step` may be combined in one call.
- After amending, call `baton execute next` to receive the first action of
  the newly inserted content.

---

### `baton execute team-record`

Record a team member completion within a team step (a step that coordinates
multiple agents in parallel under one step ID).

```
baton execute team-record \
    --step-id S \
    --member-id M \
    --agent NAME \
    [--status complete|failed] \
    [--outcome TEXT] \
    [--files F]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--step-id S` | Yes | Parent team step ID, e.g. `2.1` |
| `--member-id M` | Yes | Team member ID, e.g. `2.1.a`, `2.1.b` |
| `--agent NAME` | Yes | Agent name |
| `--status` | No | `complete` or `failed` (default: `complete`) |
| `--outcome TEXT` | No | Summary of work done |
| `--files F` | No | Comma-separated files changed |

**Output:** `Team member 2.1.a recorded: backend-engineer--python — complete`

**When to use:** Team steps appear in the plan when the engine schedules
multiple coordinated agents under a single logical step.  Call
`baton execute team-record` for each member as they finish, then call
`baton execute next` after all members are recorded.  See "Team Steps"
below.

---

### `baton execute complete`

Finalise a completed execution run.

```
baton execute complete
```

**Output:** Multi-line summary:

```
Task abc123 completed.
Steps: 4/4
Gates passed: 2
Elapsed: 312s
Trace: .claude/team-context/traces/abc123.json
Retrospective: .claude/team-context/retrospectives/abc123.md
```

**Side effects:**
- Sets execution state `status = complete`
- Commits the trace file to `.claude/team-context/traces/`
- Writes a `TaskUsageRecord` to `.claude/team-context/usage-log.jsonl`
- Generates and saves a retrospective to `.claude/team-context/retrospectives/`
- Publishes a `task.completed` event

**When to call:** Only when `baton execute next` returns a COMPLETE
action.  Do not call before all steps are recorded.

---

### `baton execute status`

Show current execution state without advancing it.

```
baton execute status
```

**Output:**

```
Task:    abc123
Status:  running
Phase:   1
Steps:   2/4
Gates:   1 passed, 0 failed
Elapsed: 145s
```

**Output when no active execution:** `No active execution.`

Use this to inspect the engine state at any point without consuming an
action or mutating state.

---

### `baton execute resume`

Resume execution after a crash or interrupted session.

```
baton execute resume
```

**Output:** Same format as `baton execute next` — prints the next action.

**How it works:** Loads `execution-state.json` from disk, reconnects the
in-memory trace (or starts a new trace continuation), then calls the same
state machine logic as `next_action()`.  Steps that were in `dispatched`
status when the session crashed will be re-dispatched.

**When to use:** At the start of a recovery session after a timeout or
crash.  If `execution-state.json` does not exist, returns a FAILED action
with the message "No execution state found on disk. Cannot resume."

---

### `baton detect`

Detect the project stack (language and framework) from config files.

```
baton detect [--path PATH]
```

| Argument | Required | Default |
|----------|----------|---------|
| `--path PATH` | No | Current directory |

**Output:**

```
Language:  python
Framework: fastapi
Signals:   pyproject.toml, requirements.txt
```

**Use:** Diagnose why `baton plan` or `baton route` picked unexpected
agents.  If Language/Framework show `unknown`, the config files that
signal the stack are not in the current directory — pass `--path` pointing
to the project root.

---

### `baton route ROLES...`

Route base agent role names to their stack-specific flavored variants.

```
baton route [ROLES...] [--path PATH]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `ROLES` | No | Base role names (default: `backend-engineer frontend-engineer`) |
| `--path PATH` | No | Project root for stack detection (default: cwd) |

**Output:**

```
Stack: python/fastapi

  backend-engineer               → backend-engineer--python *
  frontend-engineer              → frontend-engineer
```

Entries marked with `*` were remapped to a flavored variant.  Entries
without `*` have no flavor for the detected stack and use the base agent.

---

### `baton classify "description"`

Classify a task description for risk level and guardrail preset.

```
baton classify "description" [--files FILE...]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `"description"` | Yes | Task description to classify |
| `--files FILE...` | No | File paths affected (elevates risk if paths match sensitive patterns) |

**Output:**

```
Risk Level: MEDIUM
Preset: regulated-data
Confidence: 0.82
Signals: payment, user-data
Explanation: Task touches payment processing and user PII fields.
```

**Risk Levels:** `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`

---

### `baton agents`

List all available agents grouped by category.

```
baton agents
```

**Output:**

```
engineering:
  backend-engineer                    [sonnet]
  backend-engineer--python            [sonnet]   (flavor: python)
  frontend-engineer                   [sonnet]

governance:
  auditor                             [opus]
  code-reviewer                       [sonnet]

19 agents loaded.
```

Agents marked `(flavor: X)` are stack-specific variants.  If no agents
are loaded, run `scripts/install.sh` to deploy the agent definitions to
`~/.claude/agents/`.

---

## Execution Loop

The orchestrator drives the engine through a deterministic loop.  The
engine is a state machine — each call either returns an action or
advances the state.

### Complete Pseudocode

```
# 1. Create a plan
baton plan "task description" --save --explain

# 2. Create a git branch
git checkout -b feat/task-name

# 3. Start execution — returns first action
action = baton execute start

# 4. Loop until terminal action
loop:
    if action.type == DISPATCH:
        # Mark in-flight BEFORE spawning
        baton execute dispatched --step-id {step_id} --agent {agent}

        # Spawn subagent using the Agent tool
        result = Agent(
            agent_name = action.agent_name,
            task       = action.delegation_prompt
        )

        # Commit agent's work
        git add -A && git commit -m "step {step_id}: {agent} complete"

        # Record result
        baton execute record \
            --step-id {step_id} \
            --agent {agent} \
            --status complete \          # or "failed"
            --outcome "brief summary" \
            --files "file1.py,file2.py" \
            --commit {hash}

        # Advance
        action = baton execute next

    elif action.type == GATE:
        # Run the gate command
        output = bash(action.gate_command)
        passed = (exit_code == 0)

        baton execute gate \
            --phase-id {phase_id} \
            --result {"pass" if passed else "fail"} \
            --output "{output}"

        action = baton execute next

    elif action.type == APPROVAL:
        # Present context to the human reviewer
        # (action output includes the approval context block)
        decision = human_review(action.context_summary)
        # decision is one of: approve, reject, approve-with-feedback

        if decision == "approve-with-feedback":
            baton execute approve \
                --phase-id {phase_id} \
                --result approve-with-feedback \
                --feedback "{feedback_text}"
        else:
            baton execute approve \
                --phase-id {phase_id} \
                --result {decision}

        action = baton execute next

    elif action.type == TEAM_DISPATCH:
        # Engine returned multiple coordinated members under one step
        for member in action.members:
            baton execute dispatched --step-id {step_id} --agent {member.agent}
            result = Agent(
                agent_name = member.agent,
                task       = member.delegation_prompt
            )
            baton execute team-record \
                --step-id {step_id} \
                --member-id {member.member_id} \
                --agent {member.agent} \
                --status complete \
                --outcome "brief summary" \
                --files "file1.py,file2.py"

        action = baton execute next

    elif action.type == WAIT:
        # Parallel steps still running — poll after each agent completes
        # (only relevant in daemon/async mode; in sequential mode this
        #  should not appear)
        sleep(5)
        action = baton execute next

    elif action.type == COMPLETE:
        baton execute complete
        break

    elif action.type == FAILED:
        # Log failure, notify user
        break
```

### Sequential vs. Parallel

The standard loop above is sequential: one step dispatched at a time.
For phases where steps have no `depends_on` constraints, use
`baton execute next --all` to get all dispatchable steps, spawn them
in parallel with the Agent tool, call `baton execute dispatched` for
each, wait for all to return, then call `baton execute record` for each
result before calling `baton execute next` again.

---

## Action Types

Every call to `baton execute start`, `baton execute next`, or
`baton execute resume` returns exactly one action.

### DISPATCH

Spawn a subagent.

```
ACTION: DISPATCH
  Agent: backend-engineer--python
  Model: sonnet
  Step:  1.1
  Message: Dispatch agent 'backend-engineer--python' for step 1.1.

--- Delegation Prompt ---
# Agent Task: 1.1
...
--- End Prompt ---
```

| Field | Description |
|-------|-------------|
| `agent_name` | Agent to spawn (matches a definition in `.claude/agents/`) |
| `agent_model` | Model tier from the plan step (`opus`, `sonnet`, `haiku`) |
| `step_id` | Step identifier, e.g. `1.1` |
| `message` | Human-readable description |
| `delegation_prompt` | Full prompt to pass to the subagent |
| `path_enforcement` | Optional bash command for `PreToolUse` path enforcement |

**You must** use the Agent tool to spawn the subagent.  Do not do the
work yourself.  After the subagent returns, record the result with
`baton execute record`.

### GATE

Run a QA gate check.

```
ACTION: GATE
  Type:    test
  Phase:   1
  Command: pytest tests/
  Message: Run gate 'test' for phase 1.
```

| Field | Description |
|-------|-------------|
| `gate_type` | Type of gate: `build`, `test`, `lint`, `spec`, `review` |
| `phase_id` | Integer phase ID (pass this to `baton execute gate --phase-id`) |
| `gate_command` | Bash command to run to evaluate the gate |
| `message` | Human-readable description |

Run `gate_command` with Bash.  Pass `--result pass` if the command exits
0, `--result fail` otherwise.  Include the command output in `--output`.

### COMPLETE

All phases and gates finished successfully.

```
ACTION: COMPLETE
  Task abc123 completed successfully.
```

| Field | Description |
|-------|-------------|
| `summary` | Completion message |

Call `baton execute complete` to finalise the run (writes traces, usage,
retrospective).

### FAILED

Execution cannot continue.

```
ACTION: FAILED
  Execution failed. Failed step(s): 2.1
```

| Field | Description |
|-------|-------------|
| `summary` | Failure reason |

A step returned `--status failed` and the engine set `status = failed`,
or a gate returned `--result fail`.  Do not call `baton execute complete`.
Report the failure to the user.

### WAIT

Parallel steps are still in-flight; nothing new to dispatch right now.

```
ACTION: WAIT
  Waiting for in-flight steps to complete before proceeding.
```

In sequential execution this should not appear.  In parallel mode, poll
with `baton execute next` after recording each returning agent's result.

### APPROVAL

Execution is paused for human review.  The engine sets
`status = approval_pending` until a decision is recorded.

```
ACTION: APPROVAL
  Phase:   <phase-id>
  Message: <one-line summary>

--- Approval Context ---
<summary of phase output for reviewer>
--- End Context ---

Options: approve, reject, approve-with-feedback
```

| Field | Description |
|-------|-------------|
| `phase_id` | Phase requiring approval (pass to `baton execute approve --phase-id`) |
| `message` | Human-readable description of why approval is needed |
| `context_summary` | Output or evidence from the phase for the reviewer to evaluate |

Present the context summary to the human reviewer and record the decision
with `baton execute approve`.  Do not call `baton execute next` until the
approval decision has been recorded.

---

## Common Errors and Fixes

### "status must be one of: complete, failed, dispatched"

**Cause:** `baton execute record --status pass` (or `done`, `success`,
`ok`, etc.).

**Fix:** Use only `complete` or `failed` as `--status` values.

```
# Wrong
baton execute record --step-id 1.1 --agent foo --status pass

# Correct
baton execute record --step-id 1.1 --agent foo --status complete
```

### "No active execution state found"

**Cause:** `baton execute next` or `baton execute record` was called
before `baton execute start`, or `.claude/team-context/execution-state.json`
was deleted.

**Fix:** Run `baton execute start` (or `baton execute resume` if state
exists from a previous session).

### Stack detection returns `unknown`

**Cause:** `baton plan` or `baton detect` cannot find config files
(`package.json`, `pyproject.toml`, `go.mod`, etc.) in the current
directory.

**Fix:** Pass `--project PATH` pointing to the directory that contains
the config files.

```
baton plan "..." --save --project /path/to/project/root
```

Config files in subdirectories (e.g., a monorepo where the Python
package is in `backend/`) are not auto-detected; the path must point
directly to the directory that contains them.

### Plan has generic descriptions ("Implement feature", "Add tests")

**Cause:** The planner received a vague or one-word task summary.

**Fix:** Pass a richer description to `baton plan`.  The planner uses
the task summary verbatim when building delegation prompts — specific
descriptions produce specific prompts.

```
# Too vague
baton plan "auth" --save

# Better
baton plan "Add JWT authentication middleware to the FastAPI app, including login/logout endpoints and test coverage" --save
```

### `baton execute gate` fails with "No active execution state"

**Cause:** `baton execute gate` was called without a preceding
`baton execute start`, or was called after the state file was cleared.

**Fix:** Check `baton execute status`.  If no active execution, start
fresh with `baton plan --save` then `baton execute start`.

### `baton execute complete` reports "No active execution state found"

**Cause:** `complete` was called before `start`, or the state was
already finalised.

**Fix:** Verify the execution loop reached a COMPLETE action before
calling `complete`.  If the state was already finalised, the run data is
already in `usage-log.jsonl` and `retrospectives/`.

---

## File Layout

All engine files live under `.claude/team-context/` relative to the
project root.  This directory is created automatically by
`baton execute start`.

```
.claude/team-context/
├── plan.json               ← Machine-readable execution plan (MachinePlan)
├── plan.md                 ← Human-readable plan (same data, markdown render)
├── execution-state.json    ← Live engine state (crash recovery)
├── context.md              ← Shared project context (written by orchestrator)
├── mission-log.md          ← Structured log of agent completions (appended by hooks)
├── usage-log.jsonl         ← Token and cost records per task (one JSON line each)
├── traces/
│   └── {task_id}.json      ← Full execution trace with per-event timeline
└── retrospectives/
    └── {task_id}.md        ← Post-execution analysis and improvement suggestions
```

### `plan.json`

Written by `baton plan --save`.  Read by `baton execute start`.

Serialised `MachinePlan` containing:
- `task_id` — unique identifier for the run
- `task_summary` — the description passed to `baton plan`
- `risk_level` — `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
- `budget_tier` — `standard`, `economy`, `premium`
- `execution_mode` — `phased`, `sequential`, `parallel`
- `git_strategy` — `commit-per-agent`, `feature-branch`, `pr-per-phase`
- `phases[]` — ordered list of phases, each with `steps[]` and optional `gate`
- `shared_context` — pre-built context string injected into every delegation prompt

### `plan.md`

Written alongside `plan.json` by `baton plan --save`.

Human-readable markdown rendering of the plan.  The orchestrator may read
this to understand the plan structure without parsing JSON.

### `execution-state.json`

Written by the engine on every state transition.  Never write this file
manually — it is the engine's authoritative state.

Fields: `task_id`, `plan` (embedded copy), `current_phase`, `status`,
`step_results[]`, `gate_results[]`, `approval_results[]`, `amendments[]`,
`started_at`, `completed_at`.

`status` values: `running`, `gate_pending`, `approval_pending`, `complete`, `failed`.

| Status | Set by | Cleared by |
|--------|--------|------------|
| `running` | `baton execute start` | next phase transition |
| `gate_pending` | phase completion with gate | `baton execute gate --result pass` |
| `approval_pending` | phase with `approval_required=True` | `baton execute approve` |
| `complete` | `baton execute complete` | — |
| `failed` | failed step, failed gate, or rejected approval | — |

**`approval_results[]`** — list of approval decision records.  Each entry
contains `phase_id`, `result`, `feedback` (if any), `decided_at`.

**`amendments[]`** — list of plan amendment audit records.  Each entry
contains `description`, `phases_added`, `steps_added`, `amended_at`.

If this file exists from a previous session (e.g., after a crash), use
`baton execute resume` to pick up where it left off.

### `context.md`

Written by the orchestrator (not the engine) before dispatching agents.
Contains project context, architecture decisions, and conventions that
every agent in the run should read.

The engine injects a pointer to this file into every delegation prompt:
`Read .claude/team-context/context.md for shared project context.`

### `mission-log.md`

Appended by the `SubagentStop` hook (configured in `settings.json`) when
each agent finishes.  Format:

```
### 2026-03-23T14:05:12+00:00 — backend-engineer--python — COMPLETE
```

The orchestrator may also write richer entries here after recording each
step result.

### `usage-log.jsonl`

One JSON object per line, written by `baton execute complete`.  Each
record contains aggregated token counts, duration, gate outcomes, and
risk level for the full task.  Used by the learning pipeline to tune
budget selection.

### `traces/{task_id}.json`

Full event timeline written by `baton execute complete`.  Events include:
`task.started`, `phase.started`, `agent.complete`, `agent.failed`,
`gate.result`, `phase.completed`, `task.completed`.

Used by the `baton observe trace` command and the retrospective engine.

### `retrospectives/{task_id}.md`

Markdown post-mortem written by `baton execute complete`.  Includes:
task outcome, agent performance summary, gate results, and suggested
improvements for similar future tasks.

Used by the `baton observe retro` command and the pattern learner.

---

## Plan Amendments

Plans can evolve during execution.  Use `baton execute amend` when an
unexpected dependency, gap, or error surfaces after execution has started
and the original plan no longer covers what is needed.

**Typical triggers:**

| Situation | Amendment action |
|-----------|-----------------|
| An agent discovers an undocumented dependency | `--add-phase` with the dependency agent |
| A step's scope expands beyond one agent | `--add-step` into the current phase |
| A rejected approval requires rework | Engine inserts a remediation phase automatically (via `approve-with-feedback`) |
| A gate failure reveals missing test coverage | `--add-phase` for a targeted fix phase |

**Amendment audit trail:** Every call to `baton execute amend` writes a
record to `amendments[]` in `execution-state.json` with the description,
the phases and steps added, and a timestamp.  This trail is included in the
final trace and retrospective so future runs can learn from mid-flight plan
changes.

**Constraints:** Amendments cannot remove or reorder phases that have
already completed.  They can only add new content after the current phase
or into phases not yet started.

---

## Team Steps

A team step is a plan step that coordinates multiple agents under one
logical step ID.  The engine emits a TEAM_DISPATCH action (instead of
DISPATCH) when a step has two or more sub-members, each with their own
agent assignment.

**Member IDs** follow the pattern `{step_id}.{letter}`, e.g. `2.1.a`,
`2.1.b`.  The parent step ID is `2.1`.

**Orchestration pattern:**

1. Receive TEAM_DISPATCH action — inspect `action.members` for the list
   of member assignments.
2. For each member: call `baton execute dispatched`, spawn the agent, wait
   for completion.
3. For each completed member: call `baton execute team-record` (not
   `baton execute record`).
4. After all members are recorded: call `baton execute next`.

Members within a team step may be dispatched in parallel (if no inter-member
`depends_on` constraints exist) or sequentially.  The engine does not
advance past the team step until all members are in a terminal state
(`complete` or `failed`).

**Failure handling:** If any member records `--status failed`, the team
step is marked failed and the engine sets `status = failed` on the next
call to `baton execute next`.
