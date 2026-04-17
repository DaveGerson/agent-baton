# Persona Journey Validation: Priya & Tomoko

Codebase audit performed against `feat/actiontype-interact` branch.
Every rating is substantiated with file paths and code evidence.

---

## Priya's Journey (Platform/DevOps Engineer)

Priya deploys and operates agent-baton infrastructure for teams.
She evaluates tooling against production-readiness criteria before
recommending it to her organization.

### Initial Evaluation

#### 1. Container Readiness

**BLOCKED** -- No `Dockerfile` or `docker-compose.yml` exists anywhere
in the repository. The project installs via `pip install -e ".[dev]"`
(`pyproject.toml`). It _can_ run headless (`baton daemon start
--foreground`) and has a proper entry point (`baton =
"agent_baton.cli.main:main"`), so containerization is
_straightforward_ but not provided. Priya would need to write her own
Dockerfile.

- Evidence: `Glob("**/Dockerfile*")` and `Glob("**/docker-compose*")` return no results.
- The `baton daemon start` command supports `--foreground` mode (no terminal needed): `agent_baton/cli/commands/execution/daemon.py:46`.
- Headless execution via `baton execute run` spawns `claude --print` subprocesses: `agent_baton/core/runtime/headless.py`.

#### 2. Health Endpoint

**WORKS** -- A proper liveness probe exists at `GET /api/v1/health` and
a readiness probe at `GET /api/v1/ready`. Both are exempt from bearer
token authentication. The health endpoint returns status, version, and
uptime. The ready endpoint checks for an active execution state and
pending decision count.

- `agent_baton/api/routes/health.py:30-59` -- `HealthResponse(status, version, uptime_seconds)` and `ReadyResponse(ready, daemon_running, pending_decisions)`.
- Auth exemption at `agent_baton/api/middleware/auth.py:26-34` -- `/api/v1/health` and `/api/v1/ready` are in `_AUTH_EXEMPT_PATHS`.

#### 3. Secrets via Environment Variables

**WORKS** -- API token reads from `BATON_API_TOKEN` env var with CLI
flag override. External service tokens (ADO, GitHub, Jira, Linear) use
env var references (`ADO_PAT`, `GITHUB_TOKEN`, `JIRA_API_TOKEN`,
`LINEAR_API_KEY`). The Haiku classifier reads `ANTHROPIC_API_KEY` from
environment. No secrets are stored in config files.

- `agent_baton/cli/commands/serve.py:72` -- `token = args.token or os.environ.get("BATON_API_TOKEN")`.
- `agent_baton/core/engine/classifier.py:397` -- `api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()`.
- `agent_baton/cli/commands/source_cmd.py:348-367` -- adapter configs reference `pat_env_var` for PAT resolution.
- `agent_baton/core/improve/triggers.py:86` -- `BATON_MIN_TASKS` and `BATON_ANALYSIS_INTERVAL` env vars.

#### 4. OpenAPI Spec

**WORKS** -- FastAPI auto-generates OpenAPI JSON at `/openapi.json`,
interactive docs at `/docs` (Swagger UI), and `/redoc`. All three are
exempted from auth. The spec includes all 10 route modules (health,
plans, executions, agents, observe, decisions, events, webhooks, pmo,
learn).

- `agent_baton/api/server.py:130-132` -- `docs_url="/docs"`, `redoc_url="/redoc"`.
- `agent_baton/api/middleware/auth.py:29-33` -- `/openapi.json`, `/docs`, `/redoc` in exempt paths.

#### 5. Resource Requirements

**PARTIAL** -- SQLite is the storage backend. Per-project `baton.db`
stores all execution data. Telemetry log (`telemetry.jsonl`) is
rotated via `DataArchiver` (keeps last 10,000 lines). Daemon log
uses `RotatingFileHandler` at 10 MB with 3 backups. However, there is
no documentation of memory usage profiles, no resource limit guidance,
and no database size estimation tooling. SQLite files grow unbounded
outside of the JSONL rotation.

- `agent_baton/core/observe/telemetry.py:76` -- default log at `.claude/team-context/telemetry.jsonl`.
- `agent_baton/core/observe/archiver.py:14` -- JSONL rotation keeps last 10,000 lines.
- `agent_baton/core/runtime/supervisor.py:388-398` -- `RotatingFileHandler(maxBytes=10MB, backupCount=3)`.
- SQLite schema version 9 with ~20 tables: `agent_baton/core/storage/schema.py:43`.

#### 6. Horizontal Scaling

**PARTIAL** -- Multiple daemon instances can run simultaneously via
task-ID namespacing. Each supervisor writes to its own
`executions/<task_id>/` directory with flock-based PID file locking.
`baton daemon list` scans for all running workers. However, this is
single-machine concurrency only. There is no distributed lock, no
shared state coordination, and SQLite does not support multi-writer
from separate hosts.

- `agent_baton/core/runtime/supervisor.py:13-14` -- namespaced execution directories for concurrent plans.
- `agent_baton/core/runtime/supervisor.py:288-298` -- `list_workers()` scans `executions/` for running PIDs.
- `agent_baton/core/engine/persistence.py:6-13` -- namespaced `execution-state.json` per task.

#### 7. Backup/Restore for SQLite State

**PARTIAL** -- Schema migrations exist (`MIGRATIONS` dict in
`schema.py`, versions 2-9). A full JSON-to-SQLite migration tool
exists (`agent_baton/core/storage/migrate.py`). However, there is no
dedicated backup/restore command, no `baton db export`, no pg_dump
equivalent. Standard SQLite tooling (`.backup`) works, but Priya
would need to script it herself.

- `agent_baton/core/storage/schema.py:46` -- `MIGRATIONS: dict[int, str]` with 8 migration versions.
- `agent_baton/core/storage/migrate.py` -- full migration pipeline from JSON flat files to SQLite.

### Deployment

#### 8. Docker Image Buildable

**BLOCKED** -- No Dockerfile exists. The project is pip-installable
with optional extras (`[api]`, `[classify]`, `[dev]`), so writing a
Dockerfile is mechanically simple but not provided.

- `pyproject.toml:36-49` -- optional dependency groups defined.

#### 9. Kubernetes-Ready

**PARTIAL** -- Health and readiness probes exist (items 2). Graceful
shutdown via SIGTERM/SIGINT is implemented with a 30-second drain
timeout. However: no resource limit annotations, no liveness/readiness
probe configuration examples, no Helm chart, no k8s manifests.

- `agent_baton/core/runtime/signals.py` -- `SignalHandler` with SIGTERM + SIGINT.
- `agent_baton/core/runtime/supervisor.py:180-209` -- 30-second drain timeout on signal.
- Daemonization is POSIX-only: `agent_baton/core/runtime/daemon.py:32-35`.

#### 10. Prometheus Metrics Endpoint

**BLOCKED** -- No Prometheus metrics exposition endpoint exists. No
`/metrics` route. No `prometheus_client` dependency. Telemetry is
JSONL-based (`AgentTelemetry`) with manual aggregation, not
OpenMetrics/Prometheus compatible.

- `agent_baton/core/observe/telemetry.py` -- JSONL append-only log, not metrics exposition.
- No reference to "prometheus", "openmetrics", or "/metrics" in the codebase.

#### 11. Structured Logging (JSON)

**BLOCKED** -- The daemon uses Python `logging.Formatter("%(asctime)s
%(levelname)s %(message)s")`, which is plain-text, not structured JSON.
No `structlog`, no `JSONFormatter`, no log correlation IDs. The
supervisor docstring claims "structured logging" but the implementation
is standard Python text logging with rotation.

- `agent_baton/core/runtime/supervisor.py:394-395` -- `logging.Formatter("%(asctime)s %(levelname)s %(message)s")`.
- No `structlog` in `pyproject.toml` dependencies.

#### 12. Ingress/Auth for PMO Dashboard

**PARTIAL** -- Bearer token auth middleware exists and applies to all
routes except health probes and docs. CORS middleware is configured.
However: no session management, no role-based access control, no
scoped permissions. The PMO UI is served as static files at `/pmo/`
and inherits the same bearer token gate. There is no user
authentication layer (login/password, OAuth, SAML).

- `agent_baton/api/middleware/auth.py:37-66` -- `TokenAuthMiddleware` with single bearer token.
- `agent_baton/api/middleware/cors.py` -- `CORSMiddleware` configuration.
- `agent_baton/api/server.py:148-152` -- PMO UI static file mount at `/pmo/`.

### Integration

#### 13. CI Pipeline Gates

**PARTIAL** -- Gate commands are stack-aware (the planner generates
`pytest`, `npm test`, etc. based on detected language). Gates support
custom commands via `PlanGate.command`. However, there is no GitHub
Actions integration, no CI-specific webhook, no `baton ci` command.
Gates run inside the execution context, not in a CI pipeline. A team
would need to wire this manually.

- `agent_baton/core/engine/planner.py:126` -- `_STACK_GATE_COMMANDS` keyed by language.
- `agent_baton/models/execution.py:332-366` -- `PlanGate` with `gate_type` and `command`.
- No `.github/` directory exists.

#### 14. Bearer Token Auth with Scoped Permissions

**PARTIAL** -- Bearer token auth works, but it is a single shared
token with no scopes, no roles, no permission granularity. All
authenticated requests have the same access level. No timing-safe
comparison (acknowledged in code comments as acceptable for local-only
deployments).

- `agent_baton/api/middleware/auth.py:73-86` -- simple equality check, no `hmac.compare_digest`.
- No scope/role/permission model in the auth middleware.

#### 15. HMAC-Signed Webhooks

**WORKS** -- Outbound webhooks are HMAC-SHA256 signed when a secret is
configured. The signature is sent in `X-Baton-Signature` header.
Webhook registration supports per-hook secrets. Retry with exponential
backoff (3 attempts, 5s/30s/300s). Auto-disable after 10 consecutive
failures. Failure log at `webhook-failures.jsonl`.

- `agent_baton/api/webhooks/dispatcher.py:269-283` -- `_sign_payload()` using `hmac.new(secret, payload, sha256)`.
- `agent_baton/api/webhooks/dispatcher.py:225-233` -- signature header and event metadata.
- `agent_baton/api/routes/webhooks.py:23-61` -- `POST /api/v1/webhooks` registration with optional secret.

#### 16. Network Egress Requirements

**WORKS** -- The daemon makes zero external network calls by default.
All orchestration uses local `claude` CLI subprocesses. Webhooks are
opt-in outbound HTTP. External source adapters (ADO, GitHub, Jira,
Linear) are opt-in via `baton source add`. The Haiku classifier calls
the Anthropic API only when `ANTHROPIC_API_KEY` is set and the
`[classify]` extra is installed.

- `agent_baton/core/runtime/headless.py:34-40` -- environment passthrough limited to API keys.
- `agent_baton/core/engine/classifier.py:388-397` -- Haiku classification gated on env var presence.
- `agent_baton/core/storage/adapters/` -- all adapters are opt-in.

### Operations

#### 17. `baton daemon status` Works

**WORKS** -- `baton daemon status` reads PID file, checks process
liveness via `os.kill(pid, 0)`, reads engine status, and reads the
last saved daemon status JSON. Task-specific status via `--task-id`.
`baton daemon list` scans all execution directories.

- `agent_baton/cli/commands/execution/daemon.py:84-88` -- `status` subcommand with `--task-id`.
- `agent_baton/core/runtime/supervisor.py:213-249` -- `status()` method with PID check and engine query.
- `agent_baton/core/runtime/supervisor.py:288-298` -- `list_workers()` discovery.

#### 18. `baton query stalled` Works

**WORKS** -- `baton query stalled` identifies running executions not
updated within a configurable threshold (default 24 hours). Returns
task_id, status, phase, timestamps, and hours_stalled. Supports
`--hours` flag. Output in table, JSON, or CSV format.

- `agent_baton/cli/commands/observe/query.py:26,84,155,513-531` -- stalled detection with `--hours N`.

#### 19. Structured Error Messages

**PARTIAL** -- API routes return `ErrorResponse(error, detail)` via
Pydantic models. FastAPI produces standard JSON error bodies for
validation failures. However, daemon/CLI errors use plain
`print()` or `logging.exception()` with no error codes, no
correlation IDs, no structured error taxonomy.

- `agent_baton/api/models/responses.py:680-687` -- `ErrorResponse` with `error` and `detail` fields.
- `agent_baton/cli/main.py:190-198` -- CLI errors use `BATON_DEBUG=1` for full tracebacks.

#### 20. Graceful Shutdown Preserves Task State

**WORKS** -- SIGTERM/SIGINT triggers `SignalHandler`, which cancels the
worker task with a 30-second drain timeout. The supervisor writes a
status snapshot before PID file cleanup. The execution engine
persists state via atomic writes (tmp + rename). `baton daemon start
--resume` can recover from the saved state.

- `agent_baton/core/runtime/supervisor.py:180-209` -- `_run_with_signals()` with 30s drain.
- `agent_baton/core/runtime/supervisor.py:174` -- `_write_status()` in `finally` block.
- `agent_baton/core/engine/persistence.py:30-49` -- atomic state persistence with tmp-then-rename.

### Priya's Dealbreaker Assessment

| Dealbreaker | Verdict |
|---|---|
| No health endpoint | **CLEAR** -- `/api/v1/health` and `/api/v1/ready` exist |
| Secrets in config files only | **CLEAR** -- all secrets via env vars |
| No graceful shutdown | **CLEAR** -- SIGTERM handler with 30s drain |
| Unbounded resource consumption | **RISK** -- SQLite grows unbounded; no DB size limits or pruning |
| No structured logging | **FAIL** -- plain-text `%(asctime)s %(levelname)s %(message)s` format |
| Hard internet dependencies | **CLEAR** -- zero egress by default |

**Priya's overall verdict: CONDITIONAL PASS.** The health endpoint,
secrets handling, graceful shutdown, and webhook infrastructure meet
her bar. Two gaps would need addressing before production deployment:
(1) no Dockerfile/Helm chart means she writes deployment artifacts
from scratch, and (2) plain-text logging requires a structured logging
retrofit. The lack of Prometheus metrics is a third concern but
workable with log-based monitoring.

---

## Tomoko's Journey (Workflow Designer)

Tomoko is the "agent whisperer" who optimizes multi-agent workflows
through data-driven iteration. She reads source code and expects
transparency in how the system works.

### Deepening Engagement

#### 1. Reference Procedures

**WORKS** -- 16 reference documents in `references/` covering cost
budgets, decision frameworks, guardrail presets, git strategy, hooks
enforcement, knowledge architecture, research procedures, doc
generation, failure handling, task sequencing, comms protocols,
patterns, agent routing, adaptive execution, engine reference, and
planning taxonomy. These are the canonical sources that agents read
at runtime.

- `references/` -- 16 `.md` files, all distributable.
- `.claude/references/` is a symlink to `references/` for live editing.

#### 2. Agent Routing Logic

**WORKS** -- `AgentRouter` in `core/orchestration/router.py` is a
clean, readable module. Stack detection scans for package manager
signals and framework signals across two directory levels. The
`FLAVOR_MAP` is an explicit dict mapping `(language, framework)` to
agent flavor suffixes. `LearnedOverrides` can override routing
decisions from closed-loop learning. Tomoko can read and modify the
routing tables directly.

- `agent_baton/core/orchestration/router.py:19-58` -- `PACKAGE_SIGNALS`, `FRAMEWORK_SIGNALS`, `FLAVOR_MAP` as explicit dicts.
- `agent_baton/core/orchestration/router.py:257-283` -- learned override integration.

#### 3. `baton scores` Agent Performance

**WORKS** -- `PerformanceScorer` computes per-agent scorecards from
usage logs and retrospective data. Metrics include: times_used,
first_pass_rate, retry_rate, gate_pass_rate, total_estimated_tokens,
avg_tokens_per_use, models_used, positive/negative mentions, and
knowledge gaps cited. Output as Markdown. Supports `--agent NAME`,
`--write`, `--trends`, and `--teams`.

- `agent_baton/cli/commands/improve/scores.py:17-32` -- CLI with 4 modes.
- `agent_baton/core/improve/scoring.py:188-267` -- `PerformanceScorer.score_agent()` with dual data source (storage + filesystem).

#### 4. `baton patterns` Recurring Patterns

**WORKS** -- `PatternLearner` extracts recurring agent sequencing
strategies from usage logs. Groups by sequencing mode, computes
per-group statistics (token usage, retry rates, gate pass rates).
Surfaces patterns meeting minimum sample size (5+) and confidence
threshold (0.7). Supports `--refresh`, `--task-type`, `--min-confidence`,
and `--recommendations`.

- `agent_baton/cli/commands/improve/patterns.py:17-46` -- CLI with 4 flags.
- `agent_baton/core/learn/pattern_learner.py` -- full pattern extraction engine.

#### 5. `baton evolve` Prompt Improvements

**WORKS** -- `PromptEvolutionEngine` identifies underperforming agents
and generates prompt modification proposals based on a cascade of
quantitative and qualitative signals (first-pass rate, retry rate,
gate pass rate, negative mentions, knowledge gaps). Proposals are
always high-risk and never auto-applied. `AgentVersionControl`
creates timestamped backups before any modification.

- `agent_baton/cli/commands/improve/evolve.py:37-65` -- CLI with `--agent`, `--save`, `--write`.
- `agent_baton/core/improve/evolution.py:1-47` -- evolution strategy documentation with safety guardrails.

### Active Agent Design

#### 6. Talent Builder Creates Agents

**WORKS** -- The `talent-builder` agent definition at
`agents/talent-builder.md` is a comprehensive agent factory. It builds
the full knowledge stack: agent definitions, knowledge packs, skills,
and reference documents. It includes a 5-step workflow (understand
need, research domain, design knowledge layer, create artifacts,
validate). It reads the decision framework and knowledge architecture
references before creating anything.

- `agents/talent-builder.md` -- 60+ line agent definition with structured workflow.

#### 7. Study Routing Tables

**WORKS** -- Routing tables are explicit Python dicts, not hidden in
configuration or compiled artifacts. `PACKAGE_SIGNALS`,
`FRAMEWORK_SIGNALS`, and `FLAVOR_MAP` are all module-level constants
in `router.py`. `LearnedOverrides` stores runtime corrections in
`learned-overrides.json`. `baton route` CLI command exposes routing
decisions.

- `agent_baton/core/orchestration/router.py:19-58` -- three explicit routing dicts.
- `agent_baton/core/learn/overrides.py` -- persistent override storage.

#### 8. `baton experiment` for A/B Testing

**WORKS** -- `ExperimentManager` creates controlled experiments from
applied recommendations. Tracks baseline vs. post-change metrics.
Requires 5+ samples before evaluation. Improvement/degradation
thresholds at +/- 5%. Max 2 active experiments per agent to prevent
compounding. Auto-rollback on degradation. CLI supports `list`, `show`,
`conclude`, `rollback`.

- `agent_baton/cli/commands/improve/experiment.py:21-49` -- 4 subcommands.
- `agent_baton/core/improve/experiments.py:44-100` -- `ExperimentManager` with safety constraints.
- Degraded experiments auto-rollback via `ImprovementLoop`: `agent_baton/core/improve/loop.py:23-24`.

#### 9. Agent Scores Per-Stack Breakdowns

**PARTIAL** -- Scorecards are per-agent, not per-stack. There is no
`--stack python` filter on `baton scores`. The `score_teams()` method
exists for team composition effectiveness, and `detect_trends()`
tracks improving/degrading/stable patterns. But Tomoko cannot break
down an agent's performance by project stack or technology domain
without writing custom SQL via `baton query --sql`.

- `agent_baton/core/improve/scoring.py:351` -- `score_all()` returns all agents.
- `agent_baton/core/improve/scoring.py:462-469` -- `write_report()` and `score_teams()`.
- Missing: per-stack filtering on scorecards.

### Workflow Engineering

#### 10. Reusable Plan Templates

**PARTIAL** -- The planner uses built-in phase templates per task type
(`_PHASE_NAMES` dict with entries for "new-feature", "bug-fix",
"refactor", etc.). Learned patterns from `PatternLearner` influence
future plans. However, there is no `baton plan save-template` or
`baton plan load-template` command. Tomoko cannot explicitly save a
successful plan as a reusable template and apply it to future tasks
by name.

- `agent_baton/core/engine/planner.py:108-122` -- `_PHASE_NAMES` built-in templates.
- `agent_baton/core/learn/pattern_learner.py` -- learned patterns implicitly reused.
- Missing: explicit save/load template CLI.

#### 11. Conditional Branches in Plans

**PARTIAL** -- Plans support `approval_required` (pause for human
decision), `feedback_questions` (structured multiple-choice gates),
and the `INTERACT` action type for multi-turn dialogue. The planner
supports phase amendment at runtime via `baton execute amend`. However,
there are no conditional branches (if/else at the plan level). Plans
are strictly sequential phases with optional gates between them.

- `agent_baton/models/execution.py:395-397` -- `approval_required`, `feedback_questions`.
- `agent_baton/models/execution.py:448` -- `execution_mode: "phased" | "parallel" | "sequential"`.
- Missing: conditional branching, skip-if, or if/else in plan schema.

#### 12. Custom Gate Scripts

**WORKS** -- `PlanGate.command` accepts arbitrary shell commands.
Gate types include `build`, `test`, `lint`, `spec`, `review`, and
`approval`. The command field supports `{files}` placeholder
substitution. Stack-aware default gate commands are provided per
language, but custom commands can be specified in the plan JSON or
via LLM-generated plans.

- `agent_baton/models/execution.py:348-349` -- `gate_type` and `command` fields on `PlanGate`.
- `agent_baton/core/engine/gates.py:96-131` -- `build_gate_action()` with `{files}` substitution.
- `agent_baton/core/engine/planner.py:126-129` -- `_STACK_GATE_COMMANDS` per language.

#### 13. `baton learn interview`

**WORKS** -- `LearningInterviewer` provides a structured CLI dialogue
for human-directed decisions. Presents open learning issues one at a
time with multiple-choice options tailored per issue type (routing
mismatch, agent degradation, knowledge gap, pattern drift, gate
mismatch, roster bloat). Records decisions with optional reasoning.
Accessed via `baton learn interview`.

- `agent_baton/cli/commands/improve/learn_cmd.py:73-78` -- `interview` subcommand.
- `agent_baton/core/learn/interviewer.py:32-60` -- `_OPTIONS_BY_TYPE` with 6 issue types.

#### 14. Knowledge Pack Creation and Attachment

**WORKS** -- Full knowledge system with `KnowledgeRegistry`,
`KnowledgeResolver`, knowledge packs in `.claude/knowledge/`, and
per-step knowledge attachment. CLI flags `--knowledge` and
`--knowledge-pack` on `baton plan`. Beads can be promoted to
knowledge packs via `baton beads`. Agents can declare required packs
in their definitions.

- `agent_baton/core/orchestration/knowledge_registry.py:241` -- `KnowledgeRegistry`.
- `agent_baton/core/engine/knowledge_resolver.py:82` -- `KnowledgeResolver` chains resolution strategies.
- `agent_baton/cli/commands/execution/plan_cmd.py:82-83` -- `--knowledge-pack` flag.
- `agent_baton/cli/commands/bead_cmd.py:446-494` -- bead-to-knowledge-pack promotion.

### Internal Evangelism

#### 15. `baton package` Creates Shareable Packages

**WORKS** -- `PackageBuilder` creates `.tar.gz` archives bundling
agents, references, and optionally knowledge packs. Supports
`--name`, `--info` (inspect manifest), `--install` (extract and copy),
`--version`, `--description`, `--include-knowledge`, `--no-agents`,
`--no-references`, `--output-dir`, `--scope` (user/project), `--force`.

- `agent_baton/cli/commands/distribute/package.py:19-80` -- comprehensive CLI.
- `agent_baton/core/distribute/sharing.py:115` -- `PackageBuilder` with full build/extract/install cycle.

#### 16. Packages Include Agents + References + Templates

**WORKS** -- Packages bundle agents, references, and knowledge packs.
The registry client (`RegistryClient`) manages a local registry
directory for installing packages from shared locations. Transfer
command supports comma-separated knowledge pack lists. Verify command
counts agents, references, and knowledge packs.

- `agent_baton/core/distribute/sharing.py:282-346` -- install flow for agents, references, knowledge.
- `agent_baton/cli/commands/distribute/verify_package.py:50` -- verification counts.
- `agent_baton/cli/commands/distribute/transfer.py:54` -- knowledge pack transfer.

#### 17. Cross-Project Learning

**WORKS** -- `SyncEngine` pushes per-project `baton.db` data to
`~/.baton/central.db` with project_id prefix. Central DB includes
analytics views: `v_agent_reliability`, `v_cost_by_task_type`,
`v_recurring_knowledge_gaps`, `v_project_failure_rate`,
`v_cross_project_discoveries`, `v_external_plan_mapping`. `baton
cquery` runs cross-project SQL. `baton query portfolio` shows
cross-project status.

- `agent_baton/core/storage/sync.py:1-80` -- `SyncEngine` with 15+ syncable tables.
- `agent_baton/core/storage/schema.py:1347-1423` -- 6 cross-project analytics views.
- `agent_baton/cli/commands/observe/query.py:26-27` -- `stalled` and `portfolio` subcommands.

### Tomoko's Dealbreaker Assessment

| Dealbreaker | Verdict |
|---|---|
| Opaque orchestration logic | **CLEAR** -- routing, scoring, learning all readable Python with explicit data structures |
| No experiment infrastructure | **CLEAR** -- `ExperimentManager` with controlled trials, baselines, auto-rollback |
| Hidden/non-queryable data | **CLEAR** -- `baton query --sql`, `baton cquery`, and 17+ predefined queries |
| No extensibility | **CLEAR** -- custom gate scripts, knowledge packs, agent definitions, flavor overrides |
| No feedback mechanism in learning | **CLEAR** -- `baton learn interview`, `LearningInterviewer`, closed-loop `ImprovementLoop` |
| Closed ecosystem | **CLEAR** -- `baton package`, `baton transfer`, `baton publish`, `baton pull` for sharing |

**Tomoko's overall verdict: PASS with caveats.** The system provides
deep transparency: routing tables are explicit dicts, scoring uses
readable formulas, the learning pipeline has clear guardrails, and all
data is queryable via SQL. Two areas would frustrate a power user:
(1) no explicit plan template save/load (learned patterns work
implicitly but are not user-controllable), and (2) per-stack scorecard
breakdowns require manual SQL. The experiment infrastructure with
auto-rollback on degradation would particularly delight Tomoko.

---

## Summary Scorecard

### Priya (20 checks)

| Rating | Count | Items |
|--------|-------|-------|
| WORKS | 10 | Health endpoint, secrets, OpenAPI, egress, webhooks, daemon status, stalled detection, graceful shutdown, HMAC signing, network isolation |
| PARTIAL | 6 | Resource requirements, horizontal scaling, backup/restore, K8s readiness, CI gates, bearer token scopes |
| BLOCKED | 4 | Dockerfile, Docker image, Prometheus metrics, structured logging |

### Tomoko (17 checks)

| Rating | Count | Items |
|--------|-------|-------|
| WORKS | 13 | References, routing logic, scores, patterns, evolve, talent-builder, routing tables, experiments, custom gates, learn interview, knowledge packs, packages, cross-project learning |
| PARTIAL | 4 | Per-stack scores, plan templates, conditional branches, ingress auth |
| BLOCKED | 0 | -- |

### Priority Gaps (addressing both personas)

1. **Dockerfile + Helm chart** -- blocks Priya's deployment story entirely.
2. **Structured JSON logging** -- Priya dealbreaker; retrofit with
   `structlog` or `python-json-logger`.
3. **Prometheus metrics** -- standard expectation for production services;
   expose counters for dispatched steps, completed tasks, gate results.
4. **Explicit plan template save/load** -- Tomoko would benefit from
   `baton plan save-template` / `baton plan from-template`.
5. **Per-stack scorecard filtering** -- power user expectation.
6. **RBAC / scoped tokens** -- needed before multi-user deployment.
