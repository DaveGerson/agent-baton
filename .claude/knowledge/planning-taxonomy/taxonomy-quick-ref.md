---
name: taxonomy-quick-ref
description: Quick-reference for Agent Baton plan structure, step intents, phase archetypes, and gate semantics — derived from references/planning-taxonomy.md
tags: [planning, taxonomy, decomposition, intents, archetypes, foresight, gates, budget]
priority: high
---

# Planning Taxonomy — Quick Reference

Authoritative semantic model for every element of an Agent Baton execution plan. Use when classifying a step/phase/gate or when reviewing a plan for consistency.

## Element Hierarchy

```
MachinePlan
  ├─ PlanPhase (1..N)              # sequential
  │   ├─ PlanStep (1..N)           # parallel within phase
  │   │   ├─ TeamMember (0..N)     # non-empty = team step
  │   │   └─ KnowledgeAttachment (0..N)
  │   └─ PlanGate (0..1)           # QA checkpoint
  ├─ ForesightInsight (0..N)       # proactive gap analysis
  └─ PlanAmendment (0..N)          # runtime modifications
```

## Step Intents (the *why* of a step)

**Core work:** `design`, `implement`, `migrate`, `analyze`, `experiment`
**Quality:** `test`, `review`, `audit`, `validate`
**Coordination:** `synthesize`, `handoff`, `interact`, `decide`
**Recovery:** `remediate`, `rollback`, `retry`

Use `cymbal investigate StepIntent` for the canonical enum.

## Phase Archetypes (the *shape* of a phase)

| Archetype | Typical steps | Gate after |
|---|---|---|
| **Discover** | `analyze`, `interact` | none |
| **Design** | `design`, `decide` | approval |
| **Prepare** | `migrate` (rollback infra) | build |
| **Implement** | `implement`, `synthesize` | build |
| **Test** | `test`, `validate` | test |
| **Review** | `review`, `audit` | none |
| **Deploy** | `migrate` (forward) | smoke |
| **Remediate** | `remediate`, `rollback` | targeted |

A phase's archetype determines default gate type, default agent roles, and default risk tier behavior.

## Reviewer Filter (since bd-7444)

Per `agent_baton/core/orchestration/router.py`:

```python
REVIEWER_AGENTS = {
    "auditor", "code-reviewer", "security-reviewer",
    "plan-reviewer", "spec-document-reviewer",
}
```

Reviewer-class agents must NEVER appear as team-step members of an `implement`-archetype phase. They belong in `Review` or `Test` archetype phases only.

## Concern-Splitting Heuristic (since bd-7444)

When an `implement` phase's task description contains ≥3 distinct concerns (matched by `[A-Za-z]\d+\.\d+ | \(\d+\) | \d+[.\)](?!\d)`), split into N parallel steps, one per concern. Each split step gets the most-fitting agent via `_CROSS_CONCERN_SIGNALS` keyword scoring.

## Risk Tiers

`LOW` < `MEDIUM` < `HIGH` < `CRITICAL`

Risk drives:
- Number and type of gates (HIGH/CRITICAL get `audit` gates)
- Approval requirements (HIGH/CRITICAL `design` phases require approval)
- Auditor invocation (MEDIUM+ gets the `auditor` agent in review phase)
- VETO enforcement (HIGH/CRITICAL halt on `AuditorVerdict.VETO` unless `--force` + `--justification`)

## Budget Tiers

`light` < `medium` < `heavy`

Set explicitly via `--complexity` or auto-classified by the Haiku classifier. Drives default model selection (haiku/sonnet/opus) and per-step token budget.

## Gate Types

| Type | When | Command source |
|---|---|---|
| `build` | After every implementation phase | Auto-detected per stack (e.g., `python3 -c "import <pkg>"`) |
| `test` | After test phase | `pytest tests/...` for Python; `npm test` for Node |
| `lint` | Optional, after implement | `ruff` / `eslint` |
| `spec` | After spec-driven implement | `baton spec-check` |
| `ci` | Integration with external CI | GitHub Actions polling |
| `audit` | After HIGH/CRITICAL implement | Compliance auditor agent |
| `review` | Manual checkpoint | Approval CLI |

Failed gates trigger remediation: an injected `remediate` step on a feature branch.

## Foresight Insights

Pre-planning hooks that detect predictable gaps and inject preparatory steps. Examples:
- **Migration safety:** any phase touching schema gets a `Prepare` phase ahead of it (rollback scripts + backup)
- **High-fanout impact:** changes to symbols with many callers get an `analyze` step ahead

Surfaced in `MachinePlan.foresight_insights` and shown in `baton plan --explain`.

## Knowledge Attachments

Each step can declare:
- Explicit `--knowledge PATH` or `--knowledge-pack PACK`
- Agent frontmatter `default_packs`
- Tag-matched packs from registry
- TF-IDF auto-attached docs (capped by `_DOC_TOKEN_CAP_DEFAULT=8000`)

Resolver records every hit via `KnowledgeTelemetryStore.record_used()`; retrospective records outcome via `record_outcome()`. Effectiveness available in `v_knowledge_effectiveness`.

## See also

- Long-form: `references/planning-taxonomy.md` (248 lines, full spec)
- Architecture: `docs/architecture/high-level-design.md`
- Agent roster: `docs/agent-roster.md`
