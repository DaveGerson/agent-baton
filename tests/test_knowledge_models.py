"""Tests for agent_baton.models.knowledge and related model extensions."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.knowledge import (
    KnowledgeAttachment,
    KnowledgeDocument,
    KnowledgeGapRecord,
    KnowledgeGapSignal,
    KnowledgePack,
    ResolvedDecision,
)
from agent_baton.models.agent import AgentDefinition
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepStatus,
)
from agent_baton.models.retrospective import (
    KnowledgeGap,
    KnowledgeGapRecord as RetroKnowledgeGapRecord,
    Retrospective,
    _knowledge_gap_from_dict,
)


# ---------------------------------------------------------------------------
# KnowledgeDocument
# ---------------------------------------------------------------------------

class TestKnowledgeDocument:
    def test_minimal_round_trip(self):
        doc = KnowledgeDocument(name="arch", description="Architecture overview")
        assert KnowledgeDocument.from_dict(doc.to_dict()) == doc

    def test_full_round_trip(self):
        doc = KnowledgeDocument(
            name="context-economics",
            description="Token cost model",
            source_path=Path("/some/path.md"),
            content="# Context Economics\n...",
            tags=["cost", "tokens"],
            grounding="Use this to budget context windows.",
            priority="high",
            token_estimate=1024,
        )
        restored = KnowledgeDocument.from_dict(doc.to_dict())
        assert restored == doc
        assert restored.source_path == Path("/some/path.md")

    def test_source_path_none_survives_round_trip(self):
        doc = KnowledgeDocument(name="x", description="y", source_path=None)
        restored = KnowledgeDocument.from_dict(doc.to_dict())
        assert restored.source_path is None

    def test_defaults(self):
        doc = KnowledgeDocument(name="n", description="d")
        assert doc.content == ""
        assert doc.tags == []
        assert doc.grounding == ""
        assert doc.priority == "normal"
        assert doc.token_estimate == 0

    def test_from_dict_missing_optional_fields(self):
        doc = KnowledgeDocument.from_dict({"name": "n", "description": "d"})
        assert doc.priority == "normal"
        assert doc.token_estimate == 0
        assert doc.tags == []


# ---------------------------------------------------------------------------
# KnowledgePack
# ---------------------------------------------------------------------------

class TestKnowledgePack:
    def test_minimal_round_trip(self):
        pack = KnowledgePack(name="agent-baton", description="Baton knowledge")
        assert KnowledgePack.from_dict(pack.to_dict()) == pack

    def test_full_round_trip(self):
        doc = KnowledgeDocument(name="arch", description="Architecture")
        pack = KnowledgePack(
            name="agent-baton",
            description="Baton knowledge",
            source_path=Path("/proj/.claude/knowledge/agent-baton"),
            tags=["orchestration", "architecture"],
            target_agents=["backend-engineer--python", "architect"],
            default_delivery="inline",
            documents=[doc],
        )
        restored = KnowledgePack.from_dict(pack.to_dict())
        assert restored == pack
        assert len(restored.documents) == 1
        assert restored.documents[0].name == "arch"

    def test_defaults(self):
        pack = KnowledgePack(name="p", description="d")
        assert pack.tags == []
        assert pack.target_agents == []
        assert pack.default_delivery == "reference"
        assert pack.documents == []

    def test_source_path_serializes_as_string(self):
        pack = KnowledgePack(name="p", description="d", source_path=Path("/a/b"))
        d = pack.to_dict()
        assert d["source_path"] == "/a/b"


# ---------------------------------------------------------------------------
# KnowledgeAttachment
# ---------------------------------------------------------------------------

class TestKnowledgeAttachment:
    def test_minimal_round_trip(self):
        att = KnowledgeAttachment(
            source="explicit",
            pack_name="agent-baton",
            document_name="arch",
            path="/proj/.claude/knowledge/agent-baton/arch.md",
            delivery="inline",
        )
        assert KnowledgeAttachment.from_dict(att.to_dict()) == att

    def test_pack_name_none_round_trip(self):
        att = KnowledgeAttachment(
            source="explicit",
            pack_name=None,
            document_name="standalone",
            path="/docs/standalone.md",
            delivery="reference",
        )
        restored = KnowledgeAttachment.from_dict(att.to_dict())
        assert restored.pack_name is None

    def test_all_source_values_survive(self):
        sources = [
            "explicit",
            "agent-declared",
            "planner-matched:tag",
            "planner-matched:relevance",
            "gap-suggested",
        ]
        for src in sources:
            att = KnowledgeAttachment(
                source=src, pack_name="p", document_name="d",
                path="/x.md", delivery="reference",
            )
            assert KnowledgeAttachment.from_dict(att.to_dict()).source == src

    def test_defaults(self):
        att = KnowledgeAttachment(
            source="explicit", pack_name=None, document_name="d",
            path="/x.md", delivery="inline",
        )
        assert att.retrieval == "file"
        assert att.grounding == ""
        assert att.token_estimate == 0

    def test_mcp_rag_retrieval_survives(self):
        att = KnowledgeAttachment(
            source="explicit", pack_name="p", document_name="d",
            path="/x.md", delivery="reference", retrieval="mcp-rag",
        )
        assert KnowledgeAttachment.from_dict(att.to_dict()).retrieval == "mcp-rag"


# ---------------------------------------------------------------------------
# KnowledgeGapSignal
# ---------------------------------------------------------------------------

class TestKnowledgeGapSignal:
    def test_round_trip(self):
        sig = KnowledgeGapSignal(
            description="Need SOX audit trail context",
            confidence="none",
            gap_type="contextual",
            step_id="1.2",
            agent_name="backend-engineer--python",
            partial_outcome="Implemented the module up to the retention policy",
        )
        assert KnowledgeGapSignal.from_dict(sig.to_dict()) == sig

    def test_defaults(self):
        sig = KnowledgeGapSignal(
            description="x", confidence="low", gap_type="factual",
            step_id="1.1", agent_name="arch",
        )
        assert sig.partial_outcome == ""

    def test_from_dict_defaults_for_optional_fields(self):
        sig = KnowledgeGapSignal.from_dict({
            "description": "x",
            "step_id": "1.1",
            "agent_name": "arch",
        })
        assert sig.confidence == "low"
        assert sig.gap_type == "factual"
        assert sig.partial_outcome == ""


# ---------------------------------------------------------------------------
# KnowledgeGapRecord
# ---------------------------------------------------------------------------

class TestKnowledgeGapRecord:
    def test_round_trip(self):
        rec = KnowledgeGapRecord(
            description="Lacked context on SOX",
            gap_type="contextual",
            resolution="human-answered",
            resolution_detail="Use 90-day immutable logs",
            agent_name="backend-engineer--python",
            task_summary="Implement audit trail",
            task_type="feature",
        )
        assert KnowledgeGapRecord.from_dict(rec.to_dict()) == rec

    def test_task_type_none_round_trip(self):
        rec = KnowledgeGapRecord(
            description="x", gap_type="factual", resolution="unresolved",
            resolution_detail="", agent_name="arch", task_summary="task",
            task_type=None,
        )
        restored = KnowledgeGapRecord.from_dict(rec.to_dict())
        assert restored.task_type is None

    def test_defaults(self):
        rec = KnowledgeGapRecord.from_dict({
            "description": "x",
            "resolution_detail": "",
            "agent_name": "a",
            "task_summary": "t",
        })
        assert rec.gap_type == "factual"
        assert rec.resolution == "unresolved"
        assert rec.task_type is None


# ---------------------------------------------------------------------------
# ResolvedDecision
# ---------------------------------------------------------------------------

class TestResolvedDecision:
    def test_round_trip(self):
        dec = ResolvedDecision(
            gap_description="SOX audit trail requirements",
            resolution="Use 90-day retention with immutable append-only logs",
            step_id="1.2",
            timestamp="2026-03-24T12:00:00Z",
        )
        assert ResolvedDecision.from_dict(dec.to_dict()) == dec

    def test_from_dict_missing_timestamp(self):
        dec = ResolvedDecision.from_dict({
            "gap_description": "x",
            "resolution": "y",
            "step_id": "1.1",
        })
        assert dec.timestamp == ""


# ---------------------------------------------------------------------------
# PlanStep — knowledge field extension
# ---------------------------------------------------------------------------

class TestPlanStepKnowledge:
    def test_empty_knowledge_omitted_from_dict(self):
        step = PlanStep(step_id="1.1", agent_name="arch", task_description="task")
        d = step.to_dict()
        assert "knowledge" not in d

    def test_knowledge_round_trip(self):
        att = KnowledgeAttachment(
            source="explicit", pack_name="agent-baton", document_name="arch",
            path="/x.md", delivery="inline",
        )
        step = PlanStep(
            step_id="1.1", agent_name="arch", task_description="task",
            knowledge=[att],
        )
        restored = PlanStep.from_dict(step.to_dict())
        assert len(restored.knowledge) == 1
        assert restored.knowledge[0].document_name == "arch"

    def test_no_knowledge_key_in_old_json(self):
        """Steps deserialized from old plan.json (no 'knowledge' key) get empty list."""
        data = {
            "step_id": "1.1",
            "agent_name": "arch",
            "task_description": "task",
        }
        step = PlanStep.from_dict(data)
        assert step.knowledge == []


# ---------------------------------------------------------------------------
# MachinePlan — new fields
# ---------------------------------------------------------------------------

class TestMachinePlanNewFields:
    def _make_plan(self, **kwargs) -> MachinePlan:
        defaults = dict(
            task_id="t1",
            task_summary="Summary",
        )
        defaults.update(kwargs)
        return MachinePlan(**defaults)

    def test_defaults(self):
        plan = self._make_plan()
        assert plan.task_type is None
        assert plan.explicit_knowledge_packs == []
        assert plan.explicit_knowledge_docs == []
        assert plan.intervention_level == "low"

    def test_round_trip_with_new_fields(self):
        plan = self._make_plan(
            task_type="feature",
            explicit_knowledge_packs=["compliance"],
            explicit_knowledge_docs=["/docs/spec.md"],
            intervention_level="medium",
        )
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.task_type == "feature"
        assert restored.explicit_knowledge_packs == ["compliance"]
        assert restored.explicit_knowledge_docs == ["/docs/spec.md"]
        assert restored.intervention_level == "medium"

    def test_old_plan_json_without_new_fields(self):
        """Plans from before knowledge delivery use defaults gracefully."""
        data = {
            "task_id": "t1",
            "task_summary": "old task",
            "risk_level": "LOW",
            "phases": [],
        }
        plan = MachinePlan.from_dict(data)
        assert plan.task_type is None
        assert plan.explicit_knowledge_packs == []
        assert plan.intervention_level == "low"

    def test_to_markdown_includes_task_type(self):
        plan = self._make_plan(task_type="feature")
        md = plan.to_markdown()
        assert "**Task Type**: feature" in md

    def test_to_markdown_omits_task_type_when_none(self):
        plan = self._make_plan()
        md = plan.to_markdown()
        assert "Task Type" not in md

    def test_to_markdown_omits_intervention_when_default(self):
        plan = self._make_plan(intervention_level="low")
        md = plan.to_markdown()
        assert "Intervention Level" not in md

    def test_to_markdown_shows_intervention_when_non_default(self):
        plan = self._make_plan(intervention_level="high")
        md = plan.to_markdown()
        assert "**Intervention Level**: high" in md

    def test_to_markdown_shows_knowledge_attachments(self):
        att = KnowledgeAttachment(
            source="agent-declared", pack_name="agent-baton",
            document_name="arch", path="/x.md", delivery="inline",
        )
        step = PlanStep(
            step_id="1.1", agent_name="backend-engineer--python",
            task_description="task", knowledge=[att],
        )
        phase = PlanPhase(phase_id=1, name="Build", steps=[step])
        plan = MachinePlan(task_id="t1", task_summary="S", phases=[phase])
        md = plan.to_markdown()
        assert "arch (agent-baton)" in md
        assert "inline (agent-declared)" in md


# ---------------------------------------------------------------------------
# StepStatus — INTERRUPTED enum member
# ---------------------------------------------------------------------------

class TestStepStatusInterrupted:
    def test_interrupted_value(self):
        assert StepStatus.INTERRUPTED.value == "interrupted"

    def test_interrupted_in_enum_members(self):
        values = {s.value for s in StepStatus}
        assert "interrupted" in values


# ---------------------------------------------------------------------------
# ExecutionState — pending_gaps, resolved_decisions, interrupted_step_ids
# ---------------------------------------------------------------------------

def _minimal_plan() -> MachinePlan:
    return MachinePlan(task_id="t1", task_summary="Test task")


class TestExecutionStateNewFields:
    def test_defaults(self):
        state = ExecutionState(task_id="t1", plan=_minimal_plan())
        assert state.pending_gaps == []
        assert state.resolved_decisions == []

    def test_interrupted_step_ids_property(self):
        from agent_baton.models.execution import StepResult
        state = ExecutionState(task_id="t1", plan=_minimal_plan())
        state.step_results = [
            StepResult(step_id="1.1", agent_name="a", status="complete"),
            StepResult(step_id="1.2", agent_name="b", status="interrupted"),
            StepResult(step_id="1.3", agent_name="c", status="failed"),
        ]
        assert state.interrupted_step_ids == {"1.2"}

    def test_round_trip_with_pending_gaps(self):
        sig = KnowledgeGapSignal(
            description="Need domain context",
            confidence="none",
            gap_type="contextual",
            step_id="1.1",
            agent_name="arch",
        )
        state = ExecutionState(
            task_id="t1", plan=_minimal_plan(), pending_gaps=[sig]
        )
        restored = ExecutionState.from_dict(state.to_dict())
        assert len(restored.pending_gaps) == 1
        assert restored.pending_gaps[0].description == "Need domain context"

    def test_round_trip_with_resolved_decisions(self):
        dec = ResolvedDecision(
            gap_description="SOX requirement",
            resolution="Use 90-day logs",
            step_id="1.2",
            timestamp="2026-03-24T10:00:00Z",
        )
        state = ExecutionState(
            task_id="t1", plan=_minimal_plan(), resolved_decisions=[dec]
        )
        restored = ExecutionState.from_dict(state.to_dict())
        assert len(restored.resolved_decisions) == 1
        assert restored.resolved_decisions[0].resolution == "Use 90-day logs"

    def test_old_execution_state_json_without_new_fields(self):
        """Old execution-state.json files without knowledge fields load cleanly."""
        import json
        plan_dict = _minimal_plan().to_dict()
        old_data = {
            "task_id": "t1",
            "plan": plan_dict,
            "status": "running",
            "step_results": [],
            "gate_results": [],
            "approval_results": [],
            "amendments": [],
        }
        state = ExecutionState.from_dict(old_data)
        assert state.pending_gaps == []
        assert state.resolved_decisions == []


# ---------------------------------------------------------------------------
# AgentDefinition — knowledge_packs field
# ---------------------------------------------------------------------------

class TestAgentDefinitionKnowledgePacks:
    def test_default_is_empty_list(self):
        agent = AgentDefinition(name="arch", description="Architect")
        assert agent.knowledge_packs == []

    def test_explicit_knowledge_packs(self):
        agent = AgentDefinition(
            name="backend-engineer--python",
            description="Python backend",
            knowledge_packs=["agent-baton", "ai-orchestration"],
        )
        assert agent.knowledge_packs == ["agent-baton", "ai-orchestration"]


# ---------------------------------------------------------------------------
# Retrospective — KnowledgeGapRecord replacement + backward compat
# ---------------------------------------------------------------------------

class TestRetrospectiveKnowledgeGaps:
    def test_new_schema_round_trip(self):
        gap = KnowledgeGapRecord(
            description="Lacked SOX context",
            gap_type="contextual",
            resolution="human-answered",
            resolution_detail="Use 90-day logs",
            agent_name="backend-engineer--python",
            task_summary="audit trail",
            task_type="feature",
        )
        retro = Retrospective(
            task_id="t1",
            task_name="Audit Trail Task",
            timestamp="2026-03-24T10:00:00Z",
            knowledge_gaps=[gap],
        )
        d = retro.to_dict()
        restored = Retrospective.from_dict(d)
        assert len(restored.knowledge_gaps) == 1
        g = restored.knowledge_gaps[0]
        assert isinstance(g, KnowledgeGapRecord)
        assert g.description == "Lacked SOX context"
        assert g.gap_type == "contextual"
        assert g.resolution == "human-answered"
        assert g.agent_name == "backend-engineer--python"

    def test_old_schema_backward_compat(self):
        """Old KnowledgeGap data (affected_agent, suggested_fix) loads as KnowledgeGapRecord."""
        old_data = {
            "task_id": "t1",
            "task_name": "Old Task",
            "timestamp": "2025-01-01T00:00:00Z",
            "knowledge_gaps": [
                {
                    "description": "Agent lacked context on deployment targets",
                    "affected_agent": "devops-engineer",
                    "suggested_fix": "create knowledge pack",
                }
            ],
        }
        retro = Retrospective.from_dict(old_data)
        assert len(retro.knowledge_gaps) == 1
        g = retro.knowledge_gaps[0]
        assert isinstance(g, KnowledgeGapRecord)
        assert g.description == "Agent lacked context on deployment targets"
        assert g.agent_name == "devops-engineer"
        assert g.resolution_detail == "create knowledge pack"
        assert g.resolution == "unresolved"
        assert g.gap_type == "factual"

    def test_empty_knowledge_gaps_round_trip(self):
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-24T00:00:00Z"
        )
        restored = Retrospective.from_dict(retro.to_dict())
        assert restored.knowledge_gaps == []

    def test_to_markdown_renders_gap_record(self):
        gap = KnowledgeGapRecord(
            description="Lacked SOX context",
            gap_type="contextual",
            resolution="human-answered",
            resolution_detail="Use 90-day logs",
            agent_name="backend-engineer--python",
            task_summary="audit trail",
        )
        retro = Retrospective(
            task_id="t1", task_name="T", timestamp="2026-03-24T00:00:00Z",
            knowledge_gaps=[gap],
        )
        md = retro.to_markdown()
        assert "Lacked SOX context" in md
        assert "human-answered" in md
        assert "backend-engineer--python" in md


# ---------------------------------------------------------------------------
# _knowledge_gap_from_dict helper
# ---------------------------------------------------------------------------

class TestKnowledgeGapFromDict:
    def test_new_schema_delegates_to_from_dict(self):
        data = {
            "description": "x",
            "gap_type": "contextual",
            "resolution": "auto-resolved",
            "resolution_detail": "via pack-a",
            "agent_name": "arch",
            "task_summary": "t",
            "task_type": "feature",
        }
        rec = _knowledge_gap_from_dict(data)
        assert rec.gap_type == "contextual"
        assert rec.resolution == "auto-resolved"

    def test_old_schema_uses_affected_agent_key(self):
        data = {
            "description": "missing context",
            "affected_agent": "architect",
            "suggested_fix": "update prompt",
        }
        rec = _knowledge_gap_from_dict(data)
        assert rec.agent_name == "architect"
        assert rec.resolution_detail == "update prompt"
        assert rec.gap_type == "factual"
        assert rec.resolution == "unresolved"

    def test_old_schema_with_only_suggested_fix(self):
        data = {
            "description": "x",
            "suggested_fix": "create knowledge pack",
        }
        rec = _knowledge_gap_from_dict(data)
        assert rec.resolution_detail == "create knowledge pack"
        assert rec.agent_name == ""
