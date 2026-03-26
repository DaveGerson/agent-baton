"""Improve sub-package -- the action layer of the closed-loop learning pipeline.

This package consumes insights from :mod:`~agent_baton.core.observe` and
:mod:`~agent_baton.core.learn` and translates them into concrete changes
to agent prompts, budget tiers, routing weights, and sequencing templates.

Key responsibilities:

* **Performance scoring** -- :class:`PerformanceScorer` computes per-agent
  :class:`AgentScorecard` objects that combine quantitative metrics (first-pass
  rate, retry rate, gate pass rate, token usage) with qualitative signals
  (retrospective mentions, knowledge gaps cited).  Scores are the primary
  input to prompt evolution and routing recommendations.

* **Prompt evolution** -- :class:`PromptEvolutionEngine` identifies
  underperforming agents and generates :class:`EvolutionProposal` objects
  containing specific suggested prompt changes.  Prompt changes are NEVER
  auto-applied; they always require human review.

* **Version control** -- :class:`AgentVersionControl` maintains timestamped
  backups and a changelog for agent definition files, enabling safe
  experimentation and rollback.

Additional modules in this package:

* ``triggers.py`` -- :class:`TriggerEvaluator` decides when enough new data
  has accumulated to warrant a new improvement cycle.
* ``experiments.py`` -- :class:`ExperimentManager` tracks A/B-style
  experiments for applied recommendations.
* ``proposals.py`` -- :class:`ProposalManager` persists recommendation
  lifecycle (proposed -> applied -> rolled_back).
* ``rollback.py`` -- :class:`RollbackManager` restores agents on
  degradation with a circuit breaker (3+ rollbacks in 7 days pauses
  auto-apply).
* ``loop.py`` -- :class:`ImprovementLoop` orchestrates the full cycle:
  triggers -> recommendations -> classification -> apply/escalate ->
  experiments -> rollback.

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
        +--> PromptEvolutionEngine (prompt change proposals)
        +--> ProposalManager     (recommendation persistence)
        +--> ExperimentManager   (impact tracking)
        +--> RollbackManager     (safe rollback + circuit breaker)
        +--> AgentVersionControl (backups + changelog)
"""
from __future__ import annotations

from agent_baton.core.improve.scoring import PerformanceScorer, AgentScorecard
from agent_baton.core.improve.evolution import PromptEvolutionEngine, EvolutionProposal
from agent_baton.core.improve.vcs import AgentVersionControl, ChangelogEntry

__all__ = [
    "PerformanceScorer",
    "AgentScorecard",
    "PromptEvolutionEngine",
    "EvolutionProposal",
    "AgentVersionControl",
    "ChangelogEntry",
]
