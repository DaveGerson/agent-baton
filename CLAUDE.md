# Agent Baton — Development Guide

This repo contains the source for Agent Baton, a multi-agent orchestration
system for Claude Code.

## Repository Structure

```
agent_baton/       ← Python package (orchestration engine)
docs/              ← Architecture documentation (15 .md files)
agents/            ← Distributable agent definitions (20 .md files)
references/        ← Distributable reference docs (16 .md files)
templates/         ← CLAUDE.md + settings.json + skills/ installed to targets
scripts/           ← Install scripts + record_spec_audit_beads.py
tests/             ← Test suite (~6202 tests, pytest)
pmo-ui/            ← React/Vite PMO frontend (served at /pmo/)
audit-reports/     ← Architecture audit documents (8 reports)
proposals/         ← Design proposals and RFCs (6 documents)
reference_files/   ← Integration questionnaires, roadmaps, analysis docs (ignored)
.claude/           ← Project-specific orchestration setup (ignored)
```

## Key Rules

- `agents/` and `references/` are the **distributable** source of truth.
- `core/engine/` is the execution engine — changes here affect all users.
- `cli/commands/execution/execute.py` contains `_print_action()` — treat as public API.
- All imports use canonical paths (e.g. `from agent_baton.core.govern.classifier import DataClassifier`).

## Agent Roster & Usage

See **[docs/agent-roster.md](docs/agent-roster.md)** for the full roster of 47 agents.
See **[docs/orchestrator-usage.md](docs/orchestrator-usage.md)** for how to use the orchestrator.

## Development

```bash
pip install -e ".[dev]"    # Install in editable mode
pytest                     # Run tests (~6202 tests)
scripts/install.sh         # Re-install globally after editing agents/references
```

### Code Navigation (cymbal)

Use `cymbal investigate <symbol>` to find source, callers, and callees.
Use `cymbal impact <symbol>` before changing high-fanout symbols.

## Token Efficiency (MANDATORY)

- **Prefer file-references over inline tool output.**
- **Trust engine records; don't re-verify.**
- **Default to `baton execute run` for non-INTERACT phases.**
- **Don't re-read files already summarized in plan.md or beads.**

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Target a specific execution in multi-task scenarios | auto-detected |
| `BATON_APPROVAL_MODE` | PMO approval policy: `local` (self-approve) or `team` (different reviewer required) | `local` |
| `BATON_DB_PATH` | Override the project `baton.db` location (subagents in worktrees can also rely on the upward-walk discovery) | discovered |
| `ANTHROPIC_API_KEY` | Required for AI classification and Haiku classifier | (none) |

## Autonomous Incident Handling (MANDATORY)

Handle bugs/failures autonomously without pausing:
1. **Bead it.** `baton beads create --type warning ...`
2. **Fix in parallel.** Launch a background subagent on a separate branch.
3. **Require a regression test.**
4. **Continue the main flow.**

## Recent changes (2026-04-28 end-user-readiness sweep)

Twelve end-user-readiness concerns plus an autonomous planner fix shipped to master today (PRs #59 and #61–#72). Note: this is a focused append; PR #74 is doing the comprehensive documentation overhaul separately.

### New / changed environment variables

| Variable | Behaviour | PR |
|----------|-----------|----|
| `BATON_EXPERIMENTAL` | CSV opt-in for experimental subsystems. `BATON_EXPERIMENTAL=swarm` is now **required** to dispatch a `baton swarm` (the v1 stub disclosure landed at the same time). | #63 |
| `BATON_APPROVAL_MODE=team` | Now defaults `baton swarm --require-approval-bead` to ON. The explicit operator sign-off prompt and swarm-approval audit bead landed in PR #59. | #64, #59 |
| `BATON_RUN_TOKEN_CEILING` | Run-level token kill-switch as a USD float. Read fresh on every check; restored from state on `baton execute resume`. Selfheal/speculator/immune enforce; `Executor.dispatch()` only warns at HIGH/CRITICAL run start (see bd-3f80). | #67, #73 |
| `BATON_SELFHEAL_ENABLED` | Falsy values (`0`, `false`, `no`) emit a `selfheal_suppressed` row to `compliance-audit.jsonl`. PR #61 added regression coverage for the disabled fall-through. | #61 |
| `BATON_WORKTREE_STALE_HOURS` | Default lowered from `72` to `4`. GC now runs on every `baton execute complete`. Legacy alias `BATON_WORKTREE_GC_HOURS` still honoured. | #72 |
| `BATON_SKIP_GIT_NOTES_SETUP=1` | Opt out of automatic git-notes refspec setup at install time and silence the runtime warning emitted by `NotesAdapter.write()` when the wildcard refspec is missing. | #66 |

### New / changed CLI surface

| Command | Notes | PR |
|---------|-------|----|
| `baton souls revoke --reason X` | Revoke an agent soul. Persisted in `soul_revocations` (schema v34). | #68 |
| `baton souls list-revocations` | List active soul revocations. | #68 |
| `baton souls rotate` | Rotate a soul's signing identity. | #68 |
| `baton sync --migrate-storage` | Canonical path; replaces top-level `baton migrate-storage` (bd-8eef, deprecation warning on stderr). | #70 |
| `baton sync --verify ARCHIVE` | Canonical path; replaces top-level `baton verify-package` (bd-7eec). | #70 |
| `baton learn improve` | Canonical path; replaces top-level `baton improve` (bd-5049). | #70 |

### Other landings

- **WorktreeManager parent-repo detection** — now correctly walks up when `project_dir` is itself a worktree (#69, bd-c071).
- **Partitioner warning bead** — emits a `BEAD_WARNING` when libcst can't parse a file, so parallelism loss is visible in the bead stream (#62).
- **Executable-bead trust boundary** — the sandbox is **process-level only**. Do not extend `BATON_EXEC_BEADS_ENABLED` to external-origin input without further review. See `references/baton-patterns.md`. (#65)
- **Schema bumped to v34** — `immune_queue` (v33), `soul_revocations` (v34).
- **Planner explicit-agents fix (autonomous)** — `baton plan --agents ...` now honoured in the compound-subtask path (#71, bd-701e).

### Known integration gaps (tracked, not regressions)

| Gap | Bead | Surface |
|-----|------|---------|
| Per-dispatch token-ceiling enforcement | bd-3f80 | Selfheal/speculator/immune respect `BATON_RUN_TOKEN_CEILING`; main `Executor.dispatch()` only warns at HIGH/CRITICAL run start and restores `initial_run_spend_usd` on resume. Wiring into every dispatch is pending. |
| Soul caller migration | bd-1ca2 | Existing callers of `soul.verify` not yet routed through `SoulRouter.verify_signature()`. Revocation guard is opt-in for legacy code paths until the migration completes. |
| Wave 6.1 Part A bead-anchor wiring | bd-971d | `gastown_dual_write` / `_notes_adapter` / `_anchor_index` BeadStore handoff not on master yet. PR #76 restored the Part A `NotesAdapter` methods deleted by the Part C merge. |
| Swarm dispatcher v1 stubs | bd-c925, bd-2b9f | Real Haiku integration pending. Currently gated by `BATON_EXPERIMENTAL=swarm`. |
