"""Regression tests for the section-aware retrospective Markdown parser.

Covers: parse_markdown_sections() in agent_baton.core.observe.retrospective.

bd-47bc: Replace brittle line-by-line regex with section-aware parser that
tolerates whitespace variation, indentation, alternative bullet chars,
Windows line endings, and blank lines.
"""
from __future__ import annotations

import pytest

from agent_baton.core.observe.retrospective import parse_markdown_sections


# ---------------------------------------------------------------------------
# test_parses_h2_sections
# ---------------------------------------------------------------------------

def test_parses_h2_sections() -> None:
    md = (
        "## What Worked\n"
        "- Agent A succeeded\n"
        "- Agent B completed on time\n"
        "\n"
        "## What Didn't Work\n"
        "- Agent C timed out\n"
    )
    result = parse_markdown_sections(md)

    assert "What Worked" in result
    assert result["What Worked"] == ["Agent A succeeded", "Agent B completed on time"]
    assert "What Didn't Work" in result
    assert result["What Didn't Work"] == ["Agent C timed out"]


# ---------------------------------------------------------------------------
# test_parses_h3_subsections
# ---------------------------------------------------------------------------

def test_parses_h3_subsections() -> None:
    md = (
        "### Knowledge Gaps\n"
        "- Missing schema docs\n"
        "- No context on auth flow\n"
        "\n"
        "### Roster Recommendations\n"
        "- **Create:** security-reviewer\n"
    )
    result = parse_markdown_sections(md)

    assert "Knowledge Gaps" in result
    assert result["Knowledge Gaps"] == ["Missing schema docs", "No context on auth flow"]
    assert "Roster Recommendations" in result
    assert result["Roster Recommendations"] == ["**Create:** security-reviewer"]


# ---------------------------------------------------------------------------
# test_handles_windows_line_endings
# ---------------------------------------------------------------------------

def test_handles_windows_line_endings() -> None:
    md = "## What Worked\r\n- Agent A succeeded\r\n- Agent B done\r\n"
    result = parse_markdown_sections(md)

    assert "What Worked" in result
    assert result["What Worked"] == ["Agent A succeeded", "Agent B done"]


# ---------------------------------------------------------------------------
# test_handles_extra_indentation
# ---------------------------------------------------------------------------

def test_handles_extra_indentation() -> None:
    md = (
        "## Observations\n"
        "  - First bullet indented with spaces\n"
        "\t- Second bullet indented with tab\n"
    )
    result = parse_markdown_sections(md)

    assert "Observations" in result
    bullets = result["Observations"]
    assert len(bullets) == 2
    assert "First bullet indented with spaces" in bullets
    assert "Second bullet indented with tab" in bullets


# ---------------------------------------------------------------------------
# test_handles_alternative_bullet_chars
# ---------------------------------------------------------------------------

def test_handles_alternative_bullet_chars() -> None:
    md = (
        "## Mixed Bullets\n"
        "- dash bullet\n"
        "* star bullet\n"
    )
    result = parse_markdown_sections(md)

    assert "Mixed Bullets" in result
    bullets = result["Mixed Bullets"]
    assert "dash bullet" in bullets
    assert "star bullet" in bullets


# ---------------------------------------------------------------------------
# test_handles_nested_bullets
# ---------------------------------------------------------------------------

def test_handles_nested_bullets() -> None:
    """Nested bullets (extra indentation) are collected as flat entries."""
    md = (
        "## Section\n"
        "- top level\n"
        "  - nested child\n"
        "    - deeply nested\n"
    )
    result = parse_markdown_sections(md)

    assert "Section" in result
    bullets = result["Section"]
    assert "top level" in bullets
    assert "nested child" in bullets
    assert "deeply nested" in bullets


# ---------------------------------------------------------------------------
# test_handles_blank_lines
# ---------------------------------------------------------------------------

def test_handles_blank_lines() -> None:
    md = (
        "## Section One\n"
        "\n"
        "\n"
        "- bullet after blank lines\n"
        "\n"
        "- another bullet\n"
    )
    result = parse_markdown_sections(md)

    assert "Section One" in result
    bullets = result["Section One"]
    assert "bullet after blank lines" in bullets
    assert "another bullet" in bullets


# ---------------------------------------------------------------------------
# test_returns_empty_dict_on_no_headers
# ---------------------------------------------------------------------------

def test_returns_empty_dict_on_no_headers() -> None:
    md = (
        "This is just prose with no headers.\n"
        "- even bullets without a header section\n"
    )
    result = parse_markdown_sections(md)
    assert result == {}


# ---------------------------------------------------------------------------
# Additional: mixed H2/H3, header-only sections, empty input
# ---------------------------------------------------------------------------

def test_mixed_h2_and_h3() -> None:
    md = (
        "## Summary\n"
        "- item one\n"
        "### Detail\n"
        "- detail bullet\n"
        "## Conclusion\n"
        "- last bullet\n"
    )
    result = parse_markdown_sections(md)
    assert "Summary" in result
    assert "Detail" in result
    assert "Conclusion" in result
    assert result["Summary"] == ["item one"]
    assert result["Detail"] == ["detail bullet"]
    assert result["Conclusion"] == ["last bullet"]


def test_section_with_no_bullets() -> None:
    md = "## Empty Section\n\nSome prose but no bullets.\n"
    result = parse_markdown_sections(md)
    assert "Empty Section" in result
    assert result["Empty Section"] == []


def test_empty_input() -> None:
    assert parse_markdown_sections("") == {}


def test_whitespace_only_input() -> None:
    assert parse_markdown_sections("   \n\n\t\n") == {}
