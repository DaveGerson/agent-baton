"""Tests for agent_baton.utils.frontmatter.parse_frontmatter."""
from __future__ import annotations

import pytest

from agent_baton.utils.frontmatter import parse_frontmatter


class TestValidFrontmatter:
    def test_returns_metadata_dict_and_body(self):
        content = "---\nname: my-agent\nmodel: sonnet\n---\n\n# Body text"
        meta, body = parse_frontmatter(content)
        assert meta["name"] == "my-agent"
        assert meta["model"] == "sonnet"
        assert "# Body text" in body

    def test_all_known_fields_parsed(self):
        content = (
            "---\n"
            "name: test-agent\n"
            "description: A test agent\n"
            "model: opus\n"
            "permissionMode: auto-edit\n"
            "color: blue\n"
            "tools: Read, Write, Bash\n"
            "---\n"
            "\n"
            "# Instructions\n"
        )
        meta, body = parse_frontmatter(content)
        assert meta["name"] == "test-agent"
        assert meta["description"] == "A test agent"
        assert meta["model"] == "opus"
        assert meta["permissionMode"] == "auto-edit"
        assert meta["color"] == "blue"
        assert meta["tools"] == "Read, Write, Bash"
        assert "# Instructions" in body

    def test_body_is_stripped_of_leading_and_trailing_whitespace(self):
        content = "---\nname: agent\n---\n\n\n  # Indented heading\n\n"
        _, body = parse_frontmatter(content)
        # parse_frontmatter strips the body with .strip()
        assert not body.startswith("\n")
        assert "# Indented heading" in body

    def test_numeric_values_are_preserved(self):
        content = "---\nname: agent\ntimeout: 30\n---\nbody"
        meta, _ = parse_frontmatter(content)
        assert meta["timeout"] == 30

    def test_list_values_are_preserved(self):
        content = "---\nname: agent\ntools:\n  - Read\n  - Write\n---\nbody"
        meta, _ = parse_frontmatter(content)
        assert meta["tools"] == ["Read", "Write"]


class TestMissingFrontmatter:
    def test_plain_markdown_returns_empty_dict(self):
        content = "# Just a markdown file\n\nNo frontmatter here."
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_plain_markdown_returns_original_content_as_body(self):
        content = "# Just a markdown file\n\nNo frontmatter here."
        _, body = parse_frontmatter(content)
        assert body == content

    def test_content_starting_with_hash_has_no_frontmatter(self):
        content = "# Agent\n---\nThis dash is not frontmatter.\n---\n"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_string_returns_empty_dict_and_empty_body(self):
        meta, body = parse_frontmatter("")
        assert meta == {}
        assert body == ""


class TestEmptyFrontmatter:
    def test_empty_frontmatter_block_returns_empty_dict(self):
        # Just "---\n---" with nothing in the YAML block
        content = "---\n---\n\n# Body"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert "# Body" in body

    def test_whitespace_only_frontmatter_returns_empty_dict(self):
        content = "---\n   \n---\n\n# Body"
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_single_dash_line_is_not_frontmatter(self):
        content = "-\nname: agent\n-\nbody"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content


class TestInvalidYAMLFrontmatter:
    def test_invalid_yaml_returns_empty_dict(self):
        content = "---\n: invalid: yaml: [\n---\n# Body"
        meta, body = parse_frontmatter(content)
        assert meta == {}

    def test_invalid_yaml_returns_original_content_as_body(self):
        content = "---\n: invalid: yaml: [\n---\n# Body"
        meta, body = parse_frontmatter(content)
        assert body == content

    def test_tabs_in_yaml_cause_graceful_fallback(self):
        # Tabs are not allowed in YAML values in strict mode
        content = "---\nname:\tagent\n---\nbody"
        # This may or may not raise a YAML error depending on pyyaml version.
        # The important thing is parse_frontmatter does not raise.
        meta, _ = parse_frontmatter(content)
        # Just verify it returned something (dict), no exception raised


class TestMultilineDescriptionFrontmatter:
    def test_block_scalar_description_is_parsed(self):
        content = (
            "---\n"
            "name: architect\n"
            "description: |\n"
            "  Specialist for system design.\n"
            "  Use for API contract definition.\n"
            "---\n"
            "\n"
            "# Body\n"
        )
        meta, body = parse_frontmatter(content)
        assert "Specialist for system design." in meta["description"]
        assert "Use for API contract definition." in meta["description"]
        assert "# Body" in body

    def test_folded_scalar_description_is_parsed(self):
        content = (
            "---\n"
            "name: my-agent\n"
            "description: >\n"
            "  Line one.\n"
            "  Line two.\n"
            "---\n"
            "body\n"
        )
        meta, _ = parse_frontmatter(content)
        # Folded scalar: newlines become spaces (except final newline)
        assert "Line one." in meta["description"]
        assert "Line two." in meta["description"]

    def test_body_after_multiline_frontmatter_is_correct(self):
        content = (
            "---\n"
            "name: agent\n"
            "description: |\n"
            "  Multi\n"
            "  line\n"
            "---\n"
            "\n"
            "# Real body starts here\n"
        )
        _, body = parse_frontmatter(content)
        assert "# Real body starts here" in body
        assert "Multi" not in body  # description stays in metadata, not body
