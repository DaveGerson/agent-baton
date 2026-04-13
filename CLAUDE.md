# Agent Baton — Development Guide

This repo contains the source for Agent Baton, a multi-agent orchestration
system for Claude Code.

## Repository Structure

```
agent_baton/       ← Python package (orchestration engine)
  models/          ← Data models (23 modules, incl. pmo.py, knowledge.py, bead.py,
  |                  learning.py, decision.py, events.py, session.py, parallel.py)
  core/            ← Business logic (11 sub-packages, no shim files)
    engine/        ← Execution core: planner, executor, dispatcher, gates,
    │                persistence, protocols (ExecutionDriver),
    │                knowledge_resolver, knowledge_gap,
    │                bead_store, bead_signal
    orchestration/ ← Agent discovery: registry, router, context manager,
    │                knowledge_registry
    pmo/           ← PMO subsystem: store, scanner, forge
    storage/       ← Central DB: sync.py (SyncEngine), central.py (CentralStore),
    │                adapters/ (ExternalSourceAdapter protocol, AdoAdapter)
    govern/        ← Policy enforcement, compliance, validation
    observe/       ← Tracing, usage, dashboard, retrospective, telemetry
    improve/       ← Scoring, evolution, VCS
    learn/         ← Pattern learner, budget tuner, learning engine,
    │                ledger, overrides, resolvers, interviewer, recommender
    distribute/    ← Packaging, sharing, registry client
    │  experimental/ ← Incident, async dispatch, transfer (not production)
    events/        ← Event bus, domain events, persistence, projections
    runtime/       ← Async worker, supervisor, launcher, headless Claude,
                     decisions, ExecutionContext factory
  cli/             ← CLI interface (50 commands via `baton`)
    commands/
      execution/   ← execute, plan, status, daemon, async, decide
      observe/     ← dashboard, trace, usage, telemetry, context_profile, retro
      govern/      ← classify, compliance, policy, escalations, validate, spec_check, detect
      improve/     ← scores, evolve, patterns, budget, changelog, learn,
      |              anomalies, experiment, improve
      distribute/  ← package, publish, pull, verify_package, install, transfer
      agents/      ← agents, route, events, incident
      pmo_cmd      ← pmo serve, pmo status, pmo add, pmo health
      sync_cmd     ← baton sync, baton sync --all, baton sync status
      query_cmd    ← baton query (cross-project SQL against central.db)
      source_cmd   ← baton source add/list/sync/remove/map (external adapters)
      bead_cmd     ← baton beads list/show/ready/close/link (structured memory)
docs/              ← Architecture documentation (architecture.md, design-decisions.md, invariants.md)
agents/            ← Distributable agent definitions (19 .md files)
references/        ← Distributable reference docs (15 .md files)
templates/         ← CLAUDE.md + settings.json installed to target projects
scripts/           ← Install scripts (Linux + Windows)
tests/             ← Test suite (~5010 tests, pytest)
pmo-ui/            ← React/Vite PMO frontend (served at /pmo/)
.claude/           ← Project-specific orchestration setup:
  agents/          ← 19 packaged agents (mirrored from agents/) +
  |                  5 meta agents for baton development +
  |                  18 GSD framework agents
  references/      ← Symlink → ../references/ (canonical source)
  knowledge/       ← Knowledge packs (3 packs, 10 docs)
  settings.json    ← Hooks for this project
```

## Key Rules

- `agents/` and `references/` are the **distributable** source of truth.
  Changes here affect all users who install agent-baton.
- `.claude/agents/` contains three tiers: (1) **packaged agents** mirrored
  from `agents/` so this project can dogfood them, (2) **meta agents** for
  developing agent-baton itself (these are NOT distributed), and (3) **GSD
  framework agents** for project management workflows.
- `.claude/references/` is a symlink to `references/` — edits to canonical
  references are immediately available to the project's orchestrator.
- The `agent_baton` Python package reads agent definitions at runtime.
- `core/engine/` is the execution engine — changes here affect the runtime
  behavior of all orchestrated tasks.
- All imports use canonical sub-package paths (e.g.,
  `from agent_baton.core.govern.classifier import DataClassifier`).
  There are no backward-compatibility shims.
- `cli/commands/execute.py` contains `_print_action()` — the output format
  Claude reads to drive orchestration. Treat it as a public API. See
  `docs/invariants.md` for the full contract.
- Before changing CLI command names, `_print_action()` output format, or
  execution state schema, read `docs/invariants.md` — these are the protocol
  contract between Claude and the engine.

## Agent Roster (for this project)

**Packaged agents** (19 — mirrored from `agents/`, also shipped to users):

| Agent | Role |
|-------|------|
| `orchestrator` | Coordinate multi-step development tasks |
| `backend-engineer` / `--python` / `--node` | Server-side implementation |
| `frontend-engineer` / `--react` / `--dotnet` | Client-side UI |
| `architect` | Design decisions, module boundaries |
| `test-engineer` | Write and organize pytest tests |
| `code-reviewer` | Quality review before commits |
| `auditor` | Safety review for guardrail/hook changes |
| `talent-builder` | Create new distributable agent definitions |
| `security-reviewer` | Security audit (OWASP, auth, secrets) |
| `devops-engineer` | Infrastructure, CI/CD, Docker |
| `data-engineer` / `data-analyst` / `data-scientist` | Data stack |
| `visualization-expert` | Charts, dashboards |
| `subject-matter-expert` | Domain-specific business operations |

**Meta agents** (5 — project-specific, for developing agent-baton):

| Agent | Role |
|-------|------|
| `ai-systems-architect` | Multi-agent orchestration design |
| `agent-definition-engineer` | Edit agent .md files, references, knowledge packs |
| `prompt-engineer` | Agent prompt optimization |
| `ai-product-strategist` | Product decisions, value/cost analysis |
| `spec-document-reviewer` | Review and validate specification documents |

## Development

```bash
pip install -e ".[dev]"    # Install in editable mode
pytest                     # Run tests (~5010 tests)
scripts/install.sh         # Re-install globally after editing agents/references
```

## Orchestrator Usage

This project dogfoods the agent-baton execution engine for its own
orchestration. For complex tasks involving 3+ files across different
layers, use the engine path:

```
baton plan "task description" --save --explain \
    [--knowledge path/to/doc.md] \       # attach explicit knowledge document(s)
    [--knowledge-pack pack-name] \       # attach explicit knowledge pack(s)
    [--intervention low|medium|high]     # escalation threshold (default: low)
# Review plan.md — present summary to user, adjust if needed
baton execute start

loop:
  baton execute next
  if DISPATCH:
    baton execute dispatched --step ID --agent NAME
    → spawn Agent tool with the delegation_prompt ←
    baton execute record --step ID --agent NAME --status complete \
      --outcome "summary" --files "changed.py"
  if GATE:
    run gate command
    baton execute gate --phase-id N --result pass
  if APPROVAL:
    → present context to user, get decision ←
    baton execute approve --phase-id N --result approve
  if COMPLETE:
    baton execute complete
    break
```

**Headless execution:** For autonomous execution without a Claude Code
session, use `baton execute run`. This drives the full loop (start → dispatch
→ gate → complete) by spawning `claude --print` subprocesses. The PMO UI
can also launch execution directly from the board.

**Depth limit:** The orchestrator MUST run at the top level of a conversation,
never as a dispatched subagent. It needs to spawn its own agents, and Claude
Code limits agent nesting to depth 1.

**For DISPATCH actions — you MUST use the Agent tool** to spawn the
specified subagent with the delegation prompt. Do NOT do the work inline.
Valid `--status` values: `complete` or `failed`.

**For APPROVAL actions** — present the approval context to the user and
record their decision with `baton execute approve`. Options: `approve`,
`reject`, `approve-with-feedback` (inserts remediation phase).

**Plan amendments** — use `baton execute amend` to add phases or steps
during execution. **Team steps** — use `baton execute team-record` to
record individual team member completions.

See `references/baton-engine.md` for the full CLI reference and
troubleshooting guide.

For simple single-file changes, work directly without the engine.

Changes to distributable files (`agents/`, `references/`) are MEDIUM risk
and should involve the auditor when substantial.

Changes to `core/engine/` affect the execution runtime and should have
corresponding test coverage.

## Cross-Layer Linkage Rules

Schema and model changes touch multiple layers that must stay in sync.
These linkage rules prevent the category of bugs where a change in one
layer silently breaks another.

### Schema Linkage (storage/)

When adding or removing a column in `schema.py`:

1. **Project-level schema** (`PROJECT_SCHEMA_DDL`) — the CREATE TABLE
   for `baton.db`.
2. **Central schema** (`CENTRAL_SCHEMA_DDL`) — the CREATE TABLE for
   `central.db`.  The central table mirrors the project table with an
   additional `project_id` prefix column.
3. **Migration script** (`MIGRATIONS` dict) — ALTER TABLE for existing
   project databases.  Central does not have its own migration dict;
   it uses `CREATE TABLE IF NOT EXISTS` on first access.  **Important:**
   migrations are applied to both project and central databases, so
   FK constraints referencing single-column PKs must be omitted from
   `MIGRATIONS` (central tables use composite PKs).  Fresh project DBs
   get FKs from `PROJECT_SCHEMA_DDL` directly.
4. **SQLite backend** (`sqlite_backend.py`) — INSERT and SELECT
   statements that reference the column.
5. **Sync engine** (`sync.py`) — uses `SELECT *` so column presence is
   sufficient, but verify the central DDL includes the column.

**Rule:** Every column in `PROJECT_SCHEMA_DDL` must also exist in
`CENTRAL_SCHEMA_DDL` (with the `project_id` prefix added to the key).
The sync engine copies all columns via `SELECT *`; a missing column in
the central schema causes a silent sync failure.

### Model ↔ Test Linkage

When the planner produces new step types (e.g. team steps with
member IDs like `"2.1.a"`):

1. **Integration tests** that drive the engine through
   `record_step_result` must use the `_record_dispatch` helper (in
   `test_engine_integration.py`) which routes team member IDs to
   `record_team_member_result`.  Direct calls to
   `engine.record_step_result` will fail for team member IDs.
2. **CLI handler tests** (e.g. `test_planner_governance.py`) that
   construct `argparse.Namespace` fixtures must include all attributes
   that `plan_cmd.handler` reads.  When a new CLI flag is added to the
   plan command, update the test namespace.

### Planner ↔ Gate Linkage

When adding new phase names to `_PHASE_NAMES` or `_STEP_TEMPLATES`
in `planner.py`, check `_default_gate()` to ensure non-code-producing
phases (design, investigate, research, review) are excluded from
automated test gates.

## Documentation Maintenance

When completing work that changes the architecture, public API, CLI
commands, or data models, update the relevant documentation:

- **`docs/architecture.md`** — Package layout, dependency graph, key
  contracts. Update when adding/removing/moving modules or changing
  the interaction chain.
- **`docs/design-decisions.md`** — ADR log. Add an entry when making a
  non-obvious architectural decision.
- **`docs/invariants.md`** — Critical interface boundaries. Update when
  changing CLI command names, `_print_action()` output format, or
  `execution-state.json` schema.
- **`README.md`** — User-facing overview. Update when adding agents,
  references, or CLI commands.
- **This file (`CLAUDE.md`)** — Developer guide. Update when the repo
  structure, test count, or development workflow changes.
