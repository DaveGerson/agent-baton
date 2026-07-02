---
quadrant: explanation
audience: users, maintainers
see-also:
  - [../pillars.md](../pillars.md)
  - [../engine-and-runtime.md](../engine-and-runtime.md)
---

# Pillar 1 — Plan with Foresight

!!! abstract "Pillar context"
    One of [the four pillars](../pillars.md) — the first thing Baton does for every effort.

> **In one line:** see the shape of the work, and where it will break, before spending a token.

## The vision

Before a single specialist agent fires, you should be able to hold the entire effort in one view: what kind of task this is, which phases it needs in which order, which agents fit each phase, where the work is likely to break, and what it will cost — stated with explicit uncertainty, not false precision. You commit to a plan backed by analysis, not a hope backed by optimism.

The ideal classifier takes a plain-language description, detects the project stack, and produces a task type, complexity tier, and a ranked agent roster with high confidence — surfacing signals the author may not have articulated ("this touches migrations, so you need rollback provisioned first"). Risk flows from content, not from human memory: regulated keywords, sensitive file paths, and cross-domain integration signals each elevate the tier automatically, so the guardrail preset and approval requirements are set before any code is written.

Cost and wall-clock time should be knowable up front — not a surprise invoice after the run. The forecast should be honest about its own precision: a ±50% band is more useful than a false decimal place. And when a first plan does not fully satisfy a stated goal, the engine should be able to identify the gap, append the missing phases, and re-evaluate — converging on "done" rather than stopping at "shipped what I planned."

Finally, the plan itself should be transparent. Every decision — why this agent, why HIGH risk, why this phase sequence — should be explicable on demand, so a human can review and correct it before the first agent runs.

## How it works today

### The seven-stage planning pipeline

`baton plan "<task>"` calls `IntelligentPlanner.create_plan()`, which runs a seven-stage deterministic pipeline defined in `agent_baton/core/engine/planning/pipeline.py`. The stages run in fixed order:

1. **ClassificationStage** — generates a task ID, auto-detects the project stack via `AgentRouter.detect_stack()`, infers task type (new-feature, bug-fix, migration, refactor, test, audit, …), and assigns a complexity tier (light / medium / heavy). The primary path uses `FallbackClassifier` in `agent_baton/core/engine/classifier.py`, which tries `TalentAgentClassifier` (Sonnet via Claude CLI) first and falls back to `KeywordClassifier` (deterministic keyword heuristics) when the CLI is unavailable.
2. **RosterStage** — selects the agent roster for this task type and complexity, drawing on the agent registry, prior retrospective patterns, and stack routing to pick stack-flavored variants (e.g. `backend-engineer--python`).
3. **RiskStage** — runs `DataClassifier.classify()` from `agent_baton/core/govern/classifier.py` to assign a risk level (LOW / MEDIUM / HIGH / CRITICAL) and a guardrail preset. The classifier matches the task description and changed file paths against five signal categories (regulated, PII, security, infrastructure, database). Three or more regulated/PII signals escalate the risk to CRITICAL automatically. Assurance Packs can extend the classifier with domain-specific keywords and path patterns via `make_classifier_for_packs()`.
4. **DecompositionStage** — builds phases from templates keyed on task type and complexity (`agent_baton/core/engine/planning/rules/phase_templates.py`). After phases are built, the **Foresight Engine** (`agent_baton/core/engine/foresight.py`) runs as a sub-step: it scans every step description and agent assignment against seven built-in rules and inserts preparatory phases when a gap is detected (see below).
5. **EnrichmentStage** — attaches gate commands to each phase boundary, derives approval requirements from the risk tier, injects knowledge packs and reference documents, and surfaces prior bead hints from completed executions.
6. **ValidationStage** — scores the plan quality, assigns a budget tier (tight / standard / generous), and runs the structural quality gate (see `agent_baton/core/engine/planning/stages/validation.py`). Critical defects (empty plan, empty phase, wrong agent role in a phase, missing Review or Audit coverage) raise `PlanQualityError` and block by default. `BATON_DEV_MODE=1` or `BATON_PLANNER_WARN_ONLY=1` downgrade those defects to warnings for local experimentation; `BATON_PLANNER_HARD_GATE=1` forces blocking even in those modes.
7. **AssemblyStage** — assembles the final `MachinePlan` and emits OpenTelemetry spans if `BATON_OTEL_ENABLED` is set.

### The Foresight Engine

`ForesightEngine` in `agent_baton/core/engine/foresight.py` is the engine that inserts preparatory phases you did not explicitly ask for but that are necessary for success. It runs as part of DecompositionStage (step 9.7 in the legacy numbering, before shared context is assembled).

The engine maintains seven built-in rules, each with a `rule_id`, trigger keywords, trigger agents, a confidence score, and a `resolution_template`. When a rule matches a step description and the assigned agent, the engine inserts a new phase before the triggering phase. Examples:

- **`foresight-migration-rollback`** (confidence 0.9) — any step mentioning "migrate", "alter table", or "drop column" triggers insertion of a "Prepare: Migration Safety" phase that sets up reversible scripts and pre-migration backups before the migration runs.
- **`foresight-api-schema`** (confidence 0.8) — API endpoint steps trigger an architect-led "Prepare: API Schema" phase to define request/response schemas before implementation begins.
- **`foresight-destructive-safety`** (confidence 0.85) — steps mentioning "delete", "drop", "truncate", or "purge" trigger a safety-check phase adding dry-run mode and audit logging.
- **`foresight-integration-contract`** (confidence 0.75) — cross-domain integration steps trigger an architect-led contract-definition phase.

Higher-risk plans lower the confidence threshold (from 0.7 to 0.5 for HIGH/CRITICAL), so more rules fire when the stakes are higher. Duplicate insertions for the same rule are collapsed into a single preparatory phase.

### CLI surface and key flags

```bash
# Preview the plan and cost forecast without saving:
baton plan "add OAuth2 login" --dry-run

# Generate, save, and explain the rationale:
baton plan "add OAuth2 login" --save --explain

# Override complexity if the classifier gets it wrong:
baton plan "move one config key" --complexity light --save

# Plan against a completion condition; engine amends until met:
baton goal "all four integration tests pass" --max-amend-cycles 3

# Import a hand-crafted plan instead of auto-generating:
baton plan --import my-plan.json --save

# Surgically fix a saved plan without regenerating:
baton plan-edit --swap-agent 1.1 backend-engineer--python
baton plan-edit --set-risk HIGH
baton plan-edit --add-phase Review --add-agent code-reviewer
```

`--dry-run` renders a compact preview: phases, steps, assigned agents, gates that will block, and a cost forecast with an explicit ±50% confidence band. It exits without writing anything. `--explain` writes a human-readable rationale file to `.claude/team-context/explanation.md` covering pattern influence, risk signals, foresight insertions, and agent routing notes. Both flags are mutually exclusive with `--save`.

### Cost forecasting

`agent_baton/core/engine/cost_estimator.py` sums per-step token allowances using role-specific baselines (architect/code-reviewer: 8 000 tokens; backend-engineer/frontend-engineer/test-engineer: 5 000; everything else: 4 000) and multiplies by a blended I/O price per model family. The output always includes the ±50% band — the comment in `plan_cmd.py` at line 359 references `bd-47b4` as the decision to surface this band explicitly so "developers do not treat the dollar figure as authoritative."

### Optional LLM plan-quality review

After the deterministic pipeline, an optional LLM pass is available via `BATON_PLAN_REVIEW=haiku|sonnet|opus`. When enabled, the plan is sent to the selected model for structural quality review (step splitting, dependency gaps, scope balance). The result can add parallel steps, team steps, or dependency edges. The feature is off by default; `sonnet` is the recommended setting for unattended planning.

### Goal-driven amendment loop

`baton goal "<condition>"` stamps a `completion_condition` on the plan and a `max_amend_cycles` budget (default: 3). After each gate passes, `ExecutionEngine._evaluate_goal_after_gate` calls the `GoalEvaluator`. If the goal is not yet met and the amend budget remains, `amend_plan` appends new phases and execution continues. If the budget is exhausted, the engine emits `FAILED` with reason `"goal not met, amend budget exhausted"`. The `BATON_GOAL_EVALUATOR` variable selects the evaluation strategy: `stub` (deterministic, no LLM), `haiku`, or `opus`.

## The gap today

### 1. Classifier accuracy depends on API key availability

The primary classification path uses `TalentAgentClassifier` (Sonnet via the `claude` CLI), which brings broad language understanding to task-type, complexity, and agent-roster decisions. When `ANTHROPIC_API_KEY` is unset or the `claude` CLI is unavailable, `FallbackClassifier` silently degrades to `KeywordClassifier`, a deterministic keyword-scoring implementation in `agent_baton/core/engine/classifier.py`.

`KeywordClassifier` works well for clearly phrased, single-domain tasks but has known failure modes: ambiguous task descriptions that require reading intent rather than matching keywords, tasks that describe the mechanism rather than the goal ("update the config value" instead of "rename the env var"), and tasks that span multiple types simultaneously. The fallback path logs a warning but does not block — the user may not realize they received a lower-quality plan.

**What would close this gap:** make the fallback explicit in `--dry-run` output and surface it as a plan defect in `ValidationStage`. Consider expanding `KeywordClassifier`'s test coverage to characterize its known blind spots.

### 2. Complexity assessment has known limits

The CLAUDE.md for this repo states directly: "The deterministic pipeline has known limits in complexity assessment." `KeywordClassifier` uses regex signals (`_LIGHT_QUANTIFIERS`, `_HEAVY_SCOPE`, `_HEAVY_ARCH`) and gives no weight to project-specific context: a task that is "light" in a greenfield project may be "heavy" in a deeply coupled legacy codebase. The planner has no mechanism to read existing code complexity or dependency graphs before assigning the tier.

Compensating controls exist: the default planner quality gate blocks structurally defective plans (empty phases, role mismatches, missing Review or Audit coverage), and the spec queue provides pre-flight human review. The `--complexity` flag is an explicit escape hatch. But neither control addresses the root cause — the classifier does not know what "complex" means for your codebase.

**What would close this gap:** inject a static analysis signal (cyclomatic complexity, file coupling metrics) as a hint to the complexity stage, or route complexity assessment through the LLM classifier unconditionally when the API key is available.

### 3. Cost forecast is a coarse ±50% estimate

The cost estimator uses fixed per-role baselines and does not account for: knowledge pack size (which dominates token usage on document-heavy tasks), retries triggered by gate failures, INTERACT phases that can span many turns, or actual model pricing changes. The `bd-47b4` note in the source code acknowledges this directly — the ±50% band is stated on every forecast output precisely because the estimate is not reliable enough to omit it.

The wall-clock estimate adds agent minutes and gate minutes using heuristic constants (`estimate_gate_seconds`) that do not vary by project environment.

**What would close this gap:** calibrate the baseline estimates against retrospective actuals (the `observe` + `learn` subsystem already collects per-step token usage); feed that history back into `cost_estimator.py` so the bands tighten over time.

### 4. Foresight rules are limited to seven built-in patterns

`ForesightEngine` ships with seven rules covering data CRUD completeness, migration rollback safety, API schema validation, destructive operation safety, infrastructure environment preparation, integration contract definition, and test infrastructure scaffolding. These rules fire on keyword matching and agent-name matching — there is no semantic understanding of what a step actually does. A step titled "handle user records" that internally drops columns will not trigger `foresight-migration-rollback` unless the keywords appear in the step description.

There is no mechanism to author custom foresight rules per project. Plans that touch domains outside the seven built-in categories (e.g. embedded systems, custom DSLs, regulated clinical workflows) will not receive foresight insertions.

**What would close this gap:** expose a foresight rule extension point in assurance packs (analogous to how packs already extend the risk classifier's keyword lists); provide a `baton plan --explain` section that lists which foresight rules fired and which did not, so gaps in coverage are visible.

### 5. `BATON_PLAN_REVIEW` is off by default

The optional LLM post-pipeline review can catch structural quality issues — overly broad single-step phases, missing dependency edges, scope imbalance — that the deterministic pipeline cannot assess. But it is disabled by default because it adds latency and API cost. This means most plans in the wild ship without the review, including plans generated in CI pipelines or managed-mode automation where human review is unlikely.

The CLAUDE.md table notes: "The deterministic pipeline has known limits in complexity assessment; default compensating controls are the structural hard gate and pre-flight human review in the spec queue — enable this for unattended/managed-mode planning."

**What would close this gap:** run the LLM review automatically when `BATON_GOAL_EVALUATOR` is already set (i.e. the user has opted into LLM-backed evaluation), or default to `haiku` review for `heavy` complexity plans where the deterministic pipeline's limits are most likely to matter.

## Where this lives

- Docs: [../engine-and-runtime.md](../engine-and-runtime.md), [../architecture/state-machine.md](../architecture/state-machine.md), [../orchestrator-usage.md](../orchestrator-usage.md)
- Code: `agent_baton/core/engine/planning/` (full pipeline), `agent_baton/core/engine/foresight.py` (Foresight Engine), `agent_baton/core/engine/cost_estimator.py` (cost forecast), `agent_baton/core/engine/classifier.py` (task classifier + fallback), `agent_baton/core/govern/classifier.py` (risk/data classifier), `agent_baton/core/engine/plan_reviewer.py` (structural reviewer)
- Commands: `baton plan --dry-run`, `baton plan --explain`, `baton plan --save`, `baton plan-edit`, `baton goal`
