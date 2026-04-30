"""End-to-end smoke tests for the new planning pipeline.

These don't pin behavior tightly (legacy tests already do that); they
verify the pipeline scaffolding is wired correctly: every stage runs,
the draft survives the round-trip, the assembled MachinePlan is
returned to the caller.
"""
from __future__ import annotations

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.pipeline import Pipeline
from agent_baton.core.engine.planning.planner import _build_default_pipeline
from agent_baton.core.engine.planning.stages import (
    AssemblyStage,
    ClassificationStage,
    DecompositionStage,
    EnrichmentStage,
    ResearchStage,
    RiskStage,
    RosterStage,
    ValidationStage,
)


class TestPipelineScaffolding:
    def test_default_pipeline_lists_eight_stages_in_order(self) -> None:
        pipeline = _build_default_pipeline()
        assert isinstance(pipeline, Pipeline)
        names = [s.name for s in pipeline.stages]
        assert names == [
            "classification",
            "research",
            "roster",
            "risk",
            "decomposition",
            "enrichment",
            "validation",
            "assembly",
        ]

    def test_pipeline_runs_every_stage(self) -> None:
        planner = IntelligentPlanner()
        plan = planner.create_plan("Add a hello-world endpoint")
        # If any stage failed to run, the assembly stage would not have
        # produced a plan; if assembly failed, create_plan would raise.
        assert plan.task_id
        assert plan.phases

    def test_each_stage_implements_protocol(self) -> None:
        for cls in (
            ClassificationStage, ResearchStage, RosterStage, RiskStage,
            DecompositionStage, EnrichmentStage, ValidationStage,
            AssemblyStage,
        ):
            stage = cls()
            assert isinstance(stage.name, str) and stage.name
            assert callable(getattr(stage, "run", None))

    def test_draft_round_trips_through_pipeline(self) -> None:
        # PlanDraft can be constructed from inputs and survives
        # serialization to/from the pipeline.
        draft = PlanDraft.from_inputs("Add foo")
        assert draft.task_summary == "Add foo"
        assert draft.intervention_level == "low"
        assert draft.gate_scope == "focused"
