---
name: context-economics
description: Token cost model and context window budgeting for multi-agent orchestration — multiplication effects, information loss curves, and compaction strategies
tags: [context-window, tokens, cost, budgeting, compaction, handoff]
priority: high
---

# Context Window Economics

## Cost Model

| Resource | Cost | When Incurred |
|----------|------|---------------|
| Subagent spawn | ~200K token context capacity allocated | Each Agent tool call |
| File reads | ~1-5K tokens per file | Agent reads codebase files |
| Shared context | ~1-3K tokens | Every agent reads context.md |
| Handoff summary | ~500-2K tokens output | Orchestrator summarizes agent output |
| Compaction | ~50% information loss | Auto-triggered at context threshold |

## The Multiplication Effect

Each subagent independently reads files. 5 agents reading the same 10 files
= 50 file reads. The shared context doc (context.md) exists to prevent this —
research once, share the findings.

**Token cost per task by tier:**

| Tier | Agents | Estimated Tokens | When |
|------|--------|-----------------|------|
| Lean | 1-2 | 50-150K | Single-domain focused tasks |
| Standard | 3-5 | 150-500K | Multi-domain features |
| Full | 6-8 | 500K-1M+ | Large cross-cutting work |

## When to Pay for a New Context Window

Use the decision framework's five tests, but also consider:

| Factor | Inline (cheap) | Subagent (expensive) |
|--------|---------------|---------------------|
| Output size | < 50 lines | > 50 lines of code |
| Reasoning depth | Lookup/procedure | Judgment/analysis |
| Independence | Not needed | Needed (auditor, security) |
| Reuse | One-time | Could parallelize |
| Context pollution | Won't pollute parent | Would overwhelm parent |

## Information Loss Curve

Each handoff loses signal:

```
Original detail: 100%
  → Agent reads shared context: ~90% (some nuance lost in writing)
  → Agent works and returns summary: ~60% (implementation details dropped)
  → Orchestrator reads summary: ~40% (further compressed)
  → Next agent reads handoff brief: ~30%
```

**Mitigations:**
- Write file paths, not content summaries ("see src/api/routes.ts:45" not "the API uses Express")
- Include concrete names: function names, variable names, file paths
- Mission log captures decisions, not just outcomes

## Compaction Strategies

- Set `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80` for complex tasks (triggers earlier)
- Write plan, context, and mission log to disk BEFORE compaction can occur
- After compaction, re-read recovery files to restore state
- Keep orchestrator lean: delegate, don't accumulate detailed results in context
