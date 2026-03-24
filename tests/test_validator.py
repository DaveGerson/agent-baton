"""Tests for agent_baton.core.validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.govern.validator import AgentValidator, ValidationResult


@pytest.fixture
def validator() -> AgentValidator:
    return AgentValidator()


def _write_agent(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


VALID_AGENT = """\
---
name: test-agent
description: |
  A test agent for validation.
  Multi-line description.
model: sonnet
permissionMode: auto-edit
tools: Read, Write, Edit
---

# Test Agent

You are a test agent.
"""

MINIMAL_VALID = """\
---
name: simple
description: |
  A simple agent.
  Two lines.
model: sonnet
---

# Simple Agent

Instructions here.
"""


# ── Error cases ──────────────────────────────────────────────
# DECISION: Merged 13 individual error-case tests into 2 parametrized tests
# grouped by error type: structural errors (frontmatter issues) and field
# validation errors (missing/invalid field values). test_nonexistent_file
# kept separate: it uses a different code path (file read failure).


class TestValidatorErrors:
    @pytest.mark.parametrize("content,expected_error", [
        # structural errors
        ("# Just markdown\n\nNo frontmatter.", "missing frontmatter"),
        ("---\nname: broken\n", "not closed"),
        ("---\n: :\n  bad: [yaml\n---\n# Body\n", "YAML is invalid"),
        # missing/empty required fields
        ("---\ndescription: has desc\n---\n# Body\n", "'name' field is required"),
        ("---\nname: \"\"\ndescription: has desc\n---\n# Body\n", "'name' field is required"),
        # invalid field values
        (
            "---\nname: CamelCase\ndescription: |\n  Has desc.\n  Two lines.\n---\n# Body\n",
            "kebab-case",
        ),
        (
            "---\nname: my agent\ndescription: |\n  Desc.\n  Two.\n---\n# Body\n",
            "kebab-case",
        ),
        ("---\nname: my-agent\n---\n# Body\n", "'description' field is required"),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\nmodel: gpt4\n---\n# Body\n",
            "'model' must be one of",
        ),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\npermissionMode: yolo\n---\n# Body\n",
            "'permissionMode' must be one of",
        ),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\ntools: Read, Execute, Write\n---\n# Body\n",
            "invalid tool",
        ),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\n---\n",
            "empty",
        ),
    ])
    def test_invalid_content_produces_error(
        self, tmp_path: Path, validator: AgentValidator, content: str, expected_error: str
    ):
        p = _write_agent(tmp_path, "bad.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any(expected_error in e for e in result.errors)

    def test_nonexistent_file(self, tmp_path: Path, validator: AgentValidator):
        p = tmp_path / "does-not-exist.md"
        result = validator.validate_file(p)
        assert not result.valid
        assert any("cannot read" in e for e in result.errors)


# ── Warning cases ────────────────────────────────────────────
# DECISION: Merged 6 warning tests into 1 parametrized test.
# test_reviewer_with_auto_edit and test_auditor_with_auto_edit are both
# testing the "reviewer/auditor" warning — combined into one parameter tuple each.


class TestValidatorWarnings:
    @pytest.mark.parametrize("content,expected_warning", [
        (
            "---\nname: my-agent\ndescription: Short desc\nmodel: sonnet\n---\n# Body\nContent\n",
            "multi-line",
        ),
        (
            "---\nname: different-name\ndescription: |\n  Desc.\n  Two.\nmodel: sonnet\n---\n# Body\nContent\n",
            "does not match filename",
        ),
        (
            "---\nname: code-reviewer\ndescription: |\n  A code reviewer.\n  Two lines.\nmodel: sonnet\npermissionMode: auto-edit\n---\n# Body\nContent\n",
            "reviewer/auditor",
        ),
        (
            "---\nname: auditor\ndescription: |\n  An auditor.\n  Two lines.\nmodel: opus\npermissionMode: auto-edit\n---\n# Body\nContent\n",
            "reviewer/auditor",
        ),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\n---\n# Body\nContent\n",
            "'model' field should be present",
        ),
        (
            "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\nmodel: sonnet\n---\nJust text, no heading.\n",
            "top-level heading",
        ),
    ])
    def test_content_produces_warning(
        self,
        tmp_path: Path,
        validator: AgentValidator,
        content: str,
        expected_warning: str,
    ):
        fname = "my-agent.md"
        # For name-mismatch test, use a filename that doesn't match "different-name"
        if "different-name" in content:
            fname = "my-agent.md"
        elif "code-reviewer" in content:
            fname = "code-reviewer.md"
        elif "auditor" in content and "name: auditor" in content:
            fname = "auditor.md"
        p = _write_agent(tmp_path, fname, content)
        result = validator.validate_file(p)
        assert result.valid
        assert any(expected_warning in w for w in result.warnings)


# ── Valid cases ──────────────────────────────────────────────
# DECISION: Merged test_fully_valid_agent and test_minimal_valid_agent into
# a single parametrized test. test_flavored_name and test_all_valid_tools
# are distinct edge cases (double-dash name, full tool list) kept separate.


class TestValidatorValid:
    @pytest.mark.parametrize("fname,content", [
        ("test-agent.md", VALID_AGENT),
        ("simple.md", MINIMAL_VALID),
    ])
    def test_valid_agent(self, tmp_path: Path, validator: AgentValidator, fname: str, content: str):
        p = _write_agent(tmp_path, fname, content)
        result = validator.validate_file(p)
        assert result.valid
        assert result.errors == []

    def test_flavored_name_is_valid_kebab(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: backend-engineer--python\ndescription: |\n  A python specialist.\n  Two lines.\nmodel: sonnet\n---\n# Python Engineer\nContent\n"
        p = _write_agent(tmp_path, "backend-engineer--python.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert result.errors == []

    def test_all_valid_tools(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: all-tools\ndescription: |\n  All tools.\n  Two lines.\nmodel: sonnet\ntools: Read, Write, Edit, Glob, Grep, Bash, Agent\n---\n# All Tools\nContent\n"
        p = _write_agent(tmp_path, "all-tools.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert result.errors == []


# ── Directory validation ─────────────────────────────────────
# DECISION: Merged test_validates_all_md_files, test_skips_non_md_files,
# test_mixed_valid_and_invalid into parametrized test. test_empty_directory
# and test_nonexistent_directory kept separate (distinct boundary conditions).


class TestValidateDirectory:
    @pytest.mark.parametrize("files,extra_files,expected_count,expected_valid_count", [
        # all valid md files
        (
            [("good.md", VALID_AGENT), ("also-good.md", MINIMAL_VALID)],
            [],
            2,
            2,
        ),
        # skips non-.md files
        (
            [("good.md", VALID_AGENT)],
            [("readme.txt", "not an agent")],
            1,
            1,
        ),
        # mixed valid and invalid
        (
            [("good.md", VALID_AGENT), ("bad.md", "# No frontmatter\n")],
            [],
            2,
            1,
        ),
    ])
    def test_directory_validation(
        self,
        tmp_path: Path,
        validator: AgentValidator,
        files: list,
        extra_files: list,
        expected_count: int,
        expected_valid_count: int,
    ):
        for fname, content in files:
            _write_agent(tmp_path, fname, content)
        for fname, content in extra_files:
            (tmp_path / fname).write_text(content)
        results = validator.validate_directory(tmp_path)
        assert len(results) == expected_count
        assert sum(1 for r in results if r.valid) == expected_valid_count

    def test_empty_directory(self, tmp_path: Path, validator: AgentValidator):
        results = validator.validate_directory(tmp_path)
        assert results == []

    def test_nonexistent_directory(self, tmp_path: Path, validator: AgentValidator):
        results = validator.validate_directory(tmp_path / "nope")
        assert len(results) == 1
        assert not results[0].valid

    def test_spec_path_set_to_root(self, tmp_path: Path, validator: AgentValidator):
        results = validator.validate_directory(tmp_path / "nope")
        # nonexistent dir returns a single failure result
        assert len(results) == 1

# ── ValidationResult dataclass ───────────────────────────────
# DECISION: Removed test_fields_are_stored — it is trivial field storage
# after constructor call. Kept test_default_lists_are_empty (non-obvious
# mutable default behavior worth verifying).


class TestValidationResult:
    def test_default_lists_are_empty(self):
        r = ValidationResult(path=Path("x.md"), valid=True)
        assert r.errors == []
        assert r.warnings == []


# ── Real agent files ─────────────────────────────────────────
# DECISION: Merged test_all_distributable_agents_pass and
# test_all_project_agents_pass into a parametrized test over directories.


class TestValidateRealAgents:
    """Validate the actual distributable agent files in agents/."""

    @pytest.mark.parametrize("agents_dir_str", ["agents", ".claude/agents"])
    def test_agents_in_directory_pass(
        self, validator: AgentValidator, agents_dir_str: str
    ):
        agents_dir = Path(agents_dir_str)
        if not agents_dir.is_dir():
            pytest.skip(f"{agents_dir_str}/ directory not found (not running from repo root)")
        results = validator.validate_directory(agents_dir)
        assert len(results) > 0
        for r in results:
            assert r.valid, f"{r.path}: {r.errors}"
