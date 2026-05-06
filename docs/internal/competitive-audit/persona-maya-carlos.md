# Persona Journey Validation: Maya & Carlos

Audit of agent-baton codebase capabilities against two target user
personas.  Each step is rated WORKS / PARTIAL / BLOCKED / UNKNOWN with
file-level evidence.

---

## Maya — Solo Power User (Senior Full-Stack Dev)

### Day 0 — Install & First Run

#### 1. Can she `pip install agent-baton`?

**PARTIAL**

`pyproject.toml` is properly configured with `setuptools` build backend,
`[project.scripts]` entry point (`baton`), and minimal dependencies
(only `pyyaml`).  Local `pip install -e ".[dev]"` works.

However, the package is **not published to PyPI**.  `pip3 index versions
agent-baton` returns nothing.  Maya would need to clone the repo and
install locally, or use a direct git install:

```
pip install git+https://github.com/DaveGerson/agent-baton.git
```

The GitHub repo URL is declared in `pyproject.toml` but may or may not
be public.

**Evidence:**
- `/pyproject.toml` lines 5-7: name=agent-baton, version=0.1.0
- `/pyproject.toml` lines 51-52: `baton = "agent_baton.cli.main:main"`
- PyPI check: `pip3 index versions agent-baton` -> "Not found on PyPI"

**Impact:** First-touch friction.  Maya expects `pip install agent-baton`
to Just Work.  Having to clone a repo is a signup-level barrier.

---

#### 2. Does `baton detect` exist and auto-detect her Python+React stack?

**WORKS**

`baton detect` exists at `cli/commands/govern/detect.py`.  It delegates
to `AgentRouter.detect_stack()` which scans the project root for
language/framework indicator files (package.json, requirements.txt,
tsconfig.json, Cargo.toml, etc.) up to two directory levels deep.

Output is clean and concise:

```
Language:  python
Framework: react
Signals:   requirements.txt, package.json, tsconfig.json
```

**Evidence:**
- `/agent_baton/cli/commands/govern/detect.py` — full implementation
- `/agent_baton/core/orchestration/router.py` lines 75-80: `detect_stack()`

---

#### 3. Can she immediately plan and execute?

**WORKS**

The plan-to-execute loop is fully implemented:

1. `baton plan "task description" --save --explain` generates a
   `MachinePlan`, writes `plan.json` + `plan.md` to
   `.claude/team-context/`, and persists to SQLite.
2. `baton execute start` loads the plan and returns the first
   `DISPATCH` action.
3. `baton execute next` advances through `DISPATCH` / `GATE` /
   `APPROVAL` / `COMPLETE` actions.
4. `baton execute record` records step completions.
5. `baton execute complete` finalizes with usage record, trace, and
   retrospective.

No configuration files or YAML setup required.  The CLI auto-discovers
commands via `pkgutil.iter_modules`.

**Evidence:**
- `/agent_baton/cli/commands/execution/plan_cmd.py` — full planner CLI
- `/agent_baton/cli/commands/execution/execute.py` — 14 subcommands
- `/agent_baton/cli/main.py` lines 146-156: quick-start guide in help

---

#### 4. Separate commits per agent phase?

**WORKS**

The planner auto-selects a git strategy based on risk level:

- LOW/MEDIUM risk: `commit-per-agent` (one commit per agent dispatch
  on a single feature branch)
- HIGH/CRITICAL risk: `branch-per-agent` (separate branch per agent
  for independent revert)

The `git_strategy` field is stored on `MachinePlan` and conveyed in
plan output.  The actual commit creation happens at the orchestrator
layer (Claude Code reads the strategy from `_print_action` output and
follows it).

**Evidence:**
- `/agent_baton/core/engine/planner.py` lines 75-84: `_select_git_strategy()`
- `/agent_baton/models/enums.py` lines 83-90: `GitStrategy` enum
- `/agent_baton/models/execution.py` line 475: `git_strategy` field

**Caveat:** The engine emits the strategy but does not enforce git
operations itself.  Enforcement depends on the orchestrator agent (Claude
Code) interpreting the strategy correctly.  In headless mode (`baton
execute run`) there is no git commit automation -- the Claude subprocess
handles commits internally.

---

### Week 1 — Active Adoption

#### 5. Does `--complexity light` work to skip ceremony?

**WORKS**

`baton plan` accepts `--complexity light|medium|heavy`.  When provided,
it bypasses automatic classification and directly controls plan sizing.
Light plans get fewer phases and simpler gate configurations.

**Evidence:**
- `/agent_baton/cli/commands/execution/plan_cmd.py` lines 104-110:
  `--complexity` flag with choices
- `/agent_baton/core/engine/planner.py` line 299:
  `complexity=args.complexity` passed to `create_plan()`

---

#### 6. Can she use talent-builder to create custom agent variants?

**WORKS**

The `talent-builder` agent at `agents/talent-builder.md` is a
comprehensive 385-line agent definition that creates:

- Agent files (`.md` agent definitions)
- Knowledge packs (structured domain reference files)
- Skills (repeatable workflow procedures)
- Reference docs (shared knowledge)

It includes a decision framework (five tests), knowledge architecture
patterns, enterprise patterns (domain onboarding, system integration,
regulatory domain, documentation ingestion), and quality checklists.

The agent is invoked via the orchestrator's DISPATCH mechanism or
directly as a Claude Code agent.

**Evidence:**
- `/agents/talent-builder.md` — full 385-line agent definition with
  workflow, templates, quality checklists, and enterprise patterns

---

#### 7. Does `baton query agent-reliability` work?

**WORKS**

`baton query` is a comprehensive query interface with 16 predefined
queries plus ad-hoc SQL support.  `agent-reliability` returns:

- agent name, total steps, success rate, successes, failures, retries,
  total tokens, average duration

Additional queries relevant to Maya:
- `agent-history <name>` -- recent results for a specific agent
- `cost-by-agent` -- token costs by agent
- `patterns` -- learned patterns with confidence scores
- `--sql "SELECT ..."` -- arbitrary read-only SQL

Output formats: table (default), json, csv.

**Evidence:**
- `/agent_baton/cli/commands/observe/query.py` — 664-line module with
  16 subcommands, ad-hoc SQL, and 3 output formats

---

### Month 2 — Power Use

#### 8. Does crash recovery work when laptop sleeps?

**WORKS**

`baton execute resume` is implemented.  The `ExecutionEngine.resume()`
method reloads persisted state and returns the next action.  State is
persisted after every mutation (step result, gate result, approval) to
both file and SQLite backends.

Additional recovery features:
- `baton execute list` shows all executions with status
- `baton execute switch <task-id>` changes active execution
- `BATON_TASK_ID` environment variable binds a session to a task
- SQLite-first active-task resolution with file fallback

**Evidence:**
- `/agent_baton/cli/commands/execution/execute.py` lines 195-196:
  `resume` subcommand registered
- `/agent_baton/core/engine/executor.py` line 1675: `def resume()`
- `/agent_baton/cli/commands/execution/execute.py` lines 786-793:
  resume handler

---

#### 9. Does `baton trace` exist for debugging agent decisions?

**WORKS**

`baton trace` provides:

- `baton trace` -- list recent traces (with count, outcome, timestamps)
- `baton trace <task-id>` -- full timeline for a specific task
- `baton trace --last` -- timeline for most recent task
- `baton trace --summary <task-id>` -- compact summary view

Traces are auto-generated during execution and record every step
dispatch, gate result, and completion event with timestamps.

**Evidence:**
- `/agent_baton/cli/commands/observe/trace.py` — complete 106-line
  implementation with 4 display modes

---

### Maya's Dealbreakers

#### CLI Startup Time

**PASS** -- 190ms for `baton --help` (measured).  Module import is 118ms.
All command modules are discovered via `pkgutil.iter_modules` at startup
(eager discovery), but each module is lightweight.  No heavy dependencies
are imported at the top level -- `anthropic`, `fastapi`, `uvicorn` are
optional extras that import lazily.

**Evidence:** `time baton --help` -> 0.190s real

---

#### Verbose Output

**PARTIAL** -- There is no `--quiet` or `--verbose` flag.  The plan
command prints progress to stderr ("Planning...", "Analyzing patterns...",
"Creating execution plan...", "Done.").  Execute commands print structured
output to stdout.  The `--output json` flag on execute subcommands gives
machine-readable output.  `--no-color` disables ANSI colors.

Missing: a global `--quiet` flag to suppress informational stderr output.

**Evidence:**
- `/agent_baton/cli/main.py` line 115: `--no-color` flag
- `/agent_baton/cli/commands/execution/execute.py` line 78: `--output`
  flag (text or json)
- No `--quiet` or `--verbose` in `main.py`

---

#### Required Server Processes

**PASS** -- Baton works fully without any server process.  The daemon
(`baton daemon`) and API server (`baton serve` / `--serve`) are optional.
The core workflow is pure CLI: `plan` -> `execute start` -> `execute
next` -> `execute record` -> `execute complete`.

**Evidence:** No daemon or server import at the top level of `execute.py`
or `plan_cmd.py`.  Daemon and API are separate optional modules.

---

#### Required Config Files

**PASS** -- No YAML or config files are required.  The CLI auto-discovers
agents from `agents/` directories (installed by `scripts/install.sh`).
Settings are optional (`settings.json` in `.claude/`).  The planner
auto-detects stack, risk, budget, and agent routing without any
configuration.

The only prerequisite is that `.claude/agents/` exists with agent
definitions (created by `scripts/install.sh` or `baton install`).

**Evidence:**
- `/agent_baton/cli/main.py` lines 170-176: missing agents directory
  detection with install instructions
- No config file loading in the critical path of `plan_cmd.py` or
  `execute.py`

---

## Maya Summary

| Step | Rating | Notes |
|------|--------|-------|
| pip install | PARTIAL | Not on PyPI; requires git clone |
| baton detect | WORKS | Auto-detects Python+React |
| plan/execute loop | WORKS | Full 14-subcommand implementation |
| Commits per phase | WORKS | Auto-selected by risk level |
| --complexity light | WORKS | Bypasses classifier |
| talent-builder | WORKS | Comprehensive 385-line agent factory |
| baton query | WORKS | 16 predefined queries + SQL |
| Crash recovery | WORKS | Resume from persisted state |
| baton trace | WORKS | 4 display modes |
| CLI startup | PASS | 190ms |
| Verbose control | PARTIAL | No --quiet flag |
| No server required | PASS | Pure CLI workflow |
| No config required | PASS | Auto-detection + agent discovery |

**Verdict:** Maya would adopt this tool.  The PyPI gap is the biggest
first-touch risk -- she'll lose patience if install takes more than 60
seconds.  Once past that, the CLI experience is clean and the feature set
is deep.  Missing `--quiet` flag is a minor annoyance.

---

## Carlos — Overnight Backlog Drainer (Startup CTO)

### Week 1 — Initial Trial

#### 1. Can he create plans for multiple backlog items?

**PARTIAL**

Plans are created one at a time via `baton plan "description" --save`.
There is no `baton plan --batch` or multi-plan creation command.

Carlos can script it:
```bash
for task in "task 1" "task 2" "task 3"; do
  baton plan "$task" --save
done
```

But plans overwrite the same `plan.json` at
`.claude/team-context/plan.json`.  Each plan also gets a task-scoped copy
at `.claude/team-context/executions/<task-id>/plan.json`, so the data is
preserved -- but the most-recent shortcut file gets overwritten each
time.

The `baton execute list` command shows all executions, so Carlos can
track multiple plans.

**Evidence:**
- No batch/queue subcommand in `cli/commands/`
- Plan task-scoped storage: `plan_cmd.py` line 317:
  `ctx = ContextManager(team_context_dir=ctx_dir, task_id=plan.task_id)`
- Root-level overwrite: `plan_cmd.py` lines 322-326

**Impact:** Scriptable but not first-class.  Carlos needs to write a
wrapper script.

---

#### 2. Can plans run sequentially in foreground?

**WORKS**

`baton execute run` is a fully autonomous execution loop that requires
no active Claude Code session.  It:

- Starts (or resumes) an execution
- Loops through DISPATCH -> agent launch -> record until COMPLETE or
  FAILED
- Runs gates as shell subprocesses
- Handles approvals interactively (or auto-approves in dry-run mode)
- Has a `--max-steps` safety limit (default: 50)
- Has `--dry-run` mode for testing

This is the Carlos-compatible path: `baton plan --save` then
`baton execute run` and walk away.

**Evidence:**
- `/agent_baton/cli/commands/execution/execute.py` lines 174-183:
  `run` subcommand with `--max-steps`, `--dry-run`, `--model`
- Lines 883-1134: `_handle_run()` -- full autonomous loop implementation

---

#### 3. Are results reviewable per-task?

**WORKS**

Multiple review tools per task:

- `baton execute status` -- current state, step-by-step progress
- `baton query task-detail <task-id>` -- full breakdown (plan, steps,
  results, gates)
- `baton trace <task-id>` -- timeline with timestamps
- `baton retro --task-id <id>` -- retrospective with lessons learned
- `baton query agent-history <name>` -- per-agent history
- `baton execute list` -- all executions with status, steps, PIDs

**Evidence:** All commands above are fully implemented with multiple
output formats.

---

### Week 2 — Batch Mode

#### 4. Does daemon mode with `--max-parallel` work?

**WORKS**

`baton daemon start --plan plan.json --max-parallel N` is fully
implemented:

- Daemonizes the process (PID file, log redirect to `/dev/null`)
- `--foreground` flag for terminal-attached mode
- `--max-parallel N` controls concurrent agent dispatch (default: 3)
- `--serve` flag co-locates a FastAPI API server for monitoring
- `--resume` flag for resuming interrupted execution
- `baton daemon status` / `stop` / `list` for management

The worker uses `StepScheduler` with bounded concurrency.

**Evidence:**
- `/agent_baton/cli/commands/execution/daemon.py` — full 497-line
  implementation with start/status/stop/list
- Line 38: `--max-parallel` flag with default 3
- `/agent_baton/core/runtime/worker.py` — async TaskWorker with parallel
  dispatch via StepScheduler

**Caveat:** Each daemon runs one plan.  To drain a backlog of N tasks,
Carlos would need to script sequential daemon invocations or use the
`baton execute run` loop for each task.

---

#### 5. Can he set per-task cost caps?

**PARTIAL**

Budget tiers exist with token thresholds:
- `lean`: 50,000 tokens
- `standard`: 500,000 tokens
- `full`: 2,000,000 tokens

The `_check_token_budget()` method in `ExecutionEngine` compares
cumulative tokens against the tier threshold and returns a warning.

However:
- The budget check is **advisory** (returns a warning string), not
  **enforcing** (does not halt execution).
- There is no CLI flag to set a hard cost cap in tokens or dollars.
- Budget tier is auto-selected by the planner based on task type and
  agent count; there is no `--budget` CLI flag on `baton plan`.

Carlos would need to edit `plan.json` manually to set `budget_tier` to
`lean` for cost-sensitive tasks.

**Evidence:**
- `/agent_baton/core/engine/executor.py` lines 2174-2198:
  `_check_token_budget()` — warning only, no enforcement
- `/agent_baton/core/engine/planner.py` line 2294:
  `_select_budget_tier()` — auto-selection
- No `--budget` flag in `plan_cmd.py`

**Impact:** Significant gap for Carlos.  Without hard cost caps, an
overnight batch run could burn through API budget with no safety net.

---

#### 6. Does Slack webhook work for completion notifications?

**WORKS**

Full webhook infrastructure is implemented:

- `WebhookRegistry` — CRUD for webhook subscriptions (JSON file storage)
- `WebhookDispatcher` — EventBus subscriber with retry + HMAC signing
- Slack Block Kit formatter (`format_slack`) with rich interactive
  notifications including action buttons
- API routes: `POST /webhooks`, `GET /webhooks`,
  `DELETE /webhooks/{id}`
- Auto-retry with exponential backoff (5s, 30s, 300s)
- Auto-disable after 10 consecutive failures
- HMAC-SHA256 payload signing

Slack-specific features:
- Auto-detects Slack URLs (`hooks.slack.com`) for formatter selection
- Block Kit layout with header, summary, options, context, and action
  buttons for `human.decision_needed` events
- Glob-style event pattern matching (`step.*`, `gate.*`, `*`)

**Evidence:**
- `/agent_baton/api/webhooks/dispatcher.py` — full delivery engine
- `/agent_baton/api/webhooks/payloads.py` — Slack Block Kit formatter
- `/agent_baton/api/webhooks/registry.py` — CRUD + persistence
- `/agent_baton/api/routes/webhooks.py` — REST API endpoints

**Caveat:** Webhooks require the API server (`--serve` flag on daemon or
`baton serve`).  Without the server, the EventBus has no async loop to
dispatch webhooks.

---

#### 7. Can he set tasks to LOW risk with auto-approve on gate pass?

**WORKS**

The system handles this at multiple levels:

1. **Planner level:** LOW risk tasks do NOT get `approval_required`
   flags.  Approval gates are only added for HIGH+ risk on design/
   research phases (`planner.py` lines 1109-1114).

2. **Worker level (daemon/headless):** When no `DecisionManager` is
   configured (the default for daemon mode), both gates and approvals
   are auto-approved:
   - Programmatic gates (`test`, `build`, `lint`, `spec`) are always
     auto-approved (`worker.py` lines 341-347).
   - All other gates are auto-approved when no DecisionManager exists
     (`worker.py` lines 351-357).
   - Approvals are auto-approved when no DecisionManager exists
     (`worker.py` lines 404-409).

3. **`baton execute run` level:** Gate checks run as shell subprocesses
   (`execute.py` lines 1061-1091).  Pass/fail is determined by exit
   code.  Approvals prompt on stdin, but in a scripted/piped context
   EOFError triggers "reject" (line 1113).

So for Carlos's LOW-risk overnight workflow: tasks won't have approval
gates, and programmatic gates auto-execute.  This works as expected.

**Evidence:**
- `/agent_baton/core/engine/planner.py` lines 1109-1114: approval gates
  only for HIGH+ risk
- `/agent_baton/core/runtime/worker.py` lines 341-409: auto-approve
  logic

---

### Morning Review

#### 8. Can he review via `baton trace` for failures?

**WORKS**

See Maya step 9.  Additionally relevant for Carlos:

- `baton trace --last` -- quick check of the most recent task
- `baton query stalled --hours 8` -- find executions stuck overnight
- `baton execute status` -- current execution state
- `baton query tasks --status failed` -- find all failed tasks

**Evidence:** All implemented as described above.

---

#### 9. Are retrospective summaries generated per task?

**WORKS**

Retrospectives are auto-generated during `engine.complete()`:

- `generate_from_usage()` produces a structured retrospective from the
  usage record
- Output includes: agent outcomes, knowledge gaps (explicit signals +
  implicit detection via regex scanning), roster recommendations,
  sequencing notes, team composition records
- Persisted as paired `.md` (narrative) + `.json` (structured) files
- Accessible via `baton retro --task-id <id>` or
  `baton retro --search <keyword>`
- Cross-retrospective recommendations via `baton retro --recommendations`

**Evidence:**
- `/agent_baton/core/observe/retrospective.py` — full engine with
  generation, persistence, and search
- `/agent_baton/core/engine/executor.py` lines 1506-1512: auto-generate
  retro on complete
- `/agent_baton/cli/commands/observe/retro.py` — CLI with 4 display
  modes

---

#### 10. Do PRs have separate commits per phase?

**WORKS** (same as Maya step 4)

The git strategy is encoded in the plan and followed by the orchestrator.
LOW/MEDIUM risk uses `commit-per-agent`, HIGH/CRITICAL uses
`branch-per-agent`.  The delegation prompt includes the strategy, so each
dispatched agent commits its work separately.

---

### Carlos's Dealbreakers

#### No Cost Ceiling

**BLOCKED**

Budget tiers exist but enforcement is **advisory only**.  The
`_check_token_budget()` method returns a warning string but does not halt
execution.  There is no mechanism to:

- Set a hard dollar/token cap that aborts execution
- Set per-task cost limits
- Set a global overnight spending ceiling

For an unattended overnight run, this is a critical gap.  A runaway
agent could consume the entire API budget.

**Evidence:**
- `/agent_baton/core/engine/executor.py` lines 2174-2198: advisory check
  only
- `--max-steps 50` on `baton execute run` is the only hard safety limit,
  but it's step-count based, not token/cost based

**Mitigation path:** Add a `--token-limit N` flag to `baton execute run`
and `baton daemon start` that calls `_check_token_budget()` and aborts
when exceeded.

---

#### No Auto-Approve for LOW Risk

**PASS** — This is actually handled well.  LOW-risk tasks don't get
approval gates (planner only adds them for HIGH+), and daemon mode
auto-approves when no DecisionManager is configured.  See step 7 above.

---

#### Complex Setup

**PARTIAL**

Installation requires:
1. `pip install -e ".[dev]"` (or equivalent, once on PyPI)
2. `scripts/install.sh` to install agents/references into the target
   project's `.claude/` directory

The install script is the friction point -- Carlos needs to run it in
each project.  No auto-setup on first `baton plan` invocation.

Once installed, no further configuration is needed.  Auto-detection
handles stack, risk, budget, and agent routing.

**Evidence:**
- `/agent_baton/cli/main.py` lines 170-176: install instructions shown
  when `.claude/agents` missing
- `scripts/install.sh` — install script

---

#### Aggressive Unlimited Retries

**PASS** — The system does NOT have aggressive retries.

- `baton execute run` has `--max-steps 50` (configurable) as a hard
  limit on total steps before aborting.
- The TaskWorker has no retry logic for failed steps -- a failed step
  is recorded as failed and execution continues to the next step or
  fails the phase.
- Webhook delivery has capped retries: 3 attempts with backoff (5s, 30s,
  300s), auto-disable after 10 consecutive failures.
- No `grep -r retry_limit|max_retries` hits in the worker code.

**Evidence:**
- `/agent_baton/core/runtime/worker.py` — no retry logic for failed
  steps
- `/agent_baton/cli/commands/execution/execute.py` lines 180-181:
  `--max-steps` default 50
- `/agent_baton/api/webhooks/dispatcher.py` lines 56-57: capped retry
  with auto-disable

---

## Carlos Summary

| Step | Rating | Notes |
|------|--------|-------|
| Batch plan creation | PARTIAL | One at a time; scriptable but no batch command |
| Sequential foreground | WORKS | `baton execute run` — full autonomous loop |
| Per-task review | WORKS | 6+ review tools per task |
| Daemon --max-parallel | WORKS | Full daemon with parallel dispatch |
| Per-task cost caps | PARTIAL | Budget tiers exist but advisory only |
| Slack webhooks | WORKS | Full infrastructure with Block Kit |
| LOW risk auto-approve | WORKS | No approval gates for LOW risk; daemon auto-approves |
| Trace for failures | WORKS | Traces + stalled query + status |
| Retrospectives | WORKS | Auto-generated on complete with search |
| Commits per phase | WORKS | Auto git strategy by risk level |
| Hard cost ceiling | BLOCKED | Advisory only; no abort on overspend |
| Setup complexity | PARTIAL | Requires install script per project |
| Retry limits | PASS | No aggressive retries; --max-steps cap |

**Verdict:** Carlos would trial this tool but would hesitate to leave it
running overnight without hard cost ceilings.  The daemon mode, parallel
dispatch, Slack webhooks, and auto-approve behavior are exactly what he
needs.  The hard cost ceiling is the one blocker that could prevent
overnight autonomous use.

---

## Cross-Persona Gap Analysis

### Gaps That Affect Both Personas

| Gap | Severity | Persona Impact |
|-----|----------|----------------|
| Not on PyPI | HIGH | Both: first-touch friction |
| No `--quiet` flag | LOW | Maya: CLI ergonomics |
| No batch plan creation | MEDIUM | Carlos: overnight workflow |
| Advisory-only budget caps | HIGH | Carlos: cost safety |
| No `--budget` CLI flag on plan | MEDIUM | Both: per-task cost control |

### Recommended Priority Fixes

1. **Publish to PyPI** — removes the biggest adoption barrier for both
   personas.
2. **Hard cost ceiling** — add `--token-limit N` to `baton execute run`
   and `baton daemon start` that aborts execution when cumulative tokens
   exceed the limit.  Change `_check_token_budget()` from advisory to
   enforcing when a limit is set.
3. **Batch plan creation** — `baton plan --batch tasks.txt` that reads
   one task per line and creates plans for each.
4. **`--quiet` flag** — suppress stderr progress messages.
5. **`--budget lean|standard|full` on `baton plan`** — allow direct
   budget tier override without editing plan.json.

### Features That Exceed Expectations

Both personas would be pleasantly surprised by:

- **16 predefined analytics queries** with SQL escape hatch
- **Ad-hoc SQL** against the execution database
- **Knowledge gap detection** (both explicit signals and implicit
  narrative scanning)
- **Retrospective auto-generation** with cross-task recommendations
- **Webhook infrastructure** with Slack Block Kit and HMAC signing
- **Talent-builder** agent for creating custom specialist agents
- **Event bus architecture** enabling real-time monitoring
- **Cross-project analytics** via `baton cquery` against central.db
