# Troubleshooting

Symptom-keyed reference for failures while planning, executing, or
operating Agent Baton. Each entry is **Symptom → Cause → Fix**. For
runnable recipes see [orchestrator-usage.md](orchestrator-usage.md).
For full CLI flag detail see [cli-reference.md](cli-reference.md).

---

## Plan-time failures

### Symptom: `baton plan` returns "no tasks" or an empty plan

**Cause**: Task summary too short or vague (planner produced no phases),
or `--from-template` referenced a missing template.

**Fix**: Provide a richer description — the planner uses the summary
verbatim in delegation prompts. `baton plan "auth" --save` (too vague)
vs `baton plan "Add JWT authentication middleware with login/logout
endpoints and integration tests" --save`. If you used
`--from-template NAME`, confirm the template exists at
`.claude/plan-templates/NAME.json`. See
[`baton plan`](cli-reference.md#baton-plan).

---

### Symptom: Stack detection returns `unknown` and routing picks wrong agents

**Cause**: `baton plan` looks for `pyproject.toml`, `package.json`,
`go.mod` in the project root only. Subdirectory configs are not
auto-detected. Root cause of
[BUG-004](baton-engine-bugs.md#bug-004-agent-routing-picks-wrong-stack-flavor)
where `pmo-ui/package.json` masks the root `pyproject.toml`.

**Fix**: Pass `--project PATH` to the directory holding the canonical
config, or override agents with `--agents backend-engineer--python,...`.
Diagnose with `baton detect --path .`. See
[`baton plan`](cli-reference.md#baton-plan) and
[`baton detect`](cli-reference.md#baton-detect).

---

### Symptom: Plan has generic descriptions ("Implement feature", "Add tests")

**Cause**: Task summary too generic; planner can't synthesise specific
delegation prompts from one or two words.

**Fix**: Rewrite the summary to name the file, behaviour, or contract.
The planner inlines the summary into every step's delegation prompt.

---

### Symptom: `plan.md` shows `[APPROVAL REQUIRED]` but the engine never pauses

**Cause**: [BUG-005](baton-engine-bugs.md#bug-005-plan-phase-1-labeled-approval-required-but-requires_approval-is-false) —
the markdown renderer adds the label based on phase name keywords,
while `requires_approval` in `plan.json` is `false`.

**Fix**: Hand-edit `plan.json` to set `"approval_required": true`, or
amend during execution:

```bash
baton execute amend --description "Promote design phase to approval gate" \
    --add-phase "Design Review:auditor" --after-phase 1
```

---

## Execute-time failures

### Symptom: `baton execute start` says approval required

**Cause**: HIGH/CRITICAL risk classification triggered the governance
preset. The auditor's pre-execution review needs to be recorded before
the loop dispatches any work.

**Fix**: Run the auditor step (engine queues it first). When
`ACTION: APPROVAL` is emitted:

```bash
baton execute approve --phase-id 1 --result approve
baton execute run
```

To override a VETO with audit logging:
`baton execute run --force --justification "..."`. See
[orchestrator-usage Recipe 3](orchestrator-usage.md#3-run-a-high-risk-task-with-auditor-gates).

---

### Symptom: "plan.json does not exist" on `baton execute start`

**Cause**: `baton plan` was not run with `--save`, or you are in a
different directory than where the plan was saved.

**Fix**: `baton plan "..." --save`, or pass an explicit path:
`baton execute start --plan /path/to/plan.json`. See
[`baton execute start`](cli-reference.md#baton-execute-start).

---

### Symptom: "No active execution state found"

**Cause**: `baton execute next`/`record`/`gate` was called before
`baton execute start`, the wrong task ID was resolved, or
`execution-state.json` was deleted.

**Fix**: `baton execute resume` first; if that fails, check
`baton execute list` and `baton execute status`. As a last resort,
re-plan and `baton execute start` fresh.

---

### Symptom: `baton execute record` fails with "invalid choice" for `--status`

**Cause**: Only `complete` and `failed` are accepted (not `pass`, `done`,
`success`, `ok`).

**Fix**: Use `--status complete` or `--status failed`. To mark a step
in-flight, use `baton execute dispatched` instead.

---

### Symptom: gate failed: pytest exit 1

**Cause**: A QA gate (`ACTION: GATE`) ran a command returning non-zero.
The engine moves the phase to `gate_failed` and pauses.

**Fix**: Inspect output (printed inline; persisted under
`.claude/team-context/executions/<task>/gate-output/`). Then choose:

- **Retry** after fixing: `baton execute retry-gate --phase-id 2 && baton execute run`
- **Permanently fail**: `baton execute fail --phase-id 2`
- **Manually record pass**: `baton execute gate --phase-id 2 --result pass --gate-output "..."`

See [`baton execute gate`](cli-reference.md#baton-execute-gate).

---

### Symptom: `baton execute next` is stuck on `ACTION: wait`

**Cause**: [BUG-002](baton-engine-bugs.md#bug-002-baton-execute-next-stuck-in-action-wait-after-step-completion) —
SQLite and JSON state stores drifted. Engine reads "dispatched" from
one store while the other has "complete". Triggered by
[BUG-001](baton-engine-bugs.md#bug-001-baton-execute-record-silently-fails-on-sqlite-unique-constraint).

**Fix**: Inspect raw state with
`baton execute status --output json | jq '.steps'`. Re-record stale
steps with `baton execute record --status complete`. If a SQLite UNIQUE
error blocks the re-record, edit `execution-state.json` directly, then
`baton execute resume`.

---

### Symptom: session crashed; how to resume

**Cause**: API rate limit, terminal closed, machine rebooted, network
glitch. State is on disk in `execution-state.json` and `baton.db`.

**Fix**:

```bash
baton execute status      # confirm task is recoverable
baton execute resume      # picks up from saved state
baton execute run         # continue autonomously
```

If `resume` says no state found, the run was completed or cancelled —
verify with `baton execute list`. `git log --oneline` shows which
agents committed before the crash.

---

### Symptom: agent dispatch returned without writing files

**Cause**: Missing `permissionMode: auto-edit` in agent frontmatter,
wrong cwd, agent self-determined "no work needed" (recorded `complete`
with no `--files`), or worktree garbage-collected before files reached
the parent repo.

**Fix**: Confirm `permissionMode: auto-edit` in `agents/<name>.md`.
Inspect the worktree path (`Worktree:` field on `ACTION: DISPATCH`).
Re-dispatch via `baton execute amend --add-step "..."` with explicit
acceptance criteria. Audit with `baton execute audit-isolation`.

---

### Symptom: BATON_TASK_ID not set or pointing at the wrong task

**Cause**: Resolution order is `--task-id` flag → `BATON_TASK_ID` env →
`active-task-id.txt` (repo-wide marker). When agentic callers run
multiple `Bash` calls, env vars do not persist between calls, so the
third path takes over and may pick a stale task.

**Fix**: Pass `--task-id` explicitly on every CLI call from an agent
context. Inspect the active binding with `baton execute status` (the
`Bound:` field shows the resolution path). Re-anchor with
`baton execute switch <task-id>`.

---

### Symptom: agent worked on the wrong branch

**Cause**: Parent repo HEAD silently drifts during multi-agent dispatch.
Branch checkout alone does not isolate uncommitted changes when
several agents run in parallel.

**Fix**: For concurrent agents on different branches, set
`isolation: "worktree"` on the dispatch (engine emits `Worktree:` and
`Branch:` fields when allocated). Run branch-sensitive ops from inside
the agent's worktree. GC stale worktrees with `baton execute worktree-gc`.
Open a retained failed worktree with
`baton execute takeover --task-id <id> --step <step-id>`.

---

### Symptom: rate-limited mid-execution

**Cause**: Too many API calls in a short window (Anthropic tier limits).

**Fix**: Wait for the rate window, then `baton execute resume && baton execute run`.
If a soft token budget fires, clear with `baton execute resume-budget`
(optionally bump with `baton execute run --token-budget 5000000`).

---

### Symptom: permission prompts on every agent action

**Cause**: Agent definition is missing `permissionMode: auto-edit` in
its YAML frontmatter.

**Fix**: Edit `agents/<agent-name>.md` and add the line. Re-install
with `scripts/install.sh` or `baton install --upgrade`.

---

## Storage and database failures

### Symptom: permission denied on `baton.db`

**Cause**: DB file owned by another user, or the project dir is read-only.

**Fix**: Check `ls -la .claude/team-context/baton.db`. Fix ownership,
or override with `export BATON_DB_PATH=/tmp/baton-test.db`. The CLI
walks upward from `cwd` to discover `baton.db`, so a deeper directory
may pick the wrong file — pin with `BATON_DB_PATH` or run from the
project root.

---

### Symptom: "no such table: external_sources" or similar

**Cause**: `baton.db` schema is older than the installed CLI.

**Fix**: `baton storage preflight` (safety check + backup), then
`baton storage migrate` (JSON → SQLite). `migrate-storage` is a
deprecated alias.

---

### Symptom: "UNIQUE constraint failed"

**Cause**: Trying to add a row that already exists. Common: `baton
source add` (already added), `baton beads create` (duplicate id),
`baton execute record` after `dispatched` (BUG-001 condition).

**Fix**: For sources, `baton source remove <id>` then re-add. For
beads, `baton beads show <id>` instead of recreating. For step records
see [stuck on `ACTION: wait`](#symptom-baton-execute-next-is-stuck-on-action-wait);
underlying engine bug is
[BUG-001](baton-engine-bugs.md#bug-001-baton-execute-record-silently-fails-on-sqlite-unique-constraint).

---

### Symptom: `central.db` schema mismatch / `project_id` missing

**Cause**: You queried `project_id` against a per-project `baton.db`.
The `project_id` column only exists in `~/.baton/central.db`.

**Fix**: Use the right query CLI:

```bash
baton query tasks                                              # per-project
baton cquery --sql "SELECT project_id, COUNT(*) FROM tasks GROUP BY 1"  # cross-project
```

See [`baton cquery`](cli-reference.md#baton-cquery).

---

### Symptom: sync to `central.db` silently failing

**Cause**: Database locked, disk full, or permission denied on
`~/.baton/central.db`.

**Fix**:

```bash
baton sync status              # diagnose
ls -la ~/.baton/central.db
baton sync                     # retry the active project
baton sync --rebuild           # nuclear: full re-sync
```

---

## Concurrency and isolation failures

### Symptom: subagent edits collided

**Cause**: Two agents wrote to the same file in overlapping windows
without worktree isolation. Branch checkout alone does not separate
working trees.

**Fix**: Predict conflicts with `baton predict-conflicts`. Run with
prediction enabled (`baton execute start --predict-conflicts`) and
split conflicting steps into sequential phases. For mandatory
concurrency, force `isolation: "worktree"` in the agent definition or
plan amendment. Audit with `baton execute audit-isolation`.

---

### Symptom: wrong execution targeted in multi-terminal setup

**Cause**: Task ID resolved via `active-task-id.txt` (a repo-wide
pointer) instead of the per-session intended binding.

**Fix**: Export the right task ID per terminal after `start`:
`export BATON_TASK_ID=<task-id>`. Or pass `--task-id` explicitly. Check
the resolution path with `baton execute status` (the `Bound:` field).
See [orchestrator-usage Recipe 12](orchestrator-usage.md#12-multi-execution).

---

## Knowledge and approval failures

### Symptom: knowledge pack not loading

**Cause**: Pack folder in the wrong location (must be
`.claude/knowledge-packs/<name>/`); name passed to `--knowledge-pack`
doesn't match the directory name; or plan was generated before the pack
was added.

**Fix**: Confirm with `ls .claude/knowledge-packs/`, re-plan with
`--knowledge-pack <exact-dir-name>`, and verify with
`grep "knowledge_pack\|knowledge_path" .claude/team-context/plan.json`.
Audit with `baton knowledge ranking` and `baton knowledge usage <doc-id>`.
The dispatcher tracks `delivered_knowledge` and downgrades repeat
inlines to references — that is intended behaviour.

---

### Symptom: approval gate stuck — `ACTION: APPROVAL` returns repeatedly

**Cause**: Wrong `--phase-id`; previous result was `reject` (engine
waits for a replan amendment); or `BATON_APPROVAL_MODE=team` is set
and the same user is trying to self-approve.

**Fix**: Confirm phase ID matches the action header. If you rejected,
amend the plan: `baton execute amend --description "..." --add-step "..."`.
For team mode, switch reviewers or temporarily relax with
`BATON_APPROVAL_MODE=local baton execute approve ...`. See
[`baton execute approve`](cli-reference.md#baton-execute-approve).

---

### Symptom: `baton beads create` fails with `UNIQUE constraint` or "no such table"

**Cause**: `BeadStore.write()` requires a `bead_id` and a discoverable
`baton.db`. Either the table doesn't exist (migration needed) or a
duplicate bead is being created.

**Fix**: Run `baton storage migrate`. If the bead exists, view with
`baton beads list --limit 50` and `baton beads show <bead-id>`.
Subagents in worktrees rely on upward-walk discovery for `baton.db`;
if it fails, set `BATON_DB_PATH` explicitly.

---

## Installation and setup failures

### Symptom: `command not found: baton`

**Cause**: Python package not installed or `~/.local/bin` not on PATH.

**Fix**: `pip install -e ".[dev]"` from the repo root; verify with
`which baton`.

---

### Symptom: agents don't appear in `/agents` after install

**Cause**: Agent `.md` files were not copied to `.claude/agents/`.

**Fix**: `scripts/install.sh` from the repo root, or
`baton install --scope user --upgrade`. `--upgrade` preserves your
settings, CLAUDE.md, knowledge packs, and team-context while
overwriting agent and reference docs.

---

### Symptom: `install.sh: No such file or directory`

**Cause**: Running from outside the repo root.

**Fix**: `cd /path/to/agent-baton && scripts/install.sh`.

---

## Observability gaps

### Symptom: `baton trace` returns empty after `baton execute complete`

**Cause**: [BUG-1 in audit](audit/AUDIT-REPORT.md) — when the CLI runs
each command in a fresh process, `_trace` is `None` at `complete()`
time. Daemon-mode runs are unaffected.

**Fix** (workaround): `baton query task-detail <task-id>` and
`baton query phase-status` (reads persisted tables). For event-level
data, run via `baton daemon start`.

---

### Symptom: `baton scores` returns no data despite completed runs

**Cause**: [BUG-6 in audit](audit/AUDIT-REPORT.md) — `PerformanceScorer`
reads retros from the filesystem, but SQLite-mode projects write retros
only to the database.

**Fix**: Use `baton query agent-reliability` and
`baton query agent-history backend-engineer` until the fix lands.

---

### Symptom: `baton usage` shows `Real tokens: none yet`

**Cause**: `baton execute record` calls did not pass `--session-id`,
so `core/observe/jsonl_scanner.py` couldn't tie usage to a Claude
session JSONL. The engine fell back to a `len(text)/4` heuristic that
historically drifts by ~3 orders of magnitude.

**Fix**: Always pass `--session-id "$CLAUDE_SESSION_ID"` and
`--step-started-at "<ISO 8601 UTC>"` on every `record` call. See
[Token Reduction SOPs](orchestrator-usage.md#token-reduction-sops).

---

## Catch-all

- `baton execute status` — always safe; mutation-free state inspection.
- `baton execute list` — find the right task ID.
- [orchestrator-usage.md](orchestrator-usage.md) — runnable recipes.
- [cli-reference.md](cli-reference.md) — full flag detail.
- [baton-engine-bugs.md](baton-engine-bugs.md) — known engine bugs and
  workarounds.
- [audit/AUDIT-REPORT.md](audit/AUDIT-REPORT.md) — comprehensive
  functionality audit, including unwired feedback loops.
