# Agent Baton CLI Reference

Complete command reference for the `baton` CLI. Every command, flag, and
option is documented here with syntax, defaults, and usage examples.

---

## Overview

The `baton` CLI is a flat-subcommand tool powered by argparse. All
command modules live under `agent_baton/cli/commands/` and are
auto-discovered at startup. Each module exposes `register(subparsers)`
and `handler(args)`.

Commands are organized into functional groups:

| Group | Concern | Commands |
|-------|---------|----------|
| **Execution** | Plan, execute, and manage orchestrated tasks | `plan`, `execute`, `status`, `daemon`, `async`, `decide` |
| **Observe** | Traces, usage, dashboards, telemetry | `dashboard`, `trace`, `usage`, `telemetry`, `context-profile`, `retro`, `context`, `cleanup` |
| **Govern** | Risk, policy, compliance, validation | `classify`, `compliance`, `policy`, `escalations`, `validate`, `spec-check`, `detect` |
| **Improve** | Scoring, learning, patterns, budgets | `scores`, `learn`, `patterns`, `budget`, `changelog`, `anomalies` |
| **Distribute** | Packaging, publishing, installation | `package`, `publish`, `pull`, `install`, `transfer` |
| **Agents** | Agent discovery, routing, events | `agents`, `route`, `events`, `incident` |
| **PMO** | Portfolio management overlay | `pmo serve`, `pmo status`, `pmo add`, `pmo health` |
| **Sync** | Federated data sync | `sync`, `sync status` |
| **Query** | Project-local structured queries | `query` |
| **Cross-Project Query** | Cross-project SQL against central.db | `cquery` |
| **Source** | External work-item connections | `source add`, `source list`, `source sync`, `source remove`, `source map` |
| **API** | HTTP API server | `serve` |

---

## Installation & Setup

```bash
# Install the package in editable mode with dev dependencies
pip install -e ".[dev]"

# Install with API server support (FastAPI + uvicorn)
pip install -e ".[api]"

# Deploy agent definitions to ~/.claude/agents/ and references
scripts/install.sh

# Or use the CLI installer
baton install --scope user --source /path/to/agent-baton-repo
```

After installation, the `baton` command is available globally via the
console script entry point.

---

## Execution Commands

### `baton plan`

Create a data-driven execution plan for an orchestrated task.

```
baton plan SUMMARY [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUMMARY` | Yes | -- | Natural-language task summary |
| `--task-type TYPE` | No | auto-detected | Override task type: `new-feature`, `bug-fix`, `refactor`, `data-analysis`, `documentation`, `migration`, `test` |
| `--agents LIST` | No | auto-selected | Comma-separated agent names; bypasses auto-selection |
| `--project PATH` | No | cwd | Project root for stack detection |
| `--json` | No | false | Output plan as JSON instead of markdown |
| `--save` | No | false | Write `plan.json` and `plan.md` to `.claude/team-context/` |
| `--explain` | No | false | Print explanation of plan choices |
| `--knowledge PATH` | No | -- | Attach a knowledge document file globally to all steps (repeatable) |
| `--knowledge-pack PACK` | No | -- | Attach a knowledge pack by name globally to all steps (repeatable) |
| `--intervention LEVEL` | No | `low` | Escalation threshold for knowledge gaps: `low`, `medium`, `high` |

**Examples:**

```bash
# Create and save a plan with explanation
baton plan "Add JWT authentication middleware and integration tests" --save --explain

# Create a plan with explicit agents
baton plan "Migrate database schema" --save --agents "backend-engineer--python,test-engineer"

# Create a plan with attached knowledge
baton plan "Implement payment processing" --save \
    --knowledge docs/payment-api.md \
    --knowledge-pack compliance-rules

# Output plan as JSON for programmatic consumption
baton plan "Add caching layer" --json
```

**Related:** `baton execute start`, `baton classify`, `baton detect`

---

### `baton execute`

Drive an orchestrated task through the execution engine. This is a
command group with multiple subcommands.

#### `baton execute start`

Initialize execution state from a saved plan and return the first action.

```
baton execute start [--plan PATH] [--task-id ID]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--plan PATH` | No | `.claude/team-context/plan.json` | Path to plan.json |
| `--task-id ID` | No | auto | Target a specific execution by task ID |

**Side effects:**
- Creates `execution-state.json` in the task-scoped directory
- Starts an in-memory trace
- Publishes a `task.started` event
- Sets the active execution marker
- Prints `Session binding: export BATON_TASK_ID=<task-id>`

**Example:**

```bash
baton plan "Add API endpoints" --save --explain
baton execute start
# Copy the BATON_TASK_ID export from the output
export BATON_TASK_ID=task-abc123
```

---

#### `baton execute next`

Return the next action the orchestrator should perform.

```
baton execute next [--all] [--task-id ID]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--all` | No | false | Return all dispatchable actions as a JSON array (for parallel dispatch) |
| `--task-id ID` | No | auto | Target a specific execution |

Without `--all`, prints a single action in human-readable format.
With `--all`, prints a JSON array of all steps whose dependencies are
satisfied.

**Action types returned:** `DISPATCH`, `GATE`, `APPROVAL`, `COMPLETE`,
`FAILED`, `WAIT`, `TEAM_DISPATCH`.

---

#### `baton execute dispatched`

Mark a step as in-flight (dispatched but not yet complete).

```
baton execute dispatched --step-id ID --agent NAME [--task-id ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--step-id ID` | Yes | Step identifier, e.g. `1.1` |
| `--agent NAME` | Yes | Agent name, e.g. `backend-engineer--python` |

**Output:** `{"status": "dispatched", "step_id": "1.1"}`

Call this immediately after spawning a subagent, before `baton execute record`.

---

#### `baton execute record`

Record the outcome of a completed or failed step.

```
baton execute record --step-id ID --agent NAME [options] [--task-id ID]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--step-id ID` | Yes | -- | Step identifier, e.g. `1.1` |
| `--agent NAME` | Yes | -- | Agent name |
| `--status STATUS` | No | `complete` | `complete` or `failed` (no other values accepted) |
| `--outcome TEXT` | No | `""` | Free-text summary of what the agent did |
| `--files LIST` | No | `""` | Comma-separated list of files changed |
| `--commit HASH` | No | `""` | Git commit hash |
| `--tokens N` | No | `0` | Estimated token count |
| `--duration N` | No | `0.0` | Duration in seconds (float) |
| `--error TEXT` | No | `""` | Error detail if `--status failed` |

**Example:**

```bash
baton execute record \
    --step-id 1.1 \
    --agent backend-engineer--python \
    --status complete \
    --outcome "Added JWT middleware with login/logout endpoints" \
    --files "app/auth.py,app/routes.py" \
    --commit abc123f
```

---

#### `baton execute gate`

Record the result of a QA gate check.

```
baton execute gate --phase-id N --result pass|fail [--output TEXT] [--task-id ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--phase-id N` | Yes | Integer phase ID (1-based) |
| `--result` | Yes | `pass` or `fail` |
| `--output TEXT` | No | Gate command output or reviewer notes |

**Example:**

```bash
# Run the gate command, then record the result
pytest tests/ > /tmp/gate-output.txt 2>&1
baton execute gate --phase-id 1 --result pass --output "$(cat /tmp/gate-output.txt)"
```

---

#### `baton execute approve`

Record a human approval decision for a phase with `approval_required=True`.

```
baton execute approve --phase-id N --result DECISION [--feedback TEXT] [--task-id ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--phase-id N` | Yes | Phase ID requiring approval |
| `--result` | Yes | `approve`, `reject`, or `approve-with-feedback` |
| `--feedback TEXT` | No | Feedback text (required for `approve-with-feedback`) |

**State transitions:**
- `approve` -- execution continues to gate or next phase
- `reject` -- engine sets status to `failed`
- `approve-with-feedback` -- inserts a remediation phase with the feedback injected

**Example:**

```bash
baton execute approve --phase-id 2 --result approve-with-feedback \
    --feedback "Add error handling for the edge case where token is expired"
```

---

#### `baton execute amend`

Amend the running plan by adding phases or steps during execution.

```
baton execute amend --description TEXT [options] [--task-id ID]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--description TEXT` | Yes | Why this amendment is needed (audit log) |
| `--add-phase NAME:AGENT` | No | Add a phase (repeatable) |
| `--after-phase N` | No | Insert new phases after this phase ID (default: append) |
| `--add-step PHASE_ID:AGENT:DESC` | No | Add step to existing phase (repeatable) |

**Example:**

```bash
# Add a security review phase after phase 2
baton execute amend \
    --description "Security review needed for auth changes" \
    --add-phase "security-review:security-reviewer" \
    --after-phase 2

# Add a step to an existing phase
baton execute amend \
    --description "Need migration script for schema change" \
    --add-step "1:backend-engineer--python:Write database migration script"
```

---

#### `baton execute team-record`

Record a team member completion within a team step.

```
baton execute team-record --step-id S --member-id M --agent NAME [options] [--task-id ID]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--step-id S` | Yes | -- | Parent team step ID, e.g. `2.1` |
| `--member-id M` | Yes | -- | Team member ID, e.g. `2.1.a` |
| `--agent NAME` | Yes | -- | Agent name |
| `--status` | No | `complete` | `complete` or `failed` |
| `--outcome TEXT` | No | `""` | Summary of work done |
| `--files F` | No | `""` | Comma-separated files changed |

---

#### `baton execute complete`

Finalize a completed execution run.

```
baton execute complete [--task-id ID]
```

**Side effects:**
- Sets execution state `status = complete`
- Writes trace file to `.claude/team-context/traces/`
- Writes usage record to `usage-log.jsonl`
- Generates retrospective to `retrospectives/`
- Publishes `task.completed` event
- Auto-syncs to central.db (best-effort)

---

#### `baton execute status`

Show current execution state without advancing it.

```
baton execute status [--task-id ID]
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

---

#### `baton execute resume`

Resume execution after a crash or interrupted session.

```
baton execute resume [--task-id ID]
```

Loads `execution-state.json` from disk, reconnects the trace, and
returns the next action. Steps in `dispatched` status will be
re-dispatched.

---

#### `baton execute list`

List all executions (active and completed).

```
baton execute list
```

**Output:**

```
  TASK ID                                 STATUS              STEPS      PID  SUMMARY
------------------------------------------------------------------------------------------
* task-auth-abc                           running               2/4      -  Add JWT auth middleware...
  task-fix-xyz                            complete              3/3      -  Fix dashboard rendering...
```

Active execution is marked with `*`.

---

#### `baton execute switch`

Switch the active execution to a different task ID.

```
baton execute switch TASK_ID
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | Yes | Task ID to switch to |

---

### `baton status`

Show team-context file status (which recovery files exist).

```
baton status
```

**Output:**

```
Team context status:
  + plan.json
  + execution-state.json
  - context.md
  + mission-log.md
```

**Related:** `baton execute status`

---

### `baton daemon`

Background execution management. Runs the async worker (and optionally
the HTTP API server) as a daemon process.

#### `baton daemon start`

```
baton daemon start --plan FILE [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--plan FILE` | Yes* | -- | Path to MachinePlan JSON file (*required unless `--resume`) |
| `--max-parallel N` | No | `3` | Maximum parallel agents |
| `--dry-run` | No | false | Use DryRunLauncher (no real agent calls) |
| `--foreground` | No | false | Run in foreground (don't daemonize) |
| `--resume` | No | false | Resume from saved execution state |
| `--project-dir DIR` | No | cwd | Project directory for execution |
| `--serve` | No | false | Also start HTTP API server in the same process |
| `--port PORT` | No | `8741` | Port for API server (with `--serve`) |
| `--host HOST` | No | `127.0.0.1` | Bind address for API server (with `--serve`) |
| `--token TOKEN` | No | -- | Bearer token for API auth (with `--serve`) |
| `--task-id ID` | No | -- | Namespace this daemon under a specific task ID |

**Example:**

```bash
# Start background daemon with API server
baton daemon start --plan .claude/team-context/plan.json \
    --serve --port 8741 --max-parallel 4

# Dry-run in foreground for testing
baton daemon start --plan plan.json --dry-run --foreground

# Resume a crashed daemon
baton daemon start --resume --task-id task-abc123
```

---

#### `baton daemon status`

```
baton daemon status [--task-id ID]
```

Shows whether the daemon is running, its PID, task ID, phase, step
progress, gates, and elapsed time.

---

#### `baton daemon stop`

```
baton daemon stop [--task-id ID]
```

Sends a stop signal to the running daemon process.

---

#### `baton daemon list`

```
baton daemon list [--project-dir DIR]
```

Lists all daemon workers with their task IDs, PIDs, and liveness status.

---

### `baton async`

Dispatch and track asynchronous tasks.

```
baton async [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--dispatch COMMAND` | No | Dispatch a new async task |
| `--show ID` | No | Show a specific task's status |
| `--pending` | No | List only pending tasks |
| `--task-id ID` | No | Task ID for `--dispatch` (auto-generated if omitted) |
| `--type TYPE` | No | Dispatch type: `shell`, `script`, or `manual` (default: `shell`) |

Without flags, lists all async tasks with their status.

**Examples:**

```bash
# Dispatch a shell command
baton async --dispatch "pytest tests/ -x" --task-id my-test-run

# Check status
baton async --show my-test-run

# List pending tasks
baton async --pending
```

---

### `baton decide`

Manage human decision requests generated during daemon/async execution.

```
baton decide [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--list` | No | List pending decision requests (default action) |
| `--all` | No | List all decision requests regardless of status |
| `--show ID` | No | Show full details of a single decision request |
| `--resolve ID` | No | Resolve a pending decision request |
| `--option OPTION` | No | Chosen option when using `--resolve` (required) |
| `--rationale TEXT` | No | Optional rationale for the decision |

**Examples:**

```bash
# List pending decisions
baton decide

# Show details of a decision request
baton decide --show req-abc123

# Resolve a decision
baton decide --resolve req-abc123 --option "approve" --rationale "Looks good after review"
```

---

## Observe Commands

### `baton dashboard`

Generate or display the usage dashboard.

```
baton dashboard [--write]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--write` | No | Write dashboard to disk instead of printing to stdout |

---

### `baton trace`

List and inspect structured task execution traces.

```
baton trace [TASK_ID] [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | No | Show timeline for a specific task |
| `--last` | No | Show timeline for the most recent task |
| `--summary TASK_ID` | No | Show compact summary for a specific task |
| `--count N` | No | Number of recent traces to list (default: 10) |

**Examples:**

```bash
# List recent traces
baton trace

# Show timeline for most recent task
baton trace --last

# Show timeline for a specific task
baton trace task-abc123

# Show compact summary
baton trace --summary task-abc123
```

---

### `baton usage`

Show usage statistics from the usage log.

```
baton usage [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--recent N` | No | Show the N most recent records |
| `--agent NAME` | No | Show stats for a specific agent |

Without flags, prints an aggregate usage summary including total tasks,
agents used, tokens consumed, outcomes, and top agents.

**Examples:**

```bash
# Summary view
baton usage

# Last 5 usage records
baton usage --recent 5

# Stats for a specific agent
baton usage --agent backend-engineer--python
```

---

### `baton telemetry`

Show or clear agent telemetry events.

```
baton telemetry [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--agent NAME` | No | Show events for a specific agent |
| `--recent N` | No | Show the N most recent events |
| `--clear` | No | Clear the telemetry log |

Without flags, prints a telemetry summary grouped by agent and event
type, with file read/write counts.

---

### `baton context-profile`

List and inspect agent context efficiency profiles.

```
baton context-profile [TASK_ID] [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TASK_ID` | No | Show context profile for a specific task |
| `--agent NAME` | No | Show aggregate context stats for a specific agent |
| `--generate TASK_ID` | No | Generate and save a context profile from trace data |
| `--report` | No | Print a full markdown context efficiency report |
| `--count N` | No | Number of recent profiles to list (default: 10) |

**Examples:**

```bash
# Generate a profile from trace data
baton context-profile --generate task-abc123

# View a generated profile
baton context-profile task-abc123

# Agent-level aggregate stats
baton context-profile --agent backend-engineer--python

# Full report
baton context-profile --report
```

---

### `baton retro`

Show retrospectives generated after task completion.

```
baton retro [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--task-id ID` | No | Show a specific retrospective |
| `--search KEYWORD` | No | Search retrospectives by keyword |
| `--recommendations` | No | Extract roster recommendations from all retrospectives |
| `--count N` | No | Number of recent retrospectives to list (default: 10) |

**Examples:**

```bash
# List recent retrospectives
baton retro

# View a specific retrospective
baton retro --task-id task-abc123

# Search for retrospectives mentioning "auth"
baton retro --search auth

# Extract roster recommendations
baton retro --recommendations
```

---

### `baton context`

Situational awareness for Claude agents. Queries baton.db for current
task state, agent briefings, and knowledge gaps.

#### `baton context current`

```
baton context current [--db PATH] [--central] [--json]
```

Shows what task, phase, step, and agent are currently active.

#### `baton context briefing`

```
baton context briefing AGENT [--db PATH] [--central] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `AGENT` | Yes | Agent name to brief (e.g. `backend-engineer--python`) |

Prints a performance briefing for an agent about to be dispatched.

#### `baton context gaps`

```
baton context gaps [--min-frequency N] [--agent NAME] [--db PATH] [--central] [--json]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--min-frequency N` | No | `1` | Minimum occurrence count to include a gap |
| `--agent NAME` | No | -- | Filter gaps to a specific agent |

Shows knowledge gaps identified across recent retrospectives.

**Shared flags** (all `context` subcommands):

| Flag | Description |
|------|-------------|
| `--db PATH` | Explicit path to baton.db |
| `--central` | Query central database at `~/.baton/central.db` |
| `--json` | Machine-readable JSON output |

---

### `baton cleanup`

Remove old execution artifacts (traces, events, retrospectives).

```
baton cleanup [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--retention-days N` | No | `90` | Keep files newer than this many days |
| `--dry-run` | No | false | Show what would be removed without deleting |
| `--team-context PATH` | No | `.claude/team-context` | Path to team-context directory |

**Example:**

```bash
# Preview what would be cleaned up
baton cleanup --dry-run --retention-days 30

# Actually clean up files older than 60 days
baton cleanup --retention-days 60
```

---

### `baton migrate-storage` (deprecated)

> **Deprecated.** Use [`baton sync --migrate-storage`](#baton-sync) instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

## Govern Commands

### `baton classify`

Classify task sensitivity and select a guardrail preset.

```
baton classify DESCRIPTION [--files FILE...]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `DESCRIPTION` | Yes | Task description to classify |
| `--files FILE...` | No | File paths affected (elevates risk from path patterns) |

**Output:**

```
Risk Level: MEDIUM
Preset: regulated-data
Confidence: 0.82
Signals: payment, user-data
Explanation: Task touches payment processing and user PII fields.
```

**Risk levels:** `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`

**Example:**

```bash
baton classify "Update user payment processing logic" \
    --files app/payments.py app/models/user.py
```

---

### `baton compliance`

Show compliance reports generated during task execution.

```
baton compliance [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--task-id ID` | No | -- | Show a specific compliance report |
| `--count N` | No | `5` | Number of recent reports to list |

---

### `baton policy`

List, show, or evaluate guardrail policy presets.

```
baton policy [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--show NAME` | No | Show rules of a named policy preset |
| `--check AGENT` | No | Agent name to evaluate (use with `--preset`) |
| `--preset NAME` | No | Policy preset name to evaluate against (use with `--check`) |
| `--paths PATHS` | No | Comma-separated allowed file paths (use with `--check`) |
| `--tools TOOLS` | No | Comma-separated tools available (use with `--check`) |

Without flags, lists all available policy presets.

**Examples:**

```bash
# List all presets
baton policy

# Show a specific preset's rules
baton policy --show regulated-data

# Check an agent against a preset
baton policy --check backend-engineer--python \
    --preset regulated-data \
    --paths "app/,tests/" \
    --tools "Bash,Read,Edit"
```

---

### `baton escalations`

Show, resolve, or clear agent escalations.

```
baton escalations [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--all` | No | Show all escalations, including resolved |
| `--resolve AGENT ANSWER` | No | Resolve the oldest pending escalation for AGENT with ANSWER |
| `--clear` | No | Remove all resolved escalations |

Without flags, shows only pending escalations.

**Example:**

```bash
# Resolve an escalation
baton escalations --resolve backend-engineer--python "Use the v2 API endpoint instead"
```

---

### `baton validate`

Validate agent definition `.md` files for correct structure and YAML
frontmatter.

```
baton validate PATHS... [--strict]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `PATHS...` | Yes | File or directory paths to validate |
| `--strict` | No | Treat warnings as errors (exit code 1 if any warnings) |

**Example:**

```bash
# Validate all agents in a directory
baton validate agents/

# Validate a specific file in strict mode
baton validate agents/backend-engineer.md --strict
```

**Exit code:** `1` if any errors found (or warnings in strict mode).

---

### `baton spec-check`

Validate agent output against a spec (JSON schema, file structure, or
module exports).

```
baton spec-check [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--json DATA_FILE` | No | JSON data file to validate |
| `--files ROOT` | No | Directory root to check for expected files |
| `--exports MODULE` | No | Python module file to check for expected exports |
| `--schema SCHEMA_FILE` | No | JSON Schema file (use with `--json`) |
| `--expect NAMES` | No | Comma-separated expected files or names (use with `--files`/`--exports`) |

One of three modes must be specified:

```bash
# Validate JSON against a schema
baton spec-check --json output.json --schema schema.json

# Check file structure
baton spec-check --files src/ --expect "models.py,routes.py,tests/"

# Check module exports
baton spec-check --exports app/models.py --expect "User,Session,Token"
```

**Exit code:** `1` if validation fails.

---

### `baton detect`

Detect the project stack (language and framework) from config files.

```
baton detect [--path PATH]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--path PATH` | No | cwd | Project root path |

**Output:**

```
Language:  python
Framework: fastapi
Signals:   pyproject.toml, requirements.txt
```

---

## Improve Commands

### `baton scores`

Show agent performance scorecards.

```
baton scores [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--agent NAME` | No | Show scorecard for a specific agent |
| `--write` | No | Write scorecard report to disk |
| `--trends` | No | Show performance trends for all agents |

Without flags, prints the full scorecard report to stdout.

**Example:**

```bash
# View trends for all agents
baton scores --trends

# Specific agent scorecard
baton scores --agent backend-engineer--python
```

---

### `baton evolve` (deprecated)

> **Deprecated.** Prompt-evolution proposals are now produced by the unified learning loop. Use [`baton learn run-cycle`](#baton-learn) instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton patterns`

Display and refresh learned orchestration patterns.

```
baton patterns [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--refresh` | No | false | Re-analyse the usage log and update `learned-patterns.json` |
| `--task-type TYPE` | No | -- | Show patterns for a specific task type |
| `--min-confidence N` | No | `0.0` | Filter patterns by minimum confidence (0.0-1.0) |
| `--recommendations` | No | false | Show sequencing recommendations for each task type |

**Examples:**

```bash
# Refresh patterns from usage log
baton patterns --refresh

# View high-confidence patterns only
baton patterns --min-confidence 0.8

# Sequencing recommendations
baton patterns --recommendations
```

---

### `baton budget`

Show or refresh budget tier recommendations based on usage history.

```
baton budget [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--recommend` | No | Re-analyse the usage log and display fresh recommendations |
| `--save` | No | Save recommendations to `budget-recommendations.json` |
| `--auto-apply` | No | Show only auto-applicable (downgrade) recommendations above 80% confidence |

Without flags, shows previously saved recommendations.

**Example:**

```bash
# Generate and save budget recommendations
baton budget --recommend --save

# View auto-applicable downgrades
baton budget --auto-apply
```

---

### `baton changelog`

Show agent changelog entries or list backup files.

```
baton changelog [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--agent NAME` | No | Show history for a specific agent |
| `--backups [NAME]` | No | List backup files. With a name: filter to that agent. Without: all backups. |

**Examples:**

```bash
# Show full changelog
baton changelog

# Show changelog for one agent
baton changelog --agent backend-engineer--python

# List all backup files
baton changelog --backups

# List backups for a specific agent
baton changelog --backups backend-engineer--python
```

---

### `baton anomalies`

Detect and display system anomalies.

```
baton anomalies [--watch]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--watch` | No | Show anomaly detection status and trigger readiness |

Without flags, detects and displays current anomalies with severity,
agent, metric, current value, threshold, and evidence.

---

### `baton experiment` (deprecated)

> **Deprecated.** Experiment tracking is folded into the unified learning loop. Use [`baton learn run-cycle`](#baton-learn) (which handles auto-apply, escalate, and experiment rollback automatically) instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton improve` (deprecated)

> **Deprecated.** Use [`baton learn improve`](#baton-learn) instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton learn`

Learning automation — track, analyze, propose, and apply fixes for
recurring issues. This is a command group with subcommands.

```
baton learn [SUBCOMMAND] [options]
```

| Subcommand | Description |
|------------|-------------|
| `status` | Dashboard: open issues by type/severity, auto-apply stats |
| `issues` | List learning issues (filterable by `--type`, `--severity`, `--status`) |
| `analyze` | Run analysis: compute confidence, mark auto-apply candidates |
| `apply` | Apply a specific fix (`--issue ID`) or all proposed (`--all-safe`) |
| `interview` | Interactive structured dialogue for human-directed decisions |
| `history` | Show resolution history (`--limit N`, default 20) |
| `reset` | Reopen an issue and rollback its applied override (`--issue ID`) |
| `run-cycle` | Instantiate the learning-cycle plan template (and optionally execute it) |
| `improve` | Run the improvement loop or view reports (formerly `baton improve`) |

#### `baton learn run-cycle`

Instantiate the learning-cycle plan template. The cycle collects
execution data, analyzes patterns, proposes improvements, requires
human approval, applies changes, and documents outcomes.

```
baton learn run-cycle [--run] [--dry-run] [--template PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--run` | false | Execute the cycle immediately via `baton execute run` after creating the plan |
| `--dry-run` | false | Print the `baton execute run` command that would be invoked, without executing |
| `--template PATH` | bundled | Path to a custom learning-cycle plan template JSON file |

#### `baton learn improve`

Run a full improvement cycle (formerly `baton improve`). Detects
anomalies, generates recommendations, auto-applies safe changes,
escalates risky ones, and starts experiments.

```
baton learn improve [--run | --force | --report | --experiments | --history] [--min-tasks N] [--interval N]
```

| Flag | Description |
|------|-------------|
| `--run` | Run a full improvement cycle |
| `--force` | Force-run a cycle bypassing the data-threshold check |
| `--report` | Show the latest improvement report |
| `--experiments` | Show active experiments |
| `--history` | Show all improvement reports |
| `--min-tasks N` | Minimum total tasks before analysis fires (overrides `BATON_MIN_TASKS`) |
| `--interval N` | Re-analyze every N new tasks (overrides `BATON_ANALYSIS_INTERVAL`) |

**Examples:**

```bash
# Dashboard
baton learn status

# List high-severity issues
baton learn issues --severity high

# Analyze and auto-apply safe fixes
baton learn analyze
baton learn apply --all-safe

# Run a full improvement cycle
baton learn improve --run

# Instantiate and execute the learning cycle
baton learn run-cycle --run
```

---

## Distribute Commands

### `baton package`

Create, inspect, or install agent-baton package archives.

```
baton package [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--name NAME` | No | Create a package archive with this name |
| `--info ARCHIVE` | No | Show manifest of an existing `.tar.gz` package |
| `--install ARCHIVE` | No | Install an agent-baton package |
| `--version VER` | No | Package version (default: `1.0.0`) |
| `--description TEXT` | No | Package description |
| `--include-knowledge` | No | Include knowledge packs in the package |
| `--no-agents` | No | Exclude agents from the package |
| `--no-references` | No | Exclude references from the package |
| `--output-dir DIR` | No | Directory to write the archive to (default: cwd) |
| `--scope SCOPE` | No | Install scope: `user` or `project` (default: `project`) |
| `--force` | No | Overwrite existing files when installing |
| `--project ROOT` | No | Source project root (default: cwd) |

**Examples:**

```bash
# Create a package
baton package --name my-agents --version 2.0.0 \
    --description "Custom agent definitions" --include-knowledge

# Inspect a package
baton package --info my-agents-2.0.0.tar.gz

# Install a package to user scope
baton package --install my-agents-2.0.0.tar.gz --scope user --force
```

---

### `baton publish`

Publish a package archive to a local registry directory, or initialize
a new registry.

```
baton publish ARCHIVE --registry PATH
baton publish --init PATH
```

| Argument | Required | Description |
|----------|----------|-------------|
| `ARCHIVE` | Yes* | Path to the `.tar.gz` archive (*unless using `--init`) |
| `--registry PATH` | Yes* | Path to the local registry directory (*required when publishing) |
| `--init PATH` | No | Initialize a new empty registry at PATH |

**Examples:**

```bash
# Initialize a registry
baton publish --init /shared/baton-registry

# Publish a package
baton publish my-agents-2.0.0.tar.gz --registry /shared/baton-registry
```

---

### `baton pull`

Install a package from a local registry directory.

```
baton pull [NAME] --registry PATH [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | No | Name of the package to install |
| `--registry PATH` | Yes | Path to the local registry directory |
| `--version VERSION` | No | Specific version to install (default: latest) |
| `--scope SCOPE` | No | Install scope: `project` or `user` (default: `project`) |
| `--force` | No | Overwrite existing files |
| `--list` | No | List all available packages in the registry |
| `--search QUERY` | No | Search packages by name substring |

**Examples:**

```bash
# List available packages
baton pull --list --registry /shared/baton-registry

# Search for packages
baton pull --search "auth" --registry /shared/baton-registry

# Install a specific version
baton pull my-agents --registry /shared/baton-registry --version 2.0.0 --scope user
```

---

### `baton verify-package` (deprecated)

> **Deprecated.** Use [`baton sync --verify ARCHIVE`](#baton-sync) instead. The shim still works and prints a `DEPRECATED:` warning to stderr.

---

### `baton install`

Install agents and references from the agent-baton repo to user or
project scope.

```
baton install --scope SCOPE [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--scope SCOPE` | Yes | -- | `user` (`~/.claude/`) or `project` (`.claude/`) |
| `--source PATH` | No | `.` | Path to the agent-baton repo root |
| `--force` | No | false | Overwrite ALL existing files |
| `--upgrade` | No | false | Overwrite agents + references but preserve settings, CLAUDE.md, knowledge, team-context |
| `--verify` | No | false | Run post-install verification checks |

**Upgrade mode** merges hooks into `settings.json` (preserving user keys)
while overwriting agents and references. CLAUDE.md and knowledge packs
are preserved.

**Examples:**

```bash
# Fresh install to user scope
baton install --scope user --source /path/to/agent-baton

# Upgrade agents + references, preserve settings
baton install --scope project --upgrade --verify

# Force overwrite everything
baton install --scope user --force --verify
```

---

### `baton transfer`

Transfer agents, knowledge, and references between projects.

```
baton transfer [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--discover` | No | Show what is available to transfer from this project |
| `--export TARGET` | No | Export items to a target project root |
| `--import SOURCE` | No | Import items from another project root |
| `--project ROOT` | No | Source project root (default: cwd) |
| `--agents NAMES` | No | Comma-separated agent names or filenames |
| `--knowledge PACKS` | No | Comma-separated knowledge pack directory names |
| `--references NAMES` | No | Comma-separated reference filenames |
| `--all` | No | Transfer all discoverable items |
| `--min-score RATE` | No | Minimum first-pass rate for `--discover` (0.0-1.0) |
| `--force` | No | Overwrite existing files at the destination |

**Examples:**

```bash
# Discover transferable items
baton transfer --discover

# Export specific agents to another project
baton transfer --export /path/to/other-project \
    --agents "backend-engineer,test-engineer" \
    --knowledge "api-patterns"

# Import everything from another project
baton transfer --import /path/to/source-project --all --force
```

---

## Agent Commands

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

---

### `baton route`

Route base agent role names to their stack-specific flavored variants.

```
baton route [ROLES...] [--path PATH]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `ROLES` | No | `backend-engineer frontend-engineer` | Base role names to route |
| `--path PATH` | No | cwd | Project root for stack detection |

**Output:**

```
Stack: python/fastapi

  backend-engineer               -> backend-engineer--python *
  frontend-engineer              -> frontend-engineer
```

Entries marked with `*` were remapped to a flavored variant.

---

### `baton events`

Query the event log for a task.

```
baton events [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--task TASK_ID` | No | Task ID to query events for |
| `--topic PATTERN` | No | Filter events by topic pattern (glob, e.g. `step.*`) |
| `--last N` | No | Show only the last N events |
| `--json` | No | Output events as JSON |
| `--summary` | No | Show a projected summary view instead of raw events |
| `--list-tasks` | No | List all task IDs that have event logs |

Without flags, lists all tasks with event logs.

**Examples:**

```bash
# List tasks with events
baton events --list-tasks

# View events for a task
baton events --task task-abc123

# Filter by topic
baton events --task task-abc123 --topic "step.*" --last 10

# Summary view
baton events --task task-abc123 --summary

# JSON output
baton events --task task-abc123 --json
```

---

### `baton incident`

Manage incident response workflows.

```
baton incident [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--templates` | No | Show all built-in incident templates |
| `--show ID` | No | Show a specific incident document |
| `--create ID` | No | Create an incident document with the given ID |
| `--severity LEVEL` | No | Severity level for `--create`: `P1`, `P2`, `P3`, `P4` |
| `--desc TEXT` | No | Description for `--create` |

Without flags, lists all incidents.

**Example:**

```bash
# Show available templates
baton incident --templates

# Create an incident
baton incident --create INC-2024-001 --severity P2 --desc "API latency spike"

# View an incident
baton incident --show INC-2024-001
```

---

## PMO Commands

### `baton pmo serve`

Start the PMO HTTP server (requires `pip install agent-baton[api]`).

```
baton pmo serve [--port PORT] [--host HOST]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--port PORT` | No | `8741` | Port to listen on |
| `--host HOST` | No | `127.0.0.1` | Host to bind to |

---

### `baton pmo status`

Print a terminal Kanban board summary of all registered projects.

```
baton pmo status
```

Shows per-project progress bars and a cards table with execution status.

---

### `baton pmo add`

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
| `--color COLOR` | No | Optional display color |

**Example:**

```bash
baton pmo add --id nds --name "NDS Platform" \
    --path /home/user/projects/nds --program NDS --color blue
```

---

### `baton pmo health`

Print program health bar summary showing completion percentage and
task status across all programs.

```
baton pmo health
```

**Output:**

```
Program Health

  NDS       ████████████████████    85%     (3 active, 12 complete)
  ATL       ██████████░░░░░░░░░░    50%     (2 active, 1 blocked, 5 complete)
```

---

## Sync Commands

### `baton sync`

Sync project data from project-local `baton.db` to `~/.baton/central.db`.
Also hosts two utilities folded in from removed top-level commands:
`--migrate-storage` (formerly `baton migrate-storage`) and `--verify`
(formerly `baton verify-package`).

```
baton sync [SUBCOMMAND] [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUBCOMMAND` | No | -- | `status` (or omit for default sync) |
| `--all` | No | false | Sync all registered projects |
| `--project ID` | No | -- | Sync a specific project by ID |
| `--rebuild` | No | false | Full rebuild (delete all central rows then re-sync) |
| `--migrate-storage` | No | false | Migrate JSON/JSONL flat files to SQLite (`baton.db`). Formerly `baton migrate-storage`. |
| `--dry-run` | No | false | (with `--migrate-storage`) Show what would be migrated without writing |
| `--keep-files` | No | true | (with `--migrate-storage`) Keep originals after migration |
| `--remove-files` | No | false | (with `--migrate-storage`) Archive originals to `pre-sqlite-backup/` |
| `--team-context PATH` | No | `.claude/team-context` | (with `--migrate-storage`) Path to team-context directory |
| `--migrate-verify` | No | false | (with `--migrate-storage`) Verify row counts after migration |
| `--verify [ARCHIVE]` | No | -- | Validate a `.tar.gz` agent-baton package. Formerly `baton verify-package`. |
| `--checksums` | No | false | (with `--verify`) Display per-file SHA-256 checksums |

**Default behavior** (no flags): syncs the current project by auto-detecting
from the working directory.

**Examples:**

```bash
# Sync current project
baton sync

# Sync all registered projects
baton sync --all

# Sync a specific project
baton sync --project nds

# Full rebuild
baton sync --rebuild

# Show sync watermarks
baton sync status

# Migrate JSON/JSONL flat files to SQLite (replaces 'baton migrate-storage')
baton sync --migrate-storage --dry-run
baton sync --migrate-storage --migrate-verify
baton sync --migrate-storage --remove-files --migrate-verify

# Validate a package archive (replaces 'baton verify-package')
baton sync --verify my-agents-2.0.0.tar.gz --checksums
```

**Exit code:** `1` if `--verify` validation fails or sync errors occur.

---

## Query Commands

### `baton query`

Query execution history, agent performance, and cross-project data from
the project-local `baton.db`.

```
baton query [SUBCOMMAND] [ARG] [options]
```

**Predefined queries (subcommands):**

| Subcommand | Description | Argument |
|------------|-------------|----------|
| `agent-reliability` | Agent success rates and token costs | -- |
| `agent-history NAME` | Recent step results for a specific agent | Agent name |
| `tasks` | Recent task list | -- |
| `task-detail TASK_ID` | Full breakdown for one task | Task ID |
| `knowledge-gaps` | Recurring knowledge gaps across tasks | -- |
| `roster-recommendations` | Consensus roster recommendations | -- |
| `gate-stats` | Gate pass rates by type | -- |
| `cost-by-type` | Token costs grouped by task type | -- |
| `cost-by-agent` | Token costs grouped by agent | -- |
| `current` | What is running right now | -- |
| `patterns` | Learned patterns with confidence scores | -- |

**Shared options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--format FORMAT` | `table` | Output format: `table`, `json`, `csv` |
| `--days N` | `30` | Days window for time-bounded queries |
| `--limit N` | `20` | Maximum rows for list queries |
| `--status STATUS` | -- | Filter tasks by status (for `tasks` subcommand) |
| `--min-frequency N` | `1` | Minimum occurrence for knowledge-gaps |
| `--db PATH` | -- | Explicit path to baton.db |
| `--central` | false | Query `~/.baton/central.db` instead |

**Ad-hoc SQL:**

```
baton query --sql "SELECT agent_name, COUNT(*) FROM step_results GROUP BY agent_name"
```

**Examples:**

```bash
# Agent reliability report
baton query agent-reliability --format json

# Task detail
baton query task-detail task-abc123

# Cost analysis
baton query cost-by-agent --days 90

# Cross-project query
baton query tasks --central --limit 50
```

---

### `baton cquery`

Cross-project SQL queries exclusively against `~/.baton/central.db`.

```
baton cquery [QUERY] [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `QUERY` | No | SQL statement or shortcut name |
| `--format FORMAT` | No | Output format: `table` (default), `json`, `csv` |
| `--tables` | No | List all tables and views in central.db |
| `--table TABLE` | No | Describe a specific table's columns |
| `--db PATH` | No | Override path to central.db |

**Shortcuts:**

| Name | Query |
|------|-------|
| `agents` | `SELECT * FROM v_agent_reliability` |
| `costs` | `SELECT * FROM v_cost_by_task_type` |
| `gaps` | `SELECT * FROM v_recurring_knowledge_gaps` |
| `failures` | `SELECT * FROM v_project_failure_rate` |
| `mapping` | `SELECT * FROM v_external_plan_mapping` |

**Examples:**

```bash
# Use a shortcut
baton cquery agents

# Custom SQL
baton cquery "SELECT * FROM executions LIMIT 10" --format json

# Schema introspection
baton cquery --tables
baton cquery --table executions
```

---

## Source Commands

### `baton source`

Manage external work-item source connections (ADO adapter implemented;
Jira, GitHub, Linear adapters not yet implemented).

#### `baton source add`

```
baton source add TYPE --name NAME [options]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `TYPE` | Yes | Source type: `ado` (others not yet implemented) |
| `--name NAME` | Yes | Display name for this source |
| `--org ORG` | No | Organization or account name |
| `--project PROJECT` | No | Project name within the source |
| `--pat-env ENV_VAR` | No | Environment variable name holding the PAT/token |
| `--url URL` | No | Base URL for self-hosted instances |

**Example:**

```bash
baton source add ado --name "Team Board" \
    --org contoso --project "Data Platform" --pat-env ADO_PAT
```

#### `baton source list`

```
baton source list
```

Lists all registered external sources with type, name, enabled status,
and last sync time.

#### `baton source sync`

```
baton source sync [SOURCE_ID] [--all]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `SOURCE_ID` | No | Source ID to sync (see `baton source list`) |
| `--all` | No | Sync all registered sources |

#### `baton source remove`

```
baton source remove SOURCE_ID
```

Removes a registered external source from central.db.

#### `baton source map`

```
baton source map SOURCE_ID EXTERNAL_ID PROJECT_ID TASK_ID [--type TYPE]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOURCE_ID` | Yes | -- | Source ID |
| `EXTERNAL_ID` | Yes | -- | External item ID (e.g. ADO work item number) |
| `PROJECT_ID` | Yes | -- | Baton project ID |
| `TASK_ID` | Yes | -- | Baton task/execution ID |
| `--type TYPE` | No | `implements` | Relationship type: `implements`, `blocks`, `related` |

**Example:**

```bash
baton source map ado-contoso-platform 12345 nds task-abc123 --type implements
```

---

## API Server

### `baton serve`

Start the HTTP API server (requires `pip install agent-baton[api]`).

```
baton serve [options]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--port PORT` | No | `8741` | Port to listen on |
| `--host HOST` | No | `127.0.0.1` | Host to bind to |
| `--token TOKEN` | No | -- | API token for authentication (also reads `BATON_API_TOKEN` env var) |
| `--team-context DIR` | No | `.claude/team-context` | Path to the team-context root directory |

**Example:**

```bash
baton serve --port 9000 --host 0.0.0.0 --token my-secret-token
```

---

## Swarm Commands

> **EXPERIMENTAL** — `baton swarm` requires `BATON_EXPERIMENTAL=swarm` to run.
> The Wave 6.2 Part A dispatcher is a v1 stub: partition plans are real but
> agent dispatch is **not yet wired** end-to-end. See bd-c925 / bd-2b9f for the
> integration roadmap.

### `baton swarm refactor DIRECTIVE_JSON`

Partition a codebase into AST-independent chunks and (eventually) dispatch one
Haiku agent per chunk to apply a refactor directive.

**Requires:**
- `BATON_EXPERIMENTAL=swarm` (stub gate, bd-18f6)
- `BATON_SWARM_ENABLED=1` (feature gate)

```bash
# Opt in to the experimental stub
export BATON_EXPERIMENTAL=swarm
export BATON_SWARM_ENABLED=1

# Preview without dispatching
baton swarm refactor --dry-run '{"kind":"replace-import","old":"requests","new":"httpx"}'

# Rename a symbol (interactive sign-off prompt)
baton swarm refactor '{"kind":"rename-symbol","old":"mymod.OldName","new":"mymod.NewName"}'

# CI mode (skip interactive prompt, preview still printed)
baton swarm refactor --yes '{"kind":"replace-import","old":"requests","new":"httpx"}'
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `DIRECTIVE_JSON` | (required) | JSON directive: `kind` + directive fields |
| `--max-agents N` | 100 | Max parallel chunk agents (cap: 100) |
| `--language` | `python` | AST language (v1: python only) |
| `--model` | `claude-haiku` | LLM tier for chunk agents |
| `--codebase-root PATH` | `cwd` | Root of the project to refactor |
| `--dry-run` | false | Print preview and exit, no dispatch |
| `-y / --yes` | false | Skip interactive confirmation prompt |
| `--require-approval-bead [BEAD_ID]` | — | Require a pre-filed approval bead |

**Exit codes:** `2` = experimental flag not set; `1` = swarm disabled or gate failure.

---

## Common Workflows

### Full Orchestrated Task (Sequential)

```bash
# 1. Create a plan
baton plan "Add JWT auth middleware and integration tests" --save --explain

# 2. Create a feature branch
git checkout -b feat/jwt-auth

# 3. Start execution
baton execute start
export BATON_TASK_ID=<task-id-from-output>

# 4. Loop through actions
baton execute next
# -> DISPATCH: spawn subagent, then record
baton execute dispatched --step-id 1.1 --agent backend-engineer--python
# (subagent does work)
baton execute record --step-id 1.1 --agent backend-engineer--python \
    --status complete --outcome "Added JWT middleware" --files "app/auth.py"

baton execute next
# -> GATE: run the gate command
baton execute gate --phase-id 1 --result pass --output "All tests passed"

baton execute next
# -> COMPLETE
baton execute complete
```

### Background Daemon with API

```bash
# 1. Create a plan
baton plan "Refactor data pipeline" --save

# 2. Start daemon with API server
baton daemon start --plan .claude/team-context/plan.json \
    --serve --port 8741 --max-parallel 3

# 3. Monitor progress
baton daemon status
baton execute list

# 4. Check for decisions that need human input
baton decide
baton decide --resolve req-123 --option approve
```

### Cross-Project Analysis

```bash
# 1. Register projects with PMO
baton pmo add --id nds --name "NDS Platform" --path /home/user/nds --program NDS
baton pmo add --id atl --name "ATL Service" --path /home/user/atl --program ATL

# 2. Sync all projects to central.db
baton sync --all

# 3. Query across projects
baton cquery agents
baton cquery "SELECT project_id, COUNT(*) as tasks FROM executions GROUP BY project_id"

# 4. View portfolio health
baton pmo health
```

### Improvement Cycle

```bash
# 1. Check for anomalies
baton anomalies

# 2. Run improvement cycle
baton learn improve --run

# 3. Review recommendations
baton budget --recommend
baton learn run-cycle

# 4. Refresh learned patterns
baton patterns --refresh

# 5. Check experiment status (folded into the learn loop)
baton learn improve --experiments
```

### Package Distribution

```bash
# 1. Create a package
baton package --name my-agents --version 1.0.0 \
    --description "Custom agent set" --include-knowledge

# 2. Verify the package
baton sync --verify my-agents-1.0.0.tar.gz --checksums

# 3. Initialize a registry and publish
baton publish --init /shared/registry
baton publish my-agents-1.0.0.tar.gz --registry /shared/registry

# 4. Pull from registry on another machine
baton pull my-agents --registry /shared/registry --scope user
```

---

## Environment Variables

The full list of Baton environment variables. The same table is mirrored
in [references/baton-engine.md](../references/baton-engine.md#environment-variables).

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
| `BATON_WORKTREE_STALE_HOURS` | Worktree GC stale threshold in hours; legacy alias `BATON_WORKTREE_GC_HOURS`. GC runs on every `baton execute complete`. | `4` |
| `BATON_API_TOKEN` | Bearer token for the FastAPI server (`baton serve`). CLI `--token` flag takes precedence. | unset |
| `ANTHROPIC_API_KEY` | Required for AI risk classification and the Haiku planner classifier. | unset |

### Task-ID Resolution Order

Every `baton execute` subcommand resolves the target execution through
this priority chain:

```
--task-id flag  ->  BATON_TASK_ID env var  ->  active-task-id.txt  ->  None
```

| Mechanism | Scope | Notes |
|-----------|-------|-------|
| `--task-id FLAG` | Single invocation | Highest priority |
| `BATON_TASK_ID` | Shell session | Set with `export`; persists for session lifetime |
| `active-task-id.txt` | Repository | Updated by `baton execute switch`; single-execution fallback |

**For agentic callers** (Claude Code's orchestrator): env vars do not
persist across independent `Bash` tool calls. Pass `--task-id` explicitly
on every CLI call when driving concurrent executions from an agent context.

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error: missing prerequisites, validation failure, failed gate, or invalid arguments |
| `2` | Experimental feature not opted in (e.g. `baton swarm` without `BATON_EXPERIMENTAL=swarm`) |

Commands that exit with code 1:
- `baton execute start` -- plan file not found
- `baton validate` -- errors found (or warnings in `--strict` mode)
- `baton spec-check` -- validation failed
- `baton sync --verify` -- package validation failed (formerly `baton verify-package`)
- `baton sync` -- sync failures
- `baton source` -- source not found or connection failed

---

## Troubleshooting

### "status must be one of: complete, failed, dispatched"

**Cause:** `baton execute record --status pass` (or `done`, `success`).

**Fix:** Use only `complete` or `failed`:

```bash
# Wrong
baton execute record --step-id 1.1 --agent foo --status pass

# Correct
baton execute record --step-id 1.1 --agent foo --status complete
```

### "No active execution state found"

**Cause:** `baton execute next` or `baton execute record` was called
before `baton execute start`, or `execution-state.json` was deleted.

**Fix:** Run `baton execute start` (or `baton execute resume` if state
exists from a previous session).

### Stack detection returns `unknown`

**Cause:** `baton plan` or `baton detect` cannot find config files in
the current directory.

**Fix:** Pass `--project PATH` pointing to the directory containing
`pyproject.toml`, `package.json`, `go.mod`, etc.

```bash
baton plan "..." --save --project /path/to/project/root
```

### Plan has generic descriptions

**Cause:** The planner received a vague task summary.

**Fix:** Pass a richer description:

```bash
# Too vague
baton plan "auth" --save

# Better
baton plan "Add JWT authentication middleware to the FastAPI app, including login/logout endpoints and test coverage" --save
```

### "API dependencies not installed"

**Cause:** `baton serve` or `baton pmo serve` requires FastAPI + uvicorn.

**Fix:**

```bash
pip install -e ".[api]"
```

### Concurrent executions interfere with each other

**Cause:** Multiple executions without proper session binding.

**Fix:** After each `baton execute start`, set the `BATON_TASK_ID`
environment variable from the printed session binding:

```bash
export BATON_TASK_ID=<task-id-from-output>
```

Or pass `--task-id` explicitly on every command.

### "No sync watermarks found"

**Cause:** No projects have been synced to central.db yet.

**Fix:** Register a project with `baton pmo add`, then run `baton sync`.

### "No adapter implemented for source type"

**Cause:** Only the ADO adapter is currently implemented.

**Fix:** For other sources, implement `ExternalSourceAdapter` in
`agent_baton/core/storage/adapters/<type>.py`.

---

## File Layout Reference

All engine files live under `.claude/team-context/` relative to the
project root:

```
.claude/team-context/
+-- plan.json                  Machine-readable execution plan
+-- plan.md                    Human-readable plan
+-- execution-state.json       Live engine state (crash recovery)
+-- context.md                 Shared project context
+-- mission-log.md             Structured log of agent completions
+-- usage-log.jsonl            Token and cost records per task
+-- baton.db                   SQLite database (project-local)
+-- active-task-id.txt         Active execution marker
+-- executions/
|   +-- <task-id>/
|       +-- execution-state.json
|       +-- plan.json
|       +-- plan.md
+-- traces/
|   +-- <task-id>.json         Full execution trace
+-- retrospectives/
|   +-- <task-id>.md           Post-execution analysis
+-- events/
|   +-- <task-id>.jsonl        Event log per task
+-- evolution-proposals/       Prompt improvement proposals
+-- context-profiles/          Agent context efficiency profiles
+-- learned-patterns.json      Learned orchestration patterns
+-- budget-recommendations.json
```

Central database: `~/.baton/central.db` (cross-project data, PMO state).
