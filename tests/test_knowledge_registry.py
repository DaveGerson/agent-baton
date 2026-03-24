"""Tests for agent_baton.core.orchestration.knowledge_registry.KnowledgeRegistry."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.knowledge import KnowledgeDocument, KnowledgePack


# ---------------------------------------------------------------------------
# Helpers
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
    (pack_dir / "knowledge.yaml").write_text(
        yaml.dump(data), encoding="utf-8"
    )


def _make_doc(
    pack_dir: Path,
    filename: str,
    *,
    name: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "normal",
    grounding: str = "",
    body: str = "# Body\n\nSome content here.\n",
) -> None:
    fm_parts = []
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
        content = body  # no frontmatter at all
    (pack_dir / filename).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """Temporary knowledge root with two packs."""
    root = tmp_path / "knowledge"
    root.mkdir()

    # Pack 1: agent-baton
    pack1 = root / "agent-baton"
    pack1.mkdir()
    _make_manifest(
        pack1,
        name="agent-baton",
        description="Architecture and conventions for agent-baton",
        tags=["orchestration", "architecture", "development"],
        target_agents=["backend-engineer--python", "architect"],
    )
    _make_doc(
        pack1, "architecture.md",
        name="architecture",
        description="Package layout and design decisions",
        tags=["architecture", "layout"],
    )
    _make_doc(
        pack1, "conventions.md",
        name="conventions",
        description="Coding conventions and patterns",
        tags=["conventions", "patterns"],
        priority="high",
    )

    # Pack 2: ai-orchestration
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
    )

    return root


@pytest.fixture
def registry_loaded(knowledge_root: Path) -> KnowledgeRegistry:
    """KnowledgeRegistry pre-loaded from knowledge_root."""
    reg = KnowledgeRegistry()
    reg.load_directory(knowledge_root)
    return reg


# ---------------------------------------------------------------------------
# TestLoadDirectory
# ---------------------------------------------------------------------------

class TestLoadDirectory:
    def test_returns_zero_for_nonexistent_directory(self, tmp_path: Path) -> None:
        reg = KnowledgeRegistry()
        assert reg.load_directory(tmp_path / "missing") == 0

    def test_returns_zero_for_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "knowledge"
        empty.mkdir()
        reg = KnowledgeRegistry()
        assert reg.load_directory(empty) == 0

    def test_ignores_files_at_root_level(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        root.mkdir()
        (root / "stray-file.md").write_text("# stray", encoding="utf-8")
        reg = KnowledgeRegistry()
        assert reg.load_directory(root) == 0

    def test_loads_packs_from_subdirectories(self, knowledge_root: Path) -> None:
        reg = KnowledgeRegistry()
        count = reg.load_directory(knowledge_root)
        assert count == 2

    def test_all_packs_property_reflects_loaded_packs(
        self, knowledge_root: Path
    ) -> None:
        reg = KnowledgeRegistry()
        reg.load_directory(knowledge_root)
        packs = reg.all_packs
        assert set(packs.keys()) == {"agent-baton", "ai-orchestration"}

    def test_documents_are_loaded_within_packs(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        pack = registry_loaded.get_pack("agent-baton")
        assert pack is not None
        assert len(pack.documents) == 2
        doc_names = {d.name for d in pack.documents}
        assert doc_names == {"architecture", "conventions"}

    def test_token_estimate_is_nonzero(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        pack = registry_loaded.get_pack("agent-baton")
        assert pack is not None
        for doc in pack.documents:
            assert doc.token_estimate > 0

    def test_content_is_not_loaded_at_index_time(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        """Document body must not be loaded eagerly."""
        pack = registry_loaded.get_pack("agent-baton")
        assert pack is not None
        for doc in pack.documents:
            assert doc.content == ""

    def test_source_path_is_set_on_pack(
        self, registry_loaded: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        pack = registry_loaded.get_pack("agent-baton")
        assert pack is not None
        assert pack.source_path == knowledge_root / "agent-baton"

    def test_source_path_is_set_on_document(
        self, registry_loaded: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        doc = registry_loaded.get_document("agent-baton", "architecture")
        assert doc is not None
        assert doc.source_path == knowledge_root / "agent-baton" / "architecture.md"

    def test_pack_metadata_parsed_correctly(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        pack = registry_loaded.get_pack("agent-baton")
        assert pack is not None
        assert pack.description == "Architecture and conventions for agent-baton"
        assert "orchestration" in pack.tags
        assert "backend-engineer--python" in pack.target_agents

    def test_document_metadata_parsed_correctly(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        doc = registry_loaded.get_document("agent-baton", "conventions")
        assert doc is not None
        assert doc.description == "Coding conventions and patterns"
        assert doc.priority == "high"
        assert "patterns" in doc.tags


# ---------------------------------------------------------------------------
# TestOverridePrecedence
# ---------------------------------------------------------------------------

class TestOverridePrecedence:
    def test_project_overrides_global_by_name(self, tmp_path: Path) -> None:
        global_root = tmp_path / "global"
        project_root = tmp_path / "project"

        for root in (global_root, project_root):
            pack_dir = root / "mypack"
            pack_dir.mkdir(parents=True)

        _make_manifest(
            global_root / "mypack",
            name="mypack",
            description="Global version",
        )
        _make_doc(global_root / "mypack", "doc.md", name="doc")

        _make_manifest(
            project_root / "mypack",
            name="mypack",
            description="Project version",
        )
        _make_doc(project_root / "mypack", "doc.md", name="doc")

        reg = KnowledgeRegistry()
        reg.load_directory(global_root)
        reg.load_directory(project_root, override=True)

        pack = reg.get_pack("mypack")
        assert pack is not None
        assert pack.description == "Project version"

    def test_without_override_global_is_kept(self, tmp_path: Path) -> None:
        global_root = tmp_path / "global"
        project_root = tmp_path / "project"

        for root in (global_root, project_root):
            pack_dir = root / "mypack"
            pack_dir.mkdir(parents=True)
            _make_manifest(pack_dir, name="mypack", description=f"{root.name} version")

        reg = KnowledgeRegistry()
        reg.load_directory(global_root)
        reg.load_directory(project_root, override=False)

        pack = reg.get_pack("mypack")
        assert pack is not None
        assert pack.description == "global version"

    def test_only_named_pack_is_replaced_not_others(self, tmp_path: Path) -> None:
        global_root = tmp_path / "global"
        project_root = tmp_path / "project"

        (global_root / "pack-a").mkdir(parents=True)
        (global_root / "pack-b").mkdir(parents=True)
        _make_manifest(global_root / "pack-a", name="pack-a", description="global-a")
        _make_manifest(global_root / "pack-b", name="pack-b", description="global-b")

        (project_root / "pack-a").mkdir(parents=True)
        _make_manifest(project_root / "pack-a", name="pack-a", description="project-a")

        reg = KnowledgeRegistry()
        reg.load_directory(global_root)
        reg.load_directory(project_root, override=True)

        assert reg.get_pack("pack-a").description == "project-a"
        assert reg.get_pack("pack-b").description == "global-b"

    def test_override_count_reflects_packs_loaded(self, tmp_path: Path) -> None:
        global_root = tmp_path / "global"
        project_root = tmp_path / "project"

        (global_root / "mypack").mkdir(parents=True)
        _make_manifest(global_root / "mypack", name="mypack")

        (project_root / "mypack").mkdir(parents=True)
        _make_manifest(project_root / "mypack", name="mypack")

        reg = KnowledgeRegistry()
        reg.load_directory(global_root)
        count = reg.load_directory(project_root, override=True)
        assert count == 1


# ---------------------------------------------------------------------------
# TestGracefulDegradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_pack_without_manifest_still_loads(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        (root / "mypack").mkdir(parents=True)
        _make_doc(root / "mypack", "doc.md", name="doc")

        reg = KnowledgeRegistry()
        count = reg.load_directory(root)
        assert count == 1
        pack = reg.get_pack("mypack")
        assert pack is not None
        assert pack.name == "mypack"  # inferred from directory

    def test_pack_without_manifest_has_empty_metadata(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        (root / "mypack").mkdir(parents=True)
        _make_doc(root / "mypack", "doc.md", name="doc")

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        pack = reg.get_pack("mypack")
        assert pack is not None
        assert pack.description == ""
        assert pack.tags == []
        assert pack.target_agents == []

    def test_manifest_missing_name_uses_directory_name(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        pack_dir = root / "my-pack-dir"
        pack_dir.mkdir(parents=True)
        # Write manifest without 'name' key
        (pack_dir / "knowledge.yaml").write_text(
            "description: Some pack\n", encoding="utf-8"
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        pack = reg.get_pack("my-pack-dir")
        assert pack is not None

    def test_doc_without_frontmatter_still_loads(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="mypack")
        # Write doc with NO frontmatter at all
        (pack_dir / "raw-doc.md").write_text(
            "# No Frontmatter\n\nJust a body.", encoding="utf-8"
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        pack = reg.get_pack("mypack")
        assert pack is not None
        assert len(pack.documents) == 1
        doc = pack.documents[0]
        assert doc.name == "raw-doc"  # inferred from filename
        assert doc.description == ""
        assert doc.tags == []

    def test_corrupted_manifest_still_loads_pack(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        pack_dir = root / "corrupted"
        pack_dir.mkdir(parents=True)
        (pack_dir / "knowledge.yaml").write_text(
            ": invalid: yaml: {{{{", encoding="utf-8"
        )
        _make_doc(pack_dir, "doc.md", name="doc")

        reg = KnowledgeRegistry()
        count = reg.load_directory(root)
        assert count == 1
        # Name falls back to directory name
        pack = reg.get_pack("corrupted")
        assert pack is not None


# ---------------------------------------------------------------------------
# TestGetDocument
# ---------------------------------------------------------------------------

class TestGetDocument:
    def test_returns_document_when_found(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        doc = registry_loaded.get_document("agent-baton", "architecture")
        assert doc is not None
        assert doc.name == "architecture"

    def test_returns_none_for_unknown_pack(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        assert registry_loaded.get_document("nonexistent", "architecture") is None

    def test_returns_none_for_unknown_doc(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        assert registry_loaded.get_document("agent-baton", "nonexistent-doc") is None


# ---------------------------------------------------------------------------
# TestPacksForAgent
# ---------------------------------------------------------------------------

class TestPacksForAgent:
    def test_returns_packs_targeting_exact_agent_name(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        packs = registry_loaded.packs_for_agent("backend-engineer--python")
        names = [p.name for p in packs]
        assert "agent-baton" in names

    def test_does_not_return_packs_not_targeting_agent(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        # ai-orchestration targets ai-systems-architect only
        packs = registry_loaded.packs_for_agent("backend-engineer--python")
        names = [p.name for p in packs]
        assert "ai-orchestration" not in names

    def test_base_name_matching(self, tmp_path: Path) -> None:
        """Pack targeting 'backend-engineer' matches 'backend-engineer--python'."""
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(
            pack_dir,
            name="mypack",
            target_agents=["backend-engineer"],
        )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        packs = reg.packs_for_agent("backend-engineer--python")
        assert len(packs) == 1
        assert packs[0].name == "mypack"

    def test_empty_when_no_packs_target_agent(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        packs = registry_loaded.packs_for_agent("totally-unknown-agent")
        assert packs == []

    def test_pack_with_no_target_agents_is_excluded(self, tmp_path: Path) -> None:
        """Packs with empty target_agents are NOT returned by packs_for_agent."""
        root = tmp_path / "knowledge"
        pack_dir = root / "generic"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="generic", target_agents=[])

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        packs = reg.packs_for_agent("any-agent")
        assert packs == []


# ---------------------------------------------------------------------------
# TestFindByTags
# ---------------------------------------------------------------------------

class TestFindByTags:
    def test_returns_docs_matching_single_tag(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        docs = registry_loaded.find_by_tags({"architecture"})
        names = [d.name for d in docs]
        assert "architecture" in names

    def test_returns_docs_matching_any_of_multiple_tags(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        docs = registry_loaded.find_by_tags({"tokens", "patterns"})
        names = [d.name for d in docs]
        assert "context-economics" in names
        assert "conventions" in names

    def test_returns_empty_for_unknown_tags(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        docs = registry_loaded.find_by_tags({"totally-unknown-tag-xyz"})
        assert docs == []

    def test_tag_matching_is_case_insensitive(self, tmp_path: Path) -> None:
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="mypack", tags=["Orchestration"])
        _make_doc(pack_dir, "doc.md", name="doc", tags=["DeployMENT"])

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        docs = reg.find_by_tags({"orchestration"})
        assert len(docs) == 1

        docs2 = reg.find_by_tags({"deployment"})
        assert len(docs2) == 1

    def test_pack_level_tags_also_match_documents(self, tmp_path: Path) -> None:
        """A document with no tags of its own matches via its pack's tags."""
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="mypack", tags=["orchestration"])
        # Doc has no tags — match should come from pack
        _make_doc(pack_dir, "doc.md", name="doc", tags=[])

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        docs = reg.find_by_tags({"orchestration"})
        assert len(docs) == 1
        assert docs[0].name == "doc"


# ---------------------------------------------------------------------------
# TestTFIDFSearch
# ---------------------------------------------------------------------------

class TestTFIDFSearch:
    def test_search_returns_relevant_doc(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        results = registry_loaded.search("architecture layout design")
        assert len(results) > 0
        docs = [doc for doc, _score in results]
        doc_names = [d.name for d in docs]
        assert "architecture" in doc_names

    def test_search_returns_tuples_with_float_scores(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        results = registry_loaded.search("orchestration")
        for item in results:
            assert len(item) == 2
            doc, score = item
            assert isinstance(doc, KnowledgeDocument)
            assert isinstance(score, float)

    def test_search_scores_above_threshold(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        results = registry_loaded.search("architecture")
        for _doc, score in results:
            assert score >= 0.3

    def test_search_returns_empty_for_garbage_query(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        results = registry_loaded.search("xyzzy_no_match_999")
        assert results == []

    def test_search_returns_empty_when_registry_empty(self) -> None:
        reg = KnowledgeRegistry()
        results = reg.search("anything")
        assert results == []

    def test_search_respects_limit(self, tmp_path: Path) -> None:
        """With many matching docs, search() respects the limit parameter."""
        root = tmp_path / "knowledge"
        pack_dir = root / "mypack"
        pack_dir.mkdir(parents=True)
        _make_manifest(
            pack_dir, name="mypack", description="orchestration orchestration"
        )
        for i in range(15):
            _make_doc(
                pack_dir, f"doc{i}.md",
                name=f"doc{i}",
                description=f"orchestration document number {i}",
                tags=["orchestration"],
            )

        reg = KnowledgeRegistry()
        reg.load_directory(root)
        results = reg.search("orchestration", limit=5)
        assert len(results) <= 5

    def test_search_sorted_descending_by_score(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        results = registry_loaded.search("architecture layout design")
        scores = [score for _doc, score in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_token_cost_budgeting_query(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        """TF-IDF finds context-economics for a token/cost query."""
        results = registry_loaded.search("token cost budget context window")
        doc_names = [d.name for d, _s in results]
        assert "context-economics" in doc_names


# ---------------------------------------------------------------------------
# TestLazyContentLoading
# ---------------------------------------------------------------------------

class TestLazyContentLoading:
    def test_content_empty_at_index_time(
        self, registry_loaded: KnowledgeRegistry
    ) -> None:
        for pack in registry_loaded.all_packs.values():
            for doc in pack.documents:
                assert doc.content == "", (
                    f"{doc.name} should not have content loaded at index time"
                )

    def test_content_loadable_via_source_path(
        self, registry_loaded: KnowledgeRegistry, knowledge_root: Path
    ) -> None:
        """Consumer can manually load content via doc.source_path."""
        doc = registry_loaded.get_document("agent-baton", "architecture")
        assert doc is not None
        assert doc.source_path is not None
        content = doc.source_path.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_source_path_none_for_no_file(self) -> None:
        """A KnowledgeDocument created without source_path has None."""
        doc = KnowledgeDocument(name="in-memory", description="test")
        assert doc.source_path is None
        assert doc.content == ""


# ---------------------------------------------------------------------------
# TestLoadDefaultPaths (smoke test — uses tmp dirs, not real ~/.claude)
# ---------------------------------------------------------------------------

class TestLoadDefaultPaths:
    def test_load_default_paths_returns_int(self, tmp_path: Path, monkeypatch) -> None:
        """load_default_paths() should not crash when directories don't exist."""
        # Redirect home and cwd so we don't touch real filesystem
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)

        reg = KnowledgeRegistry()
        count = reg.load_default_paths()
        assert isinstance(count, int)
        assert count >= 0

    def test_load_default_paths_loads_project_packs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
        monkeypatch.chdir(tmp_path)

        project_knowledge = tmp_path / ".claude" / "knowledge"
        pack_dir = project_knowledge / "testpack"
        pack_dir.mkdir(parents=True)
        _make_manifest(pack_dir, name="testpack", description="test")

        reg = KnowledgeRegistry()
        count = reg.load_default_paths()
        assert count >= 1
        assert reg.get_pack("testpack") is not None


# ---------------------------------------------------------------------------
# TestRegistryKnowledgePacksParsing (AgentRegistry integration)
# ---------------------------------------------------------------------------

class TestAgentRegistryKnowledgePacksParsing:
    """Verify that AgentRegistry parses knowledge_packs from agent frontmatter."""

    def test_knowledge_packs_list_parsed(self, tmp_path: Path) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        content = (
            "---\n"
            "name: my-agent\n"
            "description: Test agent\n"
            "knowledge_packs:\n"
            "  - agent-baton\n"
            "  - ai-orchestration\n"
            "---\n"
            "# Body\n"
        )
        (agents_dir / "my-agent.md").write_text(content, encoding="utf-8")

        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        agent = reg.get("my-agent")
        assert agent is not None
        assert agent.knowledge_packs == ["agent-baton", "ai-orchestration"]

    def test_knowledge_packs_csv_string_parsed(self, tmp_path: Path) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        content = (
            "---\n"
            "name: my-agent\n"
            "description: Test agent\n"
            "knowledge_packs: agent-baton, ai-orchestration\n"
            "---\n"
            "# Body\n"
        )
        (agents_dir / "my-agent.md").write_text(content, encoding="utf-8")

        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        agent = reg.get("my-agent")
        assert agent is not None
        assert agent.knowledge_packs == ["agent-baton", "ai-orchestration"]

    def test_knowledge_packs_missing_defaults_to_empty(self, tmp_path: Path) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        content = (
            "---\n"
            "name: my-agent\n"
            "description: Test agent\n"
            "---\n"
            "# Body\n"
        )
        (agents_dir / "my-agent.md").write_text(content, encoding="utf-8")

        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        agent = reg.get("my-agent")
        assert agent is not None
        assert agent.knowledge_packs == []

    def test_knowledge_packs_single_entry_list(self, tmp_path: Path) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        content = (
            "---\n"
            "name: my-agent\n"
            "description: Test agent\n"
            "knowledge_packs: [compliance]\n"
            "---\n"
            "# Body\n"
        )
        (agents_dir / "my-agent.md").write_text(content, encoding="utf-8")

        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        agent = reg.get("my-agent")
        assert agent.knowledge_packs == ["compliance"]
