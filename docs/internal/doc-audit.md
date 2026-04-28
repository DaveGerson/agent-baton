# Agent Baton Documentation Audit

> Internal working document. Last updated: 2026-04-28.
> Companion to `docs/internal/doc-guiding-principles.md`. This audit
> classifies every Markdown file under `docs/`, plus `README.md` and
> `CLAUDE.md`, against the guiding principles and ground-truths the
> classification with spot-checks against the codebase.

Scope: 84 files. Three top-level files (`README.md`, `CLAUDE.md`,
`CONTRIBUTING.md`) plus everything under `docs/`. The companion files
`QUICKSTART.md` and `SECURITY.md` were also reviewed because the
adopter journey routes through them.

Verification budget: 2-3 spot-checks per long file. Counts cited in
docs were re-derived from `agents/`, `references/`, and
`agent_baton/cli/commands/`. CLI flags spot-checked via `baton --help`
and `baton execute --help`. Source: this commit (master, post bd-2b9f).

---

## Per-File Audit

| Path | Quadrant | Action | Reason | Defects | Owner-after-rewrite |
|---|---|---|---|---|---|
| `README.md` | Mixed (landing + reference dump) | REWRITE | §5.3: README must be one-screen pitch + install + 1 forward link. Currently 779 lines with full CLI tables, feature catalogue, env-var reference. | Stale: claims "19 specialist agents" / "22 agents" / "16 references"; actual `agents/` = 33, `references/` = 18. Contains banned marketing words ("powerful", "intelligent planning"). Aspirational features ("`baton evolve`", "`baton experiment`") not in `baton --help`. License "pending". | `README.md` (slim) + `docs/cli-reference.md` for tables |
| `CLAUDE.md` (root) | Agent index | REWRITE | §4.2 cap ≤200 lines (currently 70 — OK on length). Stale facts though: claims "47 agents" and "20 .md files" when `agents/` has 33. References `cli/commands/execution/execute.py` correctly. | Stale: "20 .md files" agents, "16 .md files" references (actual 18), and the link "full roster of 47 agents" contradicts `agent-roster.md` (claims 20+6+18). | `CLAUDE.md` (recount, link to roster) |
| `CONTRIBUTING.md` | How-to + Reference | REWRITE | Stale test count "~3900 tests" vs project's ~6202. Claims `License pending`. References `docs/architecture.md`, `docs/invariants.md`, `CLAUDE.md` correctly. | Stale: pytest count; conflicting license language with README. | `CONTRIBUTING.md` (slim, point to internal/ for deep reading) |
| `QUICKSTART.md` | Tutorial (mixed with how-to) | SPLIT or DELETE | §5: tutorial belongs at `docs/examples/first-run.md`. QUICKSTART is 554 lines and duplicates first-run. Two tutorials = none. | Duplication of `docs/examples/first-run.md`; ambiguous adopter entry point (README links to BOTH). | Merge into `docs/examples/first-run.md` |
| `SECURITY.md` | Reference | KEEP_AS_IS | 43 lines, vulnerability reporting, standard form. | None observed. | `SECURITY.md` |
| `docs/index.md` | Landing | REWRITE (small) | §5.4: tighten to 4-card menu. Currently re-pitches Baton instead of routing. Cites "47 agents". | Stale 47-agents count. Banned: "powerful". | `docs/index.md` |
| `docs/agent-roster.md` | Reference | REWRITE | §4.6: must be auto-generated from `agents/*.md` frontmatter. Currently hand-curated and wrong: claims "Packaged agents (20)" + "Meta agents (6)" + "GSD framework agents (18)". `agents/` actually has 33 files. Roles like `swarm-reconciler`, `team-lead`, immune-* missing entirely from the table. | Stale agent counts; unlisted: `swarm-reconciler`, `team-lead`, `immune-*` (5), `self-heal-haiku/sonnet/opus`, `speculative-drafter`, `learning-analyst`, `task-runner`. | `docs/agent-roster.md` (regen from frontmatter) |
| `docs/orchestrator-usage.md` | How-to | KEEP_AS_IS (light edit) | §2.2 marked OK; 82 lines. Token-reduction SOPs are legitimate task recipes. | One forward link missing (no link to `cli-reference.md` for `--terse`). | `docs/orchestrator-usage.md` |
| `docs/cli-reference.md` | Reference | REWRITE | §4.6 must mirror `cli/commands/` 1:1. Source has ~14 group dirs and 84+ subcommand modules; doc lists "Ten groups" / "49 commands". Multiple referenced commands don't exist (`evolve`, `experiment`). Missing entire groups: `swarm`, `predict`, `bead`, `souls`, `tenancy`, `sync` subgroup, `release`, `webhook`, `merge`, `assess`, `debate`, `quickstart`, `spec`. | Drift from source; 2185 lines but inconsistent depth; tables don't agree with `--help` output. | Auto-generated `docs/cli-reference.md` (split per group if needed) |
| `docs/api-reference.md` | Reference | REWRITE | §4.6 must mirror `api/routes/` 1:1. Doc enumerates 9 endpoint groups; actual `routes/` contains 14 modules including `metrics.py`, `noc.py`, `pmo_h3.py`, `specs.py` not in the doc. | Missing routes; "Started via `baton daemon start --serve`" — also true via `baton serve`/`baton pmo serve`. | Auto-generated from FastAPI app + `routes/*` |
| `docs/architecture.md` | Mixed (Explanation + Reference) | SPLIT | §2.3 explicit call: split into Explanation (≤500 lines) + `architecture/package-layout.md` (Reference) + `architecture/state-machine.md` (Reference). Currently 1979 lines, three boxes-and-arrows diagrams of the same flow, "(49 commands)" prose count. | Diátaxis violation; conflicting command count vs README/cli-reference; ASCII diagrams duplicated. | `docs/architecture.md` + `docs/architecture/package-layout.md` + `docs/architecture/state-machine.md` |
| `docs/architecture/phase-0-foundations/rollback-recipe.md` | How-to | RELOCATE → `docs/internal/migrations/v16-rollback.md` | Phase-0 v16 schema migration is point-in-time operator material; not part of the public Diátaxis architecture node. | Misfiled under `architecture/` which is reserved for the split target above. | `docs/internal/migrations/` |
| `docs/engine-and-runtime.md` | Explanation | SPLIT | §9.13 cap ≤500 lines for explanation. Currently 1570. Pair with new `architecture.md`. | Length violation; absorbs reference material that should live in cli/api references. | `docs/engine-and-runtime.md` (slim) + reference shards into `architecture/state-machine.md` |
| `docs/governance-knowledge-and-events.md` | Explanation | SPLIT | 1526 lines; three subsystems in one file. Diátaxis "single concern" call. | Length violation. | `docs/governance.md` + `docs/knowledge-delivery.md` + `docs/events.md` (each ≤500 lines) |
| `docs/observe-learn-and-improve.md` | Explanation | SPLIT | 1791 lines — same anti-pattern as governance file. ASCII "learning loop" doubles as the only diagram (keep). Lists banned words ("powerful pipeline" etc — verify on rewrite). | Length violation. | `docs/observe.md` + `docs/learn.md` + `docs/improve.md` |
| `docs/storage-sync-and-pmo.md` | Explanation | SPLIT | 1785 lines. PMO and storage are different consumer audiences (PMO → adopter; storage → maintainer). | Length + audience violations. | `docs/storage.md` + `docs/sync-and-central-db.md` + `docs/pmo.md` |
| `docs/design-decisions.md` | Explanation (ADR log) | KEEP_AS_IS | §2.2 OK. 1097 lines is acceptable for an ADR log if each entry is date-stamped (it is). | Verify all ADRs date-stamped; at least one is "Implemented (2026-03-23)" — passes. | `docs/design-decisions.md` |
| `docs/invariants.md` | Mixed (Reference + Explanation) | SPLIT | §2.3 explicit call: split the three invariant statements (Reference) from rationale prose (fold into `architecture.md`). | Diátaxis violation. | `docs/invariants.md` (Reference) + rationale folded into `architecture.md` |
| `docs/troubleshooting.md` | How-to (symptom-keyed) | KEEP_AS_IS | §2.2 marked OK. 149 lines, well-formed Symptom/Cause/Fix structure. | One example uses `--step 1.1` (correct flag is `--step-id`); verify each command. | `docs/troubleshooting.md` |
| `docs/finops-chargeback.md` | Explanation + How-to | SPLIT | 255 lines but mixes operator walkthrough with conceptual rationale. §2.2 says pair with future how-to. | Mixed quadrant. | `docs/finops/concepts.md` (explanation) + `docs/how-to/finops-attribute-costs.md` (how-to) |
| `docs/daemon-mode-evaluation.md` | Explanation (historical eval) | MOVE_TO `docs/internal/` | §2.2 says mark historical and not let users mistake for usage. Better: fully internal. Date 2026-03-27, branch reference `claude/daemon-mode-evaluation-DsZHj`. | Status doc, not user-facing usage; risk of mistake. | `docs/internal/evaluations/daemon-mode-2026-03.md` |
| `docs/baton-engine-bugs.md` | Reference (bug list) | MOVE_TO `docs/internal/` | §2.2 says auto-generate from issue tracker if possible. Captured bugs from "PMO UX Phase 4 execution" and "documentation overhaul session" — still operator-developer triage, not user-facing reference. | Bugs stale or fixed by date; not a stable reference. | `docs/internal/known-issues.md` (or auto-gen from beads) |
| `docs/PRODUCTION_READINESS.md` | Explanation (status report) | MOVE_TO `docs/internal/archive/` | §6.5: status pages must be date-stamped (it is, 2026-04-05) and §7.1 says delete if older than 90 days. Now ~3 weeks old; relocate, do not surface publicly. | Stale facts: "67 data models", "43 CLI commands" (now 60+), "59 core Python modules", "~3,900+ tests" (current ~6202). | `docs/internal/archive/production-readiness-2026-04-05.md` |
| `docs/pyright-diagnostics-triage.md` | Internal (working doc) | MOVE_TO `docs/internal/` | §2.2 explicit call. Already noted: line numbers may have shifted. Not user-facing. | None as internal doc. | `docs/internal/pyright-diagnostics-triage.md` |
| `docs/examples/first-run.md` | Tutorial | KEEP_AS_IS (audit run) | §2.2 canonical tutorial. 201 lines. Must run end-to-end on fresh checkout — to be CI-tested. | Uses `$` prompts (banned by §3.5). | `docs/examples/first-run.md` |
| `docs/examples/knowledge-packs/api-conventions-example.md` | Tutorial (auxiliary) | KEEP_AS_IS | §2.2 OK. Self-contained pack example. | Spot-check links not done. | `docs/examples/knowledge-packs/` |
| `docs/examples/knowledge-packs/compliance-example.md` | Tutorial (auxiliary) | KEEP_AS_IS | Same as above. | — | `docs/examples/knowledge-packs/` |
| `docs/terminology.md` | Reference (glossary) | REWRITE | §3.3 canonical glossary; must list every term referenced in style guide. Missing: "bead", "subagent", "engine", "planner", "orchestrator" (only "Orchestrator" w/ a different gloss), "model tier" (present but missing entry), "knowledge document", "central.db", "task ID resolution". 43 lines is too short. | Missing canonical terms; conflicts with style-guide section 3.3 definitions of "engine", "agent" vs "subagent". | `docs/terminology.md` (rewrite to canonical) |
| `docs/audit/AUDIT-REPORT.md` | Internal (audit) | MOVE_TO `audit-reports/` (already exists at root) | §2.2 explicit call. Internal artifact. | Date 2026-03-24; numbers stale. | `audit-reports/` |
| `docs/audit/chains-1-3.md` | Internal (audit) | MOVE_TO `audit-reports/chains/` | Same call. | Stale. | `audit-reports/chains/` |
| `docs/audit/chains-4-6.md` | Internal | MOVE_TO `audit-reports/chains/` | Same. | Stale. | `audit-reports/chains/` |
| `docs/audit/chains-7-9.md` | Internal | MOVE_TO `audit-reports/chains/` | Same. | Stale. | `audit-reports/chains/` |
| `docs/audit/chains-10-12.md` | Internal | MOVE_TO `audit-reports/chains/` | Same. | Stale. | `audit-reports/chains/` |
| `docs/audit/cross-chain-and-orphans.md` | Internal | MOVE_TO `audit-reports/chains/` | Same. | Stale. | `audit-reports/chains/` |
| `docs/reviews/agent-feedback-audit-plan.md` | Internal (review) | MOVE_TO `docs/internal/reviews/` | §2.2 says move to internal. | — | `docs/internal/reviews/` |
| `docs/reviews/pmo-ux/AUDIT.md` | Internal (review) | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/ISSUES.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/REMEDIATION-PLAN.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/architecture-fitness.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/interaction-analysis.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/scenarios.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/reviews/pmo-ux/workflow-audit.md` | Internal | MOVE_TO `docs/internal/reviews/pmo-ux/` | Same. | — | same |
| `docs/competitive-audit/INCIDENT-persistence-split-brain.md` | Internal | MOVE_TO `docs/internal/competitive/` | §2.2 explicit. | — | `docs/internal/competitive/` |
| `docs/competitive-audit/INCIDENT-plan-explosion.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/persona-james-david.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/persona-maya-carlos.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/persona-priya-tomoko.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/SYNTHESIS.md` | Internal | DELETE (superseded) | Three synthesis files exist (SYNTHESIS, SYNTHESIS-v2, SYNTHESIS-FINAL); keep only the FINAL. | Duplication. | — |
| `docs/competitive-audit/SYNTHESIS-v2.md` | Internal | DELETE (superseded) | Same. | Duplication. | — |
| `docs/competitive-audit/SYNTHESIS-FINAL.md` | Internal | MOVE_TO `docs/internal/competitive/` | Final keeper. | — | `docs/internal/competitive/` |
| `docs/competitive-audit/team-carlos-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Bulk relocation. | — | same |
| `docs/competitive-audit/team-david-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/team-james-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/team-maya-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/team-priya-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/team-tomoko-expert.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/theme-1-4-governance-planning.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/theme-2-5-observability-learning.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/competitive-audit/theme-3-6-remote-integration.md` | Internal | MOVE_TO `docs/internal/competitive/` | Same. | — | same |
| `docs/specs/agent-teams-enablement.md` | Explanation (RFC log) | KEEP_AS_IS | §2.2 OK. RFC, dated 2026-03-28. | Status "Proposal" — verify whether shipped (swarm-reconciler agent now exists). | `docs/specs/` |
| `docs/specs/daemon-mode-roadmap.md` | Explanation (RFC log) | KEEP_AS_IS | OK. Status "Proposal" 2026-03-27. | Re-status: most phases shipped per memory; mark accordingly. | `docs/specs/` |
| `docs/specs/interactive-team-journeys.md` | Explanation (RFC log) | KEEP_AS_IS | OK. | — | `docs/specs/` |
| `docs/specs/strategic-remediation-roadmap.md` | Explanation (RFC log) | KEEP_AS_IS | OK; 76 lines. | — | `docs/specs/` |
| `docs/specs/velocity-engine-spec.md` | Explanation (RFC log) | KEEP_AS_IS | "Version 3.1 — April 2026"; banned word "powerful" + marketing tone. RFC tolerance per §6.6. | Banned words inside an aspirational RFC; acceptable per Appendix B but flag for cleanup. | `docs/specs/` |
| `docs/superpowers/plans/2026-03-24-forge-smart-plan-generation.md` | Explanation (plan/spec) | KEEP_AS_IS | Plans archive. | Date-stamped already. | `docs/superpowers/plans/` |
| `docs/superpowers/plans/2026-03-26-adaptive-plan-sizing.md` | Plan archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/plans/2026-04-12-bead-memory-execution.md` | Plan archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-comprehensive-functionality-audit-design.md` | Spec archive | KEEP_AS_IS | — | — | `docs/superpowers/specs/` |
| `docs/superpowers/specs/2026-03-24-concurrent-execution-isolation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-federated-sync-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-intelligent-delegation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-knowledge-delivery-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-pipeline-gap-closure-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-24-pmo-ux-review-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-03-26-adaptive-plan-sizing-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-12-bead-memory-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-13-learning-automation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-14-actiontype-interact-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-15-flag-escalation-system-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-15-step-execution-taxonomy-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-17-audit-remediation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/superpowers/specs/2026-04-25-strategic-roadmap-remediation-design.md` | Spec archive | KEEP_AS_IS | — | — | same |
| `docs/internal/INSTALL-PROMPT.md` | Internal | DELETE | §7.1 explicit "delete; do not relocate". Replaced by `scripts/install.sh` and `baton install`. | Outdated workflow. | — |
| `docs/internal/CODEBASE_REVIEW.md` | Internal | KEEP_AS_IS (already internal) | OK; verify not linked from public nav. | Date 2026-03-21; security findings may be partially fixed. | `docs/internal/` |
| `docs/internal/REVIEW-consulting-delivery-platform.md` | Internal | KEEP_AS_IS | Same. | — | `docs/internal/` |
| `docs/internal/TODO-001-review-findings.md` | Internal | MOVE_TO `docs/internal/archive/` | Closed work item from 2026-03-22; preserve for context but archive. | — | `docs/internal/archive/` |
| `docs/internal/doc-guiding-principles.md` | Reference (style guide) | KEEP_AS_IS | This audit's authority document. | — | `docs/internal/` |
| `docs/internal/README.md` | Reference (small) | KEEP_AS_IS | 4 lines. Adequate. | — | `docs/internal/` |

---

## Coverage Gaps

The following pages are referenced or implied by the principles document
or by `CLAUDE.md` but do not exist (or exist in degraded form). Each is a
required addition.

1. **`docs/architecture/high-level-design.md`** — referenced in
   `~/.claude/CLAUDE.md` ("Documentation Maintenance" mandate) and
   project `CLAUDE.md`. Missing entirely. The Diátaxis explanation node
   currently lives in the over-long `docs/architecture.md` and must be
   extracted into a high-level design doc.
2. **`docs/architecture/technical-design.md`** — referenced in the same
   mandate. Missing. Should host the detailed subsystem patterns
   currently scattered across `engine-and-runtime.md`,
   `governance-knowledge-and-events.md`, and
   `observe-learn-and-improve.md`.
3. **`docs/architecture/package-layout.md`** — required by §2.3 split
   call for `architecture.md`.
4. **`docs/architecture/state-machine.md`** — required by §7.3.
5. **Per-task how-tos** — §2.1 implies a how-to page per task. Today
   `docs/orchestrator-usage.md` is one file containing several recipes.
   Required separates: "How to dispatch a team", "How to amend a plan",
   "How to resume after a crash", "How to run headless", "How to
   override planner agent selection", "How to attribute FinOps costs".
6. **`docs/cli-reference/` per-group split** — §7.3 calls for a split
   if rendering is poor; current 2185-line monolith definitely
   qualifies.
7. **`docs/glossary` additions** — §3.3 mandates `terminology.md` cover
   "bead", "engine", "planner", "subagent" vs "agent", "model tier".
   Missing.
8. **`/llms.txt`** — §4.1 explicit "yes, ship". Not present at repo
   root. Required.
9. **Tutorial CI harness** — §6.7 mandates CI runs
   `docs/examples/first-run.md` end-to-end. No harness exists.
10. **CLI surface diff** — §6.7 mandates CI compares
    `docs/cli-reference.md` H2 list to `agent_baton/cli/commands/`.
    Drift confirmed; harness missing.
11. **Path:line link checker** — §6.3 mandates one. None present.
12. **"How to resume a crashed execution" page** — referenced in
    README but only appears as a Symptom row in troubleshooting. The
    user journey deserves a dedicated how-to.
13. **PMO UI user docs** — `pmo-ui/` is featured in README but no
    user-facing page covers Kanban columns, Smart Forge, or approval
    workflow. Implied by §1.2 (third audience). Missing.
14. **Webhooks how-to** — README mentions "Outbound webhook
    subscription CRUD" with HMAC-SHA256 signing; no how-to or worked
    example.
15. **Beads concept page** — `baton beads` commands referenced
    everywhere, but no Explanation page for the bead memory model
    beyond what's buried in `governance-knowledge-and-events.md`.
16. **Cross-project query reference** — `baton cquery` is mentioned in
    README but not documented in `cli-reference.md`'s grouped tables.

---

## Duplication Map

Groups of files that say overlapping things, with the recommended
consolidation target.

### Cluster A — README + index + QUICKSTART + first-run

- `README.md` (779L)
- `docs/index.md` (42L, duplicates README pitch)
- `QUICKSTART.md` (554L)
- `docs/examples/first-run.md` (201L)

All four open with the same pitch and "Use the orchestrator to add a
health check" code snippet. Per §5, the canonical reading path is
`README → docs/index.md → docs/examples/first-run.md`. **Consolidate**:
slim `README.md` to ≤200 lines, slim `docs/index.md` to a 4-card menu,
delete `QUICKSTART.md` (or absorb its richer install-scope guidance into
`README.md` and `first-run.md`).

### Cluster B — architecture monoliths

- `docs/architecture.md` (1979L)
- `docs/engine-and-runtime.md` (1570L)
- `docs/governance-knowledge-and-events.md` (1526L)
- `docs/observe-learn-and-improve.md` (1791L)
- `docs/storage-sync-and-pmo.md` (1785L)

These five files together total 8651 lines and overlap heavily — every
one re-establishes the layered architecture, the state machine, and the
plan/phase/step vocabulary. **Consolidate** into the new
`docs/architecture/` tree:

- `architecture/high-level-design.md` (the *why*, ≤500L) — extract from
  `architecture.md`.
- `architecture/technical-design.md` or one-file-per-subsystem
  (`engine.md`, `governance.md`, `knowledge.md`, `events.md`,
  `observe.md`, `learn.md`, `improve.md`, `storage.md`, `sync.md`,
  `pmo.md`).
- `architecture/package-layout.md` — Reference, generated.
- `architecture/state-machine.md` — Reference, generated from the
  `ActionType` enum.

### Cluster C — competitive audit synthesis

- `docs/competitive-audit/SYNTHESIS.md`
- `docs/competitive-audit/SYNTHESIS-v2.md`
- `docs/competitive-audit/SYNTHESIS-FINAL.md`

Three drafts of the same synthesis. **Consolidate**: keep
`SYNTHESIS-FINAL.md` only, move it to `docs/internal/competitive/`,
delete the others.

### Cluster D — install instructions

- `README.md` "Get Started in 5 Minutes"
- `QUICKSTART.md` "Step 1: Install"
- `docs/examples/first-run.md` Prerequisites
- `docs/internal/INSTALL-PROMPT.md` (Claude-paste workflow)

Four install paths, three of them documented at length. **Consolidate**
into one canonical install how-to (`docs/how-to/install.md`) reachable
from README and first-run; delete `INSTALL-PROMPT.md`.

### Cluster E — agent counts

- `README.md` lines 108, 162, 638, 700, 745: claims 19 / 22 agents.
- `CLAUDE.md` line 11/32: claims 20 + 47.
- `docs/index.md` line 35: claims 47.
- `docs/agent-roster.md` lines 4–6: claims 20+6+18 = 44.

Reality: `agents/` contains 33 files at this commit. **Consolidate** by
making `docs/agent-roster.md` an auto-generated index (sourced from
`agents/*.md` frontmatter) and replacing all prose counts with
`{{agent_count}}` substitution or a generated line.

### Cluster F — CLI command counts

- `README.md` line 472: "50+ commands organized into ten groups".
- `docs/architecture.md` line 46: "(49 commands)".
- `docs/PRODUCTION_READINESS.md` line 17: "43 CLI commands".
- `docs/cli-reference.md` line 17: lists 12 group rows.

Reality: 12 top-level groups visible in `baton --help`, 84+ subcommand
modules. **Consolidate** by generating these counts from
`agent_baton/cli/commands/`.

### Cluster G — references list

- `README.md` line 162: "15 Reference Procedures" — table lists 15.
- `CLAUDE.md` line 12: "16 .md files".
- `docs/architecture.md` (Three Interfaces) implicitly assumes
  references are static.

Reality: `references/` contains 18 files. **Consolidate**: same
auto-generation strategy.

---

## Stale Facts Discovered

Concrete, ground-truth-checked findings from this audit. Each is a
**file:claim — actual** triple.

1. `README.md:108-126` — "19 Specialist Agents" with a 12-row table.
   `agents/` directory contains **33** agent definitions including
   `swarm-reconciler`, `team-lead`, `task-runner`, `talent-builder`,
   `system-maintainer`, `learning-analyst`, `speculative-drafter`,
   five `immune-*` agents, three `self-heal-*` agents — all missing
   from the README table.
2. `README.md:162-185` — "15 Reference Procedures" with a 15-row
   table. `references/` contains **18** files; missing rows:
   `compliance-audit-chain.md`, `planning-taxonomy.md`,
   `team-messaging.md`.
3. `README.md:294-306` — Lists `baton evolve` and `baton experiment` as
   commands. **Neither appears** in `baton --help`. Closest existing
   surface: `baton learn` subcommands and `baton improve`. These are
   aspirational mentions that violate §6.6.
4. `README.md:472` — "ten groups". `docs/cli-reference.md:17` — table
   lists **twelve** rows. `docs/architecture.md:46` — "(49 commands)".
   `docs/PRODUCTION_READINESS.md:17` — "43 CLI commands". Reality:
   12 top-level groups, 84+ command modules.
5. `README.md:639` and `CLAUDE.md:11` — "22 agent definitions" /
   "20 .md files". Reality: **33**.
6. `CLAUDE.md:32` — "the full roster of 47 agents". Reality: 33 in
   `agents/`. The "47" claim is unsourced.
7. `docs/agent-roster.md:4-6` — "Packaged agents (20)" — reality 33.
   "Meta agents (6)" — file lists 6 names but only the meta agents in
   `.claude/agents/` (not in `agents/`) are captured. Roster does not
   match either source.
8. `README.md:329` — "10 route modules". `agent_baton/api/routes/`
   contains **14** files (`agents.py`, `decisions.py`, `events.py`,
   `executions.py`, `health.py`, `learn.py`, `metrics.py`, `noc.py`,
   `observe.py`, `plans.py`, `pmo.py`, `pmo_h3.py`, `specs.py`,
   `webhooks.py`). API reference doc enumerates only 9 groups.
9. `README.md:712` and `CLAUDE.md:39` — "~6202 tests". `CONTRIBUTING.md:12`
   — "~3900 tests". `docs/PRODUCTION_READINESS.md:17` — "~3,900+ tests".
   `docs/audit/AUDIT-REPORT.md:9` — "3,744 tests". Pick one source;
   regenerate.
10. `CONTRIBUTING.md:106` — "License terms are pending". `README.md:779`
    — "License pending. Contact the maintainers for terms." Both
    consistent but adopter-hostile; flagged by `feedback_ip_publishing`.
    (Note: per user memory, this is intentional until employer IP rules
    clarified — keep as is, but be explicit.)
11. `docs/architecture.md:39-60` — three boxes-and-arrows diagrams
    duplicate the same flow. §7.5 explicit call: pick one.
12. `docs/architecture.md:46` — "(49 commands)" embedded in ASCII
    diagram. Replace with generated line per §7.5.
13. `docs/troubleshooting.md` (multiple) — uses `--step 1.1`. The
    actual flag in `baton execute record --help` is `--step-id`. Spot
    grep confirms `_validators.py` resolves both forms but doc should
    cite the canonical one.
14. `README.md:48-52` — `scripts/install.ps1` for Windows. Verify the
    script is named correctly (it is, at `scripts/install.ps1`).
15. `docs/PRODUCTION_READINESS.md:17` — "67 data models". §6.5
    requires status pages be archived when superseded; this is now
    23 days old and the count has drifted.
16. `docs/internal/INSTALL-PROMPT.md:30` — instruction "verify ~19
    agents are listed" hard-coded. §7.1 explicit DELETE.

---

## Recommended Sequence

Order matters. Each phase unblocks the next. Aggressive cuts first;
rewrites second; new content last.

### Phase 1 — Cuts and relocations (low risk, high signal)

1. Delete `docs/internal/INSTALL-PROMPT.md`.
2. Delete `docs/competitive-audit/SYNTHESIS.md` and
   `docs/competitive-audit/SYNTHESIS-v2.md` (keep `-FINAL`).
3. Move `docs/audit/*` → `audit-reports/chains/` (or
   `docs/internal/`).
4. Move `docs/competitive-audit/*` → `docs/internal/competitive/`.
5. Move `docs/reviews/*` → `docs/internal/reviews/`.
6. Move `docs/pyright-diagnostics-triage.md` → `docs/internal/`.
7. Move `docs/PRODUCTION_READINESS.md` →
   `docs/internal/archive/production-readiness-2026-04-05.md`.
8. Move `docs/daemon-mode-evaluation.md` →
   `docs/internal/evaluations/daemon-mode-2026-03.md`.
9. Move `docs/baton-engine-bugs.md` → `docs/internal/known-issues.md`.
10. Move `docs/internal/TODO-001-review-findings.md` →
    `docs/internal/archive/`.
11. Move `docs/architecture/phase-0-foundations/rollback-recipe.md` →
    `docs/internal/migrations/v16-rollback.md` (frees the
    `architecture/` namespace for the split).

### Phase 2 — Fix counts everywhere

Replace every prose count with a generated value or remove. Targets:
agent count (33 → derived), reference count (18 → derived), CLI
command/group counts (12 / 84+ → derived), test count (regenerate from
last `pytest` run), API route count (14 modules). Touch: `README.md`,
`CLAUDE.md`, `CONTRIBUTING.md`, `docs/index.md`, `docs/agent-roster.md`,
`docs/architecture.md`, `docs/cli-reference.md`,
`docs/api-reference.md`. Commit per file group.

### Phase 3 — Slim README + landing

Rewrite `README.md` to §5.3 spec (~200 lines). Rewrite `docs/index.md`
to §5.4 spec (4-card menu). Decide: delete `QUICKSTART.md` or fold
unique content into `docs/examples/first-run.md`.

### Phase 4 — Split the monoliths

For each, produce the new file set with explicit cross-links:

1. `docs/architecture.md` (1979L) → `architecture/high-level-design.md`
   + `architecture/package-layout.md` + `architecture/state-machine.md`.
2. `docs/engine-and-runtime.md` (1570L) → `architecture/engine.md` +
   `architecture/runtime.md` (or fold into technical-design.md).
3. `docs/governance-knowledge-and-events.md` (1526L) →
   `architecture/governance.md` + `architecture/knowledge.md` +
   `architecture/events.md`.
4. `docs/observe-learn-and-improve.md` (1791L) →
   `architecture/observe.md` + `architecture/learn.md` +
   `architecture/improve.md`.
5. `docs/storage-sync-and-pmo.md` (1785L) → `architecture/storage.md`
   + `architecture/sync.md` + `architecture/pmo.md`.
6. `docs/invariants.md` → keep three invariant statements only; fold
   rationale into `architecture/high-level-design.md`.

### Phase 5 — Regenerate references

1. Auto-generate `docs/agent-roster.md` from `agents/*.md` frontmatter.
2. Auto-generate `docs/cli-reference.md` (or split per group) from
   `agent_baton/cli/commands/`.
3. Auto-generate `docs/api-reference.md` (or sections) from FastAPI
   route modules.
4. Expand `docs/terminology.md` to cover every term named in the
   guiding principles (§3.3): bead, subagent vs agent, engine,
   planner, model tier, knowledge document, central.db, task ID
   resolution.

### Phase 6 — Add missing how-tos and tutorials

1. Split `docs/orchestrator-usage.md` into per-task how-tos under
   `docs/how-to/`.
2. New `docs/how-to/install.md`.
3. New `docs/how-to/resume-a-crashed-execution.md`.
4. New `docs/how-to/dispatch-a-team.md`.
5. New `docs/how-to/amend-a-plan.md`.
6. New `docs/how-to/run-headless.md`.
7. New `docs/how-to/attribute-finops-costs.md` (paired with the
   existing `docs/finops-chargeback.md` explanation).
8. New `docs/how-to/use-webhooks.md`.
9. New `docs/explanation/beads.md` (carved out of the governance
   monolith).
10. New `docs/pmo/` user-facing docs (Kanban, Smart Forge, approvals).

### Phase 7 — Discoverability

1. Add `/llms.txt` at repo root with the structure in §4.1.
2. Add tutorial CI harness (extracts and runs `bash` blocks from
   `docs/examples/first-run.md`).
3. Add CLI-surface-diff CI check.
4. Add path:line link checker.

### Phase 8 — Style sweep

Final pass for §3 violations across every remaining file: banned words
("powerful", "seamless", "robust", "lightning-fast", "production-grade",
"world-class", "simply", "of course", "obviously"), `$` shell prompts,
`&&` chains in tutorials, hedging, future tense outside `docs/specs/`,
and duplicate "what is Baton" overviews. Apply §9 review checklist
before merging each PR.

---

## Notes on the Split Mechanics

When splitting a long Explanation file (Phase 4), preserve the existing
H2 anchors as redirect stubs in the original file for one release cycle
per §4.4. The CLI prints anchored URLs into delegation prompts in
multiple places; renames are observable failures, not silent ones.
Verify with `grep -rn 'docs/architecture.md#' .claude/ agents/
references/ agent_baton/` before deleting any old anchor.
