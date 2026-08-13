---
quadrant: how-to
audience: users, agents
see-also:
  - [orchestrator-usage.md](orchestrator-usage.md#15-adversarial-tdd-workflow)
  - [cli-reference.md](cli-reference.md#baton-plan)
  - [../templates/playbooks/adversarial-tdd.md](../templates/playbooks/adversarial-tdd.md)
---

# Delivery workflows — optimal usage journeys

!!! abstract "Pillar context"
    This page details **Pillar 1 — Plan with foresight** (and **Pillar 3 — Right agent, right problem, right time**). For the high-level map of all four pillars, see [The Four Pillars](pillars.md).

`baton plan --workflow NAME` reshapes an assembled plan into a named
delivery-workflow preset. It is a **post-planner** step: the deterministic
planning pipeline still does classification, roster selection, risk, and
decomposition — the workflow re-shapes its output into staged phases with
pinned model tiers. The built-in preset is `adversarial-tdd`; an unknown
name exits 2 and lists the available presets.

Four journeys follow. Flag and config *reference* lives in
[cli-reference.md](cli-reference.md#workflow-presets) — this page narrates
the tasks and links there rather than restating the tables.

---

## Journey 1 — Everyday feature delivery (adversarial-tdd)

**When**: a new capability where correctness matters more than speed. The
spec is written first, tests are authored before code and independently
verified against the spec, implementation runs at an economical tier, and
the result is verified twice before a top-tier final review.

```bash
# 1. Preview — the forecast reflects the *reshaped* plan and fable pricing
baton plan "Add per-tenant rate limiting" --workflow adversarial-tdd --dry-run

# 2. Save — --explain appends a `## Workflow` stage table to explanation.md
baton plan "Add per-tenant rate limiting" --workflow adversarial-tdd --save --explain

# 3. Drive the loop (or headless: baton execute run)
baton execute start
```

Step 3 is [Recipe 1](orchestrator-usage.md#1-plan-and-execute-a-simple-task)
unchanged — the workflow shapes the plan, not the protocol.

### The seven stages

Each DISPATCH action carries the stage's pinned tier on its `Model:` line.

| # | Phase | Agent | Model | What happens |
|---|-------|-------|-------|--------------|
| 1 | Brainstorm & Spec | `architect` | fable | Writes `.claude/team-context/executions/<task_id>/spec.md` — behaviors, non-goals, acceptance criteria. |
| 2 | Architecture | `architect` | fable | Design notes + a file-boundary map for the implementers. |
| 3 | Test Authoring | `test-engineer` | opus | Failing tests encoding the spec's behaviors. No implementation code. |
| 4 | Test Verification | `test-adequacy-reviewer` | opus | Scoped to **spec + tests only**; flags missing, vacuous, or tautological tests. |
| 5 | Implementation | *your routed specialists* | sonnet | The base plan's implement steps, flattened into one phase and re-tiered. |
| 6 | Implementation Verification | `code-reviewer` | opus | Verifies the implementation against the spec, beyond "tests pass". |
| 7 | Final Review | `code-reviewer` | fable | Whole-slice review; fans out to multiple reviewers on large slices. |

**Stage 5's gate.** The Implementation phase carries the **first test- or
build-type gate found on a non-carryover phase of the base plan** — for a
Python plan that is often a build/import check, not the base plan's later
`pytest` gate, which is discarded with its phase. When the base plan has no
test-or-build gate at all, a stack-derived default gate is used instead.
Check the `Gates that will block:` line in `--dry-run` output before you
rely on it.

!!! warning "fable is a runtime alias"
    Stages 1, 2, and 7 are pinned to `fable`. Baton stamps the tier onto
    the step; resolving the `fable` alias at dispatch is the installed
    Claude Code runtime's job. Smoke-test one fable-pinned dispatch before
    relying on those stages in production — and override the tier in
    `baton.yaml` (see [Configuration](#configuration-quick-reference)) if
    your runtime does not resolve it.

### Interplay worth knowing

- `--agents` survives only in the harvested implementation steps; stages
  1–4 and 6–7 use the preset's (or your override's) agents.
- `--workflow` is mutually exclusive with `--manager-mode` and `--import`
  (exit 2). A config-level `manager_mode.enabled_by_default` is suppressed
  for that plan with a printed warning instead.
- `workflow` and `workflow_stage` live in `plan.json`, which is canonical —
  the SQLite copy does not carry them.
- Stage briefings tell the orchestrator it may dispatch sonnet
  general-purpose/domain agents for research support at any stage.

---

## Journey 2 — Goal-driven runs (`baton goal` + workflow)

**When**: "done" is a condition you can state in one sentence, and you want
the engine to keep rounding out the plan until it holds.

```bash
baton goal "the rate-limit integration tests pass under load" \
    --workflow adversarial-tdd --max-amend-cycles 5
baton execute start
```

`--workflow` is passed straight through to `baton plan`, so the saved plan
is the reshaped seven-stage pipeline. On top of that:

- The goal is evaluated at every **gate-pass boundary**; unmet goals with
  budget left trigger `amend_plan`.
- Evaluator selection is `BATON_GOAL_EVALUATOR` — `stub` (deterministic),
  `haiku` (default when `ANTHROPIC_API_KEY` is set), or `opus`.
- Safety rail: any `met=True` verdict is overridden to `met=False` unless
  the most-recent gate passed.
- Termination: goal met, amend budget exhausted, or
  `BATON_RUN_TOKEN_CEILING` hit.

!!! note "Amended phases are ordinary planner phases"
    Phases appended mid-execution by an amend cycle carry **no**
    `workflow_stage` and **no** stage model pinning. They are ordinary
    planner phases appended after the workflow's phases — do not expect the
    fable/opus/sonnet tiering to extend to them.

---

## Journey 3 — Regulated-domain runs (audit carryover)

**When**: the work touches regulated data, compliance systems, or
audit-controlled records. The regulated-domain rules still apply in full:
`subject-matter-expert` for domain context, `auditor` for pre/post review,
Regulated Data guardrail preset.

```bash
# 1. Confirm the risk tier and preset
baton classify "Update PHI redaction rules in the patient export pipeline" \
    --files app/exports.py

# 2. Activate the resolved preset for hook enforcement
baton classify "Update PHI redaction rules in the patient export pipeline" --activate

# 3. Plan, belt-and-braces on the domain agents
baton plan "Update PHI redaction rules in the patient export pipeline" \
    --workflow adversarial-tdd --save --explain \
    --agents subject-matter-expert,auditor,backend-engineer,test-engineer
```

**What the reshape guarantees.** Base phases that were not harvested and
contain at least one `auditor` step are appended **after** Final Review,
verbatim — steps, gate, and approval preserved (ids renumbered,
`workflow_stage="carryover"`). The planner's regulated-domain hard gate
survives the reshape, and because carryover phases keep their own
gate/approval, there is no double sign-off. In the plan above the eighth
phase is the base plan's `Audit` phase, sitting after Final Review.

**Harden the run:**

```bash
BATON_COMPLIANCE_FAIL_CLOSED=1 BATON_APPROVAL_MODE=team baton execute run
```

**Afterwards**, produce and check the assurance artifact:

```bash
baton evidence bundle <task_id>
baton evidence verify .claude/team-context/evidence/<task_id>/
```

See also [Recipe 3 — high-risk tasks with auditor
gates](orchestrator-usage.md#3-run-a-high-risk-task-with-auditor-gates).

---

## Journey 4 — External-vendor verification

**When**: you want a second opinion from a different vendor's CLI (gemini,
codex, or any script) before the final review. Baton has no vendor
abstraction — instead, `workflow.external_command` makes the applier append
an engine **automation** step (`task-runner`) to the Implementation
Verification phase that runs your literal command as a subprocess in the
project working directory.

```yaml
# baton.yaml (or .claude/baton.yaml)
workflow:
  external_command: "codex exec 'review the diff against .claude/team-context/executions/<task_id>/spec.md'"
  external_timeout_seconds: 1800
```

A gemini equivalent is just a different command string — e.g.
`external_command: "gemini -p 'Review the staged diff for spec violations'"`.
With the setting in place, a `--dry-run` preview shows phase 6 with a
second step: `6.2 Implementation Ve  task-runner`.

!!! warning "300-second cap on external commands"
    The v1 automation runner in `baton execute run` hard-caps automation
    commands at **300s**. `external_timeout_seconds` is stamped onto the
    step for forward-compatibility only and is not honored there yet —
    budget vendor CLIs accordingly (scope the prompt, or run the long
    version outside the loop).

Prefer a different *agent* for the same stage — instead of, or alongside,
the external command? Retarget it:

```yaml
workflow:
  stages:
    implementation_verification: {agent: security-reviewer}
```

---

## Configuration quick reference

The whole `workflow:` block, as shipped in
[`templates/baton.yaml.example`](../templates/baton.yaml.example):

```yaml
workflow:
  # stages:
  #   final_review: {model: opus}
  #   implementation_verification: {agent: security-reviewer}
  external_command: ""
  external_timeout_seconds: 1800
  final_review_fanout_divisor: 4
  final_review_max_reviewers: 3
```

| Key | Effect |
|-----|--------|
| `stages.<stage_id>.agent` | Retarget a stage's agent. Ignored for the harvesting stage (`implementation`) — those agents come from the base plan. |
| `stages.<stage_id>.model` | Retarget a stage's tier (`haiku`/`sonnet`/`opus`/`fable`). Use this to move stages 1, 2, and 7 off `fable` if your runtime does not resolve that alias. |
| `external_command` | Literal command for the Implementation Verification automation step. Empty = no external verifier. Capped at 300s by the v1 runner. |
| `external_timeout_seconds` | Stamped on the step for forward-compatibility; **not** honored by the v1 runner. |
| `final_review_fanout_divisor` | Reviewers = `ceil(implementation dispatch units / divisor)`. |
| `final_review_max_reviewers` | Hard cap on that fan-out. |

Layering is defaults < `~/.baton/config.yaml` < project config
(`.claude/baton.yaml` preferred over root `baton.yaml`; first file found
wins). An unknown `stages` key is rejected with the list of valid stage
ids. Canonical reference:
[cli-reference.md#workflow-presets](cli-reference.md#workflow-presets).

---

## See also

- [Orchestrator Usage — Recipe 15](orchestrator-usage.md#15-adversarial-tdd-workflow) — the quick recipe
- [CLI Reference — `baton plan`](cli-reference.md#baton-plan) and [`baton goal`](cli-reference.md#baton-goal)
- [First Run](examples/first-run.md) — the end-to-end walkthrough
- [`templates/playbooks/adversarial-tdd.md`](../templates/playbooks/adversarial-tdd.md) — the PMO playbook
