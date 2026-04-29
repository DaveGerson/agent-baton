"""Planning subsystem — pipeline-based plan construction.

This package replaces the monolithic ``agent_baton/core/engine/planner.py``
god-class.  The public entry point is still ``IntelligentPlanner``, which
now lives in ``planning.planner`` as a thin shell over the pipeline.

The legacy ``agent_baton.core.engine.planner`` module re-exports the
new class so existing imports keep working.

Submodules
----------

* :mod:`agent_baton.core.engine.planning.draft`     — ``PlanDraft`` working state
* :mod:`agent_baton.core.engine.planning.services`  — ``PlannerServices`` DI container
* :mod:`agent_baton.core.engine.planning.protocols` — ``Stage`` protocol
* :mod:`agent_baton.core.engine.planning.pipeline`  — Pipeline runner
* :mod:`agent_baton.core.engine.planning.stages`    — Six pipeline stages
* :mod:`agent_baton.core.engine.planning.rules`     — Pure-data lookup tables
"""
from __future__ import annotations

from .draft import PlanDraft
from .pipeline import Pipeline
from .protocols import Stage
from .services import PlannerServices

__all__ = ["PlanDraft", "Pipeline", "PlannerServices", "Stage"]
