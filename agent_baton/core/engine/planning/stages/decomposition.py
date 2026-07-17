"""DecompositionStage — build phases, attach knowledge, apply foresight.

Owns legacy ``create_plan`` steps 10-12 in the original ordering:

* Step 9+9b:    ``_step_build_phases`` — pick the phase strategy
  (compound / explicit / classifier / pattern / complexity / default)
  and build the ``PlanPhase`` list.
* Step 9.5+9.6: ``_step_resolve_knowledge`` — attach knowledge
  documents to each step.
* Step 9.7+9.8: ``_step_apply_foresight`` — insert preventive steps
  for HIGH+ risk plans; re-resolve knowledge for inserted steps.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.archetypes import get_archetype_config
from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.rules.phase_templates import PHASE_NAMES as _PHASE_NAMES
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.core.engine.planning.utils.phase_builder import (
    _normalize_phase_name,
    apply_pattern,
    assign_agents_to_phases,
    build_compound_phases,
    build_phases_for_names,
    default_phases,
    enrich_phases,
    phases_from_dicts,
)
from agent_baton.core.engine.planning.utils.phase_normalize import (
    normalize_phase_references,
    snapshot_phase_state,
)
from agent_baton.core.engine.planning.utils.repo_grounding import (
    gather_repo_findings,
    ground_phases_in_repository,
)
from agent_baton.core.orchestration.router import REVIEWER_AGENTS

if TYPE_CHECKING:
    from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)


class DecompositionStage:
    """Stage 4: build the phase list, attach knowledge, apply foresight."""

    name = "decomposition"

    # Same derivation ValidationStage._REVIEWER_BASES / RiskStage._REVIEWER_BASES
    # use (validation.py:143, risk.py:48) — keeps "is a reviewer-class agent"
    # agreement across the roster-filtering, safety-injection, and gate stages.
    # ``auditor`` is excluded: it is governed by the separate Audit
    # phase/gate, not the Review phase this stage reasons about.
    _REVIEWER_BASES = REVIEWER_AGENTS - {"auditor"}

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        # Step 9+9b — build phase list.
        draft.plan_phases = self._build_phases(
            draft=draft,
            services=services,
        )

        # Step 9.5+9.6 — resolve knowledge attachments per step.
        self._resolve_knowledge(
            plan_phases=draft.plan_phases,
            draft=draft,
            services=services,
        )

        # Step 9.7+9.8 — foresight (may rebuild plan_phases).
        draft.plan_phases = self._apply_foresight(
            plan_phases=draft.plan_phases,
            draft=draft,
            services=services,
        )
        return draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_phases(
        self,
        *,
        draft: PlanDraft,
        services: PlannerServices,
    ) -> list["PlanPhase"]:
        """Steps 9 / 9b — phase construction and enrichment."""
        registry = services.registry
        task_id = draft.task_id
        task_summary = draft.task_summary
        inferred_type = draft.inferred_type
        inferred_complexity = draft.inferred_complexity
        complexity = draft.complexity
        resolved_agents = draft.resolved_agents
        phases = draft.phases
        classified_phases = draft.classified_phases
        pattern = draft.pattern
        subtask_data = draft.subtask_data
        agent_route_map = draft.agent_route_map

        # Minimum phase counts by complexity — prevents the classifier
        # from returning a single phase for a heavy task.
        _MIN_PHASES = {"heavy": 3, "medium": 2, "light": 1}

        # 9. Build phases
        # Archetype-aware phase construction — when the classifier has determined
        # an archetype, use it to select phase templates unless the user provided
        # explicit phases or compound subtasks.
        archetype = getattr(draft, 'planning_archetype', 'phased')
        if archetype != "phased" and subtask_data is None and phases is None:
            config = get_archetype_config(archetype)
            if archetype == "direct":
                plan_phases = self._build_direct_phases(draft, config, registry)
            elif archetype == "investigative":
                plan_phases = self._build_investigative_phases(draft, config, registry)
            else:
                plan_phases = build_phases_for_names(
                    config.phase_template, resolved_agents, task_summary, registry
                )
            logger.info(
                "Archetype %r selected phases for task_id=%s: %s",
                archetype, task_id,
                [(p.name, [s.agent_name for s in p.steps]) for p in plan_phases],
            )
            self._ground_heavy_task(plan_phases, draft)
            plan_phases = enrich_phases(plan_phases, task_summary, registry)
            if getattr(draft, 'research_concerns', None):
                draft.concerns = list(draft.research_concerns)
            return plan_phases

        if subtask_data is not None:
            # Compound task — each sub-task becomes its own phase
            plan_phases = build_compound_phases(subtask_data, agent_route_map, registry)
        elif phases is not None:
            plan_phases = phases_from_dicts(phases, resolved_agents, task_summary, registry)
        elif classified_phases is not None:
            min_required = _MIN_PHASES.get(inferred_complexity, 1)
            if len(classified_phases) >= min_required:
                plan_phases = build_phases_for_names(
                    classified_phases, resolved_agents, task_summary, registry
                )
            else:
                logger.warning(
                    "Classifier returned %d phase(s) for %s complexity — "
                    "falling through to default phases",
                    len(classified_phases), inferred_complexity,
                )
                plan_phases = default_phases(
                    inferred_type, resolved_agents, task_summary, registry
                )
        elif pattern is not None:
            plan_phases = apply_pattern(pattern, inferred_type, task_summary)
            # Apply routed agent names to pattern-derived phases
            plan_phases = assign_agents_to_phases(plan_phases, resolved_agents, task_summary, registry)
        elif complexity is not None:
            # Explicit complexity override — scale phases to match.
            from agent_baton.core.engine.classifier import KeywordClassifier as _KC
            complexity_phases = _KC()._select_phases(inferred_type, inferred_complexity, _PHASE_NAMES)

            # The complexity-driven phase count and the roster are computed
            # independently on this path (phases via KeywordClassifier,
            # roster via rules/default_agents.py's static DEFAULT_AGENTS).
            # KeywordClassifier keeps its own roster/phase selection paired
            # (_select_agents drops reviewer-class agents at the same
            # complexity tiers _select_phases drops "Review"), but this
            # override path does not inherit that pairing. If the resulting
            # phase list has no Review phase, drop reviewer-class agents
            # (except auditor, which is governed by the separate Audit
            # phase/gate) from the roster before phases are built, so a
            # rostered reviewer never ends up stranded inside an Implement
            # phase where ValidationStage's review_missing gate would
            # reject the plan.
            has_review_phase = any(
                _normalize_phase_name(name) == "review" for name in complexity_phases
            )
            if not has_review_phase:
                filtered_agents = [
                    a for a in resolved_agents
                    if a.split("--")[0] not in self._REVIEWER_BASES
                ]
                if filtered_agents != resolved_agents:
                    resolved_agents = filtered_agents
                    draft.resolved_agents = filtered_agents

            plan_phases = build_phases_for_names(complexity_phases, resolved_agents, task_summary, registry)
        else:
            plan_phases = default_phases(inferred_type, resolved_agents, task_summary, registry)

        logger.info(
            "Plan phases selected for task_id=%s: %s",
            task_id,
            [(p.name, [s.agent_name for s in p.steps]) for p in plan_phases],
        )

        # 9a.5. Repository-grounded decomposition (heavy tasks only) — must
        # run BEFORE 9b's enrich_phases, which only fills fields that are
        # still empty: grounding a step in concrete repo evidence here
        # means enrich_phases's generic per-agent template never
        # overwrites it, while a step grounding found nothing for still
        # falls through to that same generic-template fallback unchanged.
        self._ground_heavy_task(plan_phases, draft)

        # 9b. Enrich steps with cross-phase context and default deliverables
        plan_phases = enrich_phases(plan_phases, task_summary, registry)

        # Propagate research concerns so EnrichmentStage can use them for
        # concern-splitting even when the task summary has no numbered markers.
        if draft.research_concerns:
            draft.concerns = list(draft.research_concerns)

        return plan_phases

    def _ground_heavy_task(
        self,
        plan_phases: list["PlanPhase"],
        draft: PlanDraft,
    ) -> None:
        """Heavy-complexity-only: ground steps in concrete repository
        evidence (files, tests, symbols) instead of leaving them on
        generic per-agent/per-phase templates. No-op for light/medium
        tasks and a no-op (by construction — see
        ``repo_grounding.gather_repo_findings``) when no project_root is
        available or the repository yields no matching evidence, so
        deterministic template-based behavior is unchanged whenever repo
        grounding has nothing to add.
        """
        if draft.inferred_complexity != "heavy":
            return
        findings = gather_repo_findings(draft.project_root, draft.task_summary)
        ground_phases_in_repository(plan_phases, draft.task_summary, findings)

    def _resolve_knowledge(
        self,
        *,
        plan_phases: list["PlanPhase"],
        draft: PlanDraft,
        services: PlannerServices,
    ) -> None:
        """Steps 9.5 + 9.6 — knowledge resolution and gap-suggested attachments.

        Mutates *plan_phases* steps in place by setting ``step.knowledge``.
        """
        resolver = draft.resolver
        ranker = draft.ranker
        max_knowledge_per_step = draft.max_knowledge_per_step
        inferred_type = draft.inferred_type
        risk_level = draft.risk_level
        explicit_knowledge_packs = draft.explicit_knowledge_packs
        explicit_knowledge_docs = draft.explicit_knowledge_docs

        # 9.5. Resolve knowledge attachments for each step.
        if resolver is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        resolved = resolver.resolve(
                            agent_name=step.agent_name,
                            task_description=step.task_description,
                            task_type=inferred_type,
                            risk_level=risk_level,
                            explicit_packs=explicit_knowledge_packs or [],
                            explicit_docs=explicit_knowledge_docs or [],
                        )
                        if ranker is not None:
                            resolved = ranker.rank(resolved)
                        step.knowledge = resolved[:max_knowledge_per_step]
                    except Exception:
                        logger.debug(
                            "Knowledge resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

        # 9.6. Gap-suggested attachments
        pattern_learner = services.pattern_learner
        if resolver is not None and pattern_learner is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        prior_gaps = pattern_learner.knowledge_gaps_for(
                            step.agent_name, inferred_type
                        )
                        for gap in prior_gaps:
                            matches = resolver.resolve(
                                agent_name=step.agent_name,
                                task_description=gap.description,
                            )
                            existing_paths = {a.path for a in step.knowledge if a.path}
                            for match in matches:
                                if match.path and match.path in existing_paths:
                                    continue
                                match.source = "gap-suggested"
                                step.knowledge.append(match)
                                if match.path:
                                    existing_paths.add(match.path)
                    except Exception:
                        logger.debug(
                            "Gap-suggested resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

    def _apply_foresight(
        self,
        *,
        plan_phases: list["PlanPhase"],
        draft: PlanDraft,
        services: PlannerServices,
    ) -> list["PlanPhase"]:
        """Steps 9.7 + 9.8 — foresight insertion and post-foresight
        knowledge resolution for inserted steps.

        Foresight may rebuild *plan_phases*, so this returns the new list.
        Writes ``draft.foresight_insights`` for pipeline consumers.
        """
        task_summary = draft.task_summary
        risk_level = draft.risk_level
        resolved_agents = draft.resolved_agents
        resolver = draft.resolver
        ranker = draft.ranker
        max_knowledge_per_step = draft.max_knowledge_per_step
        inferred_type = draft.inferred_type
        explicit_knowledge_packs = draft.explicit_knowledge_packs
        explicit_knowledge_docs = draft.explicit_knowledge_docs

        foresight_engine = services.foresight_engine

        # 9.7. Foresight analysis
        # Snapshot phase/step identity->id BEFORE foresight may insert a
        # phase ahead of an existing one and renumber everything after
        # it -- ForesightEngine.analyze mutates the surviving PlanPhase/
        # PlanStep objects in place, so their old ids are otherwise gone
        # the instant it reassigns them. See
        # ``planning.utils.phase_normalize`` module docstring.
        pre_phase_ids, pre_step_ids = snapshot_phase_state(plan_phases)

        foresight_insights: list = []
        try:
            plan_phases, foresight_insights = foresight_engine.analyze(
                plan_phases,
                task_summary,
                risk_level=risk_level,
                existing_agents=resolved_agents,
            )
        except Exception:
            logger.debug(
                "Foresight analysis failed — skipping",
                exc_info=True,
            )

        # Store on the draft for pipeline consumers and _sync_last_state.
        draft.foresight_insights = foresight_insights

        # 9.7b. Repair any phase/step reference that went stale because
        # foresight renumbered phases -- e.g. step 9b's "Build on the ...
        # output from phase N" text baked in before foresight ran. No-op
        # when foresight didn't actually change any numbering.
        plan_phases = normalize_phase_references(
            plan_phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids,
        )

        # 9.8. Resolve knowledge for foresight-inserted steps.
        if resolver is not None and foresight_insights:
            foresight_step_ids: set[str] = set()
            for ins in foresight_insights:
                foresight_step_ids.update(ins.inserted_step_ids)
            for phase in plan_phases:
                for step in phase.steps:
                    if step.step_id in foresight_step_ids:
                        try:
                            resolved = resolver.resolve(
                                agent_name=step.agent_name,
                                task_description=step.task_description,
                                task_type=inferred_type,
                                risk_level=risk_level,
                                explicit_packs=explicit_knowledge_packs or [],
                                explicit_docs=explicit_knowledge_docs or [],
                            )
                            if ranker is not None:
                                resolved = ranker.rank(resolved)
                            step.knowledge = resolved[:max_knowledge_per_step]
                        except Exception:
                            logger.debug(
                                "Knowledge resolution failed for foresight step %s — skipping",
                                step.step_id,
                                exc_info=True,
                            )
        return plan_phases

    def _build_direct_phases(
        self,
        draft: PlanDraft,
        config,  # ArchetypeConfig
        registry,
    ) -> list["PlanPhase"]:
        """DIRECT archetype: single Implement + Review, minimal overhead."""
        from agent_baton.models.execution import PlanPhase, PlanStep

        # Single implement step with the best-fit agent
        implement_agent = draft.resolved_agents[0] if draft.resolved_agents else "backend-engineer"
        implement_step = PlanStep(
            step_id="1.1",
            agent_name=implement_agent,
            task_description=draft.task_summary,
            step_type="developing",
        )

        implement_phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[implement_step],
            # Gate left unset (None) rather than a hardcoded command --
            # EnrichmentStage._apply_gates only fills in ``default_gate``
            # (which is where gate_scope/stack detection actually live)
            # for phases whose gate is still None at that point. A
            # hardcoded PlanGate here silently opted DIRECT-archetype
            # plans out of gate_scope="full"/"smoke" entirely (bd-124f
            # regression -- see tests/test_planner_gate_scoping.py
            # TestCreatePlanGateScope).
        )

        # Lightweight review phase
        review_step = PlanStep(
            step_id="2.1",
            agent_name="code-reviewer",
            task_description=f"Review the implementation of: {draft.task_summary}",
            step_type="reviewing",
            depends_on=["1.1"],
        )
        review_phase = PlanPhase(
            phase_id=2,
            name="Review",
            steps=[review_step],
        )

        return [implement_phase, review_phase]

    def _build_investigative_phases(
        self,
        draft: PlanDraft,
        config,  # ArchetypeConfig
        registry,
    ) -> list["PlanPhase"]:
        """INVESTIGATIVE archetype: hypothesis-driven with structured investigation."""
        from agent_baton.models.execution import PlanPhase, PlanStep, PlanGate

        task = draft.task_summary
        investigate_agent = draft.resolved_agents[0] if draft.resolved_agents else "general-purpose"
        fix_agent = draft.resolved_agents[0] if draft.resolved_agents else "backend-engineer"

        phases = [
            PlanPhase(
                phase_id=1,
                name="Investigate",
                steps=[PlanStep(
                    step_id="1.1",
                    agent_name=investigate_agent,
                    task_description=(
                        f"Investigate and reproduce: {task}\n\n"
                        "1. Read error messages and stack traces carefully\n"
                        "2. Reproduce the issue consistently\n"
                        "3. Check recent changes (git log/blame)\n"
                        "4. Trace data flow backward from symptom\n"
                        "5. Document: symptoms, timeline, affected paths, reproduction steps\n\n"
                        "Output a structured investigation report with evidence."
                    ),
                    model="opus",
                    step_type="consulting",
                )],
                gate=PlanGate(
                    gate_type="review",
                    description="Confirm reproduction achieved and evidence gathered",
                ),
            ),
            PlanPhase(
                phase_id=2,
                name="Hypothesize",
                steps=[PlanStep(
                    step_id="2.1",
                    agent_name=investigate_agent,
                    task_description=(
                        f"Based on investigation of: {task}\n\n"
                        "1. Form ranked hypotheses from evidence (most likely first)\n"
                        "2. For top hypothesis: design a minimal test that would confirm or falsify it\n"
                        "3. State concrete prediction: 'If hypothesis X is correct, then Y should be true'\n"
                        "4. If hypothesis is falsified, include 'RETRY_PHASE' in output to loop back\n\n"
                        "Output: ranked hypotheses with evidence, test design, and prediction."
                    ),
                    step_type="consulting",
                    depends_on=["1.1"],
                )],
            ),
            PlanPhase(
                phase_id=3,
                name="Fix",
                steps=[
                    PlanStep(
                        step_id="3.1",
                        agent_name="test-engineer",
                        task_description=(
                            f"Write a failing regression test for: {task}\n\n"
                            "The test must fail with the current bug and pass after the fix. "
                            "This is the RED phase of TDD."
                        ),
                        step_type="testing",
                        depends_on=["2.1"],
                    ),
                    PlanStep(
                        step_id="3.2",
                        agent_name=fix_agent,
                        task_description=(
                            f"Implement the minimal fix for: {task}\n\n"
                            "Fix the root cause identified in the hypothesis phase. "
                            "The regression test from step 3.1 must pass after your fix."
                        ),
                        step_type="developing",
                        depends_on=["3.1"],
                    ),
                ],
                # Gate left unset -- see the "Implement" phase comment in
                # _build_direct_phases above; EnrichmentStage._apply_gates
                # fills this in via gate_scope-aware ``default_gate``.
            ),
            PlanPhase(
                phase_id=4,
                name="Verify",
                steps=[PlanStep(
                    step_id="4.1",
                    agent_name="code-reviewer",
                    task_description=(
                        f"Verify root-cause fix for: {task}\n\n"
                        "1. Confirm fix addresses root cause, not just symptom\n"
                        "2. Check for related instances (same pattern elsewhere)\n"
                        "3. Verify regression test covers the actual failure mode\n"
                        "4. Assess if fix introduces new risks"
                    ),
                    step_type="reviewing",
                    depends_on=["3.2"],
                )],
            ),
        ]
        return phases
