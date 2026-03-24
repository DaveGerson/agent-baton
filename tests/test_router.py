"""Tests for agent_baton.core.router.AgentRouter and detect_stack."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.registry import AgentRegistry
from agent_baton.core.router import AgentRouter, StackProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry_with(*agent_names: str) -> AgentRegistry:
    """Build an in-memory registry from a list of agent name strings."""
    import tempfile, os

    registry = AgentRegistry()
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        for name in agent_names:
            (d / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {name}\n---\nbody\n",
                encoding="utf-8",
            )
        registry.load_directory(d)
    return registry


# ---------------------------------------------------------------------------
# detect_stack — Python, JS/TS, other languages, and empty directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename,content,expected_language,expected_framework", [
    # Python
    ("pyproject.toml", "[project]\nname='app'\n", "python", None),
    ("requirements.txt", "flask\n", "python", None),
    ("setup.py", "from setuptools import setup\n", "python", None),
    ("manage.py", "#!/usr/bin/env python\n", "python", "django"),
    ("wsgi.py", "application = get_wsgi_application()\n", "python", "django"),
    # JavaScript / TypeScript
    ("package.json", '{"name":"app"}\n', "javascript", None),
    ("tsconfig.json", '{"compilerOptions":{}}\n', "typescript", None),
    ("next.config.js", "module.exports = {}\n", "javascript", "react"),
    ("next.config.ts", "export default {}\n", "typescript", "react"),
    ("next.config.mjs", "export default {}\n", None, "react"),
    ("nuxt.config.js", "export default {}\n", None, "vue"),
    ("angular.json", "{}\n", None, "angular"),
    ("svelte.config.js", "export default {}\n", None, "svelte"),
    # Other languages
    ("Cargo.toml", "[package]\nname = 'app'\n", "rust", None),
    ("go.mod", "module example.com/app\n", "go", None),
    ("Gemfile", "source 'https://rubygems.org'\n", "ruby", None),
    ("build.gradle", "apply plugin: 'java'\n", "java", None),
    ("pom.xml", "<project/>\n", "java", None),
    ("appsettings.json", "{}\n", "csharp", "dotnet"),
    ("MyApp.csproj", "<Project/>\n", "csharp", None),
])
def test_detect_stack_language_and_framework(
    tmp_path: Path,
    filename: str,
    content: str,
    expected_language: str | None,
    expected_framework: str | None,
) -> None:
    (tmp_path / filename).write_text(content, encoding="utf-8")
    router = AgentRouter(AgentRegistry())
    profile = router.detect_stack(tmp_path)
    if expected_language is not None:
        assert profile.language == expected_language
    if expected_framework is not None:
        assert profile.framework == expected_framework


def test_pyproject_toml_captured_in_detected_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert "pyproject.toml" in profile.detected_files


@pytest.mark.parametrize("attribute,expected", [
    ("language", None),
    ("framework", None),
    ("detected_files", []),
])
def test_empty_directory_profile(tmp_path: Path, attribute: str, expected) -> None:
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert getattr(profile, attribute) == expected


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("setup_file,setup_content,base_agent,flavor_agent,expected_result", [
    # python stack → python flavor
    ("pyproject.toml", "[project]\nname='app'\n", "backend-engineer", "backend-engineer--python", "backend-engineer--python"),
    # javascript/react → react flavor
    ("next.config.js", "module.exports = {}\n", "frontend-engineer", "frontend-engineer--react", "frontend-engineer--react"),
    # django (manage.py) maps to python flavor via FLAVOR_MAP
    ("manage.py", "#!/usr/bin/env python\n", "backend-engineer", "backend-engineer--python", "backend-engineer--python"),
    # javascript → node flavor for backend
    ("package.json", '{"name":"app"}\n', "backend-engineer", "backend-engineer--node", "backend-engineer--node"),
])
def test_route_with_stack_file(
    tmp_path: Path,
    setup_file: str,
    setup_content: str,
    base_agent: str,
    flavor_agent: str,
    expected_result: str,
) -> None:
    (tmp_path / setup_file).write_text(setup_content, encoding="utf-8")
    registry = _make_registry_with(base_agent, flavor_agent)
    router = AgentRouter(registry)
    assert router.route(base_agent, project_root=tmp_path) == expected_result


def test_route_falls_back_to_base_when_flavor_absent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    registry = _make_registry_with("backend-engineer")
    assert AgentRouter(registry).route("backend-engineer", project_root=tmp_path) == "backend-engineer"


def test_route_unknown_stack_returns_base(tmp_path: Path) -> None:
    registry = _make_registry_with("backend-engineer", "backend-engineer--python")
    assert AgentRouter(registry).route("backend-engineer", project_root=tmp_path) == "backend-engineer"


def test_route_accepts_prebuilt_stack_profile() -> None:
    stack = StackProfile(language="python", framework=None)
    registry = _make_registry_with("backend-engineer", "backend-engineer--python")
    assert AgentRouter(registry).route("backend-engineer", stack=stack) == "backend-engineer--python"


def test_route_no_matching_flavor_returns_base_string() -> None:
    stack = StackProfile(language=None, framework=None)
    registry = _make_registry_with("test-engineer")
    assert AgentRouter(registry).route("test-engineer", stack=stack) == "test-engineer"


# ---------------------------------------------------------------------------
# route_team()
# ---------------------------------------------------------------------------


def test_route_team_routes_multiple_roles(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    registry = _make_registry_with("backend-engineer", "backend-engineer--python", "test-engineer")
    result = AgentRouter(registry).route_team(
        ["backend-engineer", "test-engineer"], project_root=tmp_path
    )
    assert result["backend-engineer"] == "backend-engineer--python"
    assert result["test-engineer"] == "test-engineer"


def test_route_team_resolves_each_role_independently(tmp_path: Path) -> None:
    (tmp_path / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
    registry = _make_registry_with(
        "frontend-engineer", "frontend-engineer--react",
        "backend-engineer", "backend-engineer--node",
    )
    result = AgentRouter(registry).route_team(
        ["frontend-engineer", "backend-engineer"], project_root=tmp_path
    )
    assert result["frontend-engineer"] == "frontend-engineer--react"
    assert result["backend-engineer"] == "backend-engineer--node"


def test_route_team_returns_dict_keyed_by_base_role(tmp_path: Path) -> None:
    registry = _make_registry_with("architect")
    result = AgentRouter(registry).route_team(["architect"], project_root=tmp_path)
    assert "architect" in result


def test_route_team_empty_roles_returns_empty_dict(tmp_path: Path) -> None:
    result = AgentRouter(AgentRegistry()).route_team([], project_root=tmp_path)
    assert result == {}


def test_route_team_accepts_prebuilt_stack_profile() -> None:
    stack = StackProfile(language="python", framework=None)
    registry = _make_registry_with("backend-engineer", "backend-engineer--python")
    result = AgentRouter(registry).route_team(["backend-engineer"], stack=stack)
    assert result["backend-engineer"] == "backend-engineer--python"
