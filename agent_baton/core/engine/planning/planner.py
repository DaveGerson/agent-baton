"""IntelligentPlanner — pipeline-based plan construction.

This is the new shell that replaces the 4,721-line god-class in
``_legacy_planner.py``.  Today it inherits from the legacy planner and
overrides ``create_plan`` — the override still delegates while stages
are being ported.  As each stage takes ownership of its share of the
legacy logic, ``create_plan`` runs the pipeline directly; eventually
the legacy parent class is deleted.

Public surface: identical to the legacy planner — same constructor
kwargs, same ``create_plan`` signature, same ``explain_plan``, same
``_last_*`` introspection attributes.  External callers (including
tests that access static methods on the class) see no change.
"""
from __future__ import annotations

from agent_baton.core.engine.planning._legacy_planner import (
    IntelligentPlanner as _LegacyIntelligentPlanner,
)


class IntelligentPlanner(_LegacyIntelligentPlanner):
    """Pipeline-based planner — replaces the legacy monolith.

    Inheriting from the legacy class preserves:

    * The full constructor (``__init__`` is not overridden yet)
    * Static and class methods (``_classify_to_preset_key`` etc.)
    * ``explain_plan`` and the ``_last_*`` introspection attributes
    * Any private helpers other code reaches into

    Stages (defined in ``planning.stages``) replace the body of
    ``create_plan`` incrementally.  Today the override is a no-op
    delegation; as stages are wired in, the body becomes a single
    ``Pipeline.run`` call and the corresponding ``_step_*`` legacy
    methods are deleted.

    Stage order:

    1. ClassificationStage — task_type, complexity, sensitivity, risk
    2. DecompositionStage  — phases, subtasks, structured-spec parsing
    3. RoutingStage        — agent selection + phase assignment
    4. EnrichmentStage     — knowledge, foresight, gates, beads
    5. ValidationStage     — score, policy, plan review (hard gate)
    6. AssemblyStage       — MachinePlan construction + telemetry
    """
