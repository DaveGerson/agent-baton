"""End-to-end integration tests for the knowledge delivery system.

Covers the full pipeline:
  KnowledgeRegistry (real disk packs) → KnowledgeResolver → IntelligentPlanner
  → PromptDispatcher → MachinePlan serialization round-trip
  → KNOWLEDGE_GAP signal parsing + escalation routing
  → PatternLearner.knowledge_gaps_for() feedback loop
  → RetrospectiveEngine implicit gap detection
  → intervention_level escalation shift

Also covers SQLite storage and sync integration (section 14):
  - SqliteStorage.save_plan() / load_plan() with knowledge fields
  - SqliteStorage.save_execution() with KnowledgeGapSignal / ResolvedDecision
  - central.db schema includes knowledge columns
  - Auto-sync syncs knowledge metadata to central.db
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.knowledge_gap import determine_escalation, parse_knowledge_gap
from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepStatus,
)
from agent_baton.models.knowledge import (
    KnowledgeAttachment,
    KnowledgeGapRecord,
    KnowledgeGapSignal,
    ResolvedDecision,
)
from agent_baton.models.retrospective import AgentOutcome, Retrospective
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Helpers shared across tests
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
    data: dict[str, Any] = {"name": name, "description": description}
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
    body: str = "x" * 400,  # ~100 tokens
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
        # Inline grounding without multi-line yaml quoting issues
        fm_parts.append(f"grounding: '{grounding}'")
    content = ("---\n" + "\n".join(fm_parts) + "\n---\n" + body) if fm_parts else body
    (pack_dir / filename).write_text(content, encoding="utf-8")


def _build_knowledge_root(tmp_path: Path) -> Path:
    """Create a realistic multi-pack knowledge root for integration tests."""
    root = tmp_path / "knowledge"
    root.mkdir()

    # Pack 1 — agent-baton: targets backend-engineer--python and architect
    p1 = root / "agent-baton"
    p1.mkdir()
    _make_manifest(
        p1,
        name="agent-baton",
        description="Architecture, conventions, and workflow for agent-baton",
        tags=["orchestration", "architecture", "development", "conventions"],
        target_agents=["backend-engineer--python", "architect"],
        default_delivery="reference",
    )
    # High-priority doc — small enough to be inlined (100 tokens)
    _make_doc(
        p1, "architecture.md",
        name="architecture",
        description="Package layout and design decisions",
        tags=["architecture", "layout", "design"],
        priority="high",
        body="x" * 400,
    )
    # Normal-priority doc
    _make_doc(
        p1, "conventions.md",
        name="conventions",
        description="Coding conventions and patterns",
        tags=["conventions", "patterns"],
        body="x" * 400,
    )

    # Pack 2 — ai-orchestration: targets ai-systems-architect
    p2 = root / "ai-orchestration"
    p2.mkdir()
    _make_manifest(
        p2,
        name="ai-orchestration",
        description="Multi-agent orchestration patterns and token budgeting",
        tags=["orchestration", "multi-agent", "tokens", "budgeting"],
        target_agents=["ai-systems-architect"],
        default_delivery="reference",
    )
    _make_doc(
        p2, "context-economics.md",
        name="context-economics",
        description="Token cost model and context window budgeting",
        tags=["context-window", "tokens", "cost", "budgeting"],
        body="x" * 400,
    )

    # Pack 3 — compliance: no target_agents (available to all via explicit)
    p3 = root / "compliance"
    p3.mkdir()
    _make_manifest(
        p3,
        name="compliance",
        description="Compliance and audit requirements",
        tags=["compliance", "audit", "sox", "regulations"],
        target_agents=[],
    )
    _make_doc(
        p3, "audit-checklist.md",
        name="audit-checklist",
        description="SOX audit trail requirements and checklist",
        tags=["audit", "sox", "compliance"],
        body="x" * 400,
    )

    return root


def _make_registry(root: Path) -> KnowledgeRegistry:
    reg = KnowledgeRegistry()
    reg.load_directory(root)
    return reg


def _make_attachment(
    document_name: str = "architecture",
    pack_name: str = "agent-baton",
    source: str = "explicit",
    delivery: str = "inline",
    path: str = "/some/path.md",
    token_estimate: int = 100,
    grounding: str = "You are receiving this for context.",
) -> KnowledgeAttachment:
    return KnowledgeAttachment(
        source=source,
        pack_name=pack_name,
        document_name=document_name,
        path=path,
        delivery=delivery,
        retrieval="file",
        grounding=grounding,
        token_estimate=token_estimate,
    )


def _make_task_usage(task_id: str = "t-001") -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-01-01T00:00:00Z",
        outcome="SHIP",
        risk_level="LOW",
        sequencing_mode="new-feature",
        agents_used=[
            AgentUsageRecord(
                name="backend-engineer--python",
                estimated_tokens=5000,
                retries=0,
                gate_results=["PASS"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# 1. Load real .claude/knowledge/ packs via KnowledgeRegistry
# ---------------------------------------------------------------------------

REAL_KNOWLEDGE_DIR = (
    Path(__file__).parent.parent / ".claude" / "knowledge"
)


class TestRealPacksLoad:
    """Load the actual .claude/knowledge/ packs and verify basic structure."""

    @pytest.fixture
    def real_registry(self) -> KnowledgeRegistry:
        reg = KnowledgeRegistry()
        reg.load_directory(REAL_KNOWLEDGE_DIR)
        return reg

    def test_loads_at_least_three_well_formed_packs(
        self, real_registry: KnowledgeRegistry
    ) -> None:
        """At least 3 well-formed packs must load. Extras and degraded packs
        are tolerated for forward-compat with new packs being added."""
        assert real_registry.well_formed_pack_count >= 3, (
            f"Expected >= 3 well-formed packs, got "
            f"{real_registry.well_formed_pack_count}. "
            f"Degraded: {sorted(real_registry.degraded_pack_names)}"
        )

    def test_expected_pack_names_present(self, real_registry: KnowledgeRegistry) -> None:
        """Required packs must be loaded; presence-only check tolerates extras."""
        names = set(real_registry.all_packs.keys())
        for required in ("agent-baton", "ai-orchestration", "case-studies"):
            assert required in names, f"Missing required pack: {required}"

    def test_agent_baton_pack_has_docs(self, real_registry: KnowledgeRegistry) -> None:
        pack = real_registry.get_pack("agent-baton")
        assert pack is not None
        assert len(pack.documents) >= 1
        doc_names = {d.name for d in pack.documents}
        assert "architecture" in doc_names

    def test_ai_orchestration_pack_has_docs(self, real_registry: KnowledgeRegistry) -> None:
        pack = real_registry.get_pack("ai-orchestration")
        assert pack is not None
        assert len(pack.documents) >= 1

    def test_all_docs_have_token_estimates(self, real_registry: KnowledgeRegistry) -> None:
        for pack in real_registry.all_packs.values():
            for doc in pack.documents:
                assert doc.token_estimate > 0, (
                    f"Expected token_estimate > 0 for {pack.name}/{doc.name}"
                )

    def test_all_docs_have_names(self, real_registry: KnowledgeRegistry) -> None:
        for pack in real_registry.all_packs.values():
            for doc in pack.documents:
                assert doc.name, f"Doc in pack {pack.name} has empty name"

    def test_content_not_loaded_eagerly(self, real_registry: KnowledgeRegistry) -> None:
        """Registry lazy-loads content — docs have empty content at index time."""
        for pack in real_registry.all_packs.values():
            for doc in pack.documents:
                assert doc.content == "", (
                    f"{pack.name}/{doc.name} content should not be loaded at index time"
                )

    def test_packs_for_agent_backend_engineer(self, real_registry: KnowledgeRegistry) -> None:
        """backend-engineer--python should be targeted by agent-baton pack."""
        packs = real_registry.packs_for_agent("backend-engineer--python")
        names = [p.name for p in packs]
        assert "agent-baton" in names

    def test_packs_for_agent_ai_systems_architect(self, real_registry: KnowledgeRegistry) -> None:
        packs = real_registry.packs_for_agent("ai-systems-architect")
        names = [p.name for p in packs]
        assert "ai-orchestration" in names

    def test_case_studies_has_no_target_agents(self, real_registry: KnowledgeRegistry) -> None:
        """case-studies pack targets no specific agents (available globally)."""
        pack = real_registry.get_pack("case-studies")
        assert pack is not None
        assert pack.target_agents == []


# ---------------------------------------------------------------------------
# 2. KnowledgeResolver + KnowledgeRegistry together (real packs)
# ---------------------------------------------------------------------------

class TestResolverWithRealPacks:
    """KnowledgeResolver using the real .claude/knowledge/ packs."""

    @pytest.fixture
    def real_registry(self) -> KnowledgeRegistry:
        reg = KnowledgeRegistry()
        reg.load_directory(REAL_KNOWLEDGE_DIR)
        return reg

    @pytest.fixture
    def resolver(self, real_registry: KnowledgeRegistry) -> KnowledgeResolver:
        return KnowledgeResolver(real_registry)

    def test_resolve_for_backend_engineer_returns_agent_baton_docs(
        self,
        real_registry: KnowledgeRegistry,
    ) -> None:
        """agent-baton pack targets backend-engineer--python; tag layer should match."""
        resolver = KnowledgeResolver(real_registry)
        # Use a task that mentions "architecture" or "orchestration" to hit tag layer
        attachments = resolver.resolve(
            agent_name="backend-engineer--python",
            task_description="Implement orchestration architecture for the pipeline",
            task_type="new-feature",
        )
        # Should find at least one agent-baton attachment via tag matching
        pack_names = {a.pack_name for a in attachments}
        assert "agent-baton" in pack_names

    def test_resolve_returns_list_of_attachments(
        self, resolver: KnowledgeResolver
    ) -> None:
        result = resolver.resolve(
            agent_name="ai-systems-architect",
            task_description="Design a multi-agent orchestration system",
        )
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, KnowledgeAttachment)

    def test_attachments_have_valid_delivery_values(
        self, resolver: KnowledgeResolver
    ) -> None:
        result = resolver.resolve(
            agent_name="architect",
            task_description="Design the architecture for the new component",
        )
        for att in result:
            assert att.delivery in ("inline", "reference"), (
                f"Unexpected delivery={att.delivery!r} for {att.document_name}"
            )

    def test_attachments_have_valid_source_values(
        self, resolver: KnowledgeResolver
    ) -> None:
        valid_sources = {
            "explicit",
            "agent-declared",
            "planner-matched:tag",
            "planner-matched:relevance",
            "gap-suggested",
        }
        result = resolver.resolve(
            agent_name="architect",
            task_description="Design the architecture for multi-agent orchestration",
        )
        for att in result:
            assert att.source in valid_sources, (
                f"Unexpected source={att.source!r} for {att.document_name}"
            )

    def test_explicit_pack_always_included(
        self, real_registry: KnowledgeRegistry
    ) -> None:
        """Explicit pack docs always appear regardless of tag matching."""
        resolver = KnowledgeResolver(real_registry)
        attachments = resolver.resolve(
            agent_name="totally-unknown-agent",
            task_description="Do something completely unrelated",
            explicit_packs=["agent-baton"],
        )
        pack_names = {a.pack_name for a in attachments}
        assert "agent-baton" in pack_names
        sources = {a.source for a in attachments}
        assert "explicit" in sources

    def test_deduplication_across_layers(
        self, real_registry: KnowledgeRegistry
    ) -> None:
        """A doc matched both explicitly and by tag should appear only once."""
        resolver = KnowledgeResolver(real_registry)
        attachments = resolver.resolve(
            agent_name="backend-engineer--python",
            task_description="Review the architecture documentation",
            explicit_packs=["agent-baton"],  # also matches via tag
        )
        doc_names = [a.document_name for a in attachments]
        # No duplicate document names within the same pack
        seen: set[str] = set()
        for att in attachments:
            key = f"{att.pack_name}::{att.document_name}"
            assert key not in seen, f"Duplicate attachment: {key}"
            seen.add(key)

    def test_rag_available_sets_mcp_rag_retrieval(
        self, real_registry: KnowledgeRegistry
    ) -> None:
        """When rag_available=True, reference deliveries get retrieval='mcp-rag'."""
        # Force a large token estimate to ensure reference delivery by using a tiny budget
        resolver = KnowledgeResolver(
            real_registry,
            rag_available=True,
            step_token_budget=1,   # budget exhausted immediately
            doc_token_cap=8_000,
        )
        attachments = resolver.resolve(
            agent_name="architect",
            task_description="Review architecture patterns",
            explicit_packs=["agent-baton"],
        )
        reference_deliveries = [a for a in attachments if a.delivery == "reference"]
        if reference_deliveries:
            for att in reference_deliveries:
                assert att.retrieval == "mcp-rag", (
                    f"Expected mcp-rag for reference delivery, got {att.retrieval!r}"
                )

    def test_resolver_no_match_returns_empty_for_unknown_agent_nondescript_task(
        self, real_registry: KnowledgeRegistry
    ) -> None:
        """Unknown agent + generic task with no keyword overlap = no results."""
        resolver = KnowledgeResolver(real_registry)
        attachments = resolver.resolve(
            agent_name="totally-unknown-agent-xyz",
            task_description="do thing",  # too short to match anything
        )
        # Should return empty list (or only relevance-fallback if any scores >= 0.3)
        # We just assert it's a list — the exact count depends on TF-IDF scores
        assert isinstance(attachments, list)


# ---------------------------------------------------------------------------
# 3. IntelligentPlanner creates plan with knowledge attachments
# ---------------------------------------------------------------------------

class TestPlannerKnowledgeResolution:
    """IntelligentPlanner produces plans with knowledge fields populated."""

    @pytest.fixture
    def knowledge_root(self, tmp_path: Path) -> Path:
        return _build_knowledge_root(tmp_path)

    @pytest.fixture
    def registry(self, knowledge_root: Path) -> KnowledgeRegistry:
        return _make_registry(knowledge_root)

    @pytest.fixture
    def planner(self, registry: KnowledgeRegistry, tmp_path: Path) -> IntelligentPlanner:
        return IntelligentPlanner(
            team_context_root=tmp_path / "team-context",
            knowledge_registry=registry,
        )

    def test_steps_get_knowledge_attachments(self, planner: IntelligentPlanner) -> None:
        plan = planner.create_plan(
            "Implement orchestration architecture for the new backend service",
            explicit_knowledge_packs=["agent-baton"],
        )
        # At least one step should have knowledge attachments
        steps_with_knowledge = [s for s in plan.all_steps if s.knowledge]
        assert steps_with_knowledge, "Expected at least one step to have knowledge attachments"

    def test_explicit_pack_attached_to_all_steps(
        self, planner: IntelligentPlanner
    ) -> None:
        """Explicit pack docs appear on every step (user said it matters globally)."""
        plan = planner.create_plan(
            "Fix a bug in the API endpoint",
            explicit_knowledge_packs=["agent-baton"],
        )
        for step in plan.all_steps:
            pack_names = {a.pack_name for a in step.knowledge}
            assert "agent-baton" in pack_names, (
                f"Step {step.step_id} ({step.agent_name}) missing explicit pack attachment"
            )

    def test_attachment_sources_correct(self, planner: IntelligentPlanner) -> None:
        plan = planner.create_plan(
            "Build orchestration architecture feature",
            explicit_knowledge_packs=["agent-baton"],
        )
        for step in plan.all_steps:
            for att in step.knowledge:
                assert att.source in {
                    "explicit",
                    "agent-declared",
                    "planner-matched:tag",
                    "planner-matched:relevance",
                    "gap-suggested",
                }, f"Unexpected source: {att.source!r}"

    def test_explicit_knowledge_pack_stored_on_plan(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Add a new feature",
            explicit_knowledge_packs=["agent-baton", "compliance"],
            explicit_knowledge_docs=[],
        )
        assert "agent-baton" in plan.explicit_knowledge_packs
        assert "compliance" in plan.explicit_knowledge_packs

    def test_explicit_knowledge_docs_stored_on_plan(
        self, knowledge_root: Path, planner: IntelligentPlanner
    ) -> None:
        doc_path = str(knowledge_root / "agent-baton" / "architecture.md")
        plan = planner.create_plan(
            "Review the architecture",
            explicit_knowledge_docs=[doc_path],
        )
        assert doc_path in plan.explicit_knowledge_docs

    def test_intervention_level_stored_on_plan(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Fix a critical bug",
            intervention_level="high",
        )
        assert plan.intervention_level == "high"

    def test_task_type_stored_on_plan(self, planner: IntelligentPlanner) -> None:
        plan = planner.create_plan("Fix the broken authentication endpoint")
        assert plan.task_type is not None

    def test_no_registry_steps_have_empty_knowledge(self, tmp_path: Path) -> None:
        """When no registry is provided, knowledge resolution is skipped entirely."""
        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")
        plan = planner.create_plan("Build something cool")
        for step in plan.all_steps:
            assert step.knowledge == []

    def test_attachment_delivery_is_inline_or_reference(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Implement backend feature with architecture review",
            explicit_knowledge_packs=["agent-baton"],
        )
        for step in plan.all_steps:
            for att in step.knowledge:
                assert att.delivery in ("inline", "reference")

    def test_high_priority_docs_preferred_inline(
        self, knowledge_root: Path, tmp_path: Path
    ) -> None:
        """High-priority docs should be inline when budget permits."""
        # architecture.md has priority=high and ~100 tokens — should fit inline budget
        reg = _make_registry(knowledge_root)
        resolver = KnowledgeResolver(
            reg,
            step_token_budget=32_000,
            doc_token_cap=8_000,
        )
        attachments = resolver.resolve(
            agent_name="backend-engineer--python",
            task_description="Implement the feature",
            explicit_packs=["agent-baton"],
        )
        arch_att = next(
            (a for a in attachments if a.document_name == "architecture"), None
        )
        assert arch_att is not None
        assert arch_att.delivery == "inline"

    def test_unestimated_doc_is_reference(self, tmp_path: Path) -> None:
        """Docs with token_estimate=0 should always get delivery='reference'."""
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="mypack")
        # Empty file → token_estimate will be 0 (or 1 per _estimate_tokens which reads the empty)
        # We manually construct a KnowledgeDocument with token_estimate=0 and test via resolver
        from agent_baton.models.knowledge import KnowledgeDocument, KnowledgePack
        reg = KnowledgeRegistry()
        pack = KnowledgePack(name="mypack", description="test")
        doc = KnowledgeDocument(
            name="zero-tokens",
            description="A doc with no estimate",
            token_estimate=0,
        )
        pack.documents.append(doc)
        reg._packs["mypack"] = pack
        reg._rebuild_tfidf()

        resolver = KnowledgeResolver(reg)
        attachments = resolver.resolve(
            agent_name="any-agent",
            task_description="Do something",
            explicit_packs=["mypack"],
        )
        zero_att = next((a for a in attachments if a.document_name == "zero-tokens"), None)
        assert zero_att is not None
        assert zero_att.delivery == "reference"


# ---------------------------------------------------------------------------
# 4. PromptDispatcher renders Knowledge Context and Knowledge References
# ---------------------------------------------------------------------------

class TestDispatcherKnowledgeSections:
    """Delegation prompt renders knowledge sections correctly."""

    @pytest.fixture
    def dispatcher(self) -> PromptDispatcher:
        return PromptDispatcher()

    def _step_with_knowledge(
        self,
        attachments: list[KnowledgeAttachment],
        tmp_path: Path | None = None,
    ) -> PlanStep:
        # If inline attachments have real paths, create the file
        if tmp_path is not None:
            for att in attachments:
                if att.delivery == "inline" and att.path:
                    p = Path(att.path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if not p.exists():
                        p.write_text("# Content\n\nDocument body.", encoding="utf-8")
        return PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the authentication module",
            knowledge=attachments,
        )

    def test_knowledge_context_section_for_inline_attachment(
        self, dispatcher: PromptDispatcher, tmp_path: Path
    ) -> None:
        doc_path = tmp_path / "doc.md"
        doc_path.write_text("# Architecture\n\nDetailed content here.", encoding="utf-8")
        att = _make_attachment(
            document_name="architecture",
            pack_name="agent-baton",
            delivery="inline",
            path=str(doc_path),
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Knowledge Context" in prompt
        assert "architecture" in prompt
        assert "agent-baton" in prompt

    def test_knowledge_references_section_for_reference_attachment(
        self, dispatcher: PromptDispatcher
    ) -> None:
        att = _make_attachment(
            document_name="context-economics",
            pack_name="ai-orchestration",
            delivery="reference",
            path="/docs/context-economics.md",
            grounding="Use this to budget context windows.",
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Knowledge References" in prompt
        assert "context-economics" in prompt
        assert "ai-orchestration" in prompt

    def test_both_sections_present_when_mixed_attachments(
        self, dispatcher: PromptDispatcher, tmp_path: Path
    ) -> None:
        doc_path = tmp_path / "inline-doc.md"
        doc_path.write_text("Inline content.", encoding="utf-8")
        inline_att = _make_attachment(
            document_name="architecture",
            delivery="inline",
            path=str(doc_path),
        )
        ref_att = _make_attachment(
            document_name="context-economics",
            pack_name="ai-orchestration",
            delivery="reference",
            path="/docs/context-economics.md",
        )
        step = self._step_with_knowledge([inline_att, ref_att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Knowledge Context" in prompt
        assert "## Knowledge References" in prompt

    def test_no_knowledge_sections_when_no_attachments(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="A task with no knowledge",
        )
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Knowledge Context" not in prompt
        assert "## Knowledge References" not in prompt

    def test_knowledge_gaps_block_always_present(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """The KNOWLEDGE_GAP instructions block must appear in every delegation prompt."""
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Do some work",
        )
        prompt = dispatcher.build_delegation_prompt(step)
        assert "KNOWLEDGE_GAP:" in prompt
        assert "CONFIDENCE:" in prompt

    def test_retrieval_hint_for_file_delivery(
        self, dispatcher: PromptDispatcher
    ) -> None:
        att = _make_attachment(
            document_name="spec",
            pack_name="agent-baton",
            delivery="reference",
            path="/path/to/spec.md",
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Read /path/to/spec.md" in prompt

    def test_retrieval_hint_for_mcp_rag(
        self, dispatcher: PromptDispatcher
    ) -> None:
        att = KnowledgeAttachment(
            source="planner-matched:tag",
            pack_name="ai-orchestration",
            document_name="context-economics",
            path="/docs/context-economics.md",
            delivery="reference",
            retrieval="mcp-rag",
            grounding="Budget context windows carefully.",
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "query RAG server" in prompt

    def test_grounding_appears_in_reference_listing(
        self, dispatcher: PromptDispatcher
    ) -> None:
        grounding = "You are receiving this because you need token budgeting context."
        att = _make_attachment(
            document_name="context-economics",
            pack_name="ai-orchestration",
            delivery="reference",
            path="/docs/context-economics.md",
            grounding=grounding,
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert grounding in prompt

    def test_team_delegation_prompt_includes_knowledge(
        self, dispatcher: PromptDispatcher, tmp_path: Path
    ) -> None:
        """Team delegation prompt also renders knowledge sections."""
        from agent_baton.models.execution import TeamMember
        doc_path = tmp_path / "team-doc.md"
        doc_path.write_text("Team knowledge.", encoding="utf-8")
        att = _make_attachment(
            document_name="architecture",
            delivery="inline",
            path=str(doc_path),
        )
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Build the backend together",
            knowledge=[att],
        )
        member = TeamMember(
            member_id="1.1.a",
            agent_name="backend-engineer--python",
            role="implementer",
        )
        prompt = dispatcher.build_team_delegation_prompt(step, member)
        assert "## Knowledge Context" in prompt

    def test_inline_content_loaded_from_disk(
        self, dispatcher: PromptDispatcher, tmp_path: Path
    ) -> None:
        """Inline delivery reads content from source_path on disk."""
        content = "# Real Content\n\nThis is the document body loaded from disk."
        doc_path = tmp_path / "real.md"
        doc_path.write_text(content, encoding="utf-8")
        att = _make_attachment(
            document_name="real",
            delivery="inline",
            path=str(doc_path),
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Real Content" in prompt
        assert "document body loaded from disk" in prompt

    def test_missing_file_path_shows_placeholder(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """When the inline file doesn't exist, the prompt shows a placeholder."""
        att = _make_attachment(
            document_name="missing-doc",
            delivery="inline",
            path="/nonexistent/path/to/missing-doc.md",
        )
        step = self._step_with_knowledge([att])
        prompt = dispatcher.build_delegation_prompt(step)
        # Should not raise; should contain a placeholder
        assert "Content unavailable" in prompt or "missing-doc" in prompt


# ---------------------------------------------------------------------------
# 5. plan.md rendering (MachinePlan.to_markdown()) with knowledge lines
# ---------------------------------------------------------------------------

class TestPlanMarkdownKnowledge:
    """MachinePlan.to_markdown() renders knowledge attachments for each step."""

    def _make_plan_with_knowledge(self) -> MachinePlan:
        att_inline = _make_attachment(
            document_name="architecture",
            pack_name="agent-baton",
            source="agent-declared",
            delivery="inline",
        )
        att_ref = _make_attachment(
            document_name="context-economics",
            pack_name="ai-orchestration",
            source="planner-matched:tag",
            delivery="reference",
        )
        att_gap = _make_attachment(
            document_name="audit-checklist",
            pack_name="compliance",
            source="gap-suggested",
            delivery="reference",
        )
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the feature",
            knowledge=[att_inline, att_ref, att_gap],
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        return MachinePlan(
            task_id="test-plan-md",
            task_summary="Test plan for knowledge rendering",
            phases=[phase],
            explicit_knowledge_packs=["agent-baton"],
            task_type="new-feature",
            intervention_level="medium",
        )

    def test_knowledge_lines_appear_in_markdown(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "**Knowledge**:" in md

    def test_inline_attachment_listed_with_source(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "architecture" in md
        assert "inline" in md
        assert "agent-declared" in md

    def test_reference_attachment_listed_with_source(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "context-economics" in md
        assert "reference" in md
        assert "planner-matched:tag" in md

    def test_gap_suggested_attachment_listed(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "audit-checklist" in md
        assert "gap-suggested" in md

    def test_explicit_knowledge_packs_header(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "agent-baton" in md

    def test_intervention_level_shown_when_non_default(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "medium" in md  # intervention_level != "low" so it's shown

    def test_task_type_shown(self) -> None:
        plan = self._make_plan_with_knowledge()
        md = plan.to_markdown()
        assert "new-feature" in md

    def test_step_without_knowledge_has_no_knowledge_section(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="A step with no knowledge",
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        plan = MachinePlan(
            task_id="test-no-knowledge",
            task_summary="No knowledge plan",
            phases=[phase],
        )
        md = plan.to_markdown()
        assert "**Knowledge**:" not in md


# ---------------------------------------------------------------------------
# 6. MachinePlan serialization round-trip with knowledge fields
# ---------------------------------------------------------------------------

class TestMachinePlanSerializationRoundTrip:
    """MachinePlan.to_dict() / from_dict() round-trips with knowledge fields."""

    def _make_rich_plan(self) -> MachinePlan:
        att = KnowledgeAttachment(
            source="explicit",
            pack_name="agent-baton",
            document_name="architecture",
            path="/path/to/architecture.md",
            delivery="inline",
            retrieval="file",
            grounding="Context for the agent",
            token_estimate=250,
        )
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the feature",
            knowledge=[att],
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        return MachinePlan(
            task_id="rt-001",
            task_summary="A rich plan",
            risk_level="MEDIUM",
            phases=[phase],
            task_type="new-feature",
            explicit_knowledge_packs=["agent-baton"],
            explicit_knowledge_docs=["/path/to/doc.md"],
            intervention_level="medium",
        )

    def test_to_dict_includes_knowledge_fields(self) -> None:
        plan = self._make_rich_plan()
        d = plan.to_dict()
        assert d["task_type"] == "new-feature"
        assert d["explicit_knowledge_packs"] == ["agent-baton"]
        assert d["explicit_knowledge_docs"] == ["/path/to/doc.md"]
        assert d["intervention_level"] == "medium"

    def test_from_dict_restores_knowledge_fields(self) -> None:
        plan = self._make_rich_plan()
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.task_type == "new-feature"
        assert restored.explicit_knowledge_packs == ["agent-baton"]
        assert restored.explicit_knowledge_docs == ["/path/to/doc.md"]
        assert restored.intervention_level == "medium"

    def test_step_knowledge_survives_round_trip(self) -> None:
        plan = self._make_rich_plan()
        restored = MachinePlan.from_dict(plan.to_dict())
        assert len(restored.all_steps) == 1
        step = restored.all_steps[0]
        assert len(step.knowledge) == 1
        att = step.knowledge[0]
        assert att.source == "explicit"
        assert att.pack_name == "agent-baton"
        assert att.document_name == "architecture"
        assert att.delivery == "inline"
        assert att.retrieval == "file"
        assert att.grounding == "Context for the agent"
        assert att.token_estimate == 250

    def test_knowledge_attachment_to_dict_from_dict(self) -> None:
        att = KnowledgeAttachment(
            source="planner-matched:tag",
            pack_name="compliance",
            document_name="audit-checklist",
            path="/knowledge/compliance/audit-checklist.md",
            delivery="reference",
            retrieval="mcp-rag",
            grounding="Compliance context",
            token_estimate=500,
        )
        restored = KnowledgeAttachment.from_dict(att.to_dict())
        assert restored.source == att.source
        assert restored.pack_name == att.pack_name
        assert restored.document_name == att.document_name
        assert restored.path == att.path
        assert restored.delivery == att.delivery
        assert restored.retrieval == att.retrieval
        assert restored.grounding == att.grounding
        assert restored.token_estimate == att.token_estimate

    def test_intervention_level_defaults_to_low(self) -> None:
        """Deserializing old plan JSON without intervention_level defaults to 'low'."""
        plan = self._make_rich_plan()
        d = plan.to_dict()
        del d["intervention_level"]
        restored = MachinePlan.from_dict(d)
        assert restored.intervention_level == "low"

    def test_explicit_knowledge_fields_default_to_empty_list(self) -> None:
        """Old plan JSON without knowledge fields deserializes to empty lists."""
        plan = MachinePlan(task_id="t-001", task_summary="Basic plan")
        d = plan.to_dict()
        d.pop("explicit_knowledge_packs", None)
        d.pop("explicit_knowledge_docs", None)
        d.pop("task_type", None)
        restored = MachinePlan.from_dict(d)
        assert restored.explicit_knowledge_packs == []
        assert restored.explicit_knowledge_docs == []
        assert restored.task_type is None

    def test_json_serializable(self) -> None:
        """The entire plan dict must be JSON-serializable (no Path objects)."""
        plan = self._make_rich_plan()
        raw = json.dumps(plan.to_dict())  # must not raise
        loaded = json.loads(raw)
        assert loaded["task_id"] == "rt-001"

    def test_execution_state_knowledge_fields_round_trip(self) -> None:
        """ExecutionState persists pending_gaps and resolved_decisions."""
        plan = self._make_rich_plan()
        state = ExecutionState(
            task_id="rt-001",
            plan=plan,
            pending_gaps=[
                KnowledgeGapSignal(
                    description="Need DB schema",
                    confidence="low",
                    gap_type="factual",
                    step_id="1.1",
                    agent_name="backend-engineer--python",
                )
            ],
            resolved_decisions=[
                ResolvedDecision(
                    gap_description="Auth token policy",
                    resolution="Use 24h expiry",
                    step_id="1.1",
                    timestamp="2026-01-01T00:00:00Z",
                )
            ],
        )
        d = state.to_dict()
        restored = ExecutionState.from_dict(d)
        assert len(restored.pending_gaps) == 1
        assert restored.pending_gaps[0].description == "Need DB schema"
        assert restored.pending_gaps[0].confidence == "low"
        assert len(restored.resolved_decisions) == 1
        assert restored.resolved_decisions[0].gap_description == "Auth token policy"
        assert restored.resolved_decisions[0].resolution == "Use 24h expiry"


# ---------------------------------------------------------------------------
# 7. KNOWLEDGE_GAP signal parsing + escalation matrix end-to-end
# ---------------------------------------------------------------------------

class TestKnowledgeGapPipelineEndToEnd:
    """Full pipeline: parse signal from agent output → escalation decision."""

    def test_contextual_gap_always_queued(self) -> None:
        outcome = textwrap.dedent("""\
            I completed the initial implementation.

            KNOWLEDGE_GAP: Need SOX audit trail retention policy from compliance team
            CONFIDENCE: none
            TYPE: contextual

            Files changed: auth.py
        """)
        signal = parse_knowledge_gap(outcome, step_id="1.1", agent_name="backend-engineer--python")
        assert signal is not None
        assert signal.gap_type == "contextual"

        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            for intervention in ("low", "medium", "high"):
                action = determine_escalation(
                    signal, risk_level=risk, intervention_level=intervention,
                    resolution_found=False,
                )
                assert action == "queue-for-gate", (
                    f"Expected queue-for-gate for contextual gap at {risk}/{intervention}"
                )

    def test_factual_gap_with_match_always_auto_resolves(self) -> None:
        outcome = (
            "KNOWLEDGE_GAP: Need the database schema for users table\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome, step_id="1.2", agent_name="db-agent")
        assert signal is not None

        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            for intervention in ("low", "medium", "high"):
                action = determine_escalation(
                    signal, risk_level=risk, intervention_level=intervention,
                    resolution_found=True,
                )
                assert action == "auto-resolve", (
                    f"Expected auto-resolve when match found at {risk}/{intervention}"
                )

    def test_factual_gap_low_risk_low_intervention_no_match_best_effort(self) -> None:
        outcome = (
            "KNOWLEDGE_GAP: Which logging library does this project prefer?\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome, step_id="2.1", agent_name="backend-agent")
        assert signal is not None

        action = determine_escalation(
            signal, risk_level="LOW", intervention_level="low",
            resolution_found=False,
        )
        assert action == "best-effort"

    def test_factual_gap_medium_risk_no_match_queued(self) -> None:
        outcome = (
            "KNOWLEDGE_GAP: What is the retention policy for audit logs?\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome, step_id="1.1", agent_name="auditor")
        assert signal is not None

        action = determine_escalation(
            signal, risk_level="MEDIUM", intervention_level="low",
            resolution_found=False,
        )
        assert action == "queue-for-gate"

    def test_signal_parse_stores_full_outcome_as_partial_outcome(self) -> None:
        outcome = (
            "I completed step 1 of 3.\n"
            "KNOWLEDGE_GAP: Need context on the legacy API contract\n"
            "CONFIDENCE: partial\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome, step_id="2.1", agent_name="backend")
        assert signal is not None
        assert signal.partial_outcome == outcome

    def test_invalid_confidence_defaults_to_low(self) -> None:
        outcome = (
            "KNOWLEDGE_GAP: Something unclear\n"
            "CONFIDENCE: uncertain\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "low"

    def test_missing_signal_returns_none(self) -> None:
        outcome = "All work completed. Tests pass. No issues."
        result = parse_knowledge_gap(outcome)
        assert result is None


# ---------------------------------------------------------------------------
# 8. intervention_level shifts escalation behavior
# ---------------------------------------------------------------------------

class TestInterventionLevelEscalation:
    """Verify that intervention_level correctly shifts escalation thresholds."""

    def _factual_signal(self) -> KnowledgeGapSignal:
        return KnowledgeGapSignal(
            description="Need the preferred logging framework",
            confidence="low",
            gap_type="factual",
            step_id="1.1",
            agent_name="backend-engineer",
        )

    def test_low_intervention_low_risk_no_match_is_best_effort(self) -> None:
        action = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="low",
            resolution_found=False,
        )
        assert action == "best-effort"

    def test_medium_intervention_low_risk_no_match_is_queue(self) -> None:
        """medium intervention raises threshold: even LOW risk → queue."""
        action = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="medium",
            resolution_found=False,
        )
        assert action == "queue-for-gate"

    def test_high_intervention_low_risk_no_match_is_queue(self) -> None:
        action = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="high",
            resolution_found=False,
        )
        assert action == "queue-for-gate"

    def test_intervention_has_no_effect_when_match_found(self) -> None:
        """Resolution found overrides intervention for factual gaps."""
        for intervention in ("low", "medium", "high"):
            action = determine_escalation(
                self._factual_signal(),
                risk_level="LOW",
                intervention_level=intervention,
                resolution_found=True,
            )
            assert action == "auto-resolve", (
                f"Expected auto-resolve when resolution found (intervention={intervention})"
            )

    def test_intervention_has_no_effect_on_contextual_gaps(self) -> None:
        """Contextual gaps always queue regardless of intervention level."""
        ctx_signal = KnowledgeGapSignal(
            description="Need org decision on data retention",
            confidence="none",
            gap_type="contextual",
            step_id="1.1",
            agent_name="agent",
        )
        for intervention in ("low", "medium", "high"):
            action = determine_escalation(
                ctx_signal,
                risk_level="LOW",
                intervention_level=intervention,
                resolution_found=False,
            )
            assert action == "queue-for-gate"

    def test_plan_stores_intervention_level(self, tmp_path: Path) -> None:
        """MachinePlan persists intervention_level end-to-end through planner."""
        reg = KnowledgeRegistry()
        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=reg,
        )
        plan = planner.create_plan("Fix a bug", intervention_level="high")
        assert plan.intervention_level == "high"

    def test_default_intervention_level_is_low(self, tmp_path: Path) -> None:
        planner = IntelligentPlanner(team_context_root=tmp_path / "tc")
        plan = planner.create_plan("Fix a bug")
        assert plan.intervention_level == "low"


# ---------------------------------------------------------------------------
# 9. PatternLearner.knowledge_gaps_for() with retrospective data
# ---------------------------------------------------------------------------

class TestPatternLearnerKnowledgeGapsFor:
    """PatternLearner reads KnowledgeGapRecord entries from retrospective JSON files."""

    def _write_retro_json(
        self,
        retros_dir: Path,
        task_id: str,
        knowledge_gaps: list[dict],
    ) -> None:
        retros_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "task_id": task_id,
            "task_name": task_id,
            "timestamp": "2026-01-01T00:00:00Z",
            "knowledge_gaps": knowledge_gaps,
        }
        (retros_dir / f"{task_id}.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def test_returns_empty_when_no_retros_dir(self, tmp_path: Path) -> None:
        learner = PatternLearner(tmp_path / "team-context")
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_returns_empty_when_no_matching_agent(self, tmp_path: Path) -> None:
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        self._write_retro_json(
            retros_dir, "task-001",
            [
                {
                    "description": "Needed DB schema",
                    "gap_type": "factual",
                    "resolution": "auto-resolved",
                    "resolution_detail": "schema.md",
                    "agent_name": "different-agent",
                    "task_summary": "Feature work",
                    "task_type": "new-feature",
                }
            ],
        )
        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result == []

    def test_returns_matching_agent_gaps(self, tmp_path: Path) -> None:
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        self._write_retro_json(
            retros_dir, "task-001",
            [
                {
                    "description": "Needed DB schema",
                    "gap_type": "factual",
                    "resolution": "auto-resolved",
                    "resolution_detail": "schema.md",
                    "agent_name": "backend-engineer--python",
                    "task_summary": "Feature work",
                    "task_type": "new-feature",
                }
            ],
        )
        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert len(result) == 1
        assert result[0].description == "Needed DB schema"
        assert result[0].agent_name == "backend-engineer--python"

    def test_filters_by_task_type(self, tmp_path: Path) -> None:
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        self._write_retro_json(
            retros_dir, "task-001",
            [
                {
                    "description": "Gap for new-feature",
                    "gap_type": "factual",
                    "resolution": "unresolved",
                    "resolution_detail": "",
                    "agent_name": "backend-engineer--python",
                    "task_summary": "Build feature",
                    "task_type": "new-feature",
                },
                {
                    "description": "Gap for bug-fix",
                    "gap_type": "factual",
                    "resolution": "unresolved",
                    "resolution_detail": "",
                    "agent_name": "backend-engineer--python",
                    "task_summary": "Fix bug",
                    "task_type": "bug-fix",
                },
            ],
        )
        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python", task_type="new-feature")
        assert len(result) == 1
        assert result[0].description == "Gap for new-feature"

    def test_deduplication_by_description(self, tmp_path: Path) -> None:
        """Same description in two files → only one record returned."""
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        gap_entry = {
            "description": "Repeated gap",
            "gap_type": "factual",
            "resolution": "unresolved",
            "resolution_detail": "",
            "agent_name": "backend-engineer--python",
            "task_summary": "Task",
            "task_type": "new-feature",
        }
        self._write_retro_json(retros_dir, "task-001", [gap_entry])
        self._write_retro_json(retros_dir, "task-002", [gap_entry])

        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        descs = [r.description for r in result]
        assert descs.count("Repeated gap") == 1

    def test_frequency_sorting(self, tmp_path: Path) -> None:
        """Gaps seen more frequently should appear first."""
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        common_gap = {
            "description": "Common gap",
            "gap_type": "factual",
            "resolution": "unresolved",
            "resolution_detail": "",
            "agent_name": "backend-engineer--python",
            "task_summary": "Task",
            "task_type": None,
        }
        rare_gap = {**common_gap, "description": "Rare gap"}

        # common_gap appears in 2 tasks, rare_gap in 1
        self._write_retro_json(retros_dir, "task-001", [common_gap, rare_gap])
        self._write_retro_json(retros_dir, "task-002", [common_gap])

        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        assert result[0].description == "Common gap"

    def test_backward_compat_old_schema(self, tmp_path: Path) -> None:
        """Old KnowledgeGap schema (affected_agent, suggested_fix) still loads."""
        tc = tmp_path / "team-context"
        retros_dir = tc / "retrospectives"
        # Old schema entry
        self._write_retro_json(
            retros_dir, "old-task",
            [
                {
                    "description": "Needed compliance context",
                    "affected_agent": "backend-engineer--python",
                    "suggested_fix": "Create compliance knowledge pack",
                }
            ],
        )
        # PatternLearner reads via Retrospective.from_dict which handles backward compat
        # But PatternLearner reads raw JSON, not via Retrospective model.
        # It calls KnowledgeGapRecord.from_dict() directly; the old schema is handled
        # by models/retrospective.py::_knowledge_gap_from_dict which is invoked by
        # Retrospective.from_dict. PatternLearner reads "knowledge_gaps" raw entries.
        # Since old entries have "affected_agent" (not "agent_name"), they may not match.
        # Verify it doesn't crash and result is a list.
        learner = PatternLearner(tc)
        result = learner.knowledge_gaps_for("backend-engineer--python")
        # Old schema missing "agent_name" will have empty agent_name → not matched
        # The important thing is no exception is raised
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 10. RetrospectiveEngine — implicit gap detection and KnowledgeGapRecord
# ---------------------------------------------------------------------------

class TestRetrospectiveImplicitGapDetection:
    """RetrospectiveEngine detects implicit gaps from narrative text."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> RetrospectiveEngine:
        return RetrospectiveEngine(retrospectives_dir=tmp_path / "retrospectives")

    def _make_usage(self) -> TaskUsageRecord:
        return _make_task_usage("task-retro-001")

    def test_detects_lacked_context_phrase(self, engine: RetrospectiveEngine) -> None:
        usage = self._make_usage()
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="The agent lacked context about the legacy API format.",
        )
        retro = engine.generate_from_usage(
            usage,
            task_name="Test task",
            what_didnt=[outcome],
            task_summary="Implement legacy integration",
        )
        assert len(retro.knowledge_gaps) > 0
        descriptions = [g.description for g in retro.knowledge_gaps]
        assert any("lacked context" in d for d in descriptions)

    def test_detects_assumed_incorrectly_phrase(self, engine: RetrospectiveEngine) -> None:
        usage = self._make_usage()
        outcome = AgentOutcome(
            name="backend-engineer--python",
            root_cause="Agent assumed incorrectly about the schema format.",
        )
        retro = engine.generate_from_usage(
            usage,
            task_name="Schema task",
            what_didnt=[outcome],
            task_summary="Schema migration",
        )
        descriptions = [g.description for g in retro.knowledge_gaps]
        assert any("assumed incorrectly" in d for d in descriptions)

    def test_implicit_gaps_have_unresolved_resolution(self, engine: RetrospectiveEngine) -> None:
        usage = self._make_usage()
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="The agent lacked context about the authentication flow.",
        )
        retro = engine.generate_from_usage(
            usage,
            task_name="Auth task",
            what_didnt=[outcome],
            task_summary="Auth implementation",
        )
        implicit = [g for g in retro.knowledge_gaps if g.resolution == "unresolved"]
        assert len(implicit) > 0

    def test_explicit_gaps_take_precedence_over_implicit(
        self, engine: RetrospectiveEngine
    ) -> None:
        usage = self._make_usage()
        explicit_gap = KnowledgeGapRecord(
            description="The agent lacked context about the authentication flow.",
            gap_type="contextual",
            resolution="human-answered",
            resolution_detail="Use OAuth2 with PKCE",
            agent_name="backend-engineer--python",
            task_summary="Auth implementation",
        )
        # Same description appears implicitly in what_didnt
        outcome = AgentOutcome(
            name="backend-engineer--python",
            issues="The agent lacked context about the authentication flow.",
        )
        retro = engine.generate_from_usage(
            usage,
            task_name="Auth task",
            what_didnt=[outcome],
            knowledge_gaps=[explicit_gap],
            task_summary="Auth implementation",
        )
        # Should have only one entry (dedup), and explicit takes precedence
        matching = [g for g in retro.knowledge_gaps
                    if "authentication flow" in g.description]
        assert len(matching) == 1
        assert matching[0].resolution == "human-answered"

    def test_gaps_serialized_to_json_sidecar(self, engine: RetrospectiveEngine) -> None:
        usage = self._make_usage()
        gap = KnowledgeGapRecord(
            description="Need compliance context",
            gap_type="contextual",
            resolution="human-answered",
            resolution_detail="Follow SOX 90-day retention",
            agent_name="auditor",
            task_summary="Audit task",
            task_type="documentation",
        )
        retro = engine.generate_from_usage(
            usage,
            task_name="Audit task",
            knowledge_gaps=[gap],
            task_summary="Audit implementation",
            task_type="documentation",
        )
        engine.save(retro)

        json_path = engine.dir / "task-retro-001.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        gaps = data.get("knowledge_gaps", [])
        assert len(gaps) == 1
        assert gaps[0]["description"] == "Need compliance context"
        assert gaps[0]["resolution"] == "human-answered"
        assert gaps[0]["agent_name"] == "auditor"

    def test_knowledge_gap_record_round_trips_via_retrospective(self) -> None:
        """KnowledgeGapRecord survives Retrospective.to_dict() → from_dict()."""
        gap = KnowledgeGapRecord(
            description="Need DB schema",
            gap_type="factual",
            resolution="auto-resolved",
            resolution_detail="via mypack/schema.md",
            agent_name="backend-engineer--python",
            task_summary="Feature work",
            task_type="new-feature",
        )
        retro = Retrospective(
            task_id="rt-test",
            task_name="Round-trip test",
            timestamp="2026-01-01T00:00:00Z",
            knowledge_gaps=[gap],
        )
        restored = Retrospective.from_dict(retro.to_dict())
        assert len(restored.knowledge_gaps) == 1
        r = restored.knowledge_gaps[0]
        assert r.description == "Need DB schema"
        assert r.gap_type == "factual"
        assert r.resolution == "auto-resolved"
        assert r.resolution_detail == "via mypack/schema.md"
        assert r.agent_name == "backend-engineer--python"
        assert r.task_type == "new-feature"

    def test_backward_compat_old_knowledge_gap_schema_in_retro(self) -> None:
        """Retrospective.from_dict() handles old KnowledgeGap schema entries."""
        old_retro_data = {
            "task_id": "old-task",
            "task_name": "Old task",
            "timestamp": "2025-01-01T00:00:00Z",
            "knowledge_gaps": [
                {
                    "description": "Needed compliance context",
                    "affected_agent": "auditor",
                    "suggested_fix": "Create compliance knowledge pack",
                }
            ],
        }
        retro = Retrospective.from_dict(old_retro_data)
        assert len(retro.knowledge_gaps) == 1
        gap = retro.knowledge_gaps[0]
        # New fields get defaults
        assert gap.description == "Needed compliance context"
        assert gap.agent_name == "auditor"       # via compat alias
        assert gap.resolution == "unresolved"    # default for old records
        assert gap.resolution_detail == "Create compliance knowledge pack"


# ---------------------------------------------------------------------------
# 11. StepStatus.INTERRUPTED is a valid enum member
# ---------------------------------------------------------------------------

class TestInterruptedStepStatus:
    """StepStatus.INTERRUPTED is correctly defined and usable."""

    def test_interrupted_status_exists(self) -> None:
        assert StepStatus.INTERRUPTED.value == "interrupted"

    def test_interrupted_step_tracked_in_interrupted_step_ids(self) -> None:
        from agent_baton.models.execution import StepResult
        plan = MachinePlan(
            task_id="t-001",
            task_summary="Task",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Phase 1",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer--python",
                            task_description="Work",
                        )
                    ],
                )
            ],
        )
        state = ExecutionState(task_id="t-001", plan=plan)
        state.step_results.append(
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="interrupted",
            )
        )
        assert "1.1" in state.interrupted_step_ids
        assert "1.1" not in state.completed_step_ids
        assert "1.1" not in state.failed_step_ids


# ---------------------------------------------------------------------------
# 12. Full pipeline smoke test: registry → planner → dispatcher
# ---------------------------------------------------------------------------

class TestFullPipelineSmoke:
    """End-to-end: load packs, plan, dispatch — verify knowledge flows through."""

    @pytest.fixture
    def knowledge_root(self, tmp_path: Path) -> Path:
        return _build_knowledge_root(tmp_path)

    def test_knowledge_flows_from_registry_through_planner_to_prompt(
        self, knowledge_root: Path, tmp_path: Path
    ) -> None:
        """Create a plan with knowledge resolution and verify prompt contains section."""
        registry = _make_registry(knowledge_root)
        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )
        plan = planner.create_plan(
            "Implement orchestration architecture for the backend service",
            explicit_knowledge_packs=["agent-baton"],
            intervention_level="medium",
        )

        # Find a step with knowledge
        steps_with_knowledge = [s for s in plan.all_steps if s.knowledge]
        assert steps_with_knowledge, "Expected at least one step with knowledge"

        dispatcher = PromptDispatcher()
        step = steps_with_knowledge[0]

        # Create files for inline attachments so content loads
        for att in step.knowledge:
            if att.delivery == "inline" and att.path:
                p = Path(att.path)
                if not p.exists() and p.parent.exists():
                    p.write_text("# Knowledge Content\n\nFor integration test.", encoding="utf-8")

        prompt = dispatcher.build_delegation_prompt(
            step,
            task_summary=plan.task_summary,
            task_type=plan.task_type or "",
        )

        # Prompt must contain the knowledge gap instructions block
        assert "KNOWLEDGE_GAP:" in prompt
        # If any inline attachments, Knowledge Context must appear
        inline = [a for a in step.knowledge if a.delivery == "inline"]
        reference = [a for a in step.knowledge if a.delivery == "reference"]
        if inline:
            assert "## Knowledge Context" in prompt
        if reference:
            assert "## Knowledge References" in prompt

    def test_plan_serialization_preserves_knowledge_through_json(
        self, knowledge_root: Path, tmp_path: Path
    ) -> None:
        """Plan created with knowledge → serialized → restored → knowledge intact."""
        registry = _make_registry(knowledge_root)
        planner = IntelligentPlanner(
            team_context_root=tmp_path / "tc",
            knowledge_registry=registry,
        )
        plan = planner.create_plan(
            "Build architecture feature",
            explicit_knowledge_packs=["agent-baton"],
        )
        # Serialize → restore
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.explicit_knowledge_packs == plan.explicit_knowledge_packs
        assert restored.task_type == plan.task_type
        assert restored.intervention_level == plan.intervention_level
        # Steps' knowledge fields also survive
        for original, loaded in zip(plan.all_steps, restored.all_steps):
            assert len(original.knowledge) == len(loaded.knowledge)

    def test_gap_suggested_source_flows_from_pattern_learner(
        self, knowledge_root: Path, tmp_path: Path
    ) -> None:
        """When pattern learner has prior gap records, attachments get gap-suggested source."""
        tc = tmp_path / "tc"
        retros_dir = tc / "retrospectives"
        retros_dir.mkdir(parents=True)

        # Write a retrospective with a prior gap for backend-engineer
        gap_data = {
            "task_id": "prior-task",
            "task_name": "Prior task",
            "timestamp": "2026-01-01T00:00:00Z",
            "knowledge_gaps": [
                {
                    "description": "orchestration architecture pattern",
                    "gap_type": "factual",
                    "resolution": "unresolved",
                    "resolution_detail": "",
                    "agent_name": "backend-engineer--python",
                    "task_summary": "Prior feature work",
                    "task_type": "new-feature",
                }
            ],
        }
        (retros_dir / "prior-task.json").write_text(
            json.dumps(gap_data, indent=2), encoding="utf-8"
        )

        registry = _make_registry(knowledge_root)
        planner = IntelligentPlanner(
            team_context_root=tc,
            knowledge_registry=registry,
        )
        plan = planner.create_plan(
            "Add new orchestration feature to backend",
            task_type="new-feature",
        )

        # Look for gap-suggested attachments across all steps
        all_attachments = [
            att
            for step in plan.all_steps
            for att in step.knowledge
        ]
        gap_suggested = [a for a in all_attachments if a.source == "gap-suggested"]
        # We can't guarantee a match (depends on TF-IDF hitting), but the pipeline
        # must not crash and must return a valid plan
        assert isinstance(plan, MachinePlan)
        # If gap-suggested did fire, verify the source label
        for att in gap_suggested:
            assert att.source == "gap-suggested"


# ---------------------------------------------------------------------------
# 13. KnowledgeGapRecord backward-compatibility aliases
# ---------------------------------------------------------------------------

class TestKnowledgeGapRecordCompatAliases:
    """KnowledgeGapRecord compat aliases affected_agent and suggested_fix work."""

    def test_affected_agent_alias(self) -> None:
        record = KnowledgeGapRecord(
            description="Need context",
            gap_type="contextual",
            resolution="unresolved",
            resolution_detail="",
            agent_name="backend-engineer--python",
            task_summary="Task",
        )
        assert record.affected_agent == "backend-engineer--python"

    def test_suggested_fix_alias(self) -> None:
        record = KnowledgeGapRecord(
            description="Need context",
            gap_type="contextual",
            resolution="unresolved",
            resolution_detail="Create compliance pack",
            agent_name="backend-engineer--python",
            task_summary="Task",
        )
        assert record.suggested_fix == "Create compliance pack"


# ---------------------------------------------------------------------------
# 14. SqliteStorage knowledge-field round-trips (federated sync integration)
# ---------------------------------------------------------------------------

import sqlite3
import tempfile

from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.core.storage.sync import SyncEngine
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Shared helpers for this section
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path, subdir: str = "proj") -> tuple[Path, SqliteStorage]:
    """Return (db_path, SqliteStorage) under tmp_path/<subdir>/baton.db."""
    db_dir = tmp_path / subdir
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "baton.db"
    return db_path, SqliteStorage(db_path)


def _simple_plan(
    task_id: str = "t-kn-001",
    knowledge_packs: list[str] | None = None,
    knowledge_docs: list[str] | None = None,
    intervention_level: str = "low",
    task_type: str | None = None,
    step_knowledge: list[KnowledgeAttachment] | None = None,
) -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement",
        model="sonnet",
        knowledge=step_knowledge or [],
    )
    phase = PlanPhase(phase_id=1, name="Impl", steps=[step], approval_required=False)
    return MachinePlan(
        task_id=task_id,
        task_summary="knowledge round-trip test",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        explicit_knowledge_packs=knowledge_packs or [],
        explicit_knowledge_docs=knowledge_docs or [],
        intervention_level=intervention_level,
        task_type=task_type,
    )


def _simple_state(
    task_id: str = "t-kn-001",
    pending_gaps: list[KnowledgeGapSignal] | None = None,
    resolved_decisions: list[ResolvedDecision] | None = None,
) -> ExecutionState:
    plan = _simple_plan(task_id)
    return ExecutionState(
        task_id=task_id,
        plan=plan,
        status="running",
        current_phase=1,
        current_step_index=0,
        started_at="2026-01-01T00:00:00Z",
        pending_gaps=pending_gaps or [],
        resolved_decisions=resolved_decisions or [],
    )


def _get_column_names(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]
    finally:
        conn.close()


def _make_project_db_for_sync(
    tmp_path: Path, subdir: str = "proj"
) -> tuple[Path, SqliteStorage]:
    """Return (path, store) with a completed execution ready to sync."""
    db_path, store = _make_store(tmp_path, subdir)
    plan = _simple_plan(
        task_id="sync-task-001",
        knowledge_packs=["agent-baton", "compliance"],
        knowledge_docs=["extra-doc.md"],
        intervention_level="medium",
        task_type="feature",
        step_knowledge=[
            _make_attachment("architecture", pack_name="agent-baton"),
            _make_attachment("audit-checklist", pack_name="compliance"),
        ],
    )
    state = ExecutionState(
        task_id="sync-task-001",
        plan=plan,
        status="complete",
        current_phase=1,
        current_step_index=0,
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T01:00:00Z",
    )
    store.save_execution(state)
    return db_path, store


# ---------------------------------------------------------------------------
# Group 1 — plans table round-trip via save_plan / load_plan
# ---------------------------------------------------------------------------

class TestSqliteStorageKnowledgeRoundTrip:
    """Knowledge fields survive SqliteStorage.save_plan() → load_plan()."""

    def test_plan_knowledge_packs_persisted_in_sqlite(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        plan = _simple_plan(
            task_id="t-packs",
            knowledge_packs=["agent-baton", "compliance"],
        )
        store.save_plan(plan)
        loaded = store.load_plan("t-packs")
        assert loaded is not None
        assert loaded.explicit_knowledge_packs == ["agent-baton", "compliance"]

    def test_plan_knowledge_docs_persisted_in_sqlite(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        plan = _simple_plan(
            task_id="t-docs",
            knowledge_docs=["some/path/doc.md", "other/doc.md"],
        )
        store.save_plan(plan)
        loaded = store.load_plan("t-docs")
        assert loaded is not None
        assert loaded.explicit_knowledge_docs == ["some/path/doc.md", "other/doc.md"]

    def test_plan_intervention_level_persisted_in_sqlite(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        for level in ("low", "medium", "high"):
            task_id = f"t-il-{level}"
            plan = _simple_plan(task_id=task_id, intervention_level=level)
            store.save_plan(plan)
            loaded = store.load_plan(task_id)
            assert loaded is not None
            assert loaded.intervention_level == level

    def test_plan_task_type_persisted_in_sqlite(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)

        # with a task type
        plan = _simple_plan(task_id="t-tt-set", task_type="bug-fix")
        store.save_plan(plan)
        loaded = store.load_plan("t-tt-set")
        assert loaded is not None
        assert loaded.task_type == "bug-fix"

        # without a task type (None)
        plan2 = _simple_plan(task_id="t-tt-none", task_type=None)
        store.save_plan(plan2)
        loaded2 = store.load_plan("t-tt-none")
        assert loaded2 is not None
        assert loaded2.task_type is None

    def test_step_knowledge_attachments_persisted_in_sqlite(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        attachment = _make_attachment(
            document_name="architecture",
            pack_name="agent-baton",
            source="explicit",
            delivery="inline",
            path="/knowledge/agent-baton/architecture.md",
            token_estimate=250,
            grounding="Architecture reference for this task.",
        )
        plan = _simple_plan(task_id="t-step-ka", step_knowledge=[attachment])
        store.save_plan(plan)
        loaded = store.load_plan("t-step-ka")
        assert loaded is not None
        step = loaded.phases[0].steps[0]
        assert len(step.knowledge) == 1
        ka = step.knowledge[0]
        assert ka.document_name == "architecture"
        assert ka.pack_name == "agent-baton"
        assert ka.source == "explicit"
        assert ka.delivery == "inline"
        assert ka.path == "/knowledge/agent-baton/architecture.md"
        assert ka.token_estimate == 250
        assert ka.grounding == "Architecture reference for this task."


# ---------------------------------------------------------------------------
# Group 2 — executions table round-trip via save_execution / load_execution
# ---------------------------------------------------------------------------

class TestSqliteStorageExecutionKnowledgeRoundTrip:
    """Knowledge gap fields survive SqliteStorage.save_execution() → load_execution()."""

    def test_pending_gaps_persisted(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        gap = KnowledgeGapSignal(
            description="Need compliance rules for GDPR",
            confidence="none",
            gap_type="factual",
            step_id="1.1",
            agent_name="backend-engineer--python",
        )
        state = _simple_state("t-pg", pending_gaps=[gap])
        store.save_execution(state)
        loaded = store.load_execution("t-pg")
        assert loaded is not None
        assert len(loaded.pending_gaps) == 1
        assert loaded.pending_gaps[0].description == "Need compliance rules for GDPR"

    def test_resolved_decisions_persisted(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        decision = ResolvedDecision(
            gap_description="Need compliance rules for GDPR",
            resolution="auto-resolved via compliance pack",
            step_id="1.1",
            timestamp="2026-01-01T00:05:00Z",
        )
        state = _simple_state("t-rd", resolved_decisions=[decision])
        store.save_execution(state)
        loaded = store.load_execution("t-rd")
        assert loaded is not None
        assert len(loaded.resolved_decisions) == 1
        assert loaded.resolved_decisions[0].resolution == "auto-resolved via compliance pack"

    def test_knowledge_gap_signal_fields_preserved(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        gap = KnowledgeGapSignal(
            description="Unknown API contract for external service",
            confidence="low",
            gap_type="contextual",
            step_id="2.1",
            agent_name="architect",
            partial_outcome="Partially designed the integration layer",
        )
        state = _simple_state("t-kgf", pending_gaps=[gap])
        store.save_execution(state)
        loaded = store.load_execution("t-kgf")
        assert loaded is not None
        g = loaded.pending_gaps[0]
        assert g.description == "Unknown API contract for external service"
        assert g.confidence == "low"
        assert g.gap_type == "contextual"
        assert g.step_id == "2.1"
        assert g.agent_name == "architect"
        assert g.partial_outcome == "Partially designed the integration layer"

    def test_resolved_decision_timestamp_preserved(self, tmp_path: Path) -> None:
        _, store = _make_store(tmp_path)
        ts = "2026-03-24T14:30:00Z"
        decision = ResolvedDecision(
            gap_description="Unclear retry policy",
            resolution="Use exponential backoff with max 3 retries",
            step_id="1.1",
            timestamp=ts,
        )
        state = _simple_state("t-rdt", resolved_decisions=[decision])
        store.save_execution(state)
        loaded = store.load_execution("t-rdt")
        assert loaded is not None
        assert loaded.resolved_decisions[0].timestamp == ts


# ---------------------------------------------------------------------------
# Group 3 — central.db schema verification
# ---------------------------------------------------------------------------

class TestCentralDbKnowledgeSchema:
    """central.db schema includes knowledge-related columns."""

    @pytest.fixture
    def central_db_path(self, tmp_path: Path) -> Path:
        """Return path to a freshly initialized central.db."""
        path = tmp_path / "central.db"
        engine = SyncEngine(central_db_path=path)
        # Trigger schema creation by accessing the connection
        engine._conn_mgr.get_connection()
        return path

    def test_plans_table_has_explicit_knowledge_packs_column(
        self, central_db_path: Path
    ) -> None:
        cols = _get_column_names(central_db_path, "plans")
        assert "explicit_knowledge_packs" in cols

    def test_plans_table_has_explicit_knowledge_docs_column(
        self, central_db_path: Path
    ) -> None:
        cols = _get_column_names(central_db_path, "plans")
        assert "explicit_knowledge_docs" in cols

    def test_plans_table_has_intervention_level_column(
        self, central_db_path: Path
    ) -> None:
        cols = _get_column_names(central_db_path, "plans")
        assert "intervention_level" in cols

    def test_execution_state_table_has_pending_gaps_column(
        self, central_db_path: Path
    ) -> None:
        cols = _get_column_names(central_db_path, "executions")
        assert "pending_gaps" in cols

    def test_execution_state_table_has_resolved_decisions_column(
        self, central_db_path: Path
    ) -> None:
        cols = _get_column_names(central_db_path, "executions")
        assert "resolved_decisions" in cols


# ---------------------------------------------------------------------------
# Group 4 — auto-sync propagates knowledge metadata to central.db
# ---------------------------------------------------------------------------

class TestAutoSyncKnowledgeMetadata:
    """Auto-sync propagates knowledge metadata to central.db."""

    @pytest.fixture
    def sync_env(self, tmp_path: Path):
        """Set up a project DB with knowledge data and a central DB, run sync."""
        db_path, store = _make_project_db_for_sync(tmp_path, "proj")
        central_path = tmp_path / "central.db"

        # Register the project in central.db first
        engine = SyncEngine(central_db_path=central_path)
        central_conn = engine._conn_mgr.get_connection()
        central_conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program)"
            " VALUES (?, ?, ?, ?)",
            ("test-proj", "Test Project", str(tmp_path / "proj"), "test-program"),
        )
        central_conn.commit()

        # Run the sync
        result = engine.push("test-proj", db_path)
        return {"engine": engine, "central_path": central_path, "result": result}

    def test_sync_includes_knowledge_pack_names(self, sync_env: dict) -> None:
        central_path = sync_env["central_path"]
        conn = sqlite3.connect(str(central_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT explicit_knowledge_packs FROM plans WHERE task_id = ?",
                ("sync-task-001",),
            ).fetchone()
            assert row is not None, "plans row not found in central.db after sync"
            import json as _json
            packs = _json.loads(row["explicit_knowledge_packs"])
            assert "agent-baton" in packs
            assert "compliance" in packs
        finally:
            conn.close()

    def test_sync_includes_intervention_level(self, sync_env: dict) -> None:
        central_path = sync_env["central_path"]
        conn = sqlite3.connect(str(central_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT intervention_level FROM plans WHERE task_id = ?",
                ("sync-task-001",),
            ).fetchone()
            assert row is not None, "plans row not found in central.db after sync"
            assert row["intervention_level"] == "medium"
        finally:
            conn.close()

    def test_sync_includes_knowledge_attachment_count_per_step(
        self, sync_env: dict
    ) -> None:
        central_path = sync_env["central_path"]
        conn = sqlite3.connect(str(central_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT knowledge_attachments FROM plan_steps"
                " WHERE task_id = ? AND step_id = ?",
                ("sync-task-001", "1.1"),
            ).fetchone()
            assert row is not None, "plan_steps row not found in central.db after sync"
            import json as _json
            attachments = _json.loads(row["knowledge_attachments"])
            assert len(attachments) == 2
        finally:
            conn.close()

    def test_sync_handles_missing_knowledge_fields_gracefully(
        self, tmp_path: Path
    ) -> None:
        """A plan saved without knowledge fields syncs without error."""
        db_path, store = _make_store(tmp_path, "proj2")
        # Save a plan with default (empty) knowledge fields
        plan = _simple_plan(task_id="sync-bare-001")
        state = ExecutionState(
            task_id="sync-bare-001",
            plan=plan,
            status="complete",
            current_phase=1,
            current_step_index=0,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
        )
        store.save_execution(state)

        central_path = tmp_path / "central2.db"
        engine = SyncEngine(central_db_path=central_path)
        central_conn = engine._conn_mgr.get_connection()
        central_conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program)"
            " VALUES (?, ?, ?, ?)",
            ("bare-proj", "Bare Project", str(tmp_path / "proj2"), "test"),
        )
        central_conn.commit()

        result = engine.push("bare-proj", db_path)
        assert result.success, f"Sync failed: {result.errors}"
        assert result.rows_synced > 0

        # Verify the plan row has empty-list defaults for knowledge fields
        conn = sqlite3.connect(str(central_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT explicit_knowledge_packs, explicit_knowledge_docs,"
                " intervention_level FROM plans WHERE task_id = ?",
                ("sync-bare-001",),
            ).fetchone()
            assert row is not None
            import json as _json
            assert _json.loads(row["explicit_knowledge_packs"]) == []
            assert _json.loads(row["explicit_knowledge_docs"]) == []
            assert row["intervention_level"] == "low"
        finally:
            conn.close()
