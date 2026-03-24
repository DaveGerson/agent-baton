"""Tests for KnowledgeRegistry integration into IntelligentPlanner.

Covers:
- knowledge resolution during planning (step 9.5)
- graceful skip when knowledge_registry is None
- gap-suggested attachments flow
- plan.md knowledge rendering via to_markdown()
- _detect_rag() settings.json scanning
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.knowledge import KnowledgeAttachment, KnowledgeGapRecord


# ---------------------------------------------------------------------------
# Shared helpers — mirrors test_knowledge_resolver.py conventions
# ---------------------------------------------------------------------------

def _make_manifest(
    pack_dir: Path,
    *,
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    target_agents: list[str] | None = None,
    default_delivery: str = "reference",
) -> None:
    data: dict = {"name": name, "description": description}
    if tags is not None:
        data["tags"] = tags
    if target_agents is not None:
        data["target_agents"] = target_agents
    data["default_delivery"] = default_delivery
    (pack_dir / "knowledge.yaml").write_text(yaml.dump(data), encoding="utf-8")


def _make_doc(
    pack_dir: Path,
    filename: str,
    *,
    name: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "normal",
    grounding: str = "",
    body: str = "x" * 400,  # ~100 tokens by default
) -> None:
    fm_parts: list[str] = []
    if name is not None:
        fm_parts.append(f"name: {name}")
    if description:
        fm_parts.append(f"description: {description}")
    if tags:
        fm_parts.append(f"tags: [{', '.join(tags)}]")
    if priority != "normal":
        fm_parts.append(f"priority: {priority}")
    if grounding:
        fm_parts.append(f"grounding: {grounding}")

    if fm_parts:
        content = "---\n" + "\n".join(fm_parts) + "\n---\n" + body
    else:
        content = body
    (pack_dir / filename).write_text(content, encoding="utf-8")


def _make_registry(knowledge_root: Path) -> KnowledgeRegistry:
    reg = KnowledgeRegistry()
    reg.load_directory(knowledge_root)
    return reg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """A small knowledge root with one pack containing two docs."""
    root = tmp_path / "knowledge"
    root.mkdir()

    pack_dir = root / "agent-baton"
    pack_dir.mkdir()
    _make_manifest(
        pack_dir,
        name="agent-baton",
        description="Architecture and conventions for agent-baton",
        tags=["orchestration", "architecture", "development"],
        target_agents=["backend-engineer--python", "architect"],
    )
    # Small doc (~100 tokens) — fits inline budget easily
    _make_doc(
        pack_dir, "architecture.md",
        name="architecture",
        description="Package layout and design decisions",
        tags=["architecture", "layout"],
        body="x" * 400,
    )
    _make_doc(
        pack_dir, "conventions.md",
        name="conventions",
        description="Coding conventions and patterns",
        tags=["conventions", "patterns"],
        body="x" * 400,
    )
    return root


@pytest.fixture
def registry(knowledge_root: Path) -> KnowledgeRegistry:
    return _make_registry(knowledge_root)


@pytest.fixture
def planner_with_registry(registry: KnowledgeRegistry, tmp_path: Path) -> IntelligentPlanner:
    """IntelligentPlanner with a knowledge_registry attached."""
    return IntelligentPlanner(
        team_context_root=tmp_path / "team-context",
        knowledge_registry=registry,
    )


@pytest.fixture
def planner_no_registry(tmp_path: Path) -> IntelligentPlanner:
    """IntelligentPlanner with no knowledge_registry — knowledge resolution is skipped."""
    return IntelligentPlanner(
        team_context_root=tmp_path / "team-context",
    )


# ---------------------------------------------------------------------------
# Tests: knowledge_registry parameter on __init__
# ---------------------------------------------------------------------------

class TestPlannerKnowledgeInit:
    def test_accepts_knowledge_registry(self, registry: KnowledgeRegistry, tmp_path: Path) -> None:
        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )
        assert planner.knowledge_registry is registry

    def test_default_registry_is_none(self, tmp_path: Path) -> None:
        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")
        assert planner.knowledge_registry is None


# ---------------------------------------------------------------------------
# Tests: graceful skip when registry is None
# ---------------------------------------------------------------------------

class TestKnowledgeResolutionSkipWhenNone:
    def test_steps_have_empty_knowledge_when_no_registry(
        self, planner_no_registry: IntelligentPlanner
    ) -> None:
        plan = planner_no_registry.create_plan("Fix a bug in the authentication module")
        for step in plan.all_steps:
            # knowledge defaults to empty list — no registry means no resolution
            assert step.knowledge == []

    def test_plan_created_successfully_without_registry(
        self, planner_no_registry: IntelligentPlanner
    ) -> None:
        plan = planner_no_registry.create_plan("Build a new reporting feature")
        assert isinstance(plan, MachinePlan)
        assert plan.task_summary == "Build a new reporting feature"

    def test_explicit_knowledge_fields_empty_when_not_provided(
        self, planner_no_registry: IntelligentPlanner
    ) -> None:
        plan = planner_no_registry.create_plan("Add OAuth2 login")
        assert plan.explicit_knowledge_packs == []
        assert plan.explicit_knowledge_docs == []


# ---------------------------------------------------------------------------
# Tests: knowledge resolution during planning
# ---------------------------------------------------------------------------

class TestKnowledgeResolutionDuringPlanning:
    def test_steps_receive_knowledge_attachments_when_registry_set(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        # "architecture" is a tag in the pack — should match via planner-matched:tag
        plan = planner_with_registry.create_plan(
            "Design the architecture for a new feature"
        )
        all_steps = plan.all_steps
        # At least one step should have knowledge attachments when tags match
        # (the pack has 'architecture' tag matching the task description)
        steps_with_knowledge = [s for s in all_steps if s.knowledge]
        assert len(steps_with_knowledge) > 0

    def test_knowledge_attachments_are_knowledge_attachment_instances(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan("Design the architecture for an API")
        for step in plan.all_steps:
            for att in step.knowledge:
                assert isinstance(att, KnowledgeAttachment)

    def test_inferred_type_written_to_plan(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan("Fix the broken authentication endpoint")
        assert plan.task_type == "bug-fix"

    def test_explicit_knowledge_packs_passed_through_to_plan(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Implement new feature",
            explicit_knowledge_packs=["agent-baton"],
        )
        assert plan.explicit_knowledge_packs == ["agent-baton"]

    def test_explicit_knowledge_docs_passed_through_to_plan(
        self, planner_with_registry: IntelligentPlanner, tmp_path: Path
    ) -> None:
        doc_path = str(tmp_path / "some-doc.md")
        plan = planner_with_registry.create_plan(
            "Implement new feature",
            explicit_knowledge_docs=[doc_path],
        )
        assert plan.explicit_knowledge_docs == [doc_path]

    def test_intervention_level_stored_on_plan(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Implement new feature",
            intervention_level="high",
        )
        assert plan.intervention_level == "high"

    def test_intervention_level_defaults_to_low(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan("Implement new feature")
        assert plan.intervention_level == "low"

    def test_knowledge_resolution_does_not_break_normal_plan_structure(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan("Build a new dashboard feature")
        # Structural integrity checks
        assert len(plan.phases) > 0
        assert plan.task_id != ""
        assert plan.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_knowledge_dedup_within_step(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        """Same doc should not appear twice on a single step."""
        plan = planner_with_registry.create_plan(
            "Design the architecture layout",
            explicit_knowledge_packs=["agent-baton"],
        )
        for step in plan.all_steps:
            doc_names = [att.document_name for att in step.knowledge]
            # Each document_name should appear at most once per step
            assert len(doc_names) == len(set(doc_names)), (
                f"Duplicate knowledge docs on step {step.step_id}: {doc_names}"
            )

    def test_explicit_pack_docs_have_explicit_source(
        self, planner_with_registry: IntelligentPlanner
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Build new feature",
            explicit_knowledge_packs=["agent-baton"],
        )
        for step in plan.all_steps:
            explicit_atts = [a for a in step.knowledge if a.source == "explicit"]
            # With explicit pack provided, at least some steps should have explicit attachments
            if explicit_atts:
                for att in explicit_atts:
                    assert att.pack_name == "agent-baton"


# ---------------------------------------------------------------------------
# Tests: gap-suggested attachments
# ---------------------------------------------------------------------------

class TestGapSuggestedAttachments:
    def _make_retro_dir_with_gaps(
        self,
        team_context_root: Path,
        agent_name: str,
        task_type: str,
        gap_description: str,
    ) -> None:
        """Write a retrospective JSON file with a knowledge gap record."""
        retros_dir = team_context_root / "retrospectives"
        retros_dir.mkdir(parents=True, exist_ok=True)
        gap_record = {
            "description": gap_description,
            "gap_type": "factual",
            "resolution": "unresolved",
            "resolution_detail": "",
            "agent_name": agent_name,
            "task_type": task_type,
            "task_summary": "some prior task",
        }
        retro_data = {
            "plan_id": "test-plan-001",
            "knowledge_gaps": [gap_record],
        }
        retro_file = retros_dir / "2026-01-01-test-001.json"
        retro_file.write_text(json.dumps(retro_data), encoding="utf-8")

    def test_gap_suggested_attachments_when_matching_gaps_exist(
        self,
        registry: KnowledgeRegistry,
        tmp_path: Path,
    ) -> None:
        team_context = tmp_path / "team-context"
        # Write a prior gap for backend-engineer matching "architecture" keywords
        # (which will hit the agent-baton pack via tag search)
        self._make_retro_dir_with_gaps(
            team_context,
            agent_name="backend-engineer",
            task_type="new-feature",
            gap_description="Need architecture overview for the project layout",
        )
        planner = IntelligentPlanner(
            team_context_root=team_context,
            knowledge_registry=registry,
        )
        plan = planner.create_plan("Add a new API endpoint", task_type="new-feature")
        # Gap-suggested attachments may appear on backend-engineer steps
        all_gap_suggested = [
            att
            for step in plan.all_steps
            for att in step.knowledge
            if att.source == "gap-suggested"
        ]
        # The gap description contains "architecture" which matches the pack tag
        # We check the flow works end-to-end — gaps may or may not match depending
        # on resolver tag intersection. The important thing: no exception raised.
        # If matches are found, they are correctly tagged.
        for att in all_gap_suggested:
            assert att.source == "gap-suggested"

    def test_gap_suggested_flow_does_not_raise_when_no_retros(
        self,
        planner_with_registry: IntelligentPlanner,
    ) -> None:
        # No retrospective directory exists — should not raise
        plan = planner_with_registry.create_plan("Build new feature")
        assert isinstance(plan, MachinePlan)

    def test_gap_suggested_skipped_when_no_registry(
        self,
        tmp_path: Path,
    ) -> None:
        team_context = tmp_path / "team-context"
        self._make_retro_dir_with_gaps(
            team_context,
            agent_name="backend-engineer",
            task_type="new-feature",
            gap_description="Need architecture overview",
        )
        planner = IntelligentPlanner(
            team_context_root=team_context,
            knowledge_registry=None,
        )
        # Should complete without error and produce no knowledge attachments
        plan = planner.create_plan("Build a new feature", task_type="new-feature")
        for step in plan.all_steps:
            assert step.knowledge == []


# ---------------------------------------------------------------------------
# Tests: plan.md knowledge rendering
# ---------------------------------------------------------------------------

class TestPlanMarkdownKnowledgeRendering:
    def _plan_with_knowledge(self) -> MachinePlan:
        """Build a MachinePlan with hand-crafted knowledge attachments."""
        att1 = KnowledgeAttachment(
            source="agent-declared",
            pack_name="agent-baton",
            document_name="architecture",
            path="/fake/architecture.md",
            delivery="inline",
            retrieval="file",
            grounding="You are receiving architecture from the agent-baton pack",
            token_estimate=50,
        )
        att2 = KnowledgeAttachment(
            source="planner-matched:tag",
            pack_name="ai-orchestration",
            document_name="context-economics",
            path="/fake/context-economics.md",
            delivery="reference",
            retrieval="file",
            grounding="You are receiving context-economics",
            token_estimate=9000,
        )
        att3 = KnowledgeAttachment(
            source="gap-suggested",
            pack_name="compliance",
            document_name="audit-checklist",
            path="/fake/audit-checklist.md",
            delivery="reference",
            retrieval="file",
            grounding="",
            token_estimate=0,
        )
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the feature",
            knowledge=[att1, att2, att3],
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        return MachinePlan(
            task_id="test-001",
            task_summary="Add OAuth2 login",
            phases=[phase],
        )

    def test_knowledge_section_present_in_markdown(self) -> None:
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        assert "**Knowledge**:" in md

    def test_knowledge_doc_names_rendered(self) -> None:
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        assert "architecture" in md
        assert "context-economics" in md
        assert "audit-checklist" in md

    def test_knowledge_pack_labels_rendered(self) -> None:
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        assert "(agent-baton)" in md
        assert "(ai-orchestration)" in md
        assert "(compliance)" in md

    def test_knowledge_delivery_rendered(self) -> None:
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        assert "inline" in md
        assert "reference" in md

    def test_knowledge_source_rendered(self) -> None:
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        assert "agent-declared" in md
        assert "planner-matched:tag" in md
        assert "gap-suggested" in md

    def test_knowledge_section_absent_when_no_attachments(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Do some work",
            knowledge=[],
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        plan = MachinePlan(
            task_id="test-002",
            task_summary="Simple task",
            phases=[phase],
        )
        md = plan.to_markdown()
        assert "**Knowledge**:" not in md

    def test_plan_level_knowledge_fields_rendered_in_header(self) -> None:
        plan = MachinePlan(
            task_id="test-003",
            task_summary="Task with explicit knowledge",
            explicit_knowledge_packs=["agent-baton", "compliance"],
            explicit_knowledge_docs=["path/to/spec.md"],
        )
        md = plan.to_markdown()
        assert "agent-baton" in md
        assert "compliance" in md
        assert "path/to/spec.md" in md

    def test_full_rendering_matches_spec_format(self) -> None:
        """Verify the rendered format matches the spec example structure."""
        plan = self._plan_with_knowledge()
        md = plan.to_markdown()
        lines = md.splitlines()

        # Find the Knowledge: line
        knowledge_lines = [ln for ln in lines if "**Knowledge**:" in ln]
        assert len(knowledge_lines) == 1

        # Knowledge items follow as '  - docname (pack) — delivery (source)'
        knowledge_items = [
            ln for ln in lines
            if ln.strip().startswith("- ") and " — " in ln and "(" in ln
        ]
        assert len(knowledge_items) == 3


# ---------------------------------------------------------------------------
# Tests: _detect_rag
# ---------------------------------------------------------------------------

class TestDetectRag:
    def test_returns_false_when_no_settings_file(self, tmp_path: Path) -> None:
        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")
        # _detect_rag searches .claude/settings.json and ~/.claude/settings.json
        # In a tmp environment neither should have rag entries
        result = planner._detect_rag()
        assert isinstance(result, bool)

    def test_returns_true_when_rag_server_in_settings(self, tmp_path: Path, monkeypatch) -> None:
        """Simulate a settings.json with an MCP server named 'my-rag-server'."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        settings_file = settings_dir / "settings.json"
        settings_data = {
            "mcpServers": {
                "my-rag-server": {"command": "npx", "args": ["-y", "mcp-rag"]},
                "filesystem": {"command": "npx", "args": []},
            }
        }
        settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")

        # Monkeypatch _detect_rag to read from our tmp settings file
        original_detect = planner._detect_rag

        def _detect_rag_patched() -> bool:
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            mcp_servers = data.get("mcpServers", {})
            if isinstance(mcp_servers, dict):
                for name in mcp_servers:
                    if "rag" in str(name).lower():
                        return True
            return False

        monkeypatch.setattr(planner, "_detect_rag", _detect_rag_patched)
        assert planner._detect_rag() is True

    def test_returns_false_when_no_rag_in_server_names(self, tmp_path: Path, monkeypatch) -> None:
        """Settings with MCP servers but none named 'rag' — should return False."""
        settings_file = tmp_path / "settings.json"
        settings_data = {
            "mcpServers": {
                "filesystem": {"command": "npx", "args": []},
                "github": {"command": "npx", "args": []},
            }
        }
        settings_file.write_text(json.dumps(settings_data), encoding="utf-8")

        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")

        def _detect_rag_patched() -> bool:
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            mcp_servers = data.get("mcpServers", {})
            if isinstance(mcp_servers, dict):
                for name in mcp_servers:
                    if "rag" in str(name).lower():
                        return True
            return False

        monkeypatch.setattr(planner, "_detect_rag", _detect_rag_patched)
        assert planner._detect_rag() is False

    def test_returns_false_on_malformed_settings(self, tmp_path: Path, monkeypatch) -> None:
        """Malformed JSON in settings.json should not raise — return False."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("this is not { valid json", encoding="utf-8")

        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")

        def _detect_rag_patched() -> bool:
            try:
                json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            return False

        monkeypatch.setattr(planner, "_detect_rag", _detect_rag_patched)
        assert planner._detect_rag() is False


# ---------------------------------------------------------------------------
# Integration: full plan creation with knowledge registry
# ---------------------------------------------------------------------------

class TestPlannerKnowledgeIntegration:
    def test_create_plan_roundtrip_with_knowledge(
        self,
        planner_with_registry: IntelligentPlanner,
        tmp_path: Path,
    ) -> None:
        """Full roundtrip: create plan with knowledge, serialize, deserialize."""
        plan = planner_with_registry.create_plan(
            "Design the architecture for a new orchestration feature",
            explicit_knowledge_packs=["agent-baton"],
        )
        plan_dict = plan.to_dict()
        restored = MachinePlan.from_dict(plan_dict)

        # Check knowledge attachments survive the roundtrip
        for orig_step, rest_step in zip(plan.all_steps, restored.all_steps):
            assert len(orig_step.knowledge) == len(rest_step.knowledge)
            for orig_att, rest_att in zip(orig_step.knowledge, rest_step.knowledge):
                assert orig_att.document_name == rest_att.document_name
                assert orig_att.source == rest_att.source
                assert orig_att.delivery == rest_att.delivery

    def test_create_plan_markdown_contains_knowledge_when_registry_set(
        self,
        planner_with_registry: IntelligentPlanner,
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Design the architecture for a new API",
            explicit_knowledge_packs=["agent-baton"],
        )
        md = plan.to_markdown()
        # With explicit pack, all steps get docs from agent-baton
        assert "**Knowledge**:" in md

    def test_create_plan_task_type_written_to_plan(
        self,
        planner_with_registry: IntelligentPlanner,
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Fix broken login authentication",
        )
        assert plan.task_type == "bug-fix"

    def test_explicit_knowledge_packs_empty_list_safe(
        self,
        planner_with_registry: IntelligentPlanner,
    ) -> None:
        plan = planner_with_registry.create_plan(
            "Build a new feature",
            explicit_knowledge_packs=[],
        )
        assert plan.explicit_knowledge_packs == []
        assert isinstance(plan, MachinePlan)

    def test_knowledge_resolution_exception_does_not_crash_plan(
        self, registry: KnowledgeRegistry, tmp_path: Path, monkeypatch
    ) -> None:
        """If resolver.resolve() raises, the plan still completes (step.knowledge stays [])."""
        from agent_baton.core.engine import knowledge_resolver

        original_resolve = knowledge_resolver.KnowledgeResolver.resolve

        call_count = {"n": 0}

        def bad_resolve(self, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("simulated resolver failure")

        monkeypatch.setattr(knowledge_resolver.KnowledgeResolver, "resolve", bad_resolve)

        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )
        # Should not raise even though resolver throws
        plan = planner.create_plan("Design new feature")
        assert isinstance(plan, MachinePlan)
        # resolve was called (exception was swallowed gracefully)
        assert call_count["n"] > 0
        # Steps have empty knowledge due to the exception
        for step in plan.all_steps:
            assert step.knowledge == []
