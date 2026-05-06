# Orchestrator Usage — How-to Recipes

Practical, copy-pasteable recipes for driving Agent Baton. Each section
answers a single "how do I X?" question. Commands link to
[cli-reference.md](cli-reference.md) for full flag detail.

## Recipes

| # | Recipe |
|---|--------|
| 1 | [Plan and execute a simple task](#1-plan-and-execute-a-simple-task) |
| 2 | [Resume a crashed execution](#2-resume-a-crashed-execution) |
| 3 | [Run a high-risk task with auditor gates](#3-run-a-high-risk-task-with-auditor-gates) |
| 4 | [Use the orchestrator for cross-domain refactors](#4-cross-domain-refactors) |
| 5 | [Inspect a trace after completion](#5-inspect-a-trace) |
| 6 | [View execution telemetry and scores](#6-telemetry-and-scores) |
| 7 | [Override agent routing for a phase](#7-override-routing) |
| 8 | [Approve or reject a HIGH-risk plan](#8-approve-or-reject) |
| 9 | [Add a knowledge pack to a project](#9-add-a-knowledge-pack) |
| 10 | [Track an autonomous incident with `baton beads`](#10-track-an-incident) |
| 11 | [Run the loop manually vs from Claude Code](#11-manual-vs-claude-code) |
| 12 | [Drive a multi-execution session safely](#12-multi-execution) |
| 13 | [Cancel or fail a stuck execution](#13-cancel-or-fail) |

The CLI emits one of six `ACTION:` lines per iteration: `DISPATCH`,
`GATE`, `APPROVAL`, `FEEDBACK`, `INTERACT`, `COMPLETE`, or `FAILED`
(plus a transient `WAIT` while parallel steps drain). Action format is
defined by `_print_action` in `agent_baton/cli/commands/execution/execute.py`
and treated as public protocol — see `docs/invariants.md`.

---

## 1. Plan and execute a simple task

**Goal**: Run a task end-to-end with one command.

**Pre-reqs**: `baton` installed; project has `pyproject.toml` or `package.json` for stack detection.

**Steps**:

1. Plan ([`baton plan`](cli-reference.md#baton-plan)):
   ```bash
   baton plan "Add CSV export endpoint to the reports API" --save --explain
   ```
2. Inspect: `cat .claude/team-context/plan.md`.
3. Run autonomously ([`baton execute run`](cli-reference.md#baton-execute-start)):
   ```bash
   baton execute run
   ```
4. Watch for `ACTION: COMPLETE`.

**Expected output** (truncated): `Plan saved: ... (4 phases, 7 steps)` then `ACTION: DISPATCH` lines and `ACTION: COMPLETE`.

**See also**: [Recipe 2](#2-resume-a-crashed-execution), [Recipe 5](#5-inspect-a-trace), [`baton plan`](cli-reference.md#baton-plan).

---

## 2. Resume a crashed execution

**Goal**: Pick up after a session crash, rate-limit, or terminal close.

**Pre-reqs**: `execution-state.json` still exists under `.claude/team-context/`.

**Steps**:

1. Check status ([`baton execute status`](cli-reference.md#baton-execute-status)): `baton execute status`.
2. Resume ([`baton execute resume`](cli-reference.md#baton-execute-resume)): `baton execute resume`.
3. Continue with `baton execute run`.
4. If `budget_exceeded`: `baton execute resume-budget` first.

**Expected output**: `Resumed task: <id>`, status block (Phase X/Y, Steps M/N), then the next `ACTION:` line.

**See also**: [Recipe 13](#13-cancel-or-fail), [Troubleshooting](troubleshooting.md), [`baton execute resume`](cli-reference.md#baton-execute-resume).

---

## 3. Run a high-risk task with auditor gates

**Goal**: Execute work touching regulated data or security-sensitive paths with mandatory pre/post-execution review.

**Pre-reqs**: `auditor` and `subject-matter-expert` agents installed.

**Steps**:

1. Classify ([`baton classify`](cli-reference.md#baton-classify)):
   ```bash
   baton classify "Update PHI redaction rules" --files agent_baton/api/routes/exports.py
   ```
   Confirm `Risk Level: HIGH` or `CRITICAL`.

2. Plan with explicit auditor inclusion (planner adds them automatically on HIGH risk; this is belt-and-braces):
   ```bash
   baton plan "Update PHI redaction rules in patient export pipeline" \
       --save --explain \
       --agents subject-matter-expert,auditor,backend-engineer,test-engineer
   ```

3. Run; engine pauses on `ACTION: APPROVAL`:
   ```bash
   baton execute run
   ```

4. Decide ([`baton execute approve`](cli-reference.md#baton-execute-approve)):
   ```bash
   baton execute approve --phase-id 1 --result approve
   ```

5. To override an auditor VETO with audit logging:
   ```bash
   baton execute run --force --justification "Hotfix; SecOps ticket #4421"
   ```

**See also**: [Recipe 8](#8-approve-or-reject), [`baton classify`](cli-reference.md#baton-classify), [`baton compliance`](cli-reference.md#baton-compliance).

---

## 4. Cross-domain refactors

**Goal**: Coordinate a change spanning backend, frontend, infra, and docs.

**Pre-reqs**: Project root has both backend and frontend config (e.g. `pyproject.toml` and `pmo-ui/package.json`).

**Steps**:

1. Plan with a richer description so the planner splits phases by concern:
   ```bash
   baton plan "Rename 'plan' to 'mission' across API routes, React components, CLI help, and docs" \
       --save --explain --intervention medium
   ```
2. Inspect phase split: `grep -E '^## Phase' .claude/team-context/plan.md`.
3. Predict file conflicts and run with prediction enabled:
   ```bash
   baton predict-conflicts
   baton execute start --predict-conflicts
   baton execute run
   ```
4. Review the cross-agent timeline: `baton trace --last`.

**See also**: [Recipe 7](#7-override-routing), [Recipe 10](#10-track-an-incident), [`baton plan`](cli-reference.md#baton-plan).

---

## 5. Inspect a trace

**Goal**: See exactly what each agent did, when, and what tokens it spent.

**Pre-reqs**: An execution has finished (or at least logged events).

**Steps**:

1. List recent traces ([`baton trace`](cli-reference.md#baton-trace)):
   ```bash
   baton trace --count 10
   baton trace --last
   baton trace --summary 2026-04-28-add-csv-export-7a2d5855
   ```

2. For deeper analysis ([`baton query`](cli-reference.md#baton-query)):
   ```bash
   baton query task-detail 2026-04-28-add-csv-export-7a2d5855
   ```

**See also**: [Recipe 6](#6-telemetry-and-scores), [`baton trace`](cli-reference.md#baton-trace), [`baton query`](cli-reference.md#baton-query).

---

## 6. Telemetry and scores

**Goal**: Audit token spend, agent reliability, and gate pass rates.

**Pre-reqs**: At least one completed execution recorded with `--session-id`.

**Steps**:

1. Top-level usage ([`baton usage`](cli-reference.md#baton-usage)): `baton usage`.
2. Agent scorecards ([`baton scores`](cli-reference.md#baton-scores)):
   ```bash
   baton scores --agent backend-engineer
   baton scores --trends
   ```
3. Telemetry events and gate stats:
   ```bash
   baton telemetry --recent 50
   baton query gate-stats --days 30
   ```

**Expected output**: `Real tokens: X (N steps with real data)` and `Estimated tokens: Y`. If "none yet" appears, your `record` calls were missing `--session-id` — see [Token Reduction SOPs](#token-reduction-sops) Rule 2.

**See also**: [Recipe 5](#5-inspect-a-trace), [`baton scores`](cli-reference.md#baton-scores), [`baton usage`](cli-reference.md#baton-usage).

---

## 7. Override routing

**Goal**: Force specific agents instead of auto-routing.

**Pre-reqs**: Agents installed (`baton agents` to confirm).

**Steps**:

1. Override at plan time:
   ```bash
   baton plan "Refactor the conflict predictor to a streaming API" \
       --save --explain --agents architect,backend-engineer--python,test-engineer
   ```

2. Or amend an in-flight plan ([`baton execute amend`](cli-reference.md#baton-execute-amend)):
   ```bash
   baton execute amend --description "Add a node-side mirror" \
       --add-step "2:frontend-engineer:Update JS client to call new streaming API"
   ```

3. Use a non-default model: `baton execute run --model opus`.

**See also**: [Recipe 4](#4-cross-domain-refactors), [`baton plan`](cli-reference.md#baton-plan), [`baton execute amend`](cli-reference.md#baton-execute-amend).

---

## 8. Approve or reject

**Goal**: Respond to an `ACTION: APPROVAL` checkpoint.

**Pre-reqs**: Loop paused on an APPROVAL action.

**Steps**:

1. Read the approval context printed inline (Phase, Message, `--- Approval Context ---` block, and Options line).

2. Respond ([`baton execute approve`](cli-reference.md#baton-execute-approve)):
   ```bash
   baton execute approve --phase-id 2 --result approve
   baton execute approve --phase-id 2 --result reject \
       --feedback "Missing rollback plan for the migration step"
   baton execute approve --phase-id 2 --result approve-with-feedback \
       --feedback "Approved; please add a follow-up SLO check"
   ```

3. Resume: `baton execute run`.

**See also**: [Recipe 3](#3-run-a-high-risk-task-with-auditor-gates), [`baton execute approve`](cli-reference.md#baton-execute-approve).

---

## 9. Add a knowledge pack

**Goal**: Surface project-specific docs to every dispatched agent.

**Pre-reqs**: A folder of `.md` files to attach.

**Steps**:

1. Place under `.claude/knowledge-packs/<pack-name>/`:
   ```bash
   mkdir -p .claude/knowledge-packs/api-conventions
   cp docs/api-style.md .claude/knowledge-packs/api-conventions/
   ```

2. Attach at plan time (`--knowledge-pack` is repeatable):
   ```bash
   baton plan "Add /v2/users endpoint" --save --explain \
       --knowledge-pack api-conventions \
       --knowledge docs/auth-patterns.md
   ```

3. Verify and audit:
   ```bash
   grep -A2 "knowledge_pack\|knowledge_path" .claude/team-context/plan.json
   baton knowledge ranking
   baton knowledge effectiveness
   ```

4. Transfer ([`baton transfer`](cli-reference.md#baton-transfer)):
   ```bash
   baton transfer --export /path/to/other-project --knowledge api-conventions
   ```

**See also**: [Recipe 4](#4-cross-domain-refactors), [`baton plan`](cli-reference.md#baton-plan), [`baton transfer`](cli-reference.md#baton-transfer).

---

## 10. Track an incident

**Goal**: When an agent finds a bug mid-flight, file a tracked bead so the main flow continues.

**Pre-reqs**: Active execution (`BATON_TASK_ID` set, or pass `--task-id`).

**Steps**:

1. Create the bead ([`baton beads create`](cli-reference.md#baton-beads)):
   ```bash
   baton beads create --type warning \
       --content "ExecutionEngine.complete() drops trace data on fresh CLI process." \
       --tag bug --tag observability \
       --file agent_baton/core/engine/executor.py \
       --confidence high
   ```

2. List, close, promote:
   ```bash
   baton beads list --status open --type warning
   baton beads close bd-1234 --summary "Fixed in PR #62."
   baton beads promote bd-1234
   ```

3. Inspect the dependency graph: `baton beads graph --task $BATON_TASK_ID`.

**Note**: BeadStore writes need a project `baton.db`. `UNIQUE constraint failed: beads.bead_id` means the bead exists already — use `baton beads show <id>`.

**See also**: [Troubleshooting](troubleshooting.md), [`baton beads`](cli-reference.md#baton-beads).

---

## 11. Manual vs Claude Code

**Goal**: Pick the right driver — three modes, choose by intent.

| Driver | When | Command |
|--------|------|---------|
| Headless (default) | Most tasks | `baton execute run` |
| Manual step loop | Debugging, INTERACT phases, demos | `baton execute next` + record/gate/approve |
| Claude Code orchestrator agent | Inside a Claude Code session | invoke the `orchestrator` agent |

### Headless

```bash
baton plan "task description" --save
baton execute run --max-steps 50
```

Pauses only on: `ACTION: APPROVAL`, failed gate, `ACTION: INTERACT`, or `--max-steps` ceiling.

### Manual

```bash
baton execute start
while :; do
    out=$(baton execute next --terse)
    case "$out" in
      *"ACTION: COMPLETE"*) baton execute complete; break;;
      *"ACTION: DISPATCH"*) ;;  # dispatch agent yourself, then `record`
      *"ACTION: GATE"*)     ;;  # run gate, then `gate --result pass|fail`
      *"ACTION: APPROVAL"*) ;;  # decide, then `approve --result ...`
    esac
done
```

`--terse` writes the full delegation prompt to `.claude/team-context/current-dispatch.prompt.md` and emits only a pointer in stdout.

### Claude Code orchestrator agent

In a Claude Code session, ask the `orchestrator` agent to take over. It uses the manual loop internally but parses `ACTION:` lines and dispatches subagents via the Agent tool. The orchestrator MUST run at the top level of a conversation — it cannot be dispatched as a subagent itself.

**See also**: [Token Reduction SOPs](#token-reduction-sops), [Recipe 1](#1-plan-and-execute-a-simple-task), [`baton execute`](cli-reference.md#baton-execute).

---

## 12. Multi-execution

**Goal**: Run two or more executions without clobbering each other.

**Pre-reqs**: Each terminal needs to know its task ID. Resolution: `--task-id` → `BATON_TASK_ID` env → `active-task-id.txt`.

**Steps**:

1. Capture the task ID at start time:
   ```bash
   baton execute start --output json | tee start.json
   export BATON_TASK_ID=$(jq -r '.task_id' start.json)
   ```

2. Or pass `--task-id` on every call (required when env vars don't persist between tool invocations):
   ```bash
   baton execute next --task-id 2026-04-28-add-csv-...
   baton execute record --task-id 2026-04-28-add-csv-... --step 1.1 ...
   ```

3. Audit and switch:
   ```bash
   baton execute list
   baton execute switch 2026-04-28-add-csv-...
   ```

4. For agents on different branches, set `isolation: "worktree"` on the dispatch. The CLI emits `Worktree:` and `Branch:` fields on `ACTION: DISPATCH` when allocated.

**See also**: [Troubleshooting](troubleshooting.md), [`baton execute list`](cli-reference.md#baton-execute-list).

---

## 13. Cancel or fail

**Goal**: Permanently end a run that cannot proceed.

**Pre-reqs**: Execution in `running`, `gate_failed`, or `approval_pending` state.

**Steps**:

1. Find it:
   ```bash
   baton execute list
   baton execute status --task-id <task-id>
   ```

2. Cancel: `baton execute cancel --reason "Superseded by hotfix branch"`.

3. Permanently fail a gate-stuck run: `baton execute fail --phase-id 2`.

4. Reset a failed gate to retry: `baton execute retry-gate --phase-id 2 && baton execute run`.

5. Clear `budget_exceeded` lockout: `baton execute resume-budget`.

**See also**: [Recipe 2](#2-resume-a-crashed-execution), [Troubleshooting](troubleshooting.md), [`baton execute`](cli-reference.md#baton-execute).

---

## Token Reduction SOPs

These rules cut per-session token spend by 60-90%. Apply by default.

**Rule 1 — Headless by default.** `baton execute run` (not `next`).

**Rule 2 — Real token tracking on every `record`.**
```bash
baton execute record --step 1.1 --agent backend-engineer \
    --status complete --outcome "Implemented endpoint" \
    --session-id "$CLAUDE_SESSION_ID" \
    --step-started-at "2026-04-28T13:00:00Z"
```
Activates `core/observe/jsonl_scanner.py`, which sums real token usage from `~/.claude/projects/<slug>/<sid>.jsonl`. Without it, the engine falls back to a `len(text)/4` heuristic.

**Rule 3 — Terse dispatch.** `baton execute next --terse` — full prompt goes to `.claude/team-context/current-dispatch.prompt.md`; stdout gets a pointer only.

**Rule 4 — Compact plan summary.** `baton plan --save` emits a four-line summary by default; only add `--verbose` when you need full markdown inline.

**Rule 5 — Trust knowledge dedup.** The dispatcher tracks `delivered_knowledge` and downgrades repeat inlines to references automatically.

**Rule 6 — File-references over inline output.** Pass `--files src/foo.py,tests/test_foo.py` rather than re-reading and inlining.

**Rule 7 — Check real spend.** `baton usage` shows `Real tokens: X (N steps with real data)` vs `Estimated tokens: Y`.

---

## Reference

| Action | Emitted by | You respond with |
|--------|-----------|------------------|
| `DISPATCH` | `baton execute next` | dispatch agent, then `baton execute record` |
| `GATE` | `baton execute next` | run gate, then `baton execute gate --result pass\|fail` |
| `APPROVAL` | `baton execute next` | `baton execute approve --result approve\|reject\|approve-with-feedback` |
| `FEEDBACK` | `baton execute next` | `baton execute feedback --question-id ID --chosen-index N` |
| `INTERACT` | `baton execute next` | `baton execute interact --input "..."` or `--done` |
| `WAIT` | `baton execute next` | wait for parallel steps; call `next` again |
| `COMPLETE` | `baton execute next` | `baton execute complete` |
| `FAILED` | `baton execute next` | inspect `baton execute status`, then `cancel`/`fail` |

For the action protocol see `_print_action` in `agent_baton/cli/commands/execution/execute.py` (public API per `docs/invariants.md`). Per-command flags: [cli-reference.md](cli-reference.md). Symptom-keyed fixes: [troubleshooting.md](troubleshooting.md).
