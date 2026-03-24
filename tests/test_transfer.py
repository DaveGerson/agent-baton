"""Tests for agent_baton.core.transfer — ProjectTransfer and TransferManifest."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.distribute.transfer import ProjectTransfer, TransferManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_MD = """\
---
name: test-agent
description: A test agent.
model: sonnet
---

# Test Agent

You are a test agent.
"""

_REFERENCE_MD = "# Reference\n\nThis is a reference document.\n"

_KNOWLEDGE_MD = "# Knowledge\n\nThis is a knowledge document.\n"


def _make_project(root: Path) -> Path:
    """Create a minimal .claude/ layout inside root and return root."""
    agents_dir = root / ".claude" / "agents"
    refs_dir = root / ".claude" / "references"
    knowledge_dir = root / ".claude" / "knowledge" / "pack-alpha"

    agents_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    (agents_dir / "test-agent.md").write_text(_AGENT_MD, encoding="utf-8")
    (agents_dir / "helper-agent.md").write_text(_AGENT_MD, encoding="utf-8")
    (refs_dir / "git-strategy.md").write_text(_REFERENCE_MD, encoding="utf-8")
    (knowledge_dir / "overview.md").write_text(_KNOWLEDGE_MD, encoding="utf-8")

    return root


# ---------------------------------------------------------------------------
# TransferManifest.to_markdown
# ---------------------------------------------------------------------------

class TestTransferManifestMarkdown:
    # DECISION: Parameterize 5 of the 6 field-presence tests into one. Each
    # tuple is a (manifest_kwargs, expected_substring) pair that maps directly
    # to the original individual tests. Kept separate: the structural tests
    # (starts_with_h1) and the special empty-manifest placeholder test.

    @pytest.mark.parametrize("kwargs,expected", [
        ({"agents": ["test-agent.md"], "source_project": "/some/path", "reason": "Test reason"}, "/some/path"),
        ({"reason": "sharing with team"}, "sharing with team"),
        ({"agents": ["agent-a.md", "agent-b.md"]}, "agent-a.md"),
        ({"knowledge_packs": ["pack-alpha"]}, "pack-alpha"),
        ({"references": ["git-strategy.md"]}, "git-strategy.md"),
    ])
    def test_field_appears_in_markdown(self, kwargs, expected):
        m = TransferManifest(**kwargs)
        assert expected in m.to_markdown()

    def test_empty_manifest_renders_none_placeholders(self):
        m = TransferManifest()
        md = m.to_markdown()
        assert "_(none)_" in md

    def test_starts_with_h1(self):
        m = TransferManifest()
        md = m.to_markdown()
        assert md.startswith("# Transfer Manifest")


# ---------------------------------------------------------------------------
# ProjectTransfer.discover_transferable
# ---------------------------------------------------------------------------

class TestDiscoverTransferable:
    # DECISION: Parameterize test_discovers_agents, test_discovers_knowledge_packs,
    # and test_discovers_references into one test. Each checks a different list
    # attribute in the returned manifest for a known member.
    @pytest.mark.parametrize("attr,expected_member", [
        ("agents", "test-agent.md"),
        ("agents", "helper-agent.md"),
        ("knowledge_packs", "pack-alpha"),
        ("references", "git-strategy.md"),
    ])
    def test_discovers_content(self, tmp_path: Path, attr, expected_member):
        src = _make_project(tmp_path / "src")
        transfer = ProjectTransfer(source_root=src)
        manifest = transfer.discover_transferable()
        assert expected_member in getattr(manifest, attr)

    def test_source_project_set_in_manifest(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        transfer = ProjectTransfer(source_root=src)
        manifest = transfer.discover_transferable()
        assert str(src) == manifest.source_project

    def test_empty_project_returns_empty_manifest(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        transfer = ProjectTransfer(source_root=empty)
        manifest = transfer.discover_transferable()
        assert manifest.agents == []
        assert manifest.knowledge_packs == []
        assert manifest.references == []

    def test_min_score_zero_includes_all_agents(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        transfer = ProjectTransfer(source_root=src)
        manifest = transfer.discover_transferable(min_score=0.0)
        assert len(manifest.agents) == 2

    def test_min_score_positive_includes_agents_with_no_usage_data(self, tmp_path: Path):
        # Agents with no usage records are always included (unknown, not bad).
        src = _make_project(tmp_path / "src")
        transfer = ProjectTransfer(source_root=src)
        manifest = transfer.discover_transferable(min_score=0.8)
        # No usage data → times_used == 0 → included anyway
        assert len(manifest.agents) == 2

    # DECISION: Merge test_agents_sorted_alphabetically and
    # test_references_sorted_alphabetically into one parameterized test. Both
    # verify the same sorting invariant on different list attributes.
    @pytest.mark.parametrize("attr,extra_setup", [
        ("agents", None),
        ("references", "another-ref.md"),
    ])
    def test_content_sorted_alphabetically(self, tmp_path: Path, attr, extra_setup):
        src = _make_project(tmp_path / "src")
        if extra_setup and attr == "references":
            refs_dir = src / ".claude" / "references"
            (refs_dir / extra_setup).write_text(_REFERENCE_MD, encoding="utf-8")
        transfer = ProjectTransfer(source_root=src)
        manifest = transfer.discover_transferable()
        lst = getattr(manifest, attr)
        assert lst == sorted(lst)


# ---------------------------------------------------------------------------
# ProjectTransfer.export_to
# ---------------------------------------------------------------------------

class TestExportTo:
    def test_copies_agents(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()
        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["test-agent.md"])
        counts = transfer.export_to(dst, manifest)
        assert (dst / ".claude" / "agents" / "test-agent.md").exists()
        assert counts["agents"] == 1

    def test_copies_references(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()
        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(references=["git-strategy.md"])
        counts = transfer.export_to(dst, manifest)
        assert (dst / ".claude" / "references" / "git-strategy.md").exists()
        assert counts["references"] == 1

    def test_copies_knowledge_pack(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()
        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(knowledge_packs=["pack-alpha"])
        counts = transfer.export_to(dst, manifest)
        assert (dst / ".claude" / "knowledge" / "pack-alpha" / "overview.md").exists()
        assert counts["knowledge"] == 1

    def test_skips_existing_without_force(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst_agent = dst / ".claude" / "agents" / "test-agent.md"
        dst_agent.parent.mkdir(parents=True, exist_ok=True)
        dst_agent.write_text("original", encoding="utf-8")

        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["test-agent.md"])
        counts = transfer.export_to(dst, manifest, force=False)
        assert counts["agents"] == 0
        assert dst_agent.read_text(encoding="utf-8") == "original"

    def test_overwrites_existing_with_force(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst_agent = dst / ".claude" / "agents" / "test-agent.md"
        dst_agent.parent.mkdir(parents=True, exist_ok=True)
        dst_agent.write_text("original", encoding="utf-8")

        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["test-agent.md"])
        counts = transfer.export_to(dst, manifest, force=True)
        assert counts["agents"] == 1
        assert dst_agent.read_text(encoding="utf-8") != "original"

    def test_skips_nonexistent_agent_file(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()
        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["ghost-agent.md"])
        counts = transfer.export_to(dst, manifest)
        assert counts["agents"] == 0

    def test_returns_zero_counts_for_empty_manifest(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()
        transfer = ProjectTransfer(source_root=src)
        counts = transfer.export_to(dst, TransferManifest())
        assert counts == {"agents": 0, "knowledge": 0, "references": 0}

    def test_creates_target_directories(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "new-project"
        # dst does not exist yet
        transfer = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["test-agent.md"])
        transfer.export_to(dst, manifest)
        assert (dst / ".claude" / "agents" / "test-agent.md").exists()


# ---------------------------------------------------------------------------
# ProjectTransfer.import_from
# ---------------------------------------------------------------------------

class TestImportFrom:
    def test_import_from_is_inverse_of_export_to(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()

        # export_to goes src → dst
        transfer_src = ProjectTransfer(source_root=src)
        manifest = TransferManifest(agents=["test-agent.md"])
        export_counts = transfer_src.export_to(dst, manifest)

        # import_from on dst project should pull from src
        dst2 = tmp_path / "dst2"
        dst2.mkdir()
        transfer_dst = ProjectTransfer(source_root=dst2)
        import_counts = transfer_dst.import_from(src, manifest)

        assert import_counts["agents"] == 1
        assert (dst2 / ".claude" / "agents" / "test-agent.md").exists()

    def test_import_respects_force_flag(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst_agent = dst / ".claude" / "agents" / "test-agent.md"
        dst_agent.parent.mkdir(parents=True, exist_ok=True)
        dst_agent.write_text("old content", encoding="utf-8")

        transfer = ProjectTransfer(source_root=dst)
        manifest = TransferManifest(agents=["test-agent.md"])

        # Without force — skips
        counts_no_force = transfer.import_from(src, manifest, force=False)
        assert counts_no_force["agents"] == 0

        # With force — overwrites
        counts_force = transfer.import_from(src, manifest, force=True)
        assert counts_force["agents"] == 1
        assert dst_agent.read_text(encoding="utf-8") != "old content"

    def test_full_round_trip_discover_and_import(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        dst = tmp_path / "dst"
        dst.mkdir()

        src_transfer = ProjectTransfer(source_root=src)
        manifest = src_transfer.discover_transferable()

        dst_transfer = ProjectTransfer(source_root=dst)
        counts = dst_transfer.import_from(src, manifest)

        assert counts["agents"] >= 2
        assert counts["knowledge"] >= 1
        assert counts["references"] >= 1
