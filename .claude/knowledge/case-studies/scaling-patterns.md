---
name: scaling-patterns
description: How multi-agent system concerns change at scale — roster growth, cross-project transfer, multi-user governance, and ADRs for scaling decisions
tags: [scaling, architecture, governance, cross-project, roster, adr]
priority: normal
---

# Scaling Patterns for Multi-Agent Systems

## Scaling Dimensions

| Dimension | Small (current) | Medium | Large |
|-----------|----------------|--------|-------|
| **Agents per task** | 3-5 | 6-10 | 10+ (use hierarchy) |
| **Agent roster** | ~20 agents | 25-40 agents | 40+ (prune regularly) |
| **Projects** | 1 | 2-5 | 5+ (cross-project transfer) |
| **Users** | 1 | 2-5 (shared repo) | Team (governance needed) |
| **Knowledge packs** | 0-5 | 5-20 | 20+ (index needed) |

## What Changes at Scale

### 3 → 15 Agents (Roster Growth)

**Problem:** More agents = more routing decisions, harder to pick the right one.
**Solution:**
- Agent routing table (already built) with clear trigger descriptions
- Regular roster health reviews (decision-framework.md)
- Flavors only when the framework conventions differ enough to matter
- Archive unused agents (don't delete — move to .archive/)

**Problem:** Agent prompts diverge in quality and conventions.
**Solution:**
- Prompt engineer agent reviews all prompts periodically
- Validator ensures format compliance
- Knowledge packs provide shared context (agents don't re-invent domain knowledge)

### 1 → Many Projects (Cross-Project)

**Problem:** Good agents/knowledge from Project A aren't available in Project B.
**Solution:**
- Global agents (~/.claude/agents/) for cross-project roles
- Project agents (.claude/agents/) for project-specific overrides
- Codebase profile cache (research-procedures.md) avoids re-researching

**Problem:** Agent learned something in Project A (via retrospective) but Project B doesn't benefit.
**Solution:**
- Cross-project knowledge transfer (roadmap 2.2)
- Global knowledge packs for shared domain knowledge
- Performance scores travel with agents, not projects

### 1 → Team Users (Multi-User)

**Problem:** Alice improves an agent, Bob doesn't get the update.
**Solution:**
- Shared team-agents git repository (roadmap 2.3)
- Agent VCS tracks who changed what
- Merge protocol for agent definition changes

**Problem:** Different users have different risk tolerances.
**Solution:**
- Per-user settings override global guardrail presets
- Auditor operates independently of user preferences
- Compliance reports provide team-level audit trail

## Architecture Decision Records for Scaling

### ADR: When to split into sub-orchestrators
**Context:** A single orchestrator can manage 5-8 agents before context exhaustion.
**Decision:** For tasks requiring 8+ agents, split into 2+ orchestrator sessions
with a handoff document between them.
**Consequence:** More complex workflow, but each session stays within context budget.

### ADR: When to promote local knowledge to global
**Context:** A knowledge pack created for one project may be useful everywhere.
**Decision:** If 2+ projects would benefit, promote from .claude/knowledge/ to ~/.claude/knowledge/.
**Consequence:** Global knowledge requires more careful maintenance (breaking changes affect all projects).

### ADR: When to create a new agent vs. a knowledge pack
**Context:** New domain capability needed.
**Decision:** Apply the five-test framework. Default to knowledge pack (Layer 2);
only create an agent if it passes Tests 1-2 (substantial independent work + independence needed).
**Consequence:** Keeps roster lean; knowledge packs are cheaper and more composable.
