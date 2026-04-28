"""Tests for concern-split knowledge partitioning (bead bd-1add).

When _split_implement_phase_by_concerns fires, child steps should receive
only the knowledge relevant to their domain rather than the full broadcast
list.  Ambiguous/cross-cutting knowledge still goes everywhere (safe
fallback).
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.models.execution import PlanPhase, PlanStep
from agent_baton.models.knowledge import KnowledgeAttachment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _att(
    document_name: str,
    pack_name: str | None = None,
    path: str = "",
) -> KnowledgeAttachment:
    """Build a minimal KnowledgeAttachment for testing."""
    return KnowledgeAttachment(
        source="explicit",
        pack_name=pack_name,
        document_name=document_name,
        path=path or f"/knowledge/{document_name}",
        delivery="reference",
    )


def _phase_with_knowledge(
    knowledge: list[KnowledgeAttachment],
    phase_id: int = 1,
    name: str = "Implement",
) -> PlanPhase:
    """Build a single-step PlanPhase that carries *knowledge*."""
    step = PlanStep(
        step_id=f"{phase_id}.1",
        agent_name="backend-engineer",
        task_description="do work",
        knowledge=list(knowledge),
    )
    return PlanPhase(phase_id=phase_id, name=name, steps=[step])


# Three-concern task summary (>= _MIN_CONCERNS_FOR_SPLIT = 3).
_FRONTEND_CONCERN = ("F0.1", "build react component with css styling")
_BACKEND_CONCERN  = ("F0.2", "implement api endpoint with database migration")
_TEST_CONCERN     = ("F0.3", "write tests for the new features")

_THREE_CONCERNS = [_FRONTEND_CONCERN, _BACKEND_CONCERN, _TEST_CONCERN]

_CANDIDATE_AGENTS = ["frontend-engineer", "backend-engineer", "test-engineer"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_split_assigns_frontend_pack_only_to_frontend_step(tmp_path):
    """A frontend-named pack should only land on the frontend child step."""
    react_doc = _att("react-patterns.md", pack_name="react-patterns")
    phase = _phase_with_knowledge([react_doc])

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="smart",
    )

    assert len(phase.steps) == 3
    frontend_step = phase.steps[0]   # F0.1 — frontend
    backend_step  = phase.steps[1]   # F0.2 — backend
    test_step     = phase.steps[2]   # F0.3 — tests

    frontend_doc_names = {k.document_name for k in frontend_step.knowledge}
    assert "react-patterns.md" in frontend_doc_names

    backend_doc_names  = {k.document_name for k in backend_step.knowledge}
    test_doc_names     = {k.document_name for k in test_step.knowledge}
    assert "react-patterns.md" not in backend_doc_names
    assert "react-patterns.md" not in test_doc_names


def test_split_assigns_backend_pack_only_to_backend_step(tmp_path):
    """An api/database-named doc should only land on the backend child step."""
    api_doc = _att("api-conventions.md", pack_name="api-guide")
    phase = _phase_with_knowledge([api_doc])

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="smart",
    )

    assert len(phase.steps) == 3
    frontend_step = phase.steps[0]
    backend_step  = phase.steps[1]
    test_step     = phase.steps[2]

    backend_doc_names = {k.document_name for k in backend_step.knowledge}
    assert "api-conventions.md" in backend_doc_names

    frontend_doc_names = {k.document_name for k in frontend_step.knowledge}
    test_doc_names     = {k.document_name for k in test_step.knowledge}
    assert "api-conventions.md" not in frontend_doc_names
    assert "api-conventions.md" not in test_doc_names


def test_split_broadcasts_ambiguous_knowledge(tmp_path):
    """A doc with no domain signal must go to every child step."""
    generic_doc = _att("project-overview.md", pack_name="meta")
    phase = _phase_with_knowledge([generic_doc])

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="smart",
    )

    assert len(phase.steps) == 3
    for step in phase.steps:
        doc_names = {k.document_name for k in step.knowledge}
        assert "project-overview.md" in doc_names, (
            f"Ambiguous doc missing from step {step.step_id}"
        )


def test_broadcast_strategy_keeps_old_behavior(tmp_path):
    """Setting strategy='broadcast' must clone the full list to every step."""
    react_doc   = _att("react-patterns.md", pack_name="react-guide")
    api_doc     = _att("api-reference.md",  pack_name="api-guide")
    generic_doc = _att("project-overview.md")

    phase = _phase_with_knowledge([react_doc, api_doc, generic_doc])

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="broadcast",
    )

    assert len(phase.steps) == 3
    expected = {"react-patterns.md", "api-reference.md", "project-overview.md"}
    for step in phase.steps:
        doc_names = {k.document_name for k in step.knowledge}
        assert doc_names == expected, (
            f"Step {step.step_id} missing docs under broadcast strategy"
        )


def test_split_preserves_total_unique_knowledge_when_unambiguous(tmp_path):
    """All unique docs must appear somewhere across the split steps."""
    react_doc = _att("react-patterns.md", pack_name="react-guide")
    api_doc   = _att("api-reference.md",  pack_name="api-guide")
    # No ambiguous doc — each should be partitioned to exactly one step.

    phase = _phase_with_knowledge([react_doc, api_doc])

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="smart",
    )

    all_names: set[str] = set()
    for step in phase.steps:
        all_names.update(k.document_name for k in step.knowledge)

    assert "react-patterns.md" in all_names
    assert "api-reference.md" in all_names


def test_split_handles_empty_knowledge_list(tmp_path):
    """Splitting a phase with no knowledge should produce steps with no knowledge."""
    phase = _phase_with_knowledge([])  # empty

    planner = IntelligentPlanner(team_context_root=tmp_path / "ctx")
    planner._split_implement_phase_by_concerns(
        phase, _THREE_CONCERNS, _CANDIDATE_AGENTS, "dummy summary",
        knowledge_split_strategy="smart",
    )

    assert len(phase.steps) == 3
    for step in phase.steps:
        assert step.knowledge == []
