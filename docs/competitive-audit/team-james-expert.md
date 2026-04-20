# Team Audit: James (Engineering Manager) + Agent-Baton Expert

> Audit date: 2026-04-16
> Method: Structured dialogue -- James asks manager-level questions, Expert investigates codebase with file:line evidence
> Comparison baseline: docs/competitive-audit/persona-james-david.md (solo audit)

---

## Rating Key

| Rating | Meaning |
|--------|---------|
| **WORKS** | Feature exists and satisfies the persona's need |
| **PARTIAL** | Feature exists but gaps would concern the persona |
| **BLOCKED** | Feature missing or would not pass evaluation |

---

## Item 1: Monday Morning Dashboard

### James asks:
"I open the PMO dashboard with my coffee on Monday morning. Walk me through exactly what I see -- every component, every data point. Is the information density right for a manager who oversees 3 teams and 12 engineers?"

### Expert investigates:

The PMO UI is a full-page React SPA served by `baton pmo serve` (FastAPI + Vite). On load, `App.tsx` renders a top nav bar with two tabs: **AI Kanban** (default) and **The Forge** (plan creation). The Kanban view is the Monday morning dashboard.

**What James sees from top to bottom:**

1. **Health Bar** (`HealthBar.tsx`): A horizontal row of program cards, one per program (e.g., "Team Alpha", "Team Bravo"). Each shows:
   - Program name with color dot
   - Completion percentage with progress bar
   - Breakdown: "N plans, X active, Y done, Z blocked, W failed"
   - Clickable -- filters the board to that program
   - Data inconsistency warning icon when counts don't sum

2. **Toolbar** (`KanbanBoard.tsx` lines 131-407): A dense control bar with:
   - Program filter buttons (All, Team Alpha, Team Bravo, etc.)
   - Search input ("Search cards...")
   - Sort dropdown: Priority / Last Updated / Risk / Progress
   - Advanced Filters toggle (risk level, agent name, date range)
   - Signals toggle with red badge count of open signals
   - Analytics button (opens `AnalyticsDashboard` modal)
   - Export button (CSV/JSON/Markdown via `DataExport`)
   - Linked Items button (ADO/GitHub/Jira/Linear items via `ExternalItemsPanel`)
   - Status indicators: "N awaiting" (pulsing orange), "N executing", "X/Y plans"
   - Last-updated timestamp
   - Connection indicator: "Live" (green, SSE) / "Connecting" / "Reconnecting"
   - "+ New Plan" button

3. **Kanban Columns** (5 columns defined in `tokens.ts` lines 28-34):
   - **Queued** (gray): "Plan ready, awaiting execution slot"
   - **Executing** (yellow): "Baton steps actively running"
   - **Awaiting Human** (orange): "Interactive step paused for input"
   - **Validating** (purple): "Test suites, baseline comparison"
   - **Deployed** (green): "Complete -- ADO synced"

4. **Cards** (`KanbanCard.tsx`): Each card shows:
   - Program color dot + title (2-line clamp)
   - ADO external ID (or abbreviated internal ID)
   - Priority chip (P0 = red, P1 = orange)
   - Risk chip (HIGH = red, MEDIUM = yellow; LOW suppressed)
   - Step progress pips (visual dots, up to 12) + "N/M" count
   - Current phase text with colored left border
   - Error text in red when present
   - Footer: project_id, first 2 agent names + overflow count, last-updated time
   - **Expandable detail** (click): program, gates passed, full agent list as chips, action buttons (Execute, Monitor, Re-forge, Edit Plan, View Plan), inline `GateApprovalPanel` when awaiting_human

5. **Modals available from toolbar:**
   - `AnalyticsDashboard`: 4 summary metric cards (Total Plans, Success Rate, Active, Blocked/Failed) + Pipeline Distribution bar chart + Program Health progress bars + Agent Utilization table (agent, tasks, success, failed, rate%) + Risk Distribution circles
   - `DataExport`: CSV/JSON/Markdown with scope (all/filtered) and health data toggle
   - `ExternalItemsPanel`: Linked ADO, GitHub, Jira, Linear items

6. **Real-time updates** (`usePmoBoard.ts`):
   - SSE connection to `/api/v1/pmo/events` with exponential backoff (1s-30s)
   - Safety-net polling at 15s (SSE alive) or 5s (SSE down)
   - Connection mode tracked and displayed

### James probes deeper:
"This looks comprehensive, but I have 3 teams. Can I see per-team views? And what about the things I'd look for first -- stalled work, things waiting on me, cost this week?"

### Expert discovers second-pass findings:

**Per-team filtering:** The `HealthBar` is clickable -- clicking a program name filters the entire board to that program. Program filter buttons are also in the toolbar. However, `ProgramHealth` data comes from the backend which groups by `program` field on PMO cards. James would need to ensure his 3 teams map to 3 programs during project registration (`baton pmo add --program "Team Alpha"`). This is not automatic -- programs are user-defined strings.

**Stalled work detection:** `baton query stalled --hours 24` (CLI only) returns executions not updated recently. The PMO dashboard does NOT surface stalled cards automatically -- there is no "stale" badge or auto-detection in the UI. A card could sit in "executing" for days without visual warning beyond its unchanged `updated_at` timestamp.

**Awaiting-me indicator:** The pulsing orange "N awaiting" badge in the toolbar is prominent. The `GateApprovalPanel` is inline on expanded cards. But there is no "My approvals" filter -- James sees ALL pending items, not just those requiring HIS attention (because there is no approver identity system).

**Cost this week:** Not available in the PMO UI. The `AnalyticsDashboard` shows aggregate totals (success rate, active plans) but no time-windowed cost data. Token costs are only available via CLI: `baton query cost-by-agent --days 7`.

**NEW finding vs solo audit:** The solo audit noted "5 columns" and "no Failed column." The team investigation reveals that the column set is actually well-designed for a manager: errors show as red text on cards within their current column, and the `AnalyticsDashboard` metric card shows "Blocked/Failed" count. More importantly, the solo audit missed the `ExecutionProgress` component -- clicking "Monitor" on an active card opens a real-time progress modal with a step-by-step event log polling every 3 seconds, including phase, elapsed time, and per-step status/agent/message. This is significant for a manager who wants to spot-check what an agent is doing right now.

### Verdict: WORKS (with caveats)

**Delta from solo audit:** Solo rated this WORKS. Team audit confirms WORKS but identifies 3 missing capabilities the solo audit did not flag:
1. No stale-card detection in the UI (only CLI)
2. No per-person approval filtering (no approver identity)
3. No time-windowed cost data in the dashboard

---

## Item 2: Approval Delegation

### James asks:
"I want my senior engineers to handle MEDIUM-risk approvals while I handle HIGH-risk only. Can I configure this? Walk me through how approval routing actually works."

### Expert investigates:

Tracing the approval flow from end to end:

1. **Plan creation** (`core/engine/planner.py`): Phases get `approval_required: bool` and `approval_description` fields. The planner sets these based on the plan's risk level and phase type, not based on who should approve.

2. **Execution engine** (`core/engine/executor.py`): When a phase with `approval_required=True` completes, the engine sets status to `"approval_pending"` and emits an APPROVAL action with `approval_description` and options (typically `approve`, `reject`, `approve-with-feedback`).

3. **CLI recording**: `baton execute approve --phase-id N --result approve` records the result. The `ApprovalResult` model (`execution.py` line 843) has: `phase_id`, `result`, `feedback`, `decided_at`. No `approved_by` field.

4. **Schema** (`schema.py` lines 338-347): `approval_results` table has: `task_id`, `phase_id`, `result`, `feedback`, `decided_at`. No approver identity column.

5. **Decision system** (`core/runtime/decisions.py`): The `DecisionManager` has a richer model -- `DecisionResolution` includes `resolved_by` and `rationale`. But decisions and approvals are separate systems. Gate approvals go through the simpler `ApprovalResult` path.

6. **Intervention level**: `baton plan --intervention low|medium|high` controls escalation frequency but does NOT route approvals to specific people. It affects how many approval gates the planner inserts, not who reviews them.

7. **Policy engine** (`core/govern/policy.py`): The `require_agent` rule type can mandate that specific agents (e.g., auditor, security-reviewer) are in the plan. But there is no `require_approver` rule type that routes human approvals by risk level.

8. **Webhook payloads** (`api/webhooks/payloads.py`): The Slack Block Kit formatter creates action buttons for `human.decision_needed` events. But the webhook subscription is topic-based (`gate.*`, `human.*`), not risk-based or role-based.

### James probes deeper:
"So right now, every approval goes to whoever is running the session? There's no way to say 'MEDIUM goes to Sarah, HIGH goes to me'?"

### Expert discovers:
Correct. The approval system is session-bound, not role-bound. Approvals surface in three places:
- `baton decide --list` in the CLI session
- `GateApprovalPanel` in the PMO UI (anyone with browser access)
- Slack webhook (anyone in the channel)

There is no:
- Role-to-person mapping table
- Risk-to-approver routing
- Per-team approval configuration
- Approval delegation or escalation timeout ("if not approved in 2h, escalate")

The closest workaround: James could set up separate Slack channels per risk level and register different webhooks with different topic patterns. But the webhook topic is `human.decision_needed` -- it does not distinguish MEDIUM from HIGH risk decisions in the event topic.

**NEW finding:** The `DecisionManager` in `decisions.py` does store `resolved_by` in its resolution, unlike the simpler `ApprovalResult`. If James's team uses `baton decide` (the decision pathway) instead of `baton execute approve` (the approval pathway), he gets identity tracking. But these are two separate codepaths and the decision pathway is primarily designed for headless/daemon execution, not for interactive approval workflows. The approval pathway is what the PMO UI uses via `GateApprovalPanel`.

### Verdict: BLOCKED

The feature James needs (risk-based approval delegation) does not exist. No configuration mechanism, no approver routing, no role mapping.

**Delta from solo audit:** Solo rated this PARTIAL ("exists but no named approvers, no escalation chain"). Team audit downgrades to BLOCKED because James's specific requirement -- delegating MEDIUM to seniors while keeping HIGH for himself -- is fundamentally unsupported. The solo audit was too generous; "PARTIAL" implies workarounds exist, but none do.

---

## Item 3: Incident Post-Mortem

### James asks:
"An agent committed code that caused a production bug. I need to walk backward from 'this commit broke prod' to 'here's what the agent was thinking, what it was told to do, and who approved it.' What's the investigation path?"

### Expert investigates:

**Step 1: From commit to task**
`step_results` table stores `commit_hash` per step (`schema.py` line 298). So `baton query --sql "SELECT task_id, step_id, agent_name, outcome FROM step_results WHERE commit_hash = 'abc123'"` returns the task, step, and agent.

**Step 2: Full task timeline**
`baton trace TASK_ID` renders the complete execution timeline from `traces` + `trace_events` tables. Each event has: timestamp, event_type (step.dispatched, step.completed, gate.passed, phase.started, etc.), agent_name, phase, step, details JSON, duration.

**Step 3: What the agent was told**
`baton query task-detail TASK_ID` shows the full plan: every phase, every step with agent assignment, task description, allowed/blocked paths, context files, deliverables. The `plan_steps` table stores `task_description` -- the exact delegation prompt.

**Step 4: Agent's own reasoning**
`mission_log_entries` table stores per-agent records with: assignment, result, files changed, decisions made, issues encountered, handoff notes, commit hash, and `failure_class`. `baton query --sql "SELECT * FROM mission_log_entries WHERE task_id = '...' AND agent_name = '...'"` provides the agent's self-reported reasoning.

**Step 5: Beads (structured memory)**
`beads` table stores agent-produced structured memories: discoveries, decisions, warnings, outcomes. `baton beads show BEAD_ID` or `baton beads graph TASK_ID` shows the dependency graph of what the agent knew and decided.

**Step 6: Who approved?**
`approval_results` table shows: phase_id, result (approve/reject), feedback text, decided_at timestamp. BUT: no `approved_by` field. James cannot prove WHO approved the phase that let the bad code through.

**Step 7: Gate results**
`gate_results` table: gate_type, passed (bool), output text, checked_at. Shows whether tests passed before the code was allowed to proceed.

**Step 8: Events timeline**
`events` table: full domain event stream with topic, sequence, timestamp, payload JSON. Every significant state change is recorded.

### James probes deeper:
"OK so I can reconstruct almost everything. But the approval record has no name -- I can't tell my VP who approved the bad change. And can I do this investigation from the dashboard, or do I need CLI access?"

### Expert discovers:

**Investigation from the PMO UI:** Limited. The card shows current_phase, steps_completed, gates_passed, error, agents. The "Monitor" button (`ExecutionProgress.tsx`) shows the event log. But there is no "Investigation view" or "Post-mortem timeline" in the UI. The full investigation requires CLI commands: `baton trace`, `baton query task-detail`, `baton query --sql`.

**The approval identity gap is real and critical.** The `approval_results` table has no approver. The `DecisionResolution` model does have `resolved_by`, but decisions and approvals are different codepaths. In the common approval flow (user clicks "Approve" in `GateApprovalPanel` or runs `baton execute approve`), no identity is captured.

**NEW finding:** The `retrospectives` table stores post-execution analysis with `what_worked`, `what_didnt`, and outcome assessment. `baton retro TASK_ID` renders this. But retrospectives are generated by the engine after completion, not by the investigating manager. They provide the system's own assessment of what went wrong, which could be useful for post-mortem context. The solo audit did not mention retrospectives as an investigation tool.

**NEW finding:** The `telemetry` table stores per-agent event-level data (timestamps, durations, token usage per event). `baton telemetry --task TASK_ID` would show the granular timeline. Combined with `trace`, this gives second-by-second reconstruction. The solo audit only mentioned traces.

### Verdict: PARTIAL

Strong investigation tooling exists, but (a) requires CLI access, not available from dashboard, and (b) the critical gap of "who approved" means the chain of accountability breaks at the approval step.

**Delta from solo audit:** Solo audit covered traces and decision chains (items 10, 11 in David's journey) but did not walk through the end-to-end investigation path James needs. Team audit reveals that the combination of `commit_hash` in `step_results`, `mission_log_entries`, `beads`, `telemetry`, and `retrospectives` provides a significantly richer investigation path than the solo audit identified. However, the solo audit's "BLOCKED" on approval identity (David item 14) applies here too.

---

## Item 4: Cost Justification Report

### James asks:
"My VP asks 'what did the agent program cost last month and what did we get for it?' Can I produce this report? What data is available vs what I'd need to assemble manually?"

### Expert investigates:

**Data available (automatically collected):**

1. **Per-task token usage** (`usage_records` + `agent_usage` tables): Every execution records total_agents, risk_level, sequencing_mode, gates_passed/failed, outcome. Per-agent: model, steps, retries, estimated_tokens, duration_seconds.

2. **Cross-project aggregation** (`central.db`):
   - `v_cost_by_task_type`: tokens + duration grouped by task summary, per project
   - `v_agent_reliability`: success rates, avg retries, avg tokens per agent
   - `v_project_failure_rate`: failure rates per project
   - CLI: `baton cquery costs --format csv` dumps cross-project token costs

3. **Per-project queries:**
   - `baton query cost-by-type --format csv`: tokens by task type
   - `baton query cost-by-agent --days 30 --format csv`: tokens by agent, last 30 days
   - `baton usage`: summary with total_estimated_tokens
   - `baton query --sql "SELECT ... WHERE timestamp > '2026-03-01'"`: ad-hoc time windows

4. **"What did we get" data:**
   - `baton query tasks --limit 50 --format csv`: task list with outcomes
   - Retrospectives store outcome assessments
   - `step_results` stores files_changed, commit_hash per step
   - `budget_recommendations` stores avg/median/p95 tokens per task type with `potential_savings`

**Data NOT available (must assemble manually):**

1. **Dollar amounts**: No price-per-token configuration anywhere. Token counts must be manually converted using Anthropic pricing. No `cost_dollars` column or computation.

2. **Monthly aggregation**: No built-in monthly rollup. James would need: `baton query --sql "SELECT strftime('%Y-%m', timestamp) AS month, SUM(estimated_tokens) FROM agent_usage GROUP BY month"`.

3. **ROI calculation**: No "time saved" or "value delivered" metric. James would need to estimate baseline (human time) and compare to agent execution time + cost.

4. **Executive report format**: The `DataExport` component generates a Markdown "Baton Portfolio Report" with summary table, program health, and plans table. This is closer to operational status than cost justification. No VP-ready slide format.

### James probes deeper:
"Can I get 'cost per completed task' and compare it across teams?"

### Expert discovers:

**Cost per completed task (single project):**
```sql
baton query --sql "SELECT ur.task_id, ur.outcome, SUM(au.estimated_tokens) as tokens
FROM usage_records ur JOIN agent_usage au ON au.task_id = ur.task_id
WHERE ur.outcome = 'success' GROUP BY ur.task_id"
```

**Cross-project comparison:**
```sql
baton cquery "SELECT project_id, COUNT(DISTINCT task_id) as tasks,
SUM(estimated_tokens) as total_tokens,
AVG(estimated_tokens) as avg_tokens_per_step
FROM agent_usage GROUP BY project_id"
```

These work but require SQL knowledge. No pre-built "cost per completed task by team" dashboard view.

**NEW finding:** The `budget_recommendations` table stores `potential_savings` per task type. This is actually a cost-optimization metric that the solo audit did not surface. `baton query --sql "SELECT task_type, potential_savings FROM budget_recommendations"` shows where the system thinks money could be saved by adjusting model/budget allocations. This would be directly useful for James's VP report.

**NEW finding:** The `AnalyticsDashboard` in the PMO UI shows "Agent Utilization" with per-agent task counts and success rates. Combined with `DataExport` in Markdown format, James could produce a reasonable VP report from the UI alone -- just not one with dollar amounts.

### Verdict: PARTIAL

Rich token data exists and is queryable. The gap is the last mile: no dollar conversion, no monthly rollup, no executive template.

**Delta from solo audit:** Solo rated cost data as WORKS and monthly reports as PARTIAL. Team audit confirms PARTIAL overall but discovers `budget_recommendations.potential_savings` and the cross-project `baton cquery costs` command as significant capabilities the solo audit did not emphasize.

---

## Item 5: Team-Wide Gate Failure Patterns

### James asks:
"I want to know which teams have the highest gate failure rates and why. Can I get this cross-team view?"

### Expert investigates:

**Per-project gate stats:**
`baton query gate-stats` returns gate_type, total, passed, failed, pass_rate from the `gate_results` table. This is project-scoped.

**Cross-project gate view:**
The central.db has `gate_results` with `project_id`. Ad-hoc SQL:
```sql
baton cquery "SELECT project_id, gate_type,
COUNT(*) as total,
SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END) as passed,
ROUND(1.0*SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END)/COUNT(*), 3) as pass_rate
FROM gate_results GROUP BY project_id, gate_type ORDER BY pass_rate ASC"
```

However, `project_id` is not "team" -- it is the project path. James's 3 teams might each work across multiple projects, or share projects. There is no team-to-project mapping in the schema.

**"Why" analysis:**
Gate results store `output` (the gate command's stdout/stderr) and `gate_type`. The `output` field contains the actual test failure messages. But there is no automatic categorization of failure reasons.

The `v_project_failure_rate` analytics view in central.db provides per-project failure rates for executions (not gates specifically). `baton cquery failures` surfaces this.

**Cross-cutting knowledge gaps:**
`v_recurring_knowledge_gaps` view identifies knowledge gaps that appear across multiple projects. `baton cquery gaps` surfaces this. This could help explain why gates fail (agents lack domain knowledge).

### James probes deeper:
"So there's no 'Team Alpha has 40% gate failure rate because test coverage is low on their auth module'?"

### Expert discovers:

No. The data exists in raw form but the correlation requires manual assembly:
1. Gate failures are per-project, not per-team
2. Gate output is free text, not categorized
3. No "failure reason classification" exists
4. The `program` field on PMO cards COULD serve as a team proxy, but gate results don't join to PMO programs

**NEW finding:** The `learned_patterns` table stores patterns learned from past executions with `confidence`, `success_rate`, and `avg_token_cost`. `baton query patterns` shows these. If a team consistently fails a particular gate type, the learning system would capture it as a pattern -- but it does so at the agent level, not the team level. `baton cquery "SELECT agent_name, success_rate, avg_retries FROM v_agent_reliability WHERE success_rate < 0.7"` identifies underperforming agents cross-project, which is a proxy for team patterns.

### Verdict: PARTIAL

Raw data exists for cross-project gate failure analysis via SQL. But no team-level aggregation, no failure categorization, and no built-in "why" analysis. James would need SQL skills or an analyst.

**Delta from solo audit:** Solo audit covered gate notifications (item 8) but did not explore cross-team gate failure analysis. Team audit reveals the cross-project SQL capability and the `v_recurring_knowledge_gaps` view as useful but insufficient for James's actual question.

---

## Item 6: Slack Integration Depth

### James asks:
"I live in Slack. What events can I receive? Can I approve from Slack without switching to the dashboard?"

### Expert investigates:

**Webhook infrastructure** (`api/webhooks/`):

1. **Registration:** `POST /api/v1/webhooks` with URL, event pattern (glob), and optional HMAC secret. Persisted to `webhooks.json` via `WebhookRegistry`.

2. **Event delivery:** `WebhookDispatcher` subscribes to EventBus `"*"` and delivers matching events via HTTP POST. Retry with exponential backoff (5s, 30s, 300s). Auto-disable after 10 consecutive failures.

3. **Slack formatting** (`payloads.py`):
   - `human.decision_needed` events get full Block Kit layout: header, summary section, options as bullets, context metadata (task ID, request ID, timestamp), and **action buttons** -- one per option (up to 5, Slack's limit). Button values carry `{request_id}::{option}`.
   - All other events get a minimal text attachment with task_id, event_id, and timestamp.

4. **Actionable from Slack?** The Slack payload includes interactive buttons with `action_id` and `value` fields. An interactive Slack app implementing the `interactions_endpoint` could POST back to baton's API to resolve the decision. BUT: baton does not ship a Slack app manifest, OAuth flow, or interactions endpoint. The buttons are correctly formatted for Slack Block Kit, but there is no server-side handler for Slack's `action_url` callbacks.

### James probes deeper:
"So the Slack messages have buttons, but clicking them does nothing unless I build a custom Slack app? What would it take?"

### Expert discovers:

**What exists:** The buttons are correctly formatted with `value: "{request_id}::{option}"`. The `DecisionManager` has a `resolve()` method that accepts a decision resolution. The API has `POST /api/v1/pmo/gates/{card_id}/approve` and `/reject` endpoints.

**What's missing for Slack interactivity:**
1. No Slack app manifest (`manifest.yaml` or `manifest.json`)
2. No OAuth2 handler for Slack app installation
3. No `POST /api/v1/slack/interact` endpoint to receive Slack button callbacks
4. No Slack signature verification middleware
5. No documentation for setting up the Slack integration

**What CAN James receive in Slack today (one-way notifications):**
- `human.decision_needed` -- rich formatted decision requests (the primary use case)
- `gate.passed` / `gate.failed` -- generic format (task_id, event_id, timestamp)
- `escalation.raised` / `escalation.resolved`
- `execution.started` / `execution.completed`
- Any EventBus topic matching the webhook's glob pattern

**What's missing from event formatting:**
Only `human.decision_needed` gets the rich Block Kit format. Gate failures, execution completions, and escalations all get the generic attachment format, which displays as a plain text block in Slack. James would not see a clear "Gate FAILED for Team Alpha's auth migration" -- he would see "Agent Baton event: `gate.failed`" with raw metadata.

### Verdict: PARTIAL

Rich Slack Block Kit formatting exists for decision events with properly structured action buttons. But Slack interactivity is not wired (buttons are decorative without a custom app). Non-decision events get poor Slack formatting. No Slack app manifest or setup documentation.

**Delta from solo audit:** Solo rated Slack as WORKS. Team audit downgrades to PARTIAL because the interactive buttons are non-functional without custom development, and non-decision events have poor formatting. The solo audit missed the critical gap that clicking buttons does nothing -- it noted the buttons exist but did not trace whether they are actually wired.

---

## Item 7: Risk Classification Accuracy

### James asks:
"A developer adds a new payment endpoint. Does the planner correctly classify this as HIGH risk? What signals does it use? Can I verify the classification?"

### Expert investigates:

**Classification system** (`core/govern/classifier.py`):

The `DataClassifier.classify()` scans task descriptions (case-insensitive) against five keyword lists:

1. **Regulated signals** (15 keywords) -> HIGH, "Regulated Data": compliance, hipaa, gdpr, sox, pci, audit, regulatory, etc.
2. **PII signals** (12 keywords) -> HIGH, "Regulated Data": pii, personal data, ssn, credit card, patient, etc.
3. **Security signals** (17 keywords) -> HIGH, "Security-Sensitive": authentication, authorization, auth, secrets, credentials, password, token, api key, oauth, jwt, etc.
4. **Infrastructure signals** (15 keywords) -> HIGH, "Infrastructure Changes": terraform, docker, kubernetes, deploy, production, etc.
5. **Database signals** (11 keywords) -> MEDIUM: migration, schema, database, alter table, etc.

**"New payment endpoint" test:**

The description "add a new payment endpoint" would NOT match any signals. "Payment" is not in any keyword list. Risk: LOW. This is wrong -- payment processing is security-sensitive.

However, if the description were "add payment endpoint with credit card processing and PCI compliance," it would match:
- `pii:credit card` -> HIGH
- `regulated:compliance` -> HIGH
- `regulated:pci` -> HIGH
3 regulated/PII signals -> CRITICAL

**File path elevation:** If the code touches `auth/`, `.env`, `secrets/`, or `migrations/`, the classifier independently elevates risk via `HIGH_RISK_PATHS`. So `auth/payment_handler.py` would trigger `path:auth/` -> HIGH even without description keywords.

**Verification:** `baton classify "add a new payment endpoint"` runs the classifier and shows the result with signals found, confidence, and explanation. James can verify before execution.

**Auto-classification from git:** `classify_from_files()` runs `git diff --name-only HEAD` to discover changed files and feeds them to the path-based classifier. So even a vague description gets elevated if the changed files are in sensitive directories.

### James probes deeper:
"The keyword list is hardcoded? Can I add 'payment', 'billing', 'stripe' to the HIGH risk signals without modifying source code?"

### Expert discovers:

The keyword lists are hardcoded in `classifier.py` (lines 95-129). There is no configuration file, no admin UI, and no CLI command to modify classification signals.

The `PolicyEngine` supports custom presets (`.claude/policies/*.json`) for enforcement rules (path_block, tool_restrict, require_agent), but NOT for classification signals. Classification and policy enforcement are separate systems.

The `LearnedOverrides` system (`core/learn/overrides.py`) stores `classifier_adjustments` in `learned-overrides.json`, which could theoretically adjust classification. But this is populated by the learning pipeline's automated analysis, not by manual configuration.

**NEW finding:** The `spec_validator` in `core/govern/` can validate specifications against policies, and the `regulated` policy preset requires `subject-matter-expert` and `auditor` agents. But these are downstream of classification -- they fire AFTER the classifier assigns a risk level. If the classifier misses "payment" as HIGH risk, the regulated policy never activates.

**NEW finding:** The classifier has a `confidence` field: "high" (2+ signals or 0 signals), "low" (exactly 1 signal). This is reported in `baton classify` output. James can use this to spot low-confidence classifications. But there is no alerting mechanism for low-confidence classifications.

### Verdict: PARTIAL

Classification exists and is verifiable via CLI. But keyword lists are hardcoded, and domain-specific terms (payment, billing, stripe) are missing. No admin-configurable signal lists. The file-path fallback partially compensates but only if code is in recognized sensitive directories.

**Delta from solo audit:** Solo rated custom risk rules as WORKS (for policy enforcement, not classification). Team audit clarifies that classification and enforcement are separate: custom policies WORKS, custom classification signals BLOCKED. The solo audit conflated these, noting "classifier keywords are hardcoded" as a gap but still rating WORKS overall.

---

## Item 8: Onboarding a New Team

### James asks:
"I want Team C to start using agent-baton. What's the onboarding process? Can the team self-serve, or does someone need to set up infrastructure?"

### Expert investigates:

**Install flow** (`scripts/install.sh`):

1. **Prerequisites:** Python 3.10+, git. Checked automatically.
2. **Scope choice:** User-level (`~/.claude/`) or project-level (`.claude/`).
3. **Files installed:**
   - 20 agent definitions (`.md` files)
   - 16 reference documents
   - Skills (e.g., `baton-help`)
   - `CLAUDE.md` (template for project documentation)
   - `settings.json` (hooks configuration)
   - Creates `team-context/`, `knowledge/`, `skills/` directories
4. **Upgrade mode:** `--upgrade` merges identity block into existing CLAUDE.md, merges settings.json (preserving custom hooks and permissions).
5. **Python package:** `pip install -e ".[dev]"` or `pip install agent-baton`.

**What Team C needs to do:**

1. Install: `scripts/install.sh` (interactive, ~30 seconds)
2. Register project with PMO: `baton pmo add --name "Team C Project" --program "Team C" --path /path/to/repo`
3. Start using: `baton plan "task description" --save` then `baton execute start`
4. For PMO visibility: `baton pmo serve` (starts the API server)
5. For cross-project sync: `baton sync` (pushes data to central.db)

**Infrastructure requirements:**
- No server needed for basic use (file-based, all local)
- For PMO dashboard: one instance of `baton serve` (any dev machine or VM)
- For Slack notifications: webhook registration via API
- central.db is at `~/.baton/central.db` (shared across all projects on the same machine; no multi-machine federation)

### James probes deeper:
"Can Team C onboard without Priya (DevOps)? And what happens if they're on a different machine -- can I still see their work in my dashboard?"

### Expert discovers:

**Self-serve onboarding:** Yes. The install script is interactive and requires only Python + git. No admin credentials, no server setup, no infrastructure provisioning. A developer can install and start using agent-baton in under 5 minutes.

**Multi-machine visibility:** This is where it gets complicated. central.db is local to each machine (`~/.baton/central.db`). If Team C is on different machines:
- Their `baton.db` (project-level) syncs to THEIR local central.db
- James's PMO dashboard reads from HIS local central.db
- There is no network federation -- no server-to-server sync

**Workaround:** If all teams use shared VMs (e.g., dev boxes on a shared filesystem), central.db aggregates naturally. But if teams use their own laptops, James cannot see Team C's data without either (a) sharing a filesystem, or (b) running `baton pmo serve` on a shared server that all teams sync to.

**NEW finding:** The `external_sources` system (`core/storage/adapters/`) supports ADO, GitHub, Jira, and Linear integrations. `baton source add --type ado --config '...'` registers an external source. `baton source sync` pulls work items. This means Team C could link their ADO work items to baton executions, providing visibility even without filesystem sharing. But this is metadata linking, not execution data sharing.

**NEW finding:** The install script has intelligent merge behavior for upgrades -- it does not clobber existing `CLAUDE.md` or `settings.json`. This is important for onboarding a team that already has project configuration. The solo audit did not examine the install flow at all.

### Verdict: PARTIAL

Self-serve install is excellent (< 5 minutes, no admin needed). But cross-machine visibility requires shared infrastructure that is not documented or automated. The central.db is local-only -- a significant gap for a manager overseeing teams on different machines.

**Delta from solo audit:** The solo audit did not examine onboarding at all. This is entirely new ground. The self-serve install is a strength; the lack of network federation is a critical gap for James's use case.

---

## Item 9: Audit Readiness

### James asks:
"My security team says 'prove that agent governance is at least as rigorous as human governance.' What evidence can I produce?"

### Expert investigates:

**Evidence available:**

1. **Policy enforcement documentation:** 5 built-in policy presets with declarative rules. Custom presets in `.claude/policies/*.json`. `baton policy list` shows active presets.

2. **Classification records:** Every plan stores its risk classification result with signals found. `baton classify` can reproduce the classification.

3. **Auditor agent:** Full auditor definition with pre/mid/post-execution review modes. Verdicts: APPROVED/APPROVED WITH CONDITIONS/BLOCKED, CONTINUE/PAUSE/HALT, SHIP/SHIP WITH NOTES/REVISE/BLOCK. Compliance reports stored in `.claude/team-context/compliance-reports/`.

4. **Gate results:** Every gate check recorded with pass/fail, output, timestamp. `baton query gate-stats` shows aggregate pass rates.

5. **Approval records:** Phase approvals with result, feedback, timestamp. (But no approver identity.)

6. **Full execution traces:** `baton trace TASK_ID` shows every step dispatch, agent assignment, gate check, approval, with timestamps.

7. **Event stream:** `events` table with complete domain event history, sequenced and timestamped.

8. **Mission logs:** Per-agent assignment records with decisions, issues, files changed, commit hashes.

9. **Beads:** Structured agent memory with discoveries, decisions, warnings, outcomes.

10. **Retrospectives:** Post-execution analysis with what worked, what didn't, recommendations.

**Evidence NOT available:**

1. **Approver identity:** `approval_results` has no `approved_by`. Cannot prove WHO approved.
2. **Immutable audit trail:** DELETE-then-INSERT pattern means records can be retroactively modified. No append-only guarantee.
3. **PII/secret redaction:** No evidence that sensitive data is scrubbed from traces/events before storage.
4. **Classification override justification:** `learned-overrides.json` has no per-change justification history.
5. **Compliance report integrity:** Reports are markdown files on disk, not checksummed or in SQLite.

### James probes deeper:
"Can I export all this as a bundle for the security team to review?"

### Expert discovers:

**Export capabilities:**
- `baton query <any-subcommand> --format csv` for any query
- `baton cquery <view> --format csv` for cross-project data
- `baton query --sql "SELECT ..." --format csv` for ad-hoc exports
- PMO UI DataExport: CSV/JSON/Markdown for board data
- `baton compliance --task-id TASK_ID` for individual compliance reports

**No unified "audit bundle" export.** James would need to manually run:
1. `baton query tasks --format csv > tasks.csv`
2. `baton query gate-stats --format csv > gates.csv`
3. `baton trace TASK_ID` for each task (text output, no CSV)
4. `baton compliance` to list reports
5. Collect all compliance reports from `.claude/team-context/compliance-reports/`

**NEW finding:** The `baton compliance` command reads from markdown files on disk (not SQLite), meaning compliance reports are not synced to central.db and not queryable via `baton cquery`. A security team reviewing across projects would need to visit each project's filesystem. This is a significant gap the solo audit noted but did not emphasize the operational impact.

### Verdict: PARTIAL

Comprehensive governance artifacts exist (policies, classifications, auditor verdicts, gate results, traces, events). But three gaps undermine audit readiness: (a) no approver identity, (b) mutable audit trail, (c) no unified export for security review.

**Delta from solo audit:** Solo audit covered these gaps individually across David's journey items. Team audit consolidates them into a single audit-readiness assessment, revealing that the combination of gaps -- not just individual ones -- makes the system fail a rigorous security review. The solo audit's "FAIL for David" is confirmed but the specific combination matters more than individual items.

---

## Item 10: Scaling from 2 to 6 VMs

### James asks:
"The pilot succeeds on 2 VMs. I want to scale to 6. What changes? What breaks? What new operational concerns emerge?"

### Expert investigates:

**Current architecture:**
- Each VM runs `baton` CLI independently
- Each project has its own `baton.db` (SQLite, per-project)
- Each VM has its own `~/.baton/central.db` (per-machine aggregation)
- PMO UI served by `baton pmo serve` (FastAPI/uvicorn, single instance)
- Sync: `baton sync` pushes project data to local central.db

**What works at 6 VMs:**
- Install is self-serve (no coordination needed)
- `baton.db` is per-project, no contention across VMs
- SQLite WAL mode handles concurrent reads from same-project agents (`connection.py`)
- Execution state is per-task, no shared state between VMs

**What breaks or degrades:**

1. **Centralized visibility:** central.db is per-machine. With 6 VMs, James has 6 separate central.db files. The PMO dashboard shows only the data from the machine running `baton pmo serve`. No multi-machine aggregation.

2. **PMO dashboard:** Only one instance of `baton pmo serve` should run (no clustering). If teams use different VMs, only projects registered on the PMO server's machine appear.

3. **Webhook delivery:** Webhooks are delivered from the `baton serve` instance. If multiple `baton serve` instances run on different VMs, each sends its own events. No deduplication.

4. **Shared filesystem workaround:** If all 6 VMs mount the same NFS/CIFS home directory, central.db is shared. But SQLite over NFS is fragile and not recommended (journal mode, locking issues).

5. **No process supervision:** `baton pmo serve` is a plain uvicorn process. No systemd unit, no Docker compose, no health check endpoint, no auto-restart on failure.

### James probes deeper:
"What would the recommended deployment look like at 6 VMs?"

### Expert discovers:

**Recommended architecture (not built, but possible):**
1. Designate one VM as the "PMO server" running `baton pmo serve`
2. All projects on all VMs sync to that server's central.db
3. Webhook subscriptions registered on the PMO server

**Problem:** `baton sync` pushes to `~/.baton/central.db` on the LOCAL machine. There is no `baton sync --remote <host>` or SSH-based sync. The sync engine (`sync.py`) directly opens a local SQLite file.

**NEW finding:** The `HeadlessClaude` class (`core/runtime/headless.py`) spawns `claude --print` subprocesses for autonomous execution. At 6 VMs with concurrent executions, this means multiple `claude` processes running simultaneously. Resource consumption (CPU, memory, API rate limits) scales linearly. No built-in rate limiting or queue management across VMs.

**NEW finding:** The `baton query stalled --hours N` command exists for detecting stuck executions, which becomes more important at scale. But it queries local baton.db only -- no cross-VM stall detection.

### Verdict: BLOCKED

The tool is designed for single-machine or shared-filesystem deployments. Scaling to 6 VMs with independent machines breaks centralized visibility, requires manual infrastructure setup that is not documented, and has no built-in multi-machine sync capability.

**Delta from solo audit:** Solo audit did not examine scaling. This is entirely new. The single-machine assumption is deeply embedded in the architecture (local central.db, local filesystem sync, local PMO server).

---

## Item 11: Agent Performance Trends

### James asks:
"Over 3 months, are agents getting better or worse? Can I see trend data without asking Tomoko?"

### Expert investigates:

**Trend detection** (`core/improve/scoring.py` line 400):

`PerformanceScorer.detect_trends(agent_name, window=10)` computes OLS linear regression over the last N tasks. Binary success vector (1.0 = zero retries, 0.0 = had retries). Thresholds:
- slope > 0.02 -> "improving"
- slope < -0.02 -> "degrading"
- otherwise -> "stable"

Minimum 3 data points required.

**CLI access:**
`baton scores --trends` shows all agents with trend indicators:
```
Agent Performance Trends:
  [+] backend-engineer: improving (health=strong)
  [=] test-engineer: stable (health=adequate)
  [-] frontend-engineer: degrading (health=needs-improvement)
```

**Scorecard data** (`baton scores` or `baton scores --agent NAME`):
- Health: strong/adequate/needs-improvement/unused
- First-pass rate, retry rate, gate pass rate
- Token consumption (avg per use)
- Models used
- Retrospective mentions (+positive / -negative)
- Knowledge gaps cited
- Bead quality metrics (avg quality, count)

**Team composition effectiveness:**
`baton scores --teams` shows which agent team combinations perform best:
```
### backend-engineer + test-engineer
- Health: strong
- Uses: 8
- Success rate: 88%
- Avg tokens/use: 45,000
```

**Learning pipeline:**
`baton learn status` shows learning issues (detected problems, proposed fixes, applied fixes).
`baton improve --run` triggers a full improvement cycle (anomaly detection, proposal generation, optional application).
`baton patterns` shows learned routing patterns with confidence scores.

### James probes deeper:
"Can I see this as a graph over time? And is this cross-team or per-project?"

### Expert discovers:

**No time-series graphs.** All trend data is CLI text output. The PMO UI's `AnalyticsDashboard` shows point-in-time agent utilization (tasks, success, failed, rate) but no historical trend lines. There is no chart component showing agent performance over time.

**Per-project scope.** `baton scores` queries the local `baton.db`. Cross-project agent performance requires `baton cquery agents` which queries the `v_agent_reliability` view in central.db -- but this is aggregate, not time-series.

**NEW finding:** The `budget_recommendations` table stores recommended budget tiers per task type based on historical performance. `baton budget` shows these. This is trend-derived data (the system learned optimal budgets over time) that could serve as evidence of improving efficiency. The solo audit did not mention this.

**NEW finding:** The `retrospective_outcomes` table stores per-execution outcome assessments. `baton retro --search "degrading"` could find retrospectives that mention degradation. This is a qualitative trend signal. The solo audit did not explore retrospective search.

### Verdict: PARTIAL

Trend detection exists and is accessible via CLI without needing an analyst. The `detect_trends` algorithm is sound (simple linear regression, sensible thresholds). But no time-series visualization, no cross-team trends, and no dashboard integration.

**Delta from solo audit:** Solo audit did not examine agent performance trends. This is entirely new. The `PerformanceScorer.detect_trends()` method with OLS regression and the `baton scores --trends` CLI are significant capabilities.

---

## Item 12: Executive Slide

### James asks:
"I need one slide for my VP: 'Agent program: here's what it does, here's the cost, here's the value.' Can I extract this from the tool?"

### Expert investigates:

**Data sources for the slide:**

1. **"Here's what it does":**
   - `baton query tasks --format csv`: task list with outcomes
   - PMO UI AnalyticsDashboard: Total Plans, Success Rate, Active, Blocked/Failed
   - PMO UI DataExport (Markdown): "Baton Portfolio Report" with summary table, program health, plans table

2. **"Here's the cost":**
   - `baton usage`: total_estimated_tokens across all tasks
   - `baton query cost-by-agent --days 90 --format csv`: 3-month agent costs
   - `baton cquery costs`: cross-project token costs
   - Must manually convert tokens to dollars

3. **"Here's the value":**
   - `baton scores --teams`: team composition effectiveness
   - `budget_recommendations`: potential_savings per task type
   - Retrospective outcome counts (success/failure)
   - No "time saved" or "ROI" calculation

**Export formats available:**
- CSV (any query, via `--format csv`)
- JSON (any query, via `--format json`)
- Markdown (PMO UI DataExport produces "Baton Portfolio Report")
- No PDF
- No PowerPoint
- No chart/graph export

**What the Markdown export produces** (DataExport lines 301-341):
```markdown
# Baton Portfolio Report
> Exported: 2026-04-16T...
> Total Plans: 47

## Summary
| Metric | Value |
|--------|-------|
| Deployed | 38 |
| Active | 5 |
| Blocked | 2 |
| Queued | 2 |

## Program Health
| Program | Plans | Active | Done | Blocked | Failed | % |
|---------|-------|--------|------|---------|--------|---|
| Team Alpha | 20 | 2 | 16 | 1 | 1 | 80% |
...

## Plans
| ID | Title | Project | Column | Risk | Steps | Agents |
...
```

### James probes deeper:
"Can I get this into a format my VP's exec assistant can drop into PowerPoint?"

### Expert discovers:

No. The export path is: PMO UI -> Markdown/CSV/JSON download -> manual formatting. The Markdown output is table-heavy and would need manual conversion to PowerPoint.

**What James would realistically do:**
1. Export Markdown from PMO UI (30 seconds)
2. Copy `baton usage` summary (token totals)
3. Copy `baton scores --trends` output (agent health trends)
4. Manually convert tokens to dollars using Anthropic pricing
5. Manually create a slide with the above data

**NEW finding:** The `AnalyticsDashboard` component renders directly in the browser with a 680px modal containing summary cards, pipeline distribution bar, program health progress bars, agent utilization table, and risk distribution circles. James could screenshot this for the slide. It is visually clean (dark theme, good data density). The solo audit mentioned the AnalyticsDashboard but did not evaluate its visual quality for executive presentation.

**NEW finding:** The DataExport component supports exporting only filtered cards (via scope toggle), which means James could export just "this month's completed work" by filtering the board first. The solo audit mentioned export but not scoped export.

### Verdict: PARTIAL

The raw data exists and the AnalyticsDashboard is visually presentable. But the last mile -- dollar conversion, executive formatting, chart export -- is missing. A screenshot of the AnalyticsDashboard is the most realistic VP-ready output today.

**Delta from solo audit:** Solo rated executive reporting as PARTIAL. Team audit confirms PARTIAL but identifies the AnalyticsDashboard screenshot path as a practical workaround, and the scoped export capability as a useful feature the solo audit understated.

---

## Summary Matrix

| # | Item | Team Verdict | Solo Verdict | Delta |
|---|------|:---:|:---:|-------|
| 1 | Monday morning dashboard | **WORKS** | WORKS | Team found `ExecutionProgress` monitor modal, stale-card gap, per-person approval gap |
| 2 | Approval delegation | **BLOCKED** | PARTIAL | Downgraded -- no workaround exists for risk-based routing |
| 3 | Incident post-mortem | **PARTIAL** | (new) | Rich investigation path via commit_hash + trace + mission_log + beads + telemetry; approval identity gap breaks chain |
| 4 | Cost justification report | **PARTIAL** | PARTIAL | Found `budget_recommendations.potential_savings` and `baton cquery costs` |
| 5 | Team-wide gate failure patterns | **PARTIAL** | (new) | Cross-project SQL works but no team-level aggregation or failure categorization |
| 6 | Slack integration depth | **PARTIAL** | WORKS | Downgraded -- interactive buttons non-functional without custom app; non-decision events poorly formatted |
| 7 | Risk classification accuracy | **PARTIAL** | WORKS | Downgraded -- classification signals are hardcoded; custom policies != custom classification |
| 8 | Onboarding a new team | **PARTIAL** | (new) | Self-serve install excellent; cross-machine visibility broken |
| 9 | Audit readiness | **PARTIAL** | (see David) | Consolidated: combination of gaps (no approver, mutable trail, no bundle export) fails security review |
| 10 | Scaling from 2 to 6 VMs | **BLOCKED** | (new) | Single-machine assumption embedded in architecture |
| 11 | Agent performance trends | **PARTIAL** | (new) | OLS trend detection + CLI access; no visualization or cross-team view |
| 12 | Executive slide | **PARTIAL** | PARTIAL | AnalyticsDashboard screenshot is viable; scoped export is useful |

## Scoring Summary

- **WORKS:** 1 of 12
- **PARTIAL:** 9 of 12
- **BLOCKED:** 2 of 12

## New Findings Not in Solo Audit

1. **ExecutionProgress component** -- real-time step-by-step monitoring modal (3s polling) not mentioned in solo audit
2. **Dual approval systems** -- `ApprovalResult` (no identity) vs `DecisionResolution` (has `resolved_by`) are separate codepaths; solo audit noted the gap but not the duality
3. **Investigation path depth** -- `commit_hash` -> `step_results` -> `trace` -> `mission_log` -> `beads` -> `telemetry` -> `retrospective` chain is richer than solo audit identified
4. **`budget_recommendations.potential_savings`** -- cost optimization metric not surfaced in solo audit
5. **Cross-project CLI** -- `baton cquery` with 5 analytics view shortcuts; solo audit mentioned central.db views but not the dedicated CLI
6. **Stale card detection** -- `baton query stalled --hours N` exists in CLI but not in PMO UI
7. **Self-serve install flow** -- interactive installer with merge/upgrade mode; not examined in solo audit
8. **Single-machine architecture assumption** -- central.db locality, no network federation; not examined in solo audit
9. **PerformanceScorer.detect_trends()** -- OLS regression with configurable window; `baton scores --trends` CLI; not examined in solo audit
10. **Team composition scoring** -- `baton scores --teams` shows which agent combinations work best; not in solo audit
11. **Learning pipeline CLI** -- `baton learn status/analyze/apply`, `baton improve --run`, `baton patterns`; not examined in solo audit
12. **InteractionQueue component** imported in KanbanBoard but file not found -- suggests in-progress feature for multi-turn interactions in the UI
13. **Slack buttons are decorative** -- Solo audit rated Slack as WORKS; buttons exist but clicking them does nothing without custom app development
14. **Classification vs Policy conflation** -- Solo audit rated "custom risk rules" as WORKS by combining classification (hardcoded) with policy enforcement (configurable); these are separate systems
15. **Scoped export** -- DataExport supports filtered-card export, not just full-board; useful for time-windowed reporting
16. **Retrospective as investigation tool** -- post-execution analysis with what-worked/what-didn't; useful for post-mortem context
