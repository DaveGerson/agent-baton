"""Stack-detection priority tests for bd-5a7c.

Regression coverage for the planner's stack misidentification bug: when a
repo contains both ``pyproject.toml`` (root) and ``package.json`` /
``tsconfig.json`` (root or subdir), Python must win so the planner emits
``pytest`` gates rather than ``npm test`` / ``npx tsc --noEmit``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter, StackProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect(root: Path) -> StackProfile:
    return AgentRouter(AgentRegistry()).detect_stack(root)


def _write(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Single-language root signals
# ---------------------------------------------------------------------------


def test_root_pyproject_only_returns_python(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    profile = _detect(tmp_path)
    assert profile.language == "python"
    assert "python" in profile.languages


def test_root_package_json_only_returns_node(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", '{"name":"app"}\n')
    profile = _detect(tmp_path)
    assert profile.language == "javascript"
    assert "javascript" in profile.languages


# ---------------------------------------------------------------------------
# Both root signals — Python wins, Node noted as secondary
# ---------------------------------------------------------------------------


def test_both_root_signals_prefer_python_with_node_secondary(tmp_path: Path) -> None:
    """pyproject.toml + package.json at root → Python primary, Node noted."""
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "package.json", '{"name":"app"}\n')
    profile = _detect(tmp_path)
    assert profile.language == "python"
    assert "javascript" in profile.languages
    assert "python" in profile.languages


def test_both_root_signals_with_tsconfig_prefer_python(tmp_path: Path) -> None:
    """pyproject.toml + tsconfig.json at root (the bd-5a7c shape) → Python."""
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "tsconfig.json", '{"compilerOptions":{}}\n')
    profile = _detect(tmp_path)
    assert profile.language == "python", (
        f"Root pyproject.toml must beat root tsconfig.json; got {profile.language}"
    )
    assert "typescript" in profile.languages


# ---------------------------------------------------------------------------
# Root vs. subdir priority (the canonical orchestrator-v2 shape)
# ---------------------------------------------------------------------------


def test_root_pyproject_beats_subdir_package_json(tmp_path: Path) -> None:
    """Root pyproject.toml + subdir/package.json → Python (rejects Node from subdir)."""
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "pmo-ui" / "package.json", '{"name":"pmo-ui"}\n')
    _write(tmp_path / "pmo-ui" / "tsconfig.json", "{}\n")
    profile = _detect(tmp_path)
    assert profile.language == "python"
    # Subdir Node signal should still be reported in languages so the
    # planner can know about the multi-stack monorepo.
    assert "javascript" in profile.languages or "typescript" in profile.languages


# ---------------------------------------------------------------------------
# Multi-stack: neither at root, both in subdirs
# ---------------------------------------------------------------------------


def test_neither_at_root_both_subdirs_emits_multi_stack(tmp_path: Path) -> None:
    """subdir/pyproject.toml + subdir/package.json (no root signals) →
    multi-stack so the planner can produce both gate commands."""
    _write(tmp_path / "backend" / "pyproject.toml", "[project]\nname='b'\n")
    _write(tmp_path / "frontend" / "package.json", '{"name":"f"}\n')
    profile = _detect(tmp_path)
    # Both languages must surface in StackProfile.languages.
    assert "python" in profile.languages
    assert "javascript" in profile.languages
    # Primary language should be one of them (deterministic ordering not
    # required, but it must be set so routing can pick a flavor).
    assert profile.language in {"python", "javascript"}


# ---------------------------------------------------------------------------
# Regression: the actual orchestrator-v2 repo root (THE bd-5a7c bug)
# ---------------------------------------------------------------------------


def test_orchestrator_v2_repo_root_is_python() -> None:
    """The actual repo MUST detect as Python.

    This is the original bd-5a7c reproduction: repo root has pyproject.toml
    AND tsconfig.json (and pmo-ui/ has package.json + vite.config.ts).
    Before the fix, detection returned ``typescript`` and the planner
    emitted ``npx tsc --noEmit`` build gates and ``npm test`` test gates.
    """
    # tests/orchestration/test_stack_detection.py → repo root is parents[2]
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").exists():
        pytest.skip("repo root does not contain pyproject.toml — wrong layout")
    profile = AgentRouter(AgentRegistry()).detect_stack(repo_root)
    assert profile.language == "python", (
        f"orchestrator-v2 root must detect as Python (bd-5a7c regression); "
        f"got {profile.language!r} (files={profile.detected_files})"
    )


# ---------------------------------------------------------------------------
# StackProfile.languages list — defaults and dedup
# ---------------------------------------------------------------------------


def test_stack_profile_default_languages_is_empty_list() -> None:
    profile = StackProfile()
    assert profile.languages == []


def test_languages_deduplicated_when_signal_appears_multiple_places(
    tmp_path: Path,
) -> None:
    """Same language at root + subdir should appear only once in languages."""
    _write(tmp_path / "pyproject.toml", "[project]\nname='a'\n")
    _write(tmp_path / "subpkg" / "pyproject.toml", "[project]\nname='b'\n")
    profile = _detect(tmp_path)
    assert profile.languages.count("python") == 1


# ---------------------------------------------------------------------------
# Gate-command propagation (integration with planner)
# ---------------------------------------------------------------------------


def test_python_stack_yields_pytest_gates_not_node(tmp_path: Path) -> None:
    """When detect_stack returns Python the planner's _default_gate must
    emit ``pytest`` for the test gate and not ``npm test`` / ``npx tsc``."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "pmo-ui" / "package.json", '{"name":"pmo-ui"}\n')

    stack = _detect(tmp_path)
    assert stack.language == "python"

    # Bypass the heavy __init__; we only need the bound _default_gate method.
    planner = IntelligentPlanner.__new__(IntelligentPlanner)
    test_gate = planner._default_gate("Test", stack=stack)
    build_gate = planner._default_gate("Implement", stack=stack)

    assert test_gate is not None
    assert "pytest" in test_gate.command
    assert "npm" not in test_gate.command
    assert "tsc" not in test_gate.command

    assert build_gate is not None
    assert "pytest" in build_gate.command
    assert "npx tsc" not in build_gate.command
    assert "npm test" not in build_gate.command


def test_javascript_stack_still_yields_node_gates(tmp_path: Path) -> None:
    """Sanity check: a true Node project still gets npm/tsc gates."""
    from agent_baton.core.engine.planner import IntelligentPlanner

    _write(tmp_path / "package.json", '{"name":"app"}\n')
    _write(tmp_path / "tsconfig.json", "{}\n")
    stack = _detect(tmp_path)
    assert stack.language == "typescript"

    planner = IntelligentPlanner.__new__(IntelligentPlanner)
    build_gate = planner._default_gate("Implement", stack=stack)
    assert build_gate is not None
    assert "tsc" in build_gate.command


# ---------------------------------------------------------------------------
# bd-75e8 / bd-fb2d: FRAMEWORK_SIGNALS + csharp glob root-priority regressions
# ---------------------------------------------------------------------------


def test_root_python_beats_subdir_next_config_js(tmp_path: Path) -> None:
    """bd-75e8: Python at root + subdir/next.config.js → Python primary.

    Before the fix, FRAMEWORK_SIGNALS unconditionally clobbered
    profile.language with the first matching subdir signal, so a Python
    backend with a Next.js frontend in pmo-ui/ was misdetected as
    JavaScript/React.
    """
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "pmo-ui" / "next.config.js", "module.exports = {};\n")
    profile = _detect(tmp_path)
    assert profile.language == "python", (
        f"Root pyproject.toml must beat subdir next.config.js; got "
        f"{profile.language!r} (files={profile.detected_files})"
    )
    # Subdir framework hint should still be discoverable for routing.
    assert "react" in profile.frameworks
    assert "javascript" in profile.languages


def test_root_python_beats_vendored_csproj(tmp_path: Path) -> None:
    """bd-fb2d: Python at root + vendored/foo.csproj → Python primary.

    Before the fix, the .csproj glob walked every scan_dir and
    unconditionally overwrote profile.language = "csharp" when any vendored
    sample/sub-repo contained a .csproj.
    """
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "vendored" / "foo.csproj", "<Project/>\n")
    profile = _detect(tmp_path)
    assert profile.language == "python", (
        f"Root pyproject.toml must beat vendored .csproj; got "
        f"{profile.language!r} (files={profile.detected_files})"
    )
    # csharp should still be visible as a secondary language.
    assert "csharp" in profile.languages


def test_root_csproj_wins_over_subdir_package_json(tmp_path: Path) -> None:
    """A real .NET repo (root .csproj) + subdir/package.json → csharp."""
    _write(tmp_path / "MyApp.csproj", "<Project/>\n")
    _write(tmp_path / "frontend" / "package.json", '{"name":"f"}\n')
    profile = _detect(tmp_path)
    assert profile.language == "csharp", (
        f"Root .csproj must keep csharp as primary; got {profile.language!r}"
    )
    assert "csharp" in profile.languages


def test_multi_stack_python_root_js_subdir_dotnet_vendored(tmp_path: Path) -> None:
    """Python at root + JS subdir + vendored .NET → primary=python, all three
    languages surfaced in profile.languages."""
    _write(tmp_path / "pyproject.toml", "[project]\nname='app'\n")
    _write(tmp_path / "frontend" / "package.json", '{"name":"f"}\n')
    _write(tmp_path / "frontend" / "next.config.js", "module.exports = {};\n")
    _write(tmp_path / "vendored" / "sample.csproj", "<Project/>\n")
    profile = _detect(tmp_path)
    assert profile.language == "python"
    assert "python" in profile.languages
    assert "javascript" in profile.languages
    assert "csharp" in profile.languages
    # And the framework hint from the subdir survives.
    assert "react" in profile.frameworks


def test_root_framework_signal_beats_subdir_framework(tmp_path: Path) -> None:
    """Root manage.py (Django) + subdir next.config.js → django wins.

    Direct bd-75e8 regression: even when the FRAMEWORK_SIGNALS dict order
    would visit next.config.js before manage.py, root must win.
    """
    _write(tmp_path / "manage.py", "# django\n")
    _write(tmp_path / "ui" / "next.config.js", "module.exports = {};\n")
    profile = _detect(tmp_path)
    assert profile.language == "python"
    assert profile.framework == "django"
    # Subdir framework still recorded.
    assert "react" in profile.frameworks
