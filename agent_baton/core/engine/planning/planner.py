"""IntelligentPlanner — standalone pipeline-based plan construction.

Runs a seven-stage pipeline.  Each stage owns its slice of the
plan-construction work; all helpers live in utils/ submodules.

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
from typing import TYPE_CHECKING, Any

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


class IntelligentPlanner:
    """Pipeline-based planner — standalone implementation.

    All collaborators are constructed in ``__init__``.  No inheritance
    from any legacy class.  ``_last_*`` attributes are written by
    ``_sync_last_state`` after the pipeline runs so that ``explain_plan``
    and test assertions on ``planner._last_*`` keep working.
    """

    def __init__(
        self,
        team_context_root: Path | None = None,
        classifier: Any = None,
        policy_engine: Any = None,
        retro_engine: Any = None,
        knowledge_registry: Any = None,
        task_classifier: Any = None,
        bead_store: Any = None,
        project_config: Any = None,
    ) -> None:
        self._team_context_root = team_context_root
        self._classifier = classifier
        self._policy_engine = policy_engine
        self._retro_engine = retro_engine
        self.knowledge_registry = knowledge_registry
        self._bead_store = bead_store

        # Build collaborators
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter
        from agent_baton.core.improve.scoring import PerformanceScorer
        from agent_baton.core.learn.pattern_learner import PatternLearner
        from agent_baton.core.learn.budget_tuner import BudgetTuner
        from agent_baton.core.engine.classifier import KeywordClassifier
        from agent_baton.core.engine.foresight import ForesightEngine
        from agent_baton.core.engine.plan_reviewer import PlanReviewer
        from agent_baton.core.config import ProjectConfig

        self._registry = AgentRegistry()
        self._registry.load_default_paths()
        self._router = AgentRouter(self._registry)
        self._scorer = PerformanceScorer()
        self._pattern_learner = PatternLearner(
            team_context_root=team_context_root,
        )
        self._budget_tuner = BudgetTuner(
            team_context_root=team_context_root,
        )

        if task_classifier is not None:
            self._task_classifier = task_classifier
        else:
            try:
                from agent_baton.core.engine.classifier import HaikuTaskClassifier
                self._task_classifier = HaikuTaskClassifier()
            except Exception:
                self._task_classifier = KeywordClassifier()

        self._foresight_engine = ForesightEngine()
        self._plan_reviewer = PlanReviewer()

        if project_config is not None:
            self._project_config = project_config
        else:
            self._project_config = ProjectConfig.load(
                start_dir=team_context_root,
            )

        # Pipeline
        self._pipeline = _build_default_pipeline()

        # Per-call introspection state (reset each create_plan call)
        self._last_task_classification: Any = None
        self._last_classification: Any = None
        self._last_foresight_insights: list = []
        self._last_review_result: Any = None
        self._last_score_warnings: list[str] = []
        self._last_policy_violations: list = []
        self._last_routing_notes: list[str] = []
        self._last_pattern_used: Any = None
        self._last_retro_feedback: Any = None
        self._last_team_cost_estimates: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
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
        """Build a complete plan by running the seven-stage pipeline."""
        from agent_baton.core.observability import current_exporter
        from datetime import datetime, timezone

        # Reset per-call introspection state.
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

        services = self._build_services()

        # Run the pipeline.
        draft = self._pipeline.run(draft, services)

        # Sync _last_* attributes from the draft so tests and explain_plan work.
        self._sync_last_state(draft)

        return AssemblyStage.extract_plan(draft)

    def explain_plan(self, plan: "MachinePlan") -> str:
        """Return a human-readable explanation of the last create_plan call."""
        lines: list[str] = [
            f"## Plan Explanation: {plan.task_summary}",
            "",
            f"**Task ID**: {plan.task_id}",
            f"**Risk Level**: {plan.risk_level}",
            f"**Budget Tier**: {plan.budget_tier}",
            f"**Complexity**: {plan.complexity}",
            f"**Git Strategy**: {plan.git_strategy}",
            f"**Classification Source**: {plan.classification_source}",
            "",
            "## Pattern Influence",
        ]

        if self._last_pattern_used is not None:
            lines.append(
                f"Pattern `{self._last_pattern_used.pattern_id}` applied "
                f"(confidence={self._last_pattern_used.confidence:.0%}, "
                f"success_rate={self._last_pattern_used.success_rate:.0%})."
            )
        else:
            lines.append("Default phase templates used (no learned pattern applied).")

        lines.append("")
        lines.append("## Score Warnings")
        if self._last_score_warnings:
            for w in self._last_score_warnings:
                lines.append(f"- {w}")
        else:
            lines.append("No agent health warnings.")

        lines.append("")
        lines.append("## Routing Notes")
        if self._last_routing_notes:
            for note in self._last_routing_notes:
                lines.append(f"- {note}")
        else:
            lines.append("No routing changes.")

        lines.append("")
        lines.append("## Phase Summary")
        for phase in plan.phases:
            agents_str = ", ".join(s.agent_name for s in phase.steps)
            gate_str = f" | gate: {phase.gate.gate_type}" if phase.gate else ""
            lines.append(f"- **{phase.name}**: {agents_str}{gate_str}")

        lines.append("")
        lines.append("## Data Classification")
        if self._last_classification is not None:
            cls = self._last_classification
            lines.append(f"Guardrail Preset: {cls.guardrail_preset}")
            if cls.signals_found:
                lines.append(f"Signals: {', '.join(cls.signals_found)}")
            lines.append(f"Confidence: {cls.confidence}")
        else:
            lines.append("No classifier configured.")

        lines.append("")
        lines.append("## Policy Notes")
        if self._last_policy_violations:
            for v in self._last_policy_violations:
                lines.append(f"- [{v.rule.severity.upper()}] {v.details}")
        else:
            lines.append("No policy violations detected.")

        if self._last_foresight_insights:
            lines.append("")
            lines.append("## Foresight Insights")
            for ins in self._last_foresight_insights:
                lines.append(
                    f"- [{ins.category}] {ins.description}"
                    + (f" (phase: {ins.inserted_phase_name})" if ins.inserted_phase_name else "")
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reset_explainability_state(self) -> None:
        """Reset per-call introspection attributes before each create_plan."""
        self._last_task_classification = None
        self._last_classification = None
        self._last_foresight_insights = []
        self._last_review_result = None
        self._last_score_warnings = []
        self._last_policy_violations = []
        self._last_routing_notes = []
        self._last_pattern_used = None
        self._last_team_cost_estimates = {}

    def isolation_for_step(self, step_id: str) -> str:
        """Return the configured isolation mode for *step_id*, or ``""``."""
        if not hasattr(self, "_isolation_overrides_map"):
            self._isolation_overrides_map = {}
        return self._isolation_overrides_map.get(step_id, "")

    def _sync_last_state(self, draft: PlanDraft) -> None:
        """Copy pipeline outputs from draft to _last_* introspection attrs."""
        self._last_task_classification = draft.task_classification
        self._last_classification = draft.classification
        self._last_foresight_insights = list(draft.foresight_insights)
        self._last_review_result = draft.review_result
        self._last_score_warnings = list(draft.score_warnings)
        self._last_policy_violations = list(draft.policy_violations)
        self._last_routing_notes = list(draft.routing_notes)
        self._last_pattern_used = draft.pattern
        self._last_retro_feedback = draft.retro_feedback
        self._last_team_cost_estimates = dict(draft.team_cost_estimates)

    def _build_services(self) -> PlannerServices:
        """Build the services container from this planner's collaborators."""
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
        )

    # ------------------------------------------------------------------
    # Backward-compat proxy methods delegating to utils/ functions
    # ------------------------------------------------------------------

    def _generate_task_id(self, summary: str) -> str:
        from agent_baton.core.engine.planning.utils.text_parsers import generate_task_id
        return generate_task_id(summary)

    def _infer_task_type(self, summary: str) -> str:
        from agent_baton.core.engine.planning.utils.text_parsers import infer_task_type
        return infer_task_type(summary)

    def _parse_subtasks(self, summary: str) -> list[tuple[int, str]]:
        from agent_baton.core.engine.planning.utils.text_parsers import parse_subtasks
        return parse_subtasks(summary)

    @staticmethod
    def _parse_concerns(summary: str) -> list[tuple[str, str]]:
        from agent_baton.core.engine.planning.utils.text_parsers import parse_concerns
        return parse_concerns(summary)

    def _parse_structured_description(
        self,
        summary: str,
    ) -> tuple[list[dict] | None, list[str] | None]:
        from agent_baton.core.engine.planning.utils.text_parsers import parse_structured_description
        return parse_structured_description(summary, self._registry)

    def _expand_agents_for_concerns(
        self, agents: list[str], text: str
    ) -> list[str]:
        from agent_baton.core.engine.planning.utils.roster_logic import expand_agents_for_concerns
        return expand_agents_for_concerns(agents, text)

    def _pick_agent_for_concern(
        self, concern_text: str, candidate_agents: list[str]
    ) -> str:
        from agent_baton.core.engine.planning.utils.roster_logic import pick_agent_for_concern
        return pick_agent_for_concern(concern_text, candidate_agents)

    def _assess_risk(self, task_summary: str, agents: list[str]) -> str:
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        return assess_risk(task_summary, agents)

    def _select_budget_tier(self, task_type: str, agent_count: int) -> str:
        from agent_baton.core.engine.planning.utils.risk_and_policy import select_budget_tier
        return select_budget_tier(task_type, agent_count, self._budget_tuner)

    def _detect_rag(self) -> bool:
        from agent_baton.core.engine.planning.utils.risk_and_policy import detect_rag
        return detect_rag()

    def _default_gate(
        self,
        phase_name: str,
        stack: Any = None,
        changed_paths: list[str] | None = None,
        gate_scope: str = "focused",
        project_root: Path | None = None,
    ) -> Any:
        from agent_baton.core.engine.planning.utils.gates import default_gate
        return default_gate(
            phase_name,
            stack=stack,
            changed_paths=changed_paths,
            gate_scope=gate_scope,
            project_root=project_root,
        )

    def _default_phases(
        self,
        task_type: str,
        agents: list[str],
        task_summary: str = "",
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import default_phases
        return default_phases(task_type, agents, task_summary, self._registry)

    def _assign_agents_to_phases(
        self,
        phases: list,
        agents: list[str],
        task_summary: str = "",
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import assign_agents_to_phases
        return assign_agents_to_phases(phases, agents, task_summary, self._registry)

    def _enrich_phases(
        self,
        phases: list,
        task_summary: str = "",
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import enrich_phases
        return enrich_phases(phases, task_summary, self._registry)

    def _step_description(
        self, phase_name: str, agent_name: str, task_summary: str
    ) -> str:
        from agent_baton.core.engine.planning.utils.phase_builder import step_description
        return step_description(phase_name, agent_name, task_summary, self._registry)

    def _is_team_phase(self, phase: Any, task_summary: str) -> bool:
        from agent_baton.core.engine.planning.utils.phase_builder import is_team_phase
        return is_team_phase(phase, task_summary)

    def _consolidate_team_step(self, phase: Any) -> Any:
        from agent_baton.core.engine.planning.utils.phase_builder import consolidate_team_step
        return consolidate_team_step(phase)

    def _split_implement_phase_by_concerns(
        self,
        phase: Any,
        concerns: list[tuple[str, str]],
        candidate_agents: list[str],
        task_summary: str,
        knowledge_split_strategy: str = "smart",
    ) -> None:
        from agent_baton.core.engine.planning.utils.phase_builder import split_implement_phase_by_concerns
        split_implement_phase_by_concerns(
            phase, concerns, candidate_agents, task_summary, knowledge_split_strategy
        )

    def _extract_file_paths(self, text: str) -> list[str]:
        from agent_baton.core.engine.planning.utils.text_parsers import extract_file_paths
        return extract_file_paths(text)

    def _apply_project_config(
        self,
        phases: list,
        isolation_overrides: dict[str, str] | None = None,
    ) -> None:
        from agent_baton.core.engine.planning.utils.gates import apply_project_config
        if isolation_overrides is None:
            if not hasattr(self, "_isolation_overrides_map"):
                self._isolation_overrides_map = {}
            isolation_overrides = self._isolation_overrides_map
        apply_project_config(phases, self._project_config, isolation_overrides)

    def _validate_agents_against_policy(
        self,
        agents: list[str],
        policy_set: Any,
        plan_phases: list,
        policy_engine: Any = None,
    ) -> list:
        from agent_baton.core.engine.planning.utils.risk_and_policy import validate_agents_against_policy
        engine = policy_engine if policy_engine is not None else self._policy_engine
        return validate_agents_against_policy(agents, policy_set, plan_phases, engine)

    @staticmethod
    def _classify_to_preset_key(classification: Any) -> str:
        from agent_baton.core.engine.planning.utils.risk_and_policy import classify_to_preset_key
        return classify_to_preset_key(classification)

    @staticmethod
    def _agent_expertise_level(agent_name: str, registry: Any) -> str:
        from agent_baton.core.engine.planning.utils.roster_logic import agent_expertise_level
        return agent_expertise_level(agent_name, registry)

    @staticmethod
    def _agent_has_output_spec(agent_name: str, registry: Any) -> bool:
        from agent_baton.core.engine.planning.utils.roster_logic import agent_has_output_spec
        return agent_has_output_spec(agent_name, registry)

    def _apply_retro_feedback(
        self,
        agents: list[str],
        feedback: Any,
        routing_notes: list[str] | None = None,
    ) -> list[str]:
        from agent_baton.core.engine.planning.utils.roster_logic import apply_retro_feedback
        if routing_notes is None:
            routing_notes = self._last_routing_notes
        return apply_retro_feedback(agents, feedback, routing_notes)

    def _capture_planning_bead(
        self,
        task_id: str,
        content: str,
        tags: list[str] | None = None,
    ) -> None:
        from agent_baton.core.engine.planning.utils.context import capture_planning_bead
        capture_planning_bead(task_id, content, tags, self._bead_store)

    def _fetch_external_annotations(self, task_summary: str) -> list[str]:
        from agent_baton.core.engine.planning.utils.context import _fetch_external_annotations
        return _fetch_external_annotations(task_summary)

    def _build_shared_context(self, plan: "MachinePlan") -> str:
        from agent_baton.core.engine.planning.utils.context import build_shared_context
        return build_shared_context(
            plan,
            classification=self._last_classification,
            policy_violations=self._last_policy_violations or None,
            retro_feedback=None,
            team_cost_estimates=self._last_team_cost_estimates or None,
            foresight_insights=self._last_foresight_insights or None,
            task_summary=plan.task_summary,
        )

    # Proxy to let stages call _check_agent_scores via services.planner
    # (used by ValidationStage._check_scores).
    def _check_agent_scores(self, agents: list[str]) -> None:
        from agent_baton.core.engine.planning.utils.roster_logic import check_agent_scores
        warnings = check_agent_scores(agents, self._scorer, self._bead_store)
        self._last_score_warnings = warnings
        # Also write to draft.score_warnings if caller is ValidationStage;
        # ValidationStage reads _last_score_warnings directly.

    # Phase-builder compound helpers used by DecompositionStage.
    def _build_compound_phases(
        self, subtask_data: list[dict], agent_route_map: dict[str, str]
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import build_compound_phases
        return build_compound_phases(subtask_data, agent_route_map, self._registry)

    def _phases_from_dicts(
        self, phase_dicts: list[dict], agents: list[str], task_summary: str
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import phases_from_dicts
        return phases_from_dicts(phase_dicts, agents, task_summary, self._registry)

    def _build_phases_for_names(
        self, phase_names: list[str], agents: list[str], task_summary: str
    ) -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import build_phases_for_names
        return build_phases_for_names(phase_names, agents, task_summary, self._registry)

    def _apply_pattern(self, pattern: Any, task_type: str, task_summary: str = "") -> list:
        from agent_baton.core.engine.planning.utils.phase_builder import apply_pattern
        return apply_pattern(pattern, task_type, task_summary)
