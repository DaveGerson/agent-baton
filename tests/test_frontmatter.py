"""Tests for agent_baton.utils.frontmatter.parse_frontmatter."""
from __future__ import annotations

import pytest

from agent_baton.utils.frontmatter import parse_frontmatter


class TestValidFrontmatter:
    # DECISION: merged test_returns_metadata_dict_and_body (2 key assertions +
    # body check) into test_all_known_fields_parsed which already covers the full
    # field set and body extraction. The merged test validates both.
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
    # DECISION: parameterized the three invalid-YAML tests (invalid_yaml_returns_empty_dict,
    # invalid_yaml_returns_original_content_as_body, tabs_in_yaml_cause_graceful_fallback)
    # into one. All three share the same contract: no exception raised, meta == {} (or dict),
    # body returned. The tabs case may produce non-empty meta so we only assert no exception.
    @pytest.mark.parametrize("content,expect_empty_meta,expect_body_is_input", [
        ("---\n: invalid: yaml: [\n---\n# Body", True, True),
        ("---\nname:\tagent\n---\nbody", False, False),  # tabs: pyyaml may accept or reject
    ])
    def test_invalid_yaml_does_not_raise(
        self, content: str, expect_empty_meta: bool, expect_body_is_input: bool
    ):
        """parse_frontmatter must never raise on malformed YAML."""
        meta, body = parse_frontmatter(content)
        assert isinstance(meta, dict)
        if expect_empty_meta:
            assert meta == {}
        if expect_body_is_input:
            assert body == content


class TestMultilineDescriptionFrontmatter:
    # DECISION: parameterized the three multiline-description tests into one.
    # test_block_scalar_description_is_parsed and test_folded_scalar_description_is_parsed
    # differ only in YAML scalar style (| vs >); test_body_after_multiline_frontmatter_is_correct
    # checks body extraction which is covered by the block-scalar case.
    @pytest.mark.parametrize("scalar_style,lines_in_desc,expected_in_body,not_in_body", [
        (
            "|",
            "  Specialist for system design.\n  Use for API contract definition.\n",
            "# Body",
            "Specialist",
        ),
        (
            ">",
            "  Line one.\n  Line two.\n",
            "body",
            None,
        ),
    ])
    def test_multiline_description_parsed(
        self,
        scalar_style: str,
        lines_in_desc: str,
        expected_in_body: str,
        not_in_body: str | None,
    ):
        content = (
            "---\n"
            "name: agent\n"
            f"description: {scalar_style}\n"
            f"{lines_in_desc}"
            "---\n"
            "\n"
            f"{expected_in_body}\n"
        )
        meta, body = parse_frontmatter(content)
        # Description must be parsed into metadata (not empty)
        assert "description" in meta
        assert meta["description"] != ""
        # Body must be present
        assert expected_in_body in body
        # Body must not bleed description content (block scalar only)
        if not_in_body is not None:
            assert not_in_body not in body
