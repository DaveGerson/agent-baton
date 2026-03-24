---
name: prompt-engineering-principles
description: Principles for writing effective agent system prompts — structure, specificity rules, anti-patterns, instruction hierarchy, and testing methodology
tags: [prompt-engineering, agent-design, instructions, anti-patterns, testing]
priority: normal
---

# Prompt Engineering Principles for Agent System Prompts

## What Makes an Effective Agent Prompt

### Structure (in order)

1. **Identity** — "You are a [seniority] [role]." Sets the persona.
2. **Mission** — One sentence on what this agent does. Not what it knows.
3. **Pre-work** — "Before starting, read [knowledge packs]." Grounds the agent.
4. **Domain knowledge** — Baked-in facts (< 100 lines). Tables over prose.
5. **Principles** — 3-5 actionable rules. "Do X when Y" not "try to be good."
6. **Anti-patterns** — What NOT to do. Agents drift without negative constraints.
7. **Output format** — Exact structure the orchestrator expects back.

### Key Principles

| Principle | Why | Example |
|-----------|-----|---------|
| **Specific over general** | Vague instructions produce vague output | "Write tests for each public method" not "write comprehensive tests" |
| **Constrain, don't suggest** | Agents follow rules better than suggestions | "You MUST NOT modify files outside src/" not "try to stay within src/" |
| **Tables over prose** | Agents scan tables 10x faster | Put domain terms, valid values, field mappings in tables |
| **Negative examples** | Prevents common failure modes | "Do NOT use mocks for database tests — we use a real test DB" |
| **Output templates** | Ensures consistent, parseable responses | Provide the exact markdown structure to return |

### Anti-Patterns in Prompt Writing

| Anti-Pattern | Problem | Fix |
|-------------|---------|-----|
| **Wall of text** | Agent loses focus; key instructions buried | Use headers, bold, tables. Front-load critical rules. |
| **Conflicting instructions** | Agent picks one randomly | Audit for contradictions. Priority order wins. |
| **Implicit knowledge** | Agent guesses wrong | Make all assumptions explicit. Name files, tools, patterns. |
| **Over-prompting** | 500+ lines = context waste + instruction dilution | Stay under 300 lines. Move details to knowledge packs. |
| **No output format** | Agent returns unstructured text | Always specify return format. |

## Instruction Hierarchy

When instructions conflict, agents follow this priority (Claude's behavior):
1. System prompt (highest)
2. Explicit user instructions in the delegation
3. Content from read files
4. Training data defaults (lowest)

Design prompts knowing this: critical rules go in the system prompt body,
not in referenced files.

## Testing Agent Prompts

1. **Happy path** — Does the prompt produce correct output for a typical task?
2. **Edge case** — What happens with ambiguous input, missing files, conflicting requirements?
3. **Scope test** — Does the agent stay within its boundaries or drift?
4. **Retry test** — After a failure + feedback, does it correct correctly?
5. **Comparison** — Side-by-side with a prompt variant: which produces better output?

Evaluate by running the same task 3 times — non-deterministic output means
the prompt needs tighter constraints.
