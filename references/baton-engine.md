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
| `--model MODEL` | No | Default model for dispatched agents (e.g. `opus`, `sonnet`). Agent definitions with explicit models take priority. |
| `--knowledge PATH` | No | Attach a knowledge document globally to all steps (repeatable) |
| `--knowledge-pack NAME` | No | Attach a knowledge pack globally to all steps (repeatable) |
| `--intervention LEVEL` | No | Escalation threshold: `low` (default), `medium`, `high` |
| `--complexity LEVEL` | No | Override task complexity: `light`, `medium`, `heavy`. Skips automatic classification when provided. |

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

**Note:** Use `baton execute dispatched` to mark a step as in-flight.
The `record` subcommand only accepts `--status complete` or `--status
failed` -- it does not accept `dispatched`.

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
    [--gate-output TEXT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--phase-id N` | Yes | Integer phase ID (matches `phase_id` in the plan, 1-based) |
| `--result pass\|fail` | Yes | Gate outcome |
| `--gate-output TEXT` | No | Command output or reviewer notes. Note: use `--gate-output`, not `--output` (which is reserved for the output format selector). |

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
Bound:   BATON_TASK_ID
Status:  running
Phase:   1
Steps:   2/4
Gates:   1 passed, 0 failed
Elapsed: 145s
```

The `Bound:` field shows which resolution path supplied the task ID:

| Value | Meaning |
|-------|---------|
| `--task-id` | Explicit `--task-id` flag on this invocation |
| `BATON_TASK_ID` | `BATON_TASK_ID` environment variable |
| `active-task-id.txt` | Repository-wide active marker file |

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

### `baton execute run`

Autonomous execution loop that drives the full plan without a Claude Code
session.  Spawns `claude --print` subprocesses for each DISPATCH action.

```
baton execute run \
    [--plan PATH] \
    [--model MODEL] \
    [--max-steps N] \
    [--dry-run]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--plan PATH` | No | `.claude/team-context/plan.json` | Path to plan.json |
| `--model MODEL` | No | `sonnet` | Default model for dispatched agents |
| `--max-steps N` | No | `50` | Safety limit: maximum steps before aborting |
| `--dry-run` | No | — | Print actions without executing them |

**When to use:** For headless execution without an interactive Claude Code
session.  The PMO UI can also launch execution via this path.

---

### `baton execute list`

List all executions (active and completed) in the repository.

```
baton execute list
```

**Output:** A table of all task IDs with their status, start time, and
step progress.

**When to use:** To see which executions exist in the current repository,
especially when managing concurrent executions.

---

### `baton execute switch`

Switch the repository-wide active execution marker to a different task ID.

```
baton execute switch TASK_ID
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | Yes | The task ID to make active |

**Side effects:** Writes the task ID to `active-task-id.txt`.  Subsequent
`baton execute` calls that do not specify `--task-id` or `BATON_TASK_ID`
will resolve to this execution.

**When to use:** When working with multiple executions in the same
repository and you want to change which one is the default target without
setting an environment variable.

---

### Global `--output` flag (all execute subcommands)

Every `baton execute` subcommand accepts an `--output` flag that controls
the output format.

```
baton execute <subcommand> [--output text|json]
```

| Value | Description |
|-------|-------------|
| `text` (default) | Human-readable output (the format documented throughout this reference) |
| `json` | Machine-readable JSON output (useful for programmatic consumers) |

**Note:** This flag is distinct from `--gate-output` on the `gate`
subcommand.  `--output` controls the format; `--gate-output` provides
the gate's command output text.

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

### `baton learn`

Learning automation — track, diagnose, and fix recurring issues.

#### `baton learn status`

Dashboard showing open issues by type, proposed fixes, and auto-apply
statistics.

#### `baton learn issues`

```
baton learn issues [--type TYPE] [--severity SEVERITY] [--status STATUS]
```

List learning issues with optional filters.

| Argument | Required | Description |
|----------|----------|-------------|
| `--type` | No | Filter by issue type (routing_mismatch, agent_degradation, knowledge_gap, roster_bloat, gate_mismatch, pattern_drift, prompt_evolution) |
| `--severity` | No | Filter by severity (low, medium, high, critical) |
| `--status` | No | Filter by status (open, investigating, proposed, applied, resolved, wontfix) |

#### `baton learn analyze`

Run analysis cycle: detect patterns across open issues, compute
confidence scores, mark issues as "proposed" when they cross auto-apply
thresholds.

#### `baton learn apply`

```
baton learn apply [--issue ID] [--all-safe]
```

Apply a specific fix or all auto-applicable fixes.  Each application
writes corrections to `learned-overrides.json`, which the router and
planner consume on the next plan.

| Argument | Required | Description |
|----------|----------|-------------|
| `--issue` | No | Apply fix for a specific issue ID (supports prefix matching) |
| `--all-safe` | No | Apply all issues that meet auto-apply thresholds |

#### `baton learn interview`

```
baton learn interview [--type TYPE] [--severity SEVERITY]
```

Interactive structured dialogue for human-directed learning decisions.
Presents issues one at a time with evidence summaries and multiple-choice
options (evolve prompt, add knowledge pack, reduce priority, drop agent,
investigate, won't fix, skip).

#### `baton learn history`

```
baton learn history [--limit N]
```

Show resolution history — resolved/applied issues with outcomes.

#### `baton learn reset`

```
baton learn reset --issue ID
```

Reopen a resolved or applied issue.  If an override was auto-applied,
the user should also remove the corresponding entry from
`learned-overrides.json`.

#### `baton learn run-cycle`

```
baton learn run-cycle [--run] [--dry-run] [--template PATH]
```

Instantiate the bundled learning-cycle plan template and optionally
execute it via `baton execute run`.  The cycle collects execution data,
analyzes patterns, proposes improvements, requires human approval,
applies changes, and documents outcomes.

| Argument | Required | Description |
|----------|----------|-------------|
| `--run` | No | Execute the cycle immediately after instantiating the plan |
| `--dry-run` | No | Print the `baton execute run` command that would run, without executing |
| `--template PATH` | No | Custom path to a learning-cycle plan template JSON file |

#### `baton learn improve`

```
baton learn improve [--run | --force | --report | --experiments | --history] [--min-tasks N] [--interval N]
```

Run a full improvement cycle (formerly `baton improve`).  Detects
anomalies, generates recommendations, auto-applies safe changes,
escalates risky ones, and starts experiments.

| Argument | Required | Description |
|----------|----------|-------------|
| `--run` | No | Run a full improvement cycle |
| `--force` | No | Force-run bypassing the data-threshold check |
| `--report` | No | Show the latest improvement report |
| `--experiments` | No | Show active experiments |
| `--history` | No | Show all improvement reports |
| `--min-tasks N` | No | Override `BATON_MIN_TASKS` for this run |
| `--interval N` | No | Override `BATON_ANALYSIS_INTERVAL` for this run |

---

### `baton context`

Situational awareness for agents: current task state, performance briefings,
and knowledge gap analysis.

#### `baton context current`

Show what task, phase, step, and agent are currently active.

```
baton context current [--db PATH] [--central] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--db PATH` | No | Explicit path to baton.db |
| `--central` | No | Query the central database at `~/.baton/central.db` |
| `--json` | No | Machine-readable JSON output |

**When to use:** Quick check of the active execution state without the
full output of `baton execute status`.

#### `baton context briefing`

Print a performance briefing for an agent about to be dispatched.

```
baton context briefing AGENT [--db PATH] [--central] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `AGENT` | Yes | Name of the agent to brief (e.g. `backend-engineer--python`) |
| `--db PATH` | No | Explicit path to baton.db |
| `--central` | No | Query the central database |
| `--json` | No | Machine-readable JSON output |

**When to use:** Before dispatching an agent, to review its recent success
rate, common failure modes, and relevant patterns from past executions.

#### `baton context gaps`

Show knowledge gaps identified across recent retrospectives.

```
baton context gaps [--min-frequency N] [--agent NAME] [--db PATH] [--central] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--min-frequency N` | No | Minimum occurrence count to include a gap (default: 1) |
| `--agent NAME` | No | Filter gaps to a specific agent |
| `--db PATH` | No | Explicit path to baton.db |
| `--central` | No | Query the central database |
| `--json` | No | Machine-readable JSON output |

**When to use:** Identify recurring knowledge gaps that could be addressed
by creating knowledge packs or improving agent definitions.

---

### `baton beads`

Inspect and manage Bead memory -- structured agent discoveries, decisions,
and warnings that persist across steps within and across tasks.

#### `baton beads create`

Create a bead manually.

```
baton beads create --type TYPE --content TEXT [--task-id TASK_ID] [--step-id STEP_ID]
    [--agent AGENT] [--tag TAG] [--file FILE] [--confidence LEVEL]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--type TYPE` | Yes | Bead type: `discovery`, `decision`, `warning`, `outcome`, `planning` |
| `--content TEXT` | Yes | The bead text (alias: `--body`) |
| `--task-id TASK_ID` | No | Task ID to scope this bead (defaults to `$BATON_TASK_ID`; omit for project-scoped) |
| `--step-id STEP_ID` | No | Step ID within the execution |
| `--agent AGENT` | No | Agent name to record as the bead author (default: `orchestrator`) |
| `--tag TAG` | No | Semantic tag (repeatable) |
| `--file FILE` | No | Affected file path (repeatable) |
| `--confidence LEVEL` | No | Confidence level: `high`, `medium`, `low` (default: `medium`) |

**When to use:** To capture discoveries, decisions, warnings, or
incidents that future agents should know about.  Inside an execution
loop, `--task-id` and `--step-id` are inherited automatically.

#### `baton beads list`

List beads with optional filters.

```
baton beads list [--type TYPE] [--status STATUS] [--task TASK_ID] [--tag TAG] [--limit N]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--type TYPE` | No | Filter by type: `discovery`, `decision`, `warning`, `outcome`, `planning` |
| `--status STATUS` | No | Filter by status: `open`, `closed`, `archived` |
| `--task TASK_ID` | No | Filter by task ID |
| `--tag TAG` | No | Filter by tag (AND semantics when repeated) |
| `--limit N` | No | Maximum number of results (default: 20) |

#### `baton beads show`

Show a single bead as JSON.

```
baton beads show BEAD_ID
```

| Argument | Required | Description |
|----------|----------|-------------|
| `BEAD_ID` | Yes | Bead ID (e.g. `bd-a1b2`) |

#### `baton beads ready`

List open beads whose `blocked_by` dependencies are satisfied.

```
baton beads ready [--task TASK_ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--task TASK_ID` | No | Task ID to scope the query (defaults to active task) |

**When to use:** Before dispatching a step, check which beads are actionable.

#### `baton beads close`

Close a bead with a summary.

```
baton beads close BEAD_ID [--summary TEXT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `BEAD_ID` | Yes | Bead ID to close |
| `--summary TEXT` | No | Compacted summary of the bead's outcome |

#### `baton beads annotate`

Append a timestamped note to an existing bead's content without
changing its status.  Works on beads in any status (open, closed,
archived).

```
baton beads annotate BEAD_ID --note TEXT [--agent NAME]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `BEAD_ID` | Yes | Bead ID to annotate |
| `--note TEXT` | Yes | Note to append (alias: `--content`) |
| `--agent NAME` | No | Agent authoring the annotation |

**When to use:** After an agent interacts with a bead — acts on it,
discovers it's wrong, or finds new context.  Keeps beads current
without creating a separate extension bead for every observation.

#### `baton beads link`

Add a typed link between two beads.

```
baton beads link SOURCE_ID \
    (--relates-to TARGET_ID | --contradicts TARGET_ID | --extends TARGET_ID | --blocks TARGET_ID | --validates TARGET_ID)
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE_ID` | Yes | Source bead ID |
| `--relates-to TARGET_ID` | No | Add a `relates_to` link |
| `--contradicts TARGET_ID` | No | Add a `contradicts` link |
| `--extends TARGET_ID` | No | Add an `extends` link |
| `--blocks TARGET_ID` | No | Add a `blocks` link |
| `--validates TARGET_ID` | No | Add a `validates` link |

Exactly one link type flag is required.

#### `baton beads cleanup`

Archive old closed beads (memory decay).

```
baton beads cleanup [--ttl HOURS] [--task TASK_ID] [--dry-run]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--ttl HOURS` | No | Archive beads closed more than HOURS ago (default: 168 = 7 days) |
| `--task TASK_ID` | No | Limit decay to beads from this task ID |
| `--dry-run` | No | Show how many beads would be archived without modifying anything |

#### `baton beads promote`

Promote a bead to a persistent knowledge document.

```
baton beads promote BEAD_ID --pack PACK_NAME
```

| Argument | Required | Description |
|----------|----------|-------------|
| `BEAD_ID` | Yes | Bead ID to promote (e.g. `bd-a1b2`) |
| `--pack PACK_NAME` | Yes | Knowledge pack to add the document to (e.g. `project-context`) |

**When to use:** When a bead contains a high-value discovery or decision
that should become permanent project knowledge, not subject to memory decay.

#### `baton beads graph`

Show the dependency graph for a task's beads.

```
baton beads graph TASK_ID
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | Yes | Task ID whose bead graph to display |

**When to use:** Visualise the relationships between beads in a task to
understand the decision and discovery chain.

---

### `baton query`

Query this project's execution history and agent performance from the local
`baton.db` database.

```
baton query [SUBCOMMAND] [ARG] \
    [--sql SQL] [--format FORMAT] [--days N] [--limit N] \
    [--status STATUS] [--min-frequency N] [--hours N] \
    [--db PATH] [--central]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SUBCOMMAND` | No | Predefined query (see table below) |
| `ARG` | No | Subcommand argument (e.g. agent name for `agent-history`, task ID for `task-detail`) |
| `--sql SQL` | No | Run arbitrary read-only SQL (SELECT only) |
| `--format FORMAT` | No | Output format: `table` (default), `json`, `csv` |
| `--days N` | No | Days window for time-bounded queries (default: 30) |
| `--limit N` | No | Maximum rows to return (default: 20) |
| `--status STATUS` | No | Filter tasks by status (for the `tasks` subcommand) |
| `--min-frequency N` | No | Minimum occurrence frequency for `knowledge-gaps` (default: 1) |
| `--hours N` | No | Staleness threshold in hours for `stalled` (default: 24) |
| `--db PATH` | No | Explicit path to baton.db |
| `--central` | No | Query the central database at `~/.baton/central.db` |

**Predefined queries:**

| Subcommand | Description |
|------------|-------------|
| `agent-reliability` | Agent success rates and reliability metrics |
| `agent-history AGENT` | Execution history for a specific agent |
| `tasks` | List recent tasks with status |
| `task-detail TASK_ID` | Detailed breakdown of a specific task |
| `knowledge-gaps` | Knowledge gaps identified across executions |
| `roster-recommendations` | Agent roster optimisation suggestions |
| `gate-stats` | QA gate pass/fail statistics |
| `cost-by-type` | Cost breakdown by task type |
| `cost-by-agent` | Cost breakdown by agent |
| `current` | Current execution state |
| `patterns` | Learned orchestration patterns |
| `plans` | Recent plans |
| `phase-status` | Phase completion status across tasks |
| `forge-sessions` | PMO forge (plan generation) sessions |
| `stalled` | Executions stalled beyond threshold |
| `portfolio` | Portfolio-level summary across projects |

**Examples:**

```bash
# Show agent reliability over the last 30 days
baton query agent-reliability

# Show all tasks with status 'failed'
baton query tasks --status failed

# Run a custom SQL query
baton query --sql "SELECT agent_name, COUNT(*) FROM step_results GROUP BY agent_name"

# Export as CSV
baton query tasks --format csv
```

---

### `baton cquery`

Cross-project SQL queries against the central database (`~/.baton/central.db`).
For per-project queries, use `baton query` instead.

```
baton cquery [QUERY] [--format FORMAT] [--tables] [--table TABLE] [--db PATH]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `QUERY` | No | SQL statement or shortcut name |
| `--format FORMAT` | No | Output format: `table` (default), `json`, `csv` |
| `--tables` | No | List all tables (and views) in central.db |
| `--table TABLE` | No | Describe a specific table: show column names and types |
| `--db PATH` | No | Override path to central.db (default: `~/.baton/central.db`) |

**Shortcuts:**

| Shortcut | Description |
|----------|-------------|
| `agents` | Agent performance across all projects |
| `costs` | Cost summary across all projects |
| `gaps` | Knowledge gaps across all projects |
| `failures` | Failed executions across all projects |
| `mapping` | External source mappings |

**Examples:**

```bash
# List all tables in central.db
baton cquery --tables

# Use a shortcut
baton cquery agents

# Run a custom cross-project query
baton cquery "SELECT project_id, COUNT(*) FROM executions GROUP BY project_id"
```

---

### `baton sync`

Sync project data to the central database (`~/.baton/central.db`).

```
baton sync [SUBCOMMAND] [--all] [--project ID] [--rebuild]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SUBCOMMAND` | No | Optional subcommand: `status` |
| `--all` | No | Sync all registered projects |
| `--project ID` | No | Sync a specific project by ID |
| `--rebuild` | No | Full rebuild (delete all central rows then re-sync) |

**`baton sync`** (no flags) syncs the current project.

**`baton sync --all`** syncs all projects registered with the PMO.

**`baton sync status`** shows the current sync state (last sync time,
row counts).

**`baton sync --rebuild`** performs a destructive rebuild: deletes all
central rows for the target project(s) and re-imports from the project
database.  Use this after schema changes or to fix sync corruption.

---

### `baton source`

Manage external work-item source connections (Azure DevOps, GitHub Issues,
Jira, Linear).

#### `baton source add`

Register an external source connection.

```
baton source add TYPE --name NAME [--org ORG] [--project PROJECT] [--pat-env ENV_VAR] [--url URL]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TYPE` | Yes | Source type: `ado`, `github`, `jira`, `linear` |
| `--name NAME` | Yes | Display name for this source |
| `--org ORG` | No | Organisation or account name (ADO/GitHub) |
| `--project PROJECT` | No | Project name within the source (ADO/Jira) |
| `--pat-env ENV_VAR` | No | Name of environment variable holding the PAT/token |
| `--url URL` | No | Base URL for self-hosted instances (Jira Server, GitHub Enterprise) |

#### `baton source list`

List all registered external sources.

```
baton source list
```

#### `baton source sync`

Pull work items from an external source.

```
baton source sync [SOURCE_ID] [--all]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE_ID` | No | Source ID to sync (see `baton source list`) |
| `--all` | No | Sync all registered sources |

#### `baton source remove`

Remove a registered external source.

```
baton source remove SOURCE_ID
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE_ID` | Yes | Source ID to remove |

#### `baton source map`

Map an external work item to a baton project/task.

```
baton source map SOURCE_ID EXTERNAL_ID PROJECT_ID TASK_ID [--type TYPE]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE_ID` | Yes | Source ID |
| `EXTERNAL_ID` | Yes | External item ID (e.g. ADO work item number) |
| `PROJECT_ID` | Yes | Baton project ID |
| `TASK_ID` | Yes | Baton task/execution ID |
| `--type TYPE` | No | Relationship type: `implements` (default), `blocks`, `related` |

---

### `baton pmo`

Portfolio management overlay -- board, projects, and program health.

#### `baton pmo serve`

Start the PMO HTTP server.

```
baton pmo serve [--port PORT] [--host HOST]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--port PORT` | No | Port to listen on (default: 8741) |
| `--host HOST` | No | Host to bind to (default: `127.0.0.1`) |

**Note:** Requires the API extras: `pip install agent-baton[api]`.

#### `baton pmo status`

Print a Kanban board summary of all registered projects.

```
baton pmo status
```

#### `baton pmo add`

Register a project with the PMO.

```
baton pmo add --id ID --name NAME --path PATH --program PROGRAM [--color COLOR]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--id ID` | Yes | Project slug identifier (e.g. `nds`) |
| `--name NAME` | Yes | Human-readable project name |
| `--path PATH` | Yes | Absolute filesystem path to the project root |
| `--program PROGRAM` | Yes | Program this project belongs to (e.g. `NDS`, `ATL`) |
| `--color COLOR` | No | Optional display colour for the project |

#### `baton pmo health`

Print program health bar summary.

```
baton pmo health
```

---

### `baton serve`

Start the HTTP API server.

```
baton serve [--port PORT] [--host HOST] [--token TOKEN] [--team-context DIR]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--port PORT` | No | Port to listen on (default: 8741) |
| `--host HOST` | No | Host to bind to (default: `127.0.0.1`) |
| `--token TOKEN` | No | API token for authentication.  Also reads `BATON_API_TOKEN` env var (CLI flag takes precedence). |
| `--team-context DIR` | No | Path to the team-context root directory (default: `.claude/team-context`) |

**Note:** Requires the API extras: `pip install agent-baton[api]`.

**When to use:** To expose the baton engine as an HTTP API for external
integrations or the PMO UI frontend.  `baton pmo serve` is a convenience
wrapper around this command.

---

### `baton cleanup`

Remove old execution artifacts (traces, events, retrospectives).

```
baton cleanup [--retention-days N] [--dry-run] [--team-context PATH]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--retention-days N` | No | Keep files newer than this many days (default: 90) |
| `--dry-run` | No | Show what would be removed without deleting |
| `--team-context PATH` | No | Path to team-context directory |

**When to use:** Periodically to reclaim disk space from accumulated
execution traces and retrospectives.  Always run with `--dry-run` first
to review what will be removed.

---

### `baton migrate-storage` (deprecated)

> **Deprecated.** Use `baton sync --migrate-storage` instead. The shim still works and prints a `DEPRECATED:` warning to stderr. All migration flags (`--dry-run`, `--keep-files`, `--remove-files`, `--team-context`, `--migrate-verify`) are now hosted under `baton sync`.

---

### `baton status`

Show team-context file status -- a quick overview of what files exist in
`.claude/team-context/`.

```
baton status
```

**When to use:** Quick check that the team-context directory is properly
set up before starting execution.

---

### `baton dashboard`

Generate a usage dashboard summarising agent performance, cost, and
execution history.

```
baton dashboard [--write]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--write` | No | Write dashboard to disk |

---

### `baton trace`

List and inspect structured task execution traces.

```
baton trace [TASK_ID] [--last] [--summary TASK_ID] [--count N]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | No | Show timeline for a specific task |
| `--last` | No | Show timeline for the most recent task |
| `--summary TASK_ID` | No | Show compact summary for a specific task |
| `--count N` | No | Number of recent traces to list (default: 10) |

---

### `baton retro`

Show retrospectives generated at execution completion.

```
baton retro [TASK_ID]
```

---

### `baton usage`

Show usage statistics (token counts, cost, durations).

```
baton usage
```

---

### `baton telemetry`

Show or clear agent telemetry events.

```
baton telemetry
```

---

### `baton context-profile`

List and inspect agent context efficiency profiles.

```
baton context-profile
```

---

### `baton scores`

Show agent performance scorecards.

```
baton scores [--agent NAME] [--write] [--trends] [--teams]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--agent NAME` | No | Show scorecard for a specific agent |
| `--write` | No | Write scorecard report to disk |
| `--trends` | No | Show performance trends for all agents |
| `--teams` | No | Show team composition effectiveness |

---

### `baton evolve` (deprecated)

> **Deprecated.** Prompt-evolution proposals are produced by the unified learning loop. Use `baton learn run-cycle` instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton patterns`

Show or refresh learned orchestration patterns.

```
baton patterns [--refresh] [--task-type TYPE] [--min-confidence N] [--recommendations]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--refresh` | No | Re-analyse the usage log and update `learned-patterns.json` |
| `--task-type TYPE` | No | Show patterns for a specific task type |
| `--min-confidence N` | No | Filter patterns by minimum confidence (0.0-1.0) |
| `--recommendations` | No | Show sequencing recommendations for each task type |

---

### `baton budget`

Show or refresh budget tier recommendations based on usage history.

```
baton budget [--recommend] [--save] [--auto-apply]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--recommend` | No | Re-analyse the usage log and display fresh recommendations |
| `--save` | No | Save recommendations to `budget-recommendations.json` |
| `--auto-apply` | No | Show only auto-applicable (downgrade) recommendations above 80% confidence |

---

### `baton anomalies`

Detect and display system anomalies.

```
baton anomalies [--watch]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--watch` | No | Show anomaly detection status and trigger readiness |

---

### `baton experiment` (deprecated)

> **Deprecated.** Experiment tracking is folded into the unified learning loop. Use `baton learn run-cycle` (auto-apply, escalate, and rollback are handled automatically) or inspect active experiments with `baton learn improve --experiments`. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton improve` (deprecated)

> **Deprecated.** Use `baton learn improve` instead (same flags). The shim still works and prints a `DEPRECATED:` warning to stderr. Note: the `baton learn` group also adds `run-cycle` (templated learning plan) and the issue-tracking subcommands (`status`, `issues`, `analyze`, `apply`, `interview`, `history`, `reset`) which the legacy `baton improve` did not expose.

---

### `baton swarm`

Experimental Wave 6.2 Part A swarm dispatcher. Partition a codebase into
AST-independent chunks and (eventually) dispatch one Haiku agent per
chunk to apply a refactor directive.

```
baton swarm refactor DIRECTIVE_JSON [options]
```

**Gates** (both required, otherwise the command exits with code 2):

- `BATON_EXPERIMENTAL=swarm`
- `BATON_SWARM_ENABLED=1`

| Flag | Default | Description |
|------|---------|-------------|
| `DIRECTIVE_JSON` | (required) | JSON directive: `kind` + directive fields (e.g. `replace-import`, `rename-symbol`) |
| `--max-agents N` | 100 | Max parallel chunk agents (cap: 100) |
| `--language` | `python` | AST language (v1: python only) |
| `--model` | `claude-haiku` | LLM tier for chunk agents |
| `--codebase-root PATH` | cwd | Root of the project to refactor |
| `--dry-run` | false | Print preview and exit, no dispatch |
| `-y / --yes` | false | Skip interactive confirmation prompt |
| `--require-approval-bead [BEAD_ID]` | -- | Require a pre-filed approval bead. Defaults ON when `BATON_APPROVAL_MODE=team`. |

**Exit codes:** `2` = experimental flag not set; `1` = swarm disabled or
gate failure. See [docs/cli-reference.md#swarm-commands](../docs/cli-reference.md#swarm-commands)
for examples.

---

### `baton daemon`

Background execution management -- run the engine as a persistent daemon
process.

#### `baton daemon start`

Start daemon execution.

```
baton daemon start
```

#### `baton daemon status`

Show daemon status.

```
baton daemon status
```

#### `baton daemon stop`

Stop the running daemon.

```
baton daemon stop
```

#### `baton daemon list`

List all daemon workers.

```
baton daemon list
```

---

### `baton async`

Dispatch and track asynchronous tasks.

```
baton async [--pending] [--show ID] [--dispatch COMMAND] [--task-id ID] [--type TYPE]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--pending` | No | List only pending tasks |
| `--show ID` | No | Show a specific task's status |
| `--dispatch COMMAND` | No | Dispatch a new task |
| `--task-id ID` | No | Task ID for `--dispatch` (auto-generated if omitted) |
| `--type TYPE` | No | Dispatch type: `shell`, `script`, or `manual` (default: `shell`) |

---

### `baton decide`

Manage human decision requests.

```
baton decide [--list] [--all] [--show ID] [--resolve ID] [--option OPTION] [--rationale TEXT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--list` | No | List pending decision requests (default action) |
| `--all` | No | List all decision requests regardless of status |
| `--show ID` | No | Show full details of a single decision request |
| `--resolve ID` | No | Resolve a pending decision request |
| `--option OPTION` | No | Chosen option when using `--resolve` |
| `--rationale TEXT` | No | Optional rationale for the decision |

---

### `baton install`

Install agents and references to user or project scope.

```
baton install --scope {user,project} [--source SOURCE] [--force] [--upgrade] [--verify]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--scope` | Yes | Install to `user` (`~/.claude/`) or `project` (`.claude/`) scope |
| `--source SOURCE` | No | Path to the agent-baton repo root (default: current directory) |
| `--force` | No | Overwrite ALL existing files without prompting |
| `--upgrade` | No | Upgrade: overwrite agents + references but preserve settings, CLAUDE.md, knowledge packs, and team-context |
| `--verify` | No | Run post-install verification: check agents load, references readable, dirs writable |

---

### `baton uninstall`

Remove agent-baton files (agents, references, team-context).

```
baton uninstall --scope {project,user} [--yes] [--keep-data]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--scope` | Yes | Scope to uninstall from: `project` (`.claude/`) or `user` (`~/.claude/`) |
| `--yes`, `-y` | No | Skip confirmation prompt |
| `--keep-data` | No | Keep execution data (`team-context/`) -- only remove agents and references |

---

### Other Governance Commands

#### `baton compliance`

Show compliance reports.

```
baton compliance
```

#### `baton policy`

List or evaluate guardrail policy presets.

```
baton policy
```

#### `baton escalations`

Show or resolve agent escalations.

```
baton escalations
```

#### `baton validate`

Validate agent `.md` files.

```
baton validate
```

#### `baton spec-check`

Validate agent output against a spec.

```
baton spec-check
```

---

### Distribution Commands

#### `baton package`

Create or install agent-baton packages.

```
baton package
```

#### `baton publish`

Publish a package archive to a local registry, or initialise a new registry.

```
baton publish
```

#### `baton pull`

Install a package from a local registry directory.

```
baton pull
```

#### `baton transfer`

Transfer agents/knowledge/references between projects.

```
baton transfer
```

#### `baton verify-package` (deprecated)

> **Deprecated.** Use `baton sync --verify ARCHIVE` instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### Other Commands

#### `baton changelog`

Show agent changelog or list backups.

```
baton changelog
```

#### `baton events`

Query the event log for a task.

```
baton events
```

#### `baton incident`

Manage incident response workflows.

```
baton incident
```

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
            --gate-output "{output}"

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
0, `--result fail` otherwise.  Include the command output in `--gate-output`.

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

### "status must be one of: complete, failed"

**Cause:** `baton execute record --status pass` (or `done`, `success`,
`ok`, `dispatched`, etc.).

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

## Concurrent Execution

Multiple `baton execute start` calls in the same repository run concurrently
without interference because each execution uses its own task-scoped state
directory (`executions/<task-id>/execution-state.json`).  The challenge is
that each terminal session needs to know *which* execution it belongs to.

### Session Binding with `BATON_TASK_ID`

After `baton execute start`, the engine prints a copyable export line:

```
Session binding: export BATON_TASK_ID=<plan-task-id>
```

Run this command in the terminal to bind all subsequent `baton execute`
calls in that shell to the new execution.  Each terminal session maintains
its own binding independently.

**Typical two-session workflow:**

Terminal A:
```bash
baton plan "Add JWT auth" --save --explain
baton execute start
# Output includes: Session binding: export BATON_TASK_ID=task-auth-abc
export BATON_TASK_ID=task-auth-abc
baton execute next   # resolves to task-auth-abc via env var
```

Terminal B (concurrently):
```bash
baton plan "Fix dashboard bug" --save --explain
baton execute start
# Output includes: Session binding: export BATON_TASK_ID=task-fix-xyz
export BATON_TASK_ID=task-fix-xyz
baton execute next   # resolves to task-fix-xyz, not task-auth-abc
```

### Task-ID Resolution Order

Every subcommand resolves the target execution through this priority chain:

```
--task-id flag  →  BATON_TASK_ID env var  →  active-task-id.txt  →  None
```

| Mechanism | Scope | Notes |
|-----------|-------|-------|
| `--task-id FLAG` | Single invocation | Highest priority; overrides both env var and marker |
| `BATON_TASK_ID` | Shell session | Set once with `export`; persists for the session lifetime |
| `active-task-id.txt` | Repository | Updated by `baton execute switch`; single-execution fallback |

**Agentic callers** (Claude Code's orchestrator): env vars do not persist
across independent `Bash` tool calls.  Pass `--task-id` explicitly on every
CLI call when driving concurrent executions from an agent context.

### Inspecting the active binding

`baton execute status` shows which resolution path is in use:

```
Task:    task-auth-abc
Bound:   BATON_TASK_ID
Status:  running
...
```

### Re-binding after starting a new plan

When `BATON_TASK_ID` is set to a previous task and `baton execute start`
creates a new plan, the export hint prints the **new** plan's task ID.
Re-run the `export` command to rebind:

```bash
# BATON_TASK_ID is still set to the old task
baton execute start
# Session binding: export BATON_TASK_ID=brand-new-task-id
export BATON_TASK_ID=brand-new-task-id   # update the binding
```

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
- `compliance_fail_closed` — `true`/`false`/`null` — per-plan override for `BATON_COMPLIANCE_FAIL_CLOSED`. Set to `true` for regulated-data, HIGH/CRITICAL risk, or audit-controlled tasks. `null` (default) defers to the env var.

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

---

## Environment Variables

The full list of Baton environment variables. Mirrored in
[docs/cli-reference.md#environment-variables](../docs/cli-reference.md#environment-variables).

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Bind a shell session to a specific execution. Set after `baton execute start` to scope all subsequent commands. | auto-detected |
| `BATON_DB_PATH` | Override the project `baton.db` location. CLI walks upward from cwd if unset. | discovered |
| `BATON_APPROVAL_MODE` | PMO approval policy: `local` (self-approve) or `team` (different reviewer required). In `team` mode, `baton swarm` defaults `--require-approval-bead` ON. | `local` |
| `BATON_RUN_TOKEN_CEILING` | Per-run cumulative spend cap (USD float). Read fresh on every check; restored on `baton execute resume`. Selfheal/speculator/immune respect it; main `Executor.dispatch()` only warns at HIGH/CRITICAL run start (bd-3f80). | unset |
| `BATON_EXPERIMENTAL` | CSV opt-in for experimental subsystems. Required for `baton swarm` (`BATON_EXPERIMENTAL=swarm`). Exits with code 2 if unset. | unset |
| `BATON_SWARM_ENABLED` | Required in addition to `BATON_EXPERIMENTAL=swarm` to dispatch a swarm refactor. | unset |
| `BATON_SOULS_ENABLED` | Wave 6.1 Part B persistent agent souls (signing + revocation). | `0` |
| `BATON_PREDICT_ENABLED` | Wave 6.2 Part C predictive computation watcher / classifier / dispatcher. | `0` |
| `BATON_IMMUNE_ENABLED` | Immune-system monitoring loop. | `0` |
| `BATON_EXEC_BEADS_ENABLED` | Wave 6.1 Part C executable beads. Sandbox is process-level only — see `references/baton-patterns.md` trust-boundary section before extending to external-origin input. | `0` |
| `BATON_SKIP_GIT_NOTES_SETUP` | Silence install-time git-notes refspec setup and the runtime warning emitted by `NotesAdapter.write()` when the wildcard refspec is missing. | unset |
| `BATON_SELFHEAL_ENABLED` | Enable speculator/selfheal escalation on gate failure. Falsy values (`0`, `false`, `no`) are honoured and emit a `selfheal_suppressed` row to `compliance-audit.jsonl`. | `0` |
| `BATON_API_TOKEN` | Bearer token for the FastAPI server (`baton serve`). CLI `--token` flag takes precedence. | unset |
| `ANTHROPIC_API_KEY` | Required for AI risk classification and the Haiku planner classifier. | unset |
