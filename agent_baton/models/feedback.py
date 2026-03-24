"""Structured feedback aggregated from recent retrospectives.

This model is the bridge between the RetrospectiveEngine (observe layer) and
the IntelligentPlanner (engine layer).  It carries only the actionable signals
the planner needs; raw retrospective detail stays in the Retrospective model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.knowledge import KnowledgeGapRecord
from agent_baton.models.retrospective import (
    KnowledgeGap,
    RosterRecommendation,
    SequencingNote,
)


@dataclass
class RetrospectiveFeedback:
    """Aggregated feedback extracted from recent retrospective files.

    Produced by :meth:`~agent_baton.core.observe.retrospective.RetrospectiveEngine.load_recent_feedback`
    and consumed by :meth:`~agent_baton.core.engine.planner.IntelligentPlanner.create_plan`.

    Attributes:
        roster_recommendations: Agents the system suggests adding, improving,
            or removing, collected across the most recent retrospectives.
        knowledge_gaps: Missing knowledge packs or documentation identified
            during recent tasks.
        sequencing_notes: Phase-ordering feedback indicating which gates or
            phases were valuable and which should be reconsidered.
        source_count: How many retrospective files contributed to this feedback.
    """

    roster_recommendations: list[RosterRecommendation] = field(default_factory=list)
    knowledge_gaps: list[KnowledgeGapRecord] = field(default_factory=list)
    sequencing_notes: list[SequencingNote] = field(default_factory=list)
    source_count: int = 0

    # ------------------------------------------------------------------
    # Convenience helpers used by the planner
    # ------------------------------------------------------------------

    def agents_to_drop(self) -> set[str]:
        """Return agent names that retrospectives recommend removing."""
        return {
            r.target
            for r in self.roster_recommendations
            if r.action.lower() in ("remove", "drop")
        }

    def agents_to_prefer(self) -> set[str]:
        """Return agent names that retrospectives recommend preferring."""
        return {
            r.target
            for r in self.roster_recommendations
            if r.action.lower() in ("prefer", "improve", "create")
        }

    def has_feedback(self) -> bool:
        """Return True if any actionable feedback exists."""
        return bool(
            self.roster_recommendations
            or self.knowledge_gaps
            or self.sequencing_notes
        )
