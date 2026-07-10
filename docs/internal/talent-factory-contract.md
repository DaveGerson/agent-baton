# Talent Factory Contract — capability gaps and the bounded generation lifecycle

**Status:** Draft
**Step:** Phase 5, 5.1 (architect) — agent-baton middle-manager hardening plan
**Scope:** `agent_baton/core/engine/planning/capability_gap.py` (new),
`agent_baton/core/engine/planning/{draft.py,planner.py,stages/roster.py,stages/assembly.py}`,
`agent_baton/core/config/manager.py` (`TalentFactoryConfig`), `agents/talent-builder.md`.
**Non-goals of this document:** it does not wire `--skip-init` / `team.allow_talent_builder`
through `baton plan` (`agent_baton/cli/commands/execution/plan_cmd.py` is outside this
step's allowed paths), does not implement the talent-builder *dispatch* itself as a plan
phase, and does not implement `baton agents doctor` validation tooling. See §11
(Follow-up work) for the enumerated hand-off.

---

## 1. The problem this document fixes

`agents/talent-builder.md` already exists and is a capable agent factory. `TeamConfig.allow_talent_builder`
(`agent_baton/core/config/manager.py`) and the `baton plan --skip-init` CLI flag
(`agent_baton/cli/commands/execution/plan_cmd.py`) already exist as *names* for policy
knobs. None of the following existed before this step:

- A way for the planner to **represent** "this task needs a role that doesn't exist" as
  a distinct, structured fact — as opposed to silently routing an unresolved agent name
  through to a dispatch that will fail at execution time with no diagnosis of *why*.
- A way to distinguish that missing-role case from two lookalikes that must **never**
  trigger agent generation: a task description too thin to route confidently (ask, don't
  build), and a role that already exists but lacks reference material for the domain at
  hand (attach/generate knowledge, not a new agent).
- Any bound on talent-builder's own lifecycle: nothing stopped a hypothetical future
  integration from retrying generation forever, or from asking talent-builder to build
  another talent-builder (recursive self-generation).
- Any policy for what happens when a generated artifact is invalid, collides with an
  existing name, or was influenced by untrusted text encountered during research.

This document specifies the model that closes those gaps (§2–§4) and the policy
that bounds the lifecycle around it (§5–§10). §11 lists what remains to wire this model
into an actual runtime dispatch.

---

## 2. The capability-gap model

Implementation: `agent_baton/core/engine/planning/capability_gap.py`.

### 2.1 `CapabilityGapKind` — three lookalikes, three different responses

| Kind | What it means | Never confuse with | Default response |
|---|---|---|---|
| `missing_role` | No agent definition matches a role the plan explicitly needs. | A task that's just hard to route (see `weak_task_description`) | May generate an **agent** |
| `weak_task_description` | The *task* lacks enough signal to route confidently — a routing problem, not a capability problem. | A missing role — the role might already exist; nobody can tell yet. | **Never** generates. Always `request_clarification`. |
| `missing_knowledge` | The role resolved fine; it lacks reference material for this domain. | A missing role — the agent exists and is capable in general. | May generate a **knowledge pack** |

Keeping these separate matters operationally: conflating "I don't know what you mean"
with "I don't have a specialist for that" would make talent-builder the default answer
to bad task descriptions, which is exactly the failure mode this contract exists to
prevent (generating agents nobody asked for, for problems generation can't fix).

### 2.2 `CapabilityGap` — evidence-backed, not asserted

```python
@dataclass(frozen=True)
class CapabilityGap:
    requested_capability: str
    kind: CapabilityGapKind
    evidence: tuple[CapabilityGapEvidence, ...]       # required, non-empty
    permitted_artifacts: tuple[PermittedArtifactType, ...] = ()  # derived from kind if omitted
    fallback: str = ""                                 # derived from kind if omitted
```

A `CapabilityGap` **cannot be constructed without evidence** —
`CapabilityGapEvidence(source, detail)` names the detector/stage that produced the
observation and a human-readable explanation. This is enforced in `__post_init__`
(`ValueError`, not a warning) because the alternative — a gap asserted from a hunch —
is precisely what would let generation run on speculation instead of a planner-observed
fact. `evidence` is a tuple so multiple independent signals (e.g. "explicit `--agents`
name unresolved" *and* "no learned pattern recommends this name either") can stack on
one gap without the caller having to pick just one.

`permitted_artifacts` and `fallback` default from `kind` (§2.3, §2.4) but callers may
override them — e.g. a caller who knows the user explicitly asked for a skill sets
`permitted_artifacts=(PermittedArtifactType.SKILL,)` on an otherwise-`missing_role` gap.
An explicit empty tuple (`permitted_artifacts=()`) is a real value, not "unset" — it
means "nothing is currently authorized for this gap," and `decide_talent_lifecycle`
(§3) treats it as an automatic fallback regardless of other policy.

### 2.3 `PermittedArtifactType` — the default product is an agent or a knowledge pack

```python
AGENT, KNOWLEDGE_PACK, SKILL, PLUGIN
```

**Skill and plugin creation are out of scope for a bare capability gap.** The
kind→default-artifact table is deliberately narrow:

| Gap kind | Default `permitted_artifacts` |
|---|---|
| `missing_role` | `(AGENT,)` |
| `missing_knowledge` | `(KNOWLEDGE_PACK,)` |
| `weak_task_description` | `()` — nothing is ever generated |

Skill/plugin only enters `permitted_artifacts` when a caller **explicitly** constructs
a gap with that override (e.g. because the human said "turn this into a reusable
skill"). No detector in `capability_gap.py` defaults to it, and `agents/talent-builder.md`
is instructed accordingly (§9). This mirrors the existing Talent Builder decision
framework's five tests (`references/decision-framework.md`) — this contract narrows,
not replaces, that framework for the specific case of a planner-detected gap.

### 2.4 Detectors — pure functions from planner-observable signal to gap-or-none

| Function | Fires when | Evidence source |
|---|---|---|
| `detect_missing_role_gap(requested_agent, known_agents=...)` | An explicitly requested agent's base name (flavor suffix stripped) isn't in the registry's known base names. | `roster_stage.explicit_agent` |
| `detect_weak_description_gap(task_summary, min_words=3)` | The task summary has fewer than `min_words` words. | `classification.task_summary` |
| `detect_missing_knowledge_gap(role, domain=...)` | Always returns a gap — called only once the runtime knowledge-gap signal (`agent_baton/core/engine/knowledge_gap.py`) has already confirmed a role-level knowledge gap exists; this function just represents it in the same evidence-backed shape as the other two. | `knowledge_resolver` |

Detectors never mutate anything — they observe and return `CapabilityGap | None` (or
always a gap, for the knowledge case). This keeps them trivially unit-testable and keeps
detection decoupled from what happens next (§3).

**Current wiring** (this step): `RosterStage._detect_capability_gaps` calls
`detect_missing_role_gap` for every name in an explicitly-supplied `--agents` list,
against the set of base names the loaded `AgentRegistry` actually has definitions for.
It deliberately does **not** scan the default/pattern/concern-expansion roster — those
paths only ever draw from names the registry is already known to have, so running the
detector there would produce noise, not evidence. `detect_weak_description_gap` and
`detect_missing_knowledge_gap` are implemented and unit-tested but not yet called from
a pipeline stage — see §11.

---

## 3. The bounded talent lifecycle

Implementation: `decide_talent_lifecycle(gap, *, allow_talent_builder, skip_init,
recursion_depth, max_recursion_depth, attempts_used, retry_budget) ->
TalentLifecycleDecision`.

### 3.1 `TalentLifecycleAction` — four possible outcomes, never a fifth

| Action | Meaning | Who acts on it |
|---|---|---|
| `dispatch_talent_builder` | Generate: the gap is evidence-backed, policy permits it, budget remains. | Orchestrator dispatches talent-builder with the gap's `requested_capability` + `permitted_artifacts`. |
| `fallback_generic_agent` | Don't generate. Proceed with the closest existing generalist / existing knowledge. Never blocks the plan. | Roster keeps the caller's original request as-is (diagnostic only, see §3.3); execution-time dispatch falls back per `gap.fallback`. |
| `queue_for_manager` | Budget or recursion ceiling exhausted without resolving the gap. | Re-plan the unresolved work (§8) — never silently drop it, never retry indefinitely. |
| `request_clarification` | The gap is a description problem, not a capability problem. | Ask the caller; never generate. |

### 3.2 Decision order (fixed — earlier checks are hard stops)

1. **`weak_task_description` always → `request_clarification`.** No policy combination
   overrides this — there is no configuration under which generating an agent for an
   undescribed task is correct.
2. **Structural recursion guard.** `gap.requested_capability`'s base name in
   `NON_GENERABLE_CAPABILITIES` (currently `{"talent-builder"}`) → `fallback_generic_agent`,
   *unconditionally*, regardless of `allow_talent_builder`, `skip_init`, or budget. This
   makes "talent-builder generates talent-builder" impossible by construction, not
   merely policy-discouraged — see §7. Independently, `recursion_depth >
   max_recursion_depth` → `queue_for_manager` (re-plan instead of nesting deeper; see §8).
3. **Explicit opt-outs.** `skip_init=True` → `fallback_generic_agent` (`--skip-init` CLI
   override, or any equivalent programmatic override). `allow_talent_builder=False` →
   `fallback_generic_agent` (`TeamConfig.allow_talent_builder`, still the master on/off
   switch — see §4).
4. **Retry budget.** `attempts_used >= retry_budget` → `queue_for_manager` (§10).
5. **No permitted artifacts.** `gap.permitted_artifacts` is empty → `fallback_generic_agent`
   (covers both the `weak_task_description` default and any caller-constructed gap with
   an explicit empty override, §2.2).
6. Otherwise → `dispatch_talent_builder`.

Every `TalentLifecycleDecision` carries `gap` (the full evidence trail) and `reason` (a
one-line human-readable explanation of which check fired) — both round-trip through
`to_dict()` for `plan_diagnostics` and audit logs.

### 3.3 What "bounded" means at plan time vs. execution time

This step's pipeline integration (`RosterStage`) calls `decide_talent_lifecycle` once,
at plan-construction time, with `recursion_depth=0`, `max_recursion_depth=0`,
`attempts_used=0`, `retry_budget=1` (the function's defaults) — i.e. "is this a
first-generation gap and is generation policy-permitted at all." The decision is
**diagnostic only**: it is recorded on `plan.plan_diagnostics["capability_gaps"]` /
`["talent_lifecycle_decisions"]` and as a routing note; it does not mutate
`draft.resolved_agents`, so a caller's explicit `--agents` list is preserved as given
and a plan still assembles even when a gap is detected. Actually dispatching
talent-builder, tracking `attempts_used` across retries within one run, and deriving
`recursion_depth` from an artifact's own generation ancestry are execution-time
concerns handed off in §11 — the function signature already accepts the parameters a
future execution-time caller needs; this step does not yet supply non-default values
for them.

---

## 4. Policy: `allow_talent_builder` and `--skip-init`

Two independent knobs, both already named before this step, now both consumed by
`decide_talent_lifecycle`:

- **`team.allow_talent_builder`** (`TeamConfig` in `agent_baton/core/config/manager.py`,
  default `True`) — the master on/off switch for talent-builder participation at all.
  `False` means "this project never wants generated capability," full stop.
- **`--skip-init`** (`baton plan` CLI flag) — a per-invocation override: "don't
  auto-initiate talent-builder for *this* plan even if the project would otherwise
  allow it," e.g. because `.claude/agents/` is empty and the caller wants the bundled
  generic roster instead of triggering generation.

Both map onto `IntelligentPlanner.create_plan(..., skip_init: bool = False,
allow_talent_builder: bool = True)`, which threads them onto `PlanDraft.skip_init` /
`PlanDraft.allow_talent_builder` for `RosterStage` to read. **Defaults preserve prior
behavior** (generation permitted, nothing skipped) so this addition is backward
compatible for every existing caller.

`TalentFactoryConfig` (new section on `ManagerConfig`, key `talent_factory` in
`baton.yaml`) governs the generation *lifecycle* once talent-builder is otherwise
permitted to run — retry budget, recursion ceiling, validation/rollback policy, name
collision policy, registry reload timing (§5–§10). `team.allow_talent_builder` stays on
`TeamConfig` for backward compatibility; `talent_factory` is additive.

```yaml
team:
  allow_talent_builder: true      # master switch (pre-existing)

talent_factory:                   # new section (this step)
  default_permitted_artifacts: [agent, knowledge_pack]
  retry_budget: 1
  max_recursion_depth: 0
  require_validation: true
  on_validation_failure: rollback         # rollback | quarantine
  name_collision_policy: reject           # reject | version_suffix | manual_review
  registry_reload: immediate              # immediate | next_plan
```

---

## 5. Generated agent/pack/skill validation

`talent_factory.require_validation` (default `true`) gates whether a generated artifact
is usable before it is validated. Validation criteria, by artifact type:

- **Agent** — must satisfy the pre-existing Generated-Agent Contract already documented
  in `agents/talent-builder.md` and `references/agent-authoring.md`: required
  frontmatter (`name`, `description`, `model`, `permissionMode`, `tools`), required body
  sections (Mission, Before Starting, Knowledge References, Principles, Anti-Patterns,
  Output Format), every path named under "Knowledge References" must resolve, and
  `name` must match the filename. This step does not add a new checker — it makes
  passing this contract a hard precondition for a generated agent to be registered,
  per `on_validation_failure` below. (A machine-checkable `baton agents doctor` command
  implementing this mechanically is tracked in §11 and in
  `reference_docs/framing_and_roadmap/03-talent-builder-subagent-management.md` Phase 2;
  it does not exist yet, so validation is currently talent-builder's own read-back +
  checklist step, per its updated instructions.)
- **Knowledge pack** — every file the pack's manifest/frontmatter references must exist
  under the pack directory; at minimum an `overview.md` under 50 lines per
  `agents/talent-builder.md`'s existing knowledge-pack format rules.
- **Skill/plugin** — out of scope by default (§2.3); when explicitly authorized, must
  satisfy the existing `SKILL.md` frontmatter contract (`name`, `description`).

### 5.1 On validation failure

`talent_factory.on_validation_failure`:

- **`rollback`** (default) — discard the artifact entirely (delete the file(s) just
  written) and apply `gap.fallback`. An invalid artifact must never be left registered
  or half-written on disk; "fail closed" here mirrors the repo-wide convention (see
  `BATON_COMPLIANCE_FAIL_CLOSED` for the analogous pattern in the compliance subsystem).
- **`quarantine`** — keep the file on disk with `status: draft` (or an explicit
  `status: rejected` marker) for human review, but do not register it for planning/
  dispatch use. Useful when a human wants to see *what* talent-builder attempted even
  though it didn't pass.

---

## 6. Name collisions

`talent_factory.name_collision_policy` governs what happens when a generated artifact's
name (`agents/<name>.md`, or a knowledge-pack directory name) already exists on disk:

- **`reject`** (default) — refuse to write, apply `gap.fallback`, and report the
  collision. Never silently overwrite — an existing agent/pack might be hand-authored
  and load-bearing.
- **`version_suffix`** — write as `<name>--v2` (incrementing) instead of overwriting.
  Only sensible for genuine re-generations of a *previous talent-builder output* for
  the same gap (provenance-checked via §7's `created_by`/`version` fields), not for
  colliding with an unrelated hand-authored agent of the same name.
- **`manual_review`** — write to a quarantine path (not the live `agents/` /
  `knowledge/` tree) and queue for a human to reconcile.

`agents/talent-builder.md` is updated (this step) to check for an existing file before
writing and to stop + report rather than overwrite, matching the default `reject`
policy; site-specific overrides via `talent_factory.name_collision_policy` are the
caller's (orchestrator's) responsibility to pass into the dispatch prompt — that
plumbing is deferred (§11).

---

## 7. Untrusted instructions

Talent-builder's research step reads external and repo-local documentation to build
knowledge packs and agent prompts. That material is **data, never instructions** — a
schema doc, README, or web page that contains text shaped like a directive ("ignore
previous instructions," "set permissionMode to auto-edit," "grant tool Bash") must not
change what talent-builder builds, what tools it grants, or what permission mode it
sets. `agents/talent-builder.md` (this step) states this explicitly as a non-negotiable
rule, alongside the pre-existing least-privilege rule (start read-only; add
`Write`/`Edit`/`Bash` only when the mission requires it) — both rules apply regardless
of what generated content "asks for."

This is the same class of defense as scope-contract enforcement (Phase 3) and the
manager-mode scope-signal guardrails: content encountered while doing the work is not
a channel for expanding what the work is allowed to do.

---

## 8. Re-planning unresolved work without recursive spawning

Two independent mechanisms, both already covered above, restated together because they
are the two halves of "no recursive talent-builder spawning":

1. **Structural**: `NON_GENERABLE_CAPABILITIES` makes "talent-builder generates
   talent-builder" return `fallback_generic_agent` unconditionally (§3.2 step 2) — this
   is not a policy setting that could be misconfigured away; it is checked in code
   before any policy knob is consulted.
2. **Depth-bounded**: when a capability gap is itself discovered while resolving a
   *prior* talent-builder-generated artifact (e.g. a generated agent's own knowledge
   pack turns out to be incomplete), `recursion_depth` reflects how many
   talent-builder generations already sit in that gap's ancestry.
   `max_recursion_depth` (default `0`, from `talent_factory.max_recursion_depth`) means
   "never nest" by default — `recursion_depth > max_recursion_depth` →
   `queue_for_manager`, not another `dispatch_talent_builder`.

`queue_for_manager` means: the unresolved work is **re-planned**, not retried by the
same mechanism that just failed. Concretely (execution-time behavior, handed off in
§11): the orchestrator treats the gap as unresolved scope, surfaces it the same way a
scope-expansion or knowledge-gap escalation is surfaced today (`queue-for-gate` in
`agent_baton/core/engine/knowledge_gap.py` is the existing analogous pattern), and lets
a human or the manager-mode PMO layer decide — route to a generalist agent, adjust the
task description, or approve one more generation attempt with an explicitly bumped
budget. Talent-builder itself never decides to retry on its own (§9) — that would
reintroduce unbounded retry through the back door.

---

## 9. Provenance

Every artifact talent-builder generates must be attributable back to this lifecycle.
`agents/talent-builder.md` (this step) requires:

- `created_by: talent-builder` on every generated agent's frontmatter (already a
  recommended field per the pre-existing Generated-Agent Contract; this step makes it
  mandatory for talent-builder's own output, not just recommended).
- `status: draft` and `version: 0.1.0` (or the next patch version for a
  `version_suffix` re-generation, §6) on first generation, so `status`-aware tooling
  (the draft/review/promote lifecycle described in
  `reference_docs/framing_and_roadmap/03-talent-builder-subagent-management.md` Phase
  3) can distinguish generated-and-unreviewed capability from a promoted, trusted
  roster agent.
- The dispatching gap's evidence (`CapabilityGap.to_dict()`) is available in
  `plan.plan_diagnostics["capability_gaps"]` for audit — a generated artifact should be
  traceable back to the specific evidence that justified its creation, not just a
  timestamp.

---

## 10. Retry budgets

`talent_factory.retry_budget` (default `1`) bounds `attempts_used` in
`decide_talent_lifecycle` (§3.2 step 4): once `attempts_used >= retry_budget`, the
lifecycle stops offering `dispatch_talent_builder` for that gap and escalates to
`queue_for_manager` instead. A budget of `1` means exactly one generation attempt per
gap per plan before escalation — deliberately conservative, since an unbounded retry
loop against a capability gap that's fundamentally unresolvable (e.g. the domain
described genuinely doesn't map to anything buildable) is indistinguishable from a
runaway loop from the outside. Sites that want more attempts set
`talent_factory.retry_budget` higher; the function never has an "unlimited" mode.

---

## 11. Follow-up work (explicitly deferred, not done in this step)

This step delivers the **model and policy** (`capability_gap.py`, `TalentFactoryConfig`,
the `RosterStage` diagnostic-only integration, and the updated `talent-builder.md`
contract). It deliberately does not:

1. **Thread `--skip-init` / `team.allow_talent_builder` from `baton plan` into
   `create_plan()`.** `agent_baton/cli/commands/execution/plan_cmd.py` is outside this
   step's allowed paths. `IntelligentPlanner.create_plan()` already accepts
   `skip_init`/`allow_talent_builder` kwargs (this step) — the CLI wiring is a small,
   low-risk follow-up: parse `args.skip_init` (already parsed, currently unused) and
   load `ManagerConfig.team.allow_talent_builder` before the `create_plan()` call
   (today `ManagerConfig` loads *after* `create_plan()` in `plan_cmd.py`, so the load
   order needs to move earlier).
2. **Actually dispatch talent-builder as a plan phase/step** when
   `TalentLifecycleAction.DISPATCH_TALENT_BUILDER` is decided. Today the decision is
   recorded in `plan_diagnostics` for visibility; inserting a generation phase ahead of
   the phase that needs the missing role (and gating that phase on validation, §5)
   is execution-pipeline work, not planning-model work.
3. **`attempts_used` / `recursion_depth` bookkeeping across an actual run.** The
   function signature supports it; nothing yet persists attempt counts or ancestry
   between dispatches. Natural home: alongside the existing bead-based tracking
   (`agent_baton/core/engine/bead_signal.py`) or a small sidecar next to
   `.claude/team-context/executions/<task_id>/`.
4. **`baton agents doctor`** — mechanical validation of the Generated-Agent Contract
   (§5). Tracked pre-existing in
   `reference_docs/framing_and_roadmap/03-talent-builder-subagent-management.md` Phase
   2; `agent_baton/cli/commands/agents/` is outside this step's allowed paths.
5. **`registry_reload: immediate`** actually reloading a live `AgentRegistry`
   mid-plan. `AgentRegistry` today is loaded once per `IntelligentPlanner` instance
   (`__init__` → `load_default_paths()`); making it reloadable mid-run is a registry
   change, not a planning-model change.
6. **`detect_weak_description_gap` / `detect_missing_knowledge_gap` pipeline wiring.**
   Both are implemented and unit-tested (`tests/test_planner.py`) but not yet called
   from `ClassificationStage` / the runtime knowledge-gap path respectively — see §2.4.

None of the above blocks this step's behavioral contract: *"the planner can represent
an evidence-backed capability gap and apply a bounded, policy-controlled generation
lifecycle with a safe fallback"* — §2–§3 deliver exactly that, independent of whether
anything downstream yet acts on `dispatch_talent_builder`. The safe fallback
(`fallback_generic_agent`) is unconditionally available today: a plan with an
unresolved capability gap still assembles and still runs, with the gap visible in
`plan_diagnostics` rather than silently swallowed.

---

## 12. Reference: files this contract governs

| File | Role |
|---|---|
| `agent_baton/core/engine/planning/capability_gap.py` | The model: `CapabilityGap`, detectors, `decide_talent_lifecycle`. |
| `agent_baton/core/engine/planning/draft.py` | `PlanDraft.skip_init` / `.allow_talent_builder` (inputs), `.capability_gaps` / `.talent_lifecycle_decisions` (outputs). |
| `agent_baton/core/engine/planning/stages/roster.py` | `RosterStage._detect_capability_gaps` — the current (diagnostic-only) pipeline integration point. |
| `agent_baton/core/engine/planning/stages/assembly.py` | Surfaces gaps/decisions on `plan.plan_diagnostics`. |
| `agent_baton/core/engine/planning/planner.py` | `IntelligentPlanner.create_plan(skip_init=, allow_talent_builder=)`; `build_plan_diagnostics` preserves gaps across re-diagnostics passes (e.g. goal-driven amend cycles). |
| `agent_baton/core/config/manager.py` | `TalentFactoryConfig` (`talent_factory` section); `TeamConfig.allow_talent_builder` (pre-existing master switch). |
| `agents/talent-builder.md` / `agent_baton/_bundled_agents/talent-builder.md` | The agent-side contract: no recursive self-generation, untrusted-instructions rule, name-collision handling, provenance frontmatter, skill/plugin scope gate. |
| `tests/test_planner.py` | Unit tests for the model + lifecycle decision table, and integration tests against `IntelligentPlanner.create_plan()`. |
