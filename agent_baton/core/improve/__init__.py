"""Improve sub-package — performance scoring, prompt evolution, version control."""
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
