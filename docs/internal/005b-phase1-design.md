# 005b Phase 1 Design — IntelligentPlanner Decomposition

**Step:** 1.1 (architect)
**Branch:** `feat/005b-engine-decomposition`
**Target file:** `agent_baton/core/engine/planner.py` (3,977 lines)
**Source proposal:** `proposals/005b-implementation-plan.md` §2

---

## 1. Public API contract — must remain byte-identical

### 1.1 Exported symbol surface

`agent_baton/core/engine/__init__.py` re-exports `IntelligentPlanner`. Other code imports via `from agent_baton.core.engine.planner import …`.

| Symbol | Kind | Where consumed |
|---|---|---|
| `IntelligentPlanner` | class | CLI (`cli/commands/execution/plan_cmd.py:26,499`), package re-export, tests, daemon, forge |
| `IntelligentPlanner.create_plan` | method | CLI (`plan_cmd.py:507`), tests |
| `IntelligentPlanner.explain_plan` | method | CLI (`plan_cmd.py:588`), tests |
| `IntelligentPlanner.isolation_for_step` | method | dispatchers, tests |
| `IntelligentPlanner.knowledge_registry` | public attr | constructor injection, tests |
| `_DEFAULT_AGENTS` | module-level dict | tests (`tests/test_engine_planner.py:12`) |
| `_PHASE_NAMES` | module-level dict | tests (`tests/test_engine_planner.py:13`) |
| `_TASK_TYPE_KEYWORDS` | module-level list | tests (`tests/test_engine_planner.py:14`) |
| `GateScope` | type alias | imported by callers passing `gate_scope=` |
| `RetroEngine` | Protocol | tests injecting fakes |

### 1.2 Constructor signature (frozen)

```python
def __init__(
    self,
    team_context_root: Path | None = None,
    classifier: DataClassifier | None = None,
    policy_engine: PolicyEngine | None = None,
    retro_engine: RetroEngine | None = None,
    knowledge_registry: KnowledgeRegistry | None = None,
    task_classifier: TaskClassifier | None = None,
    bead_store=None,
    project_config: "ProjectConfig | None" = None,
) -> None: ...
```

All eight parameters are keyword-or-positional today; CLI uses kw-only. **No positional reorderings allowed.**

### 1.3 `create_plan` signature (frozen)

```python
def create_plan(
    self,
    task_summary: str,
    *,
    task_type: str | None = None,
    complexity: str | None = None,
    project_root: Path | None = None,
    agents: list[str] | None = None,
    phases: list[dict] | None = None,
    explicit_knowledge_packs: list[str] | None = None,
    explicit_knowledge_docs: list[str] | None = None,
    intervention_level: str = "low",
    default_model: str | None = None,
    gate_scope: GateScope = "focused",
) -> MachinePlan: ...
```

### 1.4 `explain_plan` signature (frozen)

```python
def explain_plan(self, plan: MachinePlan) -> str: ...
```

Returns markdown with section headers (`# Plan Explanation`, `## Pattern Influence`, `## Score Warnings`, `## Agent Routing`, `## Data Classification`, `## Task Classification`, `## Policy Notes`, `## Team Cost Estimates`, `## Foresight Insights`, `## Plan Review`, `## Phase Summary`). Tests assert on these section headers — they must remain.

### 1.5 Public attributes / `_last_*` introspection fields

Reset at the top of `create_plan` (lines 1067-1074) and read by `explain_plan` plus tests:

- `knowledge_registry` (read/write, true public)
- `_last_pattern_used: LearnedPattern | None`
- `_last_score_warnings: list[str]`
- `_last_routing_notes: list[str]`
- `_last_retro_feedback: RetrospectiveFeedback | None`
- `_last_classification: ClassificationResult | None`
- `_last_policy_violations: list[PolicyViolation]`
- `_last_task_classification: TaskClassification | None`
- `_last_foresight_insights: list[ForesightInsight]`
- `_last_review_result: PlanReviewResult | None`
- `_last_team_cost_estimates: dict[str, int]` (lazy, set inside `create_plan` line 1725)

**Decision:** these are de-facto public. The refactor must preserve them as attributes on `IntelligentPlanner`. They become **return values** from analyzer/strategy steps, then **assigned back** onto the planner instance after the pipeline runs.

### 1.6 Module-level helpers / constants the test suite imports

Importing `_DEFAULT_AGENTS`, `_PHASE_NAMES`, `_TASK_TYPE_KEYWORDS` from `planner` is a hard contract. **These must continue to live (or be re-exported) from `agent_baton.core.engine.planner`.**

---

## 2. Analyzer extraction map

The "14-step procedural validation" the proposal refers to is the **steps 12–14 block in `create_plan`** (lines 1519–1734) plus distributed validation across steps 7–13d.

### 2.1 `DependencyAnalyzer` — DAG ordering & step-id integrity

Today: planner does not run an explicit dependency check. The analyzer **only emits warnings** (no rejection), per existing skeleton (lines 28-39 of `analyzers.py`).

**Code to move:** none from `planner.py` (greenfield logic; preserve warning-only stance).

### 2.2 `RiskAnalyzer` — risk classification + approval gating

Move into `RiskAnalyzer.validate(plan, *, task_summary, agents, classification)`:

| Lines (planner.py) | Code | Notes |
|---|---|---|
| 1322–1329 | DataClassifier call (step 7) | Sets `_last_classification`. Classifier is a constructor arg. |
| 1331–1354 | risk-merge logic (step 8) — keyword + classifier ordinal max | Pure. |
| 1356–1357 | git-strategy derivation (step 8b) | Calls `_select_git_strategy`. |
| 1546–1557 | approval-gate setting on Design/Research at HIGH+ risk (step 12b) | **Skeleton uses wrong phase names** — see §6 Q10. |
| 3667–3738 | `_assess_risk` | Pure given inputs. |

**Output:** `plan.risk_level`, `plan.git_strategy`, mutated `phase.approval_required` / `phase.approval_description` for HIGH+ Design/Research phases.

### 2.3 `CapabilityAnalyzer` — agent routing, scoring, retro/policy filtering

Move into `CapabilityAnalyzer.validate(plan, *, project_root, registry, router, scorer, retro_engine, policy_engine)`:

| Lines | Code | Notes |
|---|---|---|
| 1149–1169 | pattern lookup (step 4) | Pull pattern, override agents. |
| 1184–1197 | retrospective drop/prefer (step 5b) | With `_apply_retro_feedback` (3578–3636). |
| 1235–1265 | concern-cap, cross-concern expansion (5d) | With `_expand_agents_for_concerns` (2580–2611). |
| 1270–1278 | routing (step 6) | With `_route_agents` (3534–3560). |
| 1497–1498 | score check (step 10) | With `_check_agent_scores` (3562–3576). |
| 1503–1517 | policy validation (step 11b) | With `_classify_to_preset_key` (3744–3763) and `_validate_agents_against_policy` (3765–3832). |

**Output:** routed `resolved_agents`, `_agent_route_map`, `_last_score_warnings`, `_last_routing_notes`, `_last_retro_feedback`, `_last_policy_violations` returned in a `CapabilityResult` dataclass.

### 2.4 `DepthAnalyzer` — subscale plan rejection (NEW behaviour)

The only **net-new** analyzer. Existing skeleton hard-codes a 5-conjunction blacklist and raises `SubscalePlanError`. Upgrade per §5.

**Code to move:** none from `planner.py` (greenfield). Closest existing logic is `_parse_concerns` (2341–2384) and `_split_implement_phase_by_concerns` (2503–2578), which perform a *post hoc* split. DepthAnalyzer instead **prevents** subscale plans; concern-splitting becomes the strategy's response to rejection.

### 2.5 The "14-step procedural validation" — explicit map

| Planner step | Lines | Owner after refactor |
|---|---|---|
| 1. Generate task_id | 1077 | strategy/shared |
| 2. Detect stack | 1080–1085 | strategy input |
| 2b. Parse structured description | 1087–1093 | strategy |
| 3. Classify task type/complexity/agents/phases | 1095–1147 | strategy |
| 4. Pattern lookup | 1149–1169 | `CapabilityAnalyzer` |
| 4b. Bead hints | 1171–1182 | strategy (apply at 12d) |
| 5b. Retro feedback | 1184–1197 | `CapabilityAnalyzer` |
| 5c. Compound subtask decomposition | 1199–1233 | strategy |
| 5d/5d-cap. Cross-concern expand + cap | 1235–1265 | `CapabilityAnalyzer` |
| 6. Route agents | 1270–1278 | `CapabilityAnalyzer` |
| 6.5. Knowledge resolver setup | 1280–1320 | analyzer pipeline pre-step |
| 7. DataClassifier classification | 1322–1329 | `RiskAnalyzer` |
| 8. Merged risk + 8b git strategy | 1331–1357 | `RiskAnalyzer` |
| 9. Build phases | 1359–1389 | strategy |
| 9b. Enrich phases | 1392–1393 | strategy (post-build) |
| 9.5/9.6. Knowledge resolution + gap suggestions | 1395–1449 | `KnowledgeAnalyzer` (new) |
| 9.7/9.8. Foresight | 1451–1495 | `ForesightAnalyzer` (or DepthAnalyzer-adjacent) |
| 10. Score check | 1497–1498 | `CapabilityAnalyzer` |
| 11. Budget tier | 1500–1501 | `CapabilityAnalyzer` |
| 11b. Policy validation | 1503–1517 | `CapabilityAnalyzer` |
| 12. Add QA gates | 1519–1534 | `GateAnalyzer` (new) — wraps `_default_gate` (3271–3414) |
| 12.a. Project config | 1536–1544 | `GateAnalyzer` |
| 12b. Approval gates on Design/Research at HIGH+ | 1546–1557 | `RiskAnalyzer` |
| 12b-bis. Concern-splitting | 1559–1577 | `DepthAnalyzer` (post-build) |
| 12c. Team consolidation | 1579–1588 | strategy |
| 12c.4. File path extraction | 1590–1592 | strategy helper |
| 12c.5. Plan review | 1594–1622 | `DepthAnalyzer` (or `PlanReviewAnalyzer`) |
| 12d. Apply bead hints | 1624–1627 | strategy (post-build) |
| 13. CLAUDE.md context_files | 1629–1633 | strategy (post-build) |
| 13b. Model inheritance | 1635–1650 | strategy (post-build) |
| 13c. Context richness | 1652–1661 | strategy (post-build) |
| 13d. Prior task dependency beads | 1663–1676 | strategy (post-build) |
| 14. Shared context build | 1678–1735 | planner orchestrator (final assembly) |
| 16. Team cost estimates | 1724–1732 | strategy / planner final |

**Decision:** the proposal-named four analyzers are not enough. We add **GateAnalyzer** (steps 12 + 12.a) and **KnowledgeAnalyzer** (steps 6.5 + 9.5/9.6/9.8). Both are pure post-build mutators with crisp inputs.

---

## 3. Strategy extraction map

Strategies generate the **draft phase structure** (classification + phase building). Knowledge resolution, gating, and risk decoration run **after** the strategy in the analyzer pipeline.

### 3.1 `HeuristicStrategy` (alias `ZeroShotStrategy`) — heuristic + classifier path

The path the planner takes today when no template/refinement is requested. The skeleton's `ZeroShotStrategy` name is misleading (current code is keyword/Haiku-classifier-based, not free-form LLM). **DESIGN_CHOICE — keep `ZeroShotStrategy` for stub compatibility but expose `HeuristicStrategy` as the canonical name.**

| Lines | Code | Notes |
|---|---|---|
| 874–995 | `_parse_structured_description` | move |
| 1077 + 2291–2304 | `_generate_task_id` | move |
| 1095–1147 | classifier dispatch (step 3) | move |
| 1199–1233 | compound subtask decomposition | move |
| 2306–2316 | `_infer_task_type` | move |
| 2322–2339 | `_parse_subtasks` | move |
| 2341–2384 | `_parse_concerns` | shared helper (`_planner_helpers.py`) |
| 2386–2433 | `_pick_agent_for_concern` | move |
| 2435–2466 | `_score_knowledge_for_concern` | shared helper |
| 2468–2501 | `_partition_knowledge` | shared helper |
| 2503–2578 | `_split_implement_phase_by_concerns` | shared helper |
| 2580–2611 | `_expand_agents_for_concerns` | shared helper |
| 2613–2645 | `_build_compound_phases` | move |
| 2651–2700 | `_enrich_phases` | move |
| 2961–2969 | `_default_phases` | move |
| 2971–2984 | `_apply_pattern` | move |
| 3033–3040 | `_is_blocked_for_phase` | move |
| 3042–3202 | `_assign_agents_to_phases` | move |
| 3204–3219 | `_build_phases_for_names` | shared helper |
| 3221–3265 | `_phases_from_dicts` | move |
| 3416–3448 | `_is_team_phase` | move |
| 3450–3528 | `_consolidate_team_step` | move |
| 3642–3661 | `_select_budget_tier` | move (or keep in `CapabilityAnalyzer`) |

**LLM prompt construction:** the proposal says "move LLM prompt construction out of the planner". There is **no live LLM-prompt-construction in the planner today**; the LLM call is in `agent_baton/core/engine/classifier.py` (`HaikuClassifier`). The planner consumes a `TaskClassifier` interface. **Deviation:** in this phase, ZeroShotStrategy wraps the existing keyword + Haiku-classifier flow, no new LLM prompts are written. A future Phase 1.5 can extract `HaikuClassifier`'s prompt.

### 3.2 `TemplateStrategy` — `--from-template` path

Today implemented inside `plan_cmd.py` (loads saved template from `.claude/plan-templates/<name>.json` and instantiates a `MachinePlan` directly). The planner has **no template path** — `--from-template` short-circuits before `IntelligentPlanner.create_plan()` is called.

**DESIGN_CHOICE — TemplateStrategy in Phase 1 is a forward-port of the CLI logic into the strategy layer.** For Phase 1, leave TemplateStrategy as `NotImplementedError`. Document the gap.

### 3.3 `RefinementStrategy` — partial-plan amendment

Not implemented anywhere today. Closest is bead-hints application (`_apply_bead_hints`, 2045–2117). Phase 1 leaves this as `NotImplementedError`. **Defer to Phase 1.5;** extracts of `_apply_bead_hints`, `_attach_prior_task_beads`, `_detect_task_dependency` would naturally land here.

### 3.4 Strategy selection

```python
def _select_strategy(self, objective: str, context: dict) -> PlanStrategy:
    # Phase 1: no real selection — always Heuristic.
    return self._heuristic_strategy
```

---

## 4. Post-refactor `IntelligentPlanner.create_plan` shape

**Keep the public method named `create_plan`** (renaming to `generate` would break the API). The pipeline body becomes:

```python
def create_plan(self, task_summary: str, *, ...) -> MachinePlan:
    self._reset_explainability_state()

    ctx = self._build_context(
        task_summary=task_summary,
        task_type=task_type, complexity=complexity,
        project_root=project_root, agents=agents, phases=phases,
        explicit_knowledge_packs=explicit_knowledge_packs,
        explicit_knowledge_docs=explicit_knowledge_docs,
        intervention_level=intervention_level,
        default_model=default_model, gate_scope=gate_scope,
    )

    strategy = self._select_strategy(task_summary, ctx)
    draft = strategy.execute(task_summary, ctx)

    for analyzer in self._analyzer_pipeline:  # capability, risk, knowledge, gate, depth
        try:
            draft = analyzer.validate(draft, **ctx.as_kwargs())
        except SubscalePlanError as exc:
            draft = strategy.decompose(draft, exc, ctx)
            draft = self._rerun_pipeline_after_decompose(draft, ctx)
            break

    self._sync_explainability_from_pipeline(ctx, draft)
    draft.shared_context = self._build_shared_context(draft)
    self._capture_planning_bead_if_enabled(draft, ctx)
    self._emit_otel_span_if_enabled(draft, ctx)
    return draft
```

Body length: ~25 lines. The 1,000-line `create_plan` becomes a true orchestrator.

**Pipeline order (fixed):** Capability → Risk → Knowledge → Gate → Depth.
- Capability before Risk because risk merges classifier + agent-derived signals.
- Knowledge before Gate because gate scoping reads `step.allowed_paths`.
- Depth last because it is the rejection point and needs the final shape.

---

## 5. Subscale-plan handling

### 5.1 What counts as subscale

A step is rejected when **any** of the following holds:

1. **Conjunction signal** in `step.task_description`: existing skeleton list (`research and write`, `audit and fix`, `analyze and implement`, `design and build`, `investigate and solve`) plus broader `<verb1> and <verb2>` where both verbs are in `_PHASE_VERBS` keys (`research`, `investigate`, `design`, `implement`, `fix`, `draft`, `test`, `review`).
2. **Concern density**: ≥2 distinct markers from `_CONCERN_MARKER` (e.g. `"Implement F0.1 ... and F0.2 ..."`). Re-uses `_parse_concerns`.
3. **Multi-agent affinity**: for an `Implement`-class phase whose single step spans ≥2 of `_PHASE_IDEAL_ROLES["implement"]` (e.g. names both `backend` and `frontend`). Re-uses `_CROSS_CONCERN_SIGNALS`.

DepthAnalyzer raises `SubscalePlanError(step_id, reason, hint)`.

### 5.2 Rejection feedback loop

**DESIGN_CHOICE — exception with single-retry decomposition, not recursion.**

A single retry catches the >95% case. If the second pass also raises, the planner logs a warning and falls back to the original draft (graceful degradation, never block the user).

```python
try:
    plan = analyzer.validate(plan, **ctx)
except SubscalePlanError as exc:
    logger.info("DepthAnalyzer rejected step %s: %s", exc.step_id, exc.reason)
    plan = strategy.decompose(plan, exc, ctx)
    plan = analyzer.validate(plan, **ctx)  # ONE retry; let it raise this time
```

`PlanStrategy.decompose(plan, exc, ctx)` is a **new method on the protocol**. For `HeuristicStrategy`:

- Reason "concern-density" → call `_split_implement_phase_by_concerns(...)`.
- Reason "conjunction" → split step into two sequential steps with ids `<phase>.1`, `<phase>.2`.
- Reason "multi-agent affinity" → promote step into team via `_consolidate_team_step` after concern split.

The existing post-hoc split (12b-bis) stops being a "rescue" and becomes **the strategy's response to DepthAnalyzer rejection**.

### 5.3 Composition with `--complexity light`

`--complexity light` **suppresses DepthAnalyzer rejection**. A user explicitly asking for a light plan is consciously trading depth for speed.

```python
class DepthAnalyzer:
    def validate(self, plan, *, complexity="medium", **kwargs):
        if complexity == "light":
            return plan
        # ... rejection logic ...
```

This matches `PlanReviewer`'s existing behaviour (returns `source="skipped-light"` for light plans, planner.py 1937–1943).

### 5.4 Skeleton fixes required

The current `analyzers.py:DepthAnalyzer` (lines 72–98):
- Hard-codes only 5 conjunction patterns. **Expand to use `_PHASE_VERBS`.**
- Doesn't accept `complexity` kwarg. **Add it.**
- No concern-density check. **Add via `_parse_concerns`.**
- No multi-agent-affinity check. **Add via `_CROSS_CONCERN_SIGNALS`.**

---

## 6. Open questions and risks

### Q1. `_isolation_overrides` lifecycle (BEAD_WARNING)
`_apply_project_config` writes to `self._isolation_overrides_map` during `create_plan` (line 2800). After extraction this lives on the analyzer/strategy instance. `IntelligentPlanner.isolation_for_step()` is called by dispatchers **after `create_plan` returns**. The map must be patched back onto the planner instance at end of pipeline.

### Q2. Import cycles — `strategies.py` ↔ `analyzers.py`
Both need `_parse_concerns`, `_split_implement_phase_by_concerns`, `_CROSS_CONCERN_SIGNALS`. **Mitigation:** create `agent_baton/core/engine/_planner_helpers.py` for shared pure helpers. Strategy + analyzer modules import from it; planner.py also imports for existing public re-exports.

### Q3. `_last_team_cost_estimates` is set late (line 1725)
After the analyzer pipeline. In the new pipeline, this is part of "final assembly" inside `create_plan`. Must remain there or `explain_plan` loses the cost section.

### Q4. Knowledge resolution split across phases
Steps 6.5 (resolver setup, line 1280) and 9.5 (resolution, 1399) are separated with phase building between. The new `KnowledgeAnalyzer` collapses them. Verify with `test_engine_planner.py::test_knowledge_resolution_*`.

### Q5. Should `_select_budget_tier` be its own analyzer?
**Recommendation:** keep inside `CapabilityAnalyzer`. Revisit if it grows past ~250 LOC.

### Q6. Foresight — analyzer or strategy?
**Recommendation:** model as `ForesightAnalyzer` wrapping `ForesightEngine`. Keeps pipeline-of-mutators metaphor consistent.

### Q7. `_capture_planning_bead` / OTel span emission
Run at end of `create_plan` (1737–1774). **Observability side effects**, not pipeline work. Keep on the planner's final-assembly section.

### Q8. API-drift risk: positional constructor params (BEAD_WARNING)
Current `__init__` accepts `team_context_root` positionally. **Mitigation:** add `inspect.signature(IntelligentPlanner.__init__)` snapshot test before refactor begins.

### Q9. Test imports of module-level constants (BEAD_WARNING)
`from agent_baton.core.engine.planner import _DEFAULT_AGENTS` is a hard contract. After the refactor moves these into `strategies.py`, **planner.py must re-export them**:
```python
from agent_baton.core.engine.strategies import _DEFAULT_AGENTS, _PHASE_NAMES
from agent_baton.core.engine._planner_helpers import _TASK_TYPE_KEYWORDS
```

### Q10. Skeleton `RiskAnalyzer` is wrong for our codebase (BEAD_DISCOVERY)
Current skeleton triggers approval on phases named `("implement", "deploy", "execute")`. Planner today triggers on `("design", "research")` (line 1549). **Implementation engineer: replace skeleton's logic, do not ship as-is.**

### Q11. Skeleton `DepthAnalyzer` too aggressive (BEAD_WARNING)
The skeleton fires on any occurrence of `"audit and fix"` anywhere, including substrings. Add word-boundary regex (`\b(audit and fix)\b`) and complexity-light bypass before shipping.

---

## 7. Implementation sequencing

1. **Snapshot test for public API.** Add `tests/test_planner_api_contract.py` asserting:
   - `inspect.signature(IntelligentPlanner.__init__)` matches §1.2.
   - `inspect.signature(IntelligentPlanner.create_plan)` matches §1.3.
   - `IntelligentPlanner` exposes the `_last_*` attribute set after `create_plan` returns.
   - `from agent_baton.core.engine.planner import _DEFAULT_AGENTS, _PHASE_NAMES, _TASK_TYPE_KEYWORDS` succeeds.

   This must pass before and after the refactor — it is the canary.

2. **Create `agent_baton/core/engine/_planner_helpers.py`** with shared pure helpers. Run tests.

3. **Implement `HeuristicStrategy`** in `strategies.py` (replacing stub). Add `decompose(plan, exc, ctx)`. Run full suite.

4. **Implement `CapabilityAnalyzer`, `RiskAnalyzer`, `KnowledgeAnalyzer`, `GateAnalyzer`** per §2.

5. **Implement upgraded `DepthAnalyzer`** per §5.

6. **Refactor `IntelligentPlanner.create_plan`** to §4 shape. Patch `_last_*` from analyzer return values.

7. **Run full test suite** (3,900+ tests). Zero regressions = the gate.

8. **Cleanup pass:** remove dead code from `planner.py`. Target end state: ~600 lines.

---

## Files referenced

- `proposals/005b-implementation-plan.md`
- `agent_baton/core/engine/planner.py` (3,977 lines — extraction source)
- `agent_baton/core/engine/analyzers.py` (skeleton — needs corrections per §2.2 and §5)
- `agent_baton/core/engine/strategies.py` (skeleton — implement per §3)
- `agent_baton/core/engine/__init__.py` (re-export contract)
- `agent_baton/cli/commands/execution/plan_cmd.py` (primary external caller)
- `tests/test_engine_planner.py` (test contract)
- `agent_baton/core/engine/foresight.py`
- `agent_baton/core/engine/classifier.py`
