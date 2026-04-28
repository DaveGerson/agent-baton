# Package Layout (Reference)

> **Audience.** Engineers navigating the `agent_baton/` source tree.
> Every Python subpackage in the project, with a one-line purpose and
> the load-bearing module(s) cited by `path:line`. Use this as a map
> when you don't yet know where a class lives. For *why* the layering
> exists, see [../architecture.md §Design philosophy](../architecture.md).

---

## Top-level layout

```
agent_baton/
  __init__.py     - Public API exports (ExecutionEngine, IntelligentPlanner, ...)
  models/         - Layer 1. Pure dataclasses. No internal deps.
  utils/          - Small helpers (frontmatter parsing).
  core/           - Layer 2. Subsystems, organised by concern.
  api/            - Layer 4a. FastAPI app and routes.
  cli/            - Layer 4b. argparse subcommand modules.
  _bundled_agents/- Distributable agent .md files vendored into the wheel.

pmo-ui/           - Layer 4c. React/Vite frontend (separate root).
```

Imports flow downward only:
`models → core/* (peer-level) → engine/runtime → CLI/API`.

The full dependency contract is documented in
[../architecture.md §Design philosophy](../architecture.md). It is
enforced by import-graph tests in `tests/`.

---

## `agent_baton/models/` — Layer 1

24 modules of dataclasses. Every module imports only from `dataclasses`,
`enum`, `typing`, and the standard library. **No internal imports.**

| Module | Key types | Notes |
|--------|----------|------|
| [`execution.py`](../../agent_baton/models/execution.py) | `MachinePlan`, `PlanPhase`, `PlanStep`, `PlanGate`, `TeamMember`, `SynthesisSpec`, `ExecutionState`, `StepResult`, `GateResult`, `ApprovalResult`, `PlanAmendment`, `ExecutionAction`, `ActionType`, `StepStatus`, `PhaseStatus`, `InteractionTurn`, `FeedbackQuestion`, `FeedbackResult`, `FileAttribution`, `ConsolidationResult` | The plan and state core. ~1700 lines, the largest model file. |
| [`enums.py`](../../agent_baton/models/enums.py) | `RiskLevel`, `TrustLevel`, `BudgetTier`, `ExecutionMode`, `GateOutcome`, `FailureClass`, `GitStrategy`, `AgentCategory` | Cross-cutting enums. |
| [`agent.py`](../../agent_baton/models/agent.py) | `AgentDefinition` | Parsed from `.md` frontmatter. |
| [`events.py`](../../agent_baton/models/events.py) | `Event` | EventBus payload (`topic`, `task_id`, `sequence`, `payload`). |
| [`knowledge.py`](../../agent_baton/models/knowledge.py) | `KnowledgeDocument`, `KnowledgePack`, `KnowledgeAttachment`, `KnowledgeGapSignal`, `KnowledgeGapRecord`, `ResolvedDecision` | Knowledge delivery types. |
| [`pmo.py`](../../agent_baton/models/pmo.py) | `PmoProject`, `PmoCard`, `PmoSignal`, `ProgramHealth`, `PmoConfig`, `InterviewQuestion`, `InterviewAnswer` | PMO board types. |
| [`bead.py`](../../agent_baton/models/bead.py) | `Bead`, `BeadLink` | Structured agent memory. |
| [`usage.py`](../../agent_baton/models/usage.py) | `AgentUsageRecord`, `TaskUsageRecord` | Token / cost accounting. |
| [`retrospective.py`](../../agent_baton/models/retrospective.py) | `Retrospective`, `AgentOutcome`, `KnowledgeGap`, `RosterRecommendation`, `SequencingNote`, `TeamCompositionRecord`, `ConflictRecord` | Retro reports. |
| [`trace.py`](../../agent_baton/models/trace.py) | `TaskTrace`, `TraceEvent` | Per-task execution DAG. |
| [`decision.py`](../../agent_baton/models/decision.py) | `DecisionRequest`, `DecisionResolution`, `ContributionRequest` | Human-in-loop decisions. |
| [`pattern.py`](../../agent_baton/models/pattern.py) | `LearnedPattern`, `PlanStructureHint`, `TeamPattern` | Pattern-learner outputs. |
| [`budget.py`](../../agent_baton/models/budget.py) | `BudgetRecommendation` | Budget tuner output. |
| [`feedback.py`](../../agent_baton/models/feedback.py) | `RetrospectiveFeedback` | Closed-loop feedback record. |
| [`context_profile.py`](../../agent_baton/models/context_profile.py) | `AgentContextProfile`, `TaskContextProfile` | Context efficiency profiles. |
| [`registry.py`](../../agent_baton/models/registry.py) | `RegistryEntry`, `RegistryIndex` | Distribution registry types. |
| [`escalation.py`](../../agent_baton/models/escalation.py) | `Escalation` | Governance escalation records. |
| [`improvement.py`](../../agent_baton/models/improvement.py) | `Recommendation`, `Experiment`, `Anomaly`, `TriggerConfig`, `ImprovementReport`, `ImprovementConfig`, `RecommendationCategory`, `RecommendationStatus`, `ExperimentStatus`, `AnomalySeverity` | Improvement-loop types. |
| [`learning.py`](../../agent_baton/models/learning.py) | `LearningEvidence`, `LearningIssue` | Closed-loop learning ledger. |
| [`parallel.py`](../../agent_baton/models/parallel.py) | `ExecutionRecord`, `ResourceLimits` | Concurrency configuration. |
| [`plan.py`](../../agent_baton/models/plan.py) | `MissionLogEntry` | Mission-log entries. |
| [`reference.py`](../../agent_baton/models/reference.py) | `ReferenceDocument` | Distributable reference docs. |
| [`session.py`](../../agent_baton/models/session.py) | `SessionCheckpoint`, `SessionParticipant`, `SessionState` | Daemon session tracking. |

All model types implement `to_dict()` / `from_dict()` for JSON
round-tripping. Enum fields use typed enum instances internally and
serialize to `.value` strings only at the `to_dict()` boundary
(ADR-09).

---

## `agent_baton/utils/`

| Module | Purpose |
|--------|---------|
| [`utils/frontmatter.py`](../../agent_baton/utils/frontmatter.py) | `parse_frontmatter()` — YAML frontmatter extraction from `.md` files. |

---

## `agent_baton/core/` — Layer 2

The execution engine and all subsystems. Each subpackage is documented
below with its load-bearing module(s).

### `core/engine/` — execution state machine

The heart of Agent Baton. Owns plan state and the action loop.

| Module | Class / function | Purpose |
|--------|-----------------|---------|
| [`executor.py`](../../agent_baton/core/engine/executor.py) | `ExecutionEngine` (line 308) | The state machine. ~6900 LOC. Implements `ExecutionDriver`. |
| [`planner.py`](../../agent_baton/core/engine/planner.py) | `IntelligentPlanner` (line 760) | Data-driven plan creation. Consults `AgentRouter`, `PatternLearner`, `BudgetTuner`, `PolicyEngine`, `KnowledgeResolver`, `BeadAnalyzer`. |
| [`dispatcher.py`](../../agent_baton/core/engine/dispatcher.py) | `PromptDispatcher` (line 161) | Stateless prompt assembly: delegation prompts, gate prompts, path-enforcement bash guards. |
| [`gates.py`](../../agent_baton/core/engine/gates.py) | `GateRunner` (line 67), `DryRunGateRunner` (line 338) | Stateless gate evaluator (test/build/lint/spec/review). |
| [`persistence.py`](../../agent_baton/core/engine/persistence.py) | `StatePersistence` (line 30) | Atomic JSON I/O for `ExecutionState`; manages `active-task-id.txt`. |
| [`protocols.py`](../../agent_baton/core/engine/protocols.py) | `ExecutionDriver` (line 22) | The 15-method interface between runtime and engine. |
| [`classifier.py`](../../agent_baton/core/engine/classifier.py) | `TaskClassifier` (Protocol), `KeywordClassifier`, `HaikuClassifier`, `FallbackClassifier` | Plan-sizing classifier (Haiku → keyword fallback). |
| [`knowledge_resolver.py`](../../agent_baton/core/engine/knowledge_resolver.py) | `KnowledgeResolver` | 4-layer knowledge resolution with per-step token budget. |
| [`knowledge_gap.py`](../../agent_baton/core/engine/knowledge_gap.py) | `parse_knowledge_gap()`, `determine_escalation()` | Parses `KNOWLEDGE_GAP/CONFIDENCE/TYPE` signals. |
| [`knowledge_telemetry.py`](../../agent_baton/core/engine/knowledge_telemetry.py) | `KnowledgeTelemetry` | Knowledge-usage events. |
| [`bead_store.py`](../../agent_baton/core/engine/bead_store.py) | `BeadStore` | SQLite CRUD for `beads` + `bead_tags`; dependency-aware `ready()`. |
| [`bead_signal.py`](../../agent_baton/core/engine/bead_signal.py) | `parse_bead_signals()`, `parse_bead_feedback()` | Parses `BEAD_DISCOVERY/DECISION/WARNING/USEFUL/STALE`. |
| [`bead_selector.py`](../../agent_baton/core/engine/bead_selector.py) | `BeadSelector` | Three-tier prompt-injection selection. |
| [`bead_decay.py`](../../agent_baton/core/engine/bead_decay.py) | `decay_beads()` | Retention-based archival. |
| [`bead_anchors.py`](../../agent_baton/core/engine/bead_anchors.py) | (anchor utilities) | Bead-anchor management. |
| [`plan_reviewer.py`](../../agent_baton/core/engine/plan_reviewer.py) | `PlanReviewer` | Plan-quality static checks. |
| [`worktree_manager.py`](../../agent_baton/core/engine/worktree_manager.py) | `WorktreeManager` | Wave 1.3 git-worktree per-step isolation. |
| [`takeover.py`](../../agent_baton/core/engine/takeover.py) | (takeover support) | Wave 5.1 human-takeover. |
| [`selfheal.py`](../../agent_baton/core/engine/selfheal.py) | (self-heal escalation) | Wave 5.2 model-tier escalation on gate failure. |
| [`speculator.py`](../../agent_baton/core/engine/speculator.py) | (speculative execution) | Wave 5.3 sibling-worktree speculation. |
| [`foresight.py`](../../agent_baton/core/engine/foresight.py) | `Foresight` | Predictive next-step hinting. |
| [`cost_estimator.py`](../../agent_baton/core/engine/cost_estimator.py) | (estimator) | Token cost estimation per step. |
| [`team_board.py`](../../agent_baton/core/engine/team_board.py) | `TeamBoard` | Team-step coordination state. |
| [`team_registry.py`](../../agent_baton/core/engine/team_registry.py) | `TeamRegistry` | Team composition lookup. |
| [`team_tools.py`](../../agent_baton/core/engine/team_tools.py) | (helpers) | Team-step utilities. |
| [`soul_registry.py`](../../agent_baton/core/engine/soul_registry.py) | `SoulRegistry` | Agent persona / soul records. |
| [`soul_router.py`](../../agent_baton/core/engine/soul_router.py) | `SoulRouter` | Soul-aware routing. |
| [`notes_adapter.py`](../../agent_baton/core/engine/notes_adapter.py) | (adapter) | Notes-to-bead bridge. |
| [`dry_run_launcher.py`](../../agent_baton/core/engine/dry_run_launcher.py) | (launcher) | Engine-internal dry-run helper. |
| [`flags.py`](../../agent_baton/core/engine/flags.py) | feature-flag helpers | Reads `BATON_*_ENABLED` env vars. |
| [`errors.py`](../../agent_baton/core/engine/errors.py) | engine-specific exceptions | |

### `core/runtime/` — async execution layer

Wraps the synchronous engine in an async loop. Implements daemon mode.

| Module | Class | Purpose |
|--------|-------|---------|
| [`worker.py`](../../agent_baton/core/runtime/worker.py) | `TaskWorker` | Async event loop driving a single task. |
| [`supervisor.py`](../../agent_baton/core/runtime/supervisor.py) | `WorkerSupervisor` | Daemon lifecycle: pidfile, log rotation, graceful shutdown. |
| [`scheduler.py`](../../agent_baton/core/runtime/scheduler.py) | `StepScheduler`, `SchedulerConfig` | Bounded-concurrency dispatch (`asyncio.Semaphore`). |
| [`launcher.py`](../../agent_baton/core/runtime/launcher.py) | `AgentLauncher` (Protocol), `DryRunLauncher`, `LaunchResult` | Launcher interface + test stub. |
| [`claude_launcher.py`](../../agent_baton/core/runtime/claude_launcher.py) | `ClaudeCodeLauncher`, `ClaudeCodeConfig` | Real `claude` CLI subprocess launcher. |
| [`headless.py`](../../agent_baton/core/runtime/headless.py) | `HeadlessClaude`, `HeadlessConfig`, `HeadlessResult` | Synchronous `claude --print` wrapper used by Forge and `baton execute run`. |
| [`context.py`](../../agent_baton/core/runtime/context.py) | `ExecutionContext` | Wires `EventBus`, engine, and `EventPersistence` correctly. |
| [`decisions.py`](../../agent_baton/core/runtime/decisions.py) | `DecisionManager` | Persists human decision requests. |
| [`signals.py`](../../agent_baton/core/runtime/signals.py) | `SignalHandler` | POSIX SIGTERM/SIGINT graceful shutdown. |
| [`daemon.py`](../../agent_baton/core/runtime/daemon.py) | `daemonize()` | Classic UNIX double-fork. |
| [`tenancy_context.py`](../../agent_baton/core/runtime/tenancy_context.py) | tenancy helpers | F0.2 tenancy attribution. |
| [`_redaction.py`](../../agent_baton/core/runtime/_redaction.py) | redaction helpers | Strips API keys from launcher stderr. |

### `core/orchestration/` — agent discovery and routing

| Module | Class | Purpose |
|--------|-------|---------|
| [`registry.py`](../../agent_baton/core/orchestration/registry.py) | `AgentRegistry` | Loads `.md` agents from `~/.claude/agents/` and `.claude/agents/` (project takes precedence). |
| [`router.py`](../../agent_baton/core/orchestration/router.py) | `AgentRouter`, `StackProfile` | Stack detection (`PACKAGE_SIGNALS`, `FRAMEWORK_SIGNALS`); flavored agent routing. |
| [`context.py`](../../agent_baton/core/orchestration/context.py) | `ContextManager` | Manages `.claude/team-context/` files. |
| [`knowledge_registry.py`](../../agent_baton/core/orchestration/knowledge_registry.py) | `KnowledgeRegistry`, `_TFIDFIndex` | Knowledge-pack discovery and TF-IDF index. |

### `core/storage/` — persistence and federation

| Module | Class / function | Purpose |
|--------|-----------------|---------|
| [`__init__.py`](../../agent_baton/core/storage/__init__.py) | `get_project_storage()`, `detect_backend()`, `get_pmo_central_store()`, `get_central_storage()`, `get_sync_engine()` | Backend factories. |
| [`protocol.py`](../../agent_baton/core/storage/protocol.py) | `StorageBackend` (Protocol) | 34-method persistence interface. |
| [`sqlite_backend.py`](../../agent_baton/core/storage/sqlite_backend.py) | `SqliteStorage` | SQLite implementation; 31-table project schema. |
| [`file_backend.py`](../../agent_baton/core/storage/file_backend.py) | `FileStorage` | Legacy JSON/JSONL implementation. |
| [`schema.py`](../../agent_baton/core/storage/schema.py) | `PROJECT_SCHEMA_DDL`, `CENTRAL_SCHEMA_DDL`, `MIGRATIONS` | DDL constants. |
| [`connection.py`](../../agent_baton/core/storage/connection.py) | `ConnectionManager` | WAL-mode helper, schema migrations. |
| [`queries.py`](../../agent_baton/core/storage/queries.py) | `QueryEngine` | Ad-hoc SQL with structured helpers. |
| [`migrate.py`](../../agent_baton/core/storage/migrate.py) | `StorageMigrator` | Schema-version migrations. |
| [`migration_backup.py`](../../agent_baton/core/storage/migration_backup.py) | (backup helpers) | Pre-migration snapshots. |
| [`sync.py`](../../agent_baton/core/storage/sync.py) | `SyncEngine`, `SyncTableSpec`, `SyncResult`, `auto_sync_current_project()` | Incremental one-way sync project → central. |
| [`central.py`](../../agent_baton/core/storage/central.py) | `CentralStore` | Read-only `central.db` query interface. |
| [`pmo_sqlite.py`](../../agent_baton/core/storage/pmo_sqlite.py) | `PmoSqliteStore` | PMO data store (lives in `central.db`). |
| [`user_store.py`](../../agent_baton/core/storage/user_store.py) | (user store) | `users` + `approval_log` (in `central.db`). |
| [`conflict_store.py`](../../agent_baton/core/storage/conflict_store.py) | (conflict store) | Bead-conflict persistence. |
| [`handoff_store.py`](../../agent_baton/core/storage/handoff_store.py) | (handoff store) | Wave 3.2 handoff beads. |
| [`release_store.py`](../../agent_baton/core/storage/release_store.py) | (release store) | Release artifact tracking. |
| [`slo_store.py`](../../agent_baton/core/storage/slo_store.py) | (SLO store) | SLO targets and observations. |
| [`deployment_profile_store.py`](../../agent_baton/core/storage/deployment_profile_store.py) | (deployment profiles) | Per-environment deployment profiles. |
| [`adapters/__init__.py`](../../agent_baton/core/storage/adapters/__init__.py) | `ExternalSourceAdapter` (Protocol), `ExternalItem`, `AdapterRegistry` | External work-tracker interface. |
| [`adapters/ado.py`](../../agent_baton/core/storage/adapters/ado.py) | `AdoAdapter` | Azure DevOps adapter. |

### `core/events/` — pub/sub event bus

| Module | Class / function | Purpose |
|--------|-----------------|---------|
| [`bus.py`](../../agent_baton/core/events/bus.py) | `EventBus` | In-process pub/sub with glob topic routing. |
| [`events.py`](../../agent_baton/core/events/events.py) | 19 event factories | `step_dispatched()`, `step_completed()`, `gate_passed()`, etc. |
| [`persistence.py`](../../agent_baton/core/events/persistence.py) | `EventPersistence` | Append-only JSONL log per task. |
| [`projections.py`](../../agent_baton/core/events/projections.py) | `project_task_view()`, `TaskView`, `PhaseView`, `StepView` | Materialized views for dashboards. |

### `core/observe/` — observability

| Module | Class | Purpose |
|--------|-------|---------|
| [`trace.py`](../../agent_baton/core/observe/trace.py) | `TraceRecorder`, `TraceRenderer` | Per-task DAG tracing. |
| [`usage.py`](../../agent_baton/core/observe/usage.py) | `UsageLogger` | `TaskUsageRecord` JSONL appender. |
| [`telemetry.py`](../../agent_baton/core/observe/telemetry.py) | `AgentTelemetry`, `TelemetryEvent` | Tool-call/file-op telemetry. |
| [`dashboard.py`](../../agent_baton/core/observe/dashboard.py) | `DashboardGenerator` | Markdown dashboard renderer. |
| [`retrospective.py`](../../agent_baton/core/observe/retrospective.py) | `RetrospectiveEngine` | Auto-generated post-task retros. |
| [`context_profiler.py`](../../agent_baton/core/observe/context_profiler.py) | `ContextProfiler` | Per-agent context-efficiency metrics. |
| [`archiver.py`](../../agent_baton/core/observe/archiver.py) | `DataArchiver` | Retention-based cleanup. |
| [`incidents.py`](../../agent_baton/core/observe/incidents.py) | (incident store) | Production incidents. |
| [`jsonl_scanner.py`](../../agent_baton/core/observe/jsonl_scanner.py) | (scanner) | Fixes the usage counter from raw JSONL. |
| [`pagerduty.py`](../../agent_baton/core/observe/pagerduty.py) | (PD shipper) | PagerDuty alerts. |
| [`prometheus.py`](../../agent_baton/core/observe/prometheus.py) | (metrics) | Prometheus exposition. |
| [`slo_computer.py`](../../agent_baton/core/observe/slo_computer.py) | (SLO computer) | Computes SLOs from usage. |
| [`cost_forecaster.py`](../../agent_baton/core/observe/cost_forecaster.py) | (forecaster) | Token-cost projection. |

### `core/observability/` — OTel and FinOps

| Module | Class | Purpose |
|--------|-------|---------|
| [`otel_exporter.py`](../../agent_baton/core/observability/otel_exporter.py) | `OTelJSONLExporter`, `current_exporter()` | OTLP-shaped JSONL spans (env-gated by `BATON_OTEL_ENABLED`). |
| [`chargeback.py`](../../agent_baton/core/observability/chargeback.py) | `ChargebackBuilder`, `ChargebackReport` | F0.2 cost attribution by org/team/project/user/cost_center. |
| [`attribution_coverage.py`](../../agent_baton/core/observability/attribution_coverage.py) | `CoverageScanner`, `AttributionCoverageReport` | % of `usage_records` rows with non-default tenancy. |
| [`prometheus.py`](../../agent_baton/core/observability/prometheus.py) | (metrics) | OTel-side Prometheus support. |

### `core/govern/` — policy and compliance

| Module | Class | Purpose |
|--------|-------|---------|
| [`classifier.py`](../../agent_baton/core/govern/classifier.py) | `DataClassifier`, `ClassificationResult` | Auto-classifies risk + guardrail preset. |
| [`policy.py`](../../agent_baton/core/govern/policy.py) | `PolicyEngine`, `PolicyRule`, `PolicyViolation`, `PolicySet` | 5 built-in presets; rule types `path_block`/`path_allow`/`tool_restrict`/`require_agent`/`require_gate`. |
| [`compliance.py`](../../agent_baton/core/govern/compliance.py) | `ComplianceReportGenerator`, `ComplianceEntry`, `ComplianceReport` | Compliance report builder. |
| [`validator.py`](../../agent_baton/core/govern/validator.py) | `AgentValidator`, `ValidationResult` | Agent-frontmatter validator. |
| [`spec_validator.py`](../../agent_baton/core/govern/spec_validator.py) | `SpecValidator`, `SpecCheck`, `SpecValidationResult` | Spec-callable validation. |
| [`escalation.py`](../../agent_baton/core/govern/escalation.py) | `EscalationManager` | Escalation history. |
| [`override_log.py`](../../agent_baton/core/govern/override_log.py) | (override log) | Hash-chained compliance audit log. |
| [`aibom.py`](../../agent_baton/core/govern/aibom.py) | (AI BOM) | AI Bill of Materials emission. |
| [`budget.py`](../../agent_baton/core/govern/budget.py) | (budget guards) | Token-budget enforcement. |
| [`_redaction.py`](../../agent_baton/core/govern/_redaction.py) | redaction helpers | PII/secret redaction. |

### `core/improve/` — agent improvement loop

| Module | Class | Purpose |
|--------|-------|---------|
| [`scoring.py`](../../agent_baton/core/improve/scoring.py) | `PerformanceScorer`, `AgentScorecard`, `TeamScorecard` | Per-agent + per-team health ratings. |
| [`vcs.py`](../../agent_baton/core/improve/vcs.py) | `AgentVersionControl`, `ChangelogEntry` | Agent definition versioning + changelog. |
| [`loop.py`](../../agent_baton/core/improve/loop.py) | `ImprovementLoop` | Consolidated `ImprovementReport` builder. |
| [`proposals.py`](../../agent_baton/core/improve/proposals.py) | `ProposalManager` | `Recommendation` lifecycle. |
| [`rollback.py`](../../agent_baton/core/improve/rollback.py) | `RollbackManager`, `RollbackEntry` | Undo snapshots for applied changes. |
| [`triggers.py`](../../agent_baton/core/improve/triggers.py) | `TriggerEvaluator` | Auto-trigger conditions. |
| [`conflict_detection.py`](../../agent_baton/core/improve/conflict_detection.py) | (conflict detection) | Bead-graph conflict mining. |
| [`cost_anomaly.py`](../../agent_baton/core/improve/cost_anomaly.py) | (anomaly detection) | Cost-spike alerting. |
| [`handoff_score.py`](../../agent_baton/core/improve/handoff_score.py) | (handoff scoring) | Wave 3.2 handoff quality. |
| [`maintainer.py`](../../agent_baton/core/improve/maintainer.py) | (maintainer) | Long-running improvement housekeeping. |
| [`new_metrics.py`](../../agent_baton/core/improve/new_metrics.py) | (metrics) | Newer scorecard metrics. |
| [`readiness.py`](../../agent_baton/core/improve/readiness.py) | (readiness) | Production-readiness checks. |

### `core/learn/` — closed-loop learning

| Module | Class | Purpose |
|--------|-------|---------|
| [`pattern_learner.py`](../../agent_baton/core/learn/pattern_learner.py) | `PatternLearner` | Mines `LearnedPattern` from usage logs. |
| [`budget_tuner.py`](../../agent_baton/core/learn/budget_tuner.py) | `BudgetTuner` | Recommends budget tier changes. |
| [`engine.py`](../../agent_baton/core/learn/engine.py) | `LearningEngine` | Closed-loop `detect → analyze → apply` orchestrator. |
| [`ledger.py`](../../agent_baton/core/learn/ledger.py) | `LearningLedger` | SQLite CRUD for `LearningIssue`. |
| [`overrides.py`](../../agent_baton/core/learn/overrides.py) | `LearnedOverrides` | `learned-overrides.json` reader/writer. |
| [`resolvers.py`](../../agent_baton/core/learn/resolvers.py) | `resolve_*` functions | Type-specific resolution strategies. |
| [`interviewer.py`](../../agent_baton/core/learn/interviewer.py) | `LearningInterviewer` | Structured CLI dialogue for human-directed decisions. |
| [`recommender.py`](../../agent_baton/core/learn/recommender.py) | `Recommender` | Unified recommendation aggregator. |
| [`bead_analyzer.py`](../../agent_baton/core/learn/bead_analyzer.py) | `BeadAnalyzer` | Mines historical beads → `PlanStructureHint`. |
| [`signals.py`](../../agent_baton/core/learn/signals.py) | (signal helpers) | Learning-signal parsing. |

### `core/intel/` — intelligence helpers

| Module | Class | Purpose |
|--------|-------|---------|
| [`bead_synthesizer.py`](../../agent_baton/core/intel/bead_synthesizer.py) | `BeadSynthesizer` | Wave 2.1 — bead graph (edges + clusters), deterministic. |
| [`handoff_synthesizer.py`](../../agent_baton/core/intel/handoff_synthesizer.py) | `HandoffSynthesizer` | Wave 3.2 — compact handoff section between steps. |
| [`debate.py`](../../agent_baton/core/intel/debate.py) | (debate runner) | D4 multi-agent debate (opt-in, never auto-invoked). |
| [`knowledge_ranker.py`](../../agent_baton/core/intel/knowledge_ranker.py) | `KnowledgeRanker` | Re-orders knowledge candidates by effectiveness × recency × usage (bd-0184). |
| [`context_harvester.py`](../../agent_baton/core/intel/context_harvester.py) | (harvester) | Context-file discovery from history. |

### `core/pmo/` — portfolio management overlay

| Module | Class | Purpose |
|--------|-------|---------|
| [`store.py`](../../agent_baton/core/pmo/store.py) | `PmoStore` | Reads/writes PMO config + completed-plan archive. |
| [`scanner.py`](../../agent_baton/core/pmo/scanner.py) | `PmoScanner` | Builds Kanban board state from registered projects. |
| [`forge.py`](../../agent_baton/core/pmo/forge.py) | `ForgeSession` | Consultative plan creation with SSE progress streaming. |

### `core/distribute/` — packaging and registry

| Module | Class | Purpose |
|--------|-------|---------|
| [`sharing.py`](../../agent_baton/core/distribute/sharing.py) | `PackageBuilder`, `PackageManifest` | Builds `.tar.gz` distributable packages. |
| [`packager.py`](../../agent_baton/core/distribute/packager.py) | `PackageVerifier`, `PackageDependency`, `EnhancedManifest`, `PackageValidationResult` | Checksum + dependency validation. |
| [`registry_client.py`](../../agent_baton/core/distribute/registry_client.py) | `RegistryClient` | Local-registry directory manager. |
| [`experimental/async_dispatch.py`](../../agent_baton/core/distribute/experimental/async_dispatch.py) | `AsyncDispatcher`, `AsyncTask` | Experimental — not exercised in production. |
| [`experimental/incident.py`](../../agent_baton/core/distribute/experimental/incident.py) | `IncidentManager`, `IncidentPhase`, `IncidentTemplate` | Experimental P1-P4 incident templates. |
| [`experimental/transfer.py`](../../agent_baton/core/distribute/experimental/transfer.py) | `ProjectTransfer`, `TransferManifest` | Experimental cross-project transfer. |

### `core/swarm/` — swarm dispatcher (Wave 6.2)

Triggered by the `SWARM_DISPATCH` action.

| Module | Class | Purpose |
|--------|-------|---------|
| [`dispatcher.py`](../../agent_baton/core/swarm/dispatcher.py) | `SwarmDispatcher` (line 94), `SwarmResult`, `SwarmBudgetError` | Reconciler-driven swarm execution. |
| [`reconciler.py`](../../agent_baton/core/swarm/reconciler.py) | `Reconciler` | Decides what to dispatch. |
| [`coalescer.py`](../../agent_baton/core/swarm/coalescer.py) | `Coalescer` | Merges overlapping work items. |
| [`partitioner.py`](../../agent_baton/core/swarm/partitioner.py) | `Partitioner` | Splits work into parallelisable chunks. |

### `core/predict/` — Wave 6.2 predictive computation

| Module | Purpose |
|--------|---------|
| [`watcher.py`](../../agent_baton/core/predict/watcher.py) | Filesystem/SCM watcher feeding the classifier. |
| [`classifier.py`](../../agent_baton/core/predict/classifier.py) | Predictive intent classifier. |
| [`speculator.py`](../../agent_baton/core/predict/speculator.py) | Launches speculative computation. |
| [`accept.py`](../../agent_baton/core/predict/accept.py) | Accept/reject heuristics for speculative work. |

### `core/gates/` — CI gate runners

| Module | Class | Purpose |
|--------|-------|---------|
| [`ci_gate.py`](../../agent_baton/core/gates/ci_gate.py) | `CIGateRunner` (line 171), `CIGateResult` (line 63), `parse_ci_gate_config()` (line 121) | Polls `gh run list/view` every 15s; opt-in CI gate. |

### `core/audit/` — post-hoc compliance

| Module | Class | Purpose |
|--------|-------|---------|
| [`dispatch_verifier.py`](../../agent_baton/core/audit/dispatch_verifier.py) | `DispatchVerifier` | Read-only worktree-isolation compliance (`baton execute verify-dispatch`, `audit-isolation`). |

### `core/exec/` — sandboxed command execution

| Module | Purpose |
|--------|---------|
| [`runner.py`](../../agent_baton/core/exec/runner.py) | Shell command runner with whitelist environment. |
| [`sandbox.py`](../../agent_baton/core/exec/sandbox.py) | Process sandbox helpers. |
| [`script_lint.py`](../../agent_baton/core/exec/script_lint.py) | Lints command scripts before execution. |
| [`auditor_gate.py`](../../agent_baton/core/exec/auditor_gate.py) | Auditor-driven execution gate. |

### `core/knowledge/` — knowledge lifecycle

| Module | Purpose |
|--------|---------|
| [`lifecycle.py`](../../agent_baton/core/knowledge/lifecycle.py) | Pack create/update/retire lifecycle. |
| [`effectiveness.py`](../../agent_baton/core/knowledge/effectiveness.py) | Knowledge-effectiveness telemetry. |
| [`ab_testing.py`](../../agent_baton/core/knowledge/ab_testing.py) | A/B testing of knowledge variants. |
| [`adr_harvester.py`](../../agent_baton/core/knowledge/adr_harvester.py) | Mines ADRs into knowledge docs. |
| [`review_harvester.py`](../../agent_baton/core/knowledge/review_harvester.py) | Mines code reviews into knowledge docs. |
| [`codebase_brief.py`](../../agent_baton/core/knowledge/codebase_brief.py) | Generates codebase brief documents. |

### `core/immune/` — incident detection

| Module | Purpose |
|--------|---------|
| [`daemon.py`](../../agent_baton/core/immune/daemon.py) | Long-running incident-detection daemon. |
| [`triage.py`](../../agent_baton/core/immune/triage.py) | Auto-triages new incidents. |
| [`scheduler.py`](../../agent_baton/core/immune/scheduler.py) | Schedules sweeps. |
| [`sweeper.py`](../../agent_baton/core/immune/sweeper.py) | Periodic state sweeper. |
| [`cache.py`](../../agent_baton/core/immune/cache.py) | Decision cache. |

### `core/release/` — release readiness

| Module | Purpose |
|--------|---------|
| [`readiness.py`](../../agent_baton/core/release/readiness.py) | Release-readiness assessment. |
| [`mrp.py`](../../agent_baton/core/release/mrp.py) | Minimum Release Plan generator. |
| [`notes.py`](../../agent_baton/core/release/notes.py) | Auto-generated release notes. |
| [`profile_checker.py`](../../agent_baton/core/release/profile_checker.py) | Deployment-profile compatibility check. |
| [`conflict_predictor.py`](../../agent_baton/core/release/conflict_predictor.py) | Predicts merge conflicts before release. |

### `core/specs/` — spec storage

| Module | Purpose |
|--------|---------|
| [`store.py`](../../agent_baton/core/specs/store.py) | Spec document store. |

### `core/config/` — project config

| Module | Class | Purpose |
|--------|-------|---------|
| [`project_config.py`](../../agent_baton/core/config/project_config.py) | `ProjectConfig` | Optional `baton.yaml` loader (walks up from cwd). |

---

## `agent_baton/api/` — Layer 4a

`create_app()` factory in [`api/server.py`](../../agent_baton/api/server.py)
returns a FastAPI application. Singleton DI lives in
[`api/deps.py`](../../agent_baton/api/deps.py).

| Subdirectory | Modules |
|--------------|---------|
| [`api/middleware/`](../../agent_baton/api/middleware/) | `auth.py` (`TokenAuthMiddleware`), `cors.py` (`configure_cors()`), `user_identity.py` (`UserIdentityMiddleware`) |
| [`api/routes/`](../../agent_baton/api/routes/) | `health.py` (2), `plans.py` (2), `executions.py` (6), `agents.py` (2), `observe.py` (3), `decisions.py` (3), `events.py` (1), `webhooks.py` (3), `pmo.py` (36), `pmo_h3.py` (6), `learn.py` (5) |
| [`api/models/`](../../agent_baton/api/models/) | `requests.py` (Pydantic request bodies), `responses.py` (Pydantic responses) |
| [`api/webhooks/`](../../agent_baton/api/webhooks/) | `dispatcher.py` (`WebhookDispatcher`), `registry.py` (`WebhookRegistry`), `payloads.py` |

Endpoint count: 64 across 10 main route modules + 6 H3-PMO endpoints.

---

## `agent_baton/cli/` — Layer 4b

[`cli/main.py`](../../agent_baton/cli/main.py) auto-discovers commands
via `pkgutil.iter_modules` from
[`cli/commands/`](../../agent_baton/cli/commands/). Each module exports
`register(subparsers)` and `handler(args)`.

| Group | Directory | Top commands |
|-------|-----------|--------------|
| Execution | [`commands/execution/`](../../agent_baton/cli/commands/execution/) | `execute`, `plan`, `status`, `daemon`, `async`, `decide` |
| Observability | [`commands/observe/`](../../agent_baton/cli/commands/observe/) | `dashboard`, `trace`, `usage`, `telemetry`, `context-profile`, `retro`, `cleanup`, `migrate-storage`, `context`, `query` |
| Governance | [`commands/govern/`](../../agent_baton/cli/commands/govern/) | `classify`, `compliance`, `policy`, `escalations`, `validate`, `spec-check`, `detect` |
| Improvement | [`commands/improve/`](../../agent_baton/cli/commands/improve/) | `scores`, `evolve`, `patterns`, `budget`, `changelog`, `anomalies`, `experiment`, `improve`, `learn` |
| Distribution | [`commands/distribute/`](../../agent_baton/cli/commands/distribute/) | `package`, `publish`, `pull`, `verify-package`, `install`, `transfer` |
| Agents | [`commands/agents/`](../../agent_baton/cli/commands/agents/) | `agents`, `route`, `events`, `incident` |
| Top-level | [`commands/`](../../agent_baton/cli/commands/) | `pmo`, `sync`, `query`, `source`, `serve`, `beads`, `uninstall` |

The CLI output contract is `_print_action()` at
[`cli/commands/execution/execute.py:568`](../../agent_baton/cli/commands/execution/execute.py)
— the public API surface read by Claude.

---

## Cross-cutting load-bearing files

If you change one of these you change a contract.

| File | What it defines |
|------|----------------|
| [`agent_baton/__init__.py`](../../agent_baton/__init__.py) | The package's public re-exports. |
| [`agent_baton/core/__init__.py`](../../agent_baton/core/__init__.py) | The core layer's re-exports + layer documentation. |
| [`agent_baton/models/execution.py`](../../agent_baton/models/execution.py) | The plan and state shape. |
| [`agent_baton/core/engine/protocols.py`](../../agent_baton/core/engine/protocols.py) | The runtime↔engine contract (`ExecutionDriver`). |
| [`agent_baton/core/storage/protocol.py`](../../agent_baton/core/storage/protocol.py) | The persistence contract (`StorageBackend`). |
| [`agent_baton/cli/commands/execution/execute.py`](../../agent_baton/cli/commands/execution/execute.py) | The Claude-facing wire format (`_print_action()`). |
