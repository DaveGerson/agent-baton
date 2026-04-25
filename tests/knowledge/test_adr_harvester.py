"""Tests for the ADR-to-knowledge harvester."""
from __future__ import annotations

from pathlib import Path

import yaml

from agent_baton.core.knowledge.adr_harvester import (
    discover_adrs,
    harvest_adrs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_source_tree(root: Path) -> None:
    """Lay down a representative docs/ tree with assorted ADR shapes."""
    _write(
        root / "adr" / "ADR-001-use-sqlite.md",
        "# Use SQLite\n\nDecision: use SQLite as the primary store.\n",
    )
    _write(
        root / "architecture" / "decisions" / "0042-team-leads.md",
        "# Team leads\n\nIntroduce team-lead roles.\n",
    )
    _write(
        root / "design" / "logging.adr.md",
        "# Logging strategy\n\nRoute through stdlib logging.\n",
    )
    # Non-ADRs that should be skipped
    _write(
        root / "guides" / "intro.md",
        "# Intro\n\nRandom guide that is not an ADR.\n",
    )
    _write(
        root / "adr" / "README.md",
        "# Index\n",
    )
    # Numeric prefix outside an adr/decisions dir — must NOT be picked up.
    _write(
        root / "release" / "0001-changelog.md",
        "# Changelog 1\n",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_discover_walks_and_extracts(tmp_path: Path) -> None:
    _make_source_tree(tmp_path)
    found = discover_adrs(tmp_path)
    names = sorted((a.source_path.name, a.number, a.title) for a in found)
    assert ("0042-team-leads.md", "42", "Team leads") in names
    assert ("ADR-001-use-sqlite.md", "1", "Use SQLite") in names
    assert ("logging.adr.md", None, "Logging strategy") in names
    # Three ADRs total — the README, the ungrouped numeric file, and the
    # plain guide must be filtered out.
    assert len(found) == 3


def test_naming_convention_extraction(tmp_path: Path) -> None:
    _make_source_tree(tmp_path)
    found = {a.source_path.name: a for a in discover_adrs(tmp_path)}

    adr1 = found["ADR-001-use-sqlite.md"]
    assert adr1.number == "1"
    assert adr1.doc_filename == "adr-1-use-sqlite.md"

    madr = found["0042-team-leads.md"]
    assert madr.number == "42"
    assert madr.doc_filename == "adr-42-team-leads.md"

    suffix_style = found["logging.adr.md"]
    assert suffix_style.number is None
    assert suffix_style.doc_filename == "adr-logging.md"


def test_knowledge_yaml_updated_additively(tmp_path: Path) -> None:
    src = tmp_path / "docs"
    _make_source_tree(src)
    knowledge_root = tmp_path / ".claude" / "knowledge"

    # Pre-seed an existing manifest with a doc that is NOT in the source.
    pack_dir = knowledge_root / "decisions"
    pack_dir.mkdir(parents=True)
    manifest_path = pack_dir / "knowledge.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "name": "decisions",
                "description": "Existing description should survive",
                "tags": ["custom-tag"],
                "target_agents": ["architect"],
                "default_delivery": "reference",
                "docs": ["pre-existing-doc"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = harvest_adrs(src, knowledge_root=knowledge_root)
    assert len(result.written) == 3

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["description"] == "Existing description should survive"
    assert "custom-tag" in manifest["tags"]
    # Auto-added tags appear too
    for required in ("adr", "decisions", "architecture"):
        assert required in manifest["tags"]
    assert manifest["target_agents"] == ["architect"]
    # Pre-existing doc preserved
    assert "pre-existing-doc" in manifest["docs"]
    # Newly harvested docs registered
    assert "adr-1-use-sqlite" in manifest["docs"]
    assert "adr-42-team-leads" in manifest["docs"]
    assert "adr-logging" in manifest["docs"]


def test_idempotent_rerun(tmp_path: Path) -> None:
    src = tmp_path / "docs"
    _make_source_tree(src)
    knowledge_root = tmp_path / ".claude" / "knowledge"

    first = harvest_adrs(src, knowledge_root=knowledge_root)
    assert len(first.written) == 3
    assert len(first.skipped) == 0

    second = harvest_adrs(src, knowledge_root=knowledge_root)
    assert len(second.written) == 0
    assert len(second.skipped) == 3

    # If the source mutates, only that one is rewritten.
    target_adr = src / "adr" / "ADR-001-use-sqlite.md"
    target_adr.write_text(
        "# Use SQLite\n\nUpdated rationale: also better latency.\n",
        encoding="utf-8",
    )
    third = harvest_adrs(src, knowledge_root=knowledge_root)
    assert len(third.written) == 1
    assert third.written[0].name == "adr-1-use-sqlite.md"
    assert len(third.skipped) == 2


def test_skips_non_adr_markdown(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    _write(docs / "intro.md", "# Intro\n")
    _write(docs / "guides" / "deploy.md", "# Deploy\n")
    _write(docs / "adr" / "README.md", "# Index\n")
    _write(docs / "adr" / "template.md", "# Template\n")
    # Real ADR alongside the noise
    _write(docs / "adr" / "ADR-007-routing.md", "# Routing\n\nUse the dispatcher.\n")

    found = discover_adrs(docs)
    assert len(found) == 1
    assert found[0].title == "Routing"


def test_default_pack_name_is_decisions(tmp_path: Path) -> None:
    src = tmp_path / "docs"
    _write(src / "adr" / "ADR-1-foo.md", "# Foo\n")
    knowledge_root = tmp_path / ".claude" / "knowledge"

    result = harvest_adrs(src, knowledge_root=knowledge_root)
    assert result.pack_dir == knowledge_root / "decisions"
    assert (knowledge_root / "decisions" / "knowledge.yaml").is_file()
    manifest = yaml.safe_load(
        (knowledge_root / "decisions" / "knowledge.yaml").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "decisions"


def test_doc_frontmatter_records_source_and_hash(tmp_path: Path) -> None:
    src = tmp_path / "docs"
    _write(src / "adr" / "ADR-1-foo.md", "# Foo\n\nBody text long enough.\n")
    knowledge_root = tmp_path / ".claude" / "knowledge"

    result = harvest_adrs(src, knowledge_root=knowledge_root)
    assert len(result.written) == 1
    written = result.written[0]
    text = written.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front_yaml = text.split("---", 2)[1]
    front = yaml.safe_load(front_yaml)
    assert front["adr_number"] == "1"
    assert "source_sha256" in front
    assert "source_path" in front
    assert "adr" in front["tags"]
