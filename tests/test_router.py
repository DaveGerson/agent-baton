"""Tests for agent_baton.core.router.AgentRouter and detect_stack."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter, StackProfile


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


def test_mixed_stack_root_python_subdir_node(tmp_path: Path) -> None:
    """Root pyproject.toml should win over subdirectory package.json."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    frontend = tmp_path / "pmo-ui"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"pmo-ui"}\n', encoding="utf-8")
    (frontend / "tsconfig.json").write_text("{}\n", encoding="utf-8")
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.language == "python", (
        f"Root pyproject.toml should take priority; got {profile.language}"
    )


def test_mixed_stack_routes_to_python_flavor(tmp_path: Path) -> None:
    """In a mixed Python+JS project, backend-engineer should route to python."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"ui"}\n', encoding="utf-8")
    registry = _make_registry_with("backend-engineer", "backend-engineer--python", "backend-engineer--node")
    router = AgentRouter(registry)
    assert router.route("backend-engineer", project_root=tmp_path) == "backend-engineer--python"


def test_subdir_only_node_still_routes_to_node(tmp_path: Path) -> None:
    """When there's no root signal, subdirectory signals should still work."""
    subdir = tmp_path / "app"
    subdir.mkdir()
    (subdir / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.language == "javascript"


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


# ---------------------------------------------------------------------------
# Fix 3: Vite + React stack detection
# ---------------------------------------------------------------------------


def _write_package_json(path: Path, deps: dict[str, str], dev_deps: dict[str, str] | None = None) -> None:
    import json
    pkg = {"name": "my-app", "dependencies": deps}
    if dev_deps:
        pkg["devDependencies"] = dev_deps
    (path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


@pytest.mark.parametrize("vite_filename", [
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
])
def test_vite_react_detected_as_react_framework(tmp_path: Path, vite_filename: str) -> None:
    """vite.config.* + package.json with react dep → (javascript, react)."""
    (tmp_path / vite_filename).write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {"react": "^18.0.0", "react-dom": "^18.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.language == "javascript"
    assert profile.framework == "react"


def test_vite_react_detected_files_includes_vite_config(tmp_path: Path) -> None:
    """vite.config.ts path should appear in detected_files."""
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {"react": "^18.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert any("vite.config.ts" in f for f in profile.detected_files)


def test_vite_without_react_dep_not_detected_as_react(tmp_path: Path) -> None:
    """vite.config.ts + package.json without 'react' → framework remains None."""
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {"vue": "^3.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.framework != "react"


def test_vite_react_in_dev_deps_detected(tmp_path: Path) -> None:
    """react in devDependencies (not dependencies) should still trigger detection."""
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {}, dev_deps={"react": "^18.0.0", "@vitejs/plugin-react": "^4.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.framework == "react"


def test_vite_react_in_subdir_detected(tmp_path: Path) -> None:
    """vite.config.ts + react in a subdirectory should be detected."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(frontend, {"react": "^18.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    assert profile.framework == "react"


def test_next_config_takes_priority_over_vite(tmp_path: Path) -> None:
    """next.config.js (an explicit framework signal) should win over vite detection."""
    (tmp_path / "next.config.js").write_text("module.exports = {}\n", encoding="utf-8")
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {"react": "^18.0.0", "next": "^14.0.0"})
    profile = AgentRouter(AgentRegistry()).detect_stack(tmp_path)
    # framework should still be react (from next.config.js), not overridden
    assert profile.framework == "react"


def test_vite_react_routes_to_react_flavor(tmp_path: Path) -> None:
    """A Vite+React project should route frontend-engineer to frontend-engineer--react."""
    (tmp_path / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    _write_package_json(tmp_path, {"react": "^18.0.0"})
    registry = _make_registry_with("frontend-engineer", "frontend-engineer--react")
    router = AgentRouter(registry)
    assert router.route("frontend-engineer", project_root=tmp_path) == "frontend-engineer--react"


# ---------------------------------------------------------------------------
# Fix 2: references/baton-engine.md exists and is distributed by install.sh
# ---------------------------------------------------------------------------


def test_baton_engine_reference_exists() -> None:
    """references/baton-engine.md must exist so install.sh can copy it."""
    import os
    # Find the repo root by walking up from this file
    this_file = Path(__file__).resolve()
    repo_root = this_file.parent.parent
    ref_path = repo_root / "references" / "baton-engine.md"
    assert ref_path.exists(), (
        f"references/baton-engine.md not found at {ref_path}. "
        "The file must exist so install.sh copies it to ~/.claude/references/."
    )


def test_baton_engine_reference_has_frontmatter() -> None:
    """baton-engine.md must have YAML frontmatter with a name field."""
    import os
    this_file = Path(__file__).resolve()
    repo_root = this_file.parent.parent
    ref_path = repo_root / "references" / "baton-engine.md"
    if not ref_path.exists():
        pytest.skip("baton-engine.md not present — covered by existence test")
    content = ref_path.read_text(encoding="utf-8")
    assert content.startswith("---"), "baton-engine.md must start with YAML frontmatter (---)"
    assert "name:" in content, "baton-engine.md frontmatter must include a 'name:' field"


def test_install_sh_copies_all_md_references() -> None:
    """install.sh must contain a loop that copies *.md from the references dir."""
    this_file = Path(__file__).resolve()
    repo_root = this_file.parent.parent
    install_sh = repo_root / "scripts" / "install.sh"
    assert install_sh.exists(), "scripts/install.sh must exist"
    content = install_sh.read_text(encoding="utf-8")
    # The script iterates over $REFS_DIR/*.md and copies them
    assert "$REFS_DIR" in content, "install.sh must reference REFS_DIR"
    assert "*.md" in content, "install.sh must glob *.md files from references dir"
    assert "cp " in content, "install.sh must use cp to copy reference files"
