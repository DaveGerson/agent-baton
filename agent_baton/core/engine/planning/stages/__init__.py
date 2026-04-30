"""Planning pipeline stages.

Each stage is a small class implementing the ``Stage`` protocol:
``run(draft: PlanDraft, services: PlannerServices) -> PlanDraft``.

Stage order (preserves legacy create_plan ordering — see comments in
each stage for the legacy step numbers):

1. ClassificationStage — initialize state, classify task
2. ResearchStage       — optional codebase discovery for broad-scope tasks
3. RosterStage        — pattern, retro, decompose, expand, route agents
4. RiskStage          — knowledge resolver setup, sensitivity, risk
5. DecompositionStage — build phases, resolve knowledge, foresight
6. EnrichmentStage    — gates, approvals, bead hints, context, prior beads
7. ValidationStage    — score check, budget tier, plan review (HARD GATE)
8. AssemblyStage      — build MachinePlan, emit telemetry
"""
from __future__ import annotations

from .assembly import AssemblyStage
from .classification import ClassificationStage
from .decomposition import DecompositionStage
from .enrichment import EnrichmentStage
from .research import ResearchStage
from .risk import RiskStage
from .roster import RosterStage
from .validation import PlanQualityError, ValidationStage

__all__ = [
    "AssemblyStage",
    "ClassificationStage",
    "DecompositionStage",
    "EnrichmentStage",
    "PlanQualityError",
    "ResearchStage",
    "RiskStage",
    "RosterStage",
    "ValidationStage",
]
