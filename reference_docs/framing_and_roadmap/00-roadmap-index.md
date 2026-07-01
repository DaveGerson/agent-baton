# Agent Baton Short-Term Capability Roadmaps

**Purpose:** Four phased roadmaps that Baton can use to implement quick and short-term wins for Agent Baton without structural refactoring. These plans focus on end-user capabilities that improve software outcomes for developers using the tool: better plans, safer coordination, clearer team execution, reusable agent/knowledge assets, and more predictable day-to-day operation.

**Operating constraint:** Do not split major modules, redesign storage, replace the planning pipeline, or perform broad API/UI rewrites. Each task should be implementable as a targeted feature, validation improvement, test, CLI/API addition, or documentation improvement.

---

## How to use these roadmaps with Baton

Each capability file has four phases. To run a phase, copy the relevant **Baton run prompt** into your normal Agent Baton workflow. Recommended execution pattern:

```text
Use Agent Baton to implement Phase <N> from <roadmap-file>.md.
Follow the constraints, acceptance criteria, and validation commands exactly.
Do not perform structural refactoring outside the file/path scope listed in the roadmap.
```

Run the capabilities in the order below when possible:

1. Plan creation and coordination
2. Knowledge pack management
3. Talent Builder and subagent management
4. Agent team spin-up
5. General developer experience, packaging, and layout polish

This order wires dormant capabilities first, then improves validation, then improves generated assets and teams, then makes the result easier to install and operate.

---

## Four-phase delivery model

| Phase | Theme | End-user outcome | Definition of done |
|---|---|---|---|
| **Phase 1** | Make hidden capability visible and active | Developers can see what Baton is doing, why it chose a plan/team/knowledge set, and whether core capability wiring is active. | Capability is enabled by default or reports clearly when unavailable. CLI/API outputs include actionable diagnostics. Focused tests cover the new behavior. |
| **Phase 2** | Make outputs trustworthy | Developers get fewer malformed plans, bad dispatches, missing context, and silent degradations. | Validation failures are actionable. Important quality gates are enforced or explicitly opt-out. Golden/smoke tests cover common workflows. |
| **Phase 3** | Make workflows reusable | Developers can create, review, and reuse agents, teams, and knowledge packs with fewer manual checks. | Doctor/validate commands catch missing references, unsafe permissions, stale knowledge, and weak team contracts. |
| **Phase 4** | Make it shippable day-to-day | Developers can install, inspect, and operate Baton consistently across projects. | Docs, CLI help, packaging, UI visibility, and release checks support the improved workflows. |

---

## Capability matrix

| Capability | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| **Plan creation and coordination** | Wire planner defaults, plan diagnostics, explainability summary | Plan hard-gate defaults, golden plan tests, clearer defects | Context/handoff quality, goal/round-out visibility | PMO plan preview, docs, planner smoke pack |
| **Agent team spin-up** | Team readiness audit, backend strictness, team report | File-scope contracts, conflict severity, synthesis minimum viable path | Team validation and mailbox visibility | PMO team status and team playbook docs |
| **Talent Builder / subagent management** | Canonical naming, generated-agent contract, validation checklist | Agent doctor, knowledge reference verification, permission warnings | Draft/review/promote workflow using metadata | Agent catalog docs/UI and starter templates |
| **Knowledge pack management** | Wire KnowledgeRegistry by default, manifest normalization | `baton knowledge doctor/search`, pack validation, body-index option | Gap-to-pack suggestions, freshness/usage signals | Knowledge dashboard/docs and example packs |
| **General layout / developer UX** | Doctor command, terminology cleanup, package-resource audit | CI smoke matrix, import/package tests, CLI help snapshots | PMO auth/client polish, install verification | Release checklist, documentation nav, examples |

---

## Global non-goals

These roadmaps deliberately exclude structural refactoring. Do **not** use these plans to:

- split `ExecutionEngine` into services,
- split `api/routes/pmo.py`,
- replace SQLite/file persistence,
- redesign the PMO UI,
- change the public `MachinePlan` schema without migration handling,
- replace the planning pipeline,
- introduce a new agent runtime.

Those are valid medium-term workstreams, but they are outside this quick/short-term roadmap.

---

## Global quality bar

Every phase should include:

- at least one focused unit test or integration smoke test,
- CLI/API output that helps a developer understand what changed,
- no broad module moves,
- no silent fallback where the user needs a clear warning,
- updated docs or help text when behavior changes.
