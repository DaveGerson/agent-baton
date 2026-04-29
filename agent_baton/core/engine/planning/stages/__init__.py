"""Planning pipeline stages.

Each stage is a small class implementing the ``Stage`` protocol:
``run(draft: PlanDraft, services: PlannerServices) -> PlanDraft``.

Stage order (preserves legacy create_plan ordering — see comments in
each stage for the legacy step numbers):

1. ClassificationStage — initialize state, classify task
2. RosterStage        — pattern, retro, decompose, expand, route agents
3. RiskStage          — knowledge resolver setup, sensitivity, risk
4. DecompositionStage — build phases, resolve knowledge, foresight
5. EnrichmentStage    — gates, approvals, bead hints, context, prior beads
6. ValidationStage    — score check, budget tier, plan review (HARD GATE)
7. AssemblyStage      — build MachinePlan, emit telemetry
"""
from __future__ import annotations

from .assembly import AssemblyStage
from .classification import ClassificationStage
from .decomposition import DecompositionStage
from .enrichment import EnrichmentStage
from .risk import RiskStage
from .roster import RosterStage
from .validation import PlanQualityError, ValidationStage

__all__ = [
    "AssemblyStage",
    "ClassificationStage",
    "DecompositionStage",
    "EnrichmentStage",
    "PlanQualityError",
    "RiskStage",
    "RosterStage",
    "ValidationStage",
]
