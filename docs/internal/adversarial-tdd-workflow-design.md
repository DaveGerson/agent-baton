# Adversarial-TDD Workflow — design spec

Status: draft → implementing
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
`baton plan "<task>" --workflow adversarial-tdd`, and makes the mechanism
generic so more named workflows can be added later.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Post-processor, not pipeline stage.** A `WorkflowApplier` reshapes the assembled `MachinePlan` after `IntelligentPlanner.create_plan()`. | Same locked decision as manager mode (`docs/internal/manager-mode-pmo-design.md` #4). The 7-stage pipeline still does classification, roster, risk, decomposition — the workflow re-shapes its output. Zero protocol changes. |
| 2 | **Model tiers are stamped on `PlanStep.model` by the applier.** | `step.model` already flows to DISPATCH (`dispatcher.py:1143` → `agent_model` → `Model:` line). Stamping after planning wins over the enrichment-stage frontmatter overwrite (`planning/stages/enrichment.py:326-339`) without touching precedence rules. `PlanStep.model` is unvalidated, so `fable` works today at the step level. |
| 3 | **Presets are code-defined data**, in `agent_baton/core/workflow/presets.py` (frozen dataclasses + registry), not JSON templates. | The staged shape depends on the planned task (implementation steps come from the base plan), so a static plan JSON cannot express it. `templates/learning-cycle-plan.json` remains the precedent for fully static plans; this feature is for *task-shaped* workflows. |
| 4 | **Pure & idempotent applier.** `WorkflowApplier.apply(plan, preset, settings)` is a pure function of its inputs — no clock, no IO, safe to call twice. Idempotency marker: `PlanStep.workflow_stage` (new declared field) + `MachinePlan.workflow`. | Mirrors `PhasePolicyApplier` (`core/manager/phase_policy.py`) which uses the `review-` step-id prefix. A declared field is more robust than an id prefix and is useful to the UI/trace. |
| 5 | **Two new plan-model fields, declared + serialized**: `MachinePlan.workflow: str = ""`, `PlanStep.workflow_stage: str = ""`. Emitted by `to_dict()` only when non-empty (golden-fixture compatible). | Models use `extra="ignore"` + hand-written `to_dict` allow-lists — undeclared fields do not survive the plan.json round-trip. |
| 6 | **Vendor-neutral external verification via an automation step.** Config `workflow.implementation_verification.external_command` (baton.yaml); when set, the applier appends a `step_type="automation"` step running that command in the verification phase. | Baton has no vendor abstraction (`claude_launcher.py` is claude-only). An automation step is the existing engine-native way to run *any* CLI — `gemini`, `codex`, a script. Extensible without inventing a provider layer. |
| 7 | **`fable` becomes a valid agent-frontmatter model tier** (`govern/validator.py` `_VALID_MODELS`, `agents/CLAUDE.md`). No shipped agent switches to fable frontmatter; the workflow pins fable per step. | Pricing/cost layers already know fable. One-line enablement; avoids making every `architect` dispatch fable-priced outside this workflow. |
| 8 | **One new agent: `test-adequacy-reviewer` (opus, reviewer class).** Implementation verification reuses `code-reviewer` pinned to opus; final review reuses `code-reviewer` pinned to fable. | Test-vs-behavior verification is a genuinely distinct role with distinct scoping (must NOT read the implementation plan). The other stages map cleanly onto existing roster roles. |
| 9 | **CLI**: `baton plan --workflow NAME` (+ passthrough on `baton goal`), and a new `baton workflows` command to list/inspect presets. Unknown name → typed error listing available presets. | Mirrors `--manager-mode` wiring (`plan_cmd.py:76-86`, applied pre-save so plan.json reflects the final shape). Discoverability via `baton workflows`. |
| 10 | **Composable with manager mode and `--goal`.** Order: workflow applier runs first, then manager-mode `PhasePolicyApplier` (which is idempotent and skips phases that already carry review steps of its own marker). `--goal` stamps fields orthogonally. | Both are post-processors; a deterministic order avoids ambiguity. |

## 3. The `adversarial-tdd` preset (built-in defaults)

Stage table (defaults; every row overridable via `baton.yaml` `workflow:` section):

| # | Stage id | Phase name | Agent (default) | Model | step_type | Scope rules |
|---|----------|-----------|------------------|-------|-----------|-------------|
| 1 | `spec` | Brainstorm & Spec | `architect` | `fable` | planning | Writes `.claude/team-context/executions/<task>/spec.md`: capability framing, behaviors, non-goals, acceptance criteria. |
| 2 | `architecture` | Architecture | `architect` | `fable` | planning | Reads spec; produces architecture notes + file-boundary map for implementers. |
| 3 | `test_authoring` | Test Authoring (TDD) | `test-engineer` | `opus` | testing | Writes failing tests that encode the spec's behaviors. Must not write implementation code. |
| 4 | `test_verification` | Test Verification | `test-adequacy-reviewer` | `opus` | reviewing | Differently scoped: reads **spec + tests only** — verifies tests pin the intended behaviors, flags missing/vacuous/tautological tests. |
| 5 | `implementation` | Implementation | *base plan's implement steps* | `sonnet` | developing | The steps the pipeline planned (agents, task descriptions, deps, paths preserved), re-tiered to sonnet. Gate: stack test command (green). |
| 6 | `implementation_verification` | Implementation Verification | `code-reviewer` | `opus` | reviewing | Verifies implementation against spec beyond tests-pass. Optional extra automation step running `external_command` (gemini/codex/etc.). |
| 7 | `final_review` | Final Review | `code-reviewer` | `fable` | reviewing | Whole-slice review. Fan-out: `ceil(implementation_steps / fanout_divisor)` reviewers (default divisor 4, cap 3), each briefed on a distinct slice. |

Notes:

- **Research support**: stage briefings state that the orchestrator may
  dispatch sonnet `general-purpose`/domain agents for research at any stage;
  this is briefing guidance, not extra planned steps.
- **Gates**: implementation phase keeps/receives the stack-detected `test`
  gate (`planning/utils/gates.py::default_gate`). Final phase keeps any
  approval requirement from risk policy; the applier never *removes* gates
  or approvals, only phases/steps are reshaped.
- **Dependencies**: phases execute in order (engine phase progression);
  intra-phase `depends_on` from the base plan's implement steps is preserved
  (re-keyed to new step ids). No forward references (model validator).
- **Step ids** are renumbered `"<phase>.<n>"` to keep the plan-graph
  invariants (unique across plan).

## 4. Architecture

```
baton plan "<task>" --workflow adversarial-tdd [--save ...]
  └─ IntelligentPlanner.create_plan()          (unchanged 7-stage pipeline)
  └─ get_workflow_preset("adversarial-tdd")     core/workflow/presets.py
  └─ load_workflow_settings(baton.yaml)         core/config/workflow.py
  └─ WorkflowApplier.apply(plan, preset, cfg)   core/workflow/applier.py
       (pure, idempotent — reshapes phases, stamps step.model +
        workflow_stage, stamps plan.workflow)
  └─ [manager mode post-processor, if requested]
  └─ save/print (plan.json reflects the FINAL shape)
```

### New modules

| Path | Contents |
|------|----------|
| `agent_baton/core/workflow/__init__.py` | empty |
| `agent_baton/core/workflow/presets.py` | `WorkflowStage` + `WorkflowPreset` frozen dataclasses; `ADVERSARIAL_TDD` preset; `get_workflow_preset(name)`, `list_workflow_presets()`; `UnknownWorkflowError` (subclass of engine `BatonError`-family) |
| `agent_baton/core/workflow/applier.py` | `WorkflowApplier.apply(plan, preset, settings) -> WorkflowDecisions` (pure record of what was injected/re-tiered, for `--explain`) |
| `agent_baton/core/config/workflow.py` | `WorkflowSettings` (Pydantic, `extra="ignore"`, Literal-typed tiers `haiku\|sonnet\|opus\|fable`): per-stage `{agent, model}` overrides, `external_command`, `final_review_fanout_divisor`, `final_review_max_reviewers`. Loaded from the shared `baton.yaml` under top-level key `workflow:` (manager.py loader conventions). |
| `agent_baton/cli/commands/workflows_cmd.py` | `baton workflows [show NAME]` — lists presets with stage/agent/model table |
| `agents/test-adequacy-reviewer.md` | opus reviewer agent (see §6) |

### Touched existing files

- `agent_baton/models/execution.py` — declare `MachinePlan.workflow`,
  `PlanStep.workflow_stage`; conditional `to_dict()` emission; include in
  `to_markdown()` header when set.
- `agent_baton/cli/commands/execution/plan_cmd.py` — `--workflow NAME` flag;
  apply after `create_plan()` and **before** manager mode and `--save`;
  `--explain` section listing the stage table + decisions.
- `agent_baton/cli/commands/goal_cmd.py` — passthrough `--workflow`.
- `agent_baton/cli/main.py` — register `workflows` in `_COMMAND_GROUPS`.
- `agent_baton/core/govern/validator.py` — add `fable` to `_VALID_MODELS`.
- `agent_baton/core/orchestration/router.py` — add `test-adequacy-reviewer`
  to `REVIEWER_AGENTS`.
- `agent_baton/core/engine/planning/rules/step_types.py` —
  `test-adequacy-reviewer → reviewing`.
- `agents/CLAUDE.md` — model vocabulary now `opus|sonnet|haiku|fable`.
- `scripts/sync_bundled_agents.sh` run (bundled mirror).
- Docs: `docs/cli-reference.md`, `docs/agent-roster.md`,
  `docs/orchestrator-usage.md` (recipe), `references/baton-engine.md`
  (plan flag table), root `CLAUDE.md` reference-architecture row,
  `templates/playbooks/adversarial-tdd.md` (PMO gallery companion).

## 5. Applier semantics (normative)

1. **No-op guard**: if `plan.workflow == preset.name` and any step carries a
   non-empty `workflow_stage`, return existing decisions (idempotent).
2. **Harvest implementation steps**: collect steps from the base plan whose
   phase archetype is implementation-like (`models/taxonomy.py::phase_archetype`
   ∈ {IMPLEMENTATION, PREPARATION, REMEDIATION}) — fallback: all
   `step_type == "developing"` steps; if none exist, synthesize a single
   implementation step from the fallback agent (`backend-engineer`, or the
   stack-routed flavor already chosen by the roster when present).
3. **Rebuild `plan.phases`** in the preset's stage order. Each stage becomes
   one phase (implementation may contain many steps; final review may contain
   many reviewers per the fan-out rule). Step ids renumbered; original
   intra-implementation `depends_on` re-keyed; cross-stage deps expressed via
   phase ordering only.
4. **Stamp** `step.model` (settings override > preset default),
   `step.workflow_stage`, `step.step_type`; preserve harvested steps'
   `task_description`, `allowed_paths`, `context_files`, `knowledge`.
5. **Gates**: implementation phase gets the base plan's test/build gate if
   one existed anywhere, else `default_gate` for the detected stack.
   The last phase inherits `approval_required` from the base plan's last
   phase (risk policy result).
6. **Scoping**: `test_verification` steps get `blocked_paths` covering the
   implementation's `allowed_paths` sources? No — simpler and honest:
   their briefing text instructs spec+tests-only scope, and their
   `context_files` list spec + test paths. Path-level enforcement is a
   non-goal for v1.
7. **External verifier**: if `settings.implementation_verification.external_command`
   is non-empty, append `PlanStep(step_type="automation", command=...,
   agent_name="task-runner", model=<n/a>)` to the verification phase.
8. **Validation**: after reshaping, rely on `MachinePlan` model validators
   (unique ids, no forward deps). The applier must construct compliant
   output; a defect here is a bug, not a user error.
9. **Returned `WorkflowDecisions`**: preset name, per-stage (agents, model,
   step count), fan-out count, whether external verifier was added —
   rendered under `--explain` and by `baton workflows show`.

## 6. `test-adequacy-reviewer` agent (new)

- Frontmatter: `name: test-adequacy-reviewer`, `model: opus`,
  `tools: Read, Glob, Grep, Bash`, `permissionMode: default`, reviewer color.
- Mission: given a spec (behaviors/acceptance criteria) and a test diff,
  answer: do these tests *pin* the behaviors? Are any tests vacuous,
  tautological, over-mocked, or testing implementation details? Which spec'd
  behaviors have no test? Verdict format: PASS / FAIL with a behavior→test
  coverage table.
- Explicitly instructed NOT to read implementation plans or source-to-be —
  it reviews the tests against the spec, not against code.

## 7. Non-goals (v1)

- No vendor/provider abstraction for dispatching non-Claude models as
  first-class agents (external verification goes through automation steps).
- No path-level sandbox enforcement of the test-verification scope.
- No reuse of the dead `--from-template`/`--save-as-template` flags (plan
  JSON templates are a different feature; leaving them untouched).
- No PMO-UI surface beyond what falls out of plan.json (`workflow` /
  `workflow_stage` fields are visible in plan.md and traces).
- No changes to the execution engine, protocol, or `_print_action()`.

## 8. Test plan

`tests/workflow/` (new, mirrors `core/workflow/`):

- `test_presets.py` — registry: get/list, unknown-name error type+message;
  `adversarial-tdd` stage table matches §3 defaults (names, agents, tiers,
  step types, order).
- `test_applier.py` —
  - reshapes a representative phased plan into 7 phases in order;
  - implementation steps preserved (agents, descriptions, deps re-keyed);
  - `step.model` stamped per stage incl. `fable` on spec/architecture/final;
  - `workflow_stage` stamped on every step; `plan.workflow` stamped;
  - idempotent (second apply → no duplicate phases/steps);
  - plan-graph invariants hold (round-trip `MachinePlan.from_dict(to_dict())`);
  - fan-out: 8 impl steps → 2 final reviewers, cap respected;
  - empty implement phase → synthesized fallback step;
  - external_command setting → automation step appended;
  - gates: test gate present on implementation phase; approval preserved.
- `test_settings.py` (tests/config or tests/workflow) — baton.yaml
  overrides (stage agent/model), invalid tier rejected with actionable error,
  missing file → defaults.
- `tests/models/` — round-trip of the two new fields; absent-when-empty
  (golden-fixture compatibility).
- `tests/cli/test_workflows_cmd.py` — list + show output contract.
- `tests/cli/` plan flag test — `baton plan --workflow adversarial-tdd --json`
  end-to-end shape; unknown workflow exits non-zero listing presets.
- Routing/static: `test-adequacy-reviewer` classified as reviewer;
  validator accepts `model: fable`.

## 9. Acceptance criteria

1. `baton plan "add rate limiting" --workflow adversarial-tdd --save` writes
   a plan.json whose phases are exactly the §3 stages, with per-step models
   `fable/fable/opus/opus/sonnet/opus/fable` respectively, and
   `"workflow": "adversarial-tdd"` at the plan root.
2. `baton execute start` + the existing action loop dispatches those steps
   with `Model:` lines matching the stamped tiers — zero engine changes.
3. `baton workflows` lists `adversarial-tdd`; `baton workflows show
   adversarial-tdd` prints the stage table.
4. Re-planning with the flag twice, or applying the applier twice, never
   duplicates stages.
5. baton.yaml can retarget any stage's agent/model and plug an external
   verifier command without code changes.
6. All existing planner/CLI tests stay green; plans without `--workflow`
   are byte-identical to before (both fields absent from JSON).
