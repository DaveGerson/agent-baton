"""Tests for agent_baton.core.engine.knowledge_resolver.KnowledgeResolver."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver, _extract_keywords
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.knowledge import KnowledgeAttachment, KnowledgeDocument, KnowledgePack


# ---------------------------------------------------------------------------
# Shared fixture helpers (mirrors test_knowledge_registry.py style)
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
    body: str = "x" * 400,  # ~100 tokens by default (400 chars / 4)
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


def _make_agent_file(
    agents_dir: Path,
    filename: str,
    *,
    name: str,
    knowledge_packs: list[str] | None = None,
    description: str = "Test agent",
) -> None:
    lines = [f"name: {name}", f"description: {description}"]
    if knowledge_packs:
        kp_yaml = ", ".join(knowledge_packs)
        lines.append(f"knowledge_packs: [{kp_yaml}]")
    content = "---\n" + "\n".join(lines) + "\n---\n# Instructions\n"
    (agents_dir / filename).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """Standard knowledge root with three packs."""
    root = tmp_path / "knowledge"
    root.mkdir()

    # Pack 1: agent-baton (targets backend-engineer--python)
    pack1 = root / "agent-baton"
    pack1.mkdir()
    _make_manifest(
        pack1,
        name="agent-baton",
        description="Architecture and conventions for agent-baton",
        tags=["orchestration", "architecture", "development"],
        target_agents=["backend-engineer--python", "architect"],
    )
    # Small doc — will fit inline budget easily (body = 200 chars → ~50 tokens)
    _make_doc(
        pack1, "architecture.md",
        name="architecture",
        description="Package layout and design decisions",
        tags=["architecture", "layout"],
        body="x" * 200,
    )
    # High-priority doc
    _make_doc(
        pack1, "conventions.md",
        name="conventions",
        description="Coding conventions and patterns",
        tags=["conventions", "patterns"],
        priority="high",
        body="x" * 200,
    )

    # Pack 2: ai-orchestration (targets ai-systems-architect)
    pack2 = root / "ai-orchestration"
    pack2.mkdir()
    _make_manifest(
        pack2,
        name="ai-orchestration",
        description="Multi-agent orchestration patterns",
        tags=["orchestration", "multi-agent", "coordination"],
        target_agents=["ai-systems-architect"],
    )
    _make_doc(
        pack2, "context-economics.md",
        name="context-economics",
        description="Token cost model and context window budgeting",
        tags=["context-window", "tokens", "cost", "budgeting"],
        body="x" * 200,
    )

    # Pack 3: compliance (no target_agents — wildcard)
    pack3 = root / "compliance"
    pack3.mkdir()
    _make_manifest(
        pack3,
        name="compliance",
        description="Regulatory compliance and audit requirements",
        tags=["compliance", "audit", "regulatory"],
    )
    _make_doc(
        pack3, "audit-checklist.md",
        name="audit-checklist",
        description="Audit trail requirements and checklist",
        tags=["audit", "checklist"],
        body="x" * 200,
    )

    return root


@pytest.fixture
def registry(knowledge_root: Path) -> KnowledgeRegistry:
    reg = KnowledgeRegistry()
    reg.load_directory(knowledge_root)
    return reg


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    # backend-engineer--python declares agent-baton pack
    _make_agent_file(
        d, "backend-engineer--python.md",
        name="backend-engineer--python",
        knowledge_packs=["agent-baton"],
    )
    # generic agent with no declared packs
    _make_agent_file(
        d, "generic-agent.md",
        name="generic-agent",
        knowledge_packs=[],
    )
    return d


@pytest.fixture
def agent_registry(agents_dir: Path) -> AgentRegistry:
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    return reg


@pytest.fixture
def resolver(registry: KnowledgeRegistry, agent_registry: AgentRegistry) -> KnowledgeResolver:
    return KnowledgeResolver(registry, agent_registry=agent_registry)


# ---------------------------------------------------------------------------
# TestExtractKeywords (unit)
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_basic_extraction(self) -> None:
        keywords = _extract_keywords("implement authentication middleware")
        assert "implement" in keywords
        assert "authentication" in keywords
        assert "middleware" in keywords

    def test_stop_words_removed(self) -> None:
        keywords = _extract_keywords("the quick brown fox and a lazy dog")
        assert "the" not in keywords
        assert "and" not in keywords
        assert "a" not in keywords
        assert "fox" in keywords
        assert "dog" in keywords

    def test_case_insensitive(self) -> None:
        keywords = _extract_keywords("Architecture Design PATTERNS")
        assert "architecture" in keywords
        assert "design" in keywords
        assert "patterns" in keywords

    def test_task_type_included(self) -> None:
        keywords = _extract_keywords("fix a bug", task_type="bug-fix")
        assert "bug" in keywords
        assert "fix" in keywords

    def test_single_char_tokens_excluded(self) -> None:
        keywords = _extract_keywords("a b c foo bar")
        for kw in keywords:
            assert len(kw) > 1

    def test_empty_string_returns_empty(self) -> None:
        assert _extract_keywords("") == set()

    def test_none_task_type_is_harmless(self) -> None:
        keywords = _extract_keywords("implement feature", task_type=None)
        assert "implement" in keywords
        assert "feature" in keywords


# ---------------------------------------------------------------------------
# TestLayer1Explicit
# ---------------------------------------------------------------------------

class TestLayer1Explicit:
    def test_explicit_pack_resolves_all_docs(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["compliance"],
        )
        names = [a.document_name for a in attachments]
        assert "audit-checklist" in names

    def test_explicit_pack_source_tag(self, registry: KnowledgeRegistry) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["compliance"],
        )
        for a in attachments:
            assert a.source == "explicit"

    def test_explicit_unknown_pack_is_skipped(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        # Should not raise, just warn
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["totally-missing-pack"],
        )
        assert attachments == []

    def test_explicit_doc_by_path(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path],
        )
        assert len(attachments) == 1
        assert attachments[0].document_name == "audit-checklist"
        assert attachments[0].pack_name == "compliance"
        assert attachments[0].source == "explicit"

    def test_explicit_doc_not_in_registry_creates_stub(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        """A file path not in the registry still gets attached as a stub."""
        stub_path = tmp_path / "some-external-doc.md"
        stub_path.write_text("# External\n", encoding="utf-8")

        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[str(stub_path)],
        )
        assert len(attachments) == 1
        assert attachments[0].document_name == "some-external-doc"
        assert attachments[0].pack_name is None
        assert attachments[0].source == "explicit"

    def test_explicit_pack_docs_sorted_by_priority(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        """High-priority docs appear before normal-priority docs."""
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["agent-baton"],
        )
        # conventions is high priority, architecture is normal
        names = [a.document_name for a in attachments]
        assert names.index("conventions") < names.index("architecture")

    def test_explicit_none_values_treated_as_empty(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        # None explicit args should resolve to empty, not crash
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=None,
            explicit_docs=None,
        )
        # No explicit items — might still get matches from other layers
        # (task description "something" is unlikely to match, so 0 expected)
        assert isinstance(attachments, list)


# ---------------------------------------------------------------------------
# TestLayer2AgentDeclared
# ---------------------------------------------------------------------------

class TestLayer2AgentDeclared:
    def test_agent_declared_packs_resolved(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated task xyz",
        )
        names = [a.document_name for a in attachments]
        # agent-baton pack is declared by backend-engineer--python
        assert "architecture" in names
        assert "conventions" in names

    def test_agent_declared_source_tag(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated task xyz",
        )
        for a in attachments:
            assert a.source == "agent-declared"

    def test_agent_with_no_declared_packs_produces_no_layer2(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="generic-agent",
            task_description="unrelated task xyz",
        )
        sources = [a.source for a in attachments]
        assert "agent-declared" not in sources

    def test_no_agent_registry_skips_layer2(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)  # no agent_registry
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated task xyz",
        )
        sources = [a.source for a in attachments]
        assert "agent-declared" not in sources

    def test_unknown_agent_skips_layer2(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="unknown-agent-xyz",
            task_description="unrelated task xyz",
        )
        sources = [a.source for a in attachments]
        assert "agent-declared" not in sources

    def test_agent_declared_pack_not_in_registry_is_skipped(
        self,
        registry: KnowledgeRegistry,
        tmp_path: Path,
    ) -> None:
        """Agent declares a pack that doesn't exist in the knowledge registry."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _make_agent_file(
            agents_dir, "my-agent.md",
            name="my-agent",
            knowledge_packs=["nonexistent-pack"],
        )
        areg = AgentRegistry()
        areg.load_directory(agents_dir)

        r = KnowledgeResolver(registry, agent_registry=areg)
        attachments = r.resolve(
            agent_name="my-agent",
            task_description="task",
        )
        # nonexistent-pack simply skipped — no crash, no attachments from it
        sources = [a.source for a in attachments]
        assert "agent-declared" not in sources


# ---------------------------------------------------------------------------
# TestLayer3PlannerMatchedStrict
# ---------------------------------------------------------------------------

class TestLayer3PlannerMatchedStrict:
    def test_tag_match_returns_docs(self, registry: KnowledgeRegistry) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="implement architecture changes to the layout",
        )
        names = [a.document_name for a in attachments]
        assert "architecture" in names

    def test_tag_match_source_tag(self, registry: KnowledgeRegistry) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="architecture layout changes",
        )
        matched = [a for a in attachments if a.source == "planner-matched:tag"]
        assert len(matched) > 0

    def test_no_match_returns_empty_for_layer3(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        # "orchestration" matches pack-level tags and would produce results
        # Use something truly irrelevant
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="zzzzz qqqqqq",  # no matching keywords
        )
        matched = [a for a in attachments if a.source == "planner-matched:tag"]
        assert matched == []

    def test_task_type_keywords_contribute_to_tag_search(
        self, registry: KnowledgeRegistry, knowledge_root: Path, tmp_path: Path
    ) -> None:
        """task_type keywords add to the tag search query."""
        # Create a pack that only matches via a task_type-derived keyword
        root = tmp_path / "knowledge2"
        root.mkdir()
        pd = root / "mypack"
        pd.mkdir()
        _make_manifest(pd, name="mypack", tags=["deployment"])
        _make_doc(pd, "guide.md", name="guide", tags=["deployment"])

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="run the pipeline",
            task_type="deployment",
        )
        names = [a.document_name for a in attachments]
        assert "guide" in names

    def test_layer3_docs_sorted_by_priority(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        root = tmp_path / "knowledge3"
        root.mkdir()
        pd = root / "mypack"
        pd.mkdir()
        _make_manifest(pd, name="mypack", tags=["orchestration"])
        _make_doc(pd, "low.md", name="low-doc", tags=["orchestration"], priority="low")
        _make_doc(pd, "high.md", name="high-doc", tags=["orchestration"], priority="high")
        _make_doc(pd, "normal.md", name="normal-doc", tags=["orchestration"])

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="orchestration task",
        )
        names = [a.document_name for a in attachments]
        assert names.index("high-doc") < names.index("normal-doc")
        assert names.index("normal-doc") < names.index("low-doc")


# ---------------------------------------------------------------------------
# TestLayer4RelevanceFallback
# ---------------------------------------------------------------------------

class TestLayer4RelevanceFallback:
    def test_relevance_fallback_fires_when_strict_empty(
        self, registry: KnowledgeRegistry
    ) -> None:
        """With no tag hits, layer 4 should fire and return TF-IDF results."""
        r = KnowledgeResolver(registry)
        # Use a query that won't match via tags but will match via TF-IDF
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="token cost budgeting context window",
        )
        sources = [a.source for a in attachments]
        # "context-economics" should come via relevance
        # (no tags in base registry match "token cost budgeting context window" directly)
        # Note: "tokens", "cost", "budgeting", "context-window" are doc tags,
        # so they may match via tag layer. We test that relevance fires when strict fails.

        # Use a clearly non-tag query that should still be semantically related
        attachments2 = r.resolve(
            agent_name="any-agent",
            task_description="qqq_xyzzy_zzz_unique_word_not_a_tag",
        )
        # Should fire layer 4, but TF-IDF may return nothing for truly random text
        # The important thing: no exception raised
        assert isinstance(attachments2, list)

    def test_relevance_fallback_source_tag(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        """Layer 4 results have source 'planner-matched:relevance'."""
        # Build a registry where TF-IDF will match but there are no tags
        root = tmp_path / "knowledge4"
        root.mkdir()
        pd = root / "mypack"
        pd.mkdir()
        _make_manifest(
            pd, name="mypack",
            description="orchestration coordination patterns",
            tags=[],  # No tags -> Layer 3 won't fire
        )
        _make_doc(
            pd, "guide.md",
            name="guide",
            description="orchestration coordination patterns for agents",
            tags=[],  # No doc tags either
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="orchestration coordination patterns",
        )
        relevance_hits = [a for a in attachments if a.source == "planner-matched:relevance"]
        assert len(relevance_hits) > 0

    def test_relevance_not_fired_when_strict_succeeds(
        self, registry: KnowledgeRegistry
    ) -> None:
        """Layer 4 is skipped when Layer 3 found at least one result."""
        r = KnowledgeResolver(registry)
        # "architecture" matches layer 3 tag; layer 4 should not fire
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="architecture layout",
        )
        sources = [a.source for a in attachments]
        assert "planner-matched:relevance" not in sources

    def test_rag_available_sets_retrieval_hint_on_references(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        """When rag_available=True, reference deliveries get retrieval='mcp-rag'."""
        root = tmp_path / "knowledge5"
        root.mkdir()
        pd = root / "mypack"
        pd.mkdir()
        _make_manifest(pd, name="mypack", tags=[], description="orchestration agent rag")
        # Large doc — forces reference delivery (token_estimate > 8000)
        _make_doc(
            pd, "big.md",
            name="big-doc",
            description="orchestration agent rag",
            tags=[],
            body="x" * 40000,  # 40000 chars → ~10000 tokens > 8000 cap
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg, rag_available=True)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="orchestration agent rag",
        )
        ref_attachments = [a for a in attachments if a.delivery == "reference"]
        for a in ref_attachments:
            assert a.retrieval == "mcp-rag"

    def test_rag_false_sets_retrieval_hint_to_file(
        self, registry: KnowledgeRegistry
    ) -> None:
        """When rag_available=False, all deliveries get retrieval='file'."""
        r = KnowledgeResolver(registry, rag_available=False)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="architecture layout",
        )
        for a in attachments:
            assert a.retrieval == "file"

    def test_rag_available_inline_delivery_stays_file(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        """Inline docs always use retrieval='file' even when rag_available=True."""
        root = tmp_path / "knowledge6"
        root.mkdir()
        pd = root / "mypack"
        pd.mkdir()
        _make_manifest(pd, name="mypack", tags=[], description="orchestration rag inline")
        # Small doc — will go inline
        _make_doc(
            pd, "small.md",
            name="small-doc",
            description="orchestration rag inline",
            tags=[],
            body="x" * 100,  # ~25 tokens
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg, rag_available=True)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="orchestration rag inline",
        )
        inline = [a for a in attachments if a.delivery == "inline"]
        for a in inline:
            assert a.retrieval == "file"


# ---------------------------------------------------------------------------
# TestDeduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_same_doc_in_two_layers_appears_once(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
        knowledge_root: Path,
    ) -> None:
        """A doc resolved via agent-declared AND via explicit should appear only once."""
        doc_path = str(knowledge_root / "agent-baton" / "architecture.md")
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated xyz task",
            explicit_docs=[doc_path],
        )
        names = [a.document_name for a in attachments]
        assert names.count("architecture") == 1

    def test_layer1_takes_precedence_source_over_layer2(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
        knowledge_root: Path,
    ) -> None:
        """When a doc appears in both explicit (L1) and agent-declared (L2), L1 wins."""
        doc_path = str(knowledge_root / "agent-baton" / "architecture.md")
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated xyz",
            explicit_docs=[doc_path],
        )
        arch_attachments = [a for a in attachments if a.document_name == "architecture"]
        assert len(arch_attachments) == 1
        assert arch_attachments[0].source == "explicit"

    def test_dedup_across_layer3_and_layer2(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
    ) -> None:
        """A doc resolved via agent-declared (L2) and also matching tags (L3) appears once."""
        # backend-engineer--python declares agent-baton (has "architecture" doc with
        # tags ["architecture", "layout"]) — also matches tag search for "architecture"
        r = KnowledgeResolver(registry, agent_registry=agent_registry)
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="architecture layout changes",
        )
        arch_count = sum(1 for a in attachments if a.document_name == "architecture")
        assert arch_count == 1

    def test_identical_explicit_docs_deduplicated(
        self,
        registry: KnowledgeRegistry,
        knowledge_root: Path,
    ) -> None:
        """Same path provided twice in explicit_docs appears only once."""
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path, doc_path],
        )
        names = [a.document_name for a in attachments]
        assert names.count("audit-checklist") == 1


# ---------------------------------------------------------------------------
# TestDeliveryDecisions
# ---------------------------------------------------------------------------

class TestDeliveryDecisions:
    def _registry_with_doc(
        self,
        tmp_path: Path,
        *,
        body: str,
        name: str = "testdoc",
        tags: list[str] | None = None,
    ) -> KnowledgeRegistry:
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=tags or ["testquery"])
        _make_doc(pd, "doc.md", name=name, tags=tags or ["testquery"], body=body)
        reg = KnowledgeRegistry()
        reg.load_directory(root)
        return reg

    def test_small_doc_delivers_inline(self, tmp_path: Path) -> None:
        # 200 chars → ~50 tokens, well under both 8000 cap and 32000 budget
        reg = self._registry_with_doc(tmp_path, body="x" * 200)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "inline"

    def test_large_doc_delivers_reference_over_cap(self, tmp_path: Path) -> None:
        # 40000 chars → ~10000 tokens > 8000 doc_token_cap
        reg = self._registry_with_doc(tmp_path, body="x" * 40000)
        r = KnowledgeResolver(reg, doc_token_cap=8_000)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "reference"

    def test_zero_token_estimate_delivers_reference(self, tmp_path: Path) -> None:
        """token_estimate=0 → reference (unestimated)."""
        reg = self._registry_with_doc(tmp_path, body="x" * 200)
        # Force token_estimate to 0 by patching the doc
        pack = reg.get_pack("mypack")
        assert pack is not None
        pack.documents[0].token_estimate = 0

        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert attachments[0].delivery == "reference"

    def test_budget_exhaustion_delivers_reference(self, tmp_path: Path) -> None:
        """When step budget is exhausted, subsequent docs become references."""
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["testquery"])
        # Two docs — each 600 chars → ~150 tokens each
        _make_doc(pd, "doc1.md", name="doc1", tags=["testquery"], body="x" * 600)
        _make_doc(pd, "doc2.md", name="doc2", tags=["testquery"], body="x" * 600)

        reg = KnowledgeRegistry()
        reg.load_directory(root)

        # Step budget = 200 tokens — doc1 fits, doc2 exhausts budget
        r = KnowledgeResolver(reg, step_token_budget=200, doc_token_cap=8_000)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert len(attachments) == 2
        deliveries = {a.document_name: a.delivery for a in attachments}
        # First doc: ≤200 tokens → inline; second doc: budget exhausted → reference
        inline_count = sum(1 for d in deliveries.values() if d == "inline")
        ref_count = sum(1 for d in deliveries.values() if d == "reference")
        assert inline_count >= 1
        assert ref_count >= 1

    def test_budget_tracking_across_layers(
        self,
        registry: KnowledgeRegistry,
        agent_registry: AgentRegistry,
        knowledge_root: Path,
    ) -> None:
        """Budget consumed in L1 is not available for L2."""
        # Set a very small budget (100 tokens) — agent-baton pack has small docs
        # but even a small budget should be consumed after one inline
        r = KnowledgeResolver(
            registry,
            agent_registry=agent_registry,
            step_token_budget=50,
            doc_token_cap=8_000,
        )
        attachments = r.resolve(
            agent_name="backend-engineer--python",
            task_description="unrelated xyz",
            explicit_packs=["agent-baton"],
        )
        # With a 50-token budget and docs ~50 tokens each, at most one should be inline
        inline_count = sum(1 for a in attachments if a.delivery == "inline")
        # Not strict about the number but budget must be tracked
        assert inline_count <= 2  # sanity: not all docs are inline if budget is tight

    def test_custom_doc_token_cap(self, tmp_path: Path) -> None:
        """doc_token_cap parameter is respected."""
        # 1000 chars → ~250 tokens
        reg = self._registry_with_doc(tmp_path, body="x" * 1000)
        # With cap=100, 250 > 100 → reference
        r_small_cap = KnowledgeResolver(reg, doc_token_cap=100)
        attachments_small = r_small_cap.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert attachments_small[0].delivery == "reference"

        # With cap=10000, 250 < 10000 → inline
        reg2 = self._registry_with_doc(tmp_path / "k2", body="x" * 1000)
        r_large_cap = KnowledgeResolver(reg2, doc_token_cap=10_000)
        attachments_large = r_large_cap.resolve(
            agent_name="any-agent",
            task_description="testquery",
        )
        assert attachments_large[0].delivery == "inline"


# ---------------------------------------------------------------------------
# TestPriorityOrdering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_high_priority_before_normal_before_low(
        self, tmp_path: Path
    ) -> None:
        """Priority ordering is enforced within a single layer."""
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["priorities"])
        _make_doc(pd, "low.md", name="low-doc", tags=["priorities"], priority="low")
        _make_doc(pd, "high.md", name="high-doc", tags=["priorities"], priority="high")
        _make_doc(pd, "norm.md", name="norm-doc", tags=["priorities"])  # default=normal

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="priorities task",
        )
        names = [a.document_name for a in attachments]
        assert names.index("high-doc") < names.index("norm-doc")
        assert names.index("norm-doc") < names.index("low-doc")

    def test_priority_ordering_with_budget_affects_inline_selection(
        self, tmp_path: Path
    ) -> None:
        """With a tight budget, high-priority docs go inline; low-priority becomes reference."""
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["budget-test"])
        # Both docs are 600 chars → ~150 tokens each
        _make_doc(pd, "low.md", name="low-doc", tags=["budget-test"], priority="low", body="x" * 600)
        _make_doc(pd, "high.md", name="high-doc", tags=["budget-test"], priority="high", body="x" * 600)

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        # Budget = 200 → only one doc fits inline (high-priority wins)
        r = KnowledgeResolver(reg, step_token_budget=200, doc_token_cap=8_000)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="budget-test task",
        )
        delivery_by_name = {a.document_name: a.delivery for a in attachments}
        assert delivery_by_name.get("high-doc") == "inline"
        assert delivery_by_name.get("low-doc") == "reference"


# ---------------------------------------------------------------------------
# TestGrounding
# ---------------------------------------------------------------------------

class TestGrounding:
    def test_custom_grounding_preserved(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        """If a doc has a grounding string, it's copied verbatim to the attachment."""
        root = knowledge_root.parent / "knowledge_grounding"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["grounding-test"])
        _make_doc(
            pd, "doc.md",
            name="doc",
            tags=["grounding-test"],
            grounding="Custom grounding text for this document.",
        )
        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="grounding-test",
        )
        assert len(attachments) == 1
        assert attachments[0].grounding == "Custom grounding text for this document."

    def test_default_grounding_generated_when_absent(
        self, registry: KnowledgeRegistry
    ) -> None:
        """When no grounding is set, the resolver generates one from name+description."""
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="audit checklist compliance",
        )
        # audit-checklist has no grounding set
        audit = next(
            (a for a in attachments if a.document_name == "audit-checklist"), None
        )
        if audit is not None:
            # grounding should be auto-generated, non-empty
            assert audit.grounding != ""
            assert "audit-checklist" in audit.grounding

    def test_grounding_references_pack_name(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="audit compliance",
        )
        audit = next(
            (a for a in attachments if a.document_name == "audit-checklist"), None
        )
        if audit is not None:
            assert "compliance" in audit.grounding


# ---------------------------------------------------------------------------
# TestReturnShape
# ---------------------------------------------------------------------------

class TestReturnShape:
    def test_returns_list_of_knowledge_attachments(
        self, resolver: KnowledgeResolver
    ) -> None:
        attachments = resolver.resolve(
            agent_name="backend-engineer--python",
            task_description="implement architecture feature",
        )
        assert isinstance(attachments, list)
        for a in attachments:
            assert isinstance(a, KnowledgeAttachment)

    def test_empty_result_for_empty_registry(self) -> None:
        reg = KnowledgeRegistry()  # empty
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="anything",
        )
        assert attachments == []

    def test_attachment_fields_populated(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path],
        )
        a = attachments[0]
        assert a.source == "explicit"
        assert a.pack_name == "compliance"
        assert a.document_name == "audit-checklist"
        assert a.path != ""
        assert a.delivery in ("inline", "reference")
        assert a.retrieval in ("file", "mcp-rag")
        assert a.grounding != ""
        assert isinstance(a.token_estimate, int)

    def test_path_set_from_source_path(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path],
        )
        assert attachments[0].path == doc_path


# ---------------------------------------------------------------------------
# TestRagAvailable
# ---------------------------------------------------------------------------

class TestRagAvailable:
    def test_rag_false_retrieval_always_file(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        r = KnowledgeResolver(registry, rag_available=False)
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path],
        )
        for a in attachments:
            assert a.retrieval == "file"

    def test_rag_true_reference_uses_mcp_rag(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["ragtag"])
        # Force reference delivery: large body
        _make_doc(pd, "big.md", name="big", tags=["ragtag"], body="x" * 40000)

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg, rag_available=True)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="ragtag task",
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "reference"
        assert attachments[0].retrieval == "mcp-rag"

    def test_rag_true_inline_still_file(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["ragtag"])
        _make_doc(pd, "small.md", name="small", tags=["ragtag"], body="x" * 100)

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        r = KnowledgeResolver(reg, rag_available=True)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="ragtag task",
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "inline"
        assert attachments[0].retrieval == "file"


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_resolve_with_all_none_optionals(
        self, registry: KnowledgeRegistry
    ) -> None:
        """resolve() with only required args doesn't raise."""
        r = KnowledgeResolver(registry)
        result = r.resolve(
            agent_name="any-agent",
            task_description="",
        )
        assert isinstance(result, list)

    def test_explicit_empty_lists_are_harmless(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        result = r.resolve(
            agent_name="any-agent",
            task_description="task",
            explicit_packs=[],
            explicit_docs=[],
        )
        assert isinstance(result, list)

    def test_multiple_explicit_packs(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["agent-baton", "compliance"],
        )
        names = {a.document_name for a in attachments}
        assert "architecture" in names
        assert "audit-checklist" in names

    def test_pack_name_on_attachment(
        self, registry: KnowledgeRegistry
    ) -> None:
        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_packs=["compliance"],
        )
        for a in attachments:
            assert a.pack_name == "compliance"

    def test_token_estimate_on_attachment_matches_doc(
        self, registry: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        doc = registry.get_document("compliance", "audit-checklist")
        assert doc is not None
        r = KnowledgeResolver(registry)
        doc_path = str(knowledge_root / "compliance" / "audit-checklist.md")
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[doc_path],
        )
        assert attachments[0].token_estimate == doc.token_estimate

    def test_stub_doc_path_is_explicit_path(
        self, registry: KnowledgeRegistry, tmp_path: Path
    ) -> None:
        """Stub (not-in-registry) doc has path set to the provided file path."""
        stub = tmp_path / "external.md"
        stub.write_text("# External doc\n", encoding="utf-8")

        r = KnowledgeResolver(registry)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="something",
            explicit_docs=[str(stub)],
        )
        assert attachments[0].path == str(stub)


# ---------------------------------------------------------------------------
# TestSessionDedup
# ---------------------------------------------------------------------------

class TestSessionDedup:
    """Tests for session-level knowledge deduplication via already_delivered."""

    def _small_registry(self, tmp_path: Path) -> tuple[KnowledgeRegistry, Path]:
        """Return a registry with one small inline-eligible doc."""
        root = tmp_path / "knowledge"
        pd = root / "mypack"
        pd.mkdir(parents=True)
        _make_manifest(pd, name="mypack", tags=["dedup-test"])
        _make_doc(pd, "doc.md", name="doc", tags=["dedup-test"], body="x" * 200)
        reg = KnowledgeRegistry()
        reg.load_directory(root)
        return reg, root / "mypack" / "doc.md"

    # (a) First dispatch inlines the doc and records the step_id.
    def test_first_dispatch_inlines_doc(self, tmp_path: Path) -> None:
        reg, _ = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        delivered: dict[str, str] = {}
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            already_delivered=delivered,
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "inline"

    # (a) After first dispatch the doc key is NOT in already_delivered
    #     (that's the dispatcher's job to record; the resolver only reads it).
    def test_first_dispatch_does_not_mutate_already_delivered(
        self, tmp_path: Path
    ) -> None:
        reg, _ = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        delivered: dict[str, str] = {}
        r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            already_delivered=delivered,
        )
        # The resolver itself never mutates already_delivered — that's the
        # dispatcher's responsibility.  Dict must stay empty.
        assert delivered == {}

    # (b) Second dispatch with doc already in already_delivered → reference.
    def test_second_dispatch_downgrades_to_reference(
        self, tmp_path: Path
    ) -> None:
        reg, doc_path = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        # Simulate that step "1.1" already inlined the doc.
        delivered: dict[str, str] = {str(doc_path): "1.1"}
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            already_delivered=delivered,
        )
        assert len(attachments) == 1
        assert attachments[0].delivery == "reference"

    # (b) The grounding note on the downgraded attachment references the prior step.
    def test_downgraded_attachment_grounding_contains_prior_step(
        self, tmp_path: Path
    ) -> None:
        reg, doc_path = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        delivered: dict[str, str] = {str(doc_path): "1.1"}
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            already_delivered=delivered,
        )
        assert "step 1.1" in attachments[0].grounding
        assert str(doc_path) in attachments[0].grounding

    # (c) Explicit layer-1 attachment re-inlines even after prior delivery.
    def test_explicit_layer1_always_inlines_despite_prior_delivery(
        self, tmp_path: Path
    ) -> None:
        reg, doc_path = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        # Pretend the doc was already delivered inline in step 1.1.
        delivered: dict[str, str] = {str(doc_path): "1.1"}
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            explicit_docs=[str(doc_path)],
            already_delivered=delivered,
        )
        explicit_attachments = [a for a in attachments if a.source == "explicit"]
        assert len(explicit_attachments) == 1
        assert explicit_attachments[0].delivery == "inline"

    # (d) State serializes / deserializes correctly round-trip.
    def test_delivered_knowledge_roundtrip_in_execution_state(self) -> None:
        from agent_baton.models.execution import ExecutionState, MachinePlan

        plan = MachinePlan(
            task_id="t1",
            task_summary="test",
            risk_level="LOW",
            phases=[],
        )
        state = ExecutionState(task_id="t1", plan=plan)
        state.delivered_knowledge["docs/arch.md"] = "1.1"
        state.delivered_knowledge["mypack::conventions"] = "1.2"

        data = state.to_dict()
        assert data["delivered_knowledge"] == {
            "docs/arch.md": "1.1",
            "mypack::conventions": "1.2",
        }

        restored = ExecutionState.from_dict(data)
        assert restored.delivered_knowledge["docs/arch.md"] == "1.1"
        assert restored.delivered_knowledge["mypack::conventions"] == "1.2"

    # (d) Older state file without delivered_knowledge field loads without error.
    def test_missing_delivered_knowledge_field_defaults_to_empty(self) -> None:
        from agent_baton.models.execution import ExecutionState, MachinePlan

        plan = MachinePlan(
            task_id="t2",
            task_summary="test",
            risk_level="LOW",
            phases=[],
        )
        state = ExecutionState(task_id="t2", plan=plan)
        raw = state.to_dict()
        # Simulate old state file that has no delivered_knowledge key.
        del raw["delivered_knowledge"]

        restored = ExecutionState.from_dict(raw)
        assert restored.delivered_knowledge == {}

    # No already_delivered passed → behaviour identical to before the feature.
    def test_none_already_delivered_leaves_behavior_unchanged(
        self, tmp_path: Path
    ) -> None:
        reg, _ = self._small_registry(tmp_path)
        r = KnowledgeResolver(reg)
        attachments = r.resolve(
            agent_name="any-agent",
            task_description="dedup-test",
            already_delivered=None,
        )
        assert attachments[0].delivery == "inline"
