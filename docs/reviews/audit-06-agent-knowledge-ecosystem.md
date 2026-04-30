# Audit: Agent & Knowledge Ecosystem

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agents/` (33 files), `agent_baton/_bundled_agents/` (22 files), `references/` (18 files), `agent_baton/core/knowledge/` (6 files), `agent_baton/core/orchestration/registry.py`, `agent_baton/core/orchestration/knowledge_registry.py`

## Executive Summary

The agent and knowledge ecosystem is architecturally well-designed with clear separation of concerns across agent tiers, thoughtful knowledge lifecycle management, and a sensible layered override system (bundled < global < project). The biggest risk is the systematic duplication between `agents/` (distributable) and `_bundled_agents/` (packaged) -- these are maintained as independent copies with no automation to keep them synchronized, creating a silent divergence vector. The biggest strength is the consistency and quality of the agent definition format.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Agent definitions are consistently structured; knowledge system code is clean and well-typed |
| 2 | Acceleration & Maintainability | B | New agents are easy to create; knowledge lifecycle is well-documented; dual agent directories create maintenance burden |
| 3 | Token/Quality Tradeoffs | A | Agent prompts are right-sized; model tiers are appropriate; knowledge system has lazy loading and budget-aware delivery |
| 4 | Implementation Completeness | C | A/B testing is not wired into the engine; effectiveness module has no tests; references for immune/self-heal/swarm agents are missing |
| 5 | Silent Failure Risk | C | Bundled/distributable drift is invisible; knowledge A/B has no integration path; agent model mismatch between roster docs and definitions |
| 6 | Code Smells | B | Duplication between agents/ and _bundled_agents/ is the main smell; otherwise patterns are consistent |
| 7 | User Discoverability | B | Agent roster doc is excellent; `baton agents` CLI exists; knowledge system is less discoverable |
| 8 | Extensibility | A | Three-tier override system (bundled/global/project) is elegant; knowledge packs are fully extensible |

## Critical Issues (Fix Now)

- **Missing bundled agents for internal subsystems.** The 11 agents used by self-heal, immune, swarm, speculation, and team subsystems are absent from `_bundled_agents/`. A pip-installed user who triggers self-heal will get a missing-agent error. Either bundle these agents or make the subsystems gracefully degrade.

- **Agent roster model mismatches.** Four agents have incorrect model declarations in `docs/agent-roster.md`: team-lead (says opus, is sonnet), task-runner (says sonnet, is haiku), security-reviewer (says sonnet, is opus), subject-matter-expert (says sonnet, is opus).

## Important Issues (Fix Soon)

- **No sync mechanism between agents/ and _bundled_agents/.** Add a CI check or build step.
- **Effectiveness module has no test coverage** (444 lines of scoring logic).
- **ADR harvester and review harvester have no test coverage** (337 and 389 lines respectively).
- **A/B testing is built but unwired.** Either wire it in or move behind `BATON_EXPERIMENTAL`.

## Silent Failure Inventory

| Location | Risk Level | Description |
|----------|-----------|-------------|
| `_bundled_agents/` vs `agents/` drift | HIGH | No sync check; bug fixes silently missing for bundled users |
| Missing bundled subsystem agents | HIGH | Dispatch fails for pip-only users |
| `effectiveness.py:218-220` | MEDIUM | Schema mismatch silently returns no data |
| `ab_testing.py` unwired integration | MEDIUM | Feature appears complete but never influences behavior |
| `roster.md` model mismatches | MEDIUM | Four agents have wrong model in docs |
