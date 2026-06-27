# Agent Baton

📖 **Docs:** <https://davegerson.github.io/agent-baton/>

**A project manager for Claude Code.**

Agent Baton project-manages an effort from plan to merge. It breaks the work
down so you have **foresight** into how it will go and where it might break,
composes a **bespoke team** of specialist subagents tuned to *this* problem,
dispatches the **right agent onto the right problem at the right time**, and
keeps the work from diverging with **checks and balances** — domain-expert
verification, not just code review. Claude Code owns the work; Baton owns the
plan, the team, and the guardrails.

**The four jobs Baton does for every effort:**

- **Plan with foresight** — decompose the task, sequence the phases with
  dependency awareness, classify risk, and forecast cost *before* execution.
  You see the shape of the work — and where it's likely to go wrong — up front.
- **Compose the right team** — a single generalist agent carrying context for
  the whole codebase suffers context rot and is never sharply tuned to the
  problem in front of it. The `talent-builder` narrows in on the exact
  specialists an effort needs and creates them, so you assemble an ad-hoc fleet
  purpose-built for *this* problem instead of overloading a few generalists.
- **Right agent, right problem, right time** — a deterministic engine dispatches
  each phase to the specialist that fits it, runs automated QA gates between
  phases, and organizes the whole effort through a plan-to-merge PMO flow.
- **Checks & balances** — every change is risk-classified; medium/high-risk work
  pulls in an independent `auditor` (with veto power) and, in regulated domains,
  a `subject-matter-expert`. The question is whether the work is *functionally
  right*, not just whether it lints. Humans are one kind of check; the engine
  enforces the rest through policy gates and an auditable trail.

For regulated work, Baton can also run as a **governance harness** (policy hooks
evaluate every tool call and evidence bundles are generated automatically) or a
**managed loop** (full plan→dispatch→gate→approval), and can import specs from
GitHub Issues / Azure DevOps for senior review before any agent fires.

---

## Why Agent Baton?

**The problem:** Claude Code is powerful, but a complex effort — one that spans
many files, needs different kinds of expertise, and has to actually be
*correct* — is hard to run as one long conversation. You get context rot, missed
coverage, no foresight into what's coming, and no record of what happened.

**The solution:** Agent Baton gives Claude Code a project-management layer. It
plans the effort, composes a bespoke team of specialists, dispatches the right
agent to each phase, runs checks that catch divergence, and tracks everything —
so you keep control and oversight while the agents do the heavy lifting.

| Without Baton | With Baton |
|---------------|------------|
| One long conversation doing everything | A planned effort, phased and sequenced |
| One generalist drowning in whole-codebase context | Bespoke specialists tuned to each problem |
| No idea what's coming or what might break | Up-front plan, risk classification, cost forecast |
| Manual "did you run the tests?" | Automated gates + domain-expert checks between phases |
| Hope the AI got it *right* | Independent auditor / SME verification on risky work |
| No record of what happened | Full traces, usage logs, retrospectives |
| Single point of failure | Crash recovery via `baton execute resume` |

---

## How It Works

Every effort moves through four phases in sequence: plan with foresight, compose
the right team, dispatch the right agent, apply checks and balances. Here is what
that looks like in practice.

**1. Plan with foresight.** You hand the orchestrator a task. It runs
`baton plan`, which auto-detects your stack, classifies risk, assigns budget
tiers, sequences phases with dependency awareness, and forecasts cost — all
before a single specialist fires. The result is a `plan.json` + `plan.md` written
to `.claude/team-context/`. You see where the work will break before it starts.

**2. Compose the right team.** If the plan calls for a role that doesn't exist
yet, the `talent-builder` creates it: a narrowly scoped agent definition with
baked-in knowledge for exactly this problem. This directly addresses context rot
— a generalist agent carrying whole-codebase context is never as effective as a
specialist whose context window is entirely focused on one domain.

**3. Dispatch the right agent, right time.** `baton execute start` hands the plan
to the execution engine. The engine returns a stream of typed actions:

- `DISPATCH` — spawn the named specialist with the provided prompt
- `GATE` — run an automated QA check (tests, lint, schema validation)
- `APPROVAL` — wait for human or auditor sign-off
- `INTERACT` — multi-turn dialogue with the orchestrator

The orchestrator records each result (`baton execute record`, `baton execute
gate`) and the engine advances the state machine. `baton execute complete`
finalizes the trace, usage log, and retrospective.

**4. Checks and balances.** Risk is classified at plan time. LOW-risk work gets
guardrail presets applied inline. MEDIUM/HIGH-risk work pulls in an independent
`auditor` subagent — operating in a separate context so it can overrule the plan
without being influenced by the planner's reasoning. Regulated domains
additionally require the `subject-matter-expert`. The question the auditor asks
is whether the work is *functionally correct*, not just whether it lints. Policy
gates enforce the rest; everything is written to an auditable trail.

### Architecture contract

```
Human  <-->  Claude Code  <-->  baton CLI  <-->  Python engine
        (natural language)  (structured text)  (state machine)
```

Claude never imports the Python package. It reads structured text output from
`baton` commands and acts on it. The CLI output format is the only contract
between Claude and the engine — specifically `_print_action()` in
`agent_baton/cli/commands/execution/execute.py`, which is treated as a public API.

```
                    ┌────────────────────────────────────┐
                    │           ORCHESTRATOR              │
                    │  Reads 19 reference procedures      │
                    └───────────────┬────────────────────┘
                                    │
                    baton plan ─────┴───── baton execute
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                        │
            v                       v                        v
    ┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
    │    AUDITOR    │    │   SPECIALIST     │    │  TALENT-BUILDER  │
    │  (veto power) │    │    AGENTS        │    │  (agent factory) │
    └───────────────┘    └──────────────────┘    └──────────────────┘

    ┌─────────────────────────────────────────────────────────────┐
    │                   EXECUTION ENGINE (Python)                  │
    │  Planner → Executor → Dispatcher → Gates → Persistence       │
    │  Events → Telemetry → Traces → Retrospectives                │
    └─────────────────────────────────────────────────────────────┘
```

**Design principle: pay for context only when you need isolation.** Every
subagent costs a full context window, startup latency, and information loss at
handoff. Baton minimizes this by running research and routing inline at the
orchestrator level; specialists get their own context only for substantial work.
Shared knowledge lives in reference documents instead of being duplicated across
prompts. This is pillar 2 in practice — see [Pillar 2 — Compose the Right
Team](#pillar-2--compose-the-right-team).

---

## Get Started in 5 Minutes

### 1. Install agent definitions

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
scripts/install.sh          # Linux/macOS
# or: scripts/install.ps1   # Windows (no admin required)
```

The installer prompts for scope: user-level (`~/.claude/`) for all projects, or
project-level (`.claude/`) for the current project only. It copies 30 agent
definitions, 19 reference procedures, a template `CLAUDE.md`, `settings.json`
hooks, and skills. It also attempts to install `bd` (the bead backend) via npm or
Homebrew.

### 2. Install the Python engine

**From PyPI (recommended):**

```bash
pip install agent-baton          # Core engine + CLI
pip install agent-baton[pmo]     # + REST API and PMO server
pip install agent-baton[classify] # + AI risk classification
```

**From source (for development):**

```bash
pip install -e ".[dev]"          # Core engine + CLI + test deps
```

Requires Python 3.10+. Runtime dependencies: PyYAML, pydantic, cryptography. The
`pmo` extra adds FastAPI + uvicorn; `classify` adds the Anthropic SDK for
AI-powered risk classification.

### 3. Verify

```bash
# In Claude Code
/agents                     # Should list ~30 agents

# In terminal
baton agents                # List agents from Python registry
baton detect                # Detect your project's stack
```

### 4. Optional: install cymbal (recommended)

[cymbal](https://github.com/1broseidon/cymbal) is a tree-sitter code indexer that
agents use for symbol lookup (`cymbal investigate <symbol>`) and blast-radius
analysis (`cymbal impact <symbol>`) before edits. Without it, agents fall back to
grep — slower and less precise.

```bash
cymbal --help               # Check if already installed
# If not, install the binary to ~/.local/bin/
# (see cymbal docs for platform-specific instructions)
```

### 5. Run your first task

In a Claude Code session:

```
Use the orchestrator to add a health check endpoint with tests
```

The orchestrator plans the work (pillar 1), creates or selects the right
specialists (pillar 2), dispatches each to its phase and runs QA gates between
them (pillar 3), and invokes the auditor if risk warrants it (pillar 4). It
delivers tested, committed code.

### Usage examples

**Orchestrated task** — complex, multi-domain work:

```
Use the orchestrator to build a health check API with tests and documentation
```

**Engine-driven workflow** — explicit control over each step:

```bash
baton plan "Add input validation to the API" --save --explain
baton execute start
baton execute next              # Get next action
baton execute record --step-id 1.1 --agent backend-engineer --status complete
baton execute gate --phase-id 2 --result pass
baton execute complete
```

**Direct agent invocation** — single-domain tasks that don't need a full plan:

```
Use the data-analyst to investigate our fleet utilization trends
Use the security-reviewer to audit our authentication flow
Use the test-engineer to add unit tests for the payment module
```

**Autonomous execution** — no Claude Code session required:

```bash
baton execute run               # Full loop: plan -> dispatch -> gate -> complete
```

---

## The Four Pillars

## Pillar 1 — Plan with Foresight

Before a single token goes to an agent, Baton builds a complete picture of the
work: what kind of task it is, which phases it needs, which agents fit those
phases, where it's likely to break, and what it will cost. You commit to a plan,
not a hope.

### How planning works

`baton plan "<task>"` runs a deterministic multi-stage pipeline:

1. **Classification** — detects the project stack and classifies the task. By
   default this uses an AI classifier (Sonnet via the `claude` CLI, with the full
   agent roster as context) or falls back to a keyword heuristic when the CLI is
   unavailable. It assigns a task type (`new-feature`, `bug-fix`, `migration`,
   `data-analysis`, …), a complexity tier (`light`, `medium`, `heavy`), and an
   archetype (`direct`, `investigative`, `phased`).
2. **Roster** — assembles the agent list for that complexity tier (light → 1
   agent, medium → up to 3, heavy → up to 5), applies retrospective feedback
   (agents that underperformed on similar tasks are deprioritized), then routes
   each base role to its stack-flavored variant.
3. **Risk** — runs the `DataClassifier` (keyword signals + structural analysis).
   Risk can only be escalated, never lowered by a secondary signal. Output:
   `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`. Risk drives git strategy and safety
   roster injection — HIGH plans get `code-reviewer` appended; compliance/audit
   keywords pull in the `auditor` regardless of complexity.
4. **Decomposition + Foresight** — builds the phase list, attaches knowledge
   documents to each step, then runs the **Foresight Engine**. Foresight scans
   step descriptions for capability gaps and prerequisites you didn't ask for but
   that execution will hit — a migration step without rollback scaffolding, an
   API step without schema validation — and *inserts a preparatory phase* before
   the phase that triggers it. It lowers its confidence threshold for HIGH-risk
   plans so more gaps surface.
5. **Assembly + validation** — assembles the final `MachinePlan` with gate
   commands, knowledge attachments, budget tier, and execution mode. The hard
   gate (`BATON_PLANNER_HARD_GATE`) can block structurally defective plans.

### Key commands

```bash
# Preview the plan, cost forecast, and gate timing without saving
baton plan "Add OAuth2 login" --dry-run

# Save the plan and see why these agents and phases were chosen
baton plan "Add OAuth2 login" --save --explain

# Override complexity when you know more than the classifier
baton plan "Rename a constant across 2 files" --complexity light

# Start execution after saving
baton execute start
```

`--dry-run` renders a compact preview in seconds — risk, budget, phases, the
agent assigned to each step, the gates that will block, and a cost forecast
carrying an explicit **±50% confidence band**. The dollar figure is a planning
signal, not a bill.

Plans are not frozen: `baton execute amend` adds phases or steps while an
execution is running, and goal-driven mode (`baton goal "<condition>"`) evaluates
the condition at phase boundaries and proposes amendments until the goal is met
or the amend budget is exhausted.

## Pillar 2 — Compose the Right Team

The fundamental problem with a single generalist agent on a complex codebase is
not intelligence — it is **context rot**. The agent accumulates context across
every file it has touched and every phase it has completed; by step 4 it is
carrying the weight of steps 1–3 plus the entire codebase, and the
signal-to-noise ratio for the work in front of it has collapsed.

Baton's answer is **bespoke team composition**: assemble an ad-hoc fleet of
specialists purpose-built for this problem, then discard them when done. Each
specialist gets a clean context window containing only what it needs for its
phase.

### The talent-builder: specialists on demand

The 30 agents that ship with Baton cover the common roles. When you hit a domain
they don't cover — a new compliance framework, a specialized data system, a
regulatory area — the `talent-builder` builds the specialist you need:

- Researches the domain (reads existing code, schema, documentation)
- Decides which knowledge layer to use: facts small enough to bake into the
  prompt, reference schemas that go into a knowledge pack, or repeatable
  workflows that become skills
- Creates the agent file, knowledge pack, and skill scaffold
- Reports token cost and verification criteria before writing anything

The naming convention is `role--flavor` (e.g. `backend-engineer--python`,
`frontend-engineer--react`). First time Baton encounters a Go project,
`talent-builder` can create `backend-engineer--go`. From then on, the routing
table finds it automatically.

### The agent roster (30 agents)

| Category | Agents |
|----------|--------|
| Orchestration | `orchestrator`, `team-lead`, `task-runner` |
| Backend | `backend-engineer`, `--python`, `--node` |
| Frontend | `frontend-engineer`, `--react`, `--dotnet` |
| Architecture | `architect` |
| Quality | `test-engineer`, `code-reviewer`, `security-reviewer` |
| Governance | `auditor` (independent veto power) |
| Data | `data-engineer`, `data-analyst`, `data-scientist` |
| Visualization | `visualization-expert` |
| Operations | `devops-engineer` |
| Domain | `subject-matter-expert`, `learning-analyst`, `system-maintainer` |
| Meta | `talent-builder` (creates new agents, knowledge packs, skills) |
| Resilience | `immune-*` (autofix, deprecated-api, doc-drift, stale-comment, todo-rot, untested-edges) |

### How routing picks the flavor

`baton route [roles]` shows you what the planner will dispatch for your project:

```bash
baton route backend-engineer frontend-engineer
#   backend-engineer   → backend-engineer--python  *
#   frontend-engineer  → frontend-engineer            (no matching flavor)
```

The router scans the project root and up to two subdirectory levels for stack
signals (`pyproject.toml` → Python, `go.mod` → Go, `package.json` → JavaScript,
`next.config.js` → React, …). Root-level signals take priority over subdirectory
signals. When a flavored variant exists in the registry it wins; otherwise the
base agent runs. Learned overrides (`learned-overrides.json`), derived from past
executions, are consulted first so a project-specific correction persists without
touching the engine.

### Context economics

Every subagent costs a full context-window load, startup latency, and transfer
overhead. Baton minimizes it: research and routing run inline; specialists get
their own context only for substantial implementation phases; shared knowledge
lives in reference documents, not duplicated across windows; per-step MCP server
declarations keep unused tool schemas out of the context. Pay for isolated
context only when isolation is worth it.

## Pillar 3 — Right Agent, Right Problem, Right Time

A deterministic engine dispatches each specialist to its assigned phase, gates
the output before advancing, and recovers from crashes without losing state. You
interact with it through the `baton execute` command group; it does the
sequencing.

### The action loop

`ExecutionEngine` implements the `ExecutionDriver` 15-method protocol. Call it
once and it returns one of nine typed `ActionType` instructions:

| Action | What the caller does |
|--------|----------------------|
| `DISPATCH` | Spawn the named specialist with the built delegation prompt |
| `GATE` | Run the gate command as a subprocess and record pass/fail |
| `APPROVAL` | Pause for a human decision (`baton execute approve`) |
| `INTERACT` | Multi-turn step: agent responded, human inputs next turn |
| `FEEDBACK` | Present multiple-choice questions; dispatch based on answers |
| `CHECKPOINT` | Save state and start a fresh session to prevent context rot |
| `WAIT` | Parallel steps are still running; poll again |
| `COMPLETE` | Execution finished; call `engine.complete()` |
| `FAILED` | Execution cannot continue |

Every transition persists state to `baton.db` (SQLite) before returning. If the
session dies between calls, `baton execute resume` reconstructs the execution
state from the last checkpoint and re-issues the next action. Nothing is
re-dispatched; no step is duplicated.

### Concurrent execution

Multiple plans run in parallel. Each `baton execute start` prints
`export BATON_TASK_ID=<id>`. Set that variable in each terminal and the engine's
dispatch, gate-recording, and state reads are scoped to that task.

### Team steps

A step with a non-empty `team` list dispatches multiple agents to one step. Each
member carries its own role (`lead`, `implementer`, `reviewer`), intra-step
dependencies, and deliverables. A synthesis strategy controls how outputs merge
(`concatenate`, `merge_files`, or `agent_synthesis` — which re-dispatches a
synthesis agent, default `code-reviewer`, over the combined outcomes). Conflict
handling is configurable: `auto_merge`, `escalate` (surfaces the conflict as an
`APPROVAL` with both positions), or `fail`.

### Worktree isolation

When a step is parallel-safe, the engine provisions a linked git worktree at
`.claude/worktrees/<task_id>/<step_id>/` before dispatch, so concurrent agents
write to isolated working copies and no uncommitted change leaks between steps.
`WorktreeManager.gc_stale()` reclaims worktrees older than 4 hours (override with
`BATON_WORKTREE_STALE_HOURS`), skipping any referenced by a running execution.
Disable with `BATON_WORKTREE_ENABLED=0`.

### Selective MCP pass-through

Each step declares which MCP servers it needs; the dispatcher passes only those
into the agent's tool environment. Steps that need no external tools carry an
empty list, keeping unused tool schemas out of the context window.

### Resource governance

Concurrent agent caps are set per phase. `BATON_RUN_TOKEN_CEILING` sets a hard
USD spend cap per execution; `BudgetEnforcer` checks it before every record call
and raises `RunTokenCeilingExceeded` if the next step would breach it. The cap
survives crash recovery.

> **Known gap (bd-3f80).** The main `Executor.dispatch()` path warns at
> HIGH/CRITICAL risk but does not yet block individual dispatches when projected
> spend would exceed the ceiling. Enforcement is currently handled by the policy
> hooks.

### Headless and daemon execution

```bash
baton execute run --plan .claude/team-context/plan.json   # full loop, no session
baton daemon start --plan plan.json --max-parallel 3      # background, parallel
baton daemon status
baton daemon stop
```

`baton execute run` dispatches agents via `claude --print`, runs gates as
subprocesses, and loops until `COMPLETE` or `FAILED` (`--model`, `--max-steps`,
`--dry-run`). The daemon adds background execution with `--serve` (co-host the
REST API), `--resume`, and `baton decide` for human decision requests raised
mid-run.

### PMO plan-to-merge flow

`baton pmo serve` starts the FastAPI server and React/Vite frontend. The Kanban
board (queued → executing → awaiting_human → validating → review → deployed)
shows all active efforts. From there you create plans via Smart Forge, edit them,
control execution (pause/resume/cancel, retry or skip a failed step), review
changesets (file tree grouped by agent, with diff stats and PR/merge buttons),
and approve phases through a role-based request-review workflow with a full audit
trail. The PMO is how an effort progresses from spec to merged PR without a
terminal session open.

## Pillar 4 — Checks and Balances

Governance serves the pillars above it. It makes sure the right agent was
actually right — not just fast or syntactically correct. The checks are layered:
most run automatically; the expensive independent ones activate in proportion to
risk.

### Risk-tiered safety

Every task is classified by `DataClassifier` before the first agent fires.
Classification reads keyword and structural signals (regulated/PII, security,
infrastructure, database, path patterns) and produces a risk tier:

| Tier | Automatic response |
|------|--------------------|
| LOW | Guardrail preset applied inline; no subagent overhead |
| MEDIUM | `auditor` reviews the plan before execution starts |
| HIGH | `auditor` runs as an independent subagent with VETO authority; regulated domains also require `subject-matter-expert` |
| CRITICAL | Same as HIGH; multiple regulated/PII signals auto-escalate here |

The classifier uses an AI model when `ANTHROPIC_API_KEY` is set, and falls back
to deterministic keyword matching otherwise. `baton classify --activate "<task>"`
writes `.claude/active-policy.json` so the `policy-check` hook knows which preset
governs the current session.

### What the auditor actually does

The `auditor` agent is independent from the orchestrator by design. Its three
operating modes:

1. **Pre-execution plan review** — scope boundaries, write overlaps, data safety,
   regulatory requirements, rollback paths. Returns a guardrails report with
   per-agent permission manifests the orchestrator enforces.
2. **Mid-execution checkpoints** — CONTINUE / PAUSE / HALT verdict at defined step
   boundaries. A HALT stops the next dependent step from dispatching.
3. **Post-execution audit** — diff review, compliance scan, security scan, domain
   validation. Returns a machine-readable verdict (`APPROVE`,
   `APPROVE_WITH_CONCERNS`, `REQUEST_CHANGES`, `VETO`). A VETO halts HIGH/CRITICAL
   phase advancement; overriding requires `--force --justification`, which is
   logged to the audit chain.

The auditor checks whether the work is **functionally correct** — business rules
enforced, edge cases handled, domain logic valid — not just whether it lints.
That is what the `subject-matter-expert` enables: it supplies the domain context
(regulatory requirements, data models, validation rules) the auditor and
implementers need to be right.

### Policy hooks: tool-call-level enforcement

Two Claude Code hooks run for every tool call during execution:

- **`baton policy-check`** (PreToolUse) — evaluates the tool call against the
  active guardrail preset and denies when a blocking rule fires (`path_block`,
  `tool_restrict`, `require_agent`, `require_gate`). Fail-open by default; set
  `BATON_POLICY_FAIL_CLOSED=1` to make errors deny.
- **`baton comply-record`** (PostToolUse/Stop) — appends a hash-chained entry to
  `compliance-audit.jsonl` after each tool use. `BATON_COMPLIANCE_FAIL_CLOSED=1`
  makes write failures halt execution rather than log-and-continue — required for
  regulated work where losing an audit entry is itself a defect.

Five guardrail presets ship (`standard_dev`, `data_analysis`, `infrastructure`,
`regulated`, `security`); custom presets live under `.claude/policies/`.

### Assurance packs

Organisations author domain-specific governance units under
`.claude/packs/<name>/`: a policy JSON, classification signals, a review rubric,
gate definitions, and evidence requirements. Loading a pack merges its signals
into the classifier and registers its policy set so `policy-check` resolves it
automatically.

```bash
baton packs init <name>       # Scaffold a new pack
baton packs validate <name>   # Validate structure and required fields
baton packs list              # List loaded packs
```

### Verifiable evidence bundles

After each task, `baton evidence bundle <task_id>` assembles a self-contained
artifact under `evidence/<task_id>/`: a SHA-256 manifest of every artifact, an AI
Bill of Materials, the task-scoped compliance segment, gate results, auditor and
reviewer verdicts, approvals, and an active-pack snapshot.

```bash
baton evidence bundle <task_id>   # Build bundle (optionally --tar, --sign)
baton evidence verify <path>      # Verify SHA-256 integrity; CI-runnable, offline
```

### Segregation-of-duties approval

`BATON_APPROVAL_MODE=team` requires the approving actor to differ from whoever
requested the approval — self-approval is blocked. The request records the
requester identity; the result records the actor.

### Spec federation: the cheapest control point

Before any agent fires on externally-sourced work, a spec can be imported from
GitHub Issues or Azure DevOps, auto-enriched with a risk classification and cost
forecast (pack-aware when packs are loaded), and routed for senior review —
approve or bounce — before it ever fires into plan generation. Catching
HIGH/CRITICAL risk at the spec-review stage costs one API call instead of a full
execution cycle.

```bash
baton spec import   # Import from GitHub Issues / Azure DevOps
baton spec list
baton spec approve  # Blocked on self-approval in team mode
baton spec export
```

---

## Supporting Capabilities

Below the four pillars, Baton ships a deep toolkit for memory, observability,
improvement, and integration.

### Memory & knowledge

**Structured agent memory (Beads).** Agents emit typed bead signals
(`BEAD_DISCOVERY`, `BEAD_DECISION`, `BEAD_WARNING`, `BEAD_OUTCOME`,
`BEAD_PLANNING`) during execution. Beads are stored by the external `bd` tool (a
mandatory runtime dependency the installer auto-installs) in a per-project
`.beads/` workspace and form a typed dependency graph with status tracking and
tag-based retrieval.

```bash
baton beads list --type decision --status open
baton beads ready                          # Unblocked open beads
baton beads graph TASK_ID                  # Dependency graph
baton beads promote bd-a1b2 --pack my-pack # Promote to knowledge doc
baton beads cleanup --ttl 168 --dry-run    # Memory decay preview
```

**Knowledge delivery.** Curated knowledge packs resolved at plan time and
injected into each agent's prompt. Attach with `--knowledge` (files) or
`--knowledge-pack` (named packs); three packs ship (`agent-baton`,
`ai-orchestration`, `case-studies`). Lifecycle is managed with
`baton knowledge stale|deprecate|retire|sweep`.

### Observability & cross-project intelligence

```bash
baton usage                          # Token usage statistics
baton dashboard [--write]            # Usage dashboard
baton trace                          # Execution traces
baton retro                          # Task retrospectives
baton query agent-reliability        # Predefined views over this project's baton.db
baton query --sql "SELECT ..."       # Ad-hoc SQL
```

Execution data syncs to `~/.baton/central.db` for cross-project queries:

```bash
baton sync [--all]                   # Sync to central.db
baton cquery agents                  # Agent reliability across projects
baton cquery costs                   # Token costs by task type
baton cquery failures                # Project failure rates
```

### Learning & improvement

These commands surface **data-driven recommendations for human review** — they
record patterns and propose changes; a human decides what to apply.

```bash
baton patterns               # Recurring orchestration patterns with success rates
baton budget                 # Budget tier recommendations from historical cost data
baton evolve                 # Proposed prompt improvements backed by performance data
baton anomalies [--watch]    # Statistical deviations in agent behavior
baton scores [--agent/--trends/--teams]   # Agent performance scorecards
```

**Learning automation.** Operational issues (routing mismatches, agent
degradations, knowledge gaps, gate errors) are tracked in a SQLite-backed ledger.
Issues that recur above a threshold become auto-apply *candidates*; a human
reviews them via `baton learn apply` or `baton learn interview` before they take
effect. `baton learn reset` rolls back any applied fix.

### REST API & PMO UI

A FastAPI server exposes the full engine over HTTP (`pip install
agent-baton[pmo]`).

```bash
baton serve --port 8741       # API only
baton pmo serve --port 8741   # API + PMO UI
```

Ten route groups (`/api/v1/health`, `/plans`, `/executions`, `/agents`,
`/observe`, `/decisions`, `/events` (SSE), `/webhooks`, `/pmo`, `/learn`) with
Bearer-token auth, CORS, and HMAC-SHA256 webhook signing (Slack Block Kit
payloads). The React/Vite PMO UI provides the Kanban board, Smart Forge with SSE
progress streaming, plan editor, execution controls, changelist review, and
role-based approval.

### Extensibility

```bash
baton source add ado --name "My ADO" --org myorg --project myproj --pat-env ADO_PAT
baton source list|sync|remove|map     # External work-item sources
baton release create|notes|readiness  # Tag plans against delivery targets
baton webhook add|list|remove         # Outbound webhook subscriptions
```

The Azure DevOps work-item adapter is fully implemented; GitHub, Jira, and Linear
can be added by implementing the `ExternalSourceAdapter` protocol.

### Experimental (feature-flagged)

These surfaces emit stub warnings to stderr on invocation and are **not
production-ready**. Do not rely on them.

| Feature | Flag | Status |
|---------|------|--------|
| Immune-system daemon | `BATON_IMMUNE_ENABLED=1` | Wave 6.2 Part B stub |
| Predictive watcher | feature flag in `core/intel/` | Wave 6.2 Part C stub |
| Executable beads | `BATON_EXEC_BEADS_ENABLED=1` | Process-level sandbox only; unsafe for external-origin beads |
| Persistent agent souls | `BATON_SOULS_ENABLED=1` | Cross-project cryptographic agent identities |

---

## CLI Reference

The `baton` CLI provides 60+ commands organized into groups.

<details>
<summary><strong>Core Workflow</strong> — plan, execute, recover</summary>

| Command | Description |
|---------|-------------|
| `baton plan "<task>"` | Generate a data-driven execution plan |
| `baton plan --dry-run` | Preview plan + cost/token forecast (±50% range) without saving |
| `baton plan --from-template NAME` | Instantiate a saved plan template with a new task |
| `baton goal "<condition>"` | Plan against a completion condition; engine drives amend cycles until met |
| `baton execute start` | Start execution from a saved plan |
| `baton execute next [--all]` | Get next action(s) to perform |
| `baton execute record` | Record a step completion |
| `baton execute dispatched` | Mark a step as in-flight |
| `baton execute gate` | Record a QA gate result |
| `baton execute approve` | Record a human approval decision |
| `baton execute amend` | Add phases or steps mid-execution |
| `baton execute team-record` | Record team member completions |
| `baton execute run` | Autonomous execution loop (headless) |
| `baton execute complete` | Finalize execution |
| `baton execute status` | Show current execution state |
| `baton execute resume` | Resume after crash or interruption |
| `baton execute list` / `switch` | List / switch active executions |
| `baton status` | Show team-context file status |

</details>

<details>
<summary><strong>Execution (Advanced)</strong> — daemon, async, decisions</summary>

| Command | Description |
|---------|-------------|
| `baton daemon start/stop/status/list` | Background execution management |
| `baton daemon immune start/stop/status` | Immune-system daemon (EXPERIMENTAL — `BATON_IMMUNE_ENABLED=1`) |
| `baton async --dispatch/--pending/--show` | Dispatch and track asynchronous tasks |
| `baton decide --list/--show/--resolve` | Manage human decision requests |

</details>

<details>
<summary><strong>Observability</strong> — traces, dashboards, usage, queries</summary>

| Command | Description |
|---------|-------------|
| `baton usage` | Token usage statistics |
| `baton dashboard [--write]` | Generate usage dashboard |
| `baton trace` | Execution traces |
| `baton retro` | Task retrospectives |
| `baton telemetry` | Agent telemetry events |
| `baton context current/briefing/gaps` | Situational awareness for agents |
| `baton query <subcommand>` | Predefined and ad-hoc SQL against this project's `baton.db` |
| `baton cleanup` | Archive old execution artifacts |

</details>

<details>
<summary><strong>Governance</strong> — risk, compliance, evidence, validation</summary>

| Command | Description |
|---------|-------------|
| `baton classify` | Classify task sensitivity |
| `baton compliance` | Show compliance reports |
| `baton policy` | List or evaluate guardrail presets |
| `baton escalations` | Show or resolve agent escalations |
| `baton validate` | Validate agent definitions |
| `baton spec-check` | Validate agent output against a spec |
| `baton detect` | Detect project stack |
| `baton evidence bundle/verify` | Build and verify evidence bundles |
| `baton packs init/validate/list` | Assurance pack management |
| `baton aibom` | Generate a per-task/per-PR AI Bill of Materials |

</details>

<details>
<summary><strong>Improvement</strong> — learning, evolution, tuning</summary>

| Command | Description |
|---------|-------------|
| `baton scores [--agent/--trends/--teams]` | Agent performance scorecards |
| `baton evolve` | Propose prompt improvements |
| `baton patterns` | Learned orchestration patterns |
| `baton budget` | Budget tier recommendations |
| `baton anomalies [--watch]` | Detect statistical anomalies in agent behavior |
| `baton lookback [TASK_ID]` | Historical failure analysis |
| `baton experiment list/show/conclude/rollback` | Manage improvement experiments |
| `baton learn improve --run/--force/--report` | Run the full improvement loop |
| `baton learn status/issues/analyze/apply/interview/history/reset` | Learning-issue lifecycle |

</details>

<details>
<summary><strong>Memory (Beads) and Knowledge</strong></summary>

| Command | Description |
|---------|-------------|
| `baton beads create` | Create a bead manually |
| `baton beads list` | List beads with filters (`--type`, `--status`, `--task`, `--tag`) |
| `baton beads show/ready/close` | Inspect, surface, and close beads |
| `baton beads link <src> --relates-to\|--contradicts\|--extends\|--blocks\|--validates <tgt>` | Link two beads |
| `baton beads cleanup` | Archive old closed beads (memory decay) |
| `baton beads promote <id> --pack NAME` | Promote a bead to a knowledge document |
| `baton beads graph/synthesize/clusters` | Dependency graph and edge/cluster inference |
| `baton knowledge stale/deprecate/retire/sweep` | Knowledge lifecycle |
| `baton knowledge ab/ranking/effectiveness/harvest` | Knowledge A/B testing and effectiveness |

</details>

<details>
<summary><strong>Distribution, Storage, and Sync</strong></summary>

| Command | Description |
|---------|-------------|
| `baton install` / `uninstall` | Install / remove agent-baton files |
| `baton package` / `publish` / `pull` / `transfer` | Package archives and registry |
| `baton sync [--all]` | Sync to `~/.baton/central.db` |
| `baton sync status` | Show sync watermarks |
| `baton sync --verify ARCHIVE` | Verify a package archive |
| `baton cquery` | Cross-project SQL queries against `central.db` |
| `baton source add/list/sync/remove/map` | External source connections (ADO, GitHub, Jira) |

</details>

<details>
<summary><strong>Portfolio, Specs, and Releases</strong></summary>

| Command | Description |
|---------|-------------|
| `baton serve` | Start the HTTP API server (API only) |
| `baton pmo serve/status/add/health` | PMO server, board summary, project registration |
| `baton spec create/list/show/approve/link/import/export` | Manage first-class Spec entities |
| `baton release create/list/show/tag/notes/readiness` | Manage delivery-target releases |
| `baton webhook add/list/remove` | Outbound webhook subscriptions |
| `baton souls mint/list/show/retire/revoke/list-revocations/rotate` | Persistent agent souls (`BATON_SOULS_ENABLED=1`) |

</details>

<details>
<summary><strong>Deprecated aliases (still work, removal in a future release)</strong></summary>

These top-level commands still execute but print a deprecation warning to stderr
on every invocation. Update scripts to use the new paths.

| Old command | New canonical path | Bead |
|-------------|-------------------|------|
| `baton migrate-storage` | `baton sync --migrate-storage` | bd-8eef |
| `baton verify-package ARCHIVE` | `baton sync --verify ARCHIVE` | bd-7eec |
| `baton improve` | `baton learn improve` | bd-5049 |

</details>

---

## Project Structure

```
agents/            <- 30 agent definitions (Markdown + YAML frontmatter)
references/        <- 19 reference procedures (shared knowledge)
templates/         <- CLAUDE.md, settings.json, skills, packs, and playbooks
scripts/           <- Install scripts (Linux/macOS + Windows) and maintenance
docs/              <- Architecture docs, ADRs, invariants, CLI/API reference,
                      troubleshooting, and internal maintainer docs
agent_baton/       <- Python package
  models/          <- Pydantic data models (38 modules)
  core/            <- Business logic (22 sub-packages)
    engine/        <- Planner, executor, dispatcher, gates, persistence,
    |                 knowledge resolver, bead store, worktree manager
    orchestration/ <- Agent registry, router, context manager, knowledge registry
    pmo/           <- PMO store, scanner, Smart Forge
    storage/       <- Central DB, federated sync, external adapters
    govern/        <- Classification, compliance, policy, evidence, assurance packs
    observe/       <- Retrospectives, context profiler, cost forecaster, dashboard
    improve/       <- Scoring, conflict detection, proposals, rollback
    learn/         <- Pattern learner, budget tuner, learning automation, ledger
    intel/         <- Bead synthesizer, debate, knowledge ranker
    distribute/    <- Packaging, sharing, registry
    events/        <- Event bus, domain events, projections
    runtime/       <- Async worker, supervisor, headless Claude, decisions, daemon
    federate/      <- Spec draft store, spec enrichment, importers
    knowledge/     <- Knowledge lifecycle, AB testing, ADR harvester
    release/       <- Conflict predictor, release readiness, git notes
    immune/, exec/ <- Immune-system daemon, executable beads (EXPERIMENTAL)
  api/             <- FastAPI REST API (route modules, webhooks, middleware)
  cli/             <- CLI interface (60+ commands)
tests/             <- Extensive pytest suite (355 test files)
pmo-ui/            <- React/Vite PMO frontend
```

---

## Configuration

### Environment Variables

The variables a user is most likely to set. For the full internal list see
[CLAUDE.md](CLAUDE.md).

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Target a specific execution in multi-task scenarios | auto-detected |
| `BATON_DB_PATH` | Override the project `baton.db` location | discovered |
| `BATON_API_TOKEN` | Bearer token for API authentication | none |
| `BATON_APPROVAL_MODE` | `local` (self-approve) or `team` (different reviewer required) | `local` |
| `BATON_GATE_RETRY` | Re-dispatch a failing step once with gate output appended; second failure is terminal | `0` |
| `BATON_RUN_TOKEN_CEILING` | Per-run cumulative spend cap (USD). Survives `baton execute resume`. | unset |
| `BATON_WORKTREE_STALE_HOURS` | Max age before the worktree GC reclaims a stale worktree | `4` |
| `BATON_PLANNER_HARD_GATE` | Block structurally defective plans (deterministic checks) | unset |
| `BATON_PLAN_REVIEW` | Optional LLM plan-quality review: `off` \| `haiku` \| `sonnet` \| `opus` | `off` |
| `BATON_POLICY_FAIL_CLOSED` | `policy-check` hook: `0` fail-open, `1` blocks the tool call | `0` |
| `BATON_COMPLIANCE_FAIL_CLOSED` | Halt execution on compliance audit write failure | `0` |
| `BATON_TEAMS_BACKEND` | Team-step backend: `worktree` (default, resumable) or `claude-teams` | `worktree` |
| `BATON_SOULS_ENABLED` / `BATON_EXEC_BEADS_ENABLED` | Experimental feature flags (souls / executable beads) | unset |
| `ANTHROPIC_API_KEY` | AI risk classification (`agent-baton[classify]`) and the planner classifier | none |

### Plan Command Flags

| Flag | Description |
|------|-------------|
| `--save` | Write `plan.json` and `plan.md` to `.claude/team-context/` |
| `--explain` | Show reasoning behind plan decisions |
| `--dry-run` | Preview plan + token/cost forecast without saving |
| `--task-type TYPE` | Override task type (new-feature, bug-fix, refactor, …) |
| `--agents NAMES` | Override auto-selected agents (comma-separated) |
| `--knowledge PATH` / `--knowledge-pack NAME` | Attach knowledge documents / packs (repeatable) |
| `--model MODEL` | Default model for dispatched agents (haiku, sonnet, opus) |
| `--complexity LEVEL` | Override complexity: light, medium, heavy |

### Files Installed to Target Projects

| File | Purpose |
|------|---------|
| `.claude/agents/*.md` | Agent definitions (30 files) |
| `.claude/references/*.md` | Reference procedures (19 files) |
| `.claude/CLAUDE.md` | Project development guide (from template) |
| `.claude/settings.json` | Hook configuration (write-protect, policy-check, compliance logging) |
| `.claude/skills/` | Reusable skills |

---

## For Developers

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
pip install -e ".[dev]"        # Core + test dependencies
pip install -e ".[dev,api]"    # Add REST API dependencies
pytest                         # Run the test suite
```

Requires Python 3.10+. Runtime dependencies: `pyyaml`, `pydantic`, `cryptography`.

### Optional Dependencies

| Extra | Packages | Purpose |
|-------|----------|---------|
| `dev` | pytest, pytest-cov | Test suite |
| `pmo` | fastapi, uvicorn, httpx, sse-starlette, pydantic | REST API and PMO server |
| `api` | same as `pmo` | Backward-compatible alias |
| `daemon` | uvicorn | Background daemon runner |
| `classify` | anthropic | AI-powered risk classification |
| `viz` | rich | Terminal dashboards and rich output |
| `all` | pmo + classify + viz | Everything except dev tools |

### Key Documentation

| Document | Contents |
|----------|----------|
| [CLAUDE.md](CLAUDE.md) | Development guide, conventions, cross-cutting rules |
| [docs/architecture.md](docs/architecture.md) | Package layout and dependency graph |
| [docs/design-decisions.md](docs/design-decisions.md) | ADR log |
| [docs/invariants.md](docs/invariants.md) | Interface boundaries and CLI output contract |
| [docs/cli-reference.md](docs/cli-reference.md) | Full CLI reference |
| [docs/api-reference.md](docs/api-reference.md) | REST API reference |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common issues and solutions |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |

---

## Project Status

Agent Baton is in active development (v0.1.0). The orchestration engine, all 30
agents, 19 reference procedures, knowledge delivery, bead memory system, PMO
subsystem with end-to-end plan-to-merge workflow, REST API with webhooks,
federated sync, event system, learning automation, and the improvement pipeline
are implemented and tested.

- **Python**: 3.10+ (tested on 3.10–3.13)
- **Runtime dependencies**: pyyaml, pydantic, cryptography
- **Optional**: FastAPI + uvicorn (REST API), Anthropic SDK (AI classification), rich (dashboards)
- **Test suite**: extensive pytest suite (355 test files)
- **External adapters**: Azure DevOps fully implemented; Jira, GitHub Issues, and Linear protocols defined

### Experimental Features

The following surfaces are explicitly EXPERIMENTAL and gated behind feature flags.
Stub implementations emit warnings to stderr; do not rely on them for production
work.

| Feature | Flag | Status |
|---------|------|--------|
| Immune-system daemon | `BATON_IMMUNE_ENABLED=1` | Wave 6.2 Part B stub |
| Predictive watcher | feature flag in `core/intel/` | Wave 6.2 Part C stub |
| Executable beads | `BATON_EXEC_BEADS_ENABLED=1` | Process-level sandbox only; not safe for external-origin beads |
| Persistent agent souls | `BATON_SOULS_ENABLED=1` | Cross-project cryptographic identities; reviewer signatures flow into evidence bundles |

---

## Tips

- **Say "use the orchestrator"** explicitly for your first few runs so Claude
  Code routes to the right agent.
- **3–5 specialists per task.** More than that and coordination overhead
  outweighs the benefits.
- **Crash recovery is automatic.** Session dies mid-task? New session +
  `baton execute resume`.
- **Run tasks in parallel.** Each `baton execute start` prints
  `export BATON_TASK_ID=...`. Set it in each terminal before driving the loop.
- **Use `baton query` for local data, `baton cquery` for cross-project.** They
  target different databases (`baton.db` vs `~/.baton/central.db`).
- **Policy hooks run on every tool call.** The installed `settings.json` wires
  `baton policy-check` into PreToolUse automatically — no manual setup needed.

---

## License

Proprietary — All Rights Reserved. Contact the maintainers for terms.
