# Learning Automation — Honest Current State

**Last updated:** 2026-04-17 (D1 audit remediation)

This document describes the learning subsystem as it actually works today.
It replaces aspirational descriptions with an accurate account of what
each component does, what its limitations are, and what the intended
replacement pipeline looks like.

---

## What Exists Today

### PerformanceScorer

**Module:** `agent_baton/core/improve/scoring.py`

Tracks per-agent execution metrics by reading `usage-log.jsonl` or the
SQLite backend. Produces an `AgentScorecard` per agent with:

- `first_pass_rate` — fraction of uses that completed without retries.
- `retry_rate` — average retries per use.
- `gate_pass_rate` — fraction of gates that passed.
- `negative_mentions` — count of "What Didn't Work" retrospective entries
  mentioning this agent.
- `knowledge_gaps_cited` — count of knowledge gap records citing this agent.
- `health` — coarse label: `"needs-improvement"`, `"adequate"`, or `"healthy"`.

These are rate calculations over all historical records for the agent. There
is no statistical significance test. An agent with 3 uses and 2 failures
shows a 67% failure rate with the same weight as an agent with 50 uses and
33 failures.

### PatternLearner

**Module:** `agent_baton/core/learn/pattern_learner.py`

Groups completed tasks by `sequencing_mode` (used as a proxy for task type).
For each group it identifies the most common agent combination and computes
`success_rate` (fraction with outcome `"SHIP"`).

An `evidence_strength` score (formerly called "confidence") is calculated as:

```
evidence_strength = min(1.0, (sample_size / 15) * success_rate)
```

This is a heuristic ramp — it approaches 1.0 as sample count and success
rate both increase. It is NOT a statistically validated confidence interval.
Minimum data requirement: at least `min_sample_size` records (default 3)
before a group is considered.

The learner persists patterns to `learned-patterns.json` so the planner can
use them to propose agent sequences for future tasks.

### BudgetTuner

**Module:** `agent_baton/core/learn/budget_tuner.py`

Reads usage records and compares actual token consumption to the nominal
midpoint of the current budget tier (lean: 25k, standard: 275k, full: 750k).
When actual usage consistently falls below tier midpoint, it suggests a
downgrade; when it exceeds the tier, it suggests an upgrade.

Suggestions are output to `budget-recommendations.json`. They are
configuration change proposals, not decisions — the operator or APPLY phase
acts on them.

### ImprovementLoop

**Module:** `agent_baton/core/improve/loop.py`

The orchestration backbone. Calls TriggerEvaluator to decide whether to run,
then calls Recommender (which aggregates PerformanceScorer, PatternLearner,
BudgetTuner outputs), classifies each recommendation as auto-applicable or
escalated, and persists an `ImprovementReport`.

Auto-apply guardrails:
- Prompt changes: never auto-apply.
- Budget upgrades: never auto-apply.
- Routing reductions: never auto-apply.
- All others: only if `risk == "low"` and `evidence_strength >= threshold`.

### LearnedOverrides

**Module:** `agent_baton/core/learn/overrides.py`

Persists operational corrections (agent flavor mismatches, gate command
adjustments) to `learned-overrides.json`. These are applied automatically
on the next execution without going through the full pipeline.

---

## What Was Aspirational (Now Deprecated)

### ExperimentManager (DEPRECATED)

**Module:** `agent_baton/core/improve/experiments.py`

Previously described as tracking "A/B experiments." In practice: before/after
metric comparison with no concurrent A/B groups. Two builds of the same agent
were never run simultaneously; the "experiment" recorded metrics after a
recommendation was applied and compared them to the pre-change baseline.

This module is deprecated. Do not add new callers. The replacement is
before/after scorecard comparison across learning cycles (see Pipeline
Template below).

### PromptEvolutionEngine (DEPRECATED)

**Module:** `agent_baton/core/improve/evolution.py`

Previously described as "data-driven prompt evolution." In practice: a
template-based rule engine that matched scorecard thresholds to canned
suggestion strings (e.g., if `first_pass_rate < 0.5` then suggest "Add more
specific instructions"). The suggestions were generic, not derived from the
actual content of retrospectives or execution traces.

This module is deprecated. Do not add new callers. The replacement is the
`learning-analyst` agent (D2), which reads actual retrospectives and produces
specific, evidence-cited recommendations.

---

## Replacement: The Learning Cycle Pipeline

The learning system is being refactored from inline analysis code into a
repeatable baton execution plan template. The template is at:

```
templates/learning-cycle-plan.json
```

The pipeline has six phases:

| Phase | Agent | What It Does |
|-------|-------|-------------|
| COLLECT | test-engineer | SQL queries gather scorecards, pattern data, retrospective summaries, knowledge gaps from the last N executions. Writes a data bundle to the team-context directory. |
| ANALYZE | learning-analyst | Reads the collected data bundle. Identifies patterns and failures with specific evidence citations. |
| PROPOSE | learning-analyst | Outputs actionable recommendations (not generic templates). Each proposal names the specific agent, the specific failure mode, and the specific change. |
| REVIEW | (APPROVAL gate) | Human reviews proposals before anything is applied. |
| APPLY | backend-engineer | Writes approved changes to `learned-overrides.json`, agent definition files, or knowledge packs. |
| DOCUMENT | documentation-architect | Records what changed, why, and what outcome is expected as a retrospective entry. |

Operational corrections (routing mismatches, gate command fixes) bypass the
pipeline and go directly to the `LearnedOverrides` auto-apply path.

### Triggering a Learning Cycle

**Manual trigger:**
```
baton learn run-cycle             # create the plan and print it
baton learn run-cycle --run       # create and execute immediately
```

**Counter-based trigger:**

`TriggerEvaluator` tracks completed executions since the last learning cycle.
When the count reaches the threshold (configurable via `LEARNING_TRIGGER_COUNT`
env var, default 10), `baton execute status` reports:

```
Learning cycle recommended (N executions since last cycle)
```

The cycle does NOT run automatically — this is a flag, not an auto-trigger.
The operator or daemon runs it explicitly.

---

## What "Confidence" Means in This System

Across the codebase, fields previously named `confidence` have been relabeled
to `evidence_strength` (or equivalent) to avoid implying statistical
validation. The formula is a heuristic:

```
evidence_strength = min(1.0, (sample_size / 15) * success_rate)
```

The default threshold for acting on a pattern is 0.35 (reduced from 0.7).
In the CLI, patterns are displayed as "Seen N times, X% success rate" rather
than as a numeric confidence score.

---

## Capabilities Summary

| Capability | Status | Notes |
|-----------|--------|-------|
| Track per-agent success/failure rates | Working | PerformanceScorer |
| Identify common agent sequences | Working | PatternLearner |
| Suggest budget tier adjustments | Working | BudgetTuner |
| Auto-apply safe operational corrections | Working | LearnedOverrides |
| Human-reviewed proposal pipeline | Working | ProposalManager |
| Statistical significance tests | Not present | Not planned |
| Concurrent A/B experiments | Not present | ExperimentManager deprecated |
| Template-based prompt suggestions | Deprecated | PromptEvolutionEngine deprecated |
| Agent-driven retrospective analysis | In progress | learning-analyst agent (D2) |
| Repeatable learning cycle template | In progress | templates/learning-cycle-plan.json (D2) |
