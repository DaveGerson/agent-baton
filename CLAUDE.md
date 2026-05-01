# CLAUDE.md — agent runtime config

This file is read by every Claude Code agent dispatched inside this repo. It is **not** documentation for humans. Marketing pitch, install steps, comparison tables: not here. Those live in `README.md`. Pages for human readers live in `docs/`.

Can you ## GUIDING PRINCIPLES FOR IMPLEMENTATION
1) Does this part of the system improve the quality of code within agent-baton                                                                                                                                                                                                                               
2) Does this part of the system accelerate code generation and maintainability                                                                                                                                                                                                                               
3) Does this system make appropriate trade-offs when it comes to token usage and output quality                                                                                                                                                                                                              
4) Does this capability actually solve the problem it is intended too without having any holes or gaps in implementation                                                                                                                                                                                     
5) Could this functionality be failing silently                                                                                                                                                                                                                                                              
6) Are there any major code smells                                                                                                                                                                                                                                                                           
7) Does a user or claude-code deployment on the user side have the information needed to use this capability in the system if they need it?                                                                                                                                                                  
8) is this capability extensible to multiple challenges.                                                                                                   

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
| `BATON_APPROVAL_MODE` | `local` (self-approve) or `team` (different reviewer required) | `local` |
| `BATON_DB_PATH` | Override per-project `baton.db` location | discovered |
| `BATON_RUN_TOKEN_CEILING` | Hard kill the loop above N tokens | unset |
| `BATON_EXPERIMENTAL` | Comma list to enable experimental subsystems (e.g., `swarm`) | unset |
| `ANTHROPIC_API_KEY` | Required for AI risk classification and Haiku classifier | unset |
| `BATON_WORKTREE_ENABLED` | Enable/disable git worktree isolation for concurrent agents | `1` |
| `BATON_TAKEOVER_ENABLED` | Enable/disable developer takeover capability | `1` |
| `BATON_SELFHEAL_ENABLED` | Enable/disable self-heal escalation on failures | `0` |
| `BATON_SPECULATE_ENABLED` | Enable/disable speculative pipelining | `0` |
| `BATON_SOULS_ENABLED` | Enable/disable soul registry for agent identity | `0` |
| `BATON_PLANNER_HARD_GATE` | Enable hard validation gate that blocks bad plans | unset |
| `BATON_PREDICT_ENABLED` | Enable predictive dispatch system | `0` |
| `BATON_OTEL_ENABLED` | Enable OpenTelemetry JSONL export | unset |

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
- `GEMINI.md` — when developer conventions change

The full matrix and writer's contract is in [docs/internal/doc-guiding-principles.md](docs/internal/doc-guiding-principles.md). Audit trail of doc decisions: [docs/internal/doc-audit.md](docs/internal/doc-audit.md). Target tree: [docs/internal/doc-ia.md](docs/internal/doc-ia.md).

## Testing discipline

Agents do **not** run the full test suite. Tests run only at GATE steps the engine emits. The full suite is gated to maintainers + CI. Unit tests for the file you're editing are fine.
