# Persona Journey Validation: James (Engineering Manager) & David (Compliance/Security)

> Audit date: 2026-04-16
> Method: Static code analysis of agent-baton codebase
> Evaluator: Agent-baton internal quality review

---

## Rating Key

| Rating | Meaning |
|--------|---------|
| **WORKS** | Feature exists and would satisfy the persona |
| **PARTIAL** | Feature exists but gaps would concern the persona |
| **BLOCKED** | Feature doesn't exist or wouldn't pass evaluation |
| **UNKNOWN** | Can't determine from code alone |

---

## James's Journey (Engineering Manager)

James oversees 3 teams and needs visibility + governance for an agent program.

### Evaluation Phase

#### 1. PMO Dashboard — real-time status?

**WORKS**

The PMO UI is a full React/Vite application in `pmo-ui/src/`.  `App.tsx` renders
two views: a KanbanBoard and a ForgePanel (plan creation).  The board connects
to a Server-Sent Events endpoint (`/api/v1/pmo/events`) for real-time push
updates via `usePmoBoard.ts`.  When SSE is connected, the `ConnectionIndicator`
shows "Live" with a green dot.  On SSE failure, it falls back to 5-second
polling.

**Evidence:**
- `pmo-ui/src/hooks/usePmoBoard.ts` lines 92-127: SSE connection with
  exponential backoff reconnection, fallback polling at 5s.
- `pmo-ui/src/components/KanbanBoard.tsx` line 584-625: `ConnectionIndicator`
  component shows "Live" / "Connecting" / "Reconnecting" states.

---

#### 2. Approval workflow configuration?

**PARTIAL**

Approval workflows exist but configuration is plan-driven, not admin-configured.

- **Approval gates are per-phase:** Plan phases have `approval_required` (bool)
  and `approval_description` fields in `plan_phases` schema table.
- **Intervention level:** Plans accept `--intervention low|medium|high` to control
  escalation frequency.
- **Escalation management:** `core/govern/escalation.py` provides
  `EscalationManager` for agent-raised escalations with question/answer protocol.
- **Decision management:** `core/runtime/decisions.py` provides `DecisionManager`
  for async/daemon execution decisions with options and rationale.

**Gaps:**
- No named approvers (no `approved_by`, `approver_role` fields in the
  `approval_results` schema).  The approval model records `phase_id`, `result`,
  `feedback`, and `decided_at` but not WHO approved.
- No timeout configuration or escalation chain ("if not approved in 2 hours,
  escalate to VP").
- No role-based approval routing ("David must approve all HIGH-risk tasks").
- Configuration is embedded in plan generation, not in an admin UI or config file.

**Evidence:**
- `agent_baton/core/storage/schema.py` lines 339-347: `approval_results` table --
  `task_id`, `phase_id`, `result`, `feedback`, `decided_at`.  No approver identity.
- `agent_baton/models/execution.py` lines 843-861: `ApprovalResult` dataclass
  has `phase_id`, `result`, `feedback`, `decided_at`.  No `approved_by`.

---

#### 3. Cost data / token tracking?

**WORKS**

Token costs are tracked at multiple levels and queryable.

- **Per-agent usage:** `agent_usage` table stores `estimated_tokens`,
  `duration_seconds`, `model`, `steps`, `retries` per agent per task.
- **Usage records:** `usage_records` table aggregates per-task.
- **CLI queries:** `baton query cost-by-type` and `baton query cost-by-agent`
  provide token cost breakdowns.  `baton usage` provides summary stats.
- **Central analytics:** `v_cost_by_task_type` SQL view in central.db aggregates
  tokens across projects.
- **Budget recommendations:** `budget_recommendations` table stores per-task-type
  recommendations with `avg_tokens_used`, `median_tokens_used`, `p95_tokens_used`,
  `potential_savings`.

**Gap:** Token estimates are not converted to dollar amounts.  James would need
to manually compute costs from token counts using provider pricing.  No built-in
price-per-token configuration.

**Evidence:**
- `agent_baton/cli/commands/observe/query.py` lines 387-411: `cost-by-type` and
  `cost-by-agent` subcommands with table/json/csv output.
- `agent_baton/cli/commands/observe/usage.py` lines 75-103: summary stats with
  `total_estimated_tokens`.
- `agent_baton/core/storage/schema.py` lines 1366-1376: `v_cost_by_task_type`
  analytics view.

---

#### 4. Slack integration?

**WORKS**

Full webhook infrastructure with Slack Block Kit formatting.

- **Webhook registry:** `api/webhooks/registry.py` persists subscriptions to
  `webhooks.json` with glob-pattern event matching.
- **Webhook dispatcher:** `api/webhooks/dispatcher.py` subscribes to EventBus
  `"*"`, delivers events via HTTP POST with HMAC-SHA256 signing, retry with
  exponential backoff (5s/30s/300s), auto-disable after 10 consecutive failures.
- **Slack payloads:** `api/webhooks/payloads.py` provides `format_slack()` that
  generates Slack Block Kit messages with header, summary, options list, context
  metadata, and interactive action buttons for `human.decision_needed` events.
- **API endpoints:** `POST /api/v1/webhooks`, `GET /api/v1/webhooks`,
  `DELETE /api/v1/webhooks/{webhook_id}`.

**Gap:** No CLI command for webhook registration (must use API).  No Slack
app manifest or setup guide.

**Evidence:**
- `agent_baton/api/webhooks/payloads.py` lines 32-150: full Slack Block Kit
  layout with action buttons per option.
- `agent_baton/api/webhooks/dispatcher.py` lines 130-184: retry with backoff,
  auto-disable, HMAC signing.
- `agent_baton/api/routes/webhooks.py`: REST CRUD endpoints.

---

#### 5. Audit trail?

**WORKS**

Comprehensive data persistence in SQLite.

- **Execution state:** `executions` table with status, timestamps, phase tracking.
- **Step results:** `step_results` table with agent, status, outcome, files
  changed, commit hash, tokens, duration, retries, errors, deviations.
- **Gate results:** `gate_results` table with pass/fail, output, timestamp.
- **Approval results:** `approval_results` with result, feedback, timestamp.
- **Events:** `events` table with topic, sequence, timestamp, JSON payload.
- **Traces:** `traces` + `trace_events` tables with full lifecycle events.
- **Mission log:** `mission_log_entries` with agent assignments, results, files,
  decisions, issues, handoff notes, commit hashes.
- **Amendments:** `amendments` table tracking plan changes.
- **Telemetry:** `telemetry` table with per-agent event-level data.
- **Retrospectives:** `retrospectives` + `retrospective_outcomes` with
  post-mortem analysis.

**Evidence:**
- `agent_baton/core/storage/schema.py` lines 197-648: 24 tables in
  `PROJECT_SCHEMA_DDL`.

---

### Operational Use

#### 6. Morning dashboard — Kanban columns?

**WORKS**

The KanbanBoard renders 5 columns, though with different labels than James
might expect.

| James expects | Actual column | Match? |
|---------------|---------------|--------|
| Planned | `queued` ("Plan ready, awaiting execution slot") | Close |
| In Progress | `executing` ("Baton steps actively running") | Yes |
| Pending Gate | `awaiting_human` ("Interactive step paused for input") | Close |
| Complete | `deployed` ("Complete -- ADO synced") | Yes |
| Failed | (No separate column) | Missing |

**Gap:** No dedicated "Failed" column.  Failed tasks show as cards with error
badges in their current column.  No dedicated "Validating" vs "Pending Gate"
distinction from James's perspective -- `validating` is "Test suites, baseline
comparison" which covers gate checks.

**Evidence:**
- `pmo-ui/src/styles/tokens.ts` lines 28-34: `COLUMNS` array with 5 entries:
  `queued`, `executing`, `awaiting_human`, `validating`, `deployed`.

---

#### 7. Pending approvals via `baton decide --list`?

**WORKS**

`baton decide --list` lists pending decision requests.  `baton decide --show ID`
shows full details.  `baton decide --resolve ID --option OPTION --rationale TEXT`
resolves with logged rationale.

The PMO UI also has a `GateApprovalPanel` component that renders approve/reject
controls directly on cards in the `awaiting_human` column with inline review
context.

**Evidence:**
- `agent_baton/cli/commands/execution/decide.py` lines 56-110: full
  list/show/resolve handlers.
- `pmo-ui/src/components/GateApprovalPanel.tsx`: inline approve/reject with
  required rejection reason.

---

#### 8. Gate failure notifications pushed via webhooks?

**WORKS**

The webhook dispatcher subscribes to all EventBus events (`"*"`).  Gate failures
are published as events (topic `gate.failed` or similar).  Any registered webhook
with a matching pattern (e.g., `gate.*` or `*`) would receive the notification.
Slack-formatted payloads are generated for `human.decision_needed` events
specifically.

**Gap:** Gate failure events don't get Slack Block Kit formatting -- only
`human.decision_needed` does.  Gate failures would arrive as generic JSON payloads
to Slack, which would display poorly without a custom formatter.

**Evidence:**
- `agent_baton/api/webhooks/dispatcher.py` line 90: subscribes to `"*"` on EventBus.
- `agent_baton/api/webhooks/payloads.py` lines 53-69: non-decision events get
  fallback plain text attachment.

---

#### 9. Monthly cost reports — cost-per-task?

**PARTIAL**

Cost data exists but reporting is CLI-only and token-based, not dollar-based.

- `baton query cost-by-type --format csv` exports token costs by task type.
- `baton query cost-by-agent --days 30 --format csv` exports agent costs.
- `baton usage --recent 50` lists task records with agents and gate stats.
- Central analytics view `v_cost_by_task_type` aggregates cross-project.

**Gaps:**
- No dollar conversion (no price-per-token config anywhere in the codebase).
- No monthly aggregation query (would need ad-hoc SQL:
  `baton query --sql "SELECT strftime('%Y-%m', timestamp) ..."`).
- No executive-formatted report template.

**Evidence:**
- `agent_baton/cli/commands/observe/query.py` lines 387-411.

---

#### 10. Executive reporting / export?

**PARTIAL**

The PMO UI has a `DataExport` component that exports to CSV, JSON, or Markdown.
The Markdown export generates a "Baton Portfolio Report" with summary table,
program health, and plans table.

**Works:**
- CSV export with all card fields + program health data.
- JSON export with structured data.
- Markdown report with summary metrics and tables.
- `AnalyticsDashboard` component with pipeline distribution, program health,
  agent utilization, risk distribution.

**Gaps:**
- No PDF export.
- No executive summary template with charts/visuals.
- Export is from the PMO UI only -- no CLI `baton export` command.
- The markdown report is functional but not polished for VP-level presentation.

**Evidence:**
- `pmo-ui/src/components/DataExport.tsx` lines 32-62: CSV/JSON/Markdown export
  with download.
- `pmo-ui/src/components/AnalyticsDashboard.tsx` lines 27-226: full analytics
  modal.

---

### James's Dealbreakers Assessment

| Dealbreaker | Status | Notes |
|-------------|--------|-------|
| No real-time dashboard | **CLEAR** | SSE-based real-time updates with polling fallback |
| No cost controls | **CLEAR (partial)** | Token tracking + budget recommendations exist; no dollar amounts |
| No Slack integration | **CLEAR** | Full webhook + Slack Block Kit payload formatter |
| Complex approval config | **CONCERN** | Approval exists but no named approvers, no timeout escalation |
| No export for leadership | **CLEAR (partial)** | CSV/JSON/MD export exists; no PDF, no polished executive template |

**Overall for James: PARTIAL PASS.** The product has strong foundations for his
needs.  The PMO dashboard, real-time updates, cost tracking, and Slack
integration would impress during evaluation.  The approval workflow gaps (no named
approvers, no escalation chain) and the lack of polished executive reporting would
require follow-up.

---

## David's Journey (Compliance/Security)

David is a security engineer at a regulated company. He needs audit trails and
governance controls.

### Policy Definition

#### 1. Custom risk rules — configure which code paths trigger HIGH risk?

**WORKS**

The `DataClassifier` in `core/govern/classifier.py` uses keyword lists and
file-path pattern matching.  The `PolicyEngine` in `core/govern/policy.py`
supports custom presets persisted as JSON files under `.claude/policies/`.

- 5 built-in presets: `standard_dev`, `data_analysis`, `infrastructure`,
  `regulated`, `security`.
- Custom presets loaded from `.claude/policies/*.json` take precedence over
  built-in presets.
- Rules support: `path_block`, `path_allow`, `tool_restrict`, `require_agent`,
  `require_gate` with `block` or `warn` severity.
- Scope matching via fnmatch patterns against agent names.

**Gap:** The classifier keywords are hardcoded in `classifier.py`.  Custom risk
rules are about policy enforcement (what happens after classification), not about
classification itself.  David cannot add custom keyword signals without modifying
source code.

**Evidence:**
- `agent_baton/core/govern/policy.py` lines 406-457: `PolicyEngine` with
  `load_preset()`, `save_preset()`, `list_presets()`.
- `agent_baton/core/govern/classifier.py` lines 96-129: hardcoded signal lists.

---

#### 2. Named required approver for HIGH-risk tasks?

**BLOCKED**

No named approver mechanism exists.  The `approval_results` table records
`phase_id`, `result`, `feedback`, `decided_at` but has no `approved_by` or
`approver_role` field.  The `ApprovalResult` dataclass mirrors this -- four fields,
no identity.

There is no configuration mechanism to say "David must approve all HIGH-risk tasks"
or "security team must approve changes to auth/".

**Evidence:**
- `agent_baton/models/execution.py` lines 843-861: `ApprovalResult` -- no
  approver identity.
- `agent_baton/core/storage/schema.py` lines 339-347: `approval_results` table
  schema -- no `approved_by`.

---

#### 3. Security policy for auditor agent?

**WORKS**

The auditor agent (`agents/auditor.md`) is a fully defined subagent with:
- Veto authority over the orchestrator.
- Three modes: pre-execution review, mid-execution checkpoint, post-execution
  audit.
- Structured output with Guardrails Report, Permission Manifest, Compliance Notes.
- Per-agent trust levels (Full Autonomy / Supervised / Restricted / Plan Only).

The `regulated` policy preset in `core/govern/policy.py` enforces
`require_agent: auditor` and `require_agent: subject-matter-expert` as blocking
rules.

David can provide reference material to the auditor via `--knowledge` flag on
`baton plan` which attaches documents to the plan that agents receive in context.

**Evidence:**
- `agents/auditor.md` lines 1-260: complete auditor definition with 3 modes.
- `agent_baton/core/govern/policy.py` lines 296-343: `regulated` preset
  requiring SME + auditor.

---

#### 4. Custom gate scripts for security scanning?

**WORKS**

Plan phases support `gate_type`, `gate_command`, `gate_description`, and
`gate_fail_on` fields.  The planner generates gate commands (e.g.,
`pytest tests/ -x`) and the executor runs them via shell.  Custom gate commands
can be specified per phase.

The `security` policy preset requires a `no_hardcoded_credentials` gate.  The
`infrastructure` preset requires a `rollback_plan` gate.

**Gap:** No pre-built security scanning gate templates (e.g., `semgrep`, `trivy`,
`bandit`).  David would need to configure gate commands manually.

**Evidence:**
- `agent_baton/core/storage/schema.py` lines 239-250: `plan_phases` table with
  `gate_type`, `gate_command`, `gate_description`, `gate_fail_on`.

---

#### 5. Documentation requirements per risk tier?

**PARTIAL**

Risk tiers (LOW/MEDIUM/HIGH/CRITICAL) map to guardrail presets which enforce
different levels of scrutiny:

- LOW: Standard Development -- basic path blocks, tool restrictions.
- MEDIUM: Standard Development -- same as LOW but flagged.
- HIGH: Regulated/Security/Infrastructure presets -- require auditor, SME,
  specific gates.
- CRITICAL: Regulated Data -- auto-escalated when 3+ regulated/PII signals.

**Gap:** No configurable "documentation requirements per tier" (e.g., "CRITICAL
tasks must produce an architecture decision record").  The compliance report
generator exists but is not wired to risk tiers as a mandatory gate.

**Evidence:**
- `agent_baton/core/govern/classifier.py` lines 80-85: `_RISK_ORDINAL` mapping.
- `agent_baton/core/govern/policy.py` lines 170-394: five preset definitions.

---

### Pilot Oversight

#### 6. HIGH-risk approvals visible?

**WORKS**

- `baton decide --list` shows all pending decisions with type and summary.
- `baton decide --show ID` shows full details including options, deadline, context.
- The PMO UI shows cards in the `awaiting_human` column with an "Awaiting
  Approval" badge and a `GateApprovalPanel` for inline review.
- The `AnalyticsDashboard` shows risk distribution across all tasks.

**Evidence:**
- `pmo-ui/src/components/GateApprovalPanel.tsx`: inline approval UI with context
  loading, approve/reject forms, required rejection reason.
- `pmo-ui/src/components/KanbanBoard.tsx` lines 343-368: "awaiting human" count
  with pulsing indicator.

---

#### 7. Auditor issues APPROVE/VETO verdicts?

**WORKS**

The auditor agent definition specifies explicit verdicts at each mode:

- Pre-execution: "Approved" / "Approved With Conditions" / "Blocked"
- Mid-execution: "CONTINUE" / "PAUSE" / "HALT"
- Post-execution: "SHIP" / "SHIP WITH NOTES" / "REVISE" / "BLOCK"

The `ComplianceReport` dataclass stores `auditor_verdict` (SHIP/SHIP WITH
NOTES/REVISE/BLOCK) and `auditor_notes`.

**Gap:** The auditor verdict is a convention enforced by the agent's prompt,
not by the engine.  A misconfigured or modified auditor could skip the verdict
step.  No machine-enforced validation that the auditor actually produced a
verdict.

**Evidence:**
- `agents/auditor.md` lines 46-130: structured output templates with verdicts.
- `agent_baton/core/govern/compliance.py` lines 63-96: `ComplianceReport` with
  `auditor_verdict` field.

---

#### 8. Is `baton compliance` a command?

**WORKS**

`baton compliance` is a registered CLI command.

- `baton compliance` -- lists recent compliance reports (default 5).
- `baton compliance --task-id ID` -- shows a specific report.
- `baton compliance --count N` -- lists N recent reports.

Reports are generated by `ComplianceReportGenerator` and stored as markdown files
in `.claude/team-context/compliance-reports/`.

**Gap:** Reports are markdown files on disk, not in SQLite.  Not synced to
central.db.  Not queryable via `baton query`.

**Evidence:**
- `agent_baton/cli/commands/govern/compliance.py` lines 16-48.
- `agent_baton/core/govern/compliance.py` lines 156-287: report generation, save,
  load, list.

---

#### 9. Override a VETO with logged justification?

**PARTIAL**

`baton decide --resolve ID --option approve --rationale "justification text"`
resolves a pending decision with a logged rationale.  The `DecisionResolution`
includes `chosen_option`, `rationale`, `resolved_by`, and timestamp.

However, there is no specific "override VETO" workflow.  If the auditor blocks a
plan, the orchestrator presents it as a decision.  The user can override by
resolving the decision, but:

- There is no audit flag marking it as an "override of auditor VETO"
- There is no configurable policy requiring elevated approval for overrides
- The override is indistinguishable from a normal approval in the audit trail

**Evidence:**
- `agent_baton/core/runtime/decisions.py` lines 80-125: `resolve()` persists
  resolution with option + rationale.
- `agent_baton/models/decision.py`: `DecisionResolution` with `rationale` field.

---

### Incident Response

#### 10. `baton beads graph TASK_ID`?

**WORKS**

`baton beads graph` is a registered subcommand that displays the dependency
graph for a task's beads.  It shows bead-to-bead link relationships with conflict
markers.

- Shows all beads for a task with their links.
- Defaults to the active task if no task ID provided.
- Supports `--task TASK_ID` for specific tasks.

**Evidence:**
- `agent_baton/cli/commands/bead_cmd.py` lines 228-240: `graph` subcommand
  registration.
- `agent_baton/cli/commands/bead_cmd.py` lines 499-529: `_handle_graph()`
  implementation.

---

#### 11. Reconstruct decision chain from traces?

**WORKS**

The trace system records the full lifecycle:

- `traces` table: plan snapshot, start/complete timestamps, outcome.
- `trace_events` table: per-event records with timestamp, event type, agent name,
  phase, step, details JSON, duration.
- `baton trace TASK_ID` renders a timeline view.
- `baton trace --summary TASK_ID` renders a compact summary.
- `events` table: all domain events with topic, sequence, and payload.
- `mission_log_entries`: per-agent logs with assignment, result, decisions, issues.

**Evidence:**
- `agent_baton/cli/commands/observe/trace.py` lines 58-106: trace commands.
- `agent_baton/core/storage/schema.py` lines 484-506: `traces` and `trace_events`
  schema.

---

#### 12. Audit records append-only (immutable)?

**BLOCKED**

The SQLite backend explicitly uses mutable patterns:

- **DELETE-then-INSERT:** The `save_execution()` method deletes all child rows
  (step_results, gate_results, approval_results, etc.) and re-inserts them on
  every save.  This is documented in `sqlite_backend.py` line 17: "DELETE-then-
  INSERT pattern -- for child collections that are fully replaced on each save."
- **INSERT OR REPLACE:** Used for parent tables (executions, plans), which is
  a DELETE + INSERT in SQLite, potentially triggering cascades.
- **UPDATE statements:** Multiple UPDATE patterns exist (57 DELETE/UPDATE/INSERT
  OR REPLACE occurrences in sqlite_backend.py).
- **WAL mode:** Used for concurrent read performance, not for immutability.

There are no application-level immutability constraints, no append-only tables,
no write-ahead audit log that would prevent retroactive modification.

The compliance reports are markdown files on disk (not in SQLite), which are
overwritable.  There is no checksumming, no blockchain-style chaining, and no
database triggers preventing UPDATE/DELETE on audit tables.

**This is a hard dealbreaker for David.**

**Evidence:**
- `agent_baton/core/storage/sqlite_backend.py` lines 1-25: documents
  DELETE-then-INSERT and INSERT OR REPLACE patterns.
- `agent_baton/core/storage/connection.py` lines 1-13: WAL mode documentation
  (performance, not immutability).

---

### Audit Preparation

#### 13. Export audit trail as CSV/PDF?

**PARTIAL**

- `baton query <subcommand> --format csv` exports any query result as CSV.
- `baton query --sql "SELECT ..." --format csv` supports ad-hoc SQL export.
- The PMO UI `DataExport` component exports board data as CSV/JSON/Markdown.

**Gaps:**
- No PDF export anywhere in the codebase.
- No dedicated `baton compliance export` command.
- No pre-built audit report export template combining all audit data (compliance
  report + trace + gate results + approvals) into a single document.
- An auditor preparing for a regulatory review would need to run multiple
  queries and manually assemble the results.

**Evidence:**
- `agent_baton/cli/commands/observe/query.py` lines 554-615: table/json/csv
  renderers.

---

#### 14. Approval records complete?

**BLOCKED**

Approval records are **incomplete** for compliance purposes.

The `approval_results` table stores:
- `task_id` -- which task
- `phase_id` -- which phase
- `result` -- approve/reject/approve-with-feedback
- `feedback` -- optional text
- `decided_at` -- timestamp

**Missing critical fields:**
- No `approved_by` (who made the decision)
- No `justification` (required by most compliance frameworks)
- No `approver_role` (was this person authorized to approve?)
- No `ip_address` or `session_id` (where was the approval made from?)

The `DecisionResolution` model does include `rationale` and `resolved_by`,
but decisions and approvals are separate data models.  Gate approvals via
`baton execute approve` use the incomplete `ApprovalResult` model.

**This would fail a compliance audit.**

**Evidence:**
- `agent_baton/models/execution.py` lines 843-861: `ApprovalResult` -- 4 fields.
- `agent_baton/core/storage/schema.py` lines 339-347: `approval_results` table.

---

#### 15. Risk classification overrides require justification?

**PARTIAL**

The `LearnedOverrides` system in `core/learn/overrides.py` stores
`classifier_adjustments` and writes to `learned-overrides.json`.  This file
records `last_updated` timestamps but:

- No per-change justification field.
- No history of who changed what.
- Overrides are written atomically (temp file + rename) but the file can be
  freely edited.
- No audit trail of classification override history.

The `baton classify` command shows the classification result but does not provide
a mechanism to override it with recorded justification.

**Evidence:**
- `agent_baton/core/learn/overrides.py` lines 36-61: `LearnedOverrides` with
  atomic writes but no change history.

---

### David's Dealbreakers Assessment

| Dealbreaker | Status | Notes |
|-------------|--------|-------|
| Mutable audit trail | **FAIL** | DELETE-then-INSERT + INSERT OR REPLACE; no immutability guarantees |
| Risk overrides without justification | **CONCERN** | LearnedOverrides has no per-change justification or history |
| No independent auditor verification | **CLEAR** | Auditor agent has veto authority and runs independently |
| Secrets/PII in execution traces | **CONCERN** | No evidence of redaction/masking in trace/event persistence |
| No way to scope agent access | **CLEAR** | Policy engine + allowed/blocked paths + tool restrictions |
| Silent failures (auditor fails without notice) | **CONCERN** | Auditor failure would be a step failure; webhook notifications available but not auditor-specific |

**Detail on "Secrets/PII in traces":**  The `trace_events` and `events` tables
store `details` as free-form JSON.  The `step_results` table stores `outcome`
as free text.  There is no redaction layer.  If an agent encounters credentials
or PII during execution and includes them in its outcome report, they persist
in the database.  Files in `core/observe/` reference "PII" and "sensitive" in
`context_profiler.py` and `retrospective.py` but only for classification, not
for redaction from stored data.

**Overall for David: FAIL.**  The product has impressive governance structure
(classifier, policy engine, auditor agent, compliance reports, gate system) but
fails on two non-negotiable compliance requirements:

1. **Mutable audit trail** -- The DELETE-then-INSERT pattern means historical
   audit data can be overwritten.  An external auditor would reject this
   immediately.
2. **Incomplete approval records** -- No approver identity in approval records
   makes it impossible to prove authorization chain.

---

## Summary Matrix

### James (Engineering Manager)

| # | Journey Step | Rating | Key Finding |
|---|-------------|--------|-------------|
| 1 | PMO Dashboard real-time | **WORKS** | SSE + polling fallback |
| 2 | Approval workflows | **PARTIAL** | Exists but no named approvers, no escalation chain |
| 3 | Cost data | **WORKS** | Token tracking at all levels; no dollar conversion |
| 4 | Slack integration | **WORKS** | Full webhook + Block Kit formatting |
| 5 | Audit trail | **WORKS** | 24 tables, comprehensive |
| 6 | Kanban columns | **WORKS** | 5 columns; slightly different labels; no "Failed" column |
| 7 | Pending approvals | **WORKS** | CLI + PMO UI inline approval |
| 8 | Gate failure notifications | **WORKS** | Webhook delivery; generic format for non-decision events |
| 9 | Monthly cost reports | **PARTIAL** | Data exists; no dollar amounts, no monthly aggregation |
| 10 | Executive reporting | **PARTIAL** | CSV/JSON/MD export; no PDF, no polished templates |

### David (Compliance/Security)

| # | Journey Step | Rating | Key Finding |
|---|-------------|--------|-------------|
| 1 | Custom risk rules | **WORKS** | Policy engine with custom presets; classifier keywords hardcoded |
| 2 | Named required approver | **BLOCKED** | No approver identity in approval records |
| 3 | Auditor policy docs | **WORKS** | Full auditor agent with reference material support |
| 4 | Custom gate scripts | **WORKS** | Per-phase gate_command with shell execution |
| 5 | Per-tier doc requirements | **PARTIAL** | Tiers exist; no configurable requirements per tier |
| 6 | HIGH-risk approvals visible | **WORKS** | CLI + PMO UI with inline review |
| 7 | Auditor APPROVE/VETO | **WORKS** | Structured verdicts; convention-enforced, not machine-enforced |
| 8 | `baton compliance` command | **WORKS** | Lists and shows compliance reports |
| 9 | Override VETO with justification | **PARTIAL** | Decision resolution has rationale; not flagged as override |
| 10 | Bead graph for task | **WORKS** | `baton beads graph TASK_ID` implemented |
| 11 | Decision chain from traces | **WORKS** | Full lifecycle in traces + events + mission log |
| 12 | Append-only audit records | **BLOCKED** | DELETE-then-INSERT; fully mutable |
| 13 | Export audit trail CSV/PDF | **PARTIAL** | CSV via query; no PDF; no consolidated audit export |
| 14 | Complete approval records | **BLOCKED** | Missing approved_by, justification, approver_role |
| 15 | Override justification required | **PARTIAL** | No per-change history or justification in overrides |

---

## Recommended Fixes (Priority Order)

### For David (compliance-critical)

1. **Add `approved_by` to `approval_results`** -- Add `approved_by TEXT NOT NULL
   DEFAULT ''` and `justification TEXT NOT NULL DEFAULT ''` columns.  Update
   `ApprovalResult` model.  Migration v10.
2. **Append-only audit tables** -- Create separate `audit_log` table that is
   INSERT-only (application-level enforcement + SQLite trigger to prevent
   UPDATE/DELETE).  Log all approval, gate, and compliance events immutably.
3. **Trace redaction layer** -- Add configurable pattern matching to strip
   secrets/PII from `outcome`, `details`, and `payload` fields before
   persistence.
4. **Classification override audit** -- Add `override_history` to
   `learned-overrides.json` with timestamp, previous value, new value, and
   required justification text.

### For James (usability)

5. **Named approvers** -- Add `approver_config` table or JSON with role-to-person
   mappings.  Wire into approval routing.
6. **Dollar cost conversion** -- Add `price_config` with per-model per-token
   pricing.  Expose `cost-by-type --dollars` flag.
7. **PDF export** -- Add `weasyprint` or `reportlab` dependency for compliance
   report PDF generation.
8. **Executive report template** -- Pre-built markdown-to-HTML template with
   charts for leadership presentation.
