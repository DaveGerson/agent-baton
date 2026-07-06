"""Contract tests for generated agent authoring assets."""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.utils.frontmatter import parse_frontmatter


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FIELDS = ("name", "description", "model", "permissionMode", "tools")
RECOMMENDED_FIELDS = (
    "owner",
    "status",
    "version",
    "created_by",
    "last_reviewed",
    "knowledge_packs",
)
REQUIRED_SECTIONS = (
    "Mission",
    "Before Starting",
    "Knowledge References",
    "Principles",
    "Anti-Patterns",
    "Output Format",
)
TEMPLATE_FILES = ("base-agent.md", "flavored-agent.md", "reviewer-agent.md")


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _assert_contract_fields(text: str) -> None:
    for field in REQUIRED_FIELDS:
        assert field in text, f"missing required field {field}"
    for field in RECOMMENDED_FIELDS:
        assert field in text, f"missing recommended field {field}"


def _assert_contract_sections(text: str) -> None:
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in text or f"- {section}" in text, (
            f"missing generated-agent section {section}"
        )


def test_talent_builder_instructions_define_generated_agent_contract() -> None:
    text = _read("agents/talent-builder.md")

    assert "Generated-Agent Contract" in text
    _assert_contract_fields(text)
    _assert_contract_sections(text)
    assert "Avoid broad tools" in text
    assert "read back" in text.lower()
    assert "validate references" in text.lower()


def test_bundled_talent_builder_matches_source_agent() -> None:
    source = ROOT / "agents" / "talent-builder.md"
    bundled = ROOT / "agent_baton" / "_bundled_agents" / "talent-builder.md"

    assert bundled.exists()
    assert bundled.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_agent_authoring_reference_defines_contract() -> None:
    text = _read("references/agent-authoring.md")

    assert "# Agent Authoring" in text
    # talent-manager was never resolvable in the AgentRegistry; docs must
    # not advertise the alias.
    assert "talent-manager" not in text
    assert "`talent-builder`" in text
    _assert_contract_fields(text)
    _assert_contract_sections(text)


def test_agent_roster_links_contract_without_phantom_alias() -> None:
    text = _read("docs/agent-roster.md")

    assert "references/agent-authoring.md" in text
    assert "permissionMode" in text
    assert "talent-manager" not in text
    assert "`talent-builder`" in text


def test_starter_templates_exist_and_match_generated_agent_contract() -> None:
    template_dir = ROOT / "templates" / "agents"

    for filename in TEMPLATE_FILES:
        path = template_dir / filename
        assert path.exists(), f"missing template {filename}"
        content = path.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(content)

        for field in REQUIRED_FIELDS:
            assert metadata.get(field), f"{filename} missing required {field}"
        for field in RECOMMENDED_FIELDS:
            assert field in metadata, f"{filename} missing recommended {field}"
        _assert_contract_sections(body)


def test_existing_bundled_agents_still_parse() -> None:
    bundled_dir = ROOT / "agent_baton" / "_bundled_agents"
    registry = AgentRegistry()
    parsed_names: list[str] = []

    for path in sorted(bundled_dir.glob("*.md")):
        if path.name == "CLAUDE.md":
            continue
        agent = registry._parse_agent_content(path.read_text(encoding="utf-8"), path.name)
        assert agent is not None, f"failed to parse {path.name}"
        assert agent.name
        assert agent.description
        parsed_names.append(agent.name)

    assert "talent-builder" in parsed_names
    assert len(parsed_names) == 30
