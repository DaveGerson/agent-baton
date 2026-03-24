# Proposal 003: Closed-Loop Autonomous Learning & Self-Improvement

**Source**: `proposals/003-closed-loop-autonomous-learning.md` (pre-refactor commit `60bfb52`)
**Risk**: MEDIUM (entirely additive — no changes to existing engine behavior)
**Scope**: ~10 new files, ~6 modified files, ~12 test files, 16 steps across 5 phases

## Prompt

Run this prompt to kick off execution:

```
Use the orchestrator to execute Proposal 003: Closed-Loop Autonomous Learning.

## What To Build

The agent-baton system collects data (usage logs, traces, retrospectives) and runs
analysis (pattern learner, budget tuner, scoring, evolution engine), but the loop is
OPEN — nothing automatically acts on insights. This proposal closes the loop.

### New Files

1. **`agent_baton/models/improvement.py`** — Dataclasses: Recommendation (rec_id,
   category, target, action, description, evidence, confidence, risk, auto_applicable,
   proposed_change, rollback_spec, created_at, status), Experiment (experiment_id,
   recommendation_id, hypothesis, metric, baseline_value, target_value, agent_name,
   started_at, min_samples=5, max_duration_days=14, status, samples, result),
   Anomaly (anomaly_type, severity, agent_name, metric, current_value, threshold,
   sample_size, evidence), TriggerConfig (min_tasks_before_analysis=10,
   analysis_interval_tasks=5, agent_failure_threshold=0.3, gate_failure_threshold=0.2,
   budget_deviation_threshold=0.5, confidence_threshold=0.7),
   ImprovementReport (report_id, timestamp, skipped, reason, anomalies,
   recommendations, auto_applied, escalated, active_experiments),
   ImprovementConfig (auto_apply_threshold=0.8). All with to_dict/from_dict.

2. **`agent_baton/core/improve/triggers.py`** — TriggerEvaluator: checks if enough
   new data accumulated since last analysis. detect_anomalies() scans for agent
   failure rates >30%, gate failure rates >20%, budget overruns >50%, retry spikes,
   pattern drift. Returns list[Anomaly].

3. **`agent_baton/core/learn/recommender.py`** — Unified Recommender: takes
   PerformanceScorer, PatternLearner, BudgetTuner, PromptEvolutionEngine. analyze()
   runs all engines, produces list[Recommendation] with confidence, risk, auto_applicable.
   Categories: agent_prompt, budget_tier, routing, sequencing, gate_config, roster.
   Deduplicates and ranks by impact.

4. **`agent_baton/core/improve/proposals.py`** — ProposalManager: persists
   Recommendation lifecycle to `.claude/team-context/improvements/recommendations.jsonl`.
   Status transitions: proposed -> applied -> rolled_back (or rejected).

5. **`agent_baton/core/improve/experiments.py`** — ExperimentManager: creates
   experiments from applied recommendations, records samples from subsequent executions,
   evaluates outcome (improved if >5% gain, degraded if >5% loss, else inconclusive).
   Max 2 active experiments per agent. Min 5 samples before evaluating. Stores at
   `.claude/team-context/improvements/experiments/<id>.json`.

6. **`agent_baton/core/improve/rollback.py`** — RollbackManager: restores agent to
   pre-experiment state via AgentVersionControl. Circuit breaker: 3+ rollbacks in 7 days
   pauses all auto-apply and escalates. Logs to `.claude/team-context/improvements/rollbacks.jsonl`.

7. **`agent_baton/core/improve/loop.py`** — ImprovementLoop: the orchestrator.
   run_cycle() checks triggers -> generates recommendations -> classifies (auto_applicable
   + low risk + high confidence = auto-apply; everything else = escalate) -> applies safe
   ones -> creates experiments -> escalates risky ones via DecisionManager -> returns
   ImprovementReport. Stores reports at `.claude/team-context/improvements/reports/<id>.json`.

### New CLI Commands

8. **`agent_baton/cli/commands/improve/improve_cmd.py`** — `baton improve [--report] [--experiments] [--history]`
9. **`agent_baton/cli/commands/improve/experiment.py`** — `baton experiment {list|show|conclude|rollback}`
10. **`agent_baton/cli/commands/improve/anomalies.py`** — `baton anomalies [--watch]`

### Enhancements to Existing Modules

11. **`agent_baton/core/improve/scoring.py`** — Add detect_trends(agent_name, window=10)
    returning "improving"/"stable"/"degrading" via linear regression slope.
12. **`agent_baton/core/learn/pattern_learner.py`** — Add recommend_sequencing(task_type)
    returning optimal agent sequence + confidence from historical patterns.
13. **`agent_baton/core/learn/budget_tuner.py`** — Add auto_apply_recommendations(threshold=0.8)
    that only auto-applies downgrades (cheaper tier, never upgrades).
14. **CLI flag enhancements**: `baton scores --trends`, `baton patterns --recommendations`,
    `baton budget --auto-apply`

### Guardrails (NON-NEGOTIABLE)

- Only LOW-risk recommendations auto-apply
- Budget changes only auto-apply DOWNWARD (cheaper tier, never upgrade)
- Prompt changes NEVER auto-apply — always escalate to human
- Routing changes auto-apply only if confidence >= 0.9 and additive
- Sequencing changes auto-apply only if confidence >= 0.8 and success >= 0.9
- Rollback is ALWAYS automatic on degradation — no human approval needed
- Max 2 active experiments per agent
- Min 5 samples before evaluating any experiment
- Circuit breaker: 3+ rollbacks in 7 days -> pause all auto-apply, escalate

### Storage Layout

```
.claude/team-context/improvements/
  recommendations.jsonl      # append-only recommendation log
  experiments/<id>.json      # per-experiment tracking
  reports/<id>.json          # improvement cycle reports
  rollbacks.jsonl            # rollback audit trail
```

## Execution Phases

### Phase 1 — Foundation (5 steps)
1.1 models/improvement.py (6 dataclasses with to_dict/from_dict)
1.2 triggers.py (TriggerEvaluator, TriggerConfig, Anomaly) — parallel after 1.1
1.3 recommender.py (unified Recommender) — parallel after 1.1
1.4 proposals.py (ProposalManager, JSONL persistence) — parallel after 1.1
1.5 Tests for all Phase 1 modules
**Gate**: All Phase 1 test files pass

### Phase 2 — Experiment Tracking (3 steps)
2.1 experiments.py (ExperimentManager with max 2/agent, min 5 samples)
2.2 rollback.py (RollbackManager with circuit breaker)
2.3 Tests for Phase 2
**Gate**: All Phase 2 test files pass

### Phase 3 — Improvement Loop & CLI (3 steps)
3.1 loop.py (ImprovementLoop wiring everything together)
3.2 CLI commands: improve_cmd.py, experiment.py, anomalies.py
3.3 Tests for Phase 3
**Gate**: All Phase 3 test files pass

### Phase 4 — Enhancements to Existing (3 steps)
4.1 Add detect_trends() to scoring.py, recommend_sequencing() to pattern_learner.py,
    auto_apply_recommendations() to budget_tuner.py
4.2 Add --trends, --recommendations, --auto-apply flags to existing CLI commands
4.3 Tests for Phase 4 enhancements
**Gate**: All Phase 4 test files pass + existing tests still pass

### Phase 5 — Integration Review (1 step)
5.1 Code review: verify all 8 guardrail enforcement points, import hygiene,
    pattern consistency, error handling
**Gate**: Full pytest regression passes

## Agents

| Agent | Role |
|-------|------|
| backend-engineer--python | All implementation code |
| test-engineer | All test files |
| code-reviewer | Final review (Phase 5) |

## Git Strategy

Create `feat/proposal-003` branch. Commit each agent's work individually.
```
