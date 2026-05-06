# Agent Baton

📖 **Docs:** <https://davegerson.github.io/agent-baton/>

**Turn one prompt into a coordinated team of AI specialists.**

Agent Baton is a multi-agent orchestration system for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Describe a complex task in plain language -- Baton plans it, routes it to the right specialist agents, enforces QA gates between phases, and delivers tested, reviewed code. No external services. No API keys beyond Claude. Everything runs locally.

```
You:  "Use the orchestrator to add input validation to the API
       with tests and security review"

Baton: Plans 3 phases (implement, test, review)
       Dispatches backend-engineer, test-engineer, security-reviewer
       Runs pytest gate between phases
       Commits each agent's work separately
       Writes trace, usage log, and retrospective
```

---

## Why Agent Baton?

**The problem:** Claude Code is powerful, but complex tasks -- the ones that
touch multiple files, need testing, and require different expertise -- benefit
from structure. Without it, you get context bloat, missed test coverage, and
no audit trail.

**The solution:** Agent Baton gives Claude Code a project management layer.
It breaks work into phases, assigns each phase to a specialist agent, runs
automated QA gates between them, and tracks everything. You stay in control
while the agents do the heavy lifting.

| Without Baton | With Baton |
|---------------|------------|
| One long conversation doing everything | Phases with specialist agents |
| Manual "did you run the tests?" | Automated pytest/lint gates between phases |
| No record of what happened | Full traces, usage logs, retrospectives |
| Hope the AI remembers context | Scoped delegation prompts per agent |
| Single point of failure | Crash recovery via `baton execute resume` |

---

## Get Started in 5 Minutes

### 1. Install agent definitions

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
scripts/install.sh          # Linux/macOS
# or: scripts/install.ps1   # Windows (no admin required)
```

The installer prompts for scope (user-level `~/.claude/` for all projects,
or project-level `.claude/` for the current project only) and copies agent
definitions, reference procedures, a template `CLAUDE.md`, and
`settings.json` hooks.

### 2. Install the Python engine

**From PyPI (recommended):**

```bash
pip install agent-baton          # Core engine + CLI
pip install agent-baton[pmo]     # + REST API and PMO server
pip install agent-baton[classify] # + AI risk classification
```

**From source (for development):**

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
pip install -e ".[dev]"          # Core engine + CLI + test deps
```

### 3. Verify

```bash
# In Claude Code
/agents                     # Should list ~22 agents

# In terminal
baton agents                # List agents from Python registry
baton detect                # Detect your project's stack
```

### 4. Optional: install cymbal (recommended)

[cymbal](https://github.com/1broseidon/cymbal) is a tree-sitter
code indexer that agents use for symbol lookup and impact analysis.
Without it, agents fall back to LSP or grep.

```bash
# Check if cymbal is installed
cymbal --help

# If not, install the binary to ~/.local/bin/
# (see cymbal docs for platform-specific instructions)
```

### 5. Run your first task

In a Claude Code session:

```
Use the orchestrator to add a health check endpoint with tests
```

That's it. The orchestrator plans, dispatches agents, runs gates, and
delivers tested code.

See [QUICKSTART.md](QUICKSTART.md) for a detailed walkthrough, or
[docs/examples/first-run.md](docs/examples/first-run.md) for a complete
end-to-end example with command output.

---

## Features

### 19 Specialist Agents

Stack-aware agents that the orchestrator selects and routes automatically.
First time on a Go project? The `talent-builder` creates
`backend-engineer--go`. Next time, the routing table finds it.

| Category | Agents |
|----------|--------|
| Orchestration | `orchestrator` |
| Backend | `backend-engineer`, `--python`, `--node` |
| Frontend | `frontend-engineer`, `--react`, `--dotnet` |
| Architecture | `architect` |
| Quality | `test-engineer`, `code-reviewer`, `security-reviewer` |
| Governance | `auditor` (independent veto power) |
| Data | `data-engineer`, `data-analyst`, `data-scientist` |
| Visualization | `visualization-expert` |
| Operations | `devops-engineer` |
| Domain | `subject-matter-expert` |
| Meta | `talent-builder` (creates new agent definitions) |

### Orchestration Engine

A deterministic state machine that plans, sequences, and tracks multi-agent
tasks:

- **Intelligent planning** -- auto-detects your stack, classifies risk,
  assigns budget tiers, sequences phases with dependency awareness. Supports
  complexity override (`--complexity light|medium|heavy`) and AI-driven
  classification.
- **Execution loop** -- DISPATCH / GATE / APPROVAL / COMPLETE with full
  state persistence and crash recovery
- **Concurrent execution** -- run multiple plans in parallel, each bound
  by `BATON_TASK_ID`
- **Plan amendments** -- add phases or steps mid-execution
- **Team steps** -- dispatch multiple agents to a single step with
  configurable synthesis strategies and conflict detection/escalation
- **Selective MCP pass-through** -- per-step MCP server declarations
  prevent input token bloat from unused tool schemas
- **Resource governance** -- concurrent agent caps and token budget
  warnings across budget tiers
- **Team intelligence** -- composition tracking, cost prediction per
  team, and `baton scores --teams` effectiveness reporting
- **Async sessions** -- multi-day workflow support with checkpoints,
  participant tracking, and multi-party contribution protocol

### Risk-Tiered Safety

Every task is classified by risk level:

- **LOW** -- guardrail presets applied inline, no subagent overhead
- **MEDIUM** -- auditor reviews the plan before execution
- **HIGH** -- auditor runs as independent subagent with veto authority;
  regulated domains require subject-matter-expert involvement

### 15 Reference Procedures

Shared knowledge documents encoding planning strategy, guardrail rules,
communication protocols, failure handling, cost models, design patterns,
and more. Agents get the knowledge they need without duplicating it across
context windows.

| Reference | Topic |
|-----------|-------|
| `baton-engine.md` | Full CLI reference and engine protocol |
| `task-sequencing.md` | Phase ordering and dependency logic |
| `agent-routing.md` | How the router selects agent flavors |
| `guardrail-presets.md` | Safety policies by risk tier |
| `cost-budget.md` | Token budget tiers and cost models |
| `comms-protocols.md` | Inter-agent communication contracts |
| `failure-handling.md` | Retry, escalation, and recovery |
| `git-strategy.md` | Branch and commit conventions |
| `hooks-enforcement.md` | Pre-commit and CI hook rules |
| `decision-framework.md` | Human-in-the-loop decision protocol |
| `adaptive-execution.md` | Runtime plan adaptation |
| `research-procedures.md` | Investigation and discovery protocols |
| `doc-generation.md` | Documentation generation standards |
| `baton-patterns.md` | Reusable orchestration patterns |
| `knowledge-architecture.md` | Knowledge pack design and delivery |

### Knowledge Delivery

Curated knowledge packs resolved at plan time and injected into each
agent's delegation prompt. A feedback loop learns which agents need what
knowledge for which task types.

Three knowledge packs ship with the project:

| Pack | Contents |
|------|----------|
| `agent-baton` | Architecture, conventions, and development workflow (3 docs) |
| `ai-orchestration` | Multi-agent patterns, evaluation, prompt engineering, context economics (4 docs) |
| `case-studies` | Real-world failure modes, framework comparisons, scaling patterns (3 docs) |

Attach knowledge at plan time with `--knowledge` (individual files) or
`--knowledge-pack` (named packs).

### Structured Agent Memory (Beads)

Inspired by Steve Yegge's [Beads](https://github.com/beads-ai/beads-cli)
agent memory system. Agents emit `BEAD_DISCOVERY`, `BEAD_DECISION`,
`BEAD_WARNING`, `BEAD_OUTCOME`, and `BEAD_PLANNING` signals during
execution that are automatically parsed and persisted to SQLite. Beads form
a typed dependency graph with status tracking (`open` -> `closed` ->
`archived`), tag-based retrieval, time-based decay, and promotion to
persistent knowledge documents.

```bash
baton beads list --type decision --status open
baton beads ready                               # Unblocked open beads
baton beads graph TASK_ID                       # Dependency graph
baton beads promote bd-a1b2 --pack my-pack      # Promote to knowledge doc
baton beads cleanup --ttl 168 --dry-run         # Memory decay preview
```

### Smart Forge

AI-driven task planning via headless Claude Code subprocess. Generates
real LLM-quality plans (not rule-based templates). Includes interactive
interview-based refinement, SSE progress streaming through 5 stages
(Analyzing, Routing, Sizing, Generating, Validating), and integrates
with the PMO UI.

### Headless Execution

Run the full loop without a Claude Code session:

```bash
baton execute run --plan .claude/team-context/plan.json
```

Dispatches agents via `claude --print`, runs gates as subprocesses, loops
until complete. Supports `--model`, `--max-steps`, and `--dry-run` flags.
The PMO UI can also launch executions from its Kanban board.

### Daemon Mode

Background execution with parallel agent dispatch:

```bash
baton daemon start --plan plan.json --max-parallel 3
baton daemon status
baton daemon stop
```

Supports `--foreground`, `--resume`, `--dry-run`, and `--serve` (co-hosts
the HTTP API in the same process). Human decision requests raised during
daemon execution are managed via `baton decide`.

### Worktree Isolation (Wave 1.3)

When the planner classifies a task as parallelizable, the engine
provisions a linked git worktree per agent so concurrent edits never
clobber each other. Worktrees live under `.claude/worktrees/<short-id>/`
and are reclaimed automatically:

- **Aggressive GC.** `baton execute complete` runs `WorktreeManager.gc_stale()`
  on a daemon thread (errors emit `BEAD_WARNING` and never block completion).
- **Stale threshold.** Default is 4 hours (was 72h pre-2026-04-28). Override
  with `BATON_WORKTREE_STALE_HOURS=<hours>`. The legacy
  `BATON_WORKTREE_GC_HOURS` env var is still honoured.
- **In-flight guard.** The GC skips worktrees referenced by any
  `status='running'` execution in `baton.db` and logs `skipping in-flight
  worktree X (active execution Y)`.
- **Parent-repo detection.** When `baton` is invoked from inside a linked
  worktree, `WorktreeManager` resolves the canonical repo root via
  `git worktree list --porcelain` so all repo-level operations
  (worktree add/remove/prune, branch ops, rebase, merge) target the main
  repository — fixing the dogfood failure where running baton inside an
  agent worktree silently disabled isolation. (PR #69, bd-c071.)

### Run-Level Spend Ceiling (PR #67)

Set `BATON_RUN_TOKEN_CEILING=<USD>` to cap total cumulative spend per
execution. `BudgetEnforcer` reads the env var fresh on every check
(never cached), tracks a run-level counter that survives crash recovery,
and raises `RunTokenCeilingExceeded` before any record_* call would push
the total over the limit. The selfheal, speculator, and immune-system
daemons all check the ceiling before kicking off an escalation cycle and
abort with `'ceiling-abort'` rather than overspending.

> **Known gap (bd-3f80).** Selfheal/speculator/immune respect the ceiling,
> but the main `Executor.dispatch()` path does not yet block individual
> agent dispatches when the projected spend would breach the cap; it only
> emits a warning at HIGH/CRITICAL run start. Full executor wiring is
> tracked in bd-3f80.

### Persistent Agent Souls (EXPERIMENTAL — Wave 6.1 Part B)

Cross-project cryptographic identities for agents stored in
`~/.baton/central.db`. Each soul has an Ed25519 keypair (private key in
`~/.config/baton/souls/<soul_id>.ed25519`, mode 0600) and accumulates an
expertise vector across runs.

```bash
baton souls mint <role> <domain>     # Mint a new operator-issued soul
baton souls list [--role ROLE]       # List active souls
baton souls show <soul_id>           # Show metadata + expertise
baton souls retire <soul_id> [--successor SUCC]
baton souls revoke <soul_id> --confirm
```

Schema v33 added the `soul_revocations` table (PR #68). The Python API
exposes `SoulRegistry.revoke()`, `is_revoked()`, `list_revocations()`,
and `rotate()` (mints a successor keypair atomically). The
revocation-aware verification path is `SoulRouter.verify_signature()`.

> **Known gap (bd-1ca2).** The CLI `revoke` subcommand only accepts
> `--confirm`; the `--reason`, `list-revocations`, and `rotate`
> subcommands are available via the Python `SoulRegistry` API but not yet
> wired into the CLI. Existing callers of the legacy `soul.verify` are
> not migrated to the revocation-aware `SoulRouter.verify_signature()`
> path either; tracked in bd-1ca2.

### Executable Beads (EXPERIMENTAL — Wave 6.1 Part C)

`bead_type="executable"` beads carry a script body anchored in
`refs/notes/baton-bead-scripts`. Pipeline: `ScriptLinter` (denylist of
dangerous patterns) -> optional soul signature -> `BeadStore.write()`
with `status="quarantine"` -> `AuditorGate.approve(bead_id)` flips
status to `open` -> `ExecutableBeadRunner.run()` executes the script
through `Sandbox` (wall-clock timeout + memory limit + captured I/O) and
writes a child `discovery` bead linked via `validates`/`contradicts`.
Gated behind `BATON_EXEC_BEADS_ENABLED=1`.

> **Trust boundary.** The sandbox is **process-level only** — no
> filesystem namespacing, network namespacing, or syscall filter. Trust
> model assumes locally-authored, version-controlled, team-reviewed
> scripts. Beads from external origins (federation, downloaded packs,
> fork PRs) emit a `[security]` warning to stderr from `baton beads
> exec`. Single source of truth: [`references/baton-patterns.md`](references/baton-patterns.md)
> -> *"Pattern: Executable Beads — Trust Boundary"*.

### Git-Native Bead Persistence (Wave 6.1 Part A)

Beads are anchored to git via `refs/notes/baton-beads` (and
`refs/notes/baton-bead-scripts` for executable bead bodies). The
installer (`scripts/install.sh` Step 5) auto-configures the wildcard
notes refspec so beads round-trip across clones; the runtime warns once
per session if the refspec is missing. Opt out with
`BATON_SKIP_GIT_NOTES_SETUP=1`. (PR #66.)

### Cross-Project Intelligence

Execution data syncs to `~/.baton/central.db`. Query agent reliability,
token costs, and failure rates across projects:

```bash
baton sync                   # Sync current project
baton sync --all             # Sync all registered projects
baton sync status            # Show sync watermarks
baton cquery agents          # Agent reliability across projects
baton cquery costs           # Token costs by task type
baton cquery gaps            # Recurring knowledge gaps
baton cquery failures        # Project failure rates
baton cquery mapping         # External item -> plan mapping
```

### Local Project Queries

Query the current project's execution history with predefined views or
ad-hoc SQL:

```bash
baton query agent-reliability       # Agent success rates
baton query tasks                   # Recent task list
baton query task-detail TASK_ID     # Full breakdown for one task
baton query gate-stats              # Gate pass rates
baton query cost-by-agent           # Token costs by agent
baton query patterns                # Learned patterns
baton query stalled --hours 4       # Stalled executions
baton query --sql "SELECT ..."      # Ad-hoc SQL
```

### Pattern Learning

The engine learns from past executions -- identifies recurring agent
combinations, recommends budget adjustments, proposes prompt improvements,
and surfaces anomalies. The learning pipeline includes:

- **Pattern detection** -- `baton patterns` surfaces recurring
  orchestration patterns with confidence scores
- **Budget tuning** -- `baton budget` recommends tier adjustments
  based on historical cost data
- **Prompt evolution** -- `baton evolve` proposes prompt improvements
  backed by performance data
- **Anomaly detection** -- `baton anomalies` flags statistical deviations
  in agent behavior
- **Experiments** -- `baton experiment` manages controlled trials to
  validate improvement recommendations
- **Full-cycle improvement** -- `baton learn improve --run` executes the
  complete loop: detect anomalies, generate recommendations, auto-apply
  safe changes, escalate risky ones, start experiments

### Learning Automation

A closed-loop system that detects operational issues and auto-corrects them
with confidence thresholds:

- **Detection** -- after each execution, the engine scans for routing
  mismatches, agent degradations, knowledge gaps, gate errors, roster
  bloat, and pattern drift
- **Issue ledger** -- structured SQLite-backed ledger with occurrence
  tracking, severity classification, and status lifecycle
  (`open` -> `proposed` -> `applied` -> `resolved`)
- **Auto-correction** -- issues that recur above a confidence threshold
  (e.g., 3 routing mismatches) are automatically proposed and can be
  applied to `learned-overrides.json`, which the router and planner
  consume at plan time
- **Structured interview** -- `baton learn interview` walks through
  open issues interactively, collecting human decisions for cases that
  require judgment (pattern drift, prompt evolution)
- **Rollback** -- any applied fix can be reset with `baton learn reset`,
  reopening the issue and removing the override

### REST API and PMO UI

A FastAPI server exposes the full engine over HTTP with 10 route modules:

| Route | Purpose |
|-------|---------|
| `/api/v1/health` | Liveness and readiness probes |
| `/api/v1/plans` | Plan creation and retrieval |
| `/api/v1/executions` | Execution lifecycle (start, record, gate, complete) |
| `/api/v1/agents` | Agent registry queries |
| `/api/v1/observe` | Dashboard, traces, usage records |
| `/api/v1/decisions` | Human-in-the-loop decision management |
| `/api/v1/events` | SSE event streaming |
| `/api/v1/webhooks` | Outbound webhook subscription CRUD |
| `/api/v1/pmo` | Portfolio management (board, projects, forge, execute, gates, changelist, review, signals) |
| `/api/v1/learn` | Learning issues and auto-correction |

The API supports Bearer token authentication, CORS configuration,
user identity middleware (`BATON_APPROVAL_MODE`), and outbound webhooks
with HMAC-SHA256 signing and Slack Block Kit payloads.

A React/Vite **PMO frontend** provides a complete plan-to-merge
lifecycle:

- **Kanban board** with 6 columns (queued, executing, awaiting_human,
  validating, review, deployed) and program health dashboards
- **Smart Forge** with SSE progress streaming (5-stage indicator)
- **Advanced plan editor** with model selection, dependency multi-select,
  tag inputs, and gate editing
- **Execution controls** -- pause/resume/cancel (SIGSTOP/SIGCONT/SIGTERM),
  retry-step and skip-step for failed steps, bead alert flags
- **Changelist review** -- post-execution file tree grouped by agent with
  diff stats, merge and PR buttons
- **Role-based approval** -- request-review workflow with audit trail
  (`approval_log` table)

```bash
baton serve --port 8741              # API only
baton pmo serve --port 8741          # API + PMO UI
```

### External Source Adapters

Connect Azure DevOps, Jira, GitHub, or Linear as work-item sources.
Items sync to `central.db` and link to baton plans. The Azure DevOps
adapter is fully implemented; Jira, GitHub, and Linear adapters can be
added by implementing the `ExternalSourceAdapter` protocol.

```bash
baton source add ado --name "My ADO" --org myorg --project myproj --pat-env ADO_PAT
baton source list
baton source sync --all
baton source map EXTERNAL_ID --project PROJECT_ID --task TASK_ID
```

---

## How It Works

```
Human  <-->  Claude Code  <-->  baton CLI  <-->  Python engine
        (natural language)  (structured text)  (state machine)
```

Claude never imports the Python package. It reads structured text output
from `baton` commands and acts on it. The CLI output format is the only
contract between Claude and the engine.

```
                    +----------------------------------------------------+
                    |                    ORCHESTRATOR                     |
                    |  Reads 16 reference procedures inline               |
                    +------------------------+---------------------------+
                                             |
                          baton plan --------+-------- baton execute
                                             |
              +------------------------------+-------------------------------+
              |                              |                               |
              v                              v                               v
    +------------------+          +--------------------+          +------------------+
    |     AUDITOR      |          |    SPECIALIST      |          |   TALENT         |
    |  (veto power)    |          |    AGENTS          |          |   BUILDER        |
    +------------------+          +--------------------+          +------------------+

    +--------------------------------------------------------------------+
    |                     EXECUTION ENGINE (Python)                       |
    |                                                                    |
    |  Planner --> Executor --> Dispatcher --> Gates --> Persistence      |
    |  Events  --> Telemetry --> Traces --> Retrospectives                |
    |  Pattern Learner --> Budget Tuner --> Prompt Evolution              |
    |  Federated Sync --> Central DB --> Cross-Project Queries            |
    |  PMO Store --> Smart Forge --> REST API --> PMO UI                  |
    |  Bead Store --> Knowledge Resolver --> Learning Automation          |
    +--------------------------------------------------------------------+
```

### Design Principle

**Pay for context only when you need isolation.** Every subagent costs a
full context window, startup latency, and information loss. Agent Baton
minimizes this: research and routing run inline, specialists get their own
context only for substantial work, shared knowledge lives in reference
documents instead of being duplicated.

---

## Usage

### Orchestrated Tasks (complex, multi-domain work)

```
Use the orchestrator to build a health check API with tests and documentation
```

### Engine-Driven Workflow (explicit control)

```bash
baton plan "Add input validation to the API" --save --explain
baton execute start
baton execute next              # Get next action
baton execute record --step-id 1.1 --agent backend-engineer --status complete
baton execute gate --phase-id 2 --result pass
baton execute complete
```

### Direct Agent Invocation (simple, single-domain tasks)

```
Use the data-analyst to investigate our fleet utilization trends
Use the security-reviewer to audit our authentication flow
Use the test-engineer to add unit tests for the payment module
```

### Autonomous Execution (no Claude Code session)

```bash
baton execute run               # Full loop: plan -> dispatch -> gate -> complete
```

---

## CLI Reference

The `baton` CLI provides 50+ commands organized into ten groups:

<details>
<summary><strong>Core Workflow</strong> -- plan, execute, recover</summary>

| Command | Description |
|---------|-------------|
| `baton plan` | Create a data-driven execution plan |
| `baton plan --dry-run` | Preview plan + token/cost forecast without saving |
| `baton execute start` | Start execution from a saved plan |
| `baton execute next [--all]` | Get next action(s) to perform |
| `baton execute record` | Record a step completion |
| `baton execute dispatched` | Mark a step as in-flight |
| `baton execute gate` | Record a QA gate result |
| `baton execute approve` | Record a human approval decision |
| `baton execute amend` | Add phases or steps mid-execution |
| `baton execute team-record` | Record team member completions |
| `baton execute run` | Autonomous execution loop |
| `baton execute complete` | Finalize execution |
| `baton execute status` | Show current execution state |
| `baton execute resume` | Resume after crash or interruption |
| `baton execute list` | List all executions |
| `baton execute switch` | Switch active execution |
| `baton status` | Show team-context file status |

</details>

<details>
<summary><strong>Execution (Advanced)</strong> -- daemon, async, decisions</summary>

| Command | Description |
|---------|-------------|
| `baton daemon start/stop/status/list` | Background execution management |
| `baton async --dispatch/--pending/--show` | Dispatch and track asynchronous tasks |
| `baton decide --list/--show/--resolve` | Manage human decision requests |

</details>

<details>
<summary><strong>Observability</strong> -- traces, dashboards, usage, queries</summary>

| Command | Description |
|---------|-------------|
| `baton usage` | Token usage statistics |
| `baton dashboard [--write]` | Generate usage dashboard |
| `baton trace` | Execution traces |
| `baton retro` | Task retrospectives |
| `baton telemetry` | Agent telemetry events |
| `baton context-profile` | Context efficiency profiles |
| `baton context current/briefing/gaps` | Situational awareness for agents |
| `baton query <subcommand>` | SQL queries against this project's baton.db |
| `baton cleanup` | Archive old execution artifacts |
| `baton sync --migrate-storage` | Migrate JSON flat files to SQLite |

</details>

<details>
<summary><strong>Governance</strong> -- risk, compliance, validation</summary>

| Command | Description |
|---------|-------------|
| `baton classify` | Classify task sensitivity |
| `baton compliance` | Show compliance reports |
| `baton policy` | List or evaluate guardrail presets |
| `baton escalations` | Show or resolve agent escalations |
| `baton validate` | Validate agent definitions |
| `baton spec-check` | Validate agent output against a spec |
| `baton detect` | Detect project stack |

</details>

<details>
<summary><strong>Improvement</strong> -- learning, evolution, tuning</summary>

| Command | Description |
|---------|-------------|
| `baton scores [--agent/--trends/--teams]` | Agent performance scorecards |
| `baton evolve` | Propose prompt improvements |
| `baton patterns` | Learned orchestration patterns |
| `baton budget` | Budget tier recommendations |
| `baton changelog` | Agent changelog and backup management |
| `baton anomalies [--watch]` | Detect system anomalies |
| `baton experiment list/show/conclude/rollback` | Manage improvement experiments |
| `baton learn improve --run/--force/--report` | Run the full improvement loop |
| `baton learn status` | Dashboard of open learning issues |
| `baton learn issues` | List issues with filters (`--type`, `--severity`, `--status`) |
| `baton learn analyze` | Detect patterns across issues, propose fixes |
| `baton learn apply` | Apply a specific fix or all auto-applicable fixes |
| `baton learn interview` | Structured dialogue for human-directed decisions |
| `baton learn history` | Resolution history with outcomes |
| `baton learn reset` | Reopen an issue or rollback an applied fix |

</details>

<details>
<summary><strong>Distribution</strong> -- packaging, sharing, install</summary>

| Command | Description |
|---------|-------------|
| `baton install` | Install agents and references to a project |
| `baton uninstall` | Remove agent-baton files (project or user scope) |
| `baton package` | Create or install package archives |
| `baton publish` | Publish to a local registry |
| `baton pull` | Pull from a registry |
| `baton sync --verify ARCHIVE` | Verify a package archive |
| `baton transfer` | Transfer between projects |

</details>

<details>
<summary><strong>Agents and Events</strong></summary>

| Command | Description |
|---------|-------------|
| `baton agents` | List available agents |
| `baton route [ROLES]` | Route roles to agent flavors |
| `baton events` | Query the event log |
| `baton incident` | Manage incident response |

</details>

<details>
<summary><strong>Memory (Beads)</strong></summary>

| Command | Description |
|---------|-------------|
| `baton beads list` | List beads with filters (`--type`, `--status`, `--task`, `--tag`) |
| `baton beads show <id>` | Show a single bead in detail (JSON) |
| `baton beads ready` | Show unblocked open beads |
| `baton beads close <id>` | Close a bead with optional `--summary` |
| `baton beads link <src> --relates-to\|--contradicts\|--extends\|--blocks\|--validates <tgt>` | Link two beads |
| `baton beads cleanup` | Archive old closed beads (memory decay) |
| `baton beads promote <id> --pack NAME` | Promote a bead to a knowledge document |
| `baton beads graph <task-id>` | Show the dependency graph for a task's beads |

</details>

<details>
<summary><strong>Storage and Sync</strong></summary>

| Command | Description |
|---------|-------------|
| `baton sync [--all]` | Sync to `~/.baton/central.db` |
| `baton sync status` | Show sync watermarks |
| `baton cquery` | Cross-project SQL queries against central.db |
| `baton source add/list/sync/remove/map` | External source connections |

</details>

<details>
<summary><strong>Portfolio and API</strong></summary>

| Command | Description |
|---------|-------------|
| `baton pmo serve` | Start the PMO HTTP server with UI |
| `baton pmo status` | Terminal Kanban board summary |
| `baton pmo add` | Register a project with the PMO |
| `baton pmo health` | Program health bar summary |
| `baton serve` | Start the HTTP API server (API only) |

</details>

<details>
<summary><strong>Deprecated aliases (still work, removal in a future release)</strong></summary>

These top-level commands still execute but print a deprecation warning to
stderr on every invocation. Update scripts to use the new paths.

| Old command | New canonical path | Bead |
|-------------|-------------------|------|
| `baton migrate-storage` | `baton sync --migrate-storage` | bd-8eef |
| `baton verify-package ARCHIVE` | `baton sync --verify ARCHIVE` | bd-7eec |
| `baton improve` | `baton learn improve` | bd-5049 |

PR #70 (end-user-readiness #12) consolidated the three top-level
commands into the canonical `sync` and `learn` groups. Old aliases keep
working for the v0.1 series but emit a deprecation warning to stderr on
every invocation.

</details>

---

## Project Structure

```
agents/            <- 22 agent definitions (markdown + YAML frontmatter)
references/        <- 16 reference procedures (shared knowledge)
templates/         <- CLAUDE.md + settings.json + skills for target projects
scripts/           <- Install scripts (Linux + Windows)
docs/              <- Architecture docs, ADRs, invariants, troubleshooting
agent_baton/       <- Python package
  models/          <- Data models (24 modules)
  core/            <- Business logic (11 sub-packages)
    engine/        <- Planner, executor, dispatcher, gates, persistence,
    |                 knowledge resolver, bead store, bead signals
    orchestration/ <- Agent registry, router, context manager,
    |                 knowledge registry
    pmo/           <- PMO store, scanner, Smart Forge
    storage/       <- Central DB, federated sync, external adapters
    govern/        <- Classification, compliance, policy
    observe/       <- Tracing, usage, dashboard, telemetry
    improve/       <- Scoring, evolution, experiments, proposals, rollback
    learn/         <- Pattern learner, budget tuner, learning automation
    distribute/    <- Packaging, sharing, registry (+ experimental)
    events/        <- Event bus, domain events, projections
    runtime/       <- Async worker, supervisor, headless Claude, decisions
  api/             <- FastAPI REST API (10 route modules, webhooks, middleware)
  cli/             <- CLI interface (50+ commands)
tests/             <- Test suite (~6202 tests, pytest)
pmo-ui/            <- React/Vite PMO frontend
```

---

## Configuration

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Target a specific execution in multi-task scenarios | auto-detected |
| `BATON_DB_PATH` | Override the project `baton.db` location (subagents in worktrees can rely on the upward-walk discovery) | discovered |
| `BATON_API_TOKEN` | Bearer token for API authentication | none |
| `BATON_APPROVAL_MODE` | Approval policy: `local` (self-approve) or `team` (different reviewer required). In `team` mode, `baton swarm` defaults `--require-approval-bead` ON. | `local` |
| `BATON_SELFHEAL_ENABLED` | Enable speculator/selfheal escalation on gate failure. Falsy values (`0`, `false`, `no`) are honoured and write a `selfheal_suppressed` event to `compliance-audit.jsonl`. | `0` |
| `BATON_RUN_TOKEN_CEILING` | Per-run cumulative spend cap (USD float). When set, `BudgetEnforcer.check_run_ceiling()` raises `RunTokenCeilingExceeded` before exceeding the cap and selfheal/speculator/immune all abort. Counter is restored on `baton execute resume`. (See PR #67/#73, bd-3f80.) | unset |
| `BATON_WORKTREE_STALE_HOURS` | Max age (hours) for the worktree GC to reclaim a stale linked worktree. New canonical name; `BATON_WORKTREE_GC_HOURS` is kept as a legacy alias. | `4` |
| `BATON_EXPERIMENTAL` | CSV opt-in flag for experimental features. Required for `baton swarm` (`BATON_EXPERIMENTAL=swarm`). | unset |
| `BATON_SWARM_ENABLED` | Required (in addition to `BATON_EXPERIMENTAL=swarm`) to actually dispatch a swarm refactor. | unset |
| `BATON_SOULS_ENABLED` / `BATON_EXEC_BEADS_ENABLED` | Feature flags for persistent agent souls (Wave 6.1B) and executable beads (Wave 6.1C). | unset |
| `BATON_SKIP_GIT_NOTES_SETUP` | Set to `1` to silence the install-time git-notes replication setup and the runtime warning emitted by `NotesAdapter.write()` when the wildcard refspec is missing. | unset |
| `ANTHROPIC_API_KEY` | Required for AI classification (`pip install agent-baton[classify]`) and the Haiku planner classifier | none |

> **Note on experimental features.** `baton swarm`, the Wave 6.2 immune-system
> daemon, and the predictive watcher (Wave 6.2 Part C) are explicitly marked
> EXPERIMENTAL and gated behind feature flags. They emit stub warnings to
> stderr on every invocation and must not be relied on for production work.
> See [Known Integration Gaps](docs/architecture.md#13-known-integration-gaps-as-of-2026-04-28)
> for the current paper-functionality matrix.

### Plan Command Flags

| Flag | Description |
|------|-------------|
| `--save` | Write `plan.json` and `plan.md` to `.claude/team-context/` |
| `--explain` | Show reasoning behind plan decisions |
| `--json` | Output plan as JSON instead of markdown |
| `--task-type TYPE` | Override task type (new-feature, bug-fix, refactor, etc.) |
| `--agents NAMES` | Override auto-selected agents (comma-separated) |
| `--knowledge PATH` | Attach a knowledge document (repeatable) |
| `--knowledge-pack NAME` | Attach a knowledge pack (repeatable) |
| `--intervention LEVEL` | Escalation threshold: low, medium, high |
| `--model MODEL` | Default model for dispatched agents (opus, sonnet) |
| `--complexity LEVEL` | Override complexity: light, medium, heavy |

### Files Installed to Target Projects

| File | Purpose |
|------|---------|
| `.claude/agents/*.md` | Agent definitions (19 files) |
| `.claude/references/*.md` | Reference procedures (15 files) |
| `.claude/CLAUDE.md` | Project development guide (from template) |
| `.claude/settings.json` | Hook configuration |

---

## For Developers

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
pip install -e ".[dev]"        # Core + test deps
pip install -e ".[dev,api]"    # Everything including REST API
pytest                         # ~6202 tests
```

Requires Python 3.10+. The only runtime dependency is PyYAML.

### Optional Dependencies

| Extra | Packages | Purpose |
|-------|----------|---------|
| `dev` | pytest, pytest-cov | Test suite |
| `pmo` | FastAPI, uvicorn, httpx, sse-starlette, pydantic | REST API and PMO server |
| `api` | same as `pmo` | Backward-compatible alias |
| `daemon` | uvicorn | Background daemon runner |
| `classify` | anthropic | AI-powered risk classification |
| `all` | pmo + classify | Everything except dev tools |

### Key Documentation

| Document | Contents |
|----------|----------|
| [CLAUDE.md](CLAUDE.md) | Development guide and conventions |
| [QUICKSTART.md](QUICKSTART.md) | Getting started for new users |
| [docs/architecture.md](docs/architecture.md) | Package layout and dependency graph |
| [docs/design-decisions.md](docs/design-decisions.md) | ADR log |
| [docs/invariants.md](docs/invariants.md) | Interface boundaries (CLI output contract) |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common issues and solutions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |

---

## Project Status

Agent Baton is in active development (v0.1.0). The orchestration engine,
all 22 agents, 16 references, knowledge delivery, bead memory system,
PMO subsystem with end-to-end workflow (plan, edit, execute, review,
merge), REST API with webhooks, federated sync, event system, learning
automation, and the improvement pipeline are implemented and tested.

- **Python**: 3.10+
- **Runtime dependency**: PyYAML only
- **Optional**: FastAPI + uvicorn (REST API), Anthropic SDK (AI classification)
- **Test suite**: ~6202 tests (pytest)
- **External adapters**: Azure DevOps implemented; Jira, GitHub, Linear
  protocols defined

### End-User Readiness Sweep — 2026-04-28

The v0.1 series shipped a 12-concern end-user-readiness sweep
(PRs #59 + #61–#73) that closed paper-functionality gaps, hardened
worktree isolation, added the run-level token ceiling, gated stub
features behind `BATON_EXPERIMENTAL`, and consolidated the CLI surface.
Three integration gaps remain:

| Gap | Bead | Surface |
|-----|------|---------|
| Executor wiring of `BATON_RUN_TOKEN_CEILING` | bd-3f80 | Selfheal/speculator/immune respect the cap; main `Executor.dispatch()` only warns |
| Soul callers not migrated | bd-1ca2 | Existing `soul.verify` callers not yet routed through revocation-aware `SoulRouter.verify_signature()` |
| Wave 6.1 Part A integration | bd-971d | Git-notes bead persistence + executor BeadStore handoff |

See [Known Integration Gaps](docs/architecture.md#13-known-integration-gaps-as-of-2026-04-28) for the full matrix.

### Experimental Features

The following surfaces are explicitly EXPERIMENTAL and gated behind
feature flags. Stub implementations emit warnings to stderr; do not rely
on them for production work.

| Feature | Flag | Status |
|---------|------|--------|
| `baton swarm` (massive refactor dispatcher) | `BATON_EXPERIMENTAL=swarm` + `BATON_SWARM_ENABLED=1` | v1 stub — partition plans real, agent dispatch not wired (bd-18f6) |
| Immune-system daemon | feature flag in `core/intel/immune.py` | Wave 6.2 Part B stub (bd-dd52) |
| Predictive watcher | feature flag in `core/intel/predictive.py` | Wave 6.2 Part C stub (bd-708a) |
| Executable beads | `BATON_EXEC_BEADS_ENABLED=1` | Process-level sandbox only; not safe for external-origin beads (bd-fe40) |
| Persistent agent souls | `BATON_SOULS_ENABLED=1` | CLI partial: rotate/list-revocations API-only (bd-b05e) |

---

## Tips

- **Say "use the orchestrator"** explicitly for your first few runs so
  Claude Code routes to the right agent.
- **3-5 specialists per task.** More than that and coordination overhead
  outweighs benefits.
- **Crash recovery is automatic.** Session dies mid-task? New session +
  `baton execute resume`.
- **Run tasks in parallel.** Each `baton execute start` prints
  `export BATON_TASK_ID=...`. Run that in each terminal.
- **Use `baton query` for local data, `baton cquery` for cross-project.**
  They target different databases.
- **Beads are automatic.** Agents emit bead signals during execution.
  Use `baton beads ready` to surface unblocked work items.

---

## License

License pending. Contact the maintainers for terms.
