"""Improve sub-package -- the action layer of the closed-loop learning pipeline.

This package consumes insights from :mod:`~agent_baton.core.observe` and
:mod:`~agent_baton.core.learn` and translates them into concrete changes
to agent prompts, budget tiers, routing weights, and sequencing templates.

Key responsibilities:

* **Performance scoring** -- :class:`PerformanceScorer` computes per-agent
  :class:`AgentScorecard` objects that combine quantitative metrics (first-pass
  rate, retry rate, gate pass rate, token usage) with qualitative signals
  (retrospective mentions, knowledge gaps cited).  Scores are the primary
  input to routing recommendations.

* **Prompt evolution** -- moved out of code (L2.1, bd-362f).  The
  template-based ``PromptEvolutionEngine`` was retired in favour of the
  ``learning-analyst`` agent dispatched via ``baton learn run-cycle``,
  which reads actual retrospective content and execution traces.

* **Version control** -- :class:`AgentVersionControl` maintains timestamped
  backups and a changelog for agent definition files, enabling safe
  experimentation and rollback.

Additional modules in this package:

* ``triggers.py`` -- :class:`TriggerEvaluator` decides when enough new data
  has accumulated to warrant a new improvement cycle.
* ``proposals.py`` -- :class:`ProposalManager` persists recommendation
  lifecycle (proposed -> applied -> rolled_back).
* ``rollback.py`` -- :class:`RollbackManager` restores agents on
  degradation with a circuit breaker (3+ rollbacks in 7 days pauses
  auto-apply).
* ``loop.py`` -- :class:`ImprovementLoop` orchestrates the full cycle:
  triggers -> recommendations -> classification -> apply/escalate ->
  rollback.

Note: L2.1 (bd-362f) retired the experiment-tracking subsystem along
with prompt evolution; the retired ``ExperimentManager`` provided per-cycle
before/after metric comparison.  Impact validation now flows through the
learning-cycle pipeline (``baton learn run-cycle``) which compares
scorecards across full cycles.

Data flow::

    observe.UsageLogger + observe.RetrospectiveEngine
        |
        v
    learn.PatternLearner + learn.BudgetTuner
        |
        v
    learn.Recommender  (unified recommendation list)
        |
        v
    improve.ImprovementLoop
        |
        +--> PerformanceScorer   (agent scorecards)
        +--> ProposalManager     (recommendation persistence)
        +--> RollbackManager     (safe rollback + circuit breaker)
        +--> AgentVersionControl (backups + changelog)
"""
from __future__ import annotations

from agent_baton.core.improve.scoring import PerformanceScorer, AgentScorecard
from agent_baton.core.improve.vcs import AgentVersionControl, ChangelogEntry

__all__ = [
    "PerformanceScorer",
    "AgentScorecard",
    "AgentVersionControl",
    "ChangelogEntry",
]
