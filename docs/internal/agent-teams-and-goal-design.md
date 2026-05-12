# Agent Teams + `/goal` integration — design

**Status:** Draft
**Branch:** `claude/add-teamcreate-goal-support-YDQZ8`
**Drives changes in:** `agent_baton/core/engine/`, `agent_baton/core/orchestration/`, `agent_baton/models/`, `agent_baton/cli/commands/`, `agent_baton/api/`, `pmo-ui/`, `agents/`

## Context

Two Claude Code capabilities motivate this work:

1. **Agent Teams** (experimental, v2.1.32+, gated on `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) — team-lead + teammates with a shared task list and inter-teammate mailbox. Hooks: `TeammateIdle`, `TaskCreated`, `TaskCompleted`. Known limits: no in-process resume, one team at a time, no nested teams, permissions fixed at spawn, `skills` / `mcpServers` subagent frontmatter not honored when used as a teammate.
2. **`/goal`** (v2.1.139, May 2026) — set a completion condition; the loop continues across turns until met. Overlay surfaces elapsed time, turn count, token usage.

Both push toward longer-running, more autonomous work. baton already has the substrate for parallel execution (`TEAM_DISPATCH` is wired end-to-end; `SWARM_DISPATCH` is enum-only and unwired) and for plan amendment (`ExecutionDriver.amend_plan`). It does not have a plan-level completion condition or a mailbox.

## Decisions

### D1 — Agent Teams: option A3 (mailbox unconditional + Claude-Teams backend behind a flag)

- **A2 work (unconditional):** introduce a `Mailbox` artifact at `.claude/team-context/mailbox/` and adopt Agent Teams' hook semantics (`TaskCreated`, `TaskCompleted`, `TeammateIdle`) on top of the existing `TEAM_DISPATCH` path. Adopt **plan-approval-by-lead** as an internal governance step (today APPROVAL is human-gated; team-internal plan approval is lead-gated and orchestrator-driven). Precedent: gastown's mailbox model.
- **A1 work (opt-in behind `BATON_TEAMS_BACKEND`):** add a `TeamBackend` protocol in `core/engine/dispatcher.py` with two implementations — `worktree` (default; current behavior) and `claude-teams` (spawns a real Claude Code Agent Team via NL prompt to the lead, maps baton `team_member.id ↔ teammate name`, routes hook callbacks back into `executor.record_team_member_result`).
- **Permissions** must be hoisted to the lead at spawn time when `claude-teams` backend is selected; document the override.
- **Resumption** is unavailable for in-flight Claude-Teams teams. The planner must mark Claude-Teams team phases as non-resumable and refuse to place them late in long-running plans (budget tier ≥ `long-running`).

### D2 — `/goal`: option G1 with wrap-and-refine

`baton goal "<condition>"` wraps `baton plan` and uses a `GoalEvaluator` to round out gaps the initial plan misses, via `amend_plan`.

Loop:

```
baton goal "<condition>" [--max-amend-cycles N]
  → planner.plan(task=<condition>, completion_condition=<condition>, --save --explain)
  → baton execute start
  → loop:
      drive action loop (DISPATCH / GATE / APPROVAL / ...)
      at end-of-phase:
        evaluator(state, last_gate_output, completion_condition)
          → {met} ........ emit COMPLETE
          → {not_met, gaps[]}
                if amend_cycles < N:
                  amend_plan(new_phases=derive_from(gaps))
                  amend_cycles += 1
                  continue
                else:
                  emit FAILED (reason="goal not met, amend budget exhausted")
  → terminate on: met | failed | BATON_RUN_TOKEN_CEILING | amend budget exhausted
```

**No new `ActionType`.** The evaluator runs internally inside the engine; `_print_action()` shape is unchanged. The orchestrator agent definition does not need a new branch. (G2 — first-class `GOAL` ActionType — is deferred until regulated-domain auditor work demands discrete, observable goal-check events.)

**Evaluator inputs:** current `ExecutionState`, last gate result, beads warnings, accumulated step outcomes, the original `completion_condition` text. **Evaluator output:** a structured `GoalCheck` Pydantic model with `{met: bool, missing: list[str], suggested_phases: list[PlanPhase]}`. The "suggested_phases" become amendment input.

**Safety rails:**
- `BATON_RUN_TOKEN_CEILING` already bounds runaway cost; no new mechanism required.
- New `--max-amend-cycles` flag (default `3`) bounds the round-out loop independently of token budget.
- Evaluator MUST agree with the most recent successful GATE before declaring `met` (prevents premature claims).

## File-level change plan

### Phase 1 — A2 + G1 (low risk, independent slices)

| Slice | Files | Purpose |
|-------|-------|---------|
| **G1.a** Plan-level goal field | `agent_baton/models/execution.py` (add `MachinePlan.completion_condition: str \| None`, `MachinePlan.max_amend_cycles: int = 3`) | Surface for the goal text + amend budget on plans. |
| **G1.b** Planner accepts `--goal` | `agent_baton/core/orchestration/planner.py` (or wherever `plan()` lives), `agent_baton/cli/commands/plan.py` | Persist `completion_condition` and amend budget into the saved plan. |
| **G1.c** Goal evaluator | `agent_baton/core/engine/goal_evaluator.py` (new); `agent_baton/models/execution.py` (add `GoalCheck` model) | LLM-backed check at end-of-phase; reads ExecutionState + last gate output; returns structured result. Must require gate agreement to declare `met`. |
| **G1.d** Engine wiring | `agent_baton/core/engine/executor.py` (call evaluator at phase boundary; invoke `amend_plan` on `not_met` with budget remaining) | The wrap-and-refine loop. |
| **G1.e** CLI surface | `agent_baton/cli/commands/goal.py` (new) — `baton goal "<condition>"` thin wrapper that calls plan → execute start with the goal field set | One-shot UX. |
| **G1.f** PMO UI overlay | `pmo-ui/src/components/ExecutionProgress.tsx` (extend); `agent_baton/api/routes/pmo.py` (extend `/pmo/cards/{card_id}/execution` response to include `turn_count`, `tokens_used_usd`, `goal_status`, `amend_cycles_used`) | Surface the elapsed/turns/tokens overlay and goal status banner. |
| **A2.a** Mailbox artifact | `agent_baton/core/engine/mailbox.py` (new); store JSONL at `.claude/team-context/mailbox/{team-id}.jsonl` | Inter-teammate messaging substrate. |
| **A2.b** Hook semantics on existing TEAM_DISPATCH | `agent_baton/core/engine/executor.py` (`record_team_member_result`); `agent_baton/core/engine/dispatcher.py` (emit `TaskCreated`/`TaskCompleted` events to mailbox) | Apply Agent Teams' coordination model to the existing dispatcher. |
| **A2.c** Lead plan-approval governance step | `agent_baton/core/govern/` (or wherever APPROVAL is gated) | New internal approval kind: `team_member_plan` — lead reviews member's plan before implementation. Orchestrator-driven, not human-gated. |

### Phase 2 — A1 (opt-in)

| Slice | Files | Purpose |
|-------|-------|---------|
| **A1.a** `TeamBackend` protocol | `agent_baton/core/engine/dispatcher.py` (add `TeamBackend` Protocol; default impl = current worktree logic, new impl = `ClaudeTeamsBackend`) | Pluggable backend. |
| **A1.b** Backend selector | `BATON_TEAMS_BACKEND` env var, plumbed through `core/engine/config.py` (or equivalent) | Opt-in switch. |
| **A1.c** Hook bridge | New CLI: `baton execute record-team-member-result --hook-source claude-teams` invoked from a `TaskCompleted` shell hook | Connect Claude Code hook lifecycle → baton executor. |
| **A1.d** Planner awareness of resumability constraint | Planner: when `BATON_TEAMS_BACKEND=claude-teams` and budget tier ≥ `long-running`, emit a warning by default and refuse the plan when `BATON_TEAMS_STRICT_RESUMABILITY=1`. The two-mode split keeps the default planner usable while still satisfying the design's "refuse" intent under an opt-in flag. | Prevents footguns. |
| **A1.e** Skills/MCP gap audit | `agents/CLAUDE.md` (document the limitation); audit each agent in `agents/` and tag those whose `skills`/`mcpServers` frontmatter is load-bearing | Avoid silently broken teammates. |

### Phase 3 — conditional (G2)

Deferred. Only revisit if regulated-domain work needs auditor agent intervention at discrete goal-check points.

## Cross-cutting cleanups

- **~~Delete~~ Audit `SWARM_DISPATCH`.** The initial recon (this design's source) reported `SWARM_DISPATCH` as an unwired stub. Closer inspection during implementation showed a real subsystem: `core/swarm/` (dispatcher, reconciler, signoff), `cli/commands/swarm_cmd.py`, gated by `BATON_SWARM_ENABLED`, with multiple test files (`tests/test_swarm_*.py`, `tests/test_archetype_execution_models.py`) asserting the ActionType enum value. The resolver does not currently *emit* `SWARM_DISPATCH` from the standard execute path, but the value is consumed by the swarm CLI and its tests. **Action: leave the enum in place; do NOT delete.** A separate retrospective should decide whether to formally promote the swarm subsystem to GA or sunset it; that decision belongs with the swarm subsystem owners, not in scope for the Agent Teams + `/goal` work.
- **`docs/invariants.md`** — write the `_print_action()` shape as a contract entry. Required before G2 is ever reconsidered.
- **Agent frontmatter audit** for skills/mcpServers dependencies (see A1.e).

## Risks and open questions

1. **Goal evaluator cost.** One extra LLM call per phase boundary. Bounded by `--max-amend-cycles` and `BATON_RUN_TOKEN_CEILING`, but real. Mitigation: cache the evaluator prompt; consider a small classifier model.
2. **Evaluator hallucination.** "Goal met" when it isn't. Mitigation: hard requirement that the most recent gate passed; reject `met=True` when the last gate failed.
3. **Mailbox unbounded growth.** JSONL with retention policy: trim after team cleanup. Open: should mailbox persist past team teardown for audit purposes? (Likely yes, in regulated mode.)
4. **Claude Teams resumption gap.** Real and unfixable on baton's side. Planner constraint is the mitigation; document loudly.
5. **Plan-approval-by-lead vs. human APPROVAL.** Naming collision risk. Use `team_member_plan_approval` (internal) vs `APPROVAL` (human) to keep them distinct in traces.
6. **`amend_plan` quality.** The wrap-and-refine loop only works if the evaluator's `suggested_phases` are good. Validate against existing `BATON_PLANNER_HARD_GATE` if enabled.

## Doc updates required at merge

- `docs/design-decisions.md` — entry for A3 + G1-with-wrap once `Accepted`.
- `docs/cli-reference.md` — `baton goal` command.
- `docs/api-reference.md` — extended `/pmo/cards/{card_id}/execution` shape.
- `docs/architecture/state-machine.md` + `docs/engine-and-runtime.md` — the evaluator-at-phase-boundary hook.
- `docs/architecture.md` — Agent Teams backend abstraction.
- `agents/CLAUDE.md` — skills/MCP-as-teammate limitation.
- `CLAUDE.md` (root) — `BATON_TEAMS_BACKEND` env var row in the table; mention `baton goal` in the orchestrator behavior section if it becomes the recommended entry point for goal-driven work.
- `docs/internal/doc-audit.md` — record this design's acceptance.

## Sequencing

1. G1.a + G1.b + G1.c + G1.d as a vertical slice → tests against a minimal goal scenario.
2. G1.e (CLI) + G1.f (PMO UI overlay).
3. A2.a + A2.b in parallel with G1 work (independent code paths).
4. A2.c (lead plan-approval) once mailbox is stable.
5. SWARM_DISPATCH cleanup at any point.
6. A1.* once Phase 1 is shipping and stable.

First concrete commit on this branch: this design doc.
