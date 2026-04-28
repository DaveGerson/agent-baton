# Competitive Audit: Theme 1 (Governance & Quality) and Theme 4 (Planning Intelligence)

Audit date: 2026-04-16
Auditor: Automated codebase analysis against competitive benchmark user stories

---

## Summary Table

| Story | Title | Rating | Key Gap |
|-------|-------|--------|---------|
| 1.1 | Automated Quality Gates Between Phases | **FULLY MET** | None |
| 1.2 | Risk-Based Task Classification | **FULLY MET** | None |
| 1.3 | Auditor Agent with Veto Authority | **FULLY MET** | None |
| 1.4 | Approval Workflows with Deadlines | **PARTIALLY MET** | No named approver roles, no escalation chain, no timeout enforcement |
| 1.5 | Custom Gate Scripts | **PARTIALLY MET** | No formal plugin API; custom gates via plan.json command field only |
| 1.6 | Gate Analytics and Trend Analysis | **FULLY MET** | None |
| 4.1 | Intelligent Stack Detection and Agent Routing | **FULLY MET** | None |
| 4.2 | Risk-Aware Plan Generation | **FULLY MET** | None |
| 4.3 | Plan Amendment Mid-Execution | **FULLY MET** | None |
| 4.4 | Demo Statements / Expected Outcomes | **MINIMALLY MET** | `deliverables` field exists but no `expected_outcome` field; not validated in gates |
| 4.5 | Complexity Override | **PARTIALLY MET** | `--complexity`, `--agents`, `--intervention` exist; `--skip-phase` does not |
| 4.6 | Reusable Plan Templates | **MINIMALLY MET** | `--template` emits a skeleton; no `--save-as-template` or template library |

---

## Detailed Evidence

---

### Story 1.1 -- Automated Quality Gates Between Phases

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**Gates run between phases:**
- `core/engine/executor.py` line 2744: after all steps in a phase complete, the engine checks for a gate and transitions to `gate_pending`.
- `core/engine/executor.py` lines 2694-2704: the `_determine_action` method returns a GATE action when status is `gate_pending`.

**Gates run pytest/lint:**
- `core/engine/gates.py` lines 265-290: `GateRunner.default_gates()` defines `build` (py_compile), `test` (pytest --tb=short -q), `lint` (py_compile), and `review` (advisory).
- `core/engine/planner.py` lines 2092-2105: `_default_gate()` assigns test or build gates to code-producing phases. Non-code phases (investigate, research, review, design, feedback) get no gate (line 2071).

**Gates block on failure:**
- `core/engine/executor.py` lines 1382-1389: when `passed=False`, the engine sets `state.status = "failed"` and publishes a `gate_failed` event. This blocks all further progress.

**Results persisted to SQLite:**
- `core/storage/schema.py` lines 326-336: `gate_results` table with columns `id`, `task_id`, `phase_id`, `gate_type`, `passed`, `output`, `checked_at`, with foreign key to `executions`.
- Central schema at lines 1035-1045: identical table with `project_id` for cross-project sync.

**Stack-adaptive gate config:**
- `core/engine/planner.py` lines 129-143: `_STACK_GATE_COMMANDS` maps 9 languages (python, typescript, javascript, go, rust, java, ruby, kotlin, csharp) to language-appropriate test and build commands.
- `core/engine/planner.py` lines 2075-2090: `_default_gate()` selects commands by detected stack language, with LearnedOverrides fallback for project-specific corrections.

---

### Story 1.2 -- Risk-Based Task Classification

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**LOW/MEDIUM/HIGH classification exists:**
- `models/enums.py` defines `RiskLevel` enum: LOW, MEDIUM, HIGH, CRITICAL (four tiers, exceeding requirement).
- `core/govern/classifier.py` lines 80-85: ordinal mapping LOW=0, MEDIUM=1, HIGH=2, CRITICAL=3.

**Automatic during planning:**
- `core/engine/planner.py` lines 916-947: `create_plan()` step 7 runs `DataClassifier.classify()` on the task summary, step 8 combines it with keyword/structural risk signals to produce the final risk level.
- `core/govern/classifier.py` lines 151-282: `DataClassifier.classify()` scans for 5 signal categories (regulated, PII, security, infrastructure, database) plus file path patterns.
- `core/engine/planner.py` lines 2319-2390: `_assess_risk()` adds structural signals (agent count > 5, sensitive agent types, destructive verbs) with read-only dampening.

**Custom risk rules:**
- `core/govern/policy.py` lines 87ff: `PolicyRule` dataclass with `rule_type` field supporting `path_block`, `path_allow`, `tool_restrict`, `require_agent`, `require_gate`. Users define custom rules in `PolicySet` objects.
- Presets are provided for "Infrastructure Changes" (line 249), "Regulated Data" (line 296), "Security-Sensitive" (line 346), and "Standard Development".

**Classifications logged:**
- `core/engine/planner.py` lines 940-947: risk classification is logged at INFO level with task_id, final risk, keyword risk, classifier risk, and git strategy.
- `ClassificationResult.to_markdown()` renders signals, confidence, and explanation for plan output.
- `core/observe/telemetry.py`: telemetry events capture execution.started with risk level.

---

### Story 1.3 -- Auditor Agent with Veto Authority

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**Auditor agent exists:**
- `agents/auditor.md`: 261-line agent definition with three operational modes (pre-execution review, mid-execution check, post-execution audit).
- Uses Opus model, has Read/Glob/Grep/Bash tools, red color indicator.

**Can veto plans:**
- `agents/auditor.md` lines 73-130: Mode 1 output includes "Blocked" section with specific resolution requirements. Verdicts include "Approved", "Approved With Conditions", and "Blocked".
- `agents/auditor.md` lines 132-153: Mode 2 mid-execution verdicts: CONTINUE, PAUSE, HALT.
- `agents/auditor.md` lines 155-180: Mode 3 post-execution verdicts: SHIP, SHIP WITH NOTES, REVISE, BLOCK.

**Runs independently:**
- `agents/auditor.md` lines 36-43: explicitly states "You are independent. The orchestrator plans; you audit." and explains why it is a subagent (not a skill) -- independence from the orchestrator prevents bias.
- `agents/auditor.md` line 249: "You are independent. The orchestrator plans; you audit. If the plan is unsafe, you block it."

**APPROVE/VETO verdicts:**
- Mode 1: Approved / Approved With Conditions / Blocked
- Mode 2: CONTINUE / PAUSE / HALT
- Mode 3: SHIP / SHIP WITH NOTES / REVISE / BLOCK
- Trust levels per agent: Full Autonomy / Supervised / Restricted / Plan Only (lines 236-244)

**Policy integration enforces auditor requirement:**
- `core/govern/policy.py` lines 277-283: Infrastructure preset requires auditor (`require_agent` rule).
- `core/govern/policy.py` lines 310-316: Regulated Data preset requires auditor.
- `core/govern/policy.py` lines 353-359: Security preset requires auditor.

---

### Story 1.4 -- Approval Workflows with Deadlines

**Rating: PARTIALLY MET**

Core approval workflow exists but lacks named approver roles, timeout enforcement, and escalation chains.

**What exists:**

Approval actions and recording:
- `core/engine/executor.py` lines 795-812: HIGH/CRITICAL risk plans automatically get `approval_required=True` on the first phase.
- `core/engine/executor.py` lines 2682-2686: `_determine_action` returns an APPROVAL action when status is `approval_pending`.
- `core/engine/executor.py` lines 1764-1818: `record_approval_result()` handles approve, reject, approve-with-feedback (with remediation phase injection).
- `models/execution.py` lines 843-882: `ApprovalResult` dataclass with phase_id, result, feedback, decided_at.
- CLI command: `baton execute approve --phase-id N --result approve|reject|approve-with-feedback` (execute.py lines 123-130).

Decision manager for async decisions:
- `core/runtime/decisions.py`: full `DecisionManager` with request/resolve/pending/list_all operations.
- `models/decision.py` line 24-25: `DecisionRequest` has a `deadline` field (ISO 8601 optional).
- `models/decision.py` line 37: `deadline: str | None = None`.
- CLI command: `baton decide --resolve ID --option OPTION [--rationale TEXT]` (decide.py lines 37-53).

**What is missing:**

- No named approver roles: approvals are generic "human" -- no concept of "security lead approves security phases, tech lead approves architecture".
- No timeout enforcement: the `deadline` field exists on `DecisionRequest` but there is no scheduler or polling loop that auto-expires or escalates when the deadline passes.
- No escalation chain: if an approver does not respond, there is no fallback path to a backup approver or auto-escalation.
- `DecisionResolution.resolved_by` supports "human", "timeout_default", and "auto_policy" but the timeout_default path is not wired to an actual timer.

---

### Story 1.5 -- Custom Gate Scripts

**Rating: PARTIALLY MET**

Users can define custom gate commands but there is no formal plugin/hook API or gate composition system.

**What exists:**

Custom commands in plan.json:
- `models/execution.py` lines 332-370: `PlanGate` dataclass with `gate_type`, `command` (arbitrary bash command), `description`, `fail_on`.
- `core/engine/gates.py` lines 96-131: `build_gate_action()` surfaces the command with `{files}` placeholder substitution.
- `core/engine/gates.py` lines 232-245: unknown gate types fall back to exit_code check -- any gate_type string is accepted.
- `baton plan --template` (plan_cmd.py lines 161-210) produces a skeleton with gate objects that users can edit.
- `baton plan --import FILE` (plan_cmd.py lines 213-268) allows importing hand-crafted plans with arbitrary gate definitions.

Standard input/output contract:
- `core/engine/gates.py` lines 137-245: `evaluate_output()` accepts `command_output` (stdout/stderr) and `exit_code`. Pass/fail semantics are gate-type-specific (exit_code for test/build, error markers for lint, SpecValidator for spec, always-pass for review).

LearnedOverrides for gate command correction:
- `core/engine/planner.py` lines 2079-2090: gate commands can be overridden per-language via `learned-overrides.json`.

**What is missing:**

- No formal gate plugin/hook API: custom gates are defined by editing plan.json command strings, not by registering named gate scripts in a plugin directory.
- No gate composition: cannot chain multiple checks in a single gate (e.g., "run lint then run type-check").
- No gate discovery: custom gate types are accepted but there is no registry of available gate types or validation against registered types.
- No pre-built custom gate library (e.g., OWASP scan, dependency audit).

---

### Story 1.6 -- Gate Analytics and Trend Analysis

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**Gate pass/fail rates queryable:**
- `core/storage/queries.py` lines 425-452: `gate_stats()` method queries `gate_results` grouped by `gate_type`, returning total, passed_count, and pass_rate.
- `cli/commands/observe/query.py` line 18: `gate-stats` is a named query accessible via `baton query gate-stats`.
- `cli/commands/observe/usage.py` lines 44-51: `baton usage` displays gate pass rate per task.
- `core/observe/dashboard.py` line 179: dashboard includes "Gate pass rate" row.

**Anomaly detection on gate success rates:**
- `core/improve/triggers.py` lines 203-215: `TriggerEvaluator.detect_anomalies()` computes gate failure rate across all tasks. When it exceeds `gate_failure_threshold` (default 0.2), an anomaly of type `high_gate_failure_rate` is emitted with severity "high" (>0.4) or "medium".
- `core/improve/evolution.py` lines 185-187: evolution engine flags gate_pass_rate < 0.7 as a quality issue.
- `core/improve/scoring.py` lines 72-88: `AgentScorecard.gate_pass_rate` tracks per-agent gate performance.
- `models/improvement.py` lines 171, 298: `Anomaly` model with metric field for `gate_pass_rate`, configurable `gate_failure_threshold`.

**Gate health in PMO/dashboard:**
- `core/observe/dashboard.py` line 179: gate pass rate is rendered in the dashboard.
- `core/storage/schema.py` lines 1350-1364: `v_agent_reliability` analytics view in central.db includes success rates with retry and token data per agent.
- `core/learn/pattern_learner.py` lines 65, 178-188: `_gate_pass_rate()` function computes gate rates for pattern learning.

---

### Story 4.1 -- Intelligent Stack Detection and Agent Routing

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**`baton detect` exists:**
- `cli/commands/govern/detect.py`: 36-line command that runs `AgentRouter.detect_stack()` and prints language, framework, and signal files.

**Identifies languages/frameworks:**
- `core/orchestration/router.py` lines 19-44: `PACKAGE_SIGNALS` maps 11 filenames to languages (javascript, typescript, python, go, rust, ruby, java, kotlin). `FRAMEWORK_SIGNALS` maps 11 filenames to (language, framework) pairs (react, vue, angular, svelte, dotnet, django).
- Lines 75-210: `detect_stack()` scans up to 3 directory levels, checks framework signals first (more specific), then package manager signals, with root-level priority and Vite/React detection.

**Routing uses stack info:**
- `core/orchestration/router.py` lines 47-58: `FLAVOR_MAP` maps (language, framework) to agent flavor suffixes (e.g., backend-engineer -> python, frontend-engineer -> react).
- Lines 212-300: `route()` method resolves base agent name to flavored variant using stack profile, with registry validation and LearnedOverrides precedence.
- `core/engine/planner.py` line 889: `create_plan()` calls `_route_agents()` which applies stack-based routing to all agents.

**Talent-builder creates variants on demand:**
- `agents/talent-builder.md`: exists as a distributable agent for creating new agent definitions. While it does not auto-create variants triggered by detection, it provides the manual workflow for creating new agent flavors when needed.

---

### Story 4.2 -- Risk-Aware Plan Generation

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**Planner auto-classifies risk:**
- `core/engine/planner.py` lines 916-947: `create_plan()` runs DataClassifier + keyword assessment, takes the higher of the two.

**Adds security-reviewer/auditor phases for HIGH-risk:**
- `core/engine/planner.py` lines 1109-1119: for HIGH/CRITICAL risk, the planner sets `approval_required=True` on design/research phases with specific approval descriptions.
- `core/engine/executor.py` lines 795-812: executor additionally ensures first phase of HIGH/CRITICAL plans has approval required (defense in depth).
- `core/govern/policy.py` lines 277-283 (Infrastructure), 310-316 (Regulated), 353-365 (Security): presets enforce `require_agent` rules for auditor and security-reviewer via `PolicyEngine.validate()`.
- `core/engine/planner.py` lines 1088-1102: policy validation runs during plan creation; violations are surfaced as warnings and the engine enforces missing required agents.
- `core/engine/planner.py` line 104: migration task type defaults include `auditor` in the agent roster.

**Risk rules configurable:**
- `core/govern/policy.py`: four built-in presets (Standard, Infrastructure, Regulated, Security) with `PolicyRule` objects supporting custom `pattern`, `severity`, `scope`, and `rule_type` fields.
- `core/govern/classifier.py`: signal keyword lists are module-level constants that could be extended.
- `core/engine/planner.py` line 48-65: `_RISK_SIGNALS` dict maps keywords to risk levels.

**Git strategy adapts to risk:**
- `core/engine/planner.py` lines 75-84: `_select_git_strategy()` returns `BRANCH_PER_AGENT` for HIGH/CRITICAL and `COMMIT_PER_AGENT` for MEDIUM/LOW.

---

### Story 4.3 -- Plan Amendment Mid-Execution

**Rating: FULLY MET**

All acceptance criteria are demonstrably implemented.

**`baton execute amend` exists:**
- `cli/commands/execution/execute.py` lines 141-151: CLI subparser with `--description`, `--add-phase`, `--after-phase`, `--add-step` arguments.

**Phases can be added mid-execution:**
- `core/engine/executor.py` lines 1820-1906: `amend_plan()` method supports:
  - Adding new phases (`new_phases` arg) with optional insertion point (`insert_after_phase`).
  - Adding steps to existing phases (`new_steps` + `add_steps_to_phase`).
  - Phase renumbering after insertion (`_renumber_phases`).
  - Trigger tracking (`trigger` arg: "manual", "gate_feedback", "approval_feedback").

**Amendment history tracked:**
- `models/execution.py` lines 655-712: `PlanAmendment` dataclass with `amendment_id`, `trigger`, `trigger_phase_id`, `description`, `phases_added`, `steps_added`, `created_at`, `feedback`.
- `core/engine/executor.py` line 1888: amendments are appended to `state.amendments` list.
- Trace events recorded for each amendment (lines 1890-1903).
- Approval-with-feedback flow automatically triggers amendments (executor.py lines 1808-1818).

---

### Story 4.4 -- Demo Statements / Expected Outcomes

**Rating: MINIMALLY MET**

Basic structure for describing expected outputs exists but there is no formal `expected_outcome` field validated by gates.

**What exists:**

- `models/execution.py` lines 263-265: `PlanStep.deliverables: list[str]` field lists expected output artifacts (e.g., "Working implementation with tests", "Security audit report").
- `core/engine/planner.py` lines 358-374: `_AGENT_DELIVERABLES` provides default deliverable descriptions per agent type.
- `PlanGate.fail_on: list[str]` (execution.py line 351) lists criteria for failure but these are informational strings, not validated programmatically.
- `PlanGate.description` provides a human-readable statement of what the gate checks.

**What is missing:**

- No `expected_outcome` field on `PlanStep` -- the concept is split across `deliverables` (what) and `task_description` (how).
- No automated validation of expected outcomes in gate logic -- gates check exit codes and output patterns, not semantic matching against declared expected outcomes.
- No "demo statement" concept where each step declares a testable assertion about what success looks like.
- The `deliverables` field is not consumed by `GateRunner.evaluate_output()` -- it exists for plan documentation only.

---

### Story 4.5 -- Complexity Override

**Rating: PARTIALLY MET**

Three of four target flags exist; `--skip-phase` is absent.

**What exists:**

`--complexity` flag:
- `cli/commands/execution/plan_cmd.py` lines 103-110: `--complexity` argument with choices `light`, `medium`, `heavy`.
- `core/engine/planner.py` lines 969-975: when complexity is explicitly provided, phases are scaled using `KeywordClassifier._select_phases()`.
- `core/engine/planner.py` line 756-758: explicit complexity bypasses the HaikuClassifier.

`--agents` flag:
- `cli/commands/execution/plan_cmd.py` lines 48-52: `--agents` argument for comma-separated agent names.
- `core/engine/planner.py` lines 679-682: explicit agents override auto-selection entirely.

`--intervention` flag:
- `cli/commands/execution/plan_cmd.py` lines 89-95: `--intervention` with choices `low`, `medium`, `high`.
- Stored on `MachinePlan.intervention_level` (execution.py line 483) and threaded through to knowledge gap escalation.

**What is missing:**

- No `--skip-phase` flag: there is no CLI argument to skip specific phases during plan generation. The `_PHASE_NAMES` are applied in full for each task type. Users would need to amend the plan post-generation or hand-edit plan.json.

---

### Story 4.6 -- Reusable Plan Templates

**Rating: MINIMALLY MET**

A template skeleton exists but there is no save-as-template or template library system.

**What exists:**

- `cli/commands/execution/plan_cmd.py` lines 118-122, 161-210: `--template` flag outputs a skeleton plan.json structure for hand-editing. This is a static skeleton, not a saved template from a prior execution.
- `cli/commands/execution/plan_cmd.py` lines 111-117: `--import` flag allows importing a hand-crafted plan.json.
- `core/learn/pattern_learner.py`: `PatternLearner` learns patterns from historical executions (agent combos, templates, success rates) and applies them to future plans. This provides organic reuse without explicit templates.

**What is missing:**

- No `--save-as-template` flag: after generating or executing a plan, there is no command to save it as a named reusable template.
- No template storage directory or registry: templates are not cataloged or indexed.
- No `--use-template NAME` flag: there is no way to start a new plan from a named template.
- No template browsing/listing command (e.g., `baton plan --list-templates`).
- The `PatternLearner` provides similar *automatic* reuse via learned patterns, but this is implicit (pattern matching on task type + stack) rather than explicit (user-named templates).

---

## Cross-Cutting Observations

### Strengths

1. **Deep governance pipeline**: Risk classification, policy enforcement, auditor agent, approval workflows, and compliance logging form a layered governance system that exceeds what most competitive tools offer.

2. **Stack-adaptive intelligence**: The stack detection -> agent routing -> gate command selection pipeline is end-to-end integrated and covers 9 languages with framework detection.

3. **Closed-loop learning**: Gate pass rates, anomaly detection, pattern learning, and performance scoring form a genuine feedback loop that improves planning over time. This is a strong differentiator.

4. **SQLite persistence everywhere**: Gate results, approval results, amendments, and analytics views are all backed by SQLite with cross-project sync capability. No data loss between sessions.

### Gaps to Address

1. **Story 4.4 (Expected Outcomes)**: The `deliverables` field exists but is not wired to gate validation. Adding an `expected_outcome` field to `PlanStep` with semantic gate matching would close this gap.

2. **Story 4.6 (Reusable Templates)**: The PatternLearner provides implicit template reuse, but users cannot save, name, browse, or apply explicit templates. A thin CLI layer over plan.json export/import would close this with minimal effort.

3. **Story 1.4 (Approval Deadlines)**: The `deadline` field exists on `DecisionRequest` but is not enforced. Adding a deadline check to the daemon's polling loop would close this gap.

4. **Story 4.5 (--skip-phase)**: Simple to add as a plan-time filter on phase names.

5. **Story 1.5 (Custom Gate Plugin API)**: The current system accepts arbitrary commands but has no formal registration/discovery mechanism. A `.baton/gates/` directory convention would formalize what already works informally.
