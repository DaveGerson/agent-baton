# Agent Feedback Audit Plan

**Source**: Pre-launch functional audit feedback (2026-04-03)
**Created**: 2026-04-02
**Status**: Draft

---

## Feedback Summary

An agent conducting a multi-domain pre-launch audit of an executive scorecard
reported 7 issues and 1 positive observation after using Agent Baton v0.1.0.
The audit had to be manually orchestrated after the auto-planner failed to
produce a usable plan. The core themes are:

1. **Planner limitations** (3 issues): The planner ignores structured
   multi-phase task descriptions, never generates TEAM_DISPATCH steps despite
   engine support, and misclassifies audit tasks as bug-fix.
2. **Engine gaps** (2 issues): No cancel/abandon command for stale executions;
   no way to import a hand-crafted plan.
3. **Platform/CLI gaps** (2 issues): Unicode crash on Windows; no agent pairing
   mechanism.
4. **Positive**: Execution state persistence, task-id scoping, and action
   taxonomy are solid.

---

## Issue-by-Issue Analysis

### Issue 1 — Auto-planner ignores structured descriptions (Critical)

**Current state**: The planner (`core/engine/planner.py`) classifies tasks via
`_TASK_TYPE_KEYWORDS` (keyword-first-match, line 265) and maps to default
agent lists via `_DEFAULT_AGENTS` (line 94). It does not parse structured
multi-phase descriptions or extract agent type hints from the task text.

The `--agents` CLI flag (line 38 of `plan_cmd.py`) already exists and
overrides auto-selection, but the reporter was unaware of it or it was
insufficient for multi-phase plans where different phases need different
agents.

**Codebase investigation needed**:
- `planner.py` lines 386–540: `create_plan()` flow — how task_type and agents
  are resolved.
- `planner.py` lines 440–480: Classifier fallback to keyword path.
- `planner.py` lines 95–114: `_DEFAULT_AGENTS` and `_DEFAULT_PHASES` mappings.

**Remediation options**:
1. **Structured description parser**: Add a pre-processing step that detects
   structured input (numbered phases, agent mentions) and extracts a skeleton
   before falling back to keyword classification.
2. **Per-phase agent overrides**: Extend `--agents` to accept phase-level
   mappings (e.g. `--agents "phase1:architect,viz-expert;phase2:sme"`).
3. **Agent hint extraction**: Scan task text for known agent names and
   constrain the pool accordingly.

**Priority**: P0 — this caused complete planner failure.

---

### Issue 2 — No TEAM_DISPATCH in planner output (Major)

**Current state**: The engine supports TEAM_DISPATCH and the planner has
`_consolidate_team_step()` (line 1371) which merges multiple steps in a phase
into a single team step. However, this only consolidates existing steps — the
planner never proactively generates team steps for paired assessment patterns
(e.g., "viz expert + SME review this together").

**Remediation options**:
1. Add a `team_agents` field to phase templates that triggers TEAM_DISPATCH
   generation for phases requiring joint assessment.
2. Detect pairing keywords ("pair", "joint", "together", "adversarial review")
   in task descriptions and generate team steps.
3. Allow `--phases` to specify team compositions directly.

**Priority**: P1 — the infrastructure exists but is not leveraged.

---

### Issue 3 — No cancel/abandon for stale executions (Major)

**Current state**: The REST API has `DELETE /executions/{task_id}` which
transitions running → failed, but there is NO CLI command for this. The CLI
subcommands are: execute, plan, decide, async, daemon, status. A user stuck
with a stale execution has no CLI path to cancel it.

The execution status model (`models/execution.py`) supports: running,
gate_pending, approval_pending, complete, failed — but not "cancelled".

**Remediation**:
1. **Add `baton execute cancel [--task-id ID]`** — CLI command that calls the
   same logic as the REST API's cancel endpoint. Mark status as "cancelled"
   (new status value) rather than overloading "failed".
2. **Add auto-timeout**: Dispatched steps that haven't reported back within a
   configurable timeout (default: 1h) should be auto-expired. This can be a
   check in `execute next` or a daemon heartbeat.
3. **Add `cancelled` to ExecutionState.status** permitted values.

**Priority**: P1 — directly blocks workflow recovery.

---

### Issue 4 — Unicode crash on Windows (Minor)

**Current state**: `cli/main.py` lines 64–75 already reconfigure stdout/stderr
to UTF-8 with replacement fallback on Windows. However, the `plan --explain`
path calls `print(planner.explain_plan(plan))` (plan_cmd.py:158) which goes
through stdout — so the fix should already apply.

**Investigation needed**: The crash may occur when the reconfigure call itself
fails (e.g., on older Python where `reconfigure` is not available on the
stream), or in a subprocess/pipe context where stdout is not a TextIOWrapper.

**Remediation**:
1. Add a guard: `if hasattr(stream, 'reconfigure')` is already present — add
   a fallback for streams without `reconfigure` (set
   `PYTHONIOENCODING=utf-8:replace` or wrap the stream).
2. Add a test that `explain_plan()` output is ASCII-safe or at least
   round-trips through `encode('ascii', errors='replace')`.

**Priority**: P2 — workaround is to set `PYTHONIOENCODING=utf-8`.

---

### Issue 5 — Wrong task classification for audits (Minor)

**Current state**: `_TASK_TYPE_KEYWORDS` (planner.py:265–274) maps "review"
to "documentation" type. There is no "audit", "assessment", or "score"
keyword mapped to "data-analysis".

The `audit` keyword exists in `classifier.py` (line 98–99) but only in the
data sensitivity/compliance context, not for task-type inference.

**Remediation**:
1. Add audit/assessment/scorecard keywords to `_TASK_TYPE_KEYWORDS`:
   ```python
   ("data-analysis", [..., "audit", "assessment", "scorecard", "evaluate", "review"])
   ```
   Note: "review" conflicts with "documentation" — need to handle ordering
   or use multi-word matching ("code review" → documentation, "audit review"
   → data-analysis).
2. Alternatively, add a new task type "audit" with its own phase/agent
   template.

**Priority**: P2 — does not affect execution, only tracking metrics.

---

### Issue 6 — No plan import from file (Major)

**Current state**: Plans are only created via `baton plan "summary"` which
invokes the `IntelligentPlanner`. There is no `baton plan import` command.
The engine reads plans from `.claude/team-context/plan.json` — so a user
could technically hand-write this file, but there is no validation or CLI
support for it.

**Remediation**:
1. **Add `baton plan import <path>`** — reads a JSON file, validates it
   against the `MachinePlan` schema (via `MachinePlan.from_dict()`), saves
   it to the standard plan location, and runs the same post-processing
   (risk assessment, gate insertion) as the auto-planner.
2. Provide a `baton plan template` command that outputs a skeleton plan.json
   for hand-editing.

**Priority**: P1 — power users and complex workflows need this escape hatch.

---

### Issue 7 — No agent pairing mechanism (Minor)

**Current state**: Claude Code dispatches single agents via the Agent tool.
TEAM_DISPATCH in the engine assigns multiple agents to a step but they
execute sequentially. There is no PAIR_DISPATCH that pipes one agent's
output into another.

**Remediation**:
1. Add a PAIR_DISPATCH action type where the engine specifies two agents.
   The orchestrator runs agent A, captures output, and passes it as context
   to agent B. Agent B's prompt includes "Review and challenge the following
   assessment from {agent_a}."
2. This is a longer-term enhancement — it requires changes to the action
   taxonomy in `executor.py` and the dispatch loop in `execute.py`.

**Priority**: P3 — nice-to-have, current workaround is acceptable.

---

### Issue 8 — Execution state persistence (Positive)

No action needed. Note the positive feedback for the team — the SQLite-backed
execution state, task-id scoping, and action taxonomy are working well in
production-like usage.

---

## Remediation Plan

### Phase 1 — Critical Path (Target: Week 1)

| Task | Issue | Owner | Files |
|------|-------|-------|-------|
| Structured description parser in planner | #1 | TBD | `core/engine/planner.py` |
| Add `baton execute cancel` CLI command | #3 | TBD | `cli/commands/execution/execute.py`, `models/execution.py` |
| Add `cancelled` status to ExecutionState | #3 | TBD | `models/execution.py`, `core/engine/persistence.py` |

### Phase 2 — Major Improvements (Target: Week 2–3)

| Task | Issue | Owner | Files |
|------|-------|-------|-------|
| TEAM_DISPATCH generation in planner | #2 | TBD | `core/engine/planner.py` |
| `baton plan import` command | #6 | TBD | `cli/commands/execution/plan_cmd.py` |
| `baton plan template` command | #6 | TBD | `cli/commands/execution/plan_cmd.py` |
| Auto-timeout for dispatched steps | #3 | TBD | `core/engine/executor.py` |

### Phase 3 — Polish (Target: Week 4)

| Task | Issue | Owner | Files |
|------|-------|-------|-------|
| Add audit/assessment keywords | #5 | TBD | `core/engine/planner.py` |
| Unicode fallback hardening | #4 | TBD | `cli/main.py`, `core/engine/planner.py` |
| PAIR_DISPATCH design doc | #7 | TBD | `docs/design-decisions.md` |

### Testing Requirements

Each remediation must include:
- Unit tests for the new/changed functionality
- Integration test in `tests/test_engine_integration.py` for engine changes
- CLI handler test for new commands
- Cross-layer linkage verification per CLAUDE.md rules

### Validation Criteria

The audit plan is considered complete when:
1. `baton plan "multi-phase audit with viz-expert and SME"` produces a plan
   that correctly assigns visualization-expert and SME agents to separate
   phases with team steps.
2. `baton execute cancel` successfully transitions a running execution to
   cancelled status.
3. `baton plan import plan.json` loads and validates a hand-crafted plan.
4. `baton plan --explain` does not crash on a Windows cp1252 terminal.
5. An audit task is classified as "data-analysis" rather than "bug-fix".
