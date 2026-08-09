# Adversarial-TDD Workflow — design spec

Status: v2 (amended after two independent fable-tier architecture reviews) → implementing
Owner: maintainers
Date: 2026-08-09

## 1. Problem

Baton has no way to ask for a **named, repeatable delivery workflow**. The
planner's archetypes (`direct`, `phased`, `investigative`) carry only phase
names and gate policies — they cannot express *which role runs each stage, at
which model tier, with which scoping rules*. The most effective real-world
workflow used with baton today is a staged, model-tiered, adversarially
verified TDD pipeline:

1. **Brainstorm & spec** — frame the capability, write the initial spec.
2. **Architecture** — top-tier (fable) agents design the solution.
3. **Test authoring (TDD)** — opus agents write failing tests first.
4. **Test verification** — *differently scoped* opus agents verify the tests
   actually pin the behaviors we intend (they see spec + tests, not the
   implementation plan).
5. **Implementation** — sonnet agents implement until the tests pass
   (sonnet agents also serve as researchers at any earlier stage).
6. **Implementation verification** — opus agents (or external-vendor
   verifiers such as gemini/codex CLIs) independently verify the
   implementation against the spec, beyond "tests pass".
7. **Final review** — fable agent(s) review the whole slice; multiple
   reviewers when the slice is large.

This spec makes that workflow available **out of the box** as
`baton plan "<task>" --workflow adversarial-tdd`, with a generic preset
mechanism so more named workflows can be added later.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Post-processor, not pipeline stage.** A `WorkflowApplier` reshapes the assembled `MachinePlan` after `IntelligentPlanner.create_plan()`. | Same locked decision as manager mode (`docs/internal/manager-mode-pmo-design.md` #4). The 7-stage pipeline still does classification, roster, risk, decomposition — the workflow re-shapes its output. Zero protocol changes. |
| 2 | **Model tiers are stamped on `PlanStep.model` — and on `TeamMember.model`, recursively through `sub_team` — by the applier.** | `step.model` flows to DISPATCH (`dispatcher.py:1143`); team members dispatch with `member.model` (`executor.py:7943`), so members must be re-tiered too. Stamping after planning wins over the enrichment-stage frontmatter overwrite (`planning/stages/enrichment.py:326-339`). `PlanStep.model` is unvalidated, so `fable` works today at the step level. `SynthesisSpec.synthesis_agent` dispatches are out of scope for re-tiering (v1). |
| 3 | **Presets are code-defined data**, in `agent_baton/core/workflow/presets.py` (frozen dataclasses + registry), not JSON templates. | The staged shape depends on the planned task (implementation steps come from the base plan), so a static plan JSON cannot express it. `templates/learning-cycle-plan.json` remains the precedent for fully static plans. |
| 4 | **Pure & idempotent applier.** `WorkflowApplier.apply(plan, preset, settings, fallback_gate=None)` does no clock reads and no filesystem/network IO; safe to call twice. Any IO-dependent input (the stack-detected fallback gate) is computed by the CLI wiring layer and passed in. Idempotency marker: `MachinePlan.workflow` + non-empty `PlanStep.workflow_stage`. | Mirrors `PhasePolicyApplier` (`core/manager/phase_policy.py`). `default_gate` does filesystem IO, so it cannot be called from inside the applier. |
| 5 | **Two new plan-model fields, declared + serialized**: `MachinePlan.workflow: str = ""`, `PlanStep.workflow_stage: str = ""`. Emitted by `to_dict()` only when non-empty (golden-fixture compatible). The applier also records its `WorkflowDecisions` under `plan_diagnostics["workflow"]`. **The SQLite copy of the plan is lossy for these fields in v1** (column-mapped `_upsert_plan` has no such columns); `plan.json` is canonical, and the PMO UI does not display workflow stages in v1. | Models use `extra="ignore"` + hand-written `to_dict` allow-lists — undeclared fields do not survive the plan.json round-trip. DB columns + migration deferred to a PMO-UI increment. |
| 6 | **Vendor-neutral external verification via an automation step.** Config `workflow.external_command` (baton.yaml); when set, the applier appends a `step_type="automation"` step (`agent_name="task-runner"`, `command=<external_command>`, `timeout_seconds` from config) to the Implementation Verification phase. **v1 constraint:** `baton execute run`'s automation runner caps commands at 300s (`execute.py`/`worker.py` hardcode `timeout=300`); `timeout_seconds` is stamped for forward-compatibility but not yet honored there. Documented. | Baton has no vendor abstraction (`claude_launcher.py` is claude-only). An automation step is the engine-native way to run *any* CLI — `gemini`, `codex`, a script — with zero engine changes. |
| 7 | **`--workflow` is mutually exclusive with `--manager-mode` and `--import` in v1** (typed CLI error, exit 2). | Manager mode's `PhasePolicyApplier` keys idempotency solely on the `review-` step-id prefix and would inject adversarial-review steps into all 7 workflow phases — including the three that *are* reviews. Composition is deferred until `_has_review_step` understands `workflow_stage`. `--import` bypasses `create_plan()`; reshaping imported plans is untested territory. |
| 8 | **One new agent: `test-adequacy-reviewer` (opus, reviewer class).** Implementation verification reuses `code-reviewer` pinned to opus; final review reuses `code-reviewer` pinned to fable. No planner-rules registration (`REVIEWER_AGENTS`, `step_types.py`) in v1 — the applier stamps `step_type` explicitly and the planner never seats this agent on its own. No `_VALID_MODELS`/fable validator change — the validator only checks agent frontmatter, and no shipped agent uses `model: fable`. | Test-vs-behavior verification is a genuinely distinct role with distinct scoping. Minimal global blast radius. |
| 9 | **CLI**: `baton plan --workflow NAME` (+ passthrough on `baton goal`). Unknown name → `UnknownWorkflowError` caught by the CLI and rendered as a validation error listing available presets (exit 2). A standalone `baton workflows` list command is deferred to v2; discoverability = docs + `--explain` + the error listing. | Mirrors `--manager-mode` wiring. Smallest surface that satisfies out-of-the-box use. |
| 10 | **Application point**: immediately after `create_plan()`, before the team-resumability check, goal stamping, and all three consumer branches (`--dry-run`, `--save`, print). | Dry-run cost forecasts, resumability warnings, and saved plan.json must all reflect the reshaped, re-tiered plan. `BATON_PLAN_REVIEW` runs *inside* `create_plan()` and therefore reviews the pre-reshape plan — documented behavior. |
| 11 | **Errors**: `UnknownWorkflowError(RuntimeError)` lives in `core/workflow/presets.py` (engine-errors style; `core/` cannot import `cli/errors.BatonError`). | Dependency-arrow rule in `core/CLAUDE.md`. |

## 3. The `adversarial-tdd` preset (built-in defaults)

| # | Stage id | Phase name | Agent (default) | Model | step_type | Scope rules (briefing) |
|---|----------|-----------|------------------|-------|-----------|------------------------|
| 1 | `spec` | Brainstorm & Spec | `architect` | `fable` | planning | Writes `.claude/team-context/executions/<task_id>/spec.md`: capability framing, behaviors, non-goals, acceptance criteria. |
| 2 | `architecture` | Architecture | `architect` | `fable` | planning | Reads spec; produces architecture notes + file-boundary map for implementers. |
| 3 | `test_authoring` | Test Authoring | `test-engineer` | `opus` | testing | Writes failing tests that encode the spec's behaviors. Must not write implementation code. |
| 4 | `test_verification` | Test Verification | `test-adequacy-reviewer` | `opus` | reviewing | Differently scoped: reads **spec + tests only** — verifies tests pin the intended behaviors; flags missing/vacuous/tautological tests. `context_files` carries only the spec path; the briefing instructs locating the tests from the test-authoring step's commit/deliverables. |
| 5 | `implementation` | Implementation | *base plan's harvested implement steps* | `sonnet` | (preserved) | The steps the pipeline planned, flattened into one phase in original order (agents, descriptions, deps re-keyed, all other fields preserved), re-tiered to sonnet. Gate: test gate (green). |
| 6 | `implementation_verification` | Implementation Verification | `code-reviewer` | `opus` | reviewing | Verifies implementation against spec beyond tests-pass. Optional extra automation step running `external_command`. |
| 7 | `final_review` | Final Review | `code-reviewer` | `fable` | reviewing | Whole-slice review with fan-out (§5.8). The plan's **last non-carryover phase must be named "…Review"** (recorded invariant; audit-carryover phases may follow it). |

Notes:

- **Research support**: stage briefings state that the orchestrator may
  dispatch sonnet `general-purpose`/domain agents for research at any stage;
  briefing guidance, not extra planned steps.
- **Gates/approvals — exact preservation contract**: the applier never
  removes the plan's test/build gate (it moves to the Implementation phase)
  or its final approval requirement (`final_review.approval_required` =
  OR of all base phases' `approval_required`). Per-phase gates on discarded
  base phases do not survive. Stages 1–4 and 6–7 have `gate=None` in v1.
- **Audit carryover (compliance)**: base phases that were *not* harvested
  and contain at least one `auditor` step are appended **after** Final
  Review, steps and gate/approval preserved verbatim (ids renumbered,
  `workflow_stage="carryover"`). The planner's regulated-domain hard gate
  (`audit_missing`) mandated them; the workflow must not strip them.
- **Dependencies**: phases execute in engine order; intra-implementation
  `depends_on` re-keyed to new step ids; no cross-phase step deps.
- **Step ids** renumbered numerically `"<phase_id>.<n>"` (plan-graph
  invariants: unique across plan, no forward refs).

## 4. Architecture & public API

```
baton plan "<task>" --workflow adversarial-tdd [--save|--dry-run|--explain]
  └─ IntelligentPlanner.create_plan()          (unchanged 7-stage pipeline)
  └─ get_workflow_preset(name)                  core/workflow/presets.py
  └─ load_workflow_settings(...)                core/config/workflow.py
  └─ fallback_gate = default_gate(...)          computed in CLI wiring (IO here, not in applier)
  └─ WorkflowApplier.apply(plan, preset, settings, fallback_gate=...)
  └─ resumability check / goal stamping / dry-run | save | print
```

### `agent_baton/core/workflow/presets.py`

```python
@dataclass(frozen=True)
class WorkflowStage:
    stage_id: str              # "spec", "architecture", ...
    phase_name: str
    agent_name: str            # default agent (ignored for harvesting stage)
    model: str                 # default tier: haiku|sonnet|opus|fable
    step_type: str             # stamped on applier-CREATED steps only
    briefing_template: str     # str.format-style; placeholder: {task_summary}
    harvests_implementation: bool = False   # exactly one stage per preset

@dataclass(frozen=True)
class WorkflowPreset:
    name: str
    description: str
    stages: tuple[WorkflowStage, ...]

class UnknownWorkflowError(RuntimeError): ...
ADVERSARIAL_TDD: WorkflowPreset            # name == "adversarial-tdd"
def get_workflow_preset(name: str) -> WorkflowPreset   # raises UnknownWorkflowError
def list_workflow_presets() -> list[WorkflowPreset]
```

### `agent_baton/core/config/workflow.py`

Pydantic, `extra="ignore"`, `Literal` tiers, `manager.py` loader conventions
(defaults < `~/.baton/config.yaml` < project `baton.yaml`), top-level key
`workflow:`. **Also**: add `"workflow"` to `manager.py`'s known/sibling-owned
top-level keys so ManagerConfig stops warning about it.

```python
class StageOverride(_Section):
    agent: str | None = None
    model: Literal["haiku", "sonnet", "opus", "fable"] | None = None

class WorkflowSettings(_Section):
    stages: dict[str, StageOverride] = {}
    external_command: str = ""
    external_timeout_seconds: int = 1800
    final_review_fanout_divisor: int = 4
    final_review_max_reviewers: int = 3

def load_workflow_settings(project_root: Path | None = None) -> WorkflowSettings
```

### `agent_baton/core/workflow/applier.py`

```python
@dataclass
class WorkflowDecisions:
    workflow: str
    stages: list[dict[str, Any]]   # ⊇ {stage_id, phase_name, agents, model, steps};
                                   # "model" is the EFFECTIVE tier (after overrides)
    implementation_units: int
    final_review_reviewers: int
    external_verifier: bool
    carried_over_phases: list[str]

class WorkflowApplier:
    def apply(self, plan, preset, settings, *, fallback_gate=None) -> WorkflowDecisions
```

### Touched existing files

- `agent_baton/models/execution.py` — `MachinePlan.workflow`,
  `PlanStep.workflow_stage`; conditional `to_dict()` emission; shown in
  `to_markdown()` when set.
- `agent_baton/cli/commands/execution/plan_cmd.py` — `--workflow NAME` flag;
  mutual-exclusion guards; application per decision #10; `--explain` section
  rendering `WorkflowDecisions`.
- `agent_baton/cli/commands/goal_cmd.py` — passthrough `--workflow`.
- `agent_baton/core/config/manager.py` — recognize `workflow` as a sibling key.
- `agents/test-adequacy-reviewer.md` + `docs/agent-roster.md` +
  `scripts/sync_bundled_agents.sh` run.
- Docs: `docs/cli-reference.md`, `docs/orchestrator-usage.md` (recipe),
  `references/baton-engine.md` (plan flag table),
  `templates/baton.yaml.example` (workflow section example),
  `templates/playbooks/adversarial-tdd.md` (PMO gallery companion).

## 5. Applier semantics (normative)

1. **No-op guard**: if `plan.workflow == preset.name` and any step has a
   non-empty `workflow_stage`, recompute and return `WorkflowDecisions` from
   the already-shaped plan with **zero writes** — including
   `plan_diagnostics` (decisions are not persisted separately;
   `plan_diagnostics["workflow"]` holds the last-applied record).
2. **Harvest implementation steps.** A base phase is *implementation-like*
   iff its name, normalized by last-word keying (cf.
   `ValidationStage._phase_key`), maps **explicitly** to
   {IMPLEMENTATION, PREPARATION, REMEDIATION} in
   `models/taxonomy.py`'s name→archetype table. The unrecognized-name
   fallback to IMPLEMENTATION must NOT be used (compound names like
   "Security Review" would otherwise be harvested). From those phases,
   harvest steps with `step_type not in {"reviewing", "planning"}`.
   Fallbacks, in order: (a) if no phase qualifies **or the qualifying
   phases yield no harvested steps**, harvest all steps with
   `step_type == "developing"` anywhere in the plan; (b) if still empty,
   synthesize one implementation step from the implement-phase fallback
   agent (`backend-engineer`), briefed from the task summary.
3. **Discard rule (explicit)**: all base steps not harvested per rule 2 and
   not carried over per rule 7 are discarded; their work is assumed
   re-covered by the preset stages. `plan_diagnostics` entries computed on
   the pre-reshape plan may reference discarded step ids — known-stale,
   `plan_diagnostics["workflow"]` marks the reshape.
4. **Rebuild `plan.phases`** in preset stage order; implementation steps are
   flattened into the harvesting stage's single phase in original order.
5. **Field preservation (inverted rule)**: harvested steps preserve **all**
   fields except: `step_id` (renumbered), `depends_on` (re-keyed),
   `workflow_stage` (stamped), and `model` (stamped to the stage tier —
   including `TeamMember.model` recursively through `sub_team` — **except**
   steps with `step_type` in {"automation", "task"}, whose model is unused
   and left untouched). `step_type` is stamped only on applier-created
   steps, never on harvested ones. `TeamMember.member_id` values are
   preserved verbatim (NOT re-keyed to the renumbered step id — member
   `depends_on` references member ids, so re-keying is not free; v1 keeps
   them stable).
6. **Gates**: the Implementation phase gets the first test/build gate found
   on any base phase, else `fallback_gate` (may be None). Final Review
   `approval_required` = OR of all base phases' `approval_required`.
7. **Audit carryover**: per §3 — non-harvested base phases containing an
   `auditor` step are appended after Final Review, fully preserved,
   renumbered, steps stamped `workflow_stage="carryover"`.
8. **Final-review fan-out**: implementation units =
   Σ over harvested steps of `max(1, len(step.team))`. Reviewer count =
   `min(final_review_max_reviewers, max(1, ceil(units / final_review_fanout_divisor)))`
   — the formula is exact; it is NEVER clamped by the harvested *step*
   count. Partitioning is by **dispatch unit** (a team step contributes
   one unit per member, carrying the member's task description and the
   step's `allowed_paths`; a plain step is one unit): units are
   partitioned contiguously into reviewer-count groups, and each
   reviewer's briefing embeds exactly its group's descriptions and
   `allowed_paths`.
9. **External verifier**: if `settings.external_command` is non-empty,
   append to the Implementation Verification phase a
   `PlanStep(step_type="automation", agent_name="task-runner",
   command=settings.external_command,
   timeout_seconds=settings.external_timeout_seconds,
   workflow_stage="implementation_verification")` (model left default —
   automation dispatch bypasses it).
10. **Stamping**: every workflow-created step gets `workflow_stage=<stage_id>`;
    `plan.workflow = preset.name`;
    `plan_diagnostics["workflow"] = decisions` (JSON-serializable dict).
11. **Overrides**: `settings.stages[stage_id].agent/model` override preset
    defaults for created steps; for the harvesting stage, only `model`
    applies (agents come from the base plan).
12. **Validity**: output must satisfy `MachinePlan` model validators
    (unique ids, no forward deps, non-empty agent names). A violation is an
    applier bug, not a user error.

### CLI interaction matrix (v1)

| Combination | Behavior |
|---|---|
| `--workflow` + `--manager-mode` | exit 2, typed error (decision #7) |
| `--workflow` + config `manager_mode.enabled_by_default: true` | manager mode is **suppressed with a printed warning** for this plan (only the explicit flag combination is an error) — otherwise the config default would run `PhasePolicyApplier` over the reshaped plan, the exact failure decision #7 prevents |
| `--workflow` + `--import` | exit 2, typed error |
| `--workflow` + `--dry-run` | applier runs first; forecast reflects reshaped plan & fable pricing |
| `--workflow` + `--agents` | forced agents survive only in harvested implementation steps; stages 1–4/6–7 use preset/override agents |
| `--workflow` + `--goal` | composes; amend-cycle phases appended mid-execution carry no `workflow_stage` (documented) |
| `--workflow` + unknown name | exit 2, error lists `list_workflow_presets()` names |

## 6. `test-adequacy-reviewer` agent (new)

- Frontmatter: `name: test-adequacy-reviewer`, `model: opus`,
  `tools: Read, Glob, Grep, Bash`, `permissionMode: default`, reviewer color.
- Mission: given a spec (behaviors/acceptance criteria) and a test diff,
  answer: do these tests *pin* the behaviors? Are any vacuous, tautological,
  over-mocked, or testing implementation details? Which behaviors have no
  test? Verdict: PASS/FAIL + behavior→test coverage table.
- Explicitly instructed NOT to read implementation plans or source-to-be.

## 7. Non-goals (v1)

- No vendor/provider abstraction (external verification = automation steps).
- No path-level sandbox enforcement of test-verification scoping.
- No manager-mode composition; no `--from-template` revival; no
  `baton workflows` command; no PMO-UI surface; no DB columns for the new
  fields (plan.json canonical); no automation-timeout engine change.
- No changes to the execution engine, protocol, or `_print_action()`.
- Runtime assumption (documented): the installed Claude Code runtime
  resolves the `fable` model alias at dispatch. Smoke-test before relying
  on fable-pinned stages in production.

## 8. Test plan

`tests/workflow/` (mirrors `core/workflow/`):

- `test_presets.py` — registry get/list; unknown-name error type/message;
  `adversarial-tdd` stage table matches §3 (ids, names, agents, tiers,
  step types, order, single harvesting stage).
- `test_applier.py` —
  - reshapes a representative phased plan into the 7 stages in order;
  - harvested steps: agents/descriptions/deliverables/paths preserved,
    deps re-keyed, models re-tiered to sonnet;
  - **team step**: `TeamMember.model` (and nested `sub_team`) re-tiered;
    counts as `len(team)` units for fan-out;
  - harvested automation/task steps keep step_type and are not re-tiered;
  - harvest predicate: "Security Review" phase NOT harvested; "Fix"/"Build"
    phases harvested; reviewer steps inside implement-like phases excluded;
  - fable/opus/sonnet stamped per §3; `workflow_stage` on every step;
    `plan.workflow` stamped; `plan_diagnostics["workflow"]` recorded;
  - idempotent (second apply → no duplicate phases/steps, same shape);
  - round-trip `MachinePlan.from_dict(to_dict())` preserves both new fields;
  - fan-out: 8 units → 2 reviewers; cap 3 respected; 1 unit → 1 reviewer;
  - no harvestable steps → synthesized fallback implementation step;
  - audit carryover: base Audit phase (auditor step) appended after Final
    Review with gate/approval preserved, `workflow_stage="carryover"`;
  - approval OR-rule; gate moved onto Implementation phase; fallback_gate
    used when base plan has none;
  - external_command → automation step appended with timeout stamped;
  - graph invariants hold on output.
- `tests/config/test_workflow_settings.py` — baton.yaml overrides
  (stage agent/model), invalid tier → actionable validation error, missing
  file → defaults, ManagerConfig no longer warns on `workflow:` key.
- `tests/models/` — round-trip + absent-when-empty for the two new fields.
- `tests/cli/test_plan_workflow_flag.py` — `--workflow adversarial-tdd
  --json` end-to-end shape; unknown workflow exit 2 listing presets;
  `--manager-mode`/`--import` mutual exclusion; `baton goal` passthrough;
  **baton.yaml wiring e2e** (stage model override + external_command reach
  the plan via `load_workflow_settings` — acceptance #4); **fallback-gate
  wiring** (gateless base plan → Implementation phase carries a
  stack-derived gate); `--dry-run` reflects the reshaped plan;
  `--explain` renders the `WorkflowDecisions` section; config-default
  manager mode suppressed with a warning under `--workflow`.

Adversarial-verification addenda (must be pinned):

- Harvest normalization: phases named `"Implement: API Layer"` and
  `"Backend Implementation"` ARE harvested (last-word/colon-strip keying);
  `Prepare`/`Remediate` phases (PREPARATION/REMEDIATION archetypes) ARE
  harvested; a `planning` step inside an implement-like phase is excluded.
- Idempotency guard differentiator: second `apply()` with *different*
  settings (e.g. external_command now set) leaves the plan unchanged —
  separates the §5.1 guard from an accidental fixed-point re-apply.
- Stage-1/stage-4 scoping: spec step's briefing/deliverables name
  `.claude/team-context/executions/<task_id>/spec.md`; test-verification
  step's `context_files` == [spec path] and its briefing references the
  test-authoring deliverables.
- A `build`-type gate is also accepted by the §5.6 "first test/build gate"
  rule; auditor step inside a HARVESTED phase does not trigger carryover;
  cross-base-phase deps between two harvested steps are re-keyed.

## 9. Acceptance criteria

1. `baton plan "add rate limiting" --workflow adversarial-tdd --save`
   writes (at save time) a plan.json whose phases are the §3 stages (plus
   any audit carryover), per-step models
   `fable/fable/opus/opus/sonnet/opus/fable`, and
   `"workflow": "adversarial-tdd"` at the plan root.
2. `baton execute start` + the existing action loop dispatches those steps
   with `Model:` lines matching the stamped tiers — zero engine changes.
3. Applying the applier twice never duplicates stages (idempotent).
4. baton.yaml can retarget any stage's agent/model and plug an external
   verifier command without code changes.
5. All existing planner/CLI tests stay green; plans without `--workflow`
   are byte-identical to before (both fields absent from JSON).
