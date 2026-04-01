"""Tests for knowledge delivery integration in PromptDispatcher."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.models.execution import PlanStep, TeamMember
from agent_baton.models.knowledge import KnowledgeAttachment


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher() -> PromptDispatcher:
    return PromptDispatcher()


def _inline_attachment(
    document_name: str = "architecture.md",
    pack_name: str | None = "agent-baton",
    path: str = "",
    grounding: str = "",
    token_estimate: int = 500,
) -> KnowledgeAttachment:
    return KnowledgeAttachment(
        source="agent-declared",
        pack_name=pack_name,
        document_name=document_name,
        path=path,
        delivery="inline",
        retrieval="file",
        grounding=grounding,
        token_estimate=token_estimate,
    )


def _reference_attachment(
    document_name: str = "context-economics.md",
    pack_name: str | None = "ai-orchestration",
    path: str = "/docs/context-economics.md",
    grounding: str = "Use this to budget context windows.",
    retrieval: str = "file",
) -> KnowledgeAttachment:
    return KnowledgeAttachment(
        source="planner-matched:tag",
        pack_name=pack_name,
        document_name=document_name,
        path=path,
        delivery="reference",
        retrieval=retrieval,
        grounding=grounding,
        token_estimate=1200,
    )


def _make_step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer--python",
    task_description: str = "Implement the foo module.",
    knowledge: list[KnowledgeAttachment] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        knowledge=knowledge or [],
    )


def _make_team_member(
    member_id: str = "1.1.a",
    agent_name: str = "backend-engineer--python",
    role: str = "implementer",
    task_description: str = "Write the models.",
    depends_on: list[str] | None = None,
    deliverables: list[str] | None = None,
) -> TeamMember:
    return TeamMember(
        member_id=member_id,
        agent_name=agent_name,
        role=role,
        task_description=task_description,
        depends_on=depends_on or [],
        deliverables=deliverables or [],
    )


# ---------------------------------------------------------------------------
# _build_knowledge_section — unit tests
# ---------------------------------------------------------------------------


class TestBuildKnowledgeSectionEmpty:
    def test_returns_empty_string_for_no_attachments(
        self, dispatcher: PromptDispatcher
    ) -> None:
        result = dispatcher._build_knowledge_section([])
        assert result == ""


class TestBuildKnowledgeSectionInline:
    def test_renders_knowledge_context_header(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("# Architecture\nContent here.")
        attachment = _inline_attachment(
            document_name="architecture.md",
            pack_name="agent-baton",
            path=str(doc_file),
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "## Knowledge Context" in result

    def test_renders_document_name_and_pack_name(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("# Architecture\nContent here.")
        attachment = _inline_attachment(
            document_name="architecture.md",
            pack_name="agent-baton",
            path=str(doc_file),
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "architecture.md (agent-baton)" in result

    def test_renders_document_content(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("# Architecture\nThis is the architecture doc.")
        attachment = _inline_attachment(
            document_name="architecture.md",
            pack_name="agent-baton",
            path=str(doc_file),
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "This is the architecture doc." in result

    def test_renders_grounding_when_present(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Content.")
        grounding = "Use this to understand the overall design before writing code."
        attachment = _inline_attachment(
            document_name="architecture.md",
            pack_name="agent-baton",
            path=str(doc_file),
            grounding=grounding,
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert grounding in result

    def test_does_not_render_knowledge_references_header_for_inline_only(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Content.")
        attachment = _inline_attachment(path=str(doc_file))
        result = dispatcher._build_knowledge_section([attachment])
        assert "## Knowledge References" not in result

    def test_standalone_doc_uses_standalone_label(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "custom.md"
        doc_file.write_text("Custom doc content.")
        attachment = _inline_attachment(
            document_name="custom.md",
            pack_name=None,
            path=str(doc_file),
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "custom.md (standalone)" in result

    def test_unreadable_path_returns_placeholder(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _inline_attachment(
            document_name="missing.md",
            path="/nonexistent/path/missing.md",
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "## Knowledge Context" in result
        assert "_Content unavailable:" in result

    def test_no_path_returns_placeholder(self, dispatcher: PromptDispatcher) -> None:
        attachment = _inline_attachment(
            document_name="no-path.md",
            path="",
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "_Content unavailable:" in result
        assert "no-path.md" in result


class TestBuildKnowledgeSectionReference:
    def test_renders_knowledge_references_header(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment()
        result = dispatcher._build_knowledge_section([attachment])
        assert "## Knowledge References" in result

    def test_renders_document_name_and_pack_name(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment(
            document_name="context-economics.md",
            pack_name="ai-orchestration",
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "**context-economics.md** (ai-orchestration)" in result

    def test_renders_grounding_text(self, dispatcher: PromptDispatcher) -> None:
        grounding = "Use this to budget context windows."
        attachment = _reference_attachment(grounding=grounding)
        result = dispatcher._build_knowledge_section([attachment])
        assert grounding in result

    def test_renders_file_retrieval_hint(self, dispatcher: PromptDispatcher) -> None:
        attachment = _reference_attachment(
            path="/docs/context-economics.md",
            retrieval="file",
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "Retrieve via: `Read /docs/context-economics.md`" in result

    def test_renders_rag_retrieval_hint_for_mcp_rag(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment(
            document_name="context-economics.md",
            retrieval="mcp-rag",
            grounding="Budget context windows.",
        )
        result = dispatcher._build_knowledge_section([attachment])
        assert "query RAG server for" in result
        assert "context-economics.md" in result

    def test_rag_retrieval_hint_not_rendered_for_file_retrieval(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment(retrieval="file")
        result = dispatcher._build_knowledge_section([attachment])
        assert "query RAG server" not in result

    def test_does_not_render_knowledge_context_for_reference_only(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment()
        result = dispatcher._build_knowledge_section([attachment])
        assert "## Knowledge Context" not in result


class TestBuildKnowledgeSectionMixed:
    def test_both_headers_present_for_mixed(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Architecture content.")
        inline = _inline_attachment(path=str(doc_file))
        reference = _reference_attachment()
        result = dispatcher._build_knowledge_section([inline, reference])
        assert "## Knowledge Context" in result
        assert "## Knowledge References" in result

    def test_inline_content_before_references(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Architecture content.")
        inline = _inline_attachment(path=str(doc_file))
        reference = _reference_attachment()
        result = dispatcher._build_knowledge_section([inline, reference])
        context_pos = result.index("## Knowledge Context")
        references_pos = result.index("## Knowledge References")
        assert context_pos < references_pos

    def test_multiple_inline_docs_all_rendered(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc1 = tmp_path / "arch.md"
        doc1.write_text("Architecture content.")
        doc2 = tmp_path / "design.md"
        doc2.write_text("Design content.")
        inline1 = _inline_attachment(document_name="arch.md", path=str(doc1))
        inline2 = _inline_attachment(
            document_name="design.md",
            pack_name="agent-baton",
            path=str(doc2),
        )
        result = dispatcher._build_knowledge_section([inline1, inline2])
        assert "Architecture content." in result
        assert "Design content." in result

    def test_multiple_reference_docs_all_rendered(
        self, dispatcher: PromptDispatcher
    ) -> None:
        ref1 = _reference_attachment(
            document_name="context-economics.md",
            path="/docs/context-economics.md",
        )
        ref2 = _reference_attachment(
            document_name="audit-checklist.md",
            pack_name="compliance",
            path="/docs/audit-checklist.md",
            grounding="Use for compliance checks.",
        )
        result = dispatcher._build_knowledge_section([ref1, ref2])
        assert "context-economics.md" in result
        assert "audit-checklist.md" in result


# ---------------------------------------------------------------------------
# build_delegation_prompt — knowledge integration
# ---------------------------------------------------------------------------


class TestBuildDelegationPromptKnowledge:
    def test_no_knowledge_no_knowledge_sections(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Knowledge Context" not in prompt
        assert "## Knowledge References" not in prompt

    def test_inline_knowledge_appears_between_shared_context_and_task(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Architecture content.")
        attachment = _inline_attachment(path=str(doc_file))
        step = _make_step(knowledge=[attachment])
        prompt = dispatcher.build_delegation_prompt(step, shared_context="Team context.")
        shared_pos = prompt.index("## Shared Context")
        knowledge_pos = prompt.index("## Knowledge Context")
        task_pos = prompt.index("## Your Task")
        assert shared_pos < knowledge_pos < task_pos

    def test_reference_knowledge_appears_between_shared_context_and_task(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment()
        step = _make_step(knowledge=[attachment])
        prompt = dispatcher.build_delegation_prompt(step)
        shared_pos = prompt.index("## Shared Context")
        references_pos = prompt.index("## Knowledge References")
        task_pos = prompt.index("## Your Task")
        assert shared_pos < references_pos < task_pos

    def test_knowledge_section_content_included_in_prompt(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("This is load-bearing content.")
        attachment = _inline_attachment(path=str(doc_file))
        step = _make_step(knowledge=[attachment])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "This is load-bearing content." in prompt

    def test_knowledge_gaps_metacognition_block_always_present(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "KNOWLEDGE_GAP:" in prompt

    def test_knowledge_gaps_line_contains_signal_format(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "KNOWLEDGE_GAP:" in prompt
        assert "CONFIDENCE:" in prompt

    def test_knowledge_gaps_line_contains_high_risk_warning(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "HIGH/CRITICAL" in prompt

    def test_knowledge_gaps_line_position_after_task(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        prompt = dispatcher.build_delegation_prompt(step)
        task_pos = prompt.index("## Your Task")
        gaps_pos = prompt.index("KNOWLEDGE_GAP:")
        assert task_pos < gaps_pos


# ---------------------------------------------------------------------------
# build_team_delegation_prompt — knowledge integration
# ---------------------------------------------------------------------------


class TestBuildTeamDelegationPromptKnowledge:
    def test_no_knowledge_no_knowledge_sections(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "## Knowledge Context" not in prompt
        assert "## Knowledge References" not in prompt

    def test_step_knowledge_injected_into_team_member_prompt(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Shared team architecture doc.")
        attachment = _inline_attachment(path=str(doc_file))
        step = _make_step(knowledge=[attachment])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "Shared team architecture doc." in prompt

    def test_knowledge_appears_between_shared_context_and_task_for_team(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Team knowledge content.")
        attachment = _inline_attachment(path=str(doc_file))
        step = _make_step(knowledge=[attachment])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(
            step, member, shared_context="Team shared context."
        )
        shared_pos = prompt.index("## Shared Context")
        knowledge_pos = prompt.index("## Knowledge Context")
        task_pos = prompt.index("## Your Task")
        assert shared_pos < knowledge_pos < task_pos

    def test_knowledge_gaps_metacognition_block_present_in_team_prompt(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "KNOWLEDGE_GAP:" in prompt

    def test_knowledge_gaps_signal_format_in_team_prompt(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(knowledge=[])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "KNOWLEDGE_GAP:" in prompt
        assert "CONFIDENCE:" in prompt

    def test_reference_knowledge_in_team_prompt(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment()
        step = _make_step(knowledge=[attachment])
        member = _make_team_member()
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "## Knowledge References" in prompt
        assert "context-economics.md" in prompt

    def test_all_team_members_receive_same_step_knowledge(
        self, dispatcher: PromptDispatcher, tmp_path
    ) -> None:
        """Every team member in the step gets identical knowledge because it
        is resolved at the step level, not the member level."""
        doc_file = tmp_path / "arch.md"
        doc_file.write_text("Shared architecture content.")
        attachment = _inline_attachment(path=str(doc_file))
        step = _make_step(knowledge=[attachment])
        member_a = _make_team_member(member_id="1.1.a", role="lead")
        member_b = _make_team_member(
            member_id="1.1.b", agent_name="test-engineer", role="reviewer"
        )
        prompt_a = dispatcher.build_team_delegation_prompt(step, member_a)
        prompt_b = dispatcher.build_team_delegation_prompt(step, member_b)
        # Both prompts include the same knowledge content
        assert "Shared architecture content." in prompt_a
        assert "Shared architecture content." in prompt_b


# ---------------------------------------------------------------------------
# RAG retrieval hint — targeted tests
# ---------------------------------------------------------------------------


class TestRAGRetrievalHint:
    def test_rag_hint_rendered_only_for_mcp_rag_retrieval(
        self, dispatcher: PromptDispatcher
    ) -> None:
        rag_attachment = _reference_attachment(retrieval="mcp-rag")
        file_attachment = _reference_attachment(
            document_name="other.md",
            path="/docs/other.md",
            retrieval="file",
        )
        section = dispatcher._build_knowledge_section([rag_attachment, file_attachment])
        # Only the rag attachment gets RAG instructions
        assert "query RAG server" in section
        # File attachment gets a Read hint, not RAG
        assert "Retrieve via: `Read /docs/other.md`" in section

    def test_rag_hint_includes_document_name(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment(
            document_name="compliance-checklist.md",
            retrieval="mcp-rag",
            grounding="",
        )
        hint = PromptDispatcher._build_retrieval_hint(attachment)
        assert "compliance-checklist.md" in hint

    def test_rag_hint_uses_grounding_when_available(
        self, dispatcher: PromptDispatcher
    ) -> None:
        attachment = _reference_attachment(
            document_name="compliance-checklist.md",
            retrieval="mcp-rag",
            grounding="Use for SOX compliance verification.",
        )
        hint = PromptDispatcher._build_retrieval_hint(attachment)
        assert "compliance-checklist.md" in hint

    def test_file_hint_format(self) -> None:
        attachment = _reference_attachment(
            document_name="arch.md",
            path="/docs/arch.md",
            retrieval="file",
        )
        hint = PromptDispatcher._build_retrieval_hint(attachment)
        assert hint == "Retrieve via: `Read /docs/arch.md`"


# ---------------------------------------------------------------------------
# Load attachment content — lazy loading
# ---------------------------------------------------------------------------


class TestLoadAttachmentContent:
    def test_loads_content_from_path(self, tmp_path) -> None:
        doc_file = tmp_path / "doc.md"
        doc_file.write_text("Real document content here.")
        attachment = _inline_attachment(path=str(doc_file))
        result = PromptDispatcher._load_attachment_content(attachment)
        assert result == "Real document content here."

    def test_returns_placeholder_for_missing_path(self) -> None:
        attachment = _inline_attachment(
            document_name="missing.md",
            path="/nonexistent/missing.md",
        )
        result = PromptDispatcher._load_attachment_content(attachment)
        assert result.startswith("_Content unavailable:")
        assert "/nonexistent/missing.md" in result

    def test_returns_placeholder_for_empty_path(self) -> None:
        attachment = _inline_attachment(document_name="no-path.md", path="")
        result = PromptDispatcher._load_attachment_content(attachment)
        assert "_Content unavailable:" in result
        assert "no-path.md" in result

    def test_loads_multiline_content(self, tmp_path) -> None:
        doc_file = tmp_path / "multi.md"
        content = "# Title\n\nLine 1.\nLine 2.\n\n## Section\n\nContent."
        doc_file.write_text(content)
        attachment = _inline_attachment(path=str(doc_file))
        result = PromptDispatcher._load_attachment_content(attachment)
        assert result == content
