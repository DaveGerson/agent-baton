# CLAUDE.md — agent runtime config

This file is read by every Claude Code agent dispatched inside this repo. It is **not** documentation for humans. Marketing pitch, install steps, comparison tables: not here. Those live in `README.md`. Pages for human readers live in `docs/`.

This root file holds **cross-cutting rules** that apply everywhere. Each major
directory has its own `CLAUDE.md` with component-specific guidance — Claude Code
auto-loads the closest one based on the working directory. Drill into the
component file when you're touching that area; come back here for engine
protocol, isolation, and incident-handling rules.

For the design rationale and the decision rule for adding new `CLAUDE.md`
files, see [docs/internal/claude-md-architecture.md](docs/internal/claude-md-architecture.md).

## Guiding principles for implementation

Apply these to every change before declaring it done:

1. Does this improve the quality of code within agent-baton?
2. Does this accelerate code generation and maintainability?
3. Does this make appropriate trade-offs between token usage and output quality?
4. Does this actually solve the problem with no holes or gaps?
5. Could this functionality be failing silently?
6. Are there any major code smells?
7. Does a user or a Claude Code deployment have the information needed to use this capability?
8. Is this capability extensible to multiple challenges?

## Reference architecture (where to look)

| Path | What lives there | Per-directory CLAUDE.md |
|------|------------------|-------------------------|
| `agent_baton/` | Python orchestration engine (the `baton` CLI's source) | [agent_baton/CLAUDE.md](agent_baton/CLAUDE.md) |
| `agent_baton/api/` | FastAPI routers, middleware, webhooks (REST + PMO backend) | [agent_baton/api/CLAUDE.md](agent_baton/api/CLAUDE.md) |
| `agent_baton/cli/` | Click/Typer CLI surface — `baton <command>` entry points | [agent_baton/cli/CLAUDE.md](agent_baton/cli/CLAUDE.md) |
| `agent_baton/core/` | Engine internals: state machine, planner, dispatcher, governance, storage | [agent_baton/core/CLAUDE.md](agent_baton/core/CLAUDE.md) (deeper: [engine](agent_baton/core/engine/CLAUDE.md), [orchestration](agent_baton/core/orchestration/CLAUDE.md), [govern](agent_baton/core/govern/CLAUDE.md), [storage](agent_baton/core/storage/CLAUDE.md)) |
| `agent_baton/models/` | Pydantic data models — execution, beads, plans, decisions | [agent_baton/models/CLAUDE.md](agent_baton/models/CLAUDE.md) |
| `agents/` | 33 distributable agent definitions (Markdown with frontmatter) | [agents/CLAUDE.md](agents/CLAUDE.md) |
| `references/` | 18 distributable reference procedures | [references/CLAUDE.md](references/CLAUDE.md) |
| `templates/` | `CLAUDE.md` + `settings.json` + skills installed to user projects | (do not modify `templates/CLAUDE.md` — it's a distributable artifact) |
| `pmo-ui/` | React/Vite frontend served at `/pmo/` | [pmo-ui/CLAUDE.md](pmo-ui/CLAUDE.md) |
| `tests/` | pytest suite (unit + integration) | [tests/CLAUDE.md](tests/CLAUDE.md) |
| `docs/` | Public docs (Diátaxis quadrants); `docs/internal/` is maintainer-only | [docs/CLAUDE.md](docs/CLAUDE.md) (deeper: [internal](docs/internal/CLAUDE.md)) |
| `scripts/` | Install scripts and one-shot maintenance utilities | [scripts/CLAUDE.md](scripts/CLAUDE.md) |
| `.claude/` | Project-specific orchestration setup (not committed) | — |

All Python imports use canonical paths: `from agent_baton.core.govern.classifier import DataClassifier`. Never reach across submodules through `__init__.py` shortcuts.

## Key files (treat as public APIs)

- `agent_baton/cli/commands/execution/execute.py` — `_print_action()` is the protocol surface between the engine and the orchestrator agent. Don't break its output shape.
- `agent_baton/core/engine/states.py` — execution state machine.
- `agent_baton/core/engine/protocols.py` — `ExecutionDriver` 15-method interface.
- `agent_baton/models/execution.py` — `ActionType` enum (9 values: DISPATCH, GATE, APPROVAL, COMPLETE, FAILED, WAIT, FEEDBACK, INTERACT, SWARM_DISPATCH) and `ExecutionState`.
- `agent_baton/api/` — FastAPI routers for the REST API + PMO UI backend.

## Orchestrator behavior (mandatory)

When the `orchestrator` agent is invoked:

1. Plan with `baton plan "<task>" --save --explain`. The engine handles routing, risk, budget, sequencing. Writes `plan.json` + `plan.md` to `.claude/team-context/`.
2. Start with `baton execute start`. The engine initializes tracing and returns the first action.
3. Drive the action loop. The engine returns DISPATCH (spawn the named agent with the provided prompt), GATE (run the named check), APPROVAL (wait for sign-off), or INTERACT (multi-turn dialogue). Record results with `baton execute record` and `baton execute gate`.
4. Finalize with `baton execute complete`. The engine writes the trace, usage log, and retrospective.
5. Use a feature branch; commit each agent's work individually.
6. If the session crashes, `baton execute resume` picks up from saved state.

Recipes for common tasks: [docs/orchestrator-usage.md](docs/orchestrator-usage.md). Protocol contract from the agent side: [references/baton-engine.md](references/baton-engine.md).

## Concurrent agent isolation (mandatory)

When dispatching parallel `Agent` calls that touch tracked files, use `isolation: "worktree"`. Branch checkout alone does not isolate uncommitted changes — the parent HEAD silently drifts during multi-agent dispatch.

## Autonomous incident handling (mandatory)

Handle bugs/failures without pausing the main flow:

1. **Bead it** — `baton beads create --type warning --message "<incident>"`.
2. **Fix in parallel** — launch a subagent on a separate branch (use `isolation: "worktree"` for concurrent agents).
3. **Require a regression test.**
4. **Continue the main flow.**

Only pause for true human decisions (security, compliance, scope changes).

## Regulated-domain rules

Any work touching regulated data, compliance systems, audit-controlled records, or industry-specific business rules MUST:

- Involve the `subject-matter-expert` agent for domain context.
- Involve the `auditor` agent for pre-execution and post-execution review.
- Follow the Regulated Data guardrail preset.

## Testing discipline

Agents do **not** run the full test suite. Tests run only at GATE steps the engine emits. The full suite is gated to maintainers + CI. Unit tests for the file you're editing are fine. See [tests/CLAUDE.md](tests/CLAUDE.md) for layout.

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

## Documentation maintenance (mandatory)

After significant work, update the relevant docs before merging:

- `docs/architecture.md` + `docs/architecture/{high-level-design,technical-design,package-layout,state-machine}.md` — design and structure
- `docs/cli-reference.md` — when CLI surface changes
- `docs/api-reference.md` — when REST routes change
- `docs/agent-roster.md` — when agents are added or removed
- `README.md` — when public framing changes
- `CLAUDE.md` (this file) — when cross-cutting developer conventions change
- The component-level `CLAUDE.md` — when conventions change inside a single directory
- `GEMINI.md` — when developer conventions change

The full matrix and writer's contract is in [docs/internal/doc-guiding-principles.md](docs/internal/doc-guiding-principles.md). Audit trail of doc decisions: [docs/internal/doc-audit.md](docs/internal/doc-audit.md). Target tree: [docs/internal/doc-ia.md](docs/internal/doc-ia.md).
