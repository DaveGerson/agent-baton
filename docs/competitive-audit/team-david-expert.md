# Team Audit: David (Compliance/Security) + Agent-Baton Expert

> Audit date: 2026-04-16
> Method: Adversarial dialogue -- compliance stakeholder vs. codebase expert
> Focus: Can an external auditor certify agent-baton's governance controls?

---

## Rating Key

| Rating | Meaning |
|--------|---------|
| **WORKS** | Control exists and would satisfy an external auditor |
| **PARTIAL** | Control exists but has gaps that would require remediation |
| **BLOCKED** | Control does not exist or would fail an audit |

---

## Item 1: Audit Trail Immutability

**David asks:** "Can historical records be modified? Show me EVERY write
path to step_results, gate_results, approval_results, and events. If I find
a single UPDATE or DELETE on an audit-critical table, this is a fail."

### Expert Investigation

**Write path analysis of `sqlite_backend.py`:**

There are 59 DELETE/UPDATE/INSERT OR REPLACE occurrences. Specific findings
on audit-critical tables:

1. **step_results** -- `save_execution()` line 156: `DELETE FROM step_results WHERE task_id = ?` followed by re-INSERT. Every save wipes and rewrites all step results for a task. `save_step_result()` line 531: uses `INSERT OR REPLACE` which is DELETE+INSERT in SQLite.

2. **gate_results** -- `save_execution()` line 207: `DELETE FROM gate_results WHERE task_id = ?` followed by re-INSERT. However, `save_gate_result()` line 593: uses a plain `INSERT` (append-only). The incremental writer is safe, but the full-state writer destroys history.

3. **approval_results** -- `save_execution()` line 228: `DELETE FROM approval_results WHERE task_id = ?` followed by re-INSERT. Same as gate_results -- the incremental `save_approval_result()` line 619 uses plain INSERT, but `save_execution()` wipes and rebuilds.

4. **events** -- `append_event()` line 680: uses `INSERT OR REPLACE` keyed on event_id. This means an event with the same ID can be overwritten with different content. `delete_events()` line 733: provides a public method to delete all events for a task.

5. **trace_events** -- `save_trace()` line 1230: `DELETE FROM trace_events WHERE task_id = ?` followed by re-INSERT.

6. **mission_log_entries** -- `append_mission_log()` line 1454: uses plain INSERT (append-only). This is the one truly append-only audit table.

7. **delete_execution()** line 438: `DELETE FROM executions WHERE task_id = ?` with ON DELETE CASCADE -- this destroys ALL child rows across ALL tables for a task.

### David Probes

"So let me get this straight. Every time `save_execution()` is called --
which happens after EVERY state transition -- it deletes and rewrites all
gate results and approval results? That means if I query the database between
saves, I might see zero gate results for a task that has actually passed
three gates. And worse, if the in-memory model is corrupted or truncated,
the next save silently destroys the audit history. What about the compliance
JSONL log?"

### Expert Second Pass

The executor writes a parallel `compliance-audit.jsonl` file
(`executor.py` line 340, via `_write_compliance_entry()` line 518).
This is genuinely append-only -- it opens the file in `"a"` mode and
appends one JSON line per event. It logs dispatches, gate results, and
policy violations.

However:
- It is best-effort: failures are `_log.warning`-ed and swallowed (line 534).
- It is a plain text file on disk, not checksummed or signed.
- It can be deleted or edited by anyone with filesystem access.
- It is NOT synced to central.db (no schema table for compliance logs).
- It is NOT included in `baton cleanup` retention management.

**NEW finding vs. solo audit:** The solo audit identified the DELETE-then-INSERT
pattern but did not discover:
1. The dual nature of gate_results/approval_results writes (incremental
   is safe, bulk save is destructive).
2. The compliance-audit.jsonl as a partial mitigation (genuinely
   append-only but unsigned and local-only).
3. The `delete_events()` public method exposing a complete event wipe.
4. Events use `INSERT OR REPLACE` meaning any event can be silently
   overwritten if the caller reuses an event_id.

### Joint Verdict: BLOCKED

The compliance JSONL is a helpful secondary trail but does not meet audit
requirements because: (a) it is unsigned plain text, (b) it is not the
primary data source, (c) it can be deleted, (d) it is not synced
cross-project. The primary SQLite store remains fully mutable.

**Delta from solo audit:** Solo audit rated this BLOCKED and identified the
pattern correctly. Team audit adds: (1) dual-nature discovery of incremental
vs. bulk writes, (2) compliance JSONL as partial but insufficient mitigation,
(3) events table silently supports overwrite via INSERT OR REPLACE, (4) the
`delete_events()` and `delete_execution()` public APIs as additional
destruction vectors.

---

## Item 2: Authorization Chain

**David asks:** "For a HIGH-risk task that shipped to production, can I prove:
who requested it, who planned it, who approved it, who reviewed it? Show me
every identity field across all relevant tables."

### Expert Investigation

**Identity fields across all tables:**

| Table | Identity field | What it captures |
|-------|---------------|-----------------|
| `executions` | (none) | No requester identity |
| `plans` | (none) | No planner identity |
| `step_results` | `agent_name` | Which AI agent executed, not which human |
| `gate_results` | (none) | No gater identity |
| `approval_results` | (none) | No approver identity |
| `events` | payload (JSON) | Some events include `resolved_by` |
| `mission_log_entries` | `agent_name` | AI agent only |
| `amendments` | (none) | No amender identity |
| `traces` | (none) | No human identity |
| `retrospectives` | (none) | No author identity |
| `telemetry` | `agent_name` | AI agent only |
| `beads` | `agent_name` | AI agent only |
| `compliance-audit.jsonl` | (none) | No operator identity |

**DecisionResolution** (file-based, NOT in SQLite):
- `resolved_by` field: defaults to `"human"` -- a literal string, not an
  actual human identity. Other values: `"timeout_default"`, `"auto_policy"`.
  At `decisions.py` line 85, the `resolved_by` parameter defaults to
  `"human"` and is never set to an actual person's name by the CLI.

**Events system:**
- `human_decision_resolved` event includes `resolved_by` in its payload
  (events.py line 412), but the value comes from `DecisionManager.resolve()`
  which defaults to `"human"`.

### David Probes

"So there is literally zero human identity anywhere in the system. I cannot
prove who requested a task, who approved a deployment, or who overrode an
auditor veto. The `resolved_by` field says 'human' -- that is not an
identity, that is a category. For SOC 2 CC6.1 or any regulatory framework,
I need named individuals. What about git commits?"

### Expert Second Pass

Git commits may carry author identity (`git log --format='%an %ae'`), but:
- `step_results.commit_hash` stores the SHA but not the author.
- The commit author will be the Claude Code process user, not the human
  who initiated the task.
- There is no linkage from `commit_hash` back to a human approval.

The `InteractionTurn` model has a `source` field (line 104 of execution.py)
with values `"human"`, `"auto-agent"`, `"webhook"` -- but again, no actual
identity.

### Joint Verdict: BLOCKED

Zero human identity persisted anywhere in the audit trail. The
`DecisionResolution.resolved_by` field is the closest thing but only stores
the literal string `"human"`. An external auditor would reject this
immediately under any compliance framework.

**Delta from solo audit:** Solo audit identified missing `approved_by` on
`approval_results`. Team audit expands scope: NO table in the entire
schema carries human identity. The `resolved_by` field in the Decision
model is categorized as a type-hint ("human") not an actual identity.
The `InteractionTurn.source` field is similarly categorical. This is a
systemic gap, not an isolated omission.

---

## Item 3: Auditor Independence

**David asks:** "The auditor agent claims independence from the orchestrator.
But both run in the same Claude Code context. Can the auditor be influenced
or bypassed? What are the actual enforcement mechanisms?"

### Expert Investigation

**Auditor definition** (`agents/auditor.md`):
- Declared as a subagent with `model: opus` and `permissionMode: default`.
- The system prompt states it has "veto authority" and exists "in a separate
  context specifically so you can disagree."
- Tools: Read, Glob, Grep, Bash -- full read access to the codebase.

**Enforcement mechanisms:**
1. **Policy engine** (`policy.py`): The `regulated` and `infrastructure`
   presets include `require_agent: auditor` rules with `severity: "block"`.
   However, `evaluate()` line 567 only surfaces these as `PolicyViolation`
   warnings -- it does not actually check whether the auditor agent is in
   the plan.

2. **Planner integration**: The planner generates the plan. If the planner
   does not include an auditor step, the policy engine warns but does not
   block. The warning is a `PolicyViolation` object returned to the caller,
   but enforcement depends on the caller checking violations and refusing to
   proceed.

3. **Bypass paths:**
   - `baton execute start` does not validate the plan against the policy
     engine before starting. A plan without an auditor step can execute.
   - There is no machine-enforced gate that blocks execution unless an
     auditor SHIP/APPROVE verdict is recorded.
   - The auditor's verdict is a free-text convention in the agent's prompt,
     not a structured field validated by the engine. The `ComplianceReport.
     auditor_verdict` field is set by the compliance report generator, not
     enforced by the execution engine.

4. **Context isolation**: The auditor runs as a Claude Code subagent. While
   it has its own system prompt, it receives the delegation prompt from the
   orchestrator, which could influence its assessment. The orchestrator
   constructs the prompt via `PromptDispatcher`, which includes the plan
   context and previous step results.

### David Probes

"So the 'veto authority' is a prompt instruction, not a machine-enforced
control. The engine will happily complete an execution without ever checking
that the auditor approved it. And the orchestrator constructs the auditor's
input prompt -- that is the fox guarding the henhouse. Can I at least detect
after the fact whether the auditor was involved?"

### Expert Second Pass

You CAN detect auditor involvement post-hoc:
- `step_results` will show `agent_name = 'auditor'` if dispatched.
- `mission_log_entries` will record the auditor's assignment and result.
- The compliance report may have `auditor_verdict` set.

But you CANNOT prove:
- That the auditor was REQUIRED to be present (no structured assertion).
- That execution was BLOCKED when the auditor said "BLOCK" (the engine
  does not parse auditor output for block signals).
- That the auditor saw the COMPLETE plan (it sees what the delegation
  prompt includes).

### Joint Verdict: PARTIAL

The auditor exists as a distinct subagent with full read access, which is
genuine architectural independence. But enforcement is entirely
convention-based: the engine does not validate auditor presence, does not
parse auditor verdicts, and does not block execution on auditor rejection.
An auditor can be omitted, and a BLOCK verdict can be ignored.

**Delta from solo audit:** Solo audit said WORKS for auditor independence.
Team audit downgrades to PARTIAL: the auditor agent definition is sound,
but enforcement is prompt-convention only. The policy engine warns but does
not block. The engine does not validate auditor verdicts structurally.

---

## Item 4: Risk Classification Tampering

**David asks:** "Can a developer override risk classification from HIGH to
LOW without justification? Trace the override path."

### Expert Investigation

**Classification flow:**
1. `DataClassifier.classify()` in `classifier.py` produces a
   `ClassificationResult` with `risk_level`, `signals_found`, `confidence`.
2. The planner receives this and sets `MachinePlan.risk_level`.
3. The plan is persisted to SQLite via `_upsert_plan()` using
   `INSERT OR REPLACE INTO plans` (line 1619 of sqlite_backend.py).

**Override paths:**
1. **Learned overrides** (`overrides.py`): `classifier_adjustments` dict
   can modify classification thresholds. Written via `LearnedOverrides.save()`
   with no per-change justification, no change history, no audit trail. The
   file is a plain JSON that anyone can edit.

2. **Plan amendment** (`executor.py`): `amend_plan()` can modify the plan
   mid-execution, but risk_level is set at plan creation, not during
   amendment.

3. **Direct database modification**: Since `plans.risk_level` is a plain
   TEXT column with no constraints, anyone with SQLite access can
   `UPDATE plans SET risk_level = 'LOW' WHERE task_id = ?`.

4. **Plan regeneration**: A developer can simply run `baton plan` again
   with a reworded task description that avoids triggering HIGH-risk
   keywords. Since classification is keyword-based (hardcoded lists in
   `classifier.py` lines 95-129), gaming the classifier is trivial.

**Evidence storage:**
- `ClassificationResult.signals_found` is included in the plan's
  `shared_context` but not in a dedicated column.
- No `classification_history` table or override audit log exists.
- The compliance JSONL does not log classification events.

### David Probes

"So a developer can: (a) edit learned-overrides.json to suppress
classification signals, (b) rephrase the task description to avoid keyword
matches, or (c) directly update the database. None of these leave an audit
trail. Can I at least see what the original classification was?"

### Expert Second Pass

The `MachinePlan` model has `classification_source` (line 485 of
execution.py) which records `"haiku"` or `"keyword-fallback"` -- but this
is how the classification was made, not what the original result was. The
`ClassificationResult` object is not persisted independently; it is consumed
by the planner and discarded.

**NEW finding:** `MachinePlan.detected_stack` and `MachinePlan.foresight_insights`
are persisted but `ClassificationResult` (the actual risk assessment with
signals) is not. There is no way to reconstruct what the classifier saw at
plan time.

### Joint Verdict: BLOCKED

Risk classification can be tampered with through at least three vectors,
none of which leave an audit trail. The classification result itself is
not persisted, so even legitimate reclassification cannot be audited.

**Delta from solo audit:** Solo audit rated this PARTIAL (noting no
per-change justification). Team audit upgrades severity to BLOCKED because:
(1) the ClassificationResult is not persisted at all, (2) multiple
untracked override vectors exist, (3) keyword-based classification is
trivially gameable.

---

## Item 5: Secrets in Audit Trail

**David asks:** "If an agent encounters a hardcoded API key during code
review and reports it, does that key get stored in beads/traces/step_results?
Is there any redaction layer?"

### Expert Investigation

**Redaction in the codebase:**

1. **Anthropic API key redaction** (`claude_launcher.py` line 55-66):
   `_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")` -- only matches
   Anthropic's own `sk-ant-*` key format. Applied to stderr output only
   (`_redact_stderr`).

2. **Headless runtime** (`headless.py` line 279): Same `_API_KEY_RE`
   pattern applied to stderr.

3. **No general secret redaction**: There is no redaction layer for:
   - AWS keys (`AKIA*`)
   - GitHub tokens (`ghp_*`, `github_pat_*`)
   - Generic API keys in code (`api_key = "..."`)
   - Database credentials
   - JWT tokens
   - PII data

**Storage paths where secrets could persist:**
- `step_results.outcome` -- free-text summary of agent work (TEXT column).
- `step_results.error` -- error messages that might contain env vars.
- `events.payload` -- JSON blob with event details.
- `trace_events.details` -- JSON blob with trace details.
- `beads.content` -- structured agent memory (TEXT column).
- `telemetry.details` -- free-text telemetry (TEXT column).
- `mission_log_entries.result` -- agent outcome summary.
- `compliance-audit.jsonl` -- best-effort compliance log.

**Policy engine consideration**: The `data_analysis` preset includes
`require_pii_masking` as a `require_gate` rule (policy.py line 235), but
this is a warning to include a gate -- it does not actually perform masking.
There is no masking implementation anywhere in the codebase.

### David Probes

"So the only redaction is for Anthropic's own API keys in stderr output.
If an agent finds a customer's AWS key in a config file and reports it in
its outcome summary, that key is now persisted in at least 7 different
storage locations -- step_results, events, traces, beads, telemetry, mission
log, and the compliance log. And it syncs to central.db, so it is now in
two databases. Is there any mechanism to scrub it after the fact?"

### Expert Second Pass

No structured scrub mechanism exists. The only way to remove a secret
after it enters the audit trail is:
1. Direct SQL UPDATE on the affected tables (which itself is an audit issue).
2. `delete_execution()` which CASCADE-deletes everything for a task.
3. `baton cleanup --retention-days 0` which removes file-based artifacts but
   NOT SQLite data.

The sync engine copies all data to central.db, so scrubbing the project
database is insufficient -- central.db must also be cleaned.

### Joint Verdict: BLOCKED

There is effectively zero secret redaction for content that flows through
agent outcomes, events, traces, or beads. The only redaction is for
Anthropic API keys in stderr -- a narrow, self-interested protection that
does not extend to customer secrets.

**Delta from solo audit:** Solo audit noted the concern but rated it as a
dealbreaker detail under "Secrets/PII in traces." Team audit provides: (1)
exact enumeration of 8+ storage locations where secrets persist, (2)
confirmation that the `require_pii_masking` gate is a stub (advisory only,
no implementation), (3) discovery that sync to central.db doubles the
exposure surface, (4) no structured post-hoc scrub mechanism.

---

## Item 6: Gate Bypass

**David asks:** "Can a gate failure be overridden without a recorded
justification? What stops someone from recording a fake 'passed' gate
result?"

### Expert Investigation

**Gate recording in the CLI** (`execute.py` lines 578-589):
```python
elif args.subcommand == "gate":
    passed = args.result == "pass"
    engine.record_gate_result(
        phase_id=args.phase_id,
        passed=passed,
        output=args.gate_output,
    )
```

Anyone with CLI access can run:
```bash
baton execute gate --phase-id 1 --result pass --gate-output "all good"
```

This records a passing gate result regardless of whether the gate command
was actually run. There is no validation that:
- The gate command was executed.
- The output matches the command's actual stdout.
- The caller is authorized to record gate results.

**Headless execution** (`execute.py` lines 1061-1090): The `baton execute run`
command runs gate commands via `subprocess` and records the actual result.
But in the standard orchestrator loop, the human/Claude session runs the
gate and self-reports the result.

**Engine validation** (`executor.py` `record_gate_result()` line 1328):
The engine accepts any boolean `passed` value and any string `output`.
No cross-validation against the gate command's actual execution. No hash
or signature of the gate output.

**Dry-run bypass** (`execute.py` line 1070):
```python
if dry_run:
    engine.record_gate_result(phase_id=phase_id, passed=True, output="dry-run skip")
```
Dry-run mode auto-passes all gates with "dry-run skip" as output.

### David Probes

"So the gate system is entirely trust-based. The orchestrator runs a
command, looks at the output, and self-reports pass/fail. There is no
independent verification. The gate_results table does not even store the
command that was supposedly run -- only the output. Can I correlate the
gate result with the actual command execution?"

### Expert Second Pass

The `gate_results` table stores: `task_id`, `phase_id`, `gate_type`,
`passed`, `output`, `checked_at`. It does NOT store:
- The actual command that was executed.
- The command's exit code.
- The stderr output.
- The execution environment (who ran it, from where).
- A hash of the command output for integrity verification.

The `plan_phases` table stores `gate_command` (the intended command), but
there is no linkage proving that particular command was what produced the
gate result.

**NEW finding:** The `_policy_approved_steps` set (executor.py line 272)
allows the engine to skip policy violation checks on re-dispatch. This is a
session-level in-memory set that is not persisted or auditable. A policy
violation approval is granted once and never recorded.

### Joint Verdict: BLOCKED

Gates are entirely self-reported with no independent verification,
no command traceability, no output integrity checking, and no authorization
for who can record results. The `_policy_approved_steps` bypass is also
unauditable.

**Delta from solo audit:** Solo audit did not specifically address gate
bypass. Team audit provides: (1) the complete self-reporting trust gap, (2)
missing command traceability in gate_results, (3) dry-run auto-pass, (4)
`_policy_approved_steps` as an unauditable bypass, (5) lack of exit code
and environment recording.

---

## Item 7: Compliance Report Completeness

**David asks:** "`baton compliance` generates reports. What do they actually
contain? Would an external SOC 2 auditor accept this as evidence of
operating effectiveness?"

### Expert Investigation

**ComplianceReport structure** (`compliance.py` lines 63-96):
- `task_id`, `task_description`, `risk_level`, `classification`
- `entries`: list of `ComplianceEntry` objects with agent_name, action,
  files, business_rules_validated, commit_hash, gate_result, notes
- `auditor_verdict`, `auditor_notes`
- `total_gates_passed`, `total_gates_failed`
- `timestamp`

**What it contains (rendered markdown):**
1. Task metadata (ID, description, risk, classification, date).
2. Change log table: agent, action, files (truncated to 3), gate result,
   commit hash (first 7 chars).
3. Business rules validated (from entries).
4. Gate summary (pass/fail counts).
5. Agent notes.

**What a SOC 2 auditor would require that is MISSING:**
1. **Control owner identity** -- who approved, who reviewed, who executed.
2. **Control objective mapping** -- which SOC 2 criteria does each control address.
3. **Population and sample** -- how many tasks were executed, how many were
   reviewed; sampling methodology.
4. **Exception tracking** -- deviations from policy, compensating controls.
5. **Evidence of periodic review** -- proof that controls were tested over
   the audit period (not just at a point in time).
6. **Period coverage** -- reports are per-task, not per-period. No
   aggregation across a reporting period.
7. **Independent testing** -- reports are self-generated by the system, not
   independently verified.

**Storage:** Reports are markdown files on disk (line 244 of compliance.py).
Not in SQLite, not synced to central.db, not queryable, not tamper-evident.

### David Probes

"These reports are per-task snapshots with no identity, no control mapping,
and no period aggregation. For SOC 2 Type II, I need evidence that controls
operated effectively over a minimum 6-month period. A per-task markdown file
is a data point, not evidence of operating effectiveness."

### Joint Verdict: PARTIAL

The compliance report structure captures useful per-task data (change log,
gate results, auditor verdict) but falls far short of SOC 2 evidence
requirements. It would need: identity fields, control-to-criteria mapping,
period aggregation, exception tracking, and independent verification to
serve as audit evidence.

**Delta from solo audit:** Solo audit noted WORKS for `baton compliance`
existing but flagged markdown-on-disk as a gap. Team audit reframes: the
report content itself is insufficient for SOC 2 regardless of storage
format. Specific missing elements identified.

---

## Item 8: Change Management Integration

**David asks:** "Regulated companies require formal change management (CAB
approval, change tickets). Can agent-baton integrate with existing ITSM
processes, or does it create a parallel uncontrolled channel?"

### Expert Investigation

**External adapters** (`core/storage/adapters/`):
- `ado.py` -- Azure DevOps work item integration
- `github.py` -- GitHub issues integration
- `jira.py` -- Jira integration
- `linear.py` -- Linear integration

These adapters fetch work items INTO baton (one-way read), and external
mappings link baton tasks to external work items via `external_mappings`
table. The `v_external_plan_mapping` view provides cross-referencing.

**What exists:**
- `baton source add/list/sync/remove/map` CLI commands for adapter management.
- External items are fetched and stored in `external_items` table.
- Mappings between external IDs and baton task IDs are tracked.

**What is MISSING for formal change management:**
1. **Write-back** -- Baton can read from ITSM systems but cannot write
   back (no status updates, no comment posting, no ticket closure).
2. **Change ticket creation** -- No mechanism to auto-create a change
   ticket before execution starts.
3. **CAB approval gate** -- No gate type that blocks execution until an
   external change ticket is approved.
4. **ITSM status sync** -- Baton's execution status is not reflected back
   to the source system.
5. **Change window enforcement** -- No mechanism to restrict execution to
   approved change windows.

### David Probes

"So baton can see my change tickets but cannot update them. The actual
execution happens in a parallel channel. A CAB board reviewing change
tickets would not know that agent-baton is executing changes unless someone
manually updates the ticket. This creates exactly the kind of shadow IT
that compliance programs exist to prevent."

### Joint Verdict: PARTIAL

External adapters provide a read-only bridge to ITSM systems, which is a
strong foundation. But the lack of write-back, automatic ticket creation,
and CAB approval gates means agent-baton operates as a parallel execution
channel that ITSM systems cannot control or track.

**Delta from solo audit:** Solo audit did not examine ITSM integration at
all. This is entirely new finding.

---

## Item 9: Data Residency and Retention

**David asks:** "Where does execution data live? Can it be exported? Is
there a retention policy? Can old data be purged without breaking the audit
trail?"

### Expert Investigation

**Data locations:**
1. **Project-level**: `.claude/team-context/baton.db` -- per-project SQLite.
2. **Central**: `~/.baton/central.db` -- cross-project read replica.
3. **File artifacts**: `.claude/team-context/` subdirectories for compliance
   reports, decisions, events (JSONL), retrospectives, traces.
4. **Compliance log**: `.claude/team-context/compliance-audit.jsonl`.

**Retention mechanism:**
- `baton cleanup --retention-days N` (`cleanup.py`): Operates on file-based
  artifacts (traces, events, retrospectives, context profiles) via
  `DataArchiver`. Default retention: 90 days.
- `DataArchiver.cleanup()` deletes files older than the cutoff. It does NOT
  touch SQLite data.
- No retention mechanism for SQLite data whatsoever.
- No retention mechanism for central.db data.

**Export:**
- `baton query <subcommand> --format csv` exports query results.
- `baton query --sql "..." --format csv` for ad-hoc exports.
- PMO UI `DataExport` component for CSV/JSON/Markdown.
- No bulk export/archive command for compliance purposes.

**Purge without breaking audit trail:**
- `delete_execution()` cascade-deletes everything for a task -- destructive
  and complete.
- `baton cleanup` only handles file artifacts, not database records.
- No selective purge (e.g., "remove PII but keep the audit skeleton").
- No tombstone mechanism (e.g., "record X was purged on date Y per policy Z").

### David Probes

"So I have data in two SQLite databases and multiple file locations, with
no unified retention policy, no selective purge, and no tombstoning. If GDPR
requires me to delete a user's data, I cannot do it cleanly -- I would need
to manually identify all references across 24+ tables and two databases, and
the cascade delete would destroy the audit trail. This is a data governance
nightmare."

### Expert Second Pass

**NEW finding:** `interaction_history` (multi-turn human-agent conversations)
exists in the `StepResult` model (execution.py line 799) but is NOT
persisted to the SQLite schema. There is no `interaction_history` column in
`step_results` and no search results for it in `sqlite_backend.py`. This
means multi-turn interactions are lost on save/load cycle.

Similarly, `feedback_results` exists in `ExecutionState` (line 1054) but
has no corresponding table or persistence in `sqlite_backend.py`.

### Joint Verdict: PARTIAL

Data residency is well-defined (two known locations). Export capabilities
exist via CLI. But retention is incomplete (file-only, no SQLite), purge
is destructive (no selective delete or tombstoning), and GDPR-style data
subject requests cannot be fulfilled cleanly.

**Delta from solo audit:** Solo audit did not address data residency. Team
audit provides: (1) complete data location map, (2) retention gap between
file and SQLite artifacts, (3) missing tombstone mechanism, (4) NEW:
`interaction_history` and `feedback_results` are not persisted to SQLite
at all -- data loss on save/load.

---

## Item 10: Separation of Duties

**David asks:** "Can the same person who writes the agent definition also
approve the agent's output? Is there any enforced separation?"

### Expert Investigation

**Agent definition authorship:**
- Agent definitions are markdown files in `agents/` directory.
- Any developer can modify any agent definition via git commit.
- No code review requirement is enforced by the system (depends on
  external git workflow).

**Approval workflow:**
- `baton execute approve` records an approval with `result`, `feedback`,
  and `decided_at`. No `approved_by` field.
- `baton decide --resolve` records a decision with `chosen_option`,
  `rationale`, `resolved_by` (defaults to "human").
- In both cases, there is no enforcement of WHO can approve.

**Enforced separation:**
- The auditor agent is architecturally separate (different system prompt,
  different context), but is spawned by the orchestrator.
- There is no role-based access control (RBAC) system.
- There is no mechanism to say "the person who wrote the agent definition
  for backend-engineer cannot approve backend-engineer's output."
- There is no mechanism to prevent self-approval of one's own task.

**Policy engine check:**
- `require_agent: auditor` ensures the auditor is INCLUDED in the plan
  (advisory only), but does not enforce that a different person reviews.

### David Probes

"In a regulated environment, separation of duties is not optional. The
person who configures the controls cannot be the same person who operates
them or reviews their output. With no RBAC, no named approvers, and no
identity tracking, there is literally no way to prove separation of duties
existed. Even if you added identity tracking, there is no enforcement
mechanism."

### Joint Verdict: BLOCKED

Zero enforced separation of duties. The system has no concept of human
identity, no RBAC, no enforcement of who can approve or review. The auditor
agent provides functional separation (different AI context), but this is
AI-to-AI separation, not human-to-human separation.

**Delta from solo audit:** Solo audit identified missing `approved_by` as
an isolated gap. Team audit frames this as a systemic absence of separation
of duties -- not just a missing field, but a missing concept.

---

## Item 11: Incident Response Timeline

**David asks:** "A security incident is traced to agent code committed at
3 AM. Walk me through the forensic investigation: timestamp accuracy,
decision reconstruction, evidence chain."

### Expert Investigation

**Available forensic data:**
1. **Traces** (`traces` + `trace_events`): Full lifecycle with timestamps,
   event types, agent names, phase/step numbers, duration. Query via
   `baton trace TASK_ID`.
2. **Step results** (`step_results`): Agent name, outcome summary,
   files changed, commit hash, completion timestamp.
3. **Events** (`events`): Domain events with topic, sequence, timestamp,
   JSON payload.
4. **Mission log** (`mission_log_entries`): Per-agent log with assignment,
   result, files, decisions, issues, handoff notes, commit hash.
5. **Compliance JSONL** (`compliance-audit.jsonl`): Append-only log of
   dispatches, gates, and policy violations.
6. **Git history**: Commit hashes in step_results link to actual code changes.
7. **Beads** (`beads`): Agent memory -- discoveries, decisions, warnings.

**Timestamp accuracy:**
- All timestamps use `datetime.now(tz=timezone.utc).isoformat()` --
  UTC-based, ISO 8601 format, seconds precision.
- SQLite `created_at` columns use `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')`
  -- also UTC.
- No NTP verification or clock drift detection.

**Reconstruction path:**
1. Identify the commit hash from git log.
2. Search `step_results` for matching `commit_hash` to get `task_id` and
   `step_id`.
3. Load full execution state via `baton trace TASK_ID`.
4. Walk trace events chronologically to reconstruct the decision chain.
5. Check `events` for any approval or gate events for that task.
6. Read `mission_log_entries` for the specific agent's work log.
7. Cross-reference with `compliance-audit.jsonl` for policy context.

**Gaps in the evidence chain:**
1. No human identity at any point -- cannot determine who initiated or
   approved the task.
2. No link from the commit to the specific approval that authorized it.
3. Trace events can be overwritten (DELETE + INSERT pattern in save_trace).
4. Events can be overwritten (INSERT OR REPLACE by event_id).
5. The compliance JSONL is best-effort and may have gaps.

### David Probes

"The reconstruction path is sound from a technical standpoint -- the data
is there to tell me WHAT happened and WHEN. But I cannot tell WHO authorized
it, and I cannot prove the evidence has not been tampered with. If this is
a legal matter, opposing counsel would challenge the integrity of every
record."

### Joint Verdict: PARTIAL

Technical reconstruction capability is strong: timestamps are UTC-based,
traces provide full lifecycle, commit hashes link to code, mission logs
provide per-agent detail. But the evidence chain is weakened by: (1) no
human identity, (2) mutable storage, (3) best-effort compliance log.

**Delta from solo audit:** Solo audit rated trace reconstruction as WORKS.
Team audit provides the forensic walkthrough showing that while WHAT/WHEN
is reconstructable, WHO/WHY (authorization) is not, and evidence integrity
is not provable.

---

## Item 12: Control Effectiveness Evidence

**David asks:** "For each governance control (risk classification, gate
enforcement, auditor review, approval workflow), can I produce evidence
that the control operated as designed over the last 90 days?"

### Expert Investigation

**Risk Classification:**
- No `classification_results` table. ClassificationResult is transient.
- Cannot query "how many tasks were classified as HIGH in the last 90 days"
  except indirectly via `plans.risk_level`.
- Cannot prove the classifier ran (vs. a manually-set risk level).
- Cannot show classification accuracy or false negative rate.

**Gate Enforcement:**
- `gate_results` table has `task_id`, `phase_id`, `gate_type`, `passed`,
  `output`, `checked_at`.
- CAN query: "how many gates passed/failed in 90 days" via
  `SELECT passed, COUNT(*) FROM gate_results WHERE checked_at > ? GROUP BY passed`.
- CANNOT prove the gate command was actually run (self-reported).
- CANNOT prove a failed gate blocked execution (gate failure sets status
  to 'failed' but there is no proof the execution actually stopped).

**Auditor Review:**
- CAN detect if auditor was dispatched: `SELECT * FROM step_results WHERE
  agent_name = 'auditor'`.
- CANNOT prove auditor review was required but omitted (no expected-vs-
  actual comparison).
- CANNOT prove auditor verdict was honored (no structured verdict
  enforcement).

**Approval Workflow:**
- `approval_results` records phase approvals.
- CAN query approval history.
- CANNOT identify who approved (no identity).
- CANNOT prove approval was required but bypassed (no expected-vs-actual).

**Period reporting:**
- No built-in period aggregation for any control.
- `baton query --sql "..."` allows ad-hoc queries against baton.db.
- Cross-project via `baton cquery --sql "..."` against central.db.
- No pre-built control effectiveness dashboard or report.

### David Probes

"For a SOC 2 Type II audit, I need to demonstrate that each control
operated as designed throughout the entire reporting period -- not just that
data exists. You can tell me gates existed but cannot prove they were
enforced. You can tell me the auditor was dispatched but cannot prove its
verdict was honored. This is the difference between 'we have a control' and
'our control is effective.'"

### Joint Verdict: PARTIAL

Raw data exists in SQLite to query gate results, step results, and
approvals over time. But proving control EFFECTIVENESS requires: (1) control
design documentation mapping to criteria, (2) expected-vs-actual comparison,
(3) exception tracking, (4) independent testing. None of these exist.

**Delta from solo audit:** Solo audit did not address control effectiveness
evidence. This is an entirely new assessment showing the gap between "data
exists" and "control effectiveness is provable."

---

## Summary Matrix

| # | Item | Rating | Key Finding | Solo Audit Rating |
|---|------|--------|-------------|-------------------|
| 1 | Audit trail immutability | **BLOCKED** | DELETE-then-INSERT on every save; 8+ mutable paths; compliance JSONL insufficient | BLOCKED (same, but fewer details) |
| 2 | Authorization chain | **BLOCKED** | Zero human identity in ANY table; resolved_by is categorical, not identity | BLOCKED (only approval_results) |
| 3 | Auditor independence | **PARTIAL** | Architectural separation exists but enforcement is prompt-convention only | WORKS (overrated) |
| 4 | Risk classification tampering | **BLOCKED** | ClassificationResult not persisted; 3+ untracked override vectors | PARTIAL (underrated) |
| 5 | Secrets in audit trail | **BLOCKED** | Only Anthropic key redaction; 8+ storage locations for leaked secrets; syncs to central.db | Mentioned as concern |
| 6 | Gate bypass | **BLOCKED** | Self-reported; no command traceability; dry-run auto-pass; _policy_approved_steps unauditable | Not specifically addressed |
| 7 | Compliance report completeness | **PARTIAL** | Per-task data exists but lacks identity, control mapping, period aggregation for SOC 2 | WORKS (overrated) |
| 8 | Change management integration | **PARTIAL** | Read-only ITSM bridge; no write-back, no CAB gates, no ticket auto-creation | Not addressed |
| 9 | Data residency and retention | **PARTIAL** | Well-defined locations; file-only retention; no selective purge; interaction_history not persisted | Not addressed |
| 10 | Separation of duties | **BLOCKED** | No RBAC, no identity, no enforcement; auditor is AI-to-AI separation only | Identified as gap |
| 11 | Incident response timeline | **PARTIAL** | Strong WHAT/WHEN reconstruction; weak WHO/WHY; mutable evidence | WORKS (overrated) |
| 12 | Control effectiveness evidence | **PARTIAL** | Raw data queryable but no control design docs, no expected-vs-actual, no period reporting | Not addressed |

### Aggregate Score

- **BLOCKED**: 6 items (1, 2, 4, 5, 6, 10)
- **PARTIAL**: 5 items (3, 7, 8, 9, 11, 12)  -- note: 6 items actually
- **WORKS**: 0 items

---

## NEW Findings vs. Solo Audit

| # | Finding | Impact |
|---|---------|--------|
| 1 | Dual-nature gate/approval writes: incremental is safe, bulk save is destructive | Audit trail can be silently corrupted by any `save_execution()` call |
| 2 | `compliance-audit.jsonl` exists as partial mitigation but is unsigned, local-only, best-effort | Provides some forensic value but cannot serve as primary evidence |
| 3 | Events use INSERT OR REPLACE, enabling silent overwrites | Any event can be replaced if the caller reuses an event_id |
| 4 | `delete_events()` public API enables complete event deletion | Events are not append-only despite the EventPersistence class name |
| 5 | Zero human identity across ALL tables (not just approval_results) | Systemic gap, not an isolated missing field |
| 6 | `DecisionResolution.resolved_by` defaults to literal "human" string, never actual identity | The closest thing to identity is a categorical placeholder |
| 7 | `ClassificationResult` is transient -- not persisted | Cannot reconstruct what the classifier saw at plan time |
| 8 | `_policy_approved_steps` is session-level in-memory, not auditable | Policy violation approvals are invisible to forensic review |
| 9 | Gate results do not store the command that was executed | Cannot prove correlation between gate command and gate result |
| 10 | `interaction_history` not persisted to SQLite | Multi-turn human-agent conversations lost on save/load |
| 11 | `feedback_results` not persisted to SQLite | Feedback gate decisions lost on save/load |
| 12 | Read-only ITSM integration; no write-back or CAB gates | Agent execution is a parallel channel invisible to ITSM |
| 13 | No selective purge or tombstoning for GDPR compliance | Cannot cleanly fulfill data subject deletion requests |
| 14 | Only Anthropic API key pattern redacted; all other secrets persist in 8+ locations | Secret exposure surface is orders of magnitude larger than redaction coverage |
| 15 | Auditor enforcement is prompt-convention only, not machine-enforced | Solo audit overrated this as WORKS |

---

## Remediation Priority (David's Requirements)

### P0 -- Must fix before any regulated deployment

1. **Append-only audit log table** with SQLite triggers to prevent UPDATE/DELETE.
   Duplicate every write to gate_results, approval_results, and step_results
   into an immutable audit_log table.
2. **Human identity tracking** -- add `operator_id` to every write path.
   At minimum: approval_results, gate_results, decision_resolutions. Source
   from git config user.email or an explicit `--operator` flag.
3. **Secret redaction layer** -- pattern-based scrubbing before ANY
   persistence (step outcomes, events, beads, traces, telemetry, mission
   log). Cover at minimum: AWS keys, GitHub tokens, generic API keys,
   JWT tokens, database connection strings.
4. **Persist ClassificationResult** -- store signals_found, confidence, and
   explanation alongside the plan. Make it immutable after plan creation.
5. **Persist interaction_history and feedback_results** -- these are live
   data that is silently lost on save/load cycle.

### P1 -- Required for SOC 2 readiness

6. **Gate command traceability** -- store the command, exit code, stderr,
   and environment hash in gate_results.
7. **Auditor verdict enforcement** -- engine must parse auditor output for
   BLOCK/REVISE verdicts and refuse to advance.
8. **RBAC framework** -- at minimum, named approvers per risk tier with
   enforcement.
9. **Control effectiveness reporting** -- period-based aggregation of all
   governance controls with expected-vs-actual comparison.
10. **ITSM write-back** -- update external work items with execution status.

### P2 -- Required for production maturity

11. **Data retention for SQLite** -- configurable per-table retention with
    tombstoning support.
12. **Compliance report enrichment** -- add SOC 2 criteria mapping,
    population/sample metrics, exception tracking.
13. **Central.db audit log** -- immutable audit table synced from all projects.
