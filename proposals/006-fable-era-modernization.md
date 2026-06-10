# Proposal 006: Fable-Era Modernization — Model Catalog, 4-Tier Routing, and New API Capability Adoption

**Status**: Draft
**Author**: Codebase review (4 parallel review agents: model-surface inventory, architecture, distributables, docs/tests)
**Date**: 2026-06-10
**Risk**: MEDIUM — Phase 0/1 are additive; Phase 2 touches a persisted enum + golden states
**Estimated Scope**: ~800 LOC new (catalog + routing), ~600 LOC modified across ~30 files, plus docs/templates

---

## Problem Statement

Agent Baton was built against the pre-Mythos/Fable model lineup: a 3-tier
haiku → sonnet → opus world where Opus 4.7 was the most capable model.
The post-launch lineup invalidates several embedded assumptions:

| Fact (current) | Baton's assumption (stale) |
|---|---|
| Claude Fable 5 (`claude-fable-5`) is a **new tier above Opus** — $10/$50 per MTok, 1M context, best-in-class long-horizon agentic execution | Tiering tops out at Opus everywhere: self-heal ladder, API/CLI enums, agent frontmatter docs, pmo-ui enums |
| Opus 4.8 (`claude-opus-4-8`) is current Opus at **$5/$25 per MTok** | `cost_forecaster.py` prices Opus at $15/$75; `goal_evaluator.py` pins `claude-opus-4-7` |
| Haiku 4.5 is **$1/$5 per MTok** | `govern/budget.py`, `swarm/dispatcher.py`, and `swarm_cmd.py` use retired Haiku 3.5 pricing ($0.25/$1.25) |
| Adaptive thinking replaced fixed thinking budgets; `output_config.effort` (low→max) and task budgets (beta) are the spend levers | `templates/settings.json` distributes `CLAUDE_CODE_MAX_THINKING_TOKENS: "10000"` to every user project; engine has no effort/task-budget concept |
| Fable/Opus 4.8 under-reach for subagents unless trigger conditions are explicit, narrate more, and ask more unless granted autonomy | Distributed prompts carry pre-Fable tuning ("When in doubt, err toward the auditor"; "slightly pushy" trigger guidance; no autonomy/silence defaults) |

The most consequential finding is not any single stale string but the
**absence of a model catalog**: model IDs, tier ordering, prices, and
capability assumptions are scattered across 15+ locations in 4 mutually
inconsistent pricing tables. Haiku input is quoted as both $0.25 and
$1.00 in the same codebase today. Any Fable-tier step would silently
misprice as Sonnet (a 1.6–8x undercount), including in the
`BATON_RUN_TOKEN_CEILING` enforcement path — undercounting is the
dangerous direction, since it lets runaway spend through the ceiling.

## Evidence Inventory (where the assumptions live)

### Pricing tables (4, mutually inconsistent)

| Location | Haiku in/out | Sonnet in/out | Opus in/out | Status |
|---|---|---|---|---|
| `agent_baton/core/engine/cost_estimator.py:38` `MODEL_PRICING` | blended 1.25 | blended 6.0 | blended 30.0 | no Fable; blended format |
| `agent_baton/core/govern/budget.py:85-105` | **0.25 / 1.25** | 3.0 / 15.0 | 15.0 / 75.0 | Haiku 3.5-era; Opus 4.1-era |
| `agent_baton/core/observe/cost_forecaster.py:39` | 1.0 / 5.0 ✓ | 3.0 / 15.0 ✓ | **15.0 / 75.0** | Opus stale (current: 5/25) |
| `agent_baton/core/swarm/dispatcher.py:52-53` + `agent_baton/cli/commands/swarm_cmd.py:94-104` | **0.25 / 1.25** | — | 15/75 | `baton swarm` estimates 3–5x wrong |

Correct card: Fable 5 $10/$50 · Opus 4.8 $5/$25 · Sonnet 4.6 $3/$15 · Haiku 4.5 $1/$5.
`chargeback.py:44` is the only consumer that already imports a shared table.

### Hardcoded model knowledge (selected; full inventory in review)

- `core/engine/goal_evaluator.py:345-347` — `"opus": "claude-opus-4-7"` (previous-gen); no `fable` strategy
- `core/engine/classifier.py:395` — `_HAIKU_MODEL = "claude-haiku-4-5-20251001"` (current, but pinned locally)
- `core/engine/selfheal.py:53-109` — `EscalationTier` ends at `OPUS`; tier `.value` strings persisted in `ExecutionState.selfheal_attempts` + SQLite; matched as literals in `dispatcher.py:1147-1232`
- `core/predict/{classifier,speculator,accept}.py`, `core/swarm/dispatcher.py` — `claude-haiku`/`claude-sonnet` literals
- `core/engine/planning/stages/decomposition.py:323-455`, `planner.py:372,457`, `research.py:126,189` — hardcoded `"opus"`/`"sonnet"` per planning stage; the classifier's complexity/risk output **never routes the model**
- `models/agent.py:42` — agent default `model: "sonnet"`; `runtime/claude_launcher.py:123-127` — 3-tier timeout map
- API/CLI tier enums: `agent_baton/api/models/requests.py:338` (`Literal["opus","sonnet","haiku"]`), `cli/commands/execution/execute.py:459-460` (`choices=["haiku","sonnet","opus"]`)
- pmo-ui: `src/components/BohWalkIn.tsx:115` (`type ModelTier`), `PlanEditor.tsx:33`, `BohLockerRoom.tsx:35-37`, `BohRulebook.tsx:45`

### Direct API call sites (only 2 — both conservative, neither will 400)

- `goal_evaluator.py:260-265` and `classifier.py:441-451` pass only
  `model`, `max_tokens`, `messages`. No deprecated params (`temperature`,
  `budget_tokens`, `output_format`) anywhere in the engine — we are
  forward-compatible but leave the new levers (`effort`, structured
  outputs) unused, and both hand-parse JSON with bespoke fence-stripping.

### Distributables

- `templates/settings.json:68-69` — `CLAUDE_CODE_MAX_THINKING_TOKENS: "10000"` (removed mechanism on Fable/4.8/4.7; adaptive thinking only)
- `references/guardrail-presets.md:29` — "When in doubt, err toward the auditor" (overtriggers on literal-following models; contradicts `references/cost-budget.md:121-126`)
- `references/cost-budget.md:10-14` — 3-tier cost model, no Fable row; `references/failure-handling.md:72` — "upgrade to Opus" as ceiling
- `agents/*.md` frontmatter is clean (tier aliases only, no hardcoded IDs); `agents/CLAUDE.md:14` documents the enum as `opus | sonnet | haiku`
- `agents/talent-builder.md:159` — "slightly pushy about triggers" guidance (wrong tuning for 4.8/Fable)

### Tests (classification for the implementing PR)

- **Pricing-pinned (move in lockstep with tables)**: `tests/engine/test_cost_estimator.py` (exact `MODEL_PRICING` math), `tests/test_swarm_signoff.py:583-587`, `tests/test_swarm_dispatcher.py:363,412`, `tests/test_wave5_integration.py:735,745,797` (also asserts "Opus is terminal self-heal tier"), `tests/test_wave5_human_agent_loop.py:625`, `tests/test_immune_daemon.py:242`, `tests/test_run_token_ceiling.py:152,351`
- **Golden snapshots (REGENERATE, never hand-edit)**: `tests/models/golden_states/{ExecutionState,StepResult}.json` via `python tests/models/_generate_golden.py` (edit IDs at `_generate_golden.py:328,717` first)
- **Generic fixtures (opaque strings, optional hygiene swap)**: swarm v2/experimental/e2e tests, `test_predict_accept.py`, `tests/govern/test_aibom.py`, `tests/release/test_mrp.py`, `test_jsonl_scanner.py`, pmo-ui mocks. Exception: `tests/engine/test_strategies.py:513,529` uses retired `claude-3-opus` — swap for hygiene. `test_cost_estimator.py`'s legacy IDs (`claude-3-5-sonnet`, `claude-haiku-3.5`) intentionally test the suffix-normalization rule — keep them, add current-ID cases alongside.

---

## Plan

### Phase 0 — Correctness fixes (do first; live bugs independent of Fable)

| # | Change | Files |
|---|---|---|
| 0.1 | Reconcile all four pricing tables to the current card (Haiku $1/$5, Sonnet $3/$15, Opus $5/$25) and update the lockstep tests listed above. Until Phase 1 lands, fix in place. | `cost_estimator.py`, `govern/budget.py`, `cost_forecaster.py`, `swarm/dispatcher.py`, `cli/commands/swarm_cmd.py` + pinned tests |
| 0.2 | Goal evaluator: repoint `"opus"` → `claude-opus-4-8`; drop the dated Haiku suffix in favor of alias `claude-haiku-4-5`. Update `CLAUDE.md:147`, `GEMINI.md`, `docs/cli-reference.md:128-131`. | `goal_evaluator.py:340-349` + docs |
| 0.3 | Remove `CLAUDE_CODE_MAX_THINKING_TOKENS` (and its comment key) from the distributed settings — it fights adaptive thinking on current models. | `templates/settings.json:68-69` |
| 0.4 | Replace "When in doubt, err toward the auditor" with a concrete trigger ("Invoke the auditor when the task touches regulated data, auth, payments, or irreversible operations"). | `references/guardrail-presets.md:29` |

### Phase 1 — Model catalog (the substrate everything else builds on)

Create `agent_baton/core/config/models.py`:

```python
class ModelTier(IntEnum):            # ordering is load-bearing for escalation
    HAIKU = 0; SONNET = 1; OPUS = 2; FABLE = 3

@dataclass(frozen=True)
class ModelSpec:
    id: str                  # "claude-fable-5"
    family: str              # "fable"
    tier: ModelTier
    price_in_per_mtok: float
    price_out_per_mtok: float
    context_window: int
    supports_effort: bool
    supports_task_budget: bool
    adaptive_thinking_only: bool

CATALOG: dict[str, ModelSpec]        # fable/opus/sonnet/haiku + dated aliases
def normalise(model: str) -> ModelSpec: ...
def tier_for(complexity: str, risk: str) -> ModelTier: ...   # Phase 2 routing hook
```

Migrate consumers incrementally (registry first, one table at a time,
existing tests as guards): `cost_estimator.py` (keep `MODEL_PRICING` as a
thin alias — it's referenced by name in 3 test files), `cost_forecaster.py`,
`govern/budget.py`, `chargeback.py`, `swarm/dispatcher.py` + `swarm_cmd.py`,
`selfheal._TIER_MODELS`, `goal_evaluator.py`, `classifier.py`,
`predict/{classifier,speculator,accept}.py`, `claude_launcher.py` timeout map
(add a `fable` key; verify substring resolution per `test_claude_launcher.py:737`).

Extend the tier enums in the same phase (additive, no behavior change yet):
`api/models/requests.py:338` Literal, `execute.py:459-460` CLI choices,
`agents/CLAUDE.md` frontmatter doc, pmo-ui `ModelTier`/`MODEL_LIST`/color maps.

### Phase 2 — Fable tier adoption (behavior changes, flag-gated where risky)

| # | Change | Notes |
|---|---|---|
| 2.1 | Self-heal `FABLE` rung: extend `EscalationTier` (append, never insert — `.value` strings are persisted), extend `_TIER_ORDER`/`_TIER_MODELS`/caps, update dispatcher tier literals (`dispatcher.py:1147-1251`), add `agents/self-heal-fable.md`, keep `max_tier` default at `OPUS` so Fable escalation is opt-in (`--max-tier fable`). Requires SQLite migration check + golden-state regen + updating `test_wave5_integration.py:745,797` terminal-tier assertions. | One deliberate PR |
| 2.2 | Frontmatter upgrades: `orchestrator` → `fable` (long-horizon coordination is Fable's headline strength); evaluate `architect` and `auditor` (regulated preset) as fast-follows. | After 1's enum extension |
| 2.3 | Complexity/risk → tier routing: the classifier already emits `complexity` (light/medium/heavy) and `risk` (LOW/MED/HIGH) but planning stages hardcode model strings. Route via `tier_for()` — heavy+HIGH architectural phases → fable, medium → sonnet/opus, light/triage → haiku. Gate behind `BATON_MODEL_ROUTING` (default off) since it shifts default cost. Record *why* each step got its model in the plan (audit trail). | `planning/stages/{decomposition,enrichment}.py`, `planner.py`, `research.py`, `classifier.py` |

### Phase 3 — New API capability adoption

| # | Change | Notes |
|---|---|---|
| 3.1 | `effort` as a budget lever: add `effort` to `PlanStep` and dispatch params; map self-heal tiers to ascending effort; triage/immune runs `low`, Fable architectural phases `high`/`xhigh`. Replaces the conceptual role of the removed `budget_tokens`. | `models/execution.py`, launcher, `selfheal.py` |
| 3.2 | Map `BATON_RUN_TOKEN_CEILING` onto task budgets: keep the engine-side hard kill (defense in depth), but also pass a per-dispatch `task_budget` derived from the remaining ceiling so Fable/4.8 can self-ration on long loops. Flag-gated (beta API). | `govern/budget.py`, dispatcher, launcher |
| 3.3 | Structured outputs for the JSON-parsing LLM calls: `goal_evaluator.py:270`, `classifier.py`, `predict/classifier.py:351` all hand-parse model JSON. `output_config.format` removes ~100 lines of brittle fence-stripping. Gate behind SDK capability detection. | 3 call sites |
| 3.4 | Re-verify the `claude-teams` backend limitations documented in `docs/architecture.md:142` / `docs/internal/agent-teams-and-goal-design.md` against current Claude Code Agent Teams (the "not resumable / one team / no nesting" claims were written against the experimental version). | docs + `BATON_TEAMS_BACKEND` code path |

### Phase 4 — Distributables & prompt tuning for Fable/4.8 behavior

| # | Change | Files |
|---|---|---|
| 4.1 | Add Fable row to the cost model + upgrade-signal table ("reserve for tasks where opus retries would cost more than one fable pass"); re-baseline the parallel-Opus rate-limit heuristic. | `references/cost-budget.md:10-36` |
| 4.2 | "Upgrade to Opus" → "upgrade a tier (sonnet → opus, opus → fable)". | `references/failure-handling.md:72` |
| 4.3 | `references/baton-engine.md`: fix `claude-haiku` non-ID at `:1449`; add `fable` to `--model` examples (`:43`) and DISPATCH `agent_model` (`:1828`). | reference |
| 4.4 | talent-builder: replace "slightly pushy about triggers" with "state explicit 'use when…' trigger conditions; avoid CRITICAL/MUST emphasis — newer models follow descriptions literally". Add the trigger-condition convention to `agents/CLAUDE.md` (Fable/4.8 under-reach for subagents without explicit triggers). | `agents/talent-builder.md:159`, `agents/CLAUDE.md` |
| 4.5 | Distributed autonomy + silence defaults in `templates/CLAUDE.md`: (a) "for minor decisions (naming, defaults, equivalent approaches), pick one and note it; ask only for scope changes or destructive actions"; (b) silence-default for engineer-class agents (counters 4.8/Fable's increased narration). Drop "(MANDATORY)" from section headers while keeping MUST on true invariants (worktree isolation, depth-1). | `templates/CLAUDE.md` |
| 4.6 | New agents (optional, after 2.x): `long-horizon-executor` (`model: fable`) for overnight `baton execute run` autonomy with full task spec up front; `outcome-rubric-writer` to convert task descriptions into gradeable criteria feeding `BATON_GOAL_EVALUATOR`/`/goal` and gate derivation. | `agents/` + roster docs |
| 4.7 | Scope `templates/skills/baton-beads/SKILL.md:138,141` "Always enrich…" to "when the interaction produced a finding worth persisting". | template skill |

### Phase 5 — Docs sweep (per the mandatory doc-maintenance matrix)

- `docs/cli-reference.md` (goal-evaluator models, swarm `--model`), `docs/agent-roster.md` (tier column + count), `docs/engine-and-runtime.md:1009,1255,1519` (timeout map, evaluator strategies), `docs/architecture/state-machine.md:263`, `docs/architecture/technical-design.md:655` (`--max-tier`), `docs/architecture.md:152` (3-tier planner text), `docs/design-decisions.md` ADR-20 pricing rationale + a new ADR for the model catalog/tier change, `README.md:840` (`--model` tiers).
- Fix the count chaos while in there: root `CLAUDE.md:14` says 33 agents (actual: 34), `docs/architecture/high-level-design.md:67` says 47, `README.md` says 22 in five places. Follow `docs/internal/doc-audit.md:240-295`'s standing recommendation to derive counts rather than re-hardcoding.
- Resync `GEMINI.md`'s env-var table (missing `BATON_GOAL_EVALUATOR`, `BATON_TEAMS_BACKEND`, etc.) — root CLAUDE.md mandates the sync and it has drifted.

---

## Sequencing & risk

```
Phase 0 (days)  ──► Phase 1 (catalog, ~1 wk) ──► Phase 2 (tier adoption, ~1-2 wk)
                                              └─► Phase 3 (API levers, as SDK support confirms)
Phase 4/5 (docs+distributables) can start after Phase 0 and land alongside 1-2.
```

- **Phase 0/1 risk: LOW.** Additive; `MODEL_PRICING` alias preserves test/API compatibility. The dangerous direction (ceiling under-count) is fixed immediately.
- **Phase 2 risk: MEDIUM**, concentrated in the persisted `EscalationTier` values (SQLite + `ExecutionState`) and golden-state regen — one deliberate PR with migration + regen together. Routing (2.3) is flag-gated.
- **Phase 3 risk: MEDIUM** — depends on SDK beta surfaces (`task_budget`, `output_config`); capability-gate everything so older SDKs degrade gracefully.
- **Verification**: each phase ends with the pricing-pinned test list green, golden states regenerated via the generator (never hand-edited), and one live `baton plan --dry-run` spot-check that a `fable` step prices at $10/$50.

## Open questions for the maintainer

1. Should `orchestrator` default to `fable` for everyone (≈2x Opus cost), or stay `opus` with `fable` documented as the opt-in for long-horizon runs? This proposal recommends opt-in via frontmatter override until 2.3's routing data justifies a default flip.
2. Does `baton` need to support Fable in the self-heal ladder by default (`max_tier`), or only behind `--max-tier fable`? Recommended: opt-in (cost).
3. Is there appetite for `BATON_GOAL_EVALUATOR=fable`? Haiku remains the right default for a per-turn evaluator; fable would only make sense for end-of-run retrospective grading.
