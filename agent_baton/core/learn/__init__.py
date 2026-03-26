"""Learn sub-package -- the pattern-extraction layer of the closed-loop pipeline.

This package sits between observe and improve: it reads the raw execution
data captured by :mod:`~agent_baton.core.observe` and derives actionable
insights that :mod:`~agent_baton.core.improve` consumes.

Key responsibilities:

* **Pattern learning** -- :class:`PatternLearner` groups completed task
  records by sequencing mode, computes success rates and confidence scores,
  and surfaces recurring agent combinations that predict success.  Learned
  patterns feed into the planner so future tasks can reuse proven sequences.

* **Budget tuning** -- :class:`BudgetTuner` analyses historical token
  consumption per task type and recommends tier changes (lean / standard /
  full) when actual usage consistently falls outside the current tier's
  boundaries.  Budget recommendations feed into the improvement loop's
  auto-apply pipeline.

Data flow::

    observe.UsageLogger  (JSONL usage records)
        |
        +--> PatternLearner  --> learned-patterns.json
        |                         \\ feeds planner for future tasks
        |
        +--> BudgetTuner     --> budget-recommendations.json
                                  \\ feeds improvement loop auto-apply
"""
from __future__ import annotations

from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.learn.budget_tuner import BudgetTuner

__all__ = [
    "PatternLearner",
    "BudgetTuner",
]
