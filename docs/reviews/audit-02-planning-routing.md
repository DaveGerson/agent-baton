# Audit: Planning & Routing

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/engine/planning/` (pipeline.py, planner.py, draft.py, protocols.py, structured_spec.py, services.py, rules/*, stages/*, utils/*), `agent_baton/core/predict/` (classifier.py, accept.py, speculator.py, watcher.py), `agent_baton/core/engine/plan_reviewer.py`, `agent_baton/core/engine/classifier.py`

## Executive Summary

The planning pipeline is the strongest-architected subsystem in agent-baton. The refactor from a monolithic planner into a seven-stage pipeline with a formal Stage protocol, frozen DI container, and draft-state flow is textbook clean. The biggest risk is silent failure: the pipeline has 15+ bare `except Exception: pass` blocks that can swallow classification, risk, and routing failures without any trace. The backward-compat proxy methods on `IntelligentPlanner` (planner.py lines 530-757) are dead weight that should be removed once legacy callers are confirmed gone.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | A | Pipeline decomposition is exemplary; rules-as-data pattern is clean |
| 2 | Acceleration & Maintainability | B | New contributor can follow the 7-stage flow; proxy methods and duplication add friction |
| 3 | Token/Quality Tradeoffs | B | Haiku classification + Opus plan review is a well-tiered model strategy; the post-pipeline Opus review sits outside the stage system |
| 4 | Implementation Completeness | B | All 7 stages are wired and produce correct plans; hard-gate is off by default |
| 5 | Silent Failure Risk | C | 15+ bare except-pass blocks can mask classification, routing, and risk failures |
| 6 | Code Smells | B | One significant duplication; proxy methods are code smell; otherwise clean |
| 7 | User Discoverability | B | Plan explanation output is good; routing decisions are logged but not surfaced to CLI |
| 8 | Extensibility | A | Stage protocol makes adding stages trivial; rules-as-data pattern makes extending routing/risk/templates a one-line change |

## Detailed Findings

### Dimension 1: Code Quality Improvement -- A

The refactor from the legacy monolithic planner to the pipeline architecture is the single best structural improvement in the codebase.

- **Stage protocol** (`protocols.py:16-26`): `@runtime_checkable` protocol with a minimal `name` + `run()` contract.
- **PlanDraft** (`draft.py:34-146`): Replaces hidden `_CreatePlanState`. All fields are documented, grouped by stage ownership.
- **PlannerServices** (`services.py:41-66`): Frozen dataclass as DI container.
- **Rules as pure data** (`rules/`): Seven modules containing only constants, regexes, and dicts.
- **Pipeline runner** (`pipeline.py:26-35`): Ten lines of code. Intentionally trivial.

Minor issue: `PlannerServices` TYPE_CHECKING imports (`services.py:25-38`) reference stale paths (`agent_baton.core.routing.agent_registry`, `agent_baton.core.optimize.budget_tuner`), while runtime uses different paths. Not a runtime bug but misleading.

### Dimension 2: Acceleration & Maintainability -- B

**Strengths**: The 7-stage pipeline is self-documenting. Each stage file maps its methods to legacy step numbers. Utils are pure functions.

**Friction points**:

1. **Backward-compat proxy methods** (`planner.py:530-757`): 30 methods that do nothing but delegate. 228 lines of noise.
2. **`_DEFAULT_AGENTS` duplication**: Defined in `rules/default_agents.py:5-15` and duplicated in `stages/classification.py:33-43`.
3. **Post-pipeline review outside stage system** (`planner.py:289-485`): `_review_plan_with_cli()` runs AFTER `pipeline.run()`. This ~200-line method breaks the pipeline's single-pass design.

### Dimension 3: Token/Quality Tradeoffs -- B

- **KeywordClassifier** (free, instant): Deterministic fallback. No tokens spent.
- **CLIValidatedClassifier**: Keyword draft + Haiku correction. The "draft + validate" pattern is right.
- **Opus plan review**: ~1500-token prompt enumerating 6 known pipeline failure modes. Worth the cost.
- **PlanReviewer Haiku call**: 512 max_tokens, 8s timeout for step-splitting recommendations.

**Concern**: Both `_review_plan_with_cli()` and `PlanReviewer` review plan quality at different times. A new contributor could reasonably ask "why are there two plan reviewers?"

### Dimension 4: Implementation Completeness -- B

**Complete**: All 7 stages wired. Safety roster injection correctly re-adds code-reviewer and auditor after complexity cap.

**Gaps**:

1. **Hard gate is off by default** (`validation.py:235-236`): `BATON_PLANNER_HARD_GATE` must be explicitly set. Without it, critical defects are logged but the plan proceeds.
2. **`apply_pattern` ignores pattern phases** (`phase_builder.py:453-463`): Takes a `LearnedPattern` but discards its phase structure.
3. **Predict subsystem feature flag not enforced at import**.

### Dimension 5: Silent Failure Risk -- C

**Critical silent failures**:

1. **Pattern lookup** (`roster.py:123-139`): `except Exception: pass`. No log entry.
2. **Data classification** (`risk.py:145-146`): `except Exception: pass`. Broken classifier means no sensitivity info.
3. **Knowledge resolution** (`decomposition.py:158-161`): Failures caught at DEBUG level.
4. **Policy validation** (`validation.py:156-165`): `except Exception: pass`. Zero compliance checks.
5. **Bead capture** (`assembly.py:148-166`): Primary observability signal fails silently.
6. **CLI risk hint extraction** (`risk.py:161-165`): Uses `getattr` with duck typing. Attribute name changes produce `None` silently.

### Dimension 6: Code Smells -- B

1. `_DEFAULT_AGENTS` duplication: `rules/default_agents.py` vs `stages/classification.py`.
2. Backward-compat proxy methods: 30 methods, 228 lines of delegation boilerplate.
3. Post-pipeline method: 196-line method outside the stage system.
4. Constructor import block: 10 imports at call time, a latent circular-import workaround.

### Dimension 7: User Discoverability -- B

**Good**: `explain_plan()` produces comprehensive structured output covering pattern influence, score warnings, routing notes, data classification, policy notes, and foresight insights.

**Gaps**: Classification pipeline not documented. `BATON_PLANNER_HARD_GATE`, `BATON_MAX_KNOWLEDGE_PER_STEP`, `BATON_PREDICT_ENABLED` undiscoverable.

### Dimension 8: Extensibility -- A

1. Stage protocol: Adding a stage = implement `name` + `run()`, insert into list.
2. Rules as data: Adding an agent type = add entries to 4-5 dicts.
3. PlannerServices: Adding a collaborator = add a field with `None` default.
4. Task type extension: Four data tables, zero logic changes.
5. Gate customization: `_STACK_GATE_COMMANDS` maps language to commands.

## Critical Issues (Fix Now)

- **`_DEFAULT_AGENTS` duplication**: `stages/classification.py:33-43` defines its own copy that duplicates `rules/default_agents.py:5-15`. If either is updated without the other, keyword fallback and auto-classification produce different agent rosters. Fix: delete the local copy and import from `rules.default_agents`.

## Important Issues (Fix Soon)

- **Post-pipeline Opus review should be a stage**: `planner.py:289-485` runs outside the stage system. Should be integrated into `ValidationStage` or become a new stage.
- **`apply_pattern` ignores pattern phases**: Either use the pattern's phase data or document why it is intentionally ignored.
- **Silent pattern-lookup failure**: `roster.py:123-139` swallows all exceptions. Log at WARNING level.
- **Document `BATON_PLANNER_HARD_GATE`**: Add to the environment variable table in `CLAUDE.md`.

## Improvement Opportunities (Fix Later)

- Remove backward-compat proxy methods (228 lines).
- Consolidate plan reviewers.
- Add structured logging to silent-fail blocks.
- Fix `PlannerServices` TYPE_CHECKING imports.

## Silent Failure Inventory

| Location | What Fails Silently | Risk Level |
|----------|-------------------|------------|
| `roster.py:123-139` | Pattern learner lookup | HIGH |
| `risk.py:145-146` | Data sensitivity classification | HIGH |
| `validation.py:156-165` | Policy engine validation | HIGH |
| `assembly.py:148-166` | Planning bead capture (F4) | MEDIUM |
| `decomposition.py:158-161` | Knowledge resolution per step | MEDIUM |
| `decomposition.py:228-230` | Foresight analysis | MEDIUM |
| `risk.py:103-105` | Knowledge telemetry store init | LOW |
| `risk.py:116-118` | Knowledge ranker init | LOW |
| `roster.py:141-149` | Bead analyzer hints | LOW |
| `roster.py:166-168` | Retro feedback loading | LOW |
| `planner.py:107-111` | CLIValidatedClassifier init | LOW |
| `enrichment.py:147-152` | Project config application | LOW |
| `risk.py:161-165` | CLI risk hint via getattr | LOW |
| `context.py:237-277` | External annotations fetch | LOW |
| `planner.py:302-307` | HeadlessClaude import | LOW |
