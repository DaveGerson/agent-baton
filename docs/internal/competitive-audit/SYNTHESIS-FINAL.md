# Agent Baton Competitive Audit -- Final Synthesis

**Date:** 2026-04-16
**Branch:** `feat/actiontype-interact`
**Method:** Two-pass audit (6 solo + 6 team dialogues), architect+expert synthesis
**Inputs:** 12 audit documents, 36 user stories, 6 personas, 144 evaluations, 75+ findings

---

## How to Read This Document

This synthesis is the definitive output of the competitive audit. Each section
is structured as a dialogue between two roles:

- **Architect** -- thinks in design trade-offs, module boundaries, system
  evolution. Evaluates whether the right thing was built, not just whether
  the thing built works.
- **Expert** -- has authoritative knowledge of the agent-baton codebase.
  Verifies every claim against actual code. Challenges architectural
  assertions with implementation evidence.

The dialogue format forces rigor: the Architect cannot make sweeping claims
without the Expert checking them, and the Expert cannot get lost in code
details without the Architect asking "so what?"

---

# Section 1: Architectural Assessment

## 1.1 Architect Proposes: Three Architectural Layers, Three Maturity Levels

The audit findings cluster into three architectural layers, each at a
different maturity level:

| Layer | Maturity | Evidence |
|-------|----------|----------|
| **Planning & Intelligence** (planner, classifier, router, knowledge resolver) | Production-ready | 22/36 stories FULLY MET in solo audit; team audits confirmed plan quality |
| **Execution & Runtime** (executor, daemon, worker, launcher, scheduler) | Prototype-quality | 7 runtime boundary failures found exclusively by team audits |
| **External Surface** (API, webhooks, PMO UI, containerization, observability export) | Incomplete | 4 NOT MET stories, 3 BLOCKED items across integration themes |

This is the expected shape of an inside-out development project: the core
intelligence was built first and works well, the runtime that wraps it has
implementation gaps, and the external integration surface is partially
stubbed.

## 1.2 Expert Validates: The Evidence Supports This Framing

Confirmed. The three-layer assessment maps to code:

**Planning layer (strong):** `core/engine/planner.py` (2400+ lines) produces
well-structured plans. `core/govern/classifier.py` classifies risk across 5
signal categories. `core/orchestration/router.py` routes agents across 11
languages with framework detection. `core/engine/knowledge_resolver.py`
chains 4 resolution strategies. These modules are well-tested and
well-integrated.

**Execution layer (gaps):** The executor at `core/engine/executor.py`
(3900+ lines) handles the state machine correctly for the interactive CLI
path. But the daemon path through `core/runtime/worker.py` diverges in
critical ways:

1. `worker.py:341-347` auto-approves programmatic gates without executing
   them -- the CLI path at `execute.py:1061-1091` runs real subprocesses.
2. `claude_launcher.py:558` uses `start_new_session=True`, isolating child
   processes from signal delivery -- the graceful shutdown path only cancels
   asyncio tasks, not the actual `claude` subprocesses.
3. `executor.py:1389` makes gate failure terminal with no retry -- the
   engine sets `state.status = "failed"` permanently.
4. `health.py:55` hardcodes `ready=True` in the readiness probe.

**External surface (incomplete):** No Dockerfile. No Prometheus metrics
endpoint. Plain-text logging (`supervisor.py:394-395`). Slack interactive
buttons are formatted but have no callback handler. `baton install` cannot
self-discover bundled agents after pip install (`install.py` does raw
`Path(args.source) / "agents"` lookup).

## 1.3 Architect Refines: Root Cause Analysis

The gaps are not random. They trace to three root causes:

**Root Cause 1: CLI-first development with untested mode parity.** The
engine was built and validated in interactive CLI mode. When daemon mode was
added, it created a second code path through the execution loop that was
not subjected to the same level of scrutiny. The daemon's `TaskWorker`
makes different decisions than the CLI's `_handle_run` at gates, approvals,
and shutdown -- and these differences were never reconciled into a single,
tested execution policy.

**Root Cause 2: Operational convenience over audit integrity.** The storage
layer uses DELETE-then-INSERT for child records (`sqlite_backend.py` lines
156, 207, 228) because it simplifies the save/load lifecycle -- you always
have the in-memory state as the source of truth. But this design treats
every record as working state rather than distinguishing working state from
audit record. For operational use, this is fine. For compliance, it is
disqualifying.

**Root Cause 3: Model fields without runtime enforcement.** Several model
fields exist in the data layer but are never enforced at runtime:
`max_tokens_per_minute` in `ResourceLimits` (defined at `parallel.py:111`,
never checked), `deadline` on `DecisionRequest` (exists at `decision.py:37`,
no timer wired), budget tier thresholds (produce warnings at
`executor.py:2174-2198`, never halt execution). The pattern is consistent:
the model was designed with the intention to enforce, but enforcement was
never wired into the runtime.

## 1.4 Joint Conclusion: Architectural Decisions Assessment

### Sound Decisions (Keep)

1. **CLI-Output-as-Contract.** The boundary between the engine and Claude
   Code is structured text, never Python imports. This is the single best
   architectural decision in the project -- it enables headless execution,
   insulates from upstream changes, and makes the engine testable
   independently.

2. **SQLite as system of record.** Execution state in a database rather than
   LLM context windows is what separates agent-baton from session managers.
   The schema (24 tables, 6 analytics views, 9 migration versions) is
   well-designed and comprehensive.

3. **Deterministic state machine.** The executor's `_determine_action()`
   method is a pure function of state that returns the next action. This
   makes the engine predictable and debuggable. The action types (DISPATCH,
   GATE, APPROVAL, INTERACT, COMPLETE, FAILED) cover the full workflow
   lifecycle.

4. **Bead system for structured agent memory.** Five bead types with
   dependency graphs, quality scoring, and knowledge promotion create an
   audit trail for agent reasoning that no competitor offers. The
   `BeadSelector` injection into delegation prompts is well-designed.

5. **Federated sync architecture.** Per-project `baton.db` with a central
   `central.db` read replica is the right design for a tool that operates
   at the project level but needs cross-project analytics. The sync engine
   correctly mirrors all tables with `project_id` prefix.

### Decisions Needing Revision

6. **Single execution path should serve both CLI and daemon.** The current
   two-path design (CLI drives via `execute.py`, daemon drives via
   `worker.py`) creates behavioral divergence. The fix is not to merge the
   code paths but to extract a shared execution policy that both paths
   call. Gates should always run their commands; the question is whether
   failure is terminal or retriable.

7. **Gate failure should not be terminal.** The current design sets
   `state.status = "failed"` on gate failure with no recovery. This is
   architecturally wrong -- a gate failure means "the code does not pass
   this check," not "the entire task is doomed." The engine should
   transition to a `gate_failed` status that allows retry after fixes.

8. **Budget enforcement must be wired, not advisory.** Resource limits
   defined in the model but never enforced at runtime create false
   confidence. Either enforce the limits or remove them from the model.
   A `--token-limit` flag that converts the advisory check into a hard
   abort is the minimum viable fix.

9. **Approval records need identity.** The `ApprovalResult` model and
   `approval_results` schema table lack any human identity field. The
   `DecisionResolution` model has `resolved_by` but it defaults to the
   literal string `"human"`. This is a systemic absence, not an isolated
   gap -- no table in the entire schema carries a human identity.

### Fundamentally Wrong (Must Redesign)

10. **The learning system uses statistical language for non-statistical
    methods.** The experiment system calls itself "A/B testing" but has
    no traffic split, no concurrent control group, and no significance
    test. The pattern learner calls its heuristic ramp "confidence" but
    it is not statistical confidence. The evolution engine produces "data-
    driven proposals" but the suggestions are static templates keyed to
    metric thresholds. This is not a code bug -- it is a conceptual
    mismatch between what the system claims to do and what it actually
    does. Section 4 of this document proposes a redesign.

---

# Section 2: Strategy Document Revisions

## 2.1 Architect Proposes: The Strategy Has Three Positioning Gaps

The strategy document (`reference_files/competitive benchmarks/agent-baton-strategy.md`)
positions agent-baton around three pillars: governance, remote execution,
and learning. The audit reveals that each pillar has a credibility gap:

1. **Governance pillar:** Claims gate enforcement as the #1 differentiator,
   but daemon mode rubber-stamps gates. Claims auditor veto authority, but
   the engine does not parse or enforce auditor verdicts.

2. **Remote execution pillar:** Claims "the only tool for agents on
   infrastructure," but there is no Dockerfile, no container health checks,
   no Prometheus metrics, and no structured logging.

3. **Learning pillar:** Claims "the system gets smarter with every
   execution," but the experiment system has no statistical rigor and the
   pattern learner needs 11+ samples to surface anything.

## 2.2 Expert Validates: Specific Strategy Claims vs. Reality

| Strategy Claim | Audit Reality | Required Change |
|----------------|--------------|-----------------|
| "QA gates between every agent phase" | Daemon mode auto-approves without running commands | Must fix before this claim is credible |
| "Auditor agent with veto authority" | Veto is prompt-convention, not machine-enforced | Acknowledge limitation or add enforcement |
| "Risk classification with escalation paths" | Classification keywords are hardcoded; `ClassificationResult` is transient | Add configurable signals; persist results |
| "Execution survives agent crashes" | In-flight work lost; orphaned subprocesses continue running | Fix subprocess lifecycle management |
| "Container-ready deployment" | Zero Dockerfiles, no k8s manifests | Build deployment artifacts |
| "Learning automation is the long-term moat" | Experiments have no statistical rigor | Redesign or reframe honestly (see Section 4) |
| "Append-only audit trail" | DELETE-then-INSERT on every save | Add immutable audit log table |
| "Named approver roles with timeout escalation" | Zero human identity anywhere | Add identity tracking |
| "HMAC-signed Slack notifications with interactive buttons" | Buttons exist but clicking does nothing | Wire Slack callback handler or remove buttons |

## 2.3 Architect Refines: Revised Positioning

The strategy document should be revised to:

**Strengthen the honest claims.** The CLI-output-as-contract architecture,
SQLite state machine, bead system, and planning intelligence are genuine
differentiators that the audit validated. These should be the lead
positioning, not governance (which has enforcement gaps) or learning (which
lacks rigor).

**Reframe governance as "governance infrastructure" rather than "governance
enforcement."** The infrastructure is impressive (5 policy presets, 4 risk
tiers, auditor agent definition, approval model). The enforcement layer has
gaps that should be openly acknowledged with a roadmap to close them.

**Move the learning system from "long-term moat" to "experimental
capability."** The strategy recommends deferring learning promotion until
v0.3+. The audit validates this but goes further: the learning system
should be honestly documented as "before/after monitoring" and "heuristic
pattern recognition" rather than "statistical analysis" and "A/B testing."
Section 4 proposes a redesign that would make the learning system genuinely
valuable through a different mechanism.

**Reorder the phased roadmap.** The current strategy puts "solid foundation"
at v0.2 and "remote execution" at v0.3. The audit reveals that the
foundation has runtime enforcement gaps that must be fixed before either
Mode 1 or Mode 2 is credible. The revised order should be:

1. **v0.2: Runtime enforcement** -- Wire gates into daemon, fix subprocess
   lifecycle, add gate retry, fix readiness probe, wire budget enforcement.
2. **v0.3: Compliance and deployment** -- Append-only audit log, human
   identity, Dockerfile, structured logging, Prometheus metrics.
3. **v0.5: Integration surface** -- CI pipeline gates, Slack interactivity,
   export pipeline, plan templates, RBAC.
4. **v1.0: Learning redesign** -- Pipeline-based learning (see Section 4),
   convergence measurement, cross-project sharing improvements.

## 2.4 Joint Conclusion: Strategy Revisions Summary

The strategy document's core thesis remains valid: agent-baton occupies an
unclaimed category (governance + observability for agent teams) that session
managers do not address. The competitive analysis of the ecosystem is
accurate. The two-mode strategy is sound.

What must change:

1. Remove claims about features that don't work at runtime (daemon gates,
   auditor enforcement, budget enforcement, Slack interactivity).
2. Reposition learning from "moat" to "experimental capability with
   roadmap."
3. Reorder the roadmap to fix runtime enforcement before adding new
   features.
4. Add a "known limitations" section that builds trust through honesty
   rather than eroding it through over-promising.

---

# Section 3: User Story Revisions

## 3.1 Methodology

For each of the 36 user stories, the audit revealed whether the acceptance
criteria were (a) correct and met, (b) correct but unmet, (c) missing
criteria the audit exposed, or (d) over-specified for the problem scope.
This section proposes revisions to acceptance criteria based on what the
audit actually found.

## 3.2 Theme 1: Governance and Quality Assurance

### Story 1.1 -- Automated Quality Gates Between Phases

**Original rating:** FULLY MET (solo), confirmed by team audits as
architecturally present.

**Audit-exposed gap:** Gate failure is terminal (no retry). Daemon mode
does not execute gate commands.

**Revised acceptance criteria -- ADD:**
- Gate failure transitions to a `gate_failed` state that permits retry
  after code fixes, rather than permanently setting `status = "failed"`
- Gates execute identically in daemon mode and CLI mode (same subprocess
  execution, same pass/fail logic)
- `baton execute retry-gate --phase-id N` re-runs the gate without
  re-dispatching the agent

**Revised acceptance criteria -- MODIFY:**
- "Gate failures block progression" should become "Gate failures block
  progression AND are retriable without restarting the entire execution"

---

### Story 1.2 -- Risk-Based Task Classification

**Original rating:** FULLY MET (solo), PARTIAL (team -- classification
signals are hardcoded, `ClassificationResult` is transient).

**Revised acceptance criteria -- ADD:**
- Teams can add custom classification keywords via configuration file
  (not only via code changes to `classifier.py`)
- `ClassificationResult` (signals found, confidence, source) is persisted
  to SQLite alongside the plan for audit reconstruction
- Classification results are immutable after plan creation

**Revised acceptance criteria -- MODIFY:**
- "Teams can define custom risk rules" should distinguish between
  classification rules (what signals trigger what risk level) and policy
  rules (what happens after classification). Both must be configurable.

---

### Story 1.3 -- Auditor Agent with Veto Authority

**Original rating:** FULLY MET (solo), PARTIAL (team -- enforcement is
prompt-convention only).

**Revised acceptance criteria -- ADD:**
- Engine validates that auditor step exists in HIGH-risk plans before
  execution starts (not just advisory warning from policy engine)
- Engine parses auditor output for BLOCK/REVISE verdicts and refuses to
  advance to the next phase when BLOCK is detected
- Auditor verdict is stored as a structured field (`auditor_verdict`)
  in `step_results`, not only in compliance report markdown

**Current implementation exceeds story on:** Auditor has 3 operating modes
(pre-execution, mid-execution, post-execution) where the story only
required one. Trust levels per agent (Full Autonomy / Supervised /
Restricted / Plan Only) exceed the story's scope.

---

### Story 1.4 -- Approval Workflows with Deadlines

**Original rating:** PARTIALLY MET (solo and team agree).

**Revised acceptance criteria -- MODIFY:**
- "Approver roles can be defined with named individuals or groups" --
  requires adding `approved_by TEXT` and `approver_role TEXT` to
  `approval_results` schema, and `actor TEXT` to all write paths
- "On timeout, approval escalates" -- requires wiring the `deadline` field
  on `DecisionRequest` to a timer in the daemon's polling loop

**Revised acceptance criteria -- ADD:**
- Approval records must include: who approved, when, from which interface
  (CLI, API, Slack), and justification text
- `baton query approvals --task TASK_ID` returns complete approval chain
  with identity

---

### Story 1.5 -- Custom Gate Scripts

**Original rating:** PARTIALLY MET. The plan `gate_command` field accepts
arbitrary shell commands, but there is no plugin registry, no gate
composition, and no `.baton/gates/` directory convention.

**Revised acceptance criteria -- KEEP AS-IS.** The acceptance criteria
correctly describe what is missing. No revision needed.

---

### Story 1.6 -- Gate Analytics and Trend Analysis

**Original rating:** FULLY MET.

**Audit-exposed gap (team-James #5):** Gate failure reasons are not
categorized. Gate output is free text. No "why" analysis.

**Revised acceptance criteria -- ADD:**
- Gate failure output includes the command that was executed (not just the
  result) for traceability
- Gate results record exit code and stderr separately from stdout

---

## 3.3 Theme 2: Visibility and Observability

### Story 2.1 -- Real-Time Agent Status Dashboard

**Original rating:** FULLY MET. The PMO UI exceeds story requirements
with SSE, Kanban, analytics, data export, and external items panel.

**Audit-exposed gap (team-James #1):** No stale-card detection in the UI.
No per-person approval filtering. No time-windowed cost data.

**Revised acceptance criteria -- ADD:**
- Cards not updated within a configurable threshold (default 24h) show a
  visual "stale" indicator
- Clicking a task opens an `ExecutionProgress` modal with real-time
  step-by-step event streaming (this EXISTS but was not in the story)

---

### Story 2.2 -- Complete Execution Audit Trail

**Original rating:** FULLY MET (solo), BLOCKED (team-David -- mutable
audit trail, DELETE-then-INSERT pattern).

**This is the most critical story revision.** The acceptance criteria
include "Audit records are append-only -- no mutation or deletion of
historical records." This criterion is NOT MET despite the solo audit
rating it FULLY MET.

**Revised acceptance criteria -- MODIFY:**
- "Audit records are append-only" must be enforced via a separate
  `audit_log` table with SQLite triggers preventing UPDATE/DELETE, not
  by changing the operational `save_execution()` pattern
- "Each record includes: timestamp, task ID, phase ID, agent ID, inputs,
  outputs, duration, token cost" must add: `actor TEXT` (human identity)
  for all human-initiated actions

**Revised acceptance criteria -- ADD:**
- The compliance JSONL log (`compliance-audit.jsonl`) should be cryptographically
  signed or checksummed for tamper detection
- `interaction_history` and `feedback_results` must be persisted to SQLite
  (currently exist in-memory but are lost on save/load cycle)

---

### Story 2.3 -- Agent Decision Reasoning via Beads

**Original rating:** FULLY MET.

**Audit-exposed gap (team-Maya #9):** Bead emission depends on agent
compliance with prompt instructions. Quality scores are agent-self-reported
with no ground truth.

**Revised acceptance criteria -- ADD:**
- Bead emission rate is tracked per agent (what percentage of dispatches
  produce beads) as a signal quality metric
- Bead quality scoring distinguishes producer from consumer feedback

**No criteria removed.** The implementation genuinely exceeds requirements.

---

### Story 2.4 -- Cost and Efficiency Visibility

**Original rating:** PARTIALLY MET.

**Revised acceptance criteria -- ADD:**
- Configurable price-per-token table for dollar cost conversion
- Budget alert notification when spending approaches threshold (wire to
  webhook system)

**Revised acceptance criteria -- REMOVE:**
- "Trend lines compare current period to previous periods" and "cost
  attribution supports tagging" are over-specified for this product's
  scale. A SQL escape hatch (`baton query --sql`) provides this
  capability without building a custom analytics UI.

---

### Story 2.5 -- Webhook Notifications

**Original rating:** FULLY MET (solo), PARTIAL (team-James #6 -- Slack
interactive buttons non-functional).

**Revised acceptance criteria -- MODIFY:**
- "Interactive notifications where supported (Slack buttons)" should be
  either fully wired (Slack callback endpoint + signature verification)
  or removed from the acceptance criteria. Decorative buttons that do
  nothing are worse than no buttons.
- "Subscription management via CLI" -- add `baton webhook add/list/remove`
  CLI commands (currently API-only)

---

### Story 2.6 -- Automated Retrospective Generation

**Original rating:** FULLY MET. No revision needed. Implementation exceeds
requirements with implicit gap detection and JSON sidecars.

---

## 3.4 Theme 3: Remote and Headless Execution

### Story 3.1 -- Headless Execution on Remote VMs

**Original rating:** PARTIALLY MET.

**Audit-exposed gaps (team-Priya):** Double-fork PID-1 trap in containers.
flock/NFS caveat. Orphaned subprocess problem. Readiness probe hardcoded.

**Revised acceptance criteria -- ADD:**
- Daemon `--foreground` mode must be the documented default for container
  deployment (double-fork daemonization must not be used in containers)
- Active subprocess PIDs must be tracked and explicitly terminated on
  daemon shutdown (not orphaned via `start_new_session=True`)
- Readiness probe must check: SQLite connectivity, engine status != failed,
  disk space; must return `ready=False` when checks fail

**Revised acceptance criteria -- MODIFY:**
- "Container-ready deployment" should specify: Dockerfile with
  `--foreground`, documented volume mounts for `.claude/team-context/`,
  documented `WORKDIR` requirement
- "Multiple daemon instances can run simultaneously" should be scoped to
  "on the same machine" -- multi-machine scaling requires architectural
  work beyond this story

---

### Story 3.2 -- Crash Recovery with Context Reconstruction

**Original rating:** PARTIALLY MET.

**Revised acceptance criteria -- ADD:**
- `baton execute resume` must call `recover_dispatched_steps()` to clear
  stale "dispatched" markers (currently wired in daemon but not in
  interactive resume path)
- Resume history must distinguish between "dispatched and running" vs.
  "completed but unrecorded" steps

**Revised acceptance criteria -- MODIFY:**
- "Context reconstruction is deterministic" is aspirational. Revise to:
  "Context reconstruction recovers the execution state to the last
  persisted checkpoint; in-flight agent work that was not yet recorded is
  re-dispatched from scratch."
- "Resume works across different machines" -- remove this criterion. SQLite
  files are local. Cross-machine portability requires architectural work.

---

### Story 3.3 -- Parallel Execution with Git Worktree Isolation

**Original rating:** NOT MET. Zero worktree code in agent_baton.

**Acceptance criteria: KEEP AS-IS.** The story correctly describes what is
needed. No revision required.

---

### Story 3.4 -- API-Driven Execution Triggering

**Original rating:** PARTIALLY MET.

**Revised acceptance criteria -- REMOVE:**
- "Python, JavaScript, and Go client libraries" is over-specified. The
  auto-generated OpenAPI spec enables client generation without maintaining
  separate libraries.
- "Rate limiting and quota enforcement per API key" -- defer to RBAC story.

---

### Story 3.5 -- Multi-Day Workflow Support

**Original rating:** PARTIALLY MET.

**Revised acceptance criteria -- MODIFY:**
- "Executions can be paused (`baton execute pause`)" -- the current
  interrupt-and-resume pattern works. An explicit pause command is nice
  but not essential; the existing behavior (Ctrl-C + `baton execute resume`)
  achieves the goal.
- "Checkpoint frequency is configurable" -- remove. State is saved after
  every mutation, which is the correct behavior. Configurable frequency
  adds complexity without benefit.

---

### Story 3.6 -- Resource Governance and Quotas

**Original rating:** MINIMALLY MET.

**Revised acceptance criteria -- ADD:**
- `--token-limit N` flag on both `baton execute run` AND `baton daemon start`
  that converts the advisory `_check_token_budget()` into a hard abort
- `--max-steps N` flag on `baton daemon start` (currently only on
  `baton execute run`)
- `baton daemon halt` for emergency stop (SIGKILL to all workers)

**Revised acceptance criteria -- REMOVE:**
- "Per-user/per-project quotas" and "quota status visible in PMO
  dashboard" are over-specified for a solo-to-small-team tool. Hard cost
  caps per execution are sufficient.

---

## 3.5 Theme 4: Planning Intelligence

### Stories 4.1 (Stack Detection), 4.2 (Risk-Aware Plans), 4.3 (Plan Amendment)

**All FULLY MET.** No acceptance criteria changes required.

**Audit-exposed nuance for 4.1 (team-Maya #8):** Frameworks without
config files (FastAPI, HTMX, Flask) are invisible to the router. Consider
adding dependency-based detection (scan `requirements.txt` for known
frameworks) as a new acceptance criterion.

---

### Story 4.4 -- Demo Statements / Expected Outcomes

**Original rating:** MINIMALLY MET.

**Acceptance criteria: KEEP AS-IS.** The story correctly describes the gap.
The `deliverables` field exists but is not validated by gates.

---

### Story 4.5 -- Complexity Override

**Original rating:** PARTIALLY MET.

**Revised acceptance criteria -- MODIFY:**
- Remove `--skip-phase` as a required flag. The `--complexity light` flag
  already reduces to a single phase. Fine-grained phase skipping is
  better served by plan editing via `baton plan --import`.

---

### Story 4.6 -- Reusable Plan Templates

**Original rating:** MINIMALLY MET.

**Acceptance criteria: KEEP AS-IS.** The `PatternLearner` provides implicit
reuse but the story correctly asks for explicit user-defined templates.

---

## 3.6 Theme 5: Learning and Continuous Improvement

### Stories 5.1-5.6

**The learning theme requires the most significant revision.** See Section 4
for the full redesign. In summary:

**Story 5.1 (Patterns):** MODIFY acceptance criterion "Statistical
significance thresholds prevent false positives" -- replace with "Minimum
sample requirements are clearly documented and the confidence metric is
honestly labeled as a heuristic ramp."

**Story 5.2 (Scoring):** ADD "Per-stack scorecard filtering" as a criterion.
Current scorecards are per-agent only.

**Story 5.3 (Prompt Evolution):** MODIFY "specific text changes" criterion
-- the current static-template suggestions are a reasonable starting point.
Add "proposals reference specific retrospective entries and failure patterns"
instead of generic advice.

**Story 5.4 (Anomaly Detection):** ADD "Anomaly events publish to the
EventBus so the webhook system can deliver real-time alerts."

**Story 5.5 (Knowledge Gaps):** ADD "Verification step measures whether gap
recurrence decreased after knowledge pack was populated."

**Story 5.6 (Experiments):** REWRITE this story entirely. The current
acceptance criteria describe A/B testing with traffic splits, which is
inappropriate for this problem scale (see Section 4). Replace with:
"Before/after monitoring with clear sample requirements and honest
documentation of limitations."

---

## 3.7 Theme 6: Integration and Extensibility

### Story 6.1 (CI Pipeline Integration)

**NOT MET. Acceptance criteria: KEEP AS-IS.** The story correctly
describes the needed capability.

### Story 6.2 (Talent Builder)

**PARTIALLY MET.** ADD: "Generated agent file is validated against
frontmatter schema before persisting."

### Story 6.3 (Webhook Notifications)

**PARTIALLY MET.** MODIFY: Either wire Slack interactive buttons end-to-end
or remove the interactive criterion. ADD: `baton webhook add/list/remove`
CLI commands.

### Story 6.4 (Structured Handoff)

**PARTIALLY MET.** ADD: "Handoff accumulates across phases (rolling summary)
rather than including only the most recent step's outcome."

### Story 6.5 (Exportable Audit Reports)

**NOT MET. Acceptance criteria: KEEP AS-IS.**

### Story 6.6 (Plugin Architecture)

**NOT MET.** MODIFY: Reduce scope. Instead of a full plugin marketplace,
target a `GateEvaluator` protocol that custom Python modules can implement,
registered via `learned-overrides.json`. This is architecturally consistent
with the existing adapter pattern in `core/storage/adapters/`.

---

# Section 4: Learning System Redesign

## 4.1 Architect Proposes: Learning as Pipeline Template

The current learning system uses statistical language for non-statistical
methods. The user's feedback is correct: using statistics here does not make
sense for this problem scale. A solo developer or small team will never
generate the sample sizes needed for statistical significance in A/B testing
or pattern detection.

The redesign thesis: **the learning system should be a structured, repeatable
baton execution plan template that runs periodically, not a statistical
analysis engine.**

What does "learning as a pipeline template" mean? Instead of the current
system (which tries to be a data science engine), the learning system
becomes a baton plan that the orchestrator executes after every N
completions:

```
Plan: "Learning Cycle for project X"
Phase 1: COLLECT -- Gather metrics from last N executions
Phase 2: ANALYZE -- Agent reviews metrics and retrospectives
Phase 3: PROPOSE -- Agent produces specific, actionable recommendations
Phase 4: REVIEW -- Human approves or rejects proposals (APPROVAL gate)
Phase 5: APPLY -- System applies approved changes
Phase 6: DOCUMENT -- Record what changed and why
```

This is a baton execution plan. It uses the same engine, same gates, same
approval model, same persistence. The "learning system" is not a separate
statistical engine -- it is a specific kind of baton workflow.

## 4.2 Expert Validates: What This Replaces and What It Keeps

**What gets replaced:**

| Current Component | Problem | Replacement |
|-------------------|---------|-------------|
| `ExperimentManager` with A/B claims | No traffic split, no stats, 5 samples | Remove. Before/after monitoring via metrics queries |
| `PatternLearner.confidence` formula | Heuristic ramp called "confidence" | Replace with simple "N successful executions of this type" count |
| `PromptEvolutionEngine` static templates | "Add quality checklist" is not actionable | Agent reads retrospectives and proposes specific text changes |
| `TriggerEvaluator` statistical thresholds | Threshold-based anomaly detection with no statistical basis | Keep the thresholds but document them honestly as operational alerts |
| `ImprovementLoop._evaluate_running_experiments` | Evaluates fake experiments | Remove. The pipeline template handles improvement cycles |

**What gets kept:**

| Component | Why |
|-----------|-----|
| `PerformanceScorer` with scorecards | Good metrics collection; OLS trend is simple and honest |
| `PatternLearner.analyze()` grouping | Finding common agent sequences is useful; just don't call it statistical |
| `BudgetTuner` recommendations | Historical average is a good budget heuristic |
| `LearnedOverrides` persistence | The right mechanism for storing operational adjustments |
| `LearningInterviewer` | Human-directed resolution of open issues is valuable |
| `BeadStore` with quality scoring | Quality signals from downstream agents are useful |
| Knowledge gap detection pipeline | Well-engineered detection through resolution |

## 4.3 Architect Refines: The Pipeline Template Architecture

The learning pipeline template would look like this in the engine:

**Step 1: Define the template.** A JSON template stored in
`.claude/templates/learning-cycle.json` that describes the phases,
agents, and gates for a learning cycle. This uses the same `MachinePlan`
model as any other plan.

**Step 2: Trigger the template.** After every N completed executions (N
configurable, default 10), the engine checks whether a learning cycle
should run. This is a simple counter, not a statistical trigger. The
check happens in `ExecutionEngine.complete()`.

**Step 3: Execute as a normal plan.** The learning cycle plan is created
via `create_plan()` with `task_type="learning-cycle"` and executed through
the standard engine. The COLLECT phase gathers metrics via SQL queries.
The ANALYZE phase dispatches an agent that reads the metrics and
retrospectives. The PROPOSE phase produces specific recommendations. The
REVIEW phase is an APPROVAL gate for human review. The APPLY phase
writes to `learned-overrides.json`.

**Step 4: Record outcomes.** The learning cycle itself is an execution
with traces, retrospectives, and beads. It benefits from the same
observability stack as any other execution.

**What this simplifies:**

1. **No `ExperimentManager` needed.** If you want to test a change, apply
   it and measure through the next learning cycle. Before/after comparison
   via scorecard metrics.

2. **No `ImprovementLoop` needed as a separate subsystem.** The learning
   cycle IS the improvement loop, running as a standard baton execution.

3. **No statistical machinery needed.** The ANALYZE agent (which is a Claude
   model) reads retrospectives and metrics in natural language and produces
   natural-language recommendations. Humans review and approve. This is
   more honest and more effective than fake statistics.

4. **No confidence formula needed.** Pattern detection becomes: "You have
   used backend-engineer--python + test-engineer for 'new-feature' tasks 8
   times with 87% success. Should this be the default?" presented to a
   human during the REVIEW phase.

## 4.4 Expert Challenges: What About Automated Improvement?

The current system has auto-apply capability for low-risk recommendations
(e.g., gate command corrections, agent flavor adjustments). The pipeline
template approach requires human approval for everything. Does this
regress automation?

## 4.5 Architect Responds: Two Tiers

The pipeline template handles the "interesting" learning (agent
performance, workflow patterns, prompt improvements). For operational
corrections (gate command typos, agent flavor mismatches), keep the
existing `LearnedOverrides` auto-apply path -- these are simple
corrections that do not need the full pipeline.

| Tier | Mechanism | Human Review |
|------|-----------|:------------:|
| **Operational corrections** (gate commands, flavors, agent drops) | `LearnedOverrides` auto-apply via `LearningEngine.apply()` | No |
| **Workflow improvements** (agent selection, phase structure, prompt changes) | Pipeline template with APPROVAL gate | Yes |

This preserves the useful automation while removing the pretense of
statistical rigor for decisions that genuinely benefit from human judgment.

## 4.6 Joint Conclusion: Learning System Redesign Summary

**Remove:**
- `ExperimentManager` and all A/B testing claims
- `ImprovementLoop` as a separate subsystem
- `PromptEvolutionEngine` static template suggestions
- `PatternLearner.confidence` formula (replace with simple counts)
- Statistical language throughout documentation and CLI output

**Keep:**
- `PerformanceScorer` with scorecards and OLS trends
- `PatternLearner` grouping logic (just relabel confidence as "sample count")
- `BudgetTuner` recommendations
- `LearnedOverrides` for operational auto-corrections
- `LearningInterviewer` for human-directed resolution
- Knowledge gap detection pipeline
- `TriggerEvaluator` thresholds (relabeled as operational alerts)

**Add:**
- Learning cycle plan template (JSON in `.claude/templates/`)
- Trigger mechanism in `ExecutionEngine.complete()` (counter-based)
- ANALYZE agent definition for reading metrics and producing recommendations
- APPLY phase that writes to `learned-overrides.json`
- `baton learn cycle` CLI command to manually trigger a learning cycle

**Net effect on codebase:** Removes ~1500 lines of experiment/evolution/loop
code. Adds ~300 lines of template + trigger + CLI. The learning system
becomes simpler, more honest, and more effective because humans review
the actual recommendations instead of trusting fake statistics.

---

# Section 5: Value of Agent Teams for Future Audits

## 5.1 Architect Proposes: The Team Method is Categorically Better for Production-Readiness Evaluation

The numbers tell the story:

| Metric | Solo Agents | Team Dialogues |
|--------|:-----------:|:--------------:|
| Total evaluations | ~80 | 72 |
| Unique findings | ~40 | 75+ (40+ team-exclusive) |
| CRITICAL findings | 3 | 7 |
| Items downgraded from solo | -- | 23 |
| Average rating | ~65% WORKS | ~18% WORKS |

But the numbers understate the qualitative difference. The solo agents
answered "does this feature exist?" The team dialogues answered "does this
feature work correctly under real conditions?"

## 5.2 Expert Validates: Concrete Examples of the Probe-Discover Cycle

**Example 1: Readiness probe (Priya)**
- Solo found: Health endpoint exists, returns status + version. Rating: WORKS.
- Team probe: "Does the readiness probe actually check meaningful state?"
- Team discover: `ready=True` is hardcoded. Rating: PARTIAL.
- Impact: The difference between "safe to deploy to k8s" and "k8s will
  route traffic to a broken pod."

**Example 2: Gate failure behavior (Maya)**
- Solo found: Gates exist, run between phases, persist to SQLite. Rating: WORKS.
- Team probe: "What happens when a gate fails? Can I retry?"
- Team discover: Gate failure is terminal. No retry. Entire execution lost.
  Rating: BLOCKED.
- Impact: The difference between "usable iterative workflow" and
  "start over from scratch on every test failure."

**Example 3: Daemon gate behavior (Carlos)**
- Solo found: Daemon auto-approves gates for LOW risk. Rating: WORKS.
- Team probe: "Do programmatic gates in daemon mode actually run the
  command?"
- Team discover: Daemon auto-approves WITHOUT running gate commands.
  Rating: disclosed as critical behavioral split.
- Impact: The difference between "governance works in production" and
  "governance is theater in production."

**Example 4: Auditor independence (David)**
- Solo found: Auditor agent has veto authority, runs independently. Rating: WORKS.
- Team probe: "Can the auditor be bypassed? What enforcement mechanisms
  exist?"
- Team discover: Enforcement is prompt-convention only. Engine does not
  parse auditor verdicts. Rating: PARTIAL.
- Impact: The difference between "compliance control exists" and
  "compliance control is a suggestion."

## 5.3 Architect Refines: When to Use Each Method

| Situation | Recommended Method | Rationale |
|-----------|--------------------|-----------|
| Feature inventory / coverage check | Solo agents | Fast, broad, sufficient for "does it exist?" |
| Production readiness evaluation | Team dialogue | Probe-discover cycle catches enforcement gaps |
| Security / compliance audit | Team dialogue (adversarial) | David's team found 15 issues solo missed |
| UX / workflow validation | Team dialogue (persona-driven) | Maya's and Carlos's teams found workflow blockers |
| Architecture review | Team dialogue (architect+expert) | Forces design reasoning, not just code checking |
| Performance / scalability | Solo agents with metrics | Quantitative checks don't benefit from dialogue |

## 5.4 How to Structure the Persona+Expert Dialogue

The optimal structure, validated by this audit:

1. **Persona asks an operationally grounded question.** Not "does crash
   recovery exist?" but "my laptop sleeps mid-task. Next morning I open
   the terminal. What commands do I run? How much progress is lost?"

2. **Expert investigates with file:line evidence.** No hand-waving. Every
   claim cites a specific file and line number.

3. **Persona probes the edge case.** This is where the value is. "What if
   the crash happened mid-agent-dispatch?" "What about the actual `claude`
   subprocess?" "Can I fix the code and retry?"

4. **Expert discovers second-pass findings.** The probe forces deeper
   investigation that surface-level scanning missed.

5. **Joint verdict with delta from solo audit.** Explicitly comparing to
   the solo finding prevents confirmation bias.

The probe step is the critical differentiator. Personas ask questions that
solo agents do not generate because solo agents check features, not
scenarios. The persona's operational context ("I'm leaving agents running
overnight," "an external auditor is reviewing our records") generates
questions that test the implementation against real-world conditions.

## 5.5 Which Personas Benefited Most from the Team Method

| Persona | Solo WORKS | Team WORKS | Delta | Why Team Method Helped |
|---------|:---------:|:---------:|:-----:|----------------------|
| David (Compliance) | 7/15 | 0/12 | Massive | Adversarial probing found systemic identity gaps, mutable storage paths, and unauditable bypass vectors that feature-checking cannot detect |
| James (Eng Manager) | 6/10 | 1/12 | Major | Operational questions ("can I delegate approvals by risk?") revealed architectural absences that feature inventory misses |
| Priya (Platform Eng) | 10/20 | 1/10 | Major | Infrastructure edge cases (PID-1 in containers, NFS flock, orphaned processes) only surface under operational probing |
| Tomoko (Workflow Designer) | 13/17 | 1/12 | Major | Statistical rigor questions exposed that the learning system's claims exceed its implementation |
| Maya (Solo Power User) | 9/13 | 5/12 | Significant | Workflow scenarios (gate failure, crash recovery, context handoff) revealed UX blockers |
| Carlos (Overnight Batch) | 8/13 | 5/12 | Moderate | Failure isolation and daemon reliability questions added depth but fewer surprises |

David benefited most because compliance evaluation is inherently adversarial
-- the auditor is looking for what is wrong, not what is right. Solo agents
check "does the feature exist?" which is the wrong question for compliance.
The right question is "can this feature be circumvented, and if so, is the
circumvention detectable?"

## 5.6 Recommendation: Agent-Baton as an Audit Capability

This audit methodology -- persona+expert dialogue with probe-discover
cycles -- could itself become a baton plan template:

```
Plan: "Product Audit Cycle"
Phase 1: DEFINE SCOPE -- Identify personas and user stories to audit
Phase 2: SOLO SCAN -- Dispatch solo agents for feature inventory (parallel)
Phase 3: TEAM DIALOGUE -- For each persona, dispatch persona+expert pair
          with probe-discover cycle
Phase 4: SYNTHESIZE -- Architect+expert dialogue produces synthesis
Phase 5: REVIEW -- Human reviews and approves findings (APPROVAL gate)
```

This would be a reusable audit plan template that any project could apply
to itself. The persona definitions and user stories would be project-
specific inputs; the methodology (solo scan, team dialogue, synthesis)
would be the template structure.

---

# Section 6: Revised Execution Plan

## 6.1 Architect Proposes: Priority-Ordered by Architectural Cluster

The execution plan is organized by architectural cluster (not by persona),
because fixes within a cluster share code paths and can be batched.

## 6.2 Phase 0: Runtime Enforcement (3-5 days)

**Goal:** Make the engine's runtime behavior match its architectural claims.
These are pre-release blockers -- the governance story is not credible
without them.

| # | Work Item | Cluster | Effort | Dependencies |
|---|-----------|---------|--------|--------------|
| 0.1 | Wire `GateRunner` into `TaskWorker` -- daemon must execute real gate commands | Execution | 1-2d | None |
| 0.2 | Track subprocess PIDs in `StepScheduler`; terminate on `CancelledError` | Execution | 1d | None |
| 0.3 | Fix readiness probe -- check SQLite, engine status, disk; return `ready=False` on failure | Execution | 0.5d | None |
| 0.4 | Wire `recover_dispatched_steps()` into interactive `resume` path | Execution | 0.5d | None |
| 0.5 | Add gate retry -- `gate_failed` status with `baton execute retry-gate --phase-id N` | Execution | 1-2d | None |

**Justification:** Item 0.1 is the single highest-leverage fix. The #1
competitive differentiator (QA gates) does not work in the #1
differentiated use case (headless execution). Items 0.2-0.4 fix the
three other runtime boundary failures. Item 0.5 transforms a UX
dealbreaker (gate failure = start over) into a normal iterative workflow.

## 6.3 Phase A: Compliance and Identity (6-8 days)

**Goal:** Unblock David's persona. Convert the compliance story from a
liability to a strength.

| # | Work Item | Cluster | Effort | Dependencies |
|---|-----------|---------|--------|--------------|
| A1 | Append-only `audit_log` table with SQLite trigger preventing UPDATE/DELETE | Storage | 2d | None |
| A2 | Add `actor TEXT` column to `approval_results`, `gate_results`; populate from `$USER` or API token | Storage | 1-2d | None |
| A3 | Persist `ClassificationResult` (signals, confidence, source) to SQLite | Storage | 1d | None |
| A4 | Persist `interaction_history` and `feedback_results` to SQLite | Storage | 1d | None |
| A5 | Secret redaction layer -- pattern-matching scrub before persistence (AWS keys, GitHub tokens, JWTs, generic API keys) | Storage | 1-2d | None |
| A6 | Gate command traceability -- store command, exit code, stderr in `gate_results` | Storage | 0.5d | Phase 0.1 |

**Justification:** Items A1-A2 close the two compliance dealbreakers
(mutable trail, missing identity). Items A3-A4 fix data loss. Item A5
addresses the secret exposure surface (8+ storage locations). Item A6
enables gate result verification.

## 6.4 Phase B: Cost Enforcement and Deployment (5-7 days)

**Goal:** Unblock Carlos (overnight execution) and Priya (deployment).

| # | Work Item | Cluster | Effort | Dependencies |
|---|-----------|---------|--------|--------------|
| B1 | `--token-limit N` flag on `baton execute run` AND `baton daemon start` -- hard abort | Cost | 1-2d | None |
| B2 | `--max-steps N` flag on `baton daemon start` | Cost | 0.5d | None |
| B3 | Dockerfile + docker-compose with `--foreground`, volume mounts, health probes | Deployment | 1-2d | Phase 0.3 |
| B4 | Structured JSON logging via `python-json-logger` | Deployment | 1d | None |
| B5 | Prometheus `/metrics` endpoint (dispatched_steps, completed_tasks, gate_results, tokens) | Deployment | 1-2d | None |
| B6 | Fix `baton install` to resolve agents from installed package via `importlib.resources` | Install | 1d | None |

**Justification:** B1-B2 are the cost governance minimum viable product.
B3-B5 are the deployment minimum viable product. B6 fixes the first-run
path for pip-installed users.

## 6.5 Phase C: Integration Surface (8-10 days)

**Goal:** Close the integration gaps that affect James and Priya.

| # | Work Item | Cluster | Effort | Dependencies |
|---|-----------|---------|--------|--------------|
| C1 | Accumulating handoff context -- rolling summary across all completed phases | Handoff | 1-2d | None |
| C2 | Named approver roles with risk-based routing | Identity | 2-3d | Phase A2 |
| C3 | CI pipeline gate type (GitHub Actions Checks API) | Integration | 3-4d | Phase 0.1 |
| C4 | Slack interactive callback endpoint OR remove decorative buttons | Integration | 1-2d | None |
| C5 | `baton export --task TASK_ID --format csv|json` audit report assembly | Export | 2d | Phase A1 |
| C6 | `baton webhook add/list/remove` CLI commands | CLI | 1d | None |

## 6.6 Phase D: Learning Redesign (5-7 days)

**Goal:** Replace the pseudo-statistical learning system with a pipeline
template approach per Section 4.

| # | Work Item | Cluster | Effort | Dependencies |
|---|-----------|---------|--------|--------------|
| D1 | Define learning cycle plan template (JSON) | Learning | 1d | None |
| D2 | Add ANALYZE agent definition for metrics review | Learning | 1d | None |
| D3 | Trigger mechanism in `ExecutionEngine.complete()` (counter-based) | Learning | 0.5d | None |
| D4 | `baton learn cycle` CLI command for manual trigger | Learning | 0.5d | D1 |
| D5 | Relabel `PatternLearner.confidence` as sample count; lower threshold | Learning | 0.5d | None |
| D6 | Remove `ExperimentManager`, `ImprovementLoop`, `PromptEvolutionEngine` | Learning | 1d | D1-D4 |
| D7 | Document learning system honestly -- capabilities and limitations | Docs | 1d | D1-D6 |

## 6.7 Phase E: Power User and Polish (ongoing)

| # | Work Item | Effort |
|---|-----------|--------|
| E1 | Explicit plan template save/load (`baton plan save-template / from-template`) | 2d |
| E2 | Per-stack scorecard filtering on `baton scores` | 1d |
| E3 | Task-type dimension in routing overrides (`LearnedOverrides`) | 1d |
| E4 | PyPI publication | 0.5d |
| E5 | Git worktree isolation for parallel agents | 3-4d |
| E6 | RBAC / scoped API tokens | 2-3d |
| E7 | Dependency-based framework detection (scan requirements.txt) | 1d |

## 6.8 Effort Summary

| Phase | Days | Cumulative | Unlocks |
|-------|:----:|:----------:|---------|
| Phase 0 (Runtime) | 3-5 | 3-5 | Governance credibility |
| Phase A (Compliance) | 6-8 | 9-13 | David persona; regulated industry |
| Phase B (Cost + Deploy) | 5-7 | 14-20 | Carlos persona (overnight); Priya persona (containers) |
| Phase C (Integration) | 8-10 | 22-30 | James persona (team governance); integration surface |
| Phase D (Learning) | 5-7 | 27-37 | Honest learning system; simplified codebase |
| Phase E (Polish) | Ongoing | -- | Power user features; PyPI; worktrees |

**Phases 0+A+B represent ~15-20 days of focused work** and would transform
agent-baton from "impressive demo" to "production-ready for teams."

## 6.9 Dependency Chain

```
Phase 0 ─────────────────────────────┐
  │                                   │
  ├── 0.1 (daemon gates)             │
  │     └── A6 (gate traceability)   │
  │     └── C3 (CI gate type)        │
  │                                   │
  ├── 0.3 (readiness probe)          │
  │     └── B3 (Dockerfile)          │
  │                                   │
  └── 0.5 (gate retry)              │
                                      │
Phase A ──────────────────────────────┤
  │                                   │
  ├── A1 (audit log)                 │
  │     └── C5 (export)              │
  │                                   │
  ├── A2 (identity)                  │
  │     └── C2 (named approvers)     │
  │                                   │
  └── A3-A5 (persist + redact)      │
                                      │
Phase B ──────────────────────────────┤ (independent of A)
  │                                   │
  ├── B1-B2 (cost enforcement)       │
  ├── B3-B5 (deployment)             │
  └── B6 (install fix)              │
                                      │
Phase C ──────────────────────────────┤ (depends on A2, 0.1)
                                      │
Phase D ──────────────────────────────┘ (independent)
```

Phases 0, A, and B can run in partial parallel. Phase C depends on A2 and
0.1. Phase D is independent and can run at any time.

---

## Audit Methodology Reference

### Source Documents

**Solo audit (Pass 1):**
- `docs/competitive-audit/theme-1-4-governance-planning.md`
- `docs/competitive-audit/theme-2-5-observability-learning.md`
- `docs/competitive-audit/theme-3-6-remote-integration.md`
- `docs/competitive-audit/persona-maya-carlos.md`
- `docs/competitive-audit/persona-james-david.md`
- `docs/competitive-audit/persona-priya-tomoko.md`

**Team audit (Pass 2):**
- `docs/competitive-audit/team-priya-expert.md`
- `docs/competitive-audit/team-maya-expert.md`
- `docs/competitive-audit/team-carlos-expert.md`
- `docs/competitive-audit/team-james-expert.md`
- `docs/competitive-audit/team-david-expert.md`
- `docs/competitive-audit/team-tomoko-expert.md`

**Benchmark sources:**
- `reference_files/competitive benchmarks/agent-baton-strategy.md`
- `reference_files/competitive benchmarks/agent-baton-user-stories.md`

**Previous syntheses:**
- `docs/competitive-audit/SYNTHESIS.md` (v1, solo-only)
- `docs/competitive-audit/SYNTHESIS-v2.md` (v2, includes team findings)
