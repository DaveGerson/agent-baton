# Manager-Mode PMO Layer — Validated Design

**Date:** 2026-07-02
**Status:** Approved by director (brainstorm session)
**Implements:** `docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md` (all 9 milestones)
**Branch strategy:** one integration branch, one commit per milestone, single PR at end.

## Locked decisions (director-approved)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Delivery granularity | One branch, milestone commits, single PR |
| 2 | Knowledge manifest | Extend existing `knowledge.yaml` — **no** `pack.yaml`. Fix `pack.yaml` doc-drift + latent bead-promotion bug in passing |
| 3 | Charter/scope builders | Deterministic heuristics + optional LLM enrichment via `BATON_MANAGER_ENRICH=off|haiku|sonnet|opus` (default `off`), following the `BATON_PLAN_REVIEW` pattern. Tests exercise only the deterministic path |
| 4 | Pipeline integration | Post-processor (`ManagerModePlanner`) around `IntelligentPlanner.create_plan()` output. No pipeline-stage changes this increment |
| 5 | Subagent staffing | Sonnet subagents for TDD + implementation; Fable review gate at end of each wave; Opus escalation only if a Sonnet agent stalls |

## Reconnaissance facts the design depends on

- Planning pipeline: 7 stages assembled in `agent_baton/core/engine/planning/planner.py::_build_default_pipeline()`; entry point `IntelligentPlanner.create_plan()` (planner.py:267).
- `MachinePlan`/`PlanStep` (Pydantic v2, `agent_baton/models/execution.py`) use `extra="ignore"` + hand-written `to_dict` allow-lists → **undeclared fields do not survive round-trips**. Sidecar-by-convention is mandatory; only explicitly declared fields persist.
- Execution dir convention exists: `.claude/team-context/executions/<task_id>/` via `ContextManager` (`core/orchestration/context.py`, `_EXECUTIONS_DIR`).
- Config: `agent_baton/core/config/` package (no `config.py` module). `ProjectConfig` (plain dataclass, `project_config.py`) loads `baton.yaml` walking **up** from cwd; does **not** check `.claude/baton.yaml`.
- Knowledge: registry (`core/orchestration/knowledge_registry.py`) reads `knowledge.yaml` manifests + `*.md` frontmatter docs from `~/.claude/knowledge/` and `<root>/.claude/knowledge/`. Resolver (`core/engine/knowledge_resolver.py`) already does per-step/per-role attachment with budgets (32k step budget, 8k doc cap, priority ordering, session dedup). Ranker (`core/intel/knowledge_ranker.py`) re-orders by effectiveness. `baton knowledge` group has `doctor/search/resolve/brief/effectiveness/harvest/stale/deprecate/retire/sweep/usage/ranking/ab` — **no** `list/scan/show/audit`.
- `pack.yaml` appears only in a stale docstring (`models/knowledge.py:76`) and a dead-write in `cli/commands/bead_cmd.py:884` (appends to a file the registry never reads).
- Assurance Packs (`core/govern/packs.py`, `.claude/packs/`, `pack.json`) are a **separate governance subsystem** — not touched by this work.
- Dispatch prompts: `PromptDispatcher.build_delegation_prompt()` (`core/engine/dispatcher.py:371`), stateless, linear `parts` builder. Extension pattern = new pre-built-string kwarg (precedent: `phase_summaries_section`, `prior_context_block`), assembled engine-side in `ExecutionEngine._dispatch_action` (~executor.py:6600-6699).
- `_print_action()` (`cli/commands/execution/execute.py:558`) is a frozen public protocol — labels/delimiters must not change. Prompt **body** is opaque to the protocol; adding sections is safe.
- Phase-completion funnel: `ExecutionEngine._synthesize_beads_post_phase()` (executor.py:960) runs once before every `PhaseManager.advance_phase` call. This is the hook for phase handoffs + report refresh.
- Decisions: `core/runtime/decisions.py::DecisionManager` writes JSON+md to `.claude/team-context/decisions/` with EventBus events; CLI `baton execute decide`. Reuse, don't rebuild.
- Report template: `core/observe/retrospective.py::RetrospectiveEngine.generate()/save()` — the shape `ManagerReportBuilder` mirrors.
- Handoffs: `core/intel/handoff_synthesizer.py` (≤400-char dispatch blocks, `handoff_beads` table) and `core/intel/phase_summary.py` — reuse for phase-handoff artifacts.
- Signals: `GATE_ADDITION:` line-parser (`core/engine/gate_addition.py`) is the precedent for `SCOPE_EXPANSION:` parsing. Knowledge-gap signal pipeline already exists (`core/engine/knowledge_gap.py`).
- CLI auto-discovery: `cli/main.py::discover_commands` walks command modules; add new groups to `_COMMAND_GROUPS`.
- `models/pmo.py` + `core/pmo/` exist but are the **portfolio Kanban overlay** — unrelated; new package is `core/manager/`.

## Architecture

Post-processor composition. The 7-stage planner is untouched; `ManagerModePlanner` runs after `create_plan()`:

```
create_plan() ──► MachinePlan
                      │
         ManagerModePlanner.build(plan, task_summary, project_root, config)
                      │
   charter ► scope map ► team blueprint ► role cards ► knowledge plan
           ► scope contracts + context bundles (per nontrivial step)
                      │
         PhasePolicyApplier.apply(plan, artifacts, config)   ← only plan mutation
                      │                                         (injects review steps)
         sidecar writer ► executions/<task_id>/…
                      │
         manager-brief.md
```

### New package: `agent_baton/core/manager/`

| Module | Contents |
|---|---|
| `charter.py` | `ProjectCharterBuilder` — deterministic from task summary, classifier output, detected stack, repo signals; optional LLM enrichment |
| `scope.py` | `ScopeMapBuilder` — workstreams from plan phases + charter; likely/allowed paths from repo signals or recorded assumptions |
| `team_blueprint.py` | `TeamBlueprintBuilder` — roles from plan agents + scope map; workstream owner assignment; config review roles injected |
| `role_cards.py` | Role-card Markdown writer (template per spec §14.2) |
| `context_bundles.py` | `ScopeContract` + `ContextBundle` builders and writers; token-budget estimation + deterministic overflow (keep contract → required packs → latest handoff; drop lowest-ranked references; emit truncation warning) |
| `knowledge_plan.py` | `KnowledgePlanBuilder` — wraps existing `KnowledgeRegistry`/`KnowledgeResolver`; missing/stale pack detection per config |
| `phase_policy.py` | `PhasePolicyApplier` — `adversarial_review: always\|risk_based\|off`, `handoff_required`, gate policy mapping onto existing `gate_scope` |
| `reports.py` | `ManagerReportBuilder` (mirrors `RetrospectiveEngine`) — `manager-brief.md` post-planning, `manager-report.md` during/after execution |
| `decisions.py` | `DecisionPacketBuilder` — typed `ManagerDecision` wrapper over existing `DecisionManager`; also appends `decision-log.jsonl` |
| `planner.py` | `ManagerModePlanner` — orchestrates the above; single entry point for `plan_cmd` |
| `enrich.py` | `BATON_MANAGER_ENRICH` LLM hook (stub default) |

### Config: `agent_baton/core/config/manager.py`

Pydantic v2 models (`ManagerConfig`, `TeamConfig`, `ScopingConfig`, `ContextConfig`, `KnowledgePackConfig`, `PhasePolicyConfig`, `GateConfig`, `ReportingConfig`) matching the spec §9.1 YAML. Rules:

- Resolution: CLI flags > `.claude/baton.yaml` > `~/.baton/config.yaml` > built-in defaults.
- Loader extends the `ProjectConfig` walk: at each level check `<dir>/.claude/baton.yaml` first, then `<dir>/baton.yaml`. Manager keys and existing `ProjectConfig` keys coexist in one file; each loader reads its own keys.
- Unknown top-level keys → warn. Invalid nested enum values → fail with actionable error. Missing file → defaults. Non-manager workflows unaffected unless `--manager-mode` or `manager_mode.enabled_by_default: true`.

### Models: `agent_baton/models/manager.py`

Pydantic v2 (per `models/CLAUDE.md`), `to_dict`/`from_dict`, JSON round-trippable: `ProjectCharter`, `ScopeMap`, `Workstream`, `TeamBlueprint`, `RoleCard`, `ScopeContract`, `ContextBundle`, `ContextReference`, `KnowledgePlan`, `MissingKnowledgePack`, `ManagerDecision` (fields per spec §10).

### Plan model change (single, additive)

`MachinePlan.manager_mode: bool = False` — declared field so it survives serialization. All PMO artifacts resolved by convention:

```
.claude/team-context/executions/<task_id>/
  project-charter.md      scope-map.json        team-blueprint.json
  role-cards/<role>.md    knowledge-plan.json   manager-brief.md
  scope-contracts/<step_id>.md|.json
  context-bundles/<step_id>.json
  handoffs/phase-<n>-handoff.md
  decisions/<decision_id>.md   decision-log.jsonl
  manager-report.md
```

### Knowledge manifest extension (`knowledge.yaml`)

New optional keys: `status: active|draft|stale|deprecated` (default `active`), `confidence: low|medium|high`, `source_files: []`, `last_reviewed`, `stale_after_days`. Registry parses them; resolver respects `status` (deprecated → never auto-attach; stale → warn per config). New CLI verbs on the existing cooperative parser: `list`, `scan` (writes `knowledge-scan.json`), `show PACK`; `audit` extends `doctor` checks (stale, missing source files, missing metadata, unused).

### CLI surface

- `baton plan … --manager-mode` (new flag on existing command; `--dry-run` prints PMO artifact preview without writing).
- `baton config init --profile manager | validate | show` (new `config_cmd.py`).
- `baton report [--json] [--task-id]` (new).
- `baton team status|show [--task-id]` (new group; reads blueprint + execution state + mailbox).
- `baton knowledge list|scan|show|audit` (extend existing group).

### Execution integration (M9) — three hooks, zero protocol changes

1. **Dispatch:** `_dispatch_action` loads `scope-contracts/<step_id>.md` + `context-bundles/<step_id>.json` when `plan.manager_mode`; passes two new string kwargs to `build_delegation_prompt`, slotted between `## Intent` and `## Your Task`. Non-manager plans: kwargs absent, prompt byte-identical to today.
2. **Phase completion:** inside `_synthesize_beads_post_phase()` — write `handoffs/phase-<n>-handoff.md` (when `handoff_required`) and refresh `manager-report.md`. Best-effort, wrapped, like surrounding code. `PhaseManager` stays pure.
3. **Signals:** `SCOPE_EXPANSION: <path> — <reason>` line parsed on `record_step_result` (pattern: `gate_addition.py`); routed per `scoping.scope_expansion_policy` (`allow_with_note` → bead note; `queue_for_manager` → decision packet; `block` → step fails pending amend). Knowledge-gap signals reuse the existing pipeline, adding a decision packet when a gap blocks per policy.
4. **Completion:** `complete()` writes final `manager-report.md` alongside the existing retrospective.

## Testing

TDD per milestone (failing tests first). All deterministic — no live Claude; LLM enrichment tested only via stub. Layout: `tests/manager/` (config, charter, scope map, blueprint, bundles, knowledge packs, phase policy, reports, decision packets), `tests/engine/test_manager_context_prompt.py`, `tests/cli/` (knowledge verbs, report), `tests/fixtures/medium_project_repo/`, `tests/e2e/test_manager_mode_planning.py` + `test_manager_mode_execution_dry_run.py`. Required cases enumerated in spec §16 milestones 1–9.

## Execution plan (subagent waves)

| Wave | Scope | Staffing |
|---|---|---|
| 0 | `ManagerConfig` + loader; `models/manager.py`; `--manager-mode` flag; `ManagerModePlanner` skeleton + sidecar writer (contracts everything imports) — M1 | 1 Sonnet |
| 1 ∥ | M2 charter/scope · M3 blueprint/role-cards · M5 knowledge plan + CLI verbs | 3 Sonnet, worktrees |
| 2 ∥ | M4 bundles + dispatcher injection · M6 phase policy · M7 reports/decisions/CLI | 3 Sonnet, worktrees |
| 3 | Glue: full post-processor wiring, config CLI, M8 fixture repo + planning E2E | Sonnet |
| 4 | M9 executor consumption + execution E2E; docs matrix; final review | Sonnet + Fable review |

Fable review gate at the end of every wave before the milestone commit. Docs updated per root `CLAUDE.md` matrix before the final PR: `cli-reference.md`, `architecture/package-layout.md`, `design-decisions.md`, `CLAUDE.md` env-var table (`BATON_MANAGER_ENRICH`), `GEMINI.md`.

## Non-goals (unchanged from spec §5)

No pipeline-stage rewrite, no `_print_action()` changes, no Assurance-Pack unification, no remote execution, no PMO UI expansion, no multi-repo portfolio work.
