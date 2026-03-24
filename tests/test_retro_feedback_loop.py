"""Tests for the closed-loop retrospective feedback integration.

Covers:
- Retrospective.to_dict / from_dict round-trip
- RetrospectiveFeedback model helpers
- RetrospectiveEngine.save JSON sidecar
- RetrospectiveEngine.load_recent_feedback (JSON path and markdown fallback)
- IntelligentPlanner._apply_retro_feedback
- IntelligentPlanner.create_plan with retro_engine (drop / prefer / gaps)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.models.feedback import RetrospectiveFeedback
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_retro(
    task_id: str = "t1",
    roster: list[RosterRecommendation] | None = None,
    gaps: list[KnowledgeGap] | None = None,
    notes: list[SequencingNote] | None = None,
) -> Retrospective:
    return Retrospective(
        task_id=task_id,
        task_name=f"Task {task_id}",
        timestamp="2026-01-01T00:00:00",
        roster_recommendations=roster or [],
        knowledge_gaps=gaps or [],
        sequencing_notes=notes or [],
    )


def _make_planner(tmp_path: Path, retro_engine: RetrospectiveEngine | None = None) -> IntelligentPlanner:
    ctx = tmp_path / "team-context"
    ctx.mkdir(exist_ok=True)
    p = IntelligentPlanner(team_context_root=ctx, retro_engine=retro_engine)

    # Provide a minimal agent registry so routing doesn't fail
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in (
        "backend-engineer", "architect", "test-engineer", "code-reviewer",
        "backend-engineer--python", "data-analyst",
    ):
        content = (
            f"---\nname: {name}\ndescription: {name} specialist.\n"
            f"model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")

    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


# ---------------------------------------------------------------------------
# Retrospective serialisation round-trip
# ---------------------------------------------------------------------------

class TestRetrospectiveSerialisationRoundTrip:
    def test_to_dict_from_dict_scalars(self):
        retro = _make_retro("my-task")
        d = retro.to_dict()
        restored = Retrospective.from_dict(d)
        assert restored.task_id == "my-task"
        assert restored.task_name == "Task my-task"
        assert restored.timestamp == "2026-01-01T00:00:00"

    def test_to_dict_from_dict_roster_recommendations(self):
        rec = RosterRecommendation(action="create", target="oauth2-specialist", reason="Needed")
        retro = _make_retro("t1", roster=[rec])
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.roster_recommendations) == 1
        r = restored.roster_recommendations[0]
        assert r.action == "create"
        assert r.target == "oauth2-specialist"
        assert r.reason == "Needed"

    def test_to_dict_from_dict_knowledge_gaps(self):
        gap = KnowledgeGap(description="Missing Redis docs", affected_agent="backend", suggested_fix="create pack")
        retro = _make_retro("t1", gaps=[gap])
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.knowledge_gaps) == 1
        g = restored.knowledge_gaps[0]
        assert g.description == "Missing Redis docs"
        assert g.affected_agent == "backend"
        assert g.suggested_fix == "create pack"

    def test_to_dict_from_dict_sequencing_notes(self):
        note = SequencingNote(phase="2", observation="Gate redundant", keep=False)
        retro = _make_retro("t1", notes=[note])
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.sequencing_notes) == 1
        n = restored.sequencing_notes[0]
        assert n.phase == "2"
        assert n.keep is False

    def test_to_dict_from_dict_empty_lists(self):
        retro = _make_retro("empty")
        restored = Retrospective.from_dict(retro.to_dict())
        assert restored.roster_recommendations == []
        assert restored.knowledge_gaps == []
        assert restored.sequencing_notes == []

    def test_from_dict_missing_optional_fields_uses_defaults(self):
        minimal = {"task_id": "t99"}
        retro = Retrospective.from_dict(minimal)
        assert retro.task_id == "t99"
        assert retro.task_name == "t99"
        assert retro.risk_level == "LOW"
        assert retro.roster_recommendations == []


# ---------------------------------------------------------------------------
# Nested model serialisation
# ---------------------------------------------------------------------------

class TestNestedModelSerialisation:
    def test_agent_outcome_round_trip(self):
        o = AgentOutcome(name="arch", worked_well="Great", issues="None", root_cause="")
        assert AgentOutcome.from_dict(o.to_dict()) == o

    def test_knowledge_gap_round_trip(self):
        g = KnowledgeGap(description="Redis gap", affected_agent="be", suggested_fix="pack")
        assert KnowledgeGap.from_dict(g.to_dict()) == g

    def test_roster_recommendation_round_trip(self):
        r = RosterRecommendation(action="remove", target="old-agent", reason="Deprecated")
        assert RosterRecommendation.from_dict(r.to_dict()) == r

    def test_sequencing_note_round_trip(self):
        n = SequencingNote(phase="3", observation="Good gate", keep=True)
        assert SequencingNote.from_dict(n.to_dict()) == n


# ---------------------------------------------------------------------------
# RetrospectiveFeedback model
# ---------------------------------------------------------------------------

class TestRetrospectiveFeedback:
    def test_agents_to_drop_filters_remove_and_drop(self):
        fb = RetrospectiveFeedback(
            roster_recommendations=[
                RosterRecommendation(action="remove", target="agent-a"),
                RosterRecommendation(action="drop", target="agent-b"),
                RosterRecommendation(action="create", target="agent-c"),
            ]
        )
        assert fb.agents_to_drop() == {"agent-a", "agent-b"}

    def test_agents_to_prefer_includes_prefer_improve_create(self):
        fb = RetrospectiveFeedback(
            roster_recommendations=[
                RosterRecommendation(action="prefer", target="agent-x"),
                RosterRecommendation(action="improve", target="agent-y"),
                RosterRecommendation(action="create", target="agent-z"),
                RosterRecommendation(action="remove", target="agent-bad"),
            ]
        )
        assert fb.agents_to_prefer() == {"agent-x", "agent-y", "agent-z"}

    def test_has_feedback_true_when_any_data(self):
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="create", target="x")]
        )
        assert fb.has_feedback() is True

    def test_has_feedback_false_when_empty(self):
        fb = RetrospectiveFeedback()
        assert fb.has_feedback() is False

    def test_default_source_count_is_zero(self):
        fb = RetrospectiveFeedback()
        assert fb.source_count == 0


# ---------------------------------------------------------------------------
# RetrospectiveEngine JSON sidecar
# ---------------------------------------------------------------------------

class TestRetrospectiveEngineJsonSidecar:
    def test_save_creates_json_sidecar(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = _make_retro("sidecar-test", roster=[
            RosterRecommendation(action="create", target="redis-expert", reason="Caching")
        ])
        engine.save(retro)
        json_path = tmp_path / "retros" / "sidecar-test.json"
        assert json_path.exists()

    def test_json_sidecar_is_valid_json(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_make_retro("valid-json"))
        raw = (tmp_path / "retros" / "valid-json.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["task_id"] == "valid-json"

    def test_json_sidecar_contains_structured_data(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        retro = _make_retro(
            "data-test",
            roster=[RosterRecommendation(action="remove", target="old-agent")],
            gaps=[KnowledgeGap(description="Missing OAuth docs")],
        )
        engine.save(retro)
        raw = json.loads(
            (tmp_path / "retros" / "data-test.json").read_text(encoding="utf-8")
        )
        assert len(raw["roster_recommendations"]) == 1
        assert raw["roster_recommendations"][0]["target"] == "old-agent"
        assert len(raw["knowledge_gaps"]) == 1

    def test_save_still_creates_markdown_alongside_json(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        path = engine.save(_make_retro("dual-save"))
        assert path.suffix == ".md"
        assert path.exists()
        assert (tmp_path / "retros" / "dual-save.json").exists()

    def test_list_json_files_returns_sorted_paths(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for tid in ("a", "b", "c"):
            engine.save(_make_retro(tid))
        paths = engine.list_json_files()
        names = [p.stem for p in paths]
        assert names == sorted(names)

    def test_list_json_files_empty_when_dir_missing(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "no-such")
        assert engine.list_json_files() == []


# ---------------------------------------------------------------------------
# RetrospectiveEngine.load_recent_feedback — JSON path
# ---------------------------------------------------------------------------

class TestLoadRecentFeedbackJsonPath:
    def test_returns_empty_feedback_when_no_files(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        fb = engine.load_recent_feedback()
        assert fb.has_feedback() is False
        assert fb.source_count == 0

    def test_loads_roster_recommendations_from_json(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_make_retro("r1", roster=[
            RosterRecommendation(action="create", target="oauth2-specialist")
        ]))
        fb = engine.load_recent_feedback()
        assert fb.source_count == 1
        assert len(fb.roster_recommendations) == 1
        assert fb.roster_recommendations[0].target == "oauth2-specialist"

    def test_loads_knowledge_gaps_from_json(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_make_retro("r1", gaps=[
            KnowledgeGap(description="OAuth gap", suggested_fix="write pack")
        ]))
        fb = engine.load_recent_feedback()
        assert len(fb.knowledge_gaps) == 1
        assert fb.knowledge_gaps[0].description == "OAuth gap"

    def test_loads_sequencing_notes_from_json(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_make_retro("r1", notes=[
            SequencingNote(phase="2", observation="Gate was redundant", keep=False)
        ]))
        fb = engine.load_recent_feedback()
        assert len(fb.sequencing_notes) == 1
        assert fb.sequencing_notes[0].keep is False

    def test_aggregates_across_multiple_files(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        engine.save(_make_retro("r1", roster=[RosterRecommendation(action="create", target="a")]))
        engine.save(_make_retro("r2", roster=[RosterRecommendation(action="remove", target="b")]))
        fb = engine.load_recent_feedback()
        assert fb.source_count == 2
        assert len(fb.roster_recommendations) == 2

    def test_limit_respected(self, tmp_path: Path):
        engine = RetrospectiveEngine(tmp_path / "retros")
        for i in range(10):
            engine.save(_make_retro(
                f"task-{i:02d}",
                roster=[RosterRecommendation(action="create", target=f"agent-{i}")]
            ))
        fb = engine.load_recent_feedback(limit=3)
        assert fb.source_count == 3
        assert len(fb.roster_recommendations) == 3

    def test_skips_corrupt_json_files(self, tmp_path: Path):
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        (retros_dir / "bad.json").write_text("{not valid json", encoding="utf-8")
        engine = RetrospectiveEngine(retros_dir)
        # Should not raise; bad file is silently skipped
        fb = engine.load_recent_feedback()
        assert fb.source_count == 0

    def test_skips_json_missing_task_id(self, tmp_path: Path):
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        (retros_dir / "incomplete.json").write_text(
            json.dumps({"task_name": "oops"}), encoding="utf-8"
        )
        engine = RetrospectiveEngine(retros_dir)
        fb = engine.load_recent_feedback()
        assert fb.source_count == 0


# ---------------------------------------------------------------------------
# RetrospectiveEngine.load_recent_feedback — markdown fallback
# ---------------------------------------------------------------------------

class TestLoadRecentFeedbackMarkdownFallback:
    def test_falls_back_to_markdown_when_no_json_files(self, tmp_path: Path):
        """When the retros dir has .md but no .json sidecars, the legacy
        markdown parser kicks in and returns recommendations."""
        retros_dir = tmp_path / "retros"
        retros_dir.mkdir()
        # Write a hand-crafted markdown retro without a JSON sidecar
        (retros_dir / "legacy.md").write_text(
            "# Retrospective: Legacy\n\n"
            "## Roster Recommendations\n"
            "- **Create:** oauth2-specialist\n",
            encoding="utf-8",
        )
        engine = RetrospectiveEngine(retros_dir)
        fb = engine.load_recent_feedback()
        assert len(fb.roster_recommendations) == 1
        assert fb.roster_recommendations[0].target == "oauth2-specialist"


# ---------------------------------------------------------------------------
# IntelligentPlanner._apply_retro_feedback
# ---------------------------------------------------------------------------

class TestApplyRetroFeedback:
    def test_drops_agent_in_drop_list(self, tmp_path: Path):
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="remove", target="code-reviewer")]
        )
        agents = ["architect", "backend-engineer", "code-reviewer"]
        result = planner._apply_retro_feedback(agents, fb)
        assert "code-reviewer" not in result
        assert "architect" in result

    def test_drop_does_not_remove_all_agents(self, tmp_path: Path):
        """If dropping would empty the list, keep the original."""
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[
                RosterRecommendation(action="drop", target="backend-engineer")
            ]
        )
        agents = ["backend-engineer"]
        result = planner._apply_retro_feedback(agents, fb)
        # Should keep the original rather than returning an empty list
        assert result == ["backend-engineer"]

    def test_drop_records_routing_note(self, tmp_path: Path):
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="remove", target="test-engineer")]
        )
        planner._apply_retro_feedback(["architect", "test-engineer"], fb)
        assert any("test-engineer" in note and "removed" in note
                   for note in planner._last_routing_notes)

    def test_prefer_records_routing_note(self, tmp_path: Path):
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="prefer", target="oauth2-specialist")]
        )
        planner._apply_retro_feedback(["architect"], fb)
        assert any("oauth2-specialist" in note for note in planner._last_routing_notes)

    def test_prefer_does_not_auto_add_agent(self, tmp_path: Path):
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="prefer", target="new-agent")]
        )
        result = planner._apply_retro_feedback(["architect", "backend-engineer"], fb)
        assert "new-agent" not in result

    def test_no_change_when_feedback_empty(self, tmp_path: Path):
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback()
        agents = ["architect", "backend-engineer"]
        result = planner._apply_retro_feedback(agents, fb)
        assert result == agents

    def test_flavored_agent_name_dropped_by_base_name(self, tmp_path: Path):
        """Drop 'code-reviewer' should also remove 'code-reviewer--strict'."""
        planner = _make_planner(tmp_path)
        fb = RetrospectiveFeedback(
            roster_recommendations=[RosterRecommendation(action="drop", target="code-reviewer")]
        )
        agents = ["architect", "code-reviewer--strict", "backend-engineer"]
        result = planner._apply_retro_feedback(agents, fb)
        assert "code-reviewer--strict" not in result


# ---------------------------------------------------------------------------
# IntelligentPlanner.create_plan with retro_engine
# ---------------------------------------------------------------------------

class TestCreatePlanWithRetroEngine:
    def test_create_plan_without_retro_engine_still_works(self, tmp_path: Path):
        planner = _make_planner(tmp_path, retro_engine=None)
        plan = planner.create_plan("Add OAuth2 login")
        assert plan.task_summary == "Add OAuth2 login"

    def test_create_plan_with_retro_engine_no_feedback(self, tmp_path: Path):
        retro_dir = tmp_path / "retros"
        engine = RetrospectiveEngine(retro_dir)
        planner = _make_planner(tmp_path, retro_engine=engine)
        plan = planner.create_plan("Build a feature")
        # Empty retro store → plan should still succeed
        assert plan is not None

    def test_dropped_agent_not_in_plan(self, tmp_path: Path):
        """When a retro recommends dropping 'code-reviewer', it should not
        appear in the generated plan even when default selection would include it."""
        retro_dir = tmp_path / "retros"
        engine = RetrospectiveEngine(retro_dir)
        engine.save(_make_retro("prev", roster=[
            RosterRecommendation(action="remove", target="code-reviewer")
        ]))

        planner = _make_planner(tmp_path, retro_engine=engine)
        plan = planner.create_plan(
            "Refactor user module",
            task_type="refactor",
            agents=["architect", "backend-engineer", "code-reviewer"],
        )
        all_agents = plan.all_agents
        assert not any("code-reviewer" in a for a in all_agents)

    def test_knowledge_gaps_appear_in_shared_context(self, tmp_path: Path):
        retro_dir = tmp_path / "retros"
        engine = RetrospectiveEngine(retro_dir)
        engine.save(_make_retro("prev", gaps=[
            KnowledgeGap(description="No Redis knowledge", suggested_fix="create pack")
        ]))

        planner = _make_planner(tmp_path, retro_engine=engine)
        plan = planner.create_plan("Add caching layer")
        assert "No Redis knowledge" in plan.shared_context
        assert "create pack" in plan.shared_context

    def test_retro_engine_exception_does_not_crash_planner(self, tmp_path: Path):
        """If the retro engine raises, create_plan should degrade gracefully."""
        bad_engine = MagicMock()
        bad_engine.load_recent_feedback.side_effect = RuntimeError("disk error")
        planner = _make_planner(tmp_path, retro_engine=bad_engine)
        # Should not raise
        plan = planner.create_plan("Add feature")
        assert plan is not None
        assert planner._last_retro_feedback is None

    def test_retro_feedback_resets_between_create_plan_calls(self, tmp_path: Path):
        retro_dir = tmp_path / "retros"
        engine = RetrospectiveEngine(retro_dir)
        engine.save(_make_retro("prev", gaps=[
            KnowledgeGap(description="Gap exists")
        ]))
        planner = _make_planner(tmp_path, retro_engine=engine)
        planner.create_plan("First task")
        assert planner._last_retro_feedback is not None

        # Clear the retro dir and call again — feedback should reflect empty state
        import shutil
        shutil.rmtree(str(retro_dir))
        planner.create_plan("Second task")
        assert planner._last_retro_feedback is not None
        assert not planner._last_retro_feedback.has_feedback()

    def test_no_knowledge_gaps_not_in_shared_context(self, tmp_path: Path):
        """When there are no gaps, shared_context should not contain the header."""
        retro_dir = tmp_path / "retros"
        engine = RetrospectiveEngine(retro_dir)
        engine.save(_make_retro("prev"))  # no gaps

        planner = _make_planner(tmp_path, retro_engine=engine)
        plan = planner.create_plan("Add feature")
        assert "Knowledge Gaps" not in plan.shared_context


# ---------------------------------------------------------------------------
# Retrospective model — individual nested type from_dict coverage
# ---------------------------------------------------------------------------

class TestNestedFromDictDefaults:
    def test_agent_outcome_from_dict_missing_fields(self):
        o = AgentOutcome.from_dict({"name": "arch"})
        assert o.worked_well == ""
        assert o.issues == ""
        assert o.root_cause == ""

    def test_knowledge_gap_from_dict_missing_fields(self):
        g = KnowledgeGap.from_dict({"description": "A gap"})
        assert g.affected_agent == ""
        assert g.suggested_fix == ""

    def test_roster_recommendation_from_dict_missing_reason(self):
        r = RosterRecommendation.from_dict({"action": "create", "target": "agent-x"})
        assert r.reason == ""

    def test_sequencing_note_from_dict_default_keep_true(self):
        n = SequencingNote.from_dict({"phase": "1", "observation": "Good"})
        assert n.keep is True
