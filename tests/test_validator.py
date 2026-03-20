"""Tests for agent_baton.core.validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.validator import AgentValidator, ValidationResult


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


class TestValidatorErrors:
    def test_missing_frontmatter(self, tmp_path: Path, validator: AgentValidator):
        p = _write_agent(tmp_path, "bad.md", "# Just markdown\n\nNo frontmatter.")
        result = validator.validate_file(p)
        assert not result.valid
        assert any("missing frontmatter" in e for e in result.errors)

    def test_unclosed_frontmatter(self, tmp_path: Path, validator: AgentValidator):
        p = _write_agent(tmp_path, "bad.md", "---\nname: broken\n")
        result = validator.validate_file(p)
        assert not result.valid
        assert any("not closed" in e for e in result.errors)

    def test_invalid_yaml(self, tmp_path: Path, validator: AgentValidator):
        p = _write_agent(tmp_path, "bad.md", "---\n: :\n  bad: [yaml\n---\n# Body\n")
        result = validator.validate_file(p)
        assert not result.valid
        assert any("YAML is invalid" in e for e in result.errors)

    def test_missing_name(self, tmp_path: Path, validator: AgentValidator):
        content = "---\ndescription: has desc\n---\n# Body\n"
        p = _write_agent(tmp_path, "noname.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("'name' field is required" in e for e in result.errors)

    def test_empty_name(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: \"\"\ndescription: has desc\n---\n# Body\n"
        p = _write_agent(tmp_path, "empty.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("'name' field is required" in e for e in result.errors)

    def test_non_kebab_case_name(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: CamelCase\ndescription: |\n  Has desc.\n  Two lines.\n---\n# Body\n"
        p = _write_agent(tmp_path, "CamelCase.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("kebab-case" in e for e in result.errors)

    def test_name_with_spaces(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my agent\ndescription: |\n  Desc.\n  Two.\n---\n# Body\n"
        p = _write_agent(tmp_path, "bad.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("kebab-case" in e for e in result.errors)

    def test_missing_description(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\n---\n# Body\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("'description' field is required" in e for e in result.errors)

    def test_invalid_model(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\nmodel: gpt4\n---\n# Body\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("'model' must be one of" in e for e in result.errors)

    def test_invalid_permission_mode(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\npermissionMode: yolo\n---\n# Body\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("'permissionMode' must be one of" in e for e in result.errors)

    def test_invalid_tool(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\ntools: Read, Execute, Write\n---\n# Body\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("invalid tool" in e for e in result.errors)

    def test_empty_body(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\n---\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert not result.valid
        assert any("body" in e and "empty" in e for e in result.errors)

    def test_nonexistent_file(self, tmp_path: Path, validator: AgentValidator):
        p = tmp_path / "does-not-exist.md"
        result = validator.validate_file(p)
        assert not result.valid
        assert any("cannot read" in e for e in result.errors)


# ── Warning cases ────────────────────────────────────────────


class TestValidatorWarnings:
    def test_single_line_description(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: Short desc\nmodel: sonnet\n---\n# Body\nContent\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("multi-line" in w for w in result.warnings)

    def test_name_mismatch_filename(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: different-name\ndescription: |\n  Desc.\n  Two.\nmodel: sonnet\n---\n# Body\nContent\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("does not match filename" in w for w in result.warnings)

    def test_reviewer_with_auto_edit(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: code-reviewer\ndescription: |\n  A code reviewer.\n  Two lines.\nmodel: sonnet\npermissionMode: auto-edit\n---\n# Body\nContent\n"
        p = _write_agent(tmp_path, "code-reviewer.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("reviewer/auditor" in w for w in result.warnings)

    def test_auditor_with_auto_edit(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: auditor\ndescription: |\n  An auditor.\n  Two lines.\nmodel: opus\npermissionMode: auto-edit\n---\n# Body\nContent\n"
        p = _write_agent(tmp_path, "auditor.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("reviewer/auditor" in w for w in result.warnings)

    def test_missing_model_warns(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\n---\n# Body\nContent\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("'model' field should be present" in w for w in result.warnings)

    def test_no_heading_in_body(self, tmp_path: Path, validator: AgentValidator):
        content = "---\nname: my-agent\ndescription: |\n  Desc.\n  Two.\nmodel: sonnet\n---\nJust text, no heading.\n"
        p = _write_agent(tmp_path, "my-agent.md", content)
        result = validator.validate_file(p)
        assert result.valid
        assert any("top-level heading" in w for w in result.warnings)


# ── Valid cases ──────────────────────────────────────────────


class TestValidatorValid:
    def test_fully_valid_agent(self, tmp_path: Path, validator: AgentValidator):
        p = _write_agent(tmp_path, "test-agent.md", VALID_AGENT)
        result = validator.validate_file(p)
        assert result.valid
        assert result.errors == []

    def test_minimal_valid_agent(self, tmp_path: Path, validator: AgentValidator):
        p = _write_agent(tmp_path, "simple.md", MINIMAL_VALID)
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


class TestValidateDirectory:
    def test_validates_all_md_files(self, tmp_path: Path, validator: AgentValidator):
        _write_agent(tmp_path, "good.md", VALID_AGENT)
        _write_agent(tmp_path, "also-good.md", MINIMAL_VALID)
        results = validator.validate_directory(tmp_path)
        assert len(results) == 2
        assert all(r.valid for r in results)

    def test_skips_non_md_files(self, tmp_path: Path, validator: AgentValidator):
        _write_agent(tmp_path, "good.md", VALID_AGENT)
        (tmp_path / "readme.txt").write_text("not an agent")
        results = validator.validate_directory(tmp_path)
        assert len(results) == 1

    def test_empty_directory(self, tmp_path: Path, validator: AgentValidator):
        results = validator.validate_directory(tmp_path)
        assert results == []

    def test_nonexistent_directory(self, tmp_path: Path, validator: AgentValidator):
        results = validator.validate_directory(tmp_path / "nope")
        assert len(results) == 1
        assert not results[0].valid

    def test_mixed_valid_and_invalid(self, tmp_path: Path, validator: AgentValidator):
        _write_agent(tmp_path, "good.md", VALID_AGENT)
        _write_agent(tmp_path, "bad.md", "# No frontmatter\n")
        results = validator.validate_directory(tmp_path)
        assert len(results) == 2
        valid_count = sum(1 for r in results if r.valid)
        assert valid_count == 1


# ── ValidationResult dataclass ───────────────────────────────


class TestValidationResult:
    def test_default_lists_are_empty(self):
        r = ValidationResult(path=Path("x.md"), valid=True)
        assert r.errors == []
        assert r.warnings == []

    def test_fields_are_stored(self):
        r = ValidationResult(
            path=Path("test.md"),
            valid=False,
            errors=["e1"],
            warnings=["w1"],
        )
        assert r.path == Path("test.md")
        assert not r.valid
        assert r.errors == ["e1"]
        assert r.warnings == ["w1"]


# ── Real agent files ─────────────────────────────────────────


class TestValidateRealAgents:
    """Validate the actual distributable agent files in agents/."""

    def test_all_distributable_agents_pass(self, validator: AgentValidator):
        agents_dir = Path("agents")
        if not agents_dir.is_dir():
            pytest.skip("agents/ directory not found (not running from repo root)")
        results = validator.validate_directory(agents_dir)
        assert len(results) > 0
        for r in results:
            assert r.valid, f"{r.path}: {r.errors}"

    def test_all_project_agents_pass(self, validator: AgentValidator):
        project_dir = Path(".claude/agents")
        if not project_dir.is_dir():
            pytest.skip(".claude/agents/ not found")
        results = validator.validate_directory(project_dir)
        assert len(results) > 0
        for r in results:
            assert r.valid, f"{r.path}: {r.errors}"
