# Proposal 007: Pare to the Moat — Refocus Agent Baton as a Governance & Assurance Harness

**Status**: Draft (supersedes most of Proposal 006; see §Relationship to 006)
**Author**: Codebase review (5 review agents: model surface, architecture, distributables, docs/tests, governance-layer recon)
**Date**: 2026-06-10
**Risk**: MEDIUM — large deletions, but of flag-gated/experimental subsystems; the kept core is already production-tested
**Estimated Scope**: −4,300 LOC engine + −4,400 LOC tests deleted; ~1,500 LOC new (assurance packs, hooks integration, evidence bundle)

---

## Thesis

Agent Baton was built when the model and harness needed an external brain:
something to plan, route models by tier, speculate ahead of slow models,
shard work to cheap models, and retry failures up an escalation ladder.
Claude Code's current releases (subagents with model override and worktree
isolation, Agent Teams, plan mode, `/goal`, hooks, skills, session resume,
OTel telemetry) and the current API (adaptive thinking, effort levels, task
budgets, structured outputs) now do that natively — and better, because
they adapt mid-flight instead of executing a frozen plan.

What the platform does **not** do — and shows no sign of doing — is
**domain-specific assurance**: legal checks against regulations, compliance
validation, security guardrails, expert review with verifiable evidence,
segregation-of-duties approval, and audit-grade reporting. Baton already
has real, tested machinery here (hash-chained compliance trail, data-driven
policy engine, AIBOM with SPDX export, auditor quarantine/approve gate,
team approval with requester ≠ reviewer enforcement). That machinery is
the moat. This proposal deletes the parts that duplicate the platform and
expands the moat into a clear product: **the assurance harness around
Claude Code**.

**Decision rule applied throughout:** if Claude Code or the API provides
the capability natively, delete baton's copy and document the native path.
If the capability is about *checking, evidence, or accountability*, keep
it and make its value explicit.

---

## Part A — Pare back (natively mitigated)

| # | Delete / shrink | Native mitigation | Scope |
|---|---|---|---|
| A1 | **`core/predict/`** (speculator, intent classifier, accept, watcher — 1,750 LOC) + `BATON_PREDICT_ENABLED` / `BATON_SPECULATE_ENABLED` + speculation budget caps in `govern/budget.py` | Speculative dispatch was a latency hack for slower models. Current models at appropriate effort are fast enough; nothing to pre-warm. | Delete module, `tests/test_predict*.py`, `tests/test_speculat*.py`, env rows in CLAUDE.md/GEMINI.md, speculation pricing keys in budget tables |
| A2 | **`core/swarm/`** (dispatcher, partitioner, coalescer, reconciler — 2,046 LOC) + `cli/commands/swarm_cmd.py` + `BATON_EXPERIMENTAL=swarm` | The native Agent tool with `model: haiku` + `isolation: "worktree"` + parallel tool calls *is* swarm, with none of the bespoke pricing/reconciliation code (which carries retired Haiku 3.5 pricing today). Distribute the pattern as a short recipe in `docs/orchestrator-usage.md` instead. | Delete module + ~8 swarm test files (~3,500 LOC), `swarm-reconciler` agent, swarm sections in `references/baton-engine.md:1449`, `docs/cli-reference.md:2006`, SWARM_DISPATCH `ActionType` (deprecate first — see §Migration) |
| A3 | **Self-heal escalation ladder** (`core/engine/selfheal.py` 492 LOC, `EscalationTier`, per-tier budget pricing, `self-heal-haiku/-sonnet/-opus` agents) | The haiku→sonnet→opus retry ladder is a weak-model-era pattern — paying for the failure three times. Current guidance: run the capable model at high effort first; on gate failure, redispatch *once* with the failure context (the engine's existing FEEDBACK path). Already `BATON_SELFHEAL_ENABLED=0` by default. Keep the *incident discipline* (bead it, fix in parallel, regression test) — that's convention in CLAUDE.md, not engine code. | Delete module, 3 agents, `--max-tier` CLI flag, `selfheal_attempts` state field (schema migration), tier-keyed pricing in `budget.py`, `tests/test_wave5_integration.py` terminal-tier assertions |
| A4 | **Planner model-routing and heavy decomposition stages** (`planning/stages/decomposition.py` hardcoded `"opus"`/`"sonnet"`, `research.py` headless research, per-stage model literals) | Plan mode / `/goal` / Fable-era models plan and re-plan natively. Keep the planner as a **thin governance-enrichment pass** over a task: DataClassifier risk → guardrail preset → required reviewers → gates → budget. Stop deciding which model writes the code; that's frontmatter + Claude Code's job. | Shrink `planning/stages/`, remove model-assignment plumbing, keep `enrichment.py`'s policy/gate attachment |
| A5 | **Estimation-based cost tables** (`cost_estimator.py` blended `MODEL_PRICING`, `cost_forecaster.py` `_DEFAULT_TOKENS`, role-baseline token guesses, wall-clock guesses) | Real usage is free: `observe/jsonl_scanner.py` already parses actual tokens from Claude Code session JSONL, and OTel export exists. Replace pre-run estimation with post-run actuals + a rolling forecast from history. Keep **one** pricing config (single small table or user-editable JSON) used only to convert actual tokens → dollars for chargeback and the ceiling. This dissolves the four-inconsistent-tables problem from Proposal 006 by deleting three of them. | `cost_estimator.py` shrinks to a price-config loader; `--dry-run` cost output becomes "forecast from history" with honest confidence |
| A6 | **`claude-teams` backend write-a-spawn-prompt path** (`BATON_TEAMS_BACKEND=claude-teams`, `BATON_TEAMS_STRICT_RESUMABILITY`) | Agent Teams are now a first-class Claude Code feature; baton generating spawn prompts for an outer session is scaffolding around an experiment that ended. Re-verify current Agent Teams capabilities, then keep only the worktree backend. | Delete backend branch + 2 env vars + `docs/internal/agent-teams-and-goal-design.md` claims re-verification |
| A7 | **pmo-ui surfaces for deleted features** (speculation/swarm panels, 3-tier `ModelTier` enums in `BohWalkIn.tsx`, `PlanEditor.tsx` `MODEL_LIST`) | — | Remove with their backends; model pickers read from the pricing config rather than hardcoded enums. The UI itself is **not** pared — it's repurposed as the spec-federation/review surface (B4) |

**Not deleted, explicitly:** the execution state machine + `baton execute resume`
(it's the spine the gates/approvals/compliance entries hang off — but see
§Part C for making it optional), worktree isolation guidance, beads,
`_print_action()` protocol surface.

### Migration discipline

- **Direct deletion — no deprecation release.** Confirmed: these subsystems
  have no users. Each deletion lands as its own PR: module + tests + CLI +
  docs + env vars together. `ActionType.SWARM_DISPATCH` is removed in the
  swarm PR with a CHANGELOG note (it is a documented public enum, but with
  no consumers a warn+no-op cycle buys nothing).
- Golden states (`tests/models/golden_states/`) regenerate via
  `python tests/models/_generate_golden.py` whenever `ExecutionState` loses
  fields (A3) — never hand-edited.
- `README.md` / `docs/agent-roster.md` counts update per the derived-count
  recommendation in `docs/internal/doc-audit.md:240-295`.

---

## Part B — Expand the moat (additive, no native substitute)

Anchor: governance exists to apply **detailed checking and expert review to
specific use cases** — legal checks against regulations, compliance
validation, security guardrails. Everything below makes that concrete.

### B1. Assurance Packs — turn guardrail presets into a pluggable domain system

Today: 5 built-in presets (`standard_dev`, `data_analysis`, `infrastructure`,
`regulated`, `security`) as `PolicySet`/`PolicyRule` dataclasses
(`core/govern/policy.py`), enforced at **plan time** (require-agent,
block-tool, required gates), customizable via `.claude/policies/<name>.json`.

Expand into a first-class **Assurance Pack** unit — one directory per domain:

```
packs/hipaa-phi/
  policy.json          # PolicyRule set (require SME+auditor, block Bash-on-data, append-only)
  signals.json         # DataClassifier keyword/path signals for this domain
  rubric.md            # independently-checkable review criteria, citing the regulation
  knowledge/           # SME knowledge docs (the regulation digests the reviewer cites)
  gates.json           # derived gate commands (e.g. PII-scan script, license checker)
  evidence.json        # what the evidence bundle must contain for this domain
```

**Distribution model (decided): packs are authored by organizations inside
their own projects** — baton ships the *format*, not a content registry:

- Packs live in the user's repo at `.claude/packs/<name>/` (extending the
  existing `.claude/policies/<name>.json` override mechanism into the full
  directory contract above). They version with the org's code and never
  leave the org's repo — which matters, since rubrics and knowledge docs
  encode internal policy.
- Baton provides: the pack **schema + loader + validator**
  (`baton packs validate`), scaffolding (`baton packs init <name>`), and
  the `talent-builder` flow — *"turn this regulation / internal policy doc
  into an assurance pack"* (it already builds agents + knowledge packs;
  this is packaging).
- The repo ships only 1–2 **example packs as templates** (e.g. PHI/HIPAA
  and secure-coding/OWASP) under `templates/packs/` so orgs have a worked
  reference for the format — illustrative starting points, not maintained
  compliance content. The 5 built-in presets remain as the zero-config
  fallback.
- `baton classify` consumes per-pack `signals.json` so triage says *which*
  regime applies, not just "regulated".

### B2. Expert review with verifiable verdicts

Today: auditor/SME are required by policy rules and routed as reviewer-class
agents; verdicts are extracted from free-ish text
(`extract_verdict_from_text()` in `core/exec/auditor_gate.py`); the
executable-bead quarantine/approve gate is fully implemented.

Expand:
- **Rubric-driven review**: reviewer prompts are generated from the pack's
  `rubric.md`; verdicts returned as a structured schema (verdict, per-criterion
  findings, severity, **citations into the pack's knowledge docs**), using
  structured outputs (`output_config.format`) instead of text-scanning. This
  replaces the brittle verdict extraction and makes a verdict auditable —
  "blocked because rubric §3.2, citing HIPAA §164.312(a)".
- **Multi-expert fan-out**: a HIGH/CRITICAL task in scope of multiple packs
  gets parallel reviewers (legal + security + compliance) in isolated
  worktrees; the engine records each verdict separately and blocks on any
  `block`-severity finding. (This is the one place baton-driven dispatch is
  load-bearing: the platform won't *force* a review; baton does.)
- **Runtime guardrails, not just plan-time** — see B5.

### B3. Evidence Bundle — make the audit trail a deliverable

Today: hash-chained `compliance-audit.jsonl` (tamper-evident, redaction,
fail-closed mode) + AIBOM (models/agents/MCP servers/gates/chain-anchor,
MD/JSON/SPDX) + gate records + approval records — generated separately.

Expand: `baton evidence bundle <task-id>` emits one artifact per execution:
AIBOM + the task's compliance-chain segment + gate outcomes + structured
review verdicts (B2) + approval record (who requested, who approved, when)
+ the active pack versions. Plus:
- `baton evidence verify` (chain verification, runnable in CI),
- optional PR attachment (the GitHub plumbing already exists in AIBOM's
  `--pr` flag),
- this becomes **the** demo of baton's value: "every AI-written change in a
  regulated repo ships with a verifiable evidence bundle".

### B4. Spec federation + pre-flight review — the PMO UI's primary purpose

Today: `BATON_APPROVAL_MODE=team` enforces requester ≠ reviewer with state
preconditions; REST route `POST /pmo/gates/{task_id}/approve` exists; the
PMO UI is a general execution dashboard.

**Decided direction:** the UI becomes a primary interface, reoriented around
one workflow — **federating specs from multiple team members into a queue
that an architect or senior engineer reviews *before* execution fires**.
The economic rationale: the expensive thing in agentic development is no
longer the typing, it's the tokens — an under-specified task burns a long
autonomous run producing the wrong thing. Pre-flight review is the
cheapest point of control for organizational token spend.

The pipeline (mostly existing machinery, re-sequenced):

```
team member submits spec ──► baton classify + policy attach + cost forecast (cheap)
   (UI form, or imported           │
    from Azure DevOps work         ▼
    item / GitHub issue)    spec-review queue (PMO UI)
                                   │  architect/senior approves, edits, or bounces
                                   ▼  with feedback (requester ≠ reviewer enforced)
                            fire: dispatch to Claude Code (expensive)
                                   │
                                   ▼
                            evidence bundle + actual-cost attribution (B3, B6)
```

Components:
- **Spec intake**: submit in the UI, or import from **Azure DevOps work
  items** (primary integration) and **GitHub Issues** (the GitHub plumbing
  partially exists via the AIBOM `--pr` path). Slack/webhooks are
  secondary — nice-to-have notifications, not a core surface.
- **Pre-flight enrichment**: each queued spec automatically gets the
  classifier's risk/pack determination, required reviewers, derived gates,
  and a cost forecast from historical actuals (B6) — so the reviewer sees
  *what this will cost and what assurance it triggers* before approving.
- **Review queue**: the existing team-approval state machine
  (requester ≠ reviewer) moved to the *front* of the pipeline. Approve /
  edit-spec-then-approve / bounce-with-feedback. Approval recorded into the
  evidence bundle.
- **Spec quality assist**: the gap analysis already noted the new models
  reward a full task spec up front — an optional "rubric check" on
  submission (does the spec state verification criteria, scope boundaries,
  constraints?) raises spec quality before a human ever reviews it.

(Identity is currently a header-derived actor string — real identity
assurance comes from deploying the API behind SSO; don't build auth.)

### B5. Hook-based runtime enforcement (the key integration shift)

Today policy is enforced at plan time only; mid-execution an agent could
still run a blocked tool. Claude Code hooks close this gap **without baton
owning the loop**:

- `PreToolUse` → `baton policy check --tool <name> --path <target>` → deny
  with the rule citation when the active pack blocks it (e.g. Bash on a
  data directory under PHI pack).
- `PostToolUse` / `Stop` → `baton comply record` appends compliance-chain
  entries and runs derived gates.
- Ships in `templates/settings.json` per pack — installing a pack installs
  its hooks.

This is the architectural pivot in one line: **baton stops being the loop
and becomes the checkpoints the loop must pass through.**

### B6. FinOps on actuals

Keep chargeback (org/team/project/cost-center attribution — no native
equivalent) but feed it from `jsonl_scanner` actuals + the single pricing
config (A5). `BATON_RUN_TOKEN_CEILING` stays as the defense-in-depth hard
stop; optionally also pass the remaining ceiling down as an API task budget
so the model self-rations (the one survivor from 006 Phase 3).

### B7. Beads stay

bd-backed structured memory (discoveries/decisions/warnings, typed links,
executable beads behind the auditor gate) feeds the evidence chain and
incident discipline. No change beyond losing the swarm/speculation bead types.

---

## Part C — Repositioning the engine

The state machine stays, but its framing changes from "the orchestrator's
brain" to "the assurance checkpoint sequence":

- **Harness mode (new, default for most users):** no `baton execute` loop.
  Claude Code runs natively; baton participates via hooks (B5), `baton
  classify` at session start (via SessionStart hook), and `baton evidence
  bundle` at the end. Zero protocol overhead for unregulated work.
- **Managed mode (existing, for regulated work):** the full
  plan → DISPATCH → GATE → APPROVAL loop, which is what *forces* expert
  review and segregation of duties. This is opt-in by classification: the
  `regulated`/pack presets require it; `standard_dev` doesn't.

README/docs reframe accordingly: lead with assurance packs + evidence
bundles; the orchestration engine is the enforcement mechanism for the
cases that need it, not the headline.

---

## Relationship to Proposal 006

| 006 item | Disposition |
|---|---|
| Phase 0.1 four-pricing-tables fix | **Superseded** — A5 deletes three tables; the one survivor gets current prices |
| Phase 0.2 goal-evaluator model repoint | **Keep** (small, correct regardless) |
| Phase 0.3 settings.json thinking cap removal, 0.4 guardrail-preset wording | **Keep** |
| Phase 1 model catalog, Phase 2 Fable tier routing / self-heal-fable | **Dropped** — investments in the layers being deleted |
| Phase 3.3 structured outputs | **Kept, redirected** to review verdicts (B2) |
| Phase 3.2 task budgets | **Kept, demoted** to optional ceiling passthrough (B6) |
| Phase 4–5 distributables/doc sweep | **Folded in**, reduced by the deletions |

## Sequencing (decided: deletions first, then quick wins)

1. **PR 1–3 — deletions**, one subsystem each, no deprecation cycle:
   predict → swarm → selfheal (selfheal last; it touches persisted state +
   golden regen). Banks the LOC reduction immediately and clears the ground
   the rest builds on.
2. **PR 4 — quick wins** (006 survivors): goal-evaluator repoint,
   settings.json thinking cap, guardrail-preset wording, single pricing
   config with current prices (simpler now — two of the four tables died
   with their subsystems in PR 2–3).
3. **PR 5 — hooks integration** (B5) + harness mode docs: `baton policy
   check`, `baton comply record`, template hook wiring. Highest-value *new*
   code, independent of everything above.
4. **PR 6 — assurance pack format** (B1): schema + loader + validator +
   `baton packs init|validate`, migrate the 5 presets onto the loader,
   2 example packs under `templates/packs/`, talent-builder flow.
5. **PR 7 — verdict schema + evidence bundle** (B2, B3): structured review
   verdicts, `baton evidence bundle|verify`.
6. **PR 8 — spec federation** (B4): spec intake + pre-flight enrichment +
   review queue in the PMO UI; Azure DevOps work-item import first,
   GitHub Issues second.
7. **PR 9 — planner slimming** (A4) + repositioned docs/README.
8. Later: multi-expert fan-out (B2), spec-quality rubric check (B4),
   notifications.

## Success criteria

- Engine LOC down ≥ 30% with the full remaining test suite green.
- A regulated-repo demo: classification → pack activation → hook-blocked
  tool call with rule citation → SME+auditor structured verdicts → team
  approval → one-command evidence bundle that `baton evidence verify` passes.
- **The spec-federation loop works end-to-end**: a team member submits a
  spec (or imports an Azure DevOps work item), the queue shows risk + cost
  forecast + required assurance, a senior engineer approves, execution
  fires, and the actual cost lands in chargeback attributed to the spec.
- An unregulated repo gets value from baton with **zero** loop overhead
  (hooks + chargeback + beads only).
- No estimation-vs-actual cost discrepancy remains (single pricing config,
  actuals-based reporting).

## Decisions (resolved 2026-06-10)

1. **Deprecation window** — none. No users of swarm/predict/self-heal;
   delete directly, one PR per subsystem.
2. **Pack distribution** — packs are authored by organizations within their
   own projects (`.claude/packs/`). Baton ships the format, loader,
   validator, scaffolding, and example templates — not a content registry.
3. **PMO UI** — becomes a primary interface, purpose-built for spec
   federation + pre-flight senior review to control organizational token
   spend. Azure DevOps and GitHub Issues are the integration targets;
   Slack/webhooks are secondary.
4. **Implementation order** — deletions first, then quick wins, then the
   build-out (hooks → packs → evidence → spec federation).
