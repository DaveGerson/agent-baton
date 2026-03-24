---
name: failure-modes
description: Catalog of multi-agent orchestration failure modes (drift, scope creep, hallucination cascade, context exhaustion, etc.) with mitigations and remaining risks
tags: [failure-modes, risk, agent-drift, hallucination, context-window, orchestration]
priority: high
---

# Multi-Agent Failure Modes

A catalog of ways multi-agent orchestration systems fail and how Agent Baton
addresses (or should address) each.

## Failure Taxonomy

### 1. Agent Drift
**What:** Agent gradually moves outside its assigned scope, making changes
to unrelated files or pursuing tangential goals.
**Cause:** Vague delegation prompts; agent "helpfully" does more than asked.
**Agent Baton mitigation:** Per-agent path boundaries in guardrails; PreToolUse
hooks block writes to unauthorized paths.
**Remaining risk:** Agents can still drift in their reasoning even within scope.

### 2. Scope Creep
**What:** The orchestrator keeps adding work packages mid-execution, bloating
the task beyond the original plan.
**Cause:** Orchestrator discovers new needs during execution; doesn't stop to replan.
**Mitigation:** Plan-on-disk forces commitment; QA gates are natural checkpoints
to assess scope. Failure handling protocol limits retries to 1.
**Remaining risk:** No hard limit on plan modification.

### 3. Circular Delegation
**What:** Agent A delegates to B which delegates back to A (or to C which
delegates to A).
**Cause:** Agents spawning sub-agents without DAG enforcement.
**Mitigation:** Agent Baton's orchestrator is the only entity that delegates.
Specialist agents don't spawn other agents (no Agent tool in their tool set).
**Remaining risk:** Orchestrator could re-delegate same work if context is compacted.

### 4. Context Starvation
**What:** Agent produces poor output because it wasn't given enough context
about the codebase, conventions, or prior agent work.
**Cause:** Orchestrator writes thin shared context; handoff briefs omit critical details.
**Mitigation:** Shared context template (comms-protocols.md); codebase profile cache.
**Remaining risk:** Orchestrator quality determines context quality — garbage in, garbage out.

### 5. Summarization Loss Cascade
**What:** Information degrades across multiple handoffs. By the third agent,
the original architect's design intent is lost.
**Cause:** Each handoff summarizes, dropping detail. 3+ hops = significant signal loss.
**Mitigation:** Maximum 2 handoff hops by design; file path references instead
of content summaries; shared context doc as ground truth.
**Remaining risk:** Complex tasks with 5+ phases still lose signal.

### 6. The "Too Many Cooks" Problem
**What:** Multiple agents modify related code, creating integration conflicts
that no single agent can resolve.
**Cause:** Overlapping write scopes; insufficient coordination.
**Mitigation:** Non-overlapping file boundaries; commit-per-agent git strategy;
integration check in Phase 6.
**Remaining risk:** Semantic conflicts (incompatible API designs) aren't caught by file scope.

### 7. Hallucination Cascade
**What:** One agent hallucinates a fact, the next agent builds on it,
the third agent codifies it as truth.
**Cause:** Agent A invents a function name; Agent B imports it; Agent C tests it.
**Mitigation:** QA gates (build check catches missing imports); code review.
**Remaining risk:** Semantic hallucinations (wrong business logic) pass build checks.

### 8. Context Window Exhaustion
**What:** Orchestrator's context fills up managing handoffs, losing track
of the plan and agent states.
**Cause:** Too many agents; verbose outputs held in context instead of summarized.
**Mitigation:** Write everything to disk (plan, context, mission log);
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80; session recovery protocol.
**Remaining risk:** Compaction still loses orchestrator's internal reasoning state.

### 9. Rate Limit Collapse
**What:** Parallel agent invocations hit API rate limits, causing cascading
failures and wasted retries.
**Cause:** Full-tier tasks (6-8 agents) with Opus model selection.
**Mitigation:** Budget tier guidance (cost-budget.md); default to Sonnet;
sequential fallback when parallel fails.
**Remaining risk:** External rate limits not under agent control.

### 10. Plan Rigidity
**What:** Orchestrator follows the original plan even after discovering it
won't work, because replanning feels expensive.
**Cause:** Sunk cost fallacy in planning; no cheap replan mechanism.
**Mitigation:** Failure handling protocol says "replan rather than patching."
**Remaining risk:** The orchestrator's bias is to complete, not to stop and rethink.
