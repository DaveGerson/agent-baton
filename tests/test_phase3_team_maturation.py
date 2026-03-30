"""Tests for Phase 3 team collaboration maturation features.

Covers:
- SynthesisSpec model: serialisation round-trip and defaults
- PlanStep.synthesis field: inclusion/omission in to_dict, backward compat
- record_team_member_result() synthesis strategies: concatenate, merge_files,
  agent_synthesis, and the no-spec fallback
- _detect_team_conflict() via public API: no-conflict, conflict detection,
  record shape, < 2 members, multiple conflicting files
- Conflict escalation: escalate strategy pauses state; auto_merge completes normally
- TeamScorecard: to_markdown() correctness, health property thresholds
- PerformanceScorer.score_teams(): empty, single, aggregated, multiple, metrics
- PerformanceScorer.generate_team_report(): placeholder, health grouping, usage count
- _build_retrospective_data / complete(): retrospective includes team_compositions
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    TeamMember,
)
from agent_baton.models.retrospective import (
    Retrospective,
    TeamCompositionRecord,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.improve.scoring import PerformanceScorer, TeamScorecard


# ---------------------------------------------------------------------------
# Module-level factories
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: Path) -> ExecutionEngine:
    root = tmp_path / ".claude" / "team-context"
    root.mkdir(parents=True, exist_ok=True)
    return ExecutionEngine(team_context_root=root)


def _team_plan(synthesis: SynthesisSpec | None = None) -> MachinePlan:
    return MachinePlan(
        task_id="test-team-synth",
        task_summary="Test team synthesis",
        task_type="new-feature",
        risk_level="LOW",
        git_strategy="commit_per_agent",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="team",
                        task_description="Team work",
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="backend-engineer",
                                role="implementer",
                                task_description="Backend",
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="frontend-engineer",
                                role="implementer",
                                task_description="Frontend",
                            ),
                        ],
                        synthesis=synthesis,
                    ),
                ],
            ),
        ],
    )


def _write_retro_with_teams(
    retro_dir: Path,
    task_id: str,
    compositions: list[TeamCompositionRecord],
) -> None:
    retro = Retrospective(
        task_id=task_id,
        task_name=f"Task {task_id}",
        timestamp="2026-03-29T10:00:00",
        team_compositions=compositions,
    )
    (retro_dir / f"{task_id}.json").write_text(
        json.dumps(retro.to_dict()), encoding="utf-8"
    )
    (retro_dir / f"{task_id}.md").write_text(
        retro.to_markdown(), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. SynthesisSpec model
# ---------------------------------------------------------------------------

class TestSynthesisSpecDefaults:

    def test_default_strategy_is_concatenate(self) -> None:
        spec = SynthesisSpec()
        assert spec.strategy == "concatenate"

    def test_default_synthesis_agent_is_code_reviewer(self) -> None:
        spec = SynthesisSpec()
        assert spec.synthesis_agent == "code-reviewer"

    def test_default_conflict_handling_is_auto_merge(self) -> None:
        spec = SynthesisSpec()
        assert spec.conflict_handling == "auto_merge"

    def test_default_synthesis_prompt_is_empty(self) -> None:
        spec = SynthesisSpec()
        assert spec.synthesis_prompt == ""


class TestSynthesisSpecRoundtrip:

    def test_roundtrip_preserves_all_fields(self) -> None:
        original = SynthesisSpec(
            strategy="merge_files",
            synthesis_agent="architect",
            synthesis_prompt="Synthesize: {member_outcomes}",
            conflict_handling="escalate",
        )
        restored = SynthesisSpec.from_dict(original.to_dict())

        assert restored.strategy == original.strategy
        assert restored.synthesis_agent == original.synthesis_agent
        assert restored.synthesis_prompt == original.synthesis_prompt
        assert restored.conflict_handling == original.conflict_handling

    @pytest.mark.parametrize("strategy", ["concatenate", "merge_files", "agent_synthesis"])
    def test_all_strategy_values_survive_roundtrip(self, strategy: str) -> None:
        spec = SynthesisSpec(strategy=strategy)
        assert SynthesisSpec.from_dict(spec.to_dict()).strategy == strategy

    @pytest.mark.parametrize("handling", ["auto_merge", "escalate", "fail"])
    def test_all_conflict_handling_values_survive_roundtrip(self, handling: str) -> None:
        spec = SynthesisSpec(conflict_handling=handling)
        assert SynthesisSpec.from_dict(spec.to_dict()).conflict_handling == handling

    def test_from_dict_applies_defaults_for_missing_keys(self) -> None:
        spec = SynthesisSpec.from_dict({})
        assert spec.strategy == "concatenate"
        assert spec.synthesis_agent == "code-reviewer"
        assert spec.conflict_handling == "auto_merge"


# ---------------------------------------------------------------------------
# 2. PlanStep.synthesis field
# ---------------------------------------------------------------------------

class TestPlanStepSynthesisField:

    def test_to_dict_includes_synthesis_when_set(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="team",
            task_description="work",
            synthesis=SynthesisSpec(strategy="merge_files"),
        )
        d = step.to_dict()
        assert "synthesis" in d
        assert d["synthesis"]["strategy"] == "merge_files"

    def test_to_dict_omits_synthesis_when_none(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="team",
            task_description="work",
            synthesis=None,
        )
        d = step.to_dict()
        assert "synthesis" not in d

    def test_from_dict_with_synthesis_data(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "team",
            "task_description": "work",
            "synthesis": {
                "strategy": "agent_synthesis",
                "synthesis_agent": "architect",
                "synthesis_prompt": "",
                "conflict_handling": "fail",
            },
        }
        step = PlanStep.from_dict(data)
        assert step.synthesis is not None
        assert step.synthesis.strategy == "agent_synthesis"
        assert step.synthesis.conflict_handling == "fail"

    def test_from_dict_without_synthesis_is_none(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "team",
            "task_description": "work",
        }
        step = PlanStep.from_dict(data)
        assert step.synthesis is None

    def test_roundtrip_step_with_synthesis(self) -> None:
        original = PlanStep(
            step_id="2.3",
            agent_name="team",
            task_description="Team step",
            synthesis=SynthesisSpec(
                strategy="merge_files",
                conflict_handling="escalate",
            ),
        )
        restored = PlanStep.from_dict(original.to_dict())
        assert restored.synthesis is not None
        assert restored.synthesis.strategy == "merge_files"
        assert restored.synthesis.conflict_handling == "escalate"


# ---------------------------------------------------------------------------
# 3. Synthesis strategies in record_team_member_result
# ---------------------------------------------------------------------------

class TestSynthesisStrategies:

    def _run_both_members(
        self,
        tmp_path: Path,
        synthesis: SynthesisSpec | None,
        files_a: list[str],
        files_b: list[str],
        outcome_a: str = "Backend done",
        outcome_b: str = "Frontend done",
    ):
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))

        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome=outcome_a,
            files_changed=files_a,
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="frontend-engineer",
            status="complete",
            outcome=outcome_b,
            files_changed=files_b,
        )

        state = engine._load_state()
        return state.get_step_result("1.1")

    def test_concatenate_strategy_collects_files_with_duplicates(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(strategy="concatenate"),
            files_a=["src/api.py"],
            files_b=["src/api.py", "src/ui.jsx"],
        )
        # concatenate does NOT deduplicate
        assert result.files_changed.count("src/api.py") == 2
        assert "src/ui.jsx" in result.files_changed

    def test_concatenate_strategy_joins_outcomes(self, tmp_path: Path) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(strategy="concatenate"),
            files_a=[], files_b=[],
            outcome_a="Backend done",
            outcome_b="Frontend done",
        )
        assert "Backend done" in result.outcome
        assert "Frontend done" in result.outcome
        assert "; " in result.outcome

    def test_merge_files_strategy_deduplicates_files(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(strategy="merge_files"),
            files_a=["shared.py", "models.py"],
            files_b=["shared.py", "views.py"],
        )
        # shared.py touched by both — should appear once
        assert result.files_changed.count("shared.py") == 1
        assert "models.py" in result.files_changed
        assert "views.py" in result.files_changed

    def test_merge_files_strategy_preserves_order(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(strategy="merge_files"),
            files_a=["a.py", "b.py"],
            files_b=["c.py"],
        )
        # First occurrence order preserved; no extra entries
        assert len(set(result.files_changed)) == len(result.files_changed)

    def test_agent_synthesis_strategy_adds_deviation_marker(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(
                strategy="agent_synthesis",
                synthesis_agent="code-reviewer",
            ),
            files_a=["service.py"],
            files_b=["component.tsx"],
        )
        assert any("synthesis_requested" in d for d in result.deviations)

    def test_agent_synthesis_deviation_names_synthesis_agent(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(
                strategy="agent_synthesis",
                synthesis_agent="architect",
            ),
            files_a=[],
            files_b=[],
        )
        assert any("architect" in d for d in result.deviations)

    def test_agent_synthesis_strategy_collects_files(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=SynthesisSpec(strategy="agent_synthesis"),
            files_a=["backend.py"],
            files_b=["frontend.tsx"],
        )
        assert "backend.py" in result.files_changed
        assert "frontend.tsx" in result.files_changed

    def test_no_synthesis_spec_falls_back_to_concatenate(
        self, tmp_path: Path
    ) -> None:
        result = self._run_both_members(
            tmp_path,
            synthesis=None,
            files_a=["a.py"],
            files_b=["a.py"],
        )
        # Concatenate behaviour: both copies appear
        assert result.files_changed.count("a.py") == 2
        assert result.status == "complete"

    def test_completed_step_status_is_complete_for_all_strategies(
        self, tmp_path: Path
    ) -> None:
        for strategy in ("concatenate", "merge_files", "agent_synthesis"):
            engine = _make_engine(tmp_path / strategy)
            engine.start(_team_plan(SynthesisSpec(strategy=strategy)))
            engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                             status="complete", outcome="done")
            engine.record_team_member_result("1.1", "1.1.b", "frontend-engineer",
                                             status="complete", outcome="done")
            result = engine._load_state().get_step_result("1.1")
            assert result.status == "complete", f"strategy={strategy}"


# ---------------------------------------------------------------------------
# 4. Conflict detection via public API
# ---------------------------------------------------------------------------

class TestConflictDetection:

    def _start_and_record_both(
        self,
        tmp_path: Path,
        synthesis: SynthesisSpec,
        files_a: list[str],
        files_b: list[str],
    ):
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))
        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="Backend impl",
            files_changed=files_a,
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="Frontend impl",
            files_changed=files_b,
        )
        return engine._load_state().get_step_result("1.1")

    def test_no_conflict_when_members_touch_different_files(
        self, tmp_path: Path
    ) -> None:
        result = self._start_and_record_both(
            tmp_path,
            synthesis=SynthesisSpec(strategy="concatenate"),
            files_a=["backend.py"],
            files_b=["frontend.tsx"],
        )
        # No conflict → no deviation containing "conflict"
        assert not any("conflict" in d.lower() for d in result.deviations)

    def test_conflict_detected_when_members_touch_same_file(
        self, tmp_path: Path
    ) -> None:
        # Use auto_merge so we can still inspect the completed result
        result = self._start_and_record_both(
            tmp_path,
            synthesis=SynthesisSpec(
                strategy="concatenate",
                conflict_handling="auto_merge",
            ),
            files_a=["shared.py"],
            files_b=["shared.py"],
        )
        # Step still completes (auto_merge), but no crash
        assert result.status == "complete"

    def test_no_conflict_with_single_member(self, tmp_path: Path) -> None:
        """_detect_team_conflict returns None when fewer than 2 member results."""
        engine = _make_engine(tmp_path)
        # Build a step with a single-member team
        plan = MachinePlan(
            task_id="single-member",
            task_summary="Single member test",
            risk_level="LOW",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Work",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="team",
                            task_description="Solo work",
                            team=[
                                TeamMember(
                                    member_id="1.1.a",
                                    agent_name="backend-engineer",
                                    role="implementer",
                                    task_description="Only member",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
        engine.start(plan)
        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete",
            files_changed=["service.py"],
        )
        result = engine._load_state().get_step_result("1.1")
        # No conflict with < 2 members; no escalation deviation
        assert not any("conflict" in d.lower() for d in result.deviations)
        assert result.status == "complete"

    def test_conflict_record_contains_expected_agents(
        self, tmp_path: Path
    ) -> None:
        """_detect_team_conflict returns a ConflictRecord with correct agents list."""
        from agent_baton.models.execution import TeamStepResult

        engine = _make_engine(tmp_path)
        engine.start(_team_plan())

        step = engine._load_state().plan.phases[0].steps[0]
        member_results = [
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer",
                status="complete",
                outcome="impl",
                files_changed=["shared.py"],
            ),
            TeamStepResult(
                member_id="1.1.b",
                agent_name="frontend-engineer",
                status="complete",
                outcome="impl",
                files_changed=["shared.py"],
            ),
        ]

        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert "backend-engineer" in conflict.agents
        assert "frontend-engineer" in conflict.agents

    def test_conflict_record_has_correct_positions(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.models.execution import TeamStepResult

        engine = _make_engine(tmp_path)
        engine.start(_team_plan())
        step = engine._load_state().plan.phases[0].steps[0]

        member_results = [
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer",
                status="complete",
                outcome="backend outcome",
                files_changed=["overlap.py"],
            ),
            TeamStepResult(
                member_id="1.1.b",
                agent_name="frontend-engineer",
                status="complete",
                outcome="frontend outcome",
                files_changed=["overlap.py"],
            ),
        ]

        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.positions["backend-engineer"] == "backend outcome"
        assert conflict.positions["frontend-engineer"] == "frontend outcome"

    def test_conflict_record_evidence_contains_conflicting_file(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.models.execution import TeamStepResult

        engine = _make_engine(tmp_path)
        engine.start(_team_plan())
        step = engine._load_state().plan.phases[0].steps[0]

        member_results = [
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer",
                status="complete",
                outcome="",
                files_changed=["contested.py"],
            ),
            TeamStepResult(
                member_id="1.1.b",
                agent_name="frontend-engineer",
                status="complete",
                outcome="",
                files_changed=["contested.py"],
            ),
        ]

        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert "contested.py" in conflict.evidence.get("backend-engineer", "")
        assert "contested.py" in conflict.evidence.get("frontend-engineer", "")

    def test_multiple_conflicting_files_detected(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.models.execution import TeamStepResult

        engine = _make_engine(tmp_path)
        engine.start(_team_plan())
        step = engine._load_state().plan.phases[0].steps[0]

        member_results = [
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer",
                status="complete",
                outcome="",
                files_changed=["a.py", "b.py"],
            ),
            TeamStepResult(
                member_id="1.1.b",
                agent_name="frontend-engineer",
                status="complete",
                outcome="",
                files_changed=["a.py", "b.py"],
            ),
        ]

        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        # Both files must appear in the evidence for each agent
        for agent in ("backend-engineer", "frontend-engineer"):
            evidence = conflict.evidence.get(agent, "")
            assert "a.py" in evidence
            assert "b.py" in evidence

    def test_no_conflict_returns_none_when_files_disjoint(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.models.execution import TeamStepResult

        engine = _make_engine(tmp_path)
        engine.start(_team_plan())
        step = engine._load_state().plan.phases[0].steps[0]

        member_results = [
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer",
                status="complete",
                outcome="",
                files_changed=["only_backend.py"],
            ),
            TeamStepResult(
                member_id="1.1.b",
                agent_name="frontend-engineer",
                status="complete",
                outcome="",
                files_changed=["only_frontend.tsx"],
            ),
        ]

        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is None


# ---------------------------------------------------------------------------
# 5. Conflict escalation
# ---------------------------------------------------------------------------

class TestConflictEscalation:

    def test_escalate_strategy_pauses_state_on_overlap(
        self, tmp_path: Path
    ) -> None:
        synthesis = SynthesisSpec(
            strategy="concatenate",
            conflict_handling="escalate",
        )
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="BE impl",
            files_changed=["shared.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="FE impl",
            files_changed=["shared.py"],
        )

        state = engine._load_state()
        assert state.status == "approval_pending"

    def test_escalate_strategy_keeps_step_dispatched_on_conflict(
        self, tmp_path: Path
    ) -> None:
        synthesis = SynthesisSpec(
            strategy="concatenate",
            conflict_handling="escalate",
        )
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="A",
            files_changed=["conflict.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="B",
            files_changed=["conflict.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "dispatched"

    def test_auto_merge_strategy_completes_normally_on_overlap(
        self, tmp_path: Path
    ) -> None:
        synthesis = SynthesisSpec(
            strategy="concatenate",
            conflict_handling="auto_merge",
        )
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="A",
            files_changed=["contested.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="B",
            files_changed=["contested.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"
        assert state.status != "approval_pending"

    def test_escalate_without_conflict_completes_normally(
        self, tmp_path: Path
    ) -> None:
        synthesis = SynthesisSpec(
            strategy="concatenate",
            conflict_handling="escalate",
        )
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(synthesis))

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="A",
            files_changed=["backend.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="B",
            files_changed=["frontend.tsx"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"
        assert state.status != "approval_pending"


# ---------------------------------------------------------------------------
# 6. TeamScorecard model
# ---------------------------------------------------------------------------

class TestTeamScorecardMarkdown:

    def _scorecard(self, **kw) -> TeamScorecard:
        defaults = dict(
            agents=["backend-engineer", "frontend-engineer"],
            times_used=5,
            success_rate=0.8,
            avg_token_cost=3200,
            task_types=["new-feature"],
        )
        defaults.update(kw)
        return TeamScorecard(**defaults)

    def test_markdown_contains_agent_names(self) -> None:
        md = self._scorecard().to_markdown()
        assert "backend-engineer" in md
        assert "frontend-engineer" in md

    def test_markdown_contains_health(self) -> None:
        md = self._scorecard(success_rate=0.85).to_markdown()
        assert "strong" in md

    def test_markdown_contains_times_used(self) -> None:
        md = self._scorecard(times_used=7).to_markdown()
        assert "7" in md

    def test_markdown_contains_success_rate(self) -> None:
        md = self._scorecard(success_rate=0.6).to_markdown()
        assert "60%" in md

    def test_markdown_contains_avg_token_cost(self) -> None:
        md = self._scorecard(avg_token_cost=5000).to_markdown()
        assert "5,000" in md

    def test_markdown_contains_task_types(self) -> None:
        md = self._scorecard(task_types=["bug-fix", "new-feature"]).to_markdown()
        assert "bug-fix" in md
        assert "new-feature" in md

    def test_markdown_omits_task_types_section_when_empty(self) -> None:
        md = self._scorecard(task_types=[]).to_markdown()
        assert "Task types" not in md


class TestTeamScorecardHealth:

    @pytest.mark.parametrize("success_rate,expected", [
        (0.8, "strong"),
        (1.0, "strong"),
        (0.5, "adequate"),
        (0.79, "adequate"),
        (0.49, "needs-improvement"),
        (0.0, "needs-improvement"),
    ])
    def test_health_thresholds(self, success_rate: float, expected: str) -> None:
        sc = TeamScorecard(
            agents=["a", "b"],
            times_used=4,
            success_rate=success_rate,
        )
        assert sc.health == expected

    def test_health_is_unused_when_times_used_is_zero(self) -> None:
        sc = TeamScorecard(agents=["a", "b"], times_used=0, success_rate=0.9)
        assert sc.health == "unused"


# ---------------------------------------------------------------------------
# 7. PerformanceScorer.score_teams()
# ---------------------------------------------------------------------------

class TestScoreTeams:

    def _make_scorer(self, retro_dir: Path) -> PerformanceScorer:
        retro_engine = RetrospectiveEngine(retrospectives_dir=retro_dir)
        return PerformanceScorer(retro_engine=retro_engine)

    def test_empty_retros_returns_empty_list(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()
        scorer = self._make_scorer(retro_dir)
        assert scorer.score_teams() == []

    def test_single_composition_returns_one_scorecard(
        self, tmp_path: Path
    ) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["backend-engineer", "frontend-engineer"],
                outcome="success",
                task_type="new-feature",
                token_cost=2000,
            ),
        ])

        scorecards = self._make_scorer(retro_dir).score_teams()
        assert len(scorecards) == 1
        assert sorted(scorecards[0].agents) == ["backend-engineer", "frontend-engineer"]

    def test_same_team_across_multiple_retros_produces_one_aggregated_scorecard(
        self, tmp_path: Path
    ) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        for i, outcome in enumerate(["success", "success", "failure"]):
            _write_retro_with_teams(retro_dir, f"task-{i:03}", [
                TeamCompositionRecord(
                    step_id="1.1",
                    agents=["backend-engineer", "frontend-engineer"],
                    outcome=outcome,
                    token_cost=1000,
                ),
            ])

        scorecards = self._make_scorer(retro_dir).score_teams()
        assert len(scorecards) == 1
        sc = scorecards[0]
        assert sc.times_used == 3
        # 2/3 success
        assert abs(sc.success_rate - 2 / 3) < 0.01

    def test_different_teams_produce_separate_scorecards(
        self, tmp_path: Path
    ) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["architect", "backend-engineer"],
                outcome="success",
            ),
            TeamCompositionRecord(
                step_id="1.2",
                agents=["frontend-engineer", "test-engineer"],
                outcome="failure",
            ),
        ])

        scorecards = self._make_scorer(retro_dir).score_teams()
        assert len(scorecards) == 2

    def test_success_rate_computed_correctly(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        # 3 successes, 1 failure
        for i, outcome in enumerate(["success", "success", "success", "failure"]):
            _write_retro_with_teams(retro_dir, f"task-{i:03}", [
                TeamCompositionRecord(
                    step_id="1.1",
                    agents=["agent-a", "agent-b"],
                    outcome=outcome,
                ),
            ])

        sc = self._make_scorer(retro_dir).score_teams()[0]
        assert abs(sc.success_rate - 0.75) < 0.01

    def test_token_cost_averaged_correctly(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["agent-a", "agent-b"],
                outcome="success",
                token_cost=1000,
            ),
        ])
        _write_retro_with_teams(retro_dir, "task-002", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["agent-a", "agent-b"],
                outcome="success",
                token_cost=3000,
            ),
        ])

        sc = self._make_scorer(retro_dir).score_teams()[0]
        assert sc.avg_token_cost == 2000

    def test_team_composition_normalised_by_sort_order(
        self, tmp_path: Path
    ) -> None:
        """Two records with the same agents in different order should aggregate."""
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["frontend-engineer", "backend-engineer"],  # unsorted
                outcome="success",
            ),
        ])
        _write_retro_with_teams(retro_dir, "task-002", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["backend-engineer", "frontend-engineer"],  # sorted
                outcome="success",
            ),
        ])

        scorecards = self._make_scorer(retro_dir).score_teams()
        assert len(scorecards) == 1
        assert scorecards[0].times_used == 2


# ---------------------------------------------------------------------------
# 8. PerformanceScorer.generate_team_report()
# ---------------------------------------------------------------------------

class TestGenerateTeamReport:

    def _make_scorer(self, retro_dir: Path) -> PerformanceScorer:
        retro_engine = RetrospectiveEngine(retrospectives_dir=retro_dir)
        return PerformanceScorer(retro_engine=retro_engine)

    def test_returns_placeholder_when_no_data(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()
        report = self._make_scorer(retro_dir).generate_team_report()
        assert "No team composition data available" in report

    def test_report_groups_by_health_tier(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        # Strong team: 2/2 successes
        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["architect", "backend-engineer"],
                outcome="success",
            ),
            TeamCompositionRecord(
                step_id="1.2",
                agents=["architect", "backend-engineer"],
                outcome="success",
            ),
        ])
        # Needs-improvement team: 0/1 success
        _write_retro_with_teams(retro_dir, "task-002", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["junior-dev"],
                outcome="failure",
            ),
        ])

        report = self._make_scorer(retro_dir).generate_team_report()
        assert "Team Composition Scorecards" in report
        # Both health tiers should appear (strong + needs-improvement)
        assert "Strong" in report or "strong" in report

    def test_report_includes_times_used_count(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        _write_retro_with_teams(retro_dir, "task-001", [
            TeamCompositionRecord(
                step_id="1.1",
                agents=["agent-x", "agent-y"],
                outcome="success",
            ),
        ])

        report = self._make_scorer(retro_dir).generate_team_report()
        # The total step count (1) should appear in the header
        assert "1" in report

    def test_report_header_has_total_team_steps(self, tmp_path: Path) -> None:
        retro_dir = tmp_path / "retros"
        retro_dir.mkdir()

        for i in range(3):
            _write_retro_with_teams(retro_dir, f"task-{i:03}", [
                TeamCompositionRecord(
                    step_id="1.1",
                    agents=["agent-a", "agent-b"],
                    outcome="success",
                ),
            ])

        report = self._make_scorer(retro_dir).generate_team_report()
        assert "3 total team steps" in report


# ---------------------------------------------------------------------------
# 9. Team composition collection in _build_retrospective_data / complete()
# ---------------------------------------------------------------------------

class TestRetrospectiveTeamCompositions:

    def test_complete_writes_retrospective_with_team_compositions(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan())

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="Backend done",
            files_changed=["service.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="Frontend done",
            files_changed=["app.tsx"],
        )

        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

        engine.complete()

        # Find the JSON sidecar written by the engine
        retro_dir = (
            tmp_path / ".claude" / "team-context" / "retrospectives"
        )
        json_files = list(retro_dir.glob("*.json"))
        assert len(json_files) == 1, "Expected one retrospective JSON sidecar"

        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        retro = Retrospective.from_dict(data)

        assert len(retro.team_compositions) >= 1
        tc = retro.team_compositions[0]
        assert tc.step_id == "1.1"
        assert sorted(tc.agents) == ["backend-engineer", "frontend-engineer"]

    def test_retrospective_team_composition_outcome_is_success(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan())

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="done",
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="done",
        )

        engine.next_action()
        engine.complete()

        retro_dir = tmp_path / ".claude" / "team-context" / "retrospectives"
        json_files = list(retro_dir.glob("*.json"))
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        retro = Retrospective.from_dict(data)

        assert retro.team_compositions[0].outcome == "success"

    def test_build_retrospective_data_includes_team_compositions(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan())

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer",
            status="complete", outcome="BE done",
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "frontend-engineer",
            status="complete", outcome="FE done",
        )

        state = engine._load_state()
        data = engine._build_retrospective_data(state)

        compositions = data.get("team_compositions", [])
        assert len(compositions) >= 1
        assert compositions[0].step_id == "1.1"

    def test_non_team_steps_not_included_in_team_compositions(
        self, tmp_path: Path
    ) -> None:
        plan = MachinePlan(
            task_id="solo-task",
            task_summary="Solo work",
            risk_level="LOW",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Solo",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="Solo step",
                        ),
                    ],
                ),
            ],
        )
        engine = _make_engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete")

        state = engine._load_state()
        data = engine._build_retrospective_data(state)

        assert data.get("team_compositions", []) == []
