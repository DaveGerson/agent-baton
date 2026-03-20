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
# detect_stack
# ---------------------------------------------------------------------------

class TestDetectStackPythonProject:
    def test_pyproject_toml_signals_python(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "python"

    def test_requirements_txt_signals_python(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "python"

    def test_setup_py_signals_python(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "python"

    def test_manage_py_signals_python_django(self, tmp_path: Path):
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "python"
        assert profile.framework == "django"

    def test_wsgi_py_signals_python_django(self, tmp_path: Path):
        (tmp_path / "wsgi.py").write_text("application = get_wsgi_application()\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.framework == "django"

    def test_detected_files_includes_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert "pyproject.toml" in profile.detected_files


class TestDetectStackJavascriptProject:
    def test_package_json_signals_javascript(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "javascript"

    def test_tsconfig_json_signals_typescript(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}\n', encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "typescript"

    def test_next_config_js_signals_javascript_react(self, tmp_path: Path):
        (tmp_path / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "javascript"
        assert profile.framework == "react"

    def test_next_config_ts_signals_typescript_react(self, tmp_path: Path):
        (tmp_path / "next.config.ts").write_text("export default {}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "typescript"
        assert profile.framework == "react"

    def test_next_config_mjs_signals_javascript_react(self, tmp_path: Path):
        (tmp_path / "next.config.mjs").write_text("export default {}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.framework == "react"

    def test_nuxt_config_signals_vue(self, tmp_path: Path):
        (tmp_path / "nuxt.config.js").write_text("export default {}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.framework == "vue"

    def test_angular_json_signals_angular(self, tmp_path: Path):
        (tmp_path / "angular.json").write_text("{}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.framework == "angular"

    def test_svelte_config_signals_svelte(self, tmp_path: Path):
        (tmp_path / "svelte.config.js").write_text("export default {}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.framework == "svelte"


class TestDetectStackOtherLanguages:
    def test_cargo_toml_signals_rust(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'app'\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "rust"

    def test_go_mod_signals_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "go"

    def test_gemfile_signals_ruby(self, tmp_path: Path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "ruby"

    def test_build_gradle_signals_java(self, tmp_path: Path):
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "java"

    def test_pom_xml_signals_java(self, tmp_path: Path):
        (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "java"

    def test_appsettings_json_signals_csharp_dotnet(self, tmp_path: Path):
        (tmp_path / "appsettings.json").write_text("{}\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "csharp"
        assert profile.framework == "dotnet"

    def test_csproj_file_signals_csharp(self, tmp_path: Path):
        (tmp_path / "MyApp.csproj").write_text("<Project/>\n", encoding="utf-8")
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language == "csharp"


class TestDetectStackEmptyDirectory:
    def test_empty_directory_returns_unknown_language(self, tmp_path: Path):
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.language is None
        assert profile.framework is None

    def test_empty_directory_has_no_detected_files(self, tmp_path: Path):
        router = AgentRouter(AgentRegistry())
        profile = router.detect_stack(tmp_path)
        assert profile.detected_files == []


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------

class TestRoute:
    def test_python_stack_routes_to_python_flavor(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", project_root=tmp_path)
        assert result == "backend-engineer--python"

    def test_javascript_react_routes_to_react_flavor(self, tmp_path: Path):
        (tmp_path / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
        registry = _make_registry_with("frontend-engineer", "frontend-engineer--react")
        router = AgentRouter(registry)
        result = router.route("frontend-engineer", project_root=tmp_path)
        assert result == "frontend-engineer--react"

    def test_falls_back_to_base_when_flavor_not_in_registry(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
        # Only the base agent is in the registry — no --python flavor
        registry = _make_registry_with("backend-engineer")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", project_root=tmp_path)
        assert result == "backend-engineer"

    def test_unknown_stack_returns_base_name(self, tmp_path: Path):
        # Empty directory — no signals
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", project_root=tmp_path)
        assert result == "backend-engineer"

    def test_accepts_prebuilt_stack_profile(self):
        stack = StackProfile(language="python", framework=None)
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", stack=stack)
        assert result == "backend-engineer--python"

    def test_django_stack_routes_to_python_flavor(self, tmp_path: Path):
        """Django maps to python flavor via FLAVOR_MAP fallback."""
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", project_root=tmp_path)
        assert result == "backend-engineer--python"

    def test_javascript_node_backend_routing(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
        registry = _make_registry_with("backend-engineer", "backend-engineer--node")
        router = AgentRouter(registry)
        result = router.route("backend-engineer", project_root=tmp_path)
        assert result == "backend-engineer--node"

    def test_no_matching_flavor_returns_exact_base_string(self, tmp_path: Path):
        """When flavor lookup yields nothing, route() returns the base_name string."""
        stack = StackProfile(language=None, framework=None)
        registry = _make_registry_with("test-engineer")
        router = AgentRouter(registry)
        result = router.route("test-engineer", stack=stack)
        assert result == "test-engineer"


# ---------------------------------------------------------------------------
# route_team()
# ---------------------------------------------------------------------------

class TestRouteTeam:
    def test_routes_multiple_roles(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
        registry = _make_registry_with(
            "backend-engineer",
            "backend-engineer--python",
            "test-engineer",
        )
        router = AgentRouter(registry)
        result = router.route_team(
            ["backend-engineer", "test-engineer"],
            project_root=tmp_path,
        )
        assert result["backend-engineer"] == "backend-engineer--python"
        assert result["test-engineer"] == "test-engineer"

    def test_returns_dict_keyed_by_base_role(self, tmp_path: Path):
        registry = _make_registry_with("architect")
        router = AgentRouter(registry)
        result = router.route_team(["architect"], project_root=tmp_path)
        assert "architect" in result

    def test_empty_roles_returns_empty_dict(self, tmp_path: Path):
        registry = AgentRegistry()
        router = AgentRouter(registry)
        result = router.route_team([], project_root=tmp_path)
        assert result == {}

    def test_accepts_prebuilt_stack_profile(self):
        stack = StackProfile(language="python", framework=None)
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        result = router.route_team(["backend-engineer"], stack=stack)
        assert result["backend-engineer"] == "backend-engineer--python"

    def test_each_role_resolved_independently(self, tmp_path: Path):
        (tmp_path / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
        registry = _make_registry_with(
            "frontend-engineer",
            "frontend-engineer--react",
            "backend-engineer",
            "backend-engineer--node",
        )
        router = AgentRouter(registry)
        result = router.route_team(
            ["frontend-engineer", "backend-engineer"],
            project_root=tmp_path,
        )
        assert result["frontend-engineer"] == "frontend-engineer--react"
        assert result["backend-engineer"] == "backend-engineer--node"
