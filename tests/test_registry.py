"""Tests for agent_baton.core.registry.AgentRegistry."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.registry import AgentRegistry
from agent_baton.models.enums import AgentCategory


class TestLoadDirectory:
    def test_loads_all_md_files(self, tmp_agents_dir: Path):
        registry = AgentRegistry()
        count = registry.load_directory(tmp_agents_dir)
        assert count == 5

    def test_returns_zero_for_empty_directory(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        registry = AgentRegistry()
        count = registry.load_directory(empty)
        assert count == 0

    def test_returns_zero_for_nonexistent_directory(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist"
        registry = AgentRegistry()
        count = registry.load_directory(missing)
        assert count == 0

    def test_ignores_non_md_files(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "notes.txt").write_text("not an agent", encoding="utf-8")
        (agents_dir / "data.json").write_text("{}", encoding="utf-8")
        valid_md = (
            "---\nname: real-agent\ndescription: test\n---\n# Body\n"
        )
        (agents_dir / "real-agent.md").write_text(valid_md, encoding="utf-8")
        registry = AgentRegistry()
        count = registry.load_directory(agents_dir)
        assert count == 1

    def test_name_derived_from_filename_when_missing_from_frontmatter(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        # No 'name' key in frontmatter
        (agents_dir / "my-custom-agent.md").write_text(
            "---\ndescription: custom\n---\n# Body\n", encoding="utf-8"
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        assert registry.get("my-custom-agent") is not None

    def test_plain_markdown_without_frontmatter_still_loads(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "no-frontmatter-agent.md").write_text(
            "# Just a heading\n\nSome content.", encoding="utf-8"
        )
        registry = AgentRegistry()
        count = registry.load_directory(agents_dir)
        assert count == 1
        # Name should be derived from filename
        assert registry.get("no-frontmatter-agent") is not None

    def test_agents_property_reflects_loaded_agents(self, tmp_agents_dir: Path):
        registry = AgentRegistry()
        registry.load_directory(tmp_agents_dir)
        agents = registry.agents
        assert isinstance(agents, dict)
        assert len(agents) == 5

    def test_names_property_returns_all_names(self, tmp_agents_dir: Path):
        registry = AgentRegistry()
        registry.load_directory(tmp_agents_dir)
        names = registry.names
        assert isinstance(names, list)
        assert "architect" in names
        assert "backend-engineer--python" in names


class TestOverrideMode:
    def test_without_override_existing_agent_is_not_replaced(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        global_dir.mkdir()
        project_dir.mkdir()

        global_content = (
            "---\nname: architect\ndescription: Global architect\nmodel: sonnet\n---\nbody\n"
        )
        project_content = (
            "---\nname: architect\ndescription: Project architect\nmodel: opus\n---\nbody\n"
        )
        (global_dir / "architect.md").write_text(global_content, encoding="utf-8")
        (project_dir / "architect.md").write_text(project_content, encoding="utf-8")

        registry = AgentRegistry()
        registry.load_directory(global_dir)
        registry.load_directory(project_dir, override=False)

        agent = registry.get("architect")
        assert agent.description == "Global architect"

    def test_with_override_project_agent_wins(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        global_dir.mkdir()
        project_dir.mkdir()

        global_content = (
            "---\nname: architect\ndescription: Global architect\nmodel: sonnet\n---\nbody\n"
        )
        project_content = (
            "---\nname: architect\ndescription: Project architect\nmodel: opus\n---\nbody\n"
        )
        (global_dir / "architect.md").write_text(global_content, encoding="utf-8")
        (project_dir / "architect.md").write_text(project_content, encoding="utf-8")

        registry = AgentRegistry()
        registry.load_directory(global_dir)
        registry.load_directory(project_dir, override=True)

        agent = registry.get("architect")
        assert agent.description == "Project architect"
        assert agent.model == "opus"

    def test_override_count_includes_overwritten_agents(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        project_dir = tmp_path / "project"
        global_dir.mkdir()
        project_dir.mkdir()

        (global_dir / "architect.md").write_text(
            "---\nname: architect\ndescription: g\n---\nbody\n", encoding="utf-8"
        )
        (project_dir / "architect.md").write_text(
            "---\nname: architect\ndescription: p\n---\nbody\n", encoding="utf-8"
        )

        registry = AgentRegistry()
        registry.load_directory(global_dir)
        count = registry.load_directory(project_dir, override=True)
        assert count == 1


class TestGet:
    # DECISION: removed trivial test_get_returns_none_when_registry_empty — it
    # tests the same path as test_get_missing_name_returns_none (returns None for
    # absent key). Both share the same code path; the distinction has no value.
    @pytest.mark.parametrize("name,expected_name", [
        ("architect", "architect"),
        ("backend-engineer--python", "backend-engineer--python"),
    ])
    def test_get_known_name_returns_agent(
        self, registry_with_agents: AgentRegistry, name: str, expected_name: str
    ):
        agent = registry_with_agents.get(name)
        assert agent is not None
        assert agent.name == expected_name

    def test_get_missing_name_returns_none(self, registry_with_agents: AgentRegistry):
        assert registry_with_agents.get("nonexistent-agent") is None


class TestGetFlavors:
    def test_returns_only_flavored_variants(self, registry_with_agents: AgentRegistry):
        flavors = registry_with_agents.get_flavors("backend-engineer")
        names = [a.name for a in flavors]
        assert "backend-engineer--python" in names
        assert "backend-engineer--node" in names

    def test_does_not_return_base_agent(self, registry_with_agents: AgentRegistry):
        # If a base "backend-engineer" were loaded, get_flavors should not return it
        flavors = registry_with_agents.get_flavors("backend-engineer")
        for agent in flavors:
            assert agent.is_flavored is True

    @pytest.mark.parametrize("base_name", [
        "architect",       # has no flavored variants in the fixture
        "nonexistent-agent",  # unknown base
    ])
    def test_returns_empty_when_no_flavors_exist(
        self, registry_with_agents: AgentRegistry, base_name: str
    ):
        flavors = registry_with_agents.get_flavors(base_name)
        assert flavors == []


class TestGetBase:
    def test_base_name_returns_agent(self, registry_with_agents: AgentRegistry):
        agent = registry_with_agents.get_base("architect")
        assert agent is not None
        assert agent.name == "architect"

    def test_flavored_name_returns_base(self, tmp_path: Path):
        """get_base with a flavored name looks up just the base part."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend-engineer.md").write_text(
            "---\nname: backend-engineer\ndescription: base\n---\nbody\n",
            encoding="utf-8",
        )
        (agents_dir / "backend-engineer--python.md").write_text(
            "---\nname: backend-engineer--python\ndescription: python flavor\n---\nbody\n",
            encoding="utf-8",
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        agent = registry.get_base("backend-engineer--python")
        assert agent is not None
        assert agent.name == "backend-engineer"

    @pytest.mark.parametrize("name", [
        "backend-engineer--python",  # base "backend-engineer" not in fixture
        "nonexistent",
    ])
    def test_returns_none_when_base_not_found(
        self, registry_with_agents: AgentRegistry, name: str
    ):
        assert registry_with_agents.get_base(name) is None


class TestFindBestMatch:
    # DECISION: parameterized the five find_best_match tests into two groups:
    # fixture-based lookups (1 parametrized test) and ones needing their own
    # tmp_path setup (kept as standalone). The "flavor_search_is_exact_match"
    # case (py != python) is included as a tuple since it exercises a distinct
    # code path (no match → None).
    @pytest.mark.parametrize("base,flavor,expected_name", [
        ("backend-engineer", "python", "backend-engineer--python"),  # exact flavor match
        ("backend-engineer", "py", None),                            # inexact flavor → no base → None
        ("nonexistent-agent", "python", None),                       # nothing matches
    ])
    def test_find_best_match_fixture(
        self,
        registry_with_agents: AgentRegistry,
        base: str,
        flavor: str | None,
        expected_name: str | None,
    ):
        result = registry_with_agents.find_best_match(base, flavor)
        if expected_name is None:
            assert result is None
        else:
            assert result is not None
            assert result.name == expected_name

    def test_falls_back_to_base_when_flavor_missing(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend-engineer.md").write_text(
            "---\nname: backend-engineer\ndescription: base\n---\nbody\n",
            encoding="utf-8",
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        result = registry.find_best_match("backend-engineer", "rust")
        assert result is not None
        assert result.name == "backend-engineer"

    def test_returns_base_when_no_flavor_specified(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "architect.md").write_text(
            "---\nname: architect\ndescription: arch\n---\nbody\n",
            encoding="utf-8",
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        result = registry.find_best_match("architect")
        assert result is not None
        assert result.name == "architect"


class TestByCategory:
    # DECISION: parameterized the five category tests into two groups:
    # "present" cases (which agents appear under a category) and the "empty"
    # case. The category_returns_list check is folded into the present cases
    # by asserting isinstance inside one shared parametrized test.
    @pytest.mark.parametrize("category,expected_names", [
        (
            AgentCategory.ENGINEERING,
            ["architect", "backend-engineer--python", "backend-engineer--node", "frontend-engineer--react"],
        ),
        (
            AgentCategory.REVIEW,
            ["security-reviewer"],
        ),
    ])
    def test_category_contains_expected_agents(
        self,
        registry_with_agents: AgentRegistry,
        category: AgentCategory,
        expected_names: list[str],
    ):
        agents = registry_with_agents.by_category(category)
        assert isinstance(agents, list)
        names = [a.name for a in agents]
        for expected in expected_names:
            assert expected in names

    def test_empty_category_returns_empty_list(self, registry_with_agents: AgentRegistry):
        # No DATA category agents in the fixture
        agents = registry_with_agents.by_category(AgentCategory.DATA)
        assert agents == []


class TestToolsParsing:
    # DECISION: parameterized the three tools-parsing tests into one. All share
    # the same setup (write one agent .md file, load it, call .get().tools).
    @pytest.mark.parametrize("frontmatter_snippet,expected_tools", [
        ("tools: Read, Write, Edit\n", ["Read", "Write", "Edit"]),
        ("tools:\n  - Read\n  - Bash\n", ["Read", "Bash"]),
        ("", []),  # no tools field → defaults to empty list
    ])
    def test_tools_parsing(
        self, tmp_path: Path, frontmatter_snippet: str, expected_tools: list[str]
    ):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test-agent.md").write_text(
            f"---\nname: test-agent\ndescription: d\n{frontmatter_snippet}---\nbody\n",
            encoding="utf-8",
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        agent = registry.get("test-agent")
        assert agent.tools == expected_tools
