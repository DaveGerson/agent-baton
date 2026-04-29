# CLAUDE.md — agent runtime config

This file is read by every Claude Code agent dispatched inside this repo. It is **not** documentation for humans. Marketing pitch, install steps, comparison tables: not here. Those live in `README.md`. Pages for human readers live in `docs/`.

## Repository layout (load-bearing)

```
agent_baton/       Python package (the orchestration engine)
agents/            Distributable agent definitions (33 .md)
references/        Distributable reference procedures (18 .md)
templates/         CLAUDE.md + settings.json + skills/ — installed to targets
scripts/           install.sh, install.ps1, record_spec_audit_beads.py
tests/             pytest suite
pmo-ui/            React/Vite frontend served at /pmo/
docs/              Public documentation (Diátaxis quadrants)
docs/internal/     Maintainer-only docs (audit, reviews, working drafts)
.claude/           Project-specific orchestration setup (not committed)
```

## Key files (treat as public APIs)

- `agent_baton/cli/commands/execution/execute.py` — `_print_action()` is the protocol surface between the engine and the orchestrator agent. Don't break its output shape.
- `agent_baton/core/engine/state.py` — execution state machine.
- `agent_baton/core/engine/protocols.py` — `ExecutionDriver` 15-method interface.
- `agent_baton/models/execution.py` — `ActionType` enum (9 values: DISPATCH, GATE, APPROVAL, COMPLETE, FAILED, WAIT, FEEDBACK, INTERACT, SWARM_DISPATCH) and `ExecutionState`.
- `agent_baton/api/` — FastAPI routers for the REST API + PMO UI backend.

All imports use canonical paths: `from agent_baton.core.govern.classifier import DataClassifier`. Never reach across submodules through `__init__.py` shortcuts.

## Orchestrator behavior (mandatory)

When the `orchestrator` agent is invoked:

1. Plan with `baton plan "<task>" --save --explain`. The engine handles routing, risk, budget, sequencing. Writes `plan.json` + `plan.md` to `.claude/team-context/`.
2. Start with `baton execute start`. The engine initializes tracing and returns the first action.
3. Drive the action loop. The engine returns DISPATCH (spawn the named agent with the provided prompt), GATE (run the named check), APPROVAL (wait for sign-off), or INTERACT (multi-turn dialogue). Record results with `baton execute record` and `baton execute gate`.
4. Finalize with `baton execute complete`. The engine writes the trace, usage log, and retrospective.
5. Use a feature branch; commit each agent's work individually.
6. If the session crashes, `baton execute resume` picks up from saved state.

## Agent roster

See [docs/agent-roster.md](docs/agent-roster.md) (33 agents). Recipes for common tasks: [docs/orchestrator-usage.md](docs/orchestrator-usage.md). For Baton's protocol contract from the agent side: [references/baton-engine.md](references/baton-engine.md).

## Code navigation

Use `cymbal` instead of grep for symbol lookup:

```bash
cymbal investigate <symbol>     # source, callers, callees
cymbal impact <symbol>          # blast radius before edits
```

## Token efficiency (mandatory)

- Prefer file-reference summaries over inline tool output.
- Trust engine records; don't re-verify what `plan.md` already states.
- Default to `baton execute run` for non-INTERACT phases.
- Don't re-read files already summarized in `plan.md` or beads.

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Target a specific execution in multi-task scenarios | auto-detected |
| `BATON_APPROVAL_MODE` | `local` (self-approve) or `team` (different reviewer required). In `team` mode, `baton swarm` defaults `--require-approval-bead` ON. | `local` |
| `BATON_DB_PATH` | Override per-project `baton.db` location | discovered |
| `BATON_SELFHEAL_ENABLED` | Enable speculator/selfheal escalation on gate failure. Falsy values (`0`, `false`, `no`) are honoured and emit a `selfheal_suppressed` row to `compliance-audit.jsonl`. | `0` |
| `BATON_RUN_TOKEN_CEILING` | Per-run cumulative spend cap (USD float). Read fresh on every check; restored on `baton execute resume`. Selfheal/speculator/immune respect it; main `Executor.dispatch()` only warns at HIGH/CRITICAL run start (bd-3f80). | unset |
| `BATON_WORKTREE_STALE_HOURS` | Worktree GC stale threshold in hours; legacy alias `BATON_WORKTREE_GC_HOURS`. GC runs on every `baton execute complete` (PR #72). | `4` |
| `BATON_EXPERIMENTAL` | CSV opt-in for experimental subsystems. Required for `baton swarm` (`BATON_EXPERIMENTAL=swarm`). | unset |
| `BATON_SWARM_ENABLED` | Required in addition to `BATON_EXPERIMENTAL=swarm` to dispatch a swarm refactor. | unset |
| `BATON_SOULS_ENABLED` | Wave 6.1 Part B persistent agent souls (signing + revocation). | `0` |
| `BATON_EXEC_BEADS_ENABLED` | Wave 6.1 Part C executable beads. Sandbox is process-level only — see `references/baton-patterns.md` trust-boundary section before extending to external-origin input. | `0` |
| `BATON_SKIP_GIT_NOTES_SETUP` | Silence install-time git-notes refspec setup and the runtime warning emitted by `NotesAdapter.write()` when the wildcard refspec is missing. | unset |
| `ANTHROPIC_API_KEY` | Required for AI risk classification and Haiku classifier | unset |

### Known integration gaps as of 2026-04-28

| Gap | Bead | Surface |
|-----|------|---------|
| Executor wiring of `BATON_RUN_TOKEN_CEILING` | bd-3f80 | Selfheal/speculator/immune respect the cap; `Executor.dispatch()` only warns at run start. |
| Soul caller migration | bd-1ca2 | Existing `soul.verify` callers not yet routed through revocation-aware `SoulRouter.verify_signature()`. |
| Wave 6.1 Part A integration | bd-971d | Git-notes bead persistence + executor BeadStore handoff is partial. |

## Regulated-domain rules

Any work touching regulated data, compliance systems, audit-controlled records, or industry-specific business rules MUST:

- Involve the `subject-matter-expert` agent for domain context.
- Involve the `auditor` agent for pre-execution and post-execution review.
- Follow the Regulated Data guardrail preset.

## Autonomous incident handling (mandatory)

Handle bugs/failures without pausing the main flow:

1. **Bead it** — `baton beads create --type warning --message "<incident>"`.
2. **Fix in parallel** — launch a subagent on a separate branch (use `isolation: "worktree"` for concurrent agents).
3. **Require a regression test.**
4. **Continue the main flow.**

Only pause for true human decisions (security, compliance, scope changes).

## Concurrent agent isolation (mandatory)

When dispatching parallel `Agent` calls that touch tracked files, use `isolation: "worktree"`. Branch checkout alone does not isolate uncommitted changes — the parent HEAD silently drifts during multi-agent dispatch.

## Documentation maintenance (mandatory)

After significant work, update the relevant docs before merging:

- `docs/architecture.md` + `docs/architecture/{high-level-design,technical-design,package-layout,state-machine}.md` — design and structure
- `docs/cli-reference.md` — when CLI surface changes
- `docs/api-reference.md` — when REST routes change
- `docs/agent-roster.md` — when agents are added or removed
- `README.md` — when public framing changes
- `CLAUDE.md` (this file) — when developer conventions change

The full matrix and writer's contract is in [docs/internal/doc-guiding-principles.md](docs/internal/doc-guiding-principles.md). Audit trail of doc decisions: [docs/internal/doc-audit.md](docs/internal/doc-audit.md). Target tree: [docs/internal/doc-ia.md](docs/internal/doc-ia.md).

## Testing discipline

Agents do **not** run the full test suite. Tests run only at GATE steps the engine emits. The full suite is gated to maintainers + CI. Unit tests for the file you're editing are fine.
