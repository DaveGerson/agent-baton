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
