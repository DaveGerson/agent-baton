"""Planning pipeline stages.

Each stage is a small class that implements the ``Stage`` protocol:
takes a ``PlanDraft`` + ``PlannerServices``, returns a ``PlanDraft``.

Order (matches the legacy ``create_plan`` body):

1. ClassificationStage — task_id, stack, task_type, complexity, risk
2. DecompositionStage  — phases, subtasks, concern splitting, structured-spec parsing
3. RoutingStage        — agent selection, retro filtering, phase assignment
4. EnrichmentStage     — knowledge, foresight, gates, context files, beads
5. ValidationStage     — score check, policy validation, plan review (hard gate)
6. AssemblyStage       — MachinePlan construction, shared_context, telemetry
"""
from __future__ import annotations
