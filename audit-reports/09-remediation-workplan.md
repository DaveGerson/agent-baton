# Agent Baton — Remediation Workplan

**Generated:** 2026-08-14 · **Branch:** `claude/consort-agent-baton-compare-nn1a4d`
**Source:** 14 parallel Opus domain auditors + 1 cross-cutting completeness critic (2.0M tokens, 947 tool calls, 70 min)

## Executive summary

A fan-out audit of every domain in agent-baton produced **103 evidence-backed findings**:
**21 critical**, **47 high**, **31 medium**, **4 low**.

The dominant theme is not missing features — it is **silent failure**. 30 findings
(29%) describe a capability that reports success while doing nothing, or
swallows a hard error into a warning. This is the single most consequential pattern in the codebase, because
it defeats the project's own stated guiding principle #5 ("Could this functionality be failing silently?").

The second theme is **decorative controls**: governance mechanisms that exist, are documented, are tested, and
do not actually hold — a compliance hash chain that is not chained, segregation-of-duties resting on a spoofable
env var, a policy loader that disables all enforcement when its JSON is malformed, and a CI pipeline running
3.4% of the suite while the contributor docs forbid running it locally on the grounds that CI covers it.

### Findings by category

| Category | Count | Reading |
|---|---|---|
| silent-failure | 30 | Reports success, does nothing |
| correctness | 23 | Produces a wrong result |
| capability-gap | 18 | Documented capability absent or inert |
| doc-drift | 15 | Docs describe behaviour the code no longer has |
| security | 11 | Control bypassable |
| test-coverage | 5 | Risky path unguarded |
| code-smell | 1 | Maintainability |

### Structural constraint on remediation

`agent_baton/core/engine/executor.py` is implicated in **53 of 103 findings**.
It is the single hottest file in the repository and the principal source of coupling. This constrains the
remediation topology: parallel fixes must be grouped so that no two concurrent agents own the same file, and
everything touching `executor.py` must be serialised into one workstream.

The criticals happen to be near-disjoint by primary file (18 distinct files for 21 findings), which is what
makes Tranche A safely parallelisable.

## Method

An eight-stage pipeline, each stage a fleet of model-matched agents:

| Stage | Fleet | Model | Purpose |
|---|---|---|---|
| 1 | 14 domain auditors + 1 critic | Opus | Evidence-based defect discovery |
| 2 | Orchestrator (human-in-loop) | — | Dedupe, independent verification, workstream architecture |
| 3 | 1 per workstream | Opus | Write the failing behavioural test (RED) |
| 4 | 1 per workstream | Sonnet | Fix the root cause (GREEN); forbidden from editing the test |
| 5 | 1 per workstream | Opus | Adversarial verification incl. test-tamper detection |
| 6 | 1 reviewer | Fable | Whole-diff review and correction |
| 7 | 1 per doc surface | Opus | CLAUDE.md, personas, README, docs/ |
| 8 | 1 builder | Fable | Public GitHub Pages site |

**Test-immutability is enforced on our own process.** The RED agent returns its test verbatim; the VERIFY agent
diffs the on-disk file against that record. An implementer that weakens a test to reach green is caught
mechanically rather than on trust — the same control F093 adds to the product.

## Tranche A — 21 criticals (in flight)

18 workstreams, file-disjoint, running RED→GREEN→VERIFY now.

| ID | Domain | Finding | Cat | Eff | Primary file |
|---|---|---|---|---|---|
| F073 | agents-references-templates | PreToolUse secret guard in templates/settings.json never blocks .env writes | security | M | `templates/settings.json` |
| F031 | api | Gate approve/reject never pass the HTTP caller's identity to the engine, breaking team-mode s... | security | M | `agent_baton/api/routes/pmo.py` |
| F053 | beads-memory | First bead write silently rewrites the user's tracked .gitignore and git-ignores baton.db | correctness | M | `agent_baton/core/engine/bd_client.py` |
| F093 | consort-gap-analysis | No test-immutability control: a specialist can delete or weaken tests and the GATE still passes | capability-gap | L | `agent_baton/core/engine/gates.py` |
| F098 | cross-cutting | Split observability sinks: daemon writes JSONL, CLI writes SQLite, and every learning consume... | silent-failure | L | `agent_baton/core/engine/executor.py` |
| F001 | engine-core | resume() re-persists pre-recovery state, undoing recover_dispatched_steps() | correctness | S | `agent_baton/core/engine/executor.py` |
| F002 | engine-core | record_gate_result() validates nothing — a gate pass for any phase_id advances the phase and ... | correctness | M | `agent_baton/core/engine/executor.py` |
| F007 | engine-planning | Investigative-archetype tasks are unplannable — every plan is hard-blocked by review_missing | correctness | M | `agent_baton/core/engine/planning/stages/decomposition.py` |
| F008 | engine-planning | ForesightEngine renumbers step ids without rewriting depends_on — dependency edges silently r... | silent-failure | M | `agent_baton/core/engine/foresight.py` |
| F060 | federate-specs-manager | enrich() raises NameError on every call, silently zeroing cost breakdown and killing the cali... | silent-failure | S | `agent_baton/core/federate/enrich.py` |
| F061 | federate-specs-manager | Team-mode self-approval control is bypassed by any caller that omits the identity header | security | M | `agent_baton/api/middleware/user_identity.py` |
| F015 | govern | Compliance hash chain is not tamper-evident: entry digest excludes prev_hash | security | M | `agent_baton/core/govern/compliance.py` |
| F066 | learn-improve-immune | Budget-tier auto-apply guardrail is bypassed by the planner's direct read of budget-recommend... | silent-failure | M | `agent_baton/core/improve/loop.py` |
| F067 | learn-improve-immune | Immune sweeper and triage call ClaudeCodeLauncher.launch() with the wrong signature and never... | silent-failure | M | `agent_baton/core/immune/sweeper.py` |
| F046 | orchestration-teams | fold_back() can never succeed: git refuses to fetch into a branch checked out in the worktree | correctness | M | `agent_baton/core/engine/worktree_manager.py` |
| F048 | orchestration-teams | Agent commits made inside a worktree are invisible to the launcher, so the engine deletes the... | silent-failure | M | `agent_baton/core/runtime/claude_launcher.py` |
| F049 | orchestration-teams | gc_stale's in-flight guard queries a column that does not exist, so both safety gates are ine... | silent-failure | M | `agent_baton/core/engine/worktree_manager.py` |
| F079 | pmo-ui | SpecsPanel silently discards every spec the backend actually returns | silent-failure | S | `pmo-ui/src/components/SpecsPanel.tsx` |
| F023 | storage-models | Plan steps are reloaded in lexicographic step_id order, corrupting step order and making ≥10-... | correctness | M | `agent_baton/core/storage/sqlite_backend.py` |
| F086 | tests-docs | CI runs only 3.4% of the test suite while docs claim the full suite is CI-gated | test-coverage | M | `.github/workflows/tests.yml` |
| F087 | tests-docs | Golden planner snapshot suite is red on HEAD; one case raises PlanQualityError through the pu... | correctness | M | `tests/planning/test_plan_quality_golden.py` |

## Tranche B — 47 high

Requires re-grouping: several share `executor.py`, `pmo.py`, and `install.py` and must be serialised.

| ID | Domain | Finding | Cat | Eff | Primary file |
|---|---|---|---|---|---|
| F074 | agents-references-templates | agents/CLAUDE.md and references/CLAUDE.md are packaged and installed as agent/reference defin... | correctness | S | `agent_baton/core/orchestration/registry.py` |
| F075 | agents-references-templates | baton install never installs templates/skills, packs, specs, or playbooks — the installed CLA... | capability-gap | M | `agent_baton/cli/commands/distribute/install.py` |
| F032 | api | Unauthenticated `X-Baton-User` header is trusted as the authorization identity, letting one t... | security | L | `agent_baton/api/middleware/user_identity.py` |
| F033 | api | `POST /pmo/specs/{id}/enrich` returns an unhandled 500 for any draft past `enriched` status | correctness | S | `agent_baton/api/routes/spec_queue.py` |
| F034 | api | Approval audit-log writes are swallowed by bare `except Exception: pass`, silently losing the... | silent-failure | S | `agent_baton/api/routes/pmo.py` |
| F054 | beads-memory | `baton beads cleanup` memory decay is a hard-coded no-op while its dry-run reports a non-zero... | capability-gap | M | `agent_baton/core/engine/bd_bead_store.py` |
| F055 | beads-memory | F11 bead conflict detection is dead: `contradicts` links never produce the `conflict:unresolv... | silent-failure | S | `agent_baton/core/engine/bd_bead_store.py` |
| F056 | beads-memory | Bead IDs collide across tasks and the colliding write silently overwrites another task's bead | correctness | M | `agent_baton/models/bead.py` |
| F038 | cli | Mandatory incident-handling command in CLAUDE.md uses a nonexistent --message flag | doc-drift | S | `CLAUDE.md` |
| F039 | cli | Bead CLI swallows every backend exception and reports a false "No baton.db found" with exit 0 | silent-failure | S | `agent_baton/cli/commands/bead_cmd.py` |
| F040 | cli | baton install never installs templates/skills/, producing a silently incomplete install | capability-gap | M | `agent_baton/cli/commands/distribute/install.py` |
| F041 | cli | baton install --verify exits 0 when verification finds issues | silent-failure | S | `agent_baton/cli/commands/distribute/install.py` |
| F094 | consort-gap-analysis | Approved plans and spec drafts are never content-hashed, so post-approval drift is undetectable | capability-gap | L | `agent_baton/core/engine/executor.py` |
| F095 | consort-gap-analysis | Gates execute against ambient state: every driver shells out with cwd=Path.cwd() and a fully ... | capability-gap | XL | `agent_baton/cli/commands/execution/execute.py` |
| F096 | consort-gap-analysis | PlanGate.fail_on is dead configuration — the planner emits 'coverage below threshold' but not... | silent-failure | M | `agent_baton/core/engine/planning/utils/gates.py` |
| F097 | consort-gap-analysis | files_changed is agent self-reported, not git-derived — the substrate any test-integrity or s... | correctness | M | `agent_baton/cli/commands/execution/execute.py` |
| F099 | cross-cutting | BATON_RUN_TOKEN_CEILING is documented as a hard kill but the engine never calls it, and its p... | capability-gap | M | `agent_baton/core/engine/executor.py` |
| F100 | cross-cutting | PMO live execution monitor reads an events path nothing ever writes, and filters on fields th... | silent-failure | M | `agent_baton/api/routes/pmo.py` |
| F101 | cross-cutting | All three shipped deployment artifacts are non-functional: the Docker image is built without ... | capability-gap | M | `Dockerfile` |
| F003 | engine-core | Investigative RETRY_PHASE loop-back never re-runs anything — it burns the retry budget and ad... | capability-gap | M | `agent_baton/core/engine/phase_manager.py` |
| F004 | engine-core | Policy-block APPROVAL is unresolvable: no CLI/API surface, in-memory-only unblock, and the re... | silent-failure | M | `agent_baton/core/engine/executor.py` |
| F009 | engine-planning | consolidate_team_step drops step ids without remapping dependents, producing an unconstructib... | correctness | M | `agent_baton/core/engine/planning/utils/phase_builder.py` |
| F010 | engine-planning | Structured-spec plans hard-block when only some phases name an agent | correctness | S | `agent_baton/core/engine/planning/utils/phase_builder.py` |
| F011 | engine-planning | _phase_key misparses structured-spec phase names, silently disabling every phase↔role guard | silent-failure | S | `agent_baton/core/engine/planning/stages/validation.py` |
| F062 | federate-specs-manager | GitHub importer interpolates an unvalidated ref into the API URL, redirecting the server's GI... | security | S | `agent_baton/core/federate/importers.py` |
| F064 | federate-specs-manager | tests/federate/ covers only the rubric — importers, SpecDraftStore, and the enrich cost path ... | test-coverage | M | `tests/federate/test_rubric.py` |
| F016 | govern | Evidence-bundle segment verification fails on any interleaved compliance log | correctness | M | `agent_baton/core/govern/evidence_bundle.py` |
| F017 | govern | A corrupt .claude/policies/<preset>.json silently disables all policy enforcement | silent-failure | S | `agent_baton/core/govern/policy.py` |
| F018 | govern | LLM plan review can de-escalate risk after governance stages, dropping the regulated preset w... | security | S | `agent_baton/core/engine/planning/planner.py` |
| F019 | govern | Team-mode segregation of duties relies on spoofable $USER and is bypassed entirely via the PM... | security | M | `agent_baton/core/engine/executor.py` |
| F020 | govern | Daemon auto-decisions are recorded as decision_source="human", falsifying the approval and ga... | silent-failure | S | `agent_baton/core/engine/executor.py` |
| F068 | learn-improve-immune | Immune daemon's sweep queue is never seeded in production, so next_target() always returns None | capability-gap | M | `agent_baton/core/immune/scheduler.py` |
| F069 | learn-improve-immune | SweepScheduler.next_target() ignores the defer window, so the 30-day/7-day re-sweep policy ne... | correctness | S | `agent_baton/core/immune/scheduler.py` |
| F070 | learn-improve-immune | Executor._persist_event's JSONL write is unguarded despite a docstring promising best-effort,... | silent-failure | S | `agent_baton/core/engine/executor.py` |
| F047 | orchestration-teams | Fold-back writes the parent branch ref with update-ref while it is checked out, silently stag... | correctness | M | `agent_baton/core/engine/worktree_manager.py` |
| F050 | orchestration-teams | ExecutionAction.worktree_path is never populated, so the documented orchestrator contract sil... | capability-gap | M | `agent_baton/core/engine/executor.py` |
| F080 | pmo-ui | No React error boundary, and all six primary panels mount on every page load | correctness | M | `pmo-ui/src/main.tsx` |
| F081 | pmo-ui | Playwright e2e suite never navigates to the app — every goto('/') lands on the origin root, n... | test-coverage | M | `pmo-ui/e2e/pages/BasePage.ts` |
| F082 | pmo-ui | Two of three spec lifecycle buttons call endpoints that do not exist; the third sends no body... | capability-gap | M | `pmo-ui/src/api/client.ts` |
| F083 | pmo-ui | The built PMO UI is never packaged, so `/pmo/` silently does not exist in an installed baton | capability-gap | M | `agent_baton/api/server.py` |
| F024 | storage-models | Nine MachinePlan fields — including compliance_fail_closed and the goal-execution config — ar... | silent-failure | M | `agent_baton/core/storage/sqlite_backend.py` |
| F025 | storage-models | SyncEngine watermark is rowid-based, so updated rows never re-sync and central.db reports sta... | silent-failure | M | `agent_baton/core/storage/sync.py` |
| F026 | storage-models | CENTRAL_SCHEMA_DDL is missing the v15 team_members columns, so team_members sync fails on eve... | capability-gap | S | `agent_baton/core/storage/schema.py` |
| F027 | storage-models | Pre-migration backup only fires for one hardcoded version, so v43/v44 DROP TABLE migrations d... | correctness | S | `agent_baton/core/storage/connection.py` |
| F088 | tests-docs | Deferred-feature acceptance tests assert on source-file substrings and probe file paths that ... | silent-failure | S | `tests/govern/test_phase0_deferred.py` |
| F089 | tests-docs | Two env-var tables both claim to be the full mirrored list, disagree with each other, and omi... | doc-drift | M | `docs/cli-reference.md` |
| F090 | tests-docs | docs/cli-reference.md claims to document every command but omits 12 shipped commands entirely | doc-drift | M | `docs/cli-reference.md` |

## Tranche C — 31 medium

| ID | Domain | Finding | Cat | Eff | Primary file |
|---|---|---|---|---|---|
| F076 | agents-references-templates | Distributable orchestration contract documents 6 of 9 ActionTypes; INTERACT and FEEDBACK are ... | doc-drift | S | `templates/CLAUDE.md` |
| F077 | agents-references-templates | `tools` frontmatter is declared required but never enforced; orchestrator and task-runner shi... | security | S | `agents/task-runner.md` |
| F035 | api | PMO UI's "Mark Reviewed" and "Archive" spec buttons call REST routes that do not exist | capability-gap | M | `agent_baton/api/routes/specs.py` |
| F036 | api | docs/api-reference.md omits 21 implemented routes and the unauthenticated `/metrics` endpoint... | doc-drift | M | `docs/api-reference.md` |
| F037 | api | `POST /pmo/execute/{card_id}` reports 202 "launched" while discarding all worker stdout/stder... | silent-failure | M | `agent_baton/api/routes/pmo.py` |
| F057 | beads-memory | `BdBeadStore.close(summary=...)` discards the summary and bd overwrites the bead's recorded c... | silent-failure | S | `agent_baton/core/engine/bd_bead_store.py` |
| F058 | beads-memory | `BATON_BD_BACKEND` / `BATON_BD_ENABLED` are documented as live but their implementations are ... | doc-drift | S | `agent_baton/core/engine/bead_backend.py` |
| F059 | beads-memory | Cross-project bead projection drops the exact columns `v_cross_project_discoveries` exposes | correctness | S | `agent_baton/core/storage/central.py` |
| F042 | cli | discover_commands() keys modules by basename, silently dropping baton improve-conflicts from ... | correctness | S | `agent_baton/cli/main.py` |
| F043 | cli | policy-check misses relative Bash paths, so `echo x > .env` bypasses the block that `Write .e... | security | M | `agent_baton/cli/commands/govern/policy_check.py` |
| F044 | cli | docs/cli-reference.md claims to document every command but omits 31 of 90, including beads, s... | doc-drift | L | `docs/cli-reference.md` |
| F045 | cli | Error paths that print "error:" and still exit 0 (pagerduty, uninstall) | silent-failure | S | `agent_baton/cli/commands/observe/pagerduty_cmd.py` |
| F102 | cross-cutting | Four different budget_tier vocabularies across code and docs, reconciled by a silent .get(def... | correctness | S | `agent_baton/core/engine/executor.py` |
| F103 | cross-cutting | Outbound webhooks only fire for events published inside the API server process, so the docume... | capability-gap | M | `agent_baton/api/server.py` |
| F005 | engine-core | _print_action has no WAIT or CHECKPOINT branch — emits lowercase keywords contrary to the doc... | doc-drift | S | `agent_baton/cli/commands/execution/execute.py` |
| F006 | engine-core | ExecutionDriver.record_gate_result docstring contradicts the implementation on gate failure (... | doc-drift | S | `agent_baton/core/engine/protocols.py` |
| F012 | engine-planning | Direct/investigative archetype builders take resolved_agents[0] raw, bypassing phase-role rou... | correctness | S | `agent_baton/core/engine/planning/stages/decomposition.py` |
| F013 | engine-planning | PlanReviewer's recommendation path is dead code — two documented ValidationStage defect detec... | silent-failure | M | `agent_baton/core/engine/plan_reviewer.py` |
| F014 | engine-planning | Golden plan-snapshot suite is non-hermetic and its investigative case is neutered by an expli... | test-coverage | M | `tests/planning/test_plan_quality_golden.py` |
| F063 | federate-specs-manager | Eleven manager-mode config keys are scaffolded and validated but never read by any engine code | capability-gap | L | `agent_baton/core/config/manager.py` |
| F021 | govern | Evidence bundles ship un-redacted agent output; redaction toggle and IPv4 behaviour are undoc... | security | M | `agent_baton/core/govern/evidence_bundle.py` |
| F022 | govern | DataClassifier is documented as an LLM classifier but is pure keyword matching, and its failu... | doc-drift | S | `agent_baton/core/govern/CLAUDE.md` |
| F071 | learn-improve-immune | EventBus.replay() is memory-only and never rehydrated, so 'rebuild state after a crash' is im... | capability-gap | M | `agent_baton/core/events/bus.py` |
| F072 | learn-improve-immune | README documents an experimental 'Predictive watcher' feature flag in core/intel/ that was de... | doc-drift | S | `README.md` |
| F051 | orchestration-teams | InteractiveDecisionManager raises UnboundLocalError on EOF instead of rejecting the gate | correctness | S | `agent_baton/core/orchestration/runner.py` |
| F052 | orchestration-teams | TeamMailbox.append performs no locking and derives event_id from a racy read-then-write, cont... | code-smell | S | `agent_baton/core/engine/mailbox.py` |
| F084 | pmo-ui | checkEndpoint reports a missing endpoint as live, so Back-of-House shows LIVE badges for unco... | silent-failure | S | `pmo-ui/src/api/client.ts` |
| F028 | storage-models | Goal-execution counters (amend_cycles_used, goal_status, goal_checks, turn_count) are never p... | silent-failure | M | `agent_baton/models/execution.py` |
| F029 | storage-models | Sync drops rows on IntegrityError but still advances the watermark, losing them permanently w... | silent-failure | S | `agent_baton/core/storage/sync.py` |
| F030 | storage-models | Schema/migration drift gate covers only the executions table, leaving three live drifts undet... | test-coverage | S | `tests/models/test_execution_sqlite_roundtrip.py` |
| F091 | tests-docs | The only test validating the 30 distributable agent definitions silently skips based on the w... | silent-failure | S | `tests/test_validator.py` |

## Tranche D — 4 low

| ID | Domain | Finding | Cat | Eff | Primary file |
|---|---|---|---|---|---|
| F078 | agents-references-templates | immune-untested-edges advertises test-stub generation it has no tools to perform | doc-drift | S | `agents/immune-untested-edges.md` |
| F065 | federate-specs-manager | Documented spec-queue auth contract and cost_confidence value do not match the implementation | doc-drift | S | `docs/api-reference.md` |
| F085 | pmo-ui | pmo-ui/CLAUDE.md documents a non-existent npm script and a non-existent type generator | doc-drift | S | `pmo-ui/CLAUDE.md` |
| F092 | tests-docs | Stale test docstrings and README/readiness counts describe a codebase that no longer matches | doc-drift | S | `tests/test_bead_tiers234.py` |

## Independently verified before execution

Four claims were re-checked by the orchestrator directly rather than taken on the auditors' word:

1. **F015 (compliance chain) — CONFIRMED, and sharpened.** `verify_chain` *does* check linkage
   (`compliance.py:407`), so the initial reading "deletion is undetected" looked wrong. But `_entry_hash`
   (line 173) excludes `prev_hash` from the digest, so entry hashes never commit to their predecessor — an
   attacker can delete, reorder, or splice entries and simply rewrite the uncovered `prev_hash` fields, and
   every check still passes. The same file already implements this correctly at `_hash_entry` (line 762).
2. **F060 (`enrich()` NameError) — CONFIRMED.** `step` is never bound in `enrich()`; line 78 references
   `step.agent_name` inside a comprehension bound to `(_, tokens), cost_usd`. Fires on every call.
3. **F086 (CI coverage) — CONFIRMED.** `.github/workflows/tests.yml` runs an explicit hand-picked file list,
   not the suite.
4. **Test-suite hermeticity — CONFIRMED independently, before the audit reported it.** `BdClient` runs `bd`
   with `cwd=repo_root` (`bd_client.py:92,307`); `bd` walks *upward* to discover `.beads/`, escaping per-test
   tmpdirs into the shared `/tmp/pytest-of-root/.beads`. `tests/test_bd_backend.py` fails 6/25 in the full
   suite but passes 25/25 after `rm -rf /tmp/pytest-of-root/.beads`. ~107 of the ~177 baseline failures are
   this one defect.

## Baseline

Full suite on HEAD before any change: **9,828 collected · 9,614 passed · 100 failed · 77 errors · 34 skipped**
(36m43s). Roughly 107 of the failures are the hermeticity artifact above; a clean-`/tmp` re-run is being
measured to establish the true regression baseline for Stage 5.
