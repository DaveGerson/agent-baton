"""IntelligentPlanner — pipeline-based plan construction.

Replaces the 4,721-line god-class in ``_legacy_planner.py`` with a thin
shell that runs a seven-stage pipeline.  Today the stages **bridge**
to the legacy ``_step_*`` methods on the inherited class; future
commits port each stage's body in-place and remove the corresponding
legacy methods.

Stage order (see ``planning.stages``):

1. ClassificationStage — initialize state, classify task
2. RosterStage         — pattern, retro, decompose, expand, route
3. RiskStage           — knowledge setup, data classify, risk
4. DecompositionStage  — build phases, resolve knowledge, foresight
5. EnrichmentStage     — gates, approvals, bead hints, context, prior beads
6. ValidationStage     — score, budget tier, plan review (HARD GATE)
7. AssemblyStage       — build MachinePlan, emit telemetry

Public surface is identical to the legacy planner — same constructor
kwargs, same ``create_plan`` signature, same ``explain_plan``, same
``_last_*`` introspection attributes.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning._legacy_planner import (
    IntelligentPlanner as _LegacyIntelligentPlanner,
)
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.pipeline import Pipeline
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.stages import (
    AssemblyStage,
    ClassificationStage,
    DecompositionStage,
    EnrichmentStage,
    RiskStage,
    RosterStage,
    ValidationStage,
)

if TYPE_CHECKING:
    from agent_baton.models.execution import GateScope, MachinePlan


def _build_default_pipeline() -> Pipeline:
    """Construct the canonical seven-stage planning pipeline."""
    return Pipeline([
        ClassificationStage(),
        RosterStage(),
        RiskStage(),
        DecompositionStage(),
        EnrichmentStage(),
        ValidationStage(),
        AssemblyStage(),
    ])


class IntelligentPlanner(_LegacyIntelligentPlanner):
    """Pipeline-based planner — replaces the legacy monolith.

    Inheriting from the legacy class is transitional scaffolding that
    keeps the seven bridge stages able to call into legacy ``_step_*``
    methods without breaking the public API or the static methods tests
    poke directly.  Each ported stage drops its bridge call; once no
    bridges remain, the legacy parent class is deleted.
    """

    def __init__(
        self,
        team_context_root: Path | None = None,
        classifier=None,
        policy_engine=None,
        retro_engine=None,
        knowledge_registry=None,
        task_classifier=None,
        bead_store=None,
        project_config=None,
    ) -> None:
        super().__init__(
            team_context_root=team_context_root,
            classifier=classifier,
            policy_engine=policy_engine,
            retro_engine=retro_engine,
            knowledge_registry=knowledge_registry,
            task_classifier=task_classifier,
            bead_store=bead_store,
            project_config=project_config,
        )
        self._pipeline = _build_default_pipeline()

    # ------------------------------------------------------------------

    def create_plan(
        self,
        task_summary: str,
        *,
        task_type: str | None = None,
        complexity: str | None = None,
        project_root: Path | None = None,
        agents: list[str] | None = None,
        phases: list[dict] | None = None,
        explicit_knowledge_packs: list[str] | None = None,
        explicit_knowledge_docs: list[str] | None = None,
        intervention_level: str = "low",
        default_model: str | None = None,
        gate_scope: "GateScope" = "focused",
    ) -> "MachinePlan":
        """Build a complete plan by running the seven-stage pipeline.

        Replaces the 300-line legacy ``create_plan`` body.  The pipeline
        owns ordering and stage invocation; each stage owns one cohesive
        slice of the work.
        """
        from agent_baton.core.observability import current_exporter
        from datetime import datetime, timezone

        # Reset per-call introspection state (legacy side effect).
        self._reset_explainability_state()

        # Build the draft from inputs.
        draft = PlanDraft.from_inputs(
            task_summary,
            task_type=task_type,
            complexity=complexity,
            project_root=project_root,
            agents=agents,
            phases=phases,
            explicit_knowledge_packs=explicit_knowledge_packs,
            explicit_knowledge_docs=explicit_knowledge_docs,
            intervention_level=intervention_level,
            default_model=default_model,
            gate_scope=gate_scope,
        )
        draft.otel_exporter = current_exporter()
        draft.otel_started_at = (
            datetime.now(timezone.utc) if draft.otel_exporter else None
        )

        # Build the services container, passing self as the transitional
        # legacy planner reference for bridge stages to call into.
        services = self._build_services()

        # Run the pipeline.
        draft = self._pipeline.run(draft, services)

        # Hand back the assembled plan.
        return AssemblyStage.extract_plan(draft)

    # ------------------------------------------------------------------

    def _build_services(self) -> PlannerServices:
        """Build the services container, passing self as the legacy hook.

        Inherits: ``self._registry``, ``self._router``, ``self._scorer``,
        ``self._pattern_learner``, ``self._budget_tuner``,
        ``self._task_classifier``, ``self._foresight_engine``,
        ``self._plan_reviewer``, ``self._project_config``,
        ``self._classifier``, ``self._policy_engine``,
        ``self._retro_engine``, ``self.knowledge_registry``,
        ``self._bead_store``, ``self._team_context_root``.
        """
        return PlannerServices(
            registry=self._registry,
            router=self._router,
            scorer=self._scorer,
            pattern_learner=self._pattern_learner,
            budget_tuner=self._budget_tuner,
            task_classifier=self._task_classifier,
            foresight_engine=self._foresight_engine,
            plan_reviewer=self._plan_reviewer,
            project_config=self._project_config,
            data_classifier=self._classifier,
            policy_engine=self._policy_engine,
            retro_engine=self._retro_engine,
            knowledge_registry=self.knowledge_registry,
            bead_store=self._bead_store,
            team_context_root=self._team_context_root,
            planner=self,
        )
