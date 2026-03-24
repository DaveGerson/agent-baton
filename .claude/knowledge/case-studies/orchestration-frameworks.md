---
name: orchestration-frameworks
description: Comparative analysis of LangGraph, CrewAI, AutoGen, Claude Agent SDK, and OpenAI Swarm — strengths, weaknesses, and lessons for agent-baton design
tags: [frameworks, langgraph, crewai, autogen, comparison, architecture]
priority: normal
---

# Orchestration Frameworks — Comparative Analysis

## Framework Comparison

| Framework | Pattern | Strengths | Weaknesses | Relevance to Agent Baton |
|-----------|---------|-----------|------------|--------------------------|
| **LangGraph** | Graph-based state machine | Explicit control flow; cycles allowed; checkpointing | Requires defining full graph upfront; Python-heavy | Agent Baton uses phases (DAG) not cycles; simpler but less flexible |
| **CrewAI** | Role-based crew with manager | Natural "team" metaphor; good for business users | Agent-agent communication is implicit; hard to debug | Agent Baton's explicit delegation + handoff briefs is more traceable |
| **AutoGen** | Conversational agent groups | Flexible agent-to-agent dialogue; human-in-loop | Hard to control; can spiral; token-expensive | Agent Baton avoids agent-agent chat by design (orchestrator mediates) |
| **Claude Agent SDK** | Tool-based autonomous agent | Native tool use; sandboxed execution; simple | Single agent; no multi-agent coordination built in | Agent Baton builds multi-agent on top of this foundation |
| **OpenAI Swarm** | Lightweight handoff between agents | Minimal overhead; function-calling based | No persistence; no planning; no guardrails | Agent Baton adds the planning/guardrail layer Swarm lacks |

## Key Lessons

### From LangGraph
- **State machines work** — explicit phases with defined transitions prevent agent drift
- **Checkpointing is essential** — Agent Baton's mission log + plan-on-disk serves this role
- **Cycles are rarely needed** — most real tasks are DAGs, not cycles

### From CrewAI
- **Role clarity matters** — agents with clear, non-overlapping responsibilities produce better output
- **The "manager" pattern** is the supervisor pattern — proven effective for 3-8 agents
- **Implicit communication fails** — explicit handoff briefs (Agent Baton's approach) are more reliable

### From AutoGen
- **Agent-agent chat is token-expensive** — Agent Baton's "orchestrator mediates all communication" is cheaper
- **Human-in-the-loop at boundaries** (not mid-stream) is the right granularity
- **Uncontrolled delegation** leads to circular patterns — Agent Baton's plan-first approach prevents this

### From Production Deployments (Common Patterns)
- Teams that succeed keep agent counts low (3-5 per task)
- Shared context documents reduce redundant file reads by 60-80%
- QA gates between phases catch 70%+ of integration issues before they compound
- The biggest failure mode is not agent quality but **poor task decomposition**
