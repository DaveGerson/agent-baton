# Audit Report: Learning & Improvement Subsystem

**Scope:** `core/learn/`, `core/improve/`, `models/learning.py`, `models/decision.py`
**Date:** 2026-04-13

---

## Findings

### 1. PatternLearner — OPEN-LOOP

The planner reads patterns via `load_patterns()` (`planner.py:573`), which reads `learned-patterns.json` from disk. But `refresh()` — which actually analyzes the usage log and writes that file — is only called from `cli/commands/improve/patterns.py`. The recommender calls `load_patterns()` (`recommender.py:211`) but only READS, never WRITES. Patterns go stale unless a human runs `baton patterns --refresh`.

### 2. BudgetTuner — OPEN-LOOP

The planner reads `budget-recommendations.json` via `load_recommendations()` (`planner.py:1941`), but `save_recommendations()` is only called from `cli/commands/improve/budget.py`. The `Recommender` calls `self._tuner.analyze()` which computes recommendations in memory but never persists them. The improvement loop calls `_apply_recommendation()` (`loop.py:257-260`) which only updates proposal status — it does not call `save_recommendations()`.

### 3. PerformanceScorer — WRITE-ONLY (advisory)

The planner scores agents (`planner.py:1859`) and populates `self._last_score_warnings` (`planner.py:1865-1868`) which surfaces in `explain_plan()` output, but never acts on them — no drop, deprioritize, or reroute of low-health agents. Scoring data feeds into the Recommender which generates routing recommendations, but these only apply through the improvement loop's experiment system, not back into the planner.

### 4. Recommender — DISCONNECTED

Never imported by the planner or executor directly. Consumed only by `ImprovementLoop` (`loop.py:91`). The improvement loop runs at execution completion (`executor.py:1045-1047`), and the Recommender's output feeds into `ProposalManager` and `ExperimentManager`. However, the planner never queries proposals or experiments when generating new plans.

### 5. LearningInterviewer — CLI-ONLY

Imported only by `cli/commands/improve/learn_cmd.py`. Requires interactive terminal input (`input()` calls at `interviewer.py:279-285`). Cannot be used during automated/headless orchestration. No programmatic API exists.

### 6. PromptEvolutionEngine — WRITE-ONLY

Generates `EvolutionProposal` objects with actionable suggestions, but there is no code path that applies them. The `apply()` method mentioned in the class docstring (`evolution.py:121-122`) does not exist on the class. Proposals are written to disk via `save_proposals()` (`evolution.py:246`) but no code reads them back. The Recommender wraps proposals into `Recommendation` objects (`recommender.py:176-203`) marked `auto_applicable=False`, meaning the improvement loop always escalates them (`loop.py:240-241`) — but there is no notification mechanism.

### 7. AgentVersionControl (vcs.py) — WRITE-ONLY

VCS provides backup/restore and changelog tracking. The `PromptEvolutionEngine` holds a VCS reference (`evolution.py:136`) but never calls it since no `apply()` method exists. The only real consumer is the experiment rollback system.

### 8. LearningEngine.detect() — CLOSED LOOP ✓

The executor calls `LearningEngine(team_context_root=self._root).detect(state)` at completion (`executor.py:1055-1056`). The engine scans for 5 signal types, writes to the ledger, and auto-applies fixes when occurrence thresholds are met (`engine.py:287-313`). Applied overrides are consumed by the planner: gate overrides at `planner.py:1758-1760`, agent drops at `planner.py:1897-1898`.

### 9. detect_trends() — CLI-ONLY

`PerformanceScorer.detect_trends()` (`scoring.py:400-458`) is only called from `cli/commands/improve/scores.py`. Trend data never feeds back into agent selection, routing weight, or planning decisions.

### 10. LearnedOverrides — CLOSED LOOP ✓

Overrides written by resolvers are read by the planner at planning time: gate commands (`planner.py:1759`), agent drops (`planner.py:1898`), flavor overrides (via `overrides.py:180`).

---

## Summary Table

| Component | Category | Detail |
|-----------|----------|--------|
| PatternLearner.refresh() | OPEN-LOOP | Patterns read by planner but only written by manual CLI |
| BudgetTuner.save_recommendations() | OPEN-LOOP | Planner reads file that only CLI writes |
| PerformanceScorer score warnings | WRITE-ONLY | Warnings emitted but never acted on |
| Recommender | DISCONNECTED | Output never reaches planner or dispatcher |
| LearningInterviewer | CLI-ONLY | Requires interactive terminal; no programmatic API |
| PromptEvolutionEngine | WRITE-ONLY | Proposals generated but no apply path exists |
| detect_trends() | CLI-ONLY | Trend data computed but never consumed by automation |
| VCS | WRITE-ONLY | Changelog written but not queried by decision-making code |
| LearningEngine.detect() | **Closed loop** | executor → detect → ledger → resolve → overrides → planner |
| LearnedOverrides | **Closed loop** | Planner reads overrides at plan time |

## Key Architectural Gap

The system has two closed loops (`LearningEngine.detect()` → overrides → planner, and `ImprovementLoop.run_cycle()` → experiments) that operate independently. The improvement loop generates recommendations but does not persist them in a format the planner reads. The pattern learner and budget tuner have analysis capabilities that produce useful data, but the "write to disk" step is gated behind manual CLI invocation, so the planner reads stale or empty files during automated execution. **Most of the learning and improvement infrastructure is dormant during automated orchestration.**
