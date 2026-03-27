# Agent Baton

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

### 2. Install the Python engine

```bash
pip install -e ".[dev]"     # Core engine + CLI
```

### 3. Verify

```bash
# In Claude Code
/agents                     # Should list ~19 agents

# In terminal
baton agents                # List agents from Python registry
baton detect                # Detect your project's stack
```

### 4. Run your first task

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
  assigns budget tiers, sequences phases with dependency awareness
- **Execution loop** -- DISPATCH / GATE / APPROVAL / COMPLETE with full
  state persistence and crash recovery
- **Concurrent execution** -- run multiple plans in parallel, each bound
  by `BATON_TASK_ID`
- **Plan amendments** -- add phases or steps mid-execution
- **Team steps** -- dispatch multiple agents to a single step

### Risk-Tiered Safety

Every task is classified by risk level:

- **LOW** -- guardrail presets applied inline, no subagent overhead
- **MEDIUM** -- auditor reviews the plan before execution
- **HIGH** -- auditor runs as independent subagent with veto authority;
  regulated domains require subject-matter-expert involvement

### 15 Reference Procedures

Shared knowledge documents encoding planning strategy, guardrail rules,
communication protocols, failure handling, and cost models. Agents get the
knowledge they need without duplicating it across context windows.

### Knowledge Delivery

Curated knowledge packs resolved at plan time and injected into each
agent's delegation prompt. A feedback loop learns which agents need what
knowledge for which task types.

### Smart Forge

AI-driven task planning via headless Claude Code subprocess. Generates
real LLM-quality plans (not rule-based templates). Includes interactive
interview-based refinement and integrates with the PMO UI.

### Headless Execution

Run the full loop without a Claude Code session:

```bash
baton execute run --plan .claude/team-context/plan.json
```

Dispatches agents via `claude --print`, runs gates as subprocesses, loops
until complete. The PMO UI can also launch executions from its Kanban board.

### Cross-Project Intelligence

Execution data syncs to `~/.baton/central.db`. Query agent reliability,
token costs, and failure rates across projects:

```bash
baton sync --all
baton cquery agents          # Agent reliability across projects
baton cquery costs           # Token costs by task type
```

### Pattern Learning

The engine learns from past executions -- identifies recurring agent
combinations, recommends budget adjustments, proposes prompt improvements,
and surfaces anomalies.

### REST API and PMO UI

A FastAPI server exposes the full engine over HTTP. A React/Vite PMO
frontend provides Kanban boards, the Forge plan builder, one-click
execution, and program health dashboards.

### External Source Adapters

Connect Azure DevOps, Jira, GitHub, or Linear as work-item sources.
Items sync to `central.db` and link to baton plans.

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
                    |  Reads 15 reference procedures inline               |
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

The `baton` CLI provides 45+ commands organized into seven groups:

<details>
<summary><strong>Execution</strong> -- plan, execute, recover</summary>

| Command | Description |
|---------|-------------|
| `baton plan` | Create a data-driven execution plan |
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
| `baton daemon start/stop` | Background execution management |
| `baton async` | Dispatch and track asynchronous tasks |
| `baton decide` | Manage human decision requests |

</details>

<details>
<summary><strong>Observability</strong> -- traces, dashboards, usage</summary>

| Command | Description |
|---------|-------------|
| `baton usage` | Token usage statistics |
| `baton dashboard [--write]` | Generate usage dashboard |
| `baton trace` | Execution traces |
| `baton retro` | Task retrospectives |
| `baton telemetry` | Agent telemetry events |
| `baton context-profile` | Context efficiency profiles |
| `baton context` | Situational awareness |
| `baton query` | SQL queries against baton.db |
| `baton cleanup` | Archive old execution artifacts |
| `baton migrate-storage` | Migrate JSON flat files to SQLite |

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
| `baton scores` | Agent performance scorecards |
| `baton evolve` | Propose prompt improvements |
| `baton patterns` | Learned orchestration patterns |
| `baton budget` | Budget tier recommendations |
| `baton changelog` | Agent changelog and backup management |
| `baton anomalies` | Detect system anomalies |
| `baton experiment` | Manage improvement experiments |
| `baton improve` | Run the full improvement loop |

</details>

<details>
<summary><strong>Distribution</strong> -- packaging, sharing</summary>

| Command | Description |
|---------|-------------|
| `baton package` | Create or install package archives |
| `baton publish` | Publish to a local registry |
| `baton pull` | Pull from a registry |
| `baton verify-package` | Verify a package archive |
| `baton install` | Install agents and references |
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
<summary><strong>Cross-Project</strong> -- sync, PMO, API</summary>

| Command | Description |
|---------|-------------|
| `baton sync [--all]` | Sync to `~/.baton/central.db` |
| `baton cquery` | Cross-project SQL queries |
| `baton source add/list/sync/remove/map` | External source connections |
| `baton pmo serve/status/add/health` | Portfolio management |
| `baton serve` | Start the HTTP API server |

</details>

---

## Project Structure

```
agents/            <- 19 agent definitions (markdown + YAML frontmatter)
references/        <- 15 reference procedures (shared knowledge)
templates/         <- CLAUDE.md + settings.json for target projects
scripts/           <- Install scripts (Linux + Windows)
docs/              <- Architecture docs, ADRs, invariants
agent_baton/       <- Python package
  models/          <- Data models (18 modules)
  core/            <- Business logic (10 sub-packages)
    engine/        <- Planner, executor, dispatcher, gates, persistence
    orchestration/ <- Agent registry, router, context manager
    pmo/           <- PMO store, scanner, Smart Forge
    storage/       <- Central DB, federated sync, external adapters
    govern/        <- Classification, compliance, policy
    observe/       <- Tracing, usage, dashboard, telemetry
    improve/       <- Scoring, evolution, experiments
    learn/         <- Pattern learner, budget tuner
    distribute/    <- Packaging, sharing, registry
    events/        <- Event bus, domain events, projections
    runtime/       <- Async worker, supervisor, headless Claude
  api/             <- FastAPI REST API server
  cli/             <- CLI interface (45+ commands)
tests/             <- Test suite (~3900 tests, pytest)
pmo-ui/            <- React/Vite PMO frontend
```

---

## For Developers

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
pip install -e ".[dev]"        # Core + test deps
pip install -e ".[dev,api]"    # Everything including REST API
pytest                         # ~3900 tests
```

Requires Python 3.10+. The only runtime dependency is PyYAML.

### Key Documentation

| Document | Contents |
|----------|----------|
| [CLAUDE.md](CLAUDE.md) | Development guide and conventions |
| [QUICKSTART.md](QUICKSTART.md) | Getting started for new users |
| [docs/architecture.md](docs/architecture.md) | Package layout and dependency graph |
| [docs/design-decisions.md](docs/design-decisions.md) | ADR log (12 decisions documented) |
| [docs/invariants.md](docs/invariants.md) | Interface boundaries (CLI output contract) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |

---

## Project Status

Agent Baton is in active development (v0.1.0). The orchestration engine,
all 19 agents, 15 references, knowledge delivery, PMO subsystem, REST API,
federated sync, event system, and the improvement pipeline are implemented
and tested.

- **Python**: 3.10+
- **Runtime dependency**: PyYAML only
- **Optional**: FastAPI + uvicorn (REST API), Anthropic SDK (AI classification)
- **Test suite**: ~3900 tests (pytest)
- **External adapters**: Azure DevOps implemented; Jira, GitHub, Linear
  protocols defined

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

---

## License

License pending. Contact the maintainers for terms.
