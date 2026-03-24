---
name: agent-evaluation
description: Quantitative and qualitative metrics for measuring agent quality, scorecard health ratings, A/B testing approach, and prompt evolution triggers
tags: [evaluation, metrics, scoring, retrospective, prompt-engineering, quality]
priority: normal
---

# Agent Evaluation Framework

## Measuring Agent Quality

### Quantitative Metrics (from usage logs)

| Metric | Definition | Good | Concerning |
|--------|-----------|------|-----------|
| **First-pass rate** | % of uses with 0 retries | > 80% | < 50% |
| **Retry rate** | Avg retries per use | < 0.3 | > 1.0 |
| **Gate pass rate** | % of QA gates passed | > 90% | < 70% |
| **Token efficiency** | Output quality per token spent | Subjective | Compare agents on same task |
| **Scope compliance** | % of uses within approved boundaries | 100% | Any violations |

### Qualitative Metrics (from retrospectives)

| Signal | Source | Indicates |
|--------|--------|-----------|
| Positive mentions in "What Worked" | Retrospective | Agent is effective |
| Negative mentions in "What Didn't" | Retrospective | Prompt needs revision |
| Knowledge gaps cited | Retrospective | Knowledge pack needed |
| Roster recommendation: "improve" | Retrospective | Specific prompt issue identified |
| Roster recommendation: "remove" | Retrospective | Agent isn't earning its context window |

## Scorecard Health Ratings

| Rating | Criteria | Action |
|--------|----------|--------|
| **Strong** | First-pass > 80%, no negative retro mentions | Keep as-is |
| **Adequate** | First-pass 50-80%, minor issues | Monitor; improve if pattern persists |
| **Needs Improvement** | First-pass < 50% OR repeated negatives | Revise prompt; review with prompt engineer |
| **Unused** | 0 uses across N tasks | Consider archiving; may indicate bad description triggers |

## Prompt A/B Testing

To compare prompt variants:

1. Create a variant: `agent-name.v2.md` alongside `agent-name.md`
2. Run the same task with each variant (3 runs each for non-determinism)
3. Compare: first-pass rate, output quality, scope compliance
4. If v2 wins, promote it (backup v1 via Agent VCS)

**Challenges:**
- Non-deterministic output means single comparisons are unreliable
- Task complexity varies — normalize by comparing on similar tasks
- Subjective quality needs human judgment (code review verdict as proxy)

## When to Evolve a Prompt

| Trigger | Evidence | Action |
|---------|----------|--------|
| Repeated retries on same issue | Usage log shows pattern | Add specific instruction addressing the failure mode |
| Knowledge gap in retrospective | Retro cites missing domain knowledge | Create knowledge pack, reference from agent prompt |
| Scope violations | Agent writes outside boundaries | Strengthen boundary language; add negative examples |
| Slow output | High token use, low signal | Tighten instructions; remove unnecessary context |
| Stack mismatch | Generic agent struggles with specific framework | Create flavored variant |
