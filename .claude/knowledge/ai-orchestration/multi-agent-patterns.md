---
name: multi-agent-patterns
description: Taxonomy of multi-agent coordination patterns (supervisor, router, hierarchical, fan-out, pipeline, blackboard) with trade-offs and anti-patterns
tags: [multi-agent, coordination, patterns, supervisor, anti-patterns, orchestration]
priority: normal
---

# Multi-Agent Coordination Patterns

## Pattern Taxonomy

| Pattern | Structure | When to Use | Trade-offs |
|---------|-----------|-------------|------------|
| **Supervisor** | Central coordinator dispatches to workers | Most orchestrated tasks; clear decomposition | Coordinator is bottleneck; single point of context loss |
| **Router** | Classifier sends to single specialist | Task fits one domain cleanly | No multi-agent synergy; limited to one expert |
| **Hierarchical** | Tree of supervisors with sub-teams | Very large tasks (10+ agents) | Deep context loss; coordination overhead compounds |
| **Parallel Fan-out** | Independent agents work simultaneously | Non-dependent steps | No shared state; merge conflicts possible |
| **Sequential Pipeline** | Output of A feeds input of B | Dependent chain (design → implement → test) | Slow; no parallelism; early failures block all |
| **Blackboard** | Shared state file all agents read/write | Collaborative refinement | Requires locking; state can grow unbounded |

## Agent Baton's Pattern: Enhanced Supervisor

Agent Baton uses the **Supervisor** pattern with these enhancements:
- **Inline skills** reduce the coordinator's delegation overhead
- **Phased delivery** combines sequential (between phases) and parallel (within phases)
- **Shared context files** provide a limited blackboard (context.md, mission-log.md)
- **QA gates** act as synchronization barriers between phases

## When to Add Hierarchy

Split into sub-orchestrators when:
- Task exceeds 8 agents (context exhaustion for single orchestrator)
- Task spans 2+ unrelated domains (e.g., data pipeline + frontend + compliance)
- One sub-task is complex enough to need its own phased delivery

## Anti-Patterns

| Anti-Pattern | Symptom | Fix |
|-------------|---------|-----|
| **Circular delegation** | Agent A delegates to B which delegates back to A | Strict DAG enforcement in plan |
| **Agent sprawl** | 15+ agents per task | Apply decision framework; merge or demote |
| **Context starvation** | Agent produces poor output because orchestrator's summary was too thin | Write richer shared context; include file paths not summaries |
| **Too many cooks** | Multiple agents modify same files | Non-overlapping write scopes in guardrails |
| **Summarization cascade** | 3+ levels of summarization destroy signal | Maximum 2 handoff hops; prefer direct file references |
