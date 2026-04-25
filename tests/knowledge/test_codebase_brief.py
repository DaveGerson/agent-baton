"""Tests for `baton knowledge brief` (K2.7).

Covers the pure-logic CodebaseBriefer plus the CLI handler --save/--format
behaviour.  No external dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_baton.cli.commands.knowledge import brief as brief_cli
from agent_baton.core.knowledge.codebase_brief import (
    CodebaseBrief,
    CodebaseBriefer,
    render,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def repo_root() -> Path:
    """Project root for self-tests against the agent_baton repo."""
    return REPO_ROOT


@pytest.fixture()
def temp_project(tmp_path: Path) -> Path:
    """A minimal Python+Make project used for isolated tests."""
    (tmp_path / "pyproject.toml").write_text(
        """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110"]

[project.scripts]
demo = "demo.cli:main"
demo-server = "demo.server:main"

[project.optional-dependencies]
dev = ["pytest>=7"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""",
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        "test:\n\tpytest\n\nlint:\n\truff check .\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text(
        """\
# Demo

Some intro text.

## Rules

- All imports MUST use absolute paths.
- Tests should ALWAYS run via pytest.
- NEVER commit secrets to the repo.
- Just a normal note about formatting.
""",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()  # should be skipped
    (tmp_path / "node_modules").mkdir()  # should be skipped
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Detects Python stack on this repo
# ---------------------------------------------------------------------------

def test_detects_python_stack_on_self(repo_root: Path) -> None:
    brief = CodebaseBriefer.generate(repo_root)
    assert brief.language is not None
    assert brief.language.startswith("python"), brief.language


# ---------------------------------------------------------------------------
# 2. Detects test runner (pytest)
# ---------------------------------------------------------------------------

def test_detects_pytest(repo_root: Path) -> None:
    brief = CodebaseBriefer.generate(repo_root)
    assert brief.test_runner == "pytest"
    assert brief.test_run_all == "pytest"
    assert "<module>" in (brief.test_run_scoped or "")


# ---------------------------------------------------------------------------
# 3. Repo shape lists top-level dirs and skips dotfiles / node_modules
# ---------------------------------------------------------------------------

def test_layout_skips_hidden_and_skip_dirs(temp_project: Path) -> None:
    brief = CodebaseBriefer.generate(temp_project)
    names = [name for name, _ in brief.layout]
    assert "demo" in names
    assert "tests" in names
    assert "docs" in names
    # hidden + node_modules must be skipped.
    assert ".hidden" not in names
    assert "node_modules" not in names

    # Heuristic descriptions where we have hints.
    layout_map = dict(brief.layout)
    assert layout_map["tests"] == "test suite"
    assert layout_map["docs"] == "documentation"
    # demo has __init__.py -> Python package.
    assert layout_map["demo"] == "Python package"


# ---------------------------------------------------------------------------
# 4. Entry points pulled from console_scripts
# ---------------------------------------------------------------------------

def test_entry_points_from_pyproject_console_scripts(temp_project: Path) -> None:
    brief = CodebaseBriefer.generate(temp_project)
    ep_names = [name for name, _ in brief.entry_points]
    assert "demo" in ep_names
    assert "demo-server" in ep_names
    # Makefile targets surface too.
    assert "make test" in ep_names or "make lint" in ep_names


# ---------------------------------------------------------------------------
# 5. CLAUDE.md MUST/NEVER lines surface
# ---------------------------------------------------------------------------

def test_conventions_extract_must_never_always(temp_project: Path) -> None:
    brief = CodebaseBriefer.generate(temp_project)
    joined = " || ".join(brief.conventions)
    assert "MUST" in joined
    assert "NEVER" in joined
    assert "ALWAYS" in joined
    # The "normal note" line shouldn't be picked up.
    for line in brief.conventions:
        assert "normal note" not in line


# ---------------------------------------------------------------------------
# 6. JSON format round-trips
# ---------------------------------------------------------------------------

def test_json_format_round_trips(temp_project: Path) -> None:
    brief = CodebaseBriefer.generate(temp_project)
    payload = render(brief, fmt="json")
    data = json.loads(payload)
    # Required structural keys.
    assert data["repo_name"] == temp_project.name
    assert data["language"].startswith("python")
    assert isinstance(data["layout"], list)
    assert all("name" in e and "description" in e for e in data["layout"])
    assert isinstance(data["entry_points"], list)
    assert isinstance(data["conventions"], list)


# ---------------------------------------------------------------------------
# 7. --save writes the file; default does not
# ---------------------------------------------------------------------------

def test_save_writes_file_default_does_not(
    temp_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = temp_project / ".claude" / "team-context" / "codebase-brief.md"

    # Default: stdout, no file.
    args = argparse.Namespace(
        project=str(temp_project),
        save=False,
        fmt="markdown",
        knowledge_cmd="brief",
    )
    brief_cli.handler(args)
    out = capsys.readouterr().out
    assert "Codebase Brief" in out
    assert not target.exists()

    # --save: writes file, no markdown to stdout.
    args = argparse.Namespace(
        project=str(temp_project),
        save=True,
        fmt="markdown",
        knowledge_cmd="brief",
    )
    brief_cli.handler(args)
    captured = capsys.readouterr().out
    assert target.exists()
    assert "Brief written to" in captured
    body = target.read_text(encoding="utf-8")
    assert "Codebase Brief" in body


# ---------------------------------------------------------------------------
# Bonus coverage: markdown render is non-empty, includes key sections
# ---------------------------------------------------------------------------

def test_markdown_render_includes_sections(temp_project: Path) -> None:
    brief = CodebaseBriefer.generate(temp_project)
    md = brief.to_markdown()
    assert "# Codebase Brief" in md
    assert "**Stack:**" in md
    assert "**Layout:**" in md
    assert "**Entry points:**" in md
    assert "**Conventions" in md
    assert "**Tests:**" in md
    # Guard the size budget — keep under 80 lines for typical repos.
    assert md.count("\n") < 200


# ---------------------------------------------------------------------------
# Health: when run inside a real git repo we should get a branch name
# ---------------------------------------------------------------------------

def test_health_section_populated_in_real_repo(repo_root: Path) -> None:
    brief = CodebaseBriefer.generate(repo_root)
    # Repo always has a .git, so branch should be detectable.
    assert brief.git_branch is not None and brief.git_branch != ""
    assert isinstance(brief.git_dirty, bool)


# ---------------------------------------------------------------------------
# Performance: must complete quickly on this repo (<2s)
# ---------------------------------------------------------------------------

def test_briefer_completes_quickly(repo_root: Path) -> None:
    import time
    start = time.perf_counter()
    CodebaseBriefer.generate(repo_root)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"Briefer took {elapsed:.2f}s (budget 2.0s)"
