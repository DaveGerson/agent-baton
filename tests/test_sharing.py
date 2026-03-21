"""Tests for agent_baton.core.sharing — PackageBuilder and PackageManifest."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from agent_baton.core.sharing import PackageBuilder, PackageManifest


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
    """Create a minimal .claude/ layout and return root."""
    agents_dir = root / ".claude" / "agents"
    refs_dir = root / ".claude" / "references"
    knowledge_dir = root / ".claude" / "knowledge" / "pack-beta"

    agents_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    (agents_dir / "test-agent.md").write_text(_AGENT_MD, encoding="utf-8")
    (agents_dir / "helper-agent.md").write_text(_AGENT_MD, encoding="utf-8")
    (refs_dir / "git-strategy.md").write_text(_REFERENCE_MD, encoding="utf-8")
    (knowledge_dir / "overview.md").write_text(_KNOWLEDGE_MD, encoding="utf-8")

    return root


# ---------------------------------------------------------------------------
# PackageManifest.to_dict / from_dict
# ---------------------------------------------------------------------------

class TestPackageManifestSerde:
    def test_round_trip(self):
        m = PackageManifest(
            name="my-pkg",
            version="2.0.0",
            description="A test package",
            baton_version="0.2.0",
            created_at="2026-03-20T10:00:00+00:00",
            agents=["test-agent.md"],
            references=["git-strategy.md"],
            knowledge_packs=["pack-beta"],
        )
        d = m.to_dict()
        restored = PackageManifest.from_dict(d)
        assert restored.name == m.name
        assert restored.version == m.version
        assert restored.description == m.description
        assert restored.baton_version == m.baton_version
        assert restored.created_at == m.created_at
        assert restored.agents == m.agents
        assert restored.references == m.references
        assert restored.knowledge_packs == m.knowledge_packs

    def test_from_dict_missing_keys_use_defaults(self):
        m = PackageManifest.from_dict({"name": "minimal"})
        assert m.version == "1.0.0"
        assert m.agents == []
        assert m.references == []
        assert m.knowledge_packs == []

    def test_to_dict_is_json_serialisable(self):
        m = PackageManifest(name="pkg", agents=["a.md"])
        raw = json.dumps(m.to_dict())
        assert '"name": "pkg"' in raw


# ---------------------------------------------------------------------------
# PackageBuilder.build
# ---------------------------------------------------------------------------

class TestPackageBuilderBuild:
    def test_creates_tar_gz_file(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", output_dir=tmp_path / "out")
        assert archive.exists()
        assert archive.suffix == ".gz"
        assert "my-pkg" in archive.name

    def test_archive_name_includes_version(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", version="3.1.0", output_dir=tmp_path / "out")
        assert "3.1.0" in archive.name

    def test_archive_contains_manifest_json(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", output_dir=tmp_path / "out")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert "manifest.json" in names

    def test_archive_contains_agents(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", include_agents=True, output_dir=tmp_path / "out")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert "agents/test-agent.md" in names

    def test_archive_contains_references(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", include_references=True, output_dir=tmp_path / "out")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert "references/git-strategy.md" in names

    def test_archive_contains_knowledge_when_requested(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build(
            "my-pkg", include_knowledge=True, output_dir=tmp_path / "out"
        )
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any("knowledge" in n for n in names)

    def test_archive_excludes_knowledge_by_default(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("my-pkg", output_dir=tmp_path / "out")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert not any("knowledge" in n for n in names)

    def test_archive_excludes_agents_when_disabled(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build(
            "my-pkg", include_agents=False, output_dir=tmp_path / "out"
        )
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert not any(n.startswith("agents/") for n in names)

    def test_manifest_in_archive_has_correct_name(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("cool-pkg", version="0.5.0", output_dir=tmp_path / "out")
        with tarfile.open(archive, "r:gz") as tar:
            f = tar.extractfile("manifest.json")
            assert f is not None
            data = json.loads(f.read())
        assert data["name"] == "cool-pkg"
        assert data["version"] == "0.5.0"

    def test_creates_output_dir_if_missing(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        out = tmp_path / "nested" / "output"
        archive = builder.build("my-pkg", output_dir=out)
        assert archive.exists()

    def test_empty_project_produces_minimal_archive(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        builder = PackageBuilder(source_root=empty)
        archive = builder.build("empty-pkg", output_dir=tmp_path / "out")
        assert archive.exists()
        with tarfile.open(archive, "r:gz") as tar:
            assert "manifest.json" in tar.getnames()


# ---------------------------------------------------------------------------
# PackageBuilder.extract
# ---------------------------------------------------------------------------

class TestPackageBuilderExtract:
    def test_extract_returns_manifest(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("pkg", output_dir=tmp_path / "out")
        manifest = builder.extract(archive, target_dir=tmp_path / "extracted")
        assert manifest.name == "pkg"

    def test_extract_creates_directory_structure(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("pkg", include_agents=True, output_dir=tmp_path / "out")
        extract_dir = tmp_path / "extracted"
        builder.extract(archive, target_dir=extract_dir)
        assert (extract_dir / "manifest.json").exists()
        assert (extract_dir / "agents" / "test-agent.md").exists()

    def test_extract_raises_for_missing_archive(self, tmp_path: Path):
        builder = PackageBuilder(source_root=tmp_path)
        with pytest.raises(FileNotFoundError):
            builder.extract(tmp_path / "nonexistent.tar.gz")

    def test_extract_raises_for_archive_without_manifest(self, tmp_path: Path):
        archive = tmp_path / "no-manifest.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            import io
            data = b"hello"
            info = tarfile.TarInfo(name="some-file.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        builder = PackageBuilder(source_root=tmp_path)
        with pytest.raises(KeyError):
            builder.extract(archive, target_dir=tmp_path / "out")


# ---------------------------------------------------------------------------
# PackageBuilder.read_manifest
# ---------------------------------------------------------------------------

class TestReadManifest:
    def test_reads_manifest_from_valid_archive(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("readable-pkg", version="1.2.3", output_dir=tmp_path / "out")
        manifest = builder.read_manifest(archive)
        assert manifest is not None
        assert manifest.name == "readable-pkg"
        assert manifest.version == "1.2.3"

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        builder = PackageBuilder(source_root=tmp_path)
        result = builder.read_manifest(tmp_path / "ghost.tar.gz")
        assert result is None

    def test_returns_none_for_archive_without_manifest(self, tmp_path: Path):
        archive = tmp_path / "no-manifest.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            import io
            data = b"not a manifest"
            info = tarfile.TarInfo(name="other.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        builder = PackageBuilder(source_root=tmp_path)
        assert builder.read_manifest(archive) is None

    def test_reads_agents_list_from_manifest(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("pkg", include_agents=True, output_dir=tmp_path / "out")
        manifest = builder.read_manifest(archive)
        assert manifest is not None
        assert "test-agent.md" in manifest.agents

    def test_does_not_fully_extract_archive(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build("pkg", output_dir=tmp_path / "out")
        # No extraction directory passed — should not write files to disk
        builder.read_manifest(archive)
        # There should be no extracted files in the tmp dir
        extracted_dirs = [
            d for d in tmp_path.iterdir()
            if d.is_dir() and d.name not in ("src", "out")
        ]
        assert extracted_dirs == []


# ---------------------------------------------------------------------------
# PackageBuilder.install_package
# ---------------------------------------------------------------------------

class TestInstallPackage:
    def test_installs_agents_to_project_scope(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build("pkg", include_agents=True, output_dir=tmp_path / "out")

        dst = tmp_path / "dst"
        dst.mkdir()
        builder_dst = PackageBuilder(source_root=dst)
        counts = builder_dst.install_package(archive, scope="project")
        assert counts["agents"] >= 1
        assert (dst / ".claude" / "agents" / "test-agent.md").exists()

    def test_installs_references_to_project_scope(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build(
            "pkg", include_agents=False, include_references=True, output_dir=tmp_path / "out"
        )
        dst = tmp_path / "dst"
        dst.mkdir()
        builder_dst = PackageBuilder(source_root=dst)
        counts = builder_dst.install_package(archive, scope="project")
        assert counts["references"] >= 1
        assert (dst / ".claude" / "references" / "git-strategy.md").exists()

    def test_installs_knowledge_packs(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build(
            "pkg",
            include_agents=False,
            include_references=False,
            include_knowledge=True,
            output_dir=tmp_path / "out",
        )
        dst = tmp_path / "dst"
        dst.mkdir()
        builder_dst = PackageBuilder(source_root=dst)
        counts = builder_dst.install_package(archive, scope="project")
        assert counts["knowledge"] >= 1
        assert (dst / ".claude" / "knowledge" / "pack-beta" / "overview.md").exists()

    def test_skip_existing_without_force(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build("pkg", include_agents=True, output_dir=tmp_path / "out")

        dst = tmp_path / "dst"
        existing = dst / ".claude" / "agents" / "test-agent.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("old", encoding="utf-8")

        builder_dst = PackageBuilder(source_root=dst)
        counts = builder_dst.install_package(archive, scope="project", force=False)
        # File was already there, should be skipped
        assert existing.read_text(encoding="utf-8") == "old"

    def test_overwrite_with_force(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build("pkg", include_agents=True, output_dir=tmp_path / "out")

        dst = tmp_path / "dst"
        existing = dst / ".claude" / "agents" / "test-agent.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("old", encoding="utf-8")

        builder_dst = PackageBuilder(source_root=dst)
        builder_dst.install_package(archive, scope="project", force=True)
        assert existing.read_text(encoding="utf-8") != "old"

    def test_install_to_user_scope(self, tmp_path: Path, monkeypatch):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build("pkg", include_agents=True, output_dir=tmp_path / "out")

        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        builder_any = PackageBuilder(source_root=tmp_path / "any")
        counts = builder_any.install_package(archive, scope="user")
        assert counts["agents"] >= 1
        assert (fake_home / ".claude" / "agents" / "test-agent.md").exists()

    def test_returns_correct_counts(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder_src = PackageBuilder(source_root=src)
        archive = builder_src.build(
            "pkg",
            include_agents=True,
            include_references=True,
            include_knowledge=True,
            output_dir=tmp_path / "out",
        )
        dst = tmp_path / "dst"
        dst.mkdir()
        builder_dst = PackageBuilder(source_root=dst)
        counts = builder_dst.install_package(archive, scope="project")
        assert counts["agents"] == 2
        assert counts["references"] == 1
        assert counts["knowledge"] == 1

    def test_raises_on_archive_missing_manifest(self, tmp_path: Path):
        archive = tmp_path / "bad.tar.gz"
        import io
        with tarfile.open(archive, "w:gz") as tar:
            data = b"not a manifest"
            info = tarfile.TarInfo(name="readme.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        builder = PackageBuilder(source_root=tmp_path)
        with pytest.raises(KeyError):
            builder.install_package(archive)


# ---------------------------------------------------------------------------
# _safe_extractall
# ---------------------------------------------------------------------------

class TestSafeExtractall:
    """Verify _safe_extractall rejects path traversal attacks."""

    def test_rejects_dotdot_path(self, tmp_path: Path) -> None:
        """Archive member with ../../ prefix must raise ValueError."""
        from agent_baton.core.distribute.sharing import _safe_extractall

        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            import io
            data = b"malicious content"
            info = tarfile.TarInfo(name="../../etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        dest = tmp_path / "extract"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            with pytest.raises(ValueError, match="Path traversal"):
                _safe_extractall(tar, dest)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        """Archive member with absolute path must raise ValueError."""
        from agent_baton.core.distribute.sharing import _safe_extractall

        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            import io
            data = b"malicious content"
            info = tarfile.TarInfo(name="/tmp/evil")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        dest = tmp_path / "extract"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            with pytest.raises(ValueError, match="Path traversal"):
                _safe_extractall(tar, dest)

    def test_allows_normal_paths(self, tmp_path: Path) -> None:
        """Normal archive members should extract without error."""
        from agent_baton.core.distribute.sharing import _safe_extractall

        archive = tmp_path / "good.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            import io
            for name in ("manifest.json", "agents/test.md", "references/ref.md"):
                data = b"content"
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

        dest = tmp_path / "extract"
        dest.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extractall(tar, dest)

        assert (dest / "manifest.json").exists()
        assert (dest / "agents" / "test.md").exists()
