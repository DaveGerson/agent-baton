"""Unit tests for agent_baton.core.engine._planner_helpers.

Each helper gets at minimum: happy path, edge case, and relevant
boundary conditions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine._planner_helpers import (
    _CONCERN_CONSTRAINT_KEYWORDS,
    _CONCERN_MARKER,
    _CROSS_CONCERN_SIGNALS,
    _MIN_CONCERNS_FOR_SPLIT,
    _PHASE_VERBS,
    _build_phases_for_names,
    _expand_agents_for_concerns,
    _parse_concerns,
    _partition_knowledge,
    _score_knowledge_for_concern,
    _split_implement_phase_by_concerns,
)


# ---------------------------------------------------------------------------
# Fake KnowledgeAttachment for tests (avoids heavy model import)
# ---------------------------------------------------------------------------

@dataclass
class _FakeAttachment:
    pack_name: str | None = None
    document_name: str = ""
    path: str = ""


# ---------------------------------------------------------------------------
# _parse_concerns
# ---------------------------------------------------------------------------

class TestParseConcerns:
    def test_happy_path_feature_ids(self) -> None:
        """F0.x style markers produce concern list."""
        summary = "F0.1 Add user login. F0.2 Add logout. F0.3 Add session expiry."
        concerns = _parse_concerns(summary)
        assert len(concerns) == 3
        assert concerns[0][0] == "F0.1"
        assert "Add user login" in concerns[0][1]
        assert concerns[1][0] == "F0.2"
        assert concerns[2][0] == "F0.3"

    def test_happy_path_numbered_list(self) -> None:
        """1. style markers produce concern list."""
        summary = "1. Research API options. 2. Design schema. 3. Implement endpoints."
        concerns = _parse_concerns(summary)
        assert len(concerns) == 3
        assert concerns[0][0] == "1"

    def test_happy_path_parenthesized(self) -> None:
        """(1) style markers produce concern list."""
        summary = "(1) Fix auth bug. (2) Add rate limiting. (3) Write tests."
        concerns = _parse_concerns(summary)
        assert len(concerns) == 3

    def test_fewer_than_min_returns_empty(self) -> None:
        """Fewer than _MIN_CONCERNS_FOR_SPLIT markers returns []."""
        # Only 2 markers — below threshold of 3
        summary = "F0.1 Add login. F0.2 Add logout."
        concerns = _parse_concerns(summary)
        assert concerns == []

    def test_constraint_keyword_bounds_markers(self) -> None:
        """Markers appearing after a constraint keyword are not counted as deliverables."""
        summary = (
            "F0.1 Add login. F0.2 Add logout. F0.3 Add session. "
            "Must not regress F0.4 or F0.5."
        )
        concerns = _parse_concerns(summary)
        # F0.4 and F0.5 appear after "must not regress" — should be excluded
        assert len(concerns) == 3

    def test_plain_text_no_markers(self) -> None:
        """Plain text with no markers returns []."""
        concerns = _parse_concerns("Add OAuth2 login to the API.")
        assert concerns == []

    def test_markers_stripped_of_punctuation(self) -> None:
        """Marker text is stripped of surrounding ()."""
        summary = "(1) First task. (2) Second task. (3) Third task."
        concerns = _parse_concerns(summary)
        # Markers should be bare "1", "2", "3" not "(1)"
        assert concerns[0][0] == "1"
        assert concerns[1][0] == "2"


# ---------------------------------------------------------------------------
# _score_knowledge_for_concern
# ---------------------------------------------------------------------------

class TestScoreKnowledgeForConcern:
    def test_matching_keyword_scores_positive(self) -> None:
        att = _FakeAttachment(pack_name="api-guide", document_name="endpoint-spec")
        score = _score_knowledge_for_concern(att, "add api endpoint for users")  # type: ignore[arg-type]
        assert score > 0

    def test_no_match_scores_zero(self) -> None:
        att = _FakeAttachment(pack_name="css-patterns", document_name="flexbox-guide")
        score = _score_knowledge_for_concern(att, "implement database migration")  # type: ignore[arg-type]
        assert score == 0

    def test_empty_attachment_scores_zero(self) -> None:
        att = _FakeAttachment()
        score = _score_knowledge_for_concern(att, "fix api bug")  # type: ignore[arg-type]
        assert score == 0


# ---------------------------------------------------------------------------
# _partition_knowledge
# ---------------------------------------------------------------------------

class TestPartitionKnowledge:
    def test_domain_specific_attachment_goes_to_one_concern(self) -> None:
        """An attachment matching only one concern is assigned to that slot."""
        att_api = _FakeAttachment(pack_name="api-guide", document_name="endpoint-spec")
        att_ui = _FakeAttachment(pack_name="ui-guide", document_name="react-components")

        concerns = [
            ("F0.1", "add api endpoint"),
            ("F0.2", "build react component"),
            ("F0.3", "write integration tests"),
        ]
        partitions = _partition_knowledge([att_api, att_ui], concerns)  # type: ignore[arg-type]
        assert len(partitions) == 3
        # api attachment should be in concern 0 only
        assert att_api in partitions[0]
        assert att_api not in partitions[1]
        assert att_api not in partitions[2]

    def test_ambiguous_attachment_broadcasts_to_all(self) -> None:
        """An attachment with no clear domain match goes to every slot."""
        att = _FakeAttachment(pack_name="general-docs", document_name="readme")
        concerns = [
            ("1", "add login"),
            ("2", "add logout"),
            ("3", "add session"),
        ]
        partitions = _partition_knowledge([att], concerns)  # type: ignore[arg-type]
        for p in partitions:
            assert att in p

    def test_empty_knowledge_gives_empty_partitions(self) -> None:
        concerns = [("1", "a"), ("2", "b"), ("3", "c")]
        partitions = _partition_knowledge([], concerns)
        assert all(p == [] for p in partitions)


# ---------------------------------------------------------------------------
# _expand_agents_for_concerns
# ---------------------------------------------------------------------------

class TestExpandAgentsForConcerns:
    def test_adds_missing_agent_when_keyword_matches(self) -> None:
        agents = ["backend-engineer"]
        expanded = _expand_agents_for_concerns(agents, "fix bug in api endpoint")
        # backend-engineer already there, but keyword "fix" matches again — still deduplicated
        assert "backend-engineer" in expanded

    def test_adds_frontend_engineer_for_ui_keyword(self) -> None:
        agents = ["backend-engineer"]
        expanded = _expand_agents_for_concerns(agents, "add ui component for dashboard")
        assert "frontend-engineer" in expanded

    def test_does_not_add_already_present_agent(self) -> None:
        agents = ["frontend-engineer", "backend-engineer"]
        expanded = _expand_agents_for_concerns(agents, "add ui and fix api")
        # No duplicates
        assert expanded.count("frontend-engineer") == 1
        assert expanded.count("backend-engineer") == 1

    def test_no_keywords_no_expansion(self) -> None:
        agents = ["architect"]
        expanded = _expand_agents_for_concerns(agents, "design the overall system")
        # No cross-concern signals match — architect stays alone
        assert expanded == ["architect"]

    def test_flavored_agent_variant_not_duplicated(self) -> None:
        """backend-engineer--python already satisfies the backend-engineer slot."""
        agents = ["backend-engineer--python"]
        expanded = _expand_agents_for_concerns(agents, "fix the api endpoint bug")
        # Should NOT add bare "backend-engineer" since the flavored variant is present
        assert "backend-engineer" not in expanded


# ---------------------------------------------------------------------------
# _split_implement_phase_by_concerns
# ---------------------------------------------------------------------------

class TestSplitImplementPhaseByConcerns:
    def _make_phase(self) -> Any:
        from agent_baton.models.execution import PlanPhase, PlanStep
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement everything",
        )
        return PlanPhase(phase_id=1, name="Implement", steps=[step])

    def _pick_agent(self, concern_text: str, candidates: list[str]) -> str:
        return candidates[0] if candidates else "backend-engineer"

    def _step_type(self, agent: str, desc: str, phase: str) -> str:
        return "developing"

    def test_happy_path_replaces_steps(self) -> None:
        phase = self._make_phase()
        concerns = [
            ("F0.1", "auth module"),
            ("F0.2", "dashboard module"),
            ("F0.3", "reporting module"),
        ]
        _split_implement_phase_by_concerns(
            phase, concerns, ["backend-engineer"],
            "implement all modules",
            self._pick_agent,
            self._step_type,
        )
        assert len(phase.steps) == 3
        assert phase.steps[0].step_id == "1.1"
        assert phase.steps[1].step_id == "1.2"
        assert phase.steps[2].step_id == "1.3"

    def test_step_descriptions_include_verb_and_marker(self) -> None:
        phase = self._make_phase()
        concerns = [("F0.1", "auth"), ("F0.2", "core"), ("F0.3", "ui")]
        _split_implement_phase_by_concerns(
            phase, concerns, ["backend-engineer"],
            "implement",
            self._pick_agent,
            self._step_type,
        )
        desc = phase.steps[0].task_description
        assert "Implement" in desc
        assert "F0.1" in desc

    def test_broadcast_strategy_clones_all_knowledge(self) -> None:
        from agent_baton.models.execution import PlanPhase, PlanStep
        from agent_baton.models.knowledge import KnowledgeAttachment

        att = KnowledgeAttachment(
            source="explicit", pack_name=None, document_name="shared-guide",
            path="/docs/guide.md", delivery="reference",
        )
        step = PlanStep(
            step_id="2.1",
            agent_name="backend-engineer",
            task_description="Implement",
            knowledge=[att],
        )
        phase = PlanPhase(phase_id=2, name="Implement", steps=[step])
        concerns = [("1", "a"), ("2", "b"), ("3", "c")]
        _split_implement_phase_by_concerns(
            phase, concerns, ["backend-engineer"],
            "task",
            self._pick_agent,
            self._step_type,
            knowledge_split_strategy="broadcast",
        )
        for s in phase.steps:
            assert att in s.knowledge


# ---------------------------------------------------------------------------
# _build_phases_for_names
# ---------------------------------------------------------------------------

class TestBuildPhasesForNames:
    def test_happy_path_produces_correct_phases(self) -> None:
        phases = _build_phases_for_names(["Design", "Implement", "Review"])
        assert len(phases) == 3
        assert phases[0].name == "Design"
        assert phases[0].phase_id == 1
        assert phases[1].name == "Implement"
        assert phases[1].phase_id == 2
        assert phases[2].name == "Review"
        assert phases[2].phase_id == 3

    def test_custom_start_phase_id(self) -> None:
        phases = _build_phases_for_names(["Test", "Review"], start_phase_id=5)
        assert phases[0].phase_id == 5
        assert phases[1].phase_id == 6

    def test_empty_steps_lists(self) -> None:
        phases = _build_phases_for_names(["Design", "Implement"])
        for p in phases:
            assert p.steps == []

    def test_empty_names_returns_empty(self) -> None:
        phases = _build_phases_for_names([])
        assert phases == []


# ---------------------------------------------------------------------------
# Constants smoke tests
# ---------------------------------------------------------------------------

class TestConstants:
    def test_phase_verbs_contains_core_phases(self) -> None:
        assert "implement" in _PHASE_VERBS
        assert "design" in _PHASE_VERBS
        assert "test" in _PHASE_VERBS
        assert "review" in _PHASE_VERBS

    def test_cross_concern_signals_has_core_agents(self) -> None:
        assert "backend-engineer" in _CROSS_CONCERN_SIGNALS
        assert "frontend-engineer" in _CROSS_CONCERN_SIGNALS
        assert "test-engineer" in _CROSS_CONCERN_SIGNALS

    def test_min_concerns_for_split_is_at_least_two(self) -> None:
        assert _MIN_CONCERNS_FOR_SPLIT >= 2

    def test_constraint_keywords_non_empty(self) -> None:
        assert len(_CONCERN_CONSTRAINT_KEYWORDS) > 0

    def test_concern_marker_regex_compiles(self) -> None:
        """Regex object must be pre-compiled."""
        import re as _re
        assert isinstance(_CONCERN_MARKER, _re.Pattern)
