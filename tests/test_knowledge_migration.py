"""Verify that the migrated knowledge packs load correctly via KnowledgeRegistry.

Checks that all 3 packs in .claude/knowledge/ have:
- knowledge.yaml manifests with non-empty name and description
- All .md docs indexed with non-empty name and description metadata
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.knowledge import KnowledgePack, KnowledgeDocument


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_KNOWLEDGE_DIR = Path(__file__).parent.parent / ".claude" / "knowledge"

EXPECTED_PACKS = {
    "agent-baton": {
        "target_agents": ["backend-engineer--python", "architect", "ai-systems-architect"],
        "docs": ["agent-format", "architecture", "development-workflow"],
    },
    "ai-orchestration": {
        "target_agents": ["ai-systems-architect", "architect", "ai-product-strategist"],
        "docs": [
            "agent-evaluation",
            "context-economics",
            "multi-agent-patterns",
            "prompt-engineering-principles",
        ],
    },
    "case-studies": {
        "target_agents": [],  # broadly applicable
        "docs": ["failure-modes", "orchestration-frameworks", "scaling-patterns"],
    },
}


@pytest.fixture(scope="module")
def registry() -> KnowledgeRegistry:
    """KnowledgeRegistry loaded from the project's .claude/knowledge/ directory."""
    assert PROJECT_KNOWLEDGE_DIR.is_dir(), (
        f"Project knowledge directory not found: {PROJECT_KNOWLEDGE_DIR}"
    )
    reg = KnowledgeRegistry()
    count = reg.load_directory(PROJECT_KNOWLEDGE_DIR)
    assert count == 3, f"Expected 3 packs to load, got {count}"
    return reg


# ---------------------------------------------------------------------------
# TestAllThreePacksPresent
# ---------------------------------------------------------------------------

class TestAllThreePacksPresent:
    def test_all_expected_packs_are_indexed(self, registry: KnowledgeRegistry) -> None:
        loaded_names = set(registry.all_packs.keys())
        for pack_name in EXPECTED_PACKS:
            assert pack_name in loaded_names, (
                f"Pack '{pack_name}' not found in registry. Loaded: {loaded_names}"
            )

    def test_no_extra_packs_loaded(self, registry: KnowledgeRegistry) -> None:
        loaded_names = set(registry.all_packs.keys())
        assert loaded_names == set(EXPECTED_PACKS.keys()), (
            f"Registry has unexpected packs. Expected: {set(EXPECTED_PACKS)}, "
            f"got: {loaded_names}"
        )


# ---------------------------------------------------------------------------
# TestPackManifestMetadata
# ---------------------------------------------------------------------------

class TestPackManifestMetadata:
    @pytest.mark.parametrize("pack_name", list(EXPECTED_PACKS.keys()))
    def test_pack_has_non_empty_description(
        self, registry: KnowledgeRegistry, pack_name: str
    ) -> None:
        pack = registry.get_pack(pack_name)
        assert pack is not None
        assert pack.description, (
            f"Pack '{pack_name}' has empty description — knowledge.yaml is missing or incomplete"
        )

    @pytest.mark.parametrize("pack_name", list(EXPECTED_PACKS.keys()))
    def test_pack_has_non_empty_tags(
        self, registry: KnowledgeRegistry, pack_name: str
    ) -> None:
        pack = registry.get_pack(pack_name)
        assert pack is not None
        assert pack.tags, (
            f"Pack '{pack_name}' has no tags — planner strict-matching will miss it"
        )

    def test_agent_baton_targets_correct_agents(
        self, registry: KnowledgeRegistry
    ) -> None:
        pack = registry.get_pack("agent-baton")
        assert pack is not None
        expected = {"backend-engineer--python", "architect", "ai-systems-architect"}
        assert set(pack.target_agents) == expected

    def test_ai_orchestration_targets_correct_agents(
        self, registry: KnowledgeRegistry
    ) -> None:
        pack = registry.get_pack("ai-orchestration")
        assert pack is not None
        expected = {"ai-systems-architect", "architect", "ai-product-strategist"}
        assert set(pack.target_agents) == expected

    def test_case_studies_has_empty_target_agents(
        self, registry: KnowledgeRegistry
    ) -> None:
        """case-studies is broadly applicable — no targeted agents."""
        pack = registry.get_pack("case-studies")
        assert pack is not None
        assert pack.target_agents == []

    @pytest.mark.parametrize("pack_name", list(EXPECTED_PACKS.keys()))
    def test_pack_source_path_points_to_real_directory(
        self, registry: KnowledgeRegistry, pack_name: str
    ) -> None:
        pack = registry.get_pack(pack_name)
        assert pack is not None
        assert pack.source_path is not None
        assert pack.source_path.is_dir(), (
            f"Pack '{pack_name}' source_path {pack.source_path} is not a directory"
        )


# ---------------------------------------------------------------------------
# TestDocumentCount
# ---------------------------------------------------------------------------

class TestDocumentCount:
    @pytest.mark.parametrize("pack_name,pack_info", EXPECTED_PACKS.items())
    def test_pack_has_expected_document_count(
        self,
        registry: KnowledgeRegistry,
        pack_name: str,
        pack_info: dict,
    ) -> None:
        pack = registry.get_pack(pack_name)
        assert pack is not None
        expected_count = len(pack_info["docs"])
        assert len(pack.documents) == expected_count, (
            f"Pack '{pack_name}' has {len(pack.documents)} docs, "
            f"expected {expected_count}: {[d.name for d in pack.documents]}"
        )

    @pytest.mark.parametrize("pack_name,pack_info", EXPECTED_PACKS.items())
    def test_all_expected_docs_are_present(
        self,
        registry: KnowledgeRegistry,
        pack_name: str,
        pack_info: dict,
    ) -> None:
        pack = registry.get_pack(pack_name)
        assert pack is not None
        loaded_doc_names = {d.name for d in pack.documents}
        for expected_doc in pack_info["docs"]:
            assert expected_doc in loaded_doc_names, (
                f"Doc '{expected_doc}' missing from pack '{pack_name}'. "
                f"Found: {loaded_doc_names}"
            )


# ---------------------------------------------------------------------------
# TestDocumentFrontmatterMetadata
# ---------------------------------------------------------------------------

class TestDocumentFrontmatterMetadata:
    """Every migrated doc must have non-empty name and description from frontmatter."""

    def _all_docs(self, registry: KnowledgeRegistry) -> list[tuple[str, KnowledgeDocument]]:
        """Return (pack_name, doc) pairs for all docs in all packs."""
        result = []
        for pack_name, pack in registry.all_packs.items():
            for doc in pack.documents:
                result.append((pack_name, doc))
        return result

    def test_all_docs_have_non_empty_name(self, registry: KnowledgeRegistry) -> None:
        for pack_name, doc in self._all_docs(registry):
            assert doc.name, (
                f"Doc in pack '{pack_name}' has empty name — "
                f"check frontmatter in {doc.source_path}"
            )

    def test_all_docs_have_non_empty_description(
        self, registry: KnowledgeRegistry
    ) -> None:
        for pack_name, doc in self._all_docs(registry):
            assert doc.description, (
                f"Doc '{doc.name}' in pack '{pack_name}' has empty description — "
                f"frontmatter is missing or incomplete in {doc.source_path}"
            )

    def test_all_docs_have_non_empty_tags(self, registry: KnowledgeRegistry) -> None:
        for pack_name, doc in self._all_docs(registry):
            assert doc.tags, (
                f"Doc '{doc.name}' in pack '{pack_name}' has no tags — "
                f"planner tag-matching will not find it"
            )

    def test_all_docs_have_valid_priority(self, registry: KnowledgeRegistry) -> None:
        valid_priorities = {"high", "normal", "low"}
        for pack_name, doc in self._all_docs(registry):
            assert doc.priority in valid_priorities, (
                f"Doc '{doc.name}' in pack '{pack_name}' has invalid priority "
                f"'{doc.priority}'. Must be one of {valid_priorities}"
            )

    def test_all_docs_have_positive_token_estimate(
        self, registry: KnowledgeRegistry
    ) -> None:
        for pack_name, doc in self._all_docs(registry):
            assert doc.token_estimate > 0, (
                f"Doc '{doc.name}' in pack '{pack_name}' has token_estimate=0 — "
                f"file may be unreadable at {doc.source_path}"
            )

    def test_all_doc_source_paths_exist(self, registry: KnowledgeRegistry) -> None:
        for pack_name, doc in self._all_docs(registry):
            assert doc.source_path is not None
            assert doc.source_path.is_file(), (
                f"Doc '{doc.name}' source_path {doc.source_path} does not exist"
            )

    def test_all_docs_content_empty_at_index_time(
        self, registry: KnowledgeRegistry
    ) -> None:
        """Content must be lazy — never loaded at index time."""
        for pack_name, doc in self._all_docs(registry):
            assert doc.content == "", (
                f"Doc '{doc.name}' in pack '{pack_name}' has non-empty content "
                f"at index time — registry loaded content eagerly"
            )


# ---------------------------------------------------------------------------
# TestSpecificDocMetadata
# ---------------------------------------------------------------------------

class TestSpecificDocMetadata:
    """Spot-checks on specific documents to verify frontmatter values are correct."""

    def test_agent_format_doc_has_high_priority(
        self, registry: KnowledgeRegistry
    ) -> None:
        doc = registry.get_document("agent-baton", "agent-format")
        assert doc is not None
        assert doc.priority == "high"

    def test_architecture_doc_has_high_priority(
        self, registry: KnowledgeRegistry
    ) -> None:
        doc = registry.get_document("agent-baton", "architecture")
        assert doc is not None
        assert doc.priority == "high"

    def test_context_economics_has_high_priority(
        self, registry: KnowledgeRegistry
    ) -> None:
        doc = registry.get_document("ai-orchestration", "context-economics")
        assert doc is not None
        assert doc.priority == "high"

    def test_failure_modes_has_high_priority(
        self, registry: KnowledgeRegistry
    ) -> None:
        doc = registry.get_document("case-studies", "failure-modes")
        assert doc is not None
        assert doc.priority == "high"

    def test_context_economics_has_token_tags(
        self, registry: KnowledgeRegistry
    ) -> None:
        doc = registry.get_document("ai-orchestration", "context-economics")
        assert doc is not None
        assert "tokens" in doc.tags or "cost" in doc.tags, (
            f"context-economics should have token/cost tags, got: {doc.tags}"
        )

    def test_failure_modes_has_risk_tags(self, registry: KnowledgeRegistry) -> None:
        doc = registry.get_document("case-studies", "failure-modes")
        assert doc is not None
        assert "failure-modes" in doc.tags or "risk" in doc.tags, (
            f"failure-modes should have failure/risk tags, got: {doc.tags}"
        )


# ---------------------------------------------------------------------------
# TestRegistryQueries
# ---------------------------------------------------------------------------

class TestRegistryQueries:
    """Verify that migrated packs are discoverable via the registry's query API."""

    def test_packs_for_backend_engineer_includes_agent_baton(
        self, registry: KnowledgeRegistry
    ) -> None:
        packs = registry.packs_for_agent("backend-engineer--python")
        pack_names = [p.name for p in packs]
        assert "agent-baton" in pack_names

    def test_packs_for_ai_systems_architect_includes_both_packs(
        self, registry: KnowledgeRegistry
    ) -> None:
        packs = registry.packs_for_agent("ai-systems-architect")
        pack_names = [p.name for p in packs]
        assert "agent-baton" in pack_names
        assert "ai-orchestration" in pack_names

    def test_packs_for_ai_product_strategist_includes_ai_orchestration(
        self, registry: KnowledgeRegistry
    ) -> None:
        packs = registry.packs_for_agent("ai-product-strategist")
        pack_names = [p.name for p in packs]
        assert "ai-orchestration" in pack_names

    def test_case_studies_not_returned_by_packs_for_agent(
        self, registry: KnowledgeRegistry
    ) -> None:
        """case-studies has no target_agents — packs_for_agent should not return it."""
        packs = registry.packs_for_agent("backend-engineer--python")
        pack_names = [p.name for p in packs]
        assert "case-studies" not in pack_names

    def test_find_by_tags_orchestration_returns_docs(
        self, registry: KnowledgeRegistry
    ) -> None:
        docs = registry.find_by_tags({"orchestration"})
        assert len(docs) > 0, "No docs found for 'orchestration' tag"

    def test_find_by_tags_tokens_returns_context_economics(
        self, registry: KnowledgeRegistry
    ) -> None:
        docs = registry.find_by_tags({"tokens"})
        names = [d.name for d in docs]
        assert "context-economics" in names

    def test_find_by_tags_failure_modes_returns_failure_doc(
        self, registry: KnowledgeRegistry
    ) -> None:
        docs = registry.find_by_tags({"failure-modes"})
        names = [d.name for d in docs]
        assert "failure-modes" in names

    def test_tfidf_search_architecture_returns_relevant_docs(
        self, registry: KnowledgeRegistry
    ) -> None:
        results = registry.search("package layout architecture design")
        doc_names = [d.name for d, _s in results]
        assert "architecture" in doc_names

    def test_tfidf_search_context_window_returns_context_economics(
        self, registry: KnowledgeRegistry
    ) -> None:
        results = registry.search("token cost context window budget")
        doc_names = [d.name for d, _s in results]
        assert "context-economics" in doc_names

    def test_tfidf_search_failure_returns_failure_modes(
        self, registry: KnowledgeRegistry
    ) -> None:
        results = registry.search("agent drift hallucination scope creep failure")
        doc_names = [d.name for d, _s in results]
        assert "failure-modes" in doc_names
