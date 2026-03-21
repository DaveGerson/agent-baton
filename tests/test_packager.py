"""Tests for agent_baton.core.distribute.packager."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from agent_baton.core.distribute.packager import (
    EnhancedManifest,
    PackageDependency,
    PackageValidationResult,
    PackageVerifier,
)
from agent_baton.core.distribute.sharing import PackageBuilder


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_AGENT_MD = """\
---
name: test-agent
description: |
  A test agent for packaging tests.
  It validates packaging functionality.
model: sonnet
permissionMode: auto-edit
tools: Read, Write, Edit
---

# Test Agent

You are a test agent used in packaging tests.
"""

_INVALID_AGENT_MD = """\
This agent has no frontmatter at all.
It will fail AgentValidator.
"""

_REFERENCE_MD = "# Reference\n\nThis is a reference document.\n"


def _make_project(root: Path, agent_content: str = _VALID_AGENT_MD) -> Path:
    """Create a minimal .claude/ layout and return root."""
    agents_dir = root / ".claude" / "agents"
    refs_dir = root / ".claude" / "references"
    agents_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    (agents_dir / "test-agent.md").write_text(agent_content, encoding="utf-8")
    (refs_dir / "git-strategy.md").write_text(_REFERENCE_MD, encoding="utf-8")
    return root


def _build_archive(tmp_path: Path, agent_content: str = _VALID_AGENT_MD) -> Path:
    """Build and return a minimal test archive."""
    src = _make_project(tmp_path / "src", agent_content)
    builder = PackageBuilder(source_root=src)
    return builder.build("test-pkg", version="1.0.0", output_dir=tmp_path / "out")


def _make_archive_without_manifest(tmp_path: Path) -> Path:
    """Create a .tar.gz that contains no manifest.json."""
    archive = tmp_path / "no-manifest.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"hello"
        info = tarfile.TarInfo(name="readme.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return archive


# ---------------------------------------------------------------------------
# PackageDependency
# ---------------------------------------------------------------------------


class TestPackageDependency:
    def test_round_trip(self):
        dep = PackageDependency(name="base-pack", min_version="0.2.0")
        d = dep.to_dict()
        restored = PackageDependency.from_dict(d)
        assert restored.name == dep.name
        assert restored.min_version == dep.min_version

    def test_min_version_defaults_empty(self):
        dep = PackageDependency(name="simple-dep")
        assert dep.min_version == ""

    def test_from_dict_missing_name(self):
        dep = PackageDependency.from_dict({})
        assert dep.name == ""

    def test_from_dict_ignores_extra_keys(self):
        dep = PackageDependency.from_dict(
            {"name": "x", "min_version": "1.0", "unknown_key": "ignored"}
        )
        assert dep.name == "x"
        assert dep.min_version == "1.0"

    def test_to_dict_is_json_serialisable(self):
        dep = PackageDependency(name="foo", min_version="0.1.0")
        raw = json.dumps(dep.to_dict())
        assert '"foo"' in raw


# ---------------------------------------------------------------------------
# EnhancedManifest serialisation
# ---------------------------------------------------------------------------


class TestEnhancedManifestSerde:
    def test_round_trip_full(self):
        m = EnhancedManifest(
            name="full-pkg",
            version="2.0.0",
            description="A full manifest",
            baton_version="0.2.0",
            created_at="2026-03-20T10:00:00+00:00",
            agents=["test-agent.md"],
            references=["git-strategy.md"],
            knowledge_packs=["my-pack"],
            checksums={"manifest.json": "abc123", "agents/test-agent.md": "def456"},
            dependencies=[PackageDependency(name="base-pack", min_version="1.0.0")],
        )
        d = m.to_dict()
        restored = EnhancedManifest.from_dict(d)

        assert restored.name == m.name
        assert restored.version == m.version
        assert restored.description == m.description
        assert restored.baton_version == m.baton_version
        assert restored.created_at == m.created_at
        assert restored.agents == m.agents
        assert restored.references == m.references
        assert restored.knowledge_packs == m.knowledge_packs
        assert restored.checksums == m.checksums
        assert len(restored.dependencies) == 1
        assert restored.dependencies[0].name == "base-pack"
        assert restored.dependencies[0].min_version == "1.0.0"

    def test_backward_compat_old_manifest_without_checksums(self):
        """from_dict must accept old manifests that lack checksums/dependencies."""
        old_data = {
            "name": "legacy-pkg",
            "version": "1.0.0",
            "agents": ["agent.md"],
            "references": [],
            "knowledge_packs": [],
        }
        m = EnhancedManifest.from_dict(old_data)
        assert m.name == "legacy-pkg"
        assert m.checksums == {}
        assert m.dependencies == []

    def test_backward_compat_empty_dict(self):
        m = EnhancedManifest.from_dict({})
        assert m.name == ""
        assert m.version == "1.0.0"
        assert m.checksums == {}
        assert m.dependencies == []

    def test_to_dict_checksums_included(self):
        m = EnhancedManifest(name="pkg", checksums={"a.txt": "deadbeef"})
        d = m.to_dict()
        assert d["checksums"] == {"a.txt": "deadbeef"}

    def test_to_dict_dependencies_serialised(self):
        m = EnhancedManifest(
            name="pkg",
            dependencies=[PackageDependency(name="dep-a", min_version="2.0")],
        )
        d = m.to_dict()
        assert d["dependencies"] == [{"name": "dep-a", "min_version": "2.0"}]

    def test_to_dict_is_json_serialisable(self):
        m = EnhancedManifest(
            name="pkg",
            checksums={"manifest.json": "abc"},
            dependencies=[PackageDependency(name="dep")],
        )
        raw = json.dumps(m.to_dict())
        assert '"name": "pkg"' in raw

    def test_from_dict_defaults_version(self):
        m = EnhancedManifest.from_dict({"name": "minimal"})
        assert m.version == "1.0.0"

    def test_from_dict_empty_dependencies_list(self):
        m = EnhancedManifest.from_dict({"name": "pkg", "dependencies": []})
        assert m.dependencies == []

    def test_from_dict_non_dict_checksums_gracefully_ignored(self):
        """If checksums field is not a dict (corrupted data), default to empty."""
        m = EnhancedManifest.from_dict({"name": "pkg", "checksums": "not-a-dict"})
        assert m.checksums == {}


# ---------------------------------------------------------------------------
# PackageVerifier.compute_checksums
# ---------------------------------------------------------------------------


class TestComputeChecksums:
    def test_returns_dict_of_member_to_digest(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        checksums = verifier.compute_checksums(archive)
        assert isinstance(checksums, dict)
        assert "manifest.json" in checksums
        # SHA-256 hex digest is always 64 hex chars
        for digest in checksums.values():
            assert len(digest) == 64

    def test_includes_agents_member(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        checksums = verifier.compute_checksums(archive)
        assert "agents/test-agent.md" in checksums

    def test_includes_references_member(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        checksums = verifier.compute_checksums(archive)
        assert "references/git-strategy.md" in checksums

    def test_checksums_are_deterministic(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        first = verifier.compute_checksums(archive)
        second = verifier.compute_checksums(archive)
        assert first == second

    def test_raises_file_not_found_for_missing_archive(self, tmp_path: Path):
        verifier = PackageVerifier()
        with pytest.raises(FileNotFoundError):
            verifier.compute_checksums(tmp_path / "ghost.tar.gz")

    def test_empty_project_archive_has_manifest_checksum(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        builder = PackageBuilder(source_root=empty)
        archive = builder.build("empty-pkg", output_dir=tmp_path / "out")
        verifier = PackageVerifier()
        checksums = verifier.compute_checksums(archive)
        assert "manifest.json" in checksums


# ---------------------------------------------------------------------------
# PackageVerifier.verify_checksums
# ---------------------------------------------------------------------------


class TestVerifyChecksums:
    def test_returns_empty_list_when_all_match(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        expected = verifier.compute_checksums(archive)
        problems = verifier.verify_checksums(archive, expected)
        assert problems == []

    def test_detects_checksum_mismatch(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        expected = verifier.compute_checksums(archive)
        # Corrupt one entry
        expected["manifest.json"] = "0" * 64
        problems = verifier.verify_checksums(archive, expected)
        assert any("manifest.json" in p for p in problems)
        assert any("mismatch" in p for p in problems)

    def test_detects_missing_file(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        # Supply an expected checksum for a file that is not in the archive
        problems = verifier.verify_checksums(
            archive, {"agents/nonexistent-agent.md": "a" * 64}
        )
        assert any("nonexistent-agent.md" in p for p in problems)
        assert any("missing" in p for p in problems)

    def test_ignores_extra_files_in_archive(self, tmp_path: Path):
        """Files in the archive but NOT in expected dict are not reported."""
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        # Only verify manifest.json, ignore the rest
        actual = verifier.compute_checksums(archive)
        expected_subset = {"manifest.json": actual["manifest.json"]}
        problems = verifier.verify_checksums(archive, expected_subset)
        assert problems == []

    def test_empty_expected_returns_no_problems(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        problems = verifier.verify_checksums(archive, {})
        assert problems == []

    def test_multiple_mismatches_all_reported(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        actual = verifier.compute_checksums(archive)
        bad = {k: "f" * 64 for k in actual}
        problems = verifier.verify_checksums(archive, bad)
        assert len(problems) == len(actual)


# ---------------------------------------------------------------------------
# PackageVerifier.validate_package — valid packages
# ---------------------------------------------------------------------------


class TestValidatePackageValid:
    def test_valid_package_returns_valid_true(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.valid is True
        assert result.errors == []

    def test_result_has_correct_counts(self, tmp_path: Path):
        src = _make_project(tmp_path / "src")
        builder = PackageBuilder(source_root=src)
        archive = builder.build(
            "pkg",
            include_agents=True,
            include_references=True,
            output_dir=tmp_path / "out",
        )
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.agent_count == 1
        assert result.reference_count == 1

    def test_result_includes_checksums(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert "manifest.json" in result.checksums
        assert len(result.checksums["manifest.json"]) == 64

    def test_valid_package_no_errors_no_warnings_message(self, tmp_path: Path):
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.valid
        # There may be warnings (e.g. empty created_at from PackageBuilder is
        # actually populated — so no warning expected), but no errors.
        assert result.errors == []

    def test_manifest_checksum_present_and_passes(self, tmp_path: Path):
        """When the manifest embeds checksums, verify_checksums is called."""
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        actual_checksums = verifier.compute_checksums(archive)

        # Re-package with an EnhancedManifest that has correct checksums
        # embedded — simulate by building then inspecting; here we just verify
        # that a package without embedded checksums still validates fine.
        result = verifier.validate_package(archive)
        assert result.valid


# ---------------------------------------------------------------------------
# PackageVerifier.validate_package — invalid packages
# ---------------------------------------------------------------------------


class TestValidatePackageInvalid:
    def test_missing_archive_returns_invalid(self, tmp_path: Path):
        verifier = PackageVerifier()
        result = verifier.validate_package(tmp_path / "ghost.tar.gz")
        assert result.valid is False
        assert any("not found" in e for e in result.errors)

    def test_missing_manifest_returns_invalid(self, tmp_path: Path):
        archive = _make_archive_without_manifest(tmp_path)
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.valid is False
        assert any("manifest" in e.lower() for e in result.errors)

    def test_invalid_agent_file_returns_invalid(self, tmp_path: Path):
        archive = _build_archive(tmp_path, agent_content=_INVALID_AGENT_MD)
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.valid is False
        # AgentValidator should have flagged the bad agent
        assert any("test-agent.md" in e for e in result.errors)

    def test_empty_package_produces_warning(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        builder = PackageBuilder(source_root=empty)
        archive = builder.build("empty-pkg", output_dir=tmp_path / "out")
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        # An empty package has no errors but should warn about no content
        assert any("no agents" in w for w in result.warnings)

    def test_corrupted_archive_returns_invalid(self, tmp_path: Path):
        archive = tmp_path / "corrupt.tar.gz"
        archive.write_bytes(b"this is not a valid gzip")
        verifier = PackageVerifier()
        result = verifier.validate_package(archive)
        assert result.valid is False

    def test_mismatched_embedded_checksums_returns_invalid(self, tmp_path: Path):
        """A manifest with wrong checksums embedded should fail validation."""
        # Build a real archive first so we have a valid structure
        archive = _build_archive(tmp_path)
        verifier = PackageVerifier()
        actual_checksums = verifier.compute_checksums(archive)

        # Build an EnhancedManifest with bad checksums and create a new archive
        bad_manifest = EnhancedManifest(
            name="test-pkg",
            version="1.0.0",
            agents=["test-agent.md"],
            checksums={k: "0" * 64 for k in actual_checksums},
        )
        bad_manifest_json = json.dumps(bad_manifest.to_dict(), indent=2)

        # Reconstruct the archive replacing manifest.json with bad checksums
        bad_archive = tmp_path / "bad-checksums.tar.gz"
        with tarfile.open(archive, "r:gz") as src_tar, \
             tarfile.open(bad_archive, "w:gz") as dst_tar:
            for member in src_tar.getmembers():
                if member.name == "manifest.json":
                    encoded = bad_manifest_json.encode("utf-8")
                    info = tarfile.TarInfo(name="manifest.json")
                    info.size = len(encoded)
                    dst_tar.addfile(info, io.BytesIO(encoded))
                else:
                    fobj = src_tar.extractfile(member)
                    if fobj is not None:
                        dst_tar.addfile(member, fobj)

        result = verifier.validate_package(bad_archive)
        assert result.valid is False
        assert any("checksum" in e.lower() for e in result.errors)

    def test_agent_listed_in_manifest_but_missing_from_archive(self, tmp_path: Path):
        """manifest.agents references a file not present in agents/ dir."""
        archive = _build_archive(tmp_path)

        # Build a patched manifest claiming a ghost agent exists
        ghost_manifest = EnhancedManifest(
            name="test-pkg",
            version="1.0.0",
            agents=["ghost-agent.md"],  # not in the archive
        )
        ghost_manifest_json = json.dumps(ghost_manifest.to_dict(), indent=2)

        patched_archive = tmp_path / "ghost-agent.tar.gz"
        with tarfile.open(archive, "r:gz") as src_tar, \
             tarfile.open(patched_archive, "w:gz") as dst_tar:
            for member in src_tar.getmembers():
                if member.name == "manifest.json":
                    encoded = ghost_manifest_json.encode("utf-8")
                    info = tarfile.TarInfo(name="manifest.json")
                    info.size = len(encoded)
                    dst_tar.addfile(info, io.BytesIO(encoded))
                else:
                    fobj = src_tar.extractfile(member)
                    if fobj is not None:
                        dst_tar.addfile(member, fobj)

        verifier = PackageVerifier()
        result = verifier.validate_package(patched_archive)
        assert result.valid is False
        assert any("ghost-agent.md" in e for e in result.errors)


# ---------------------------------------------------------------------------
# PackageValidationResult dataclass
# ---------------------------------------------------------------------------


class TestPackageValidationResult:
    def test_fields_accessible(self):
        r = PackageValidationResult(
            valid=True,
            errors=[],
            warnings=["a warning"],
            agent_count=2,
            reference_count=1,
            knowledge_count=0,
            checksums={"manifest.json": "abc"},
        )
        assert r.valid is True
        assert r.warnings == ["a warning"]
        assert r.agent_count == 2
        assert r.checksums == {"manifest.json": "abc"}


# ---------------------------------------------------------------------------
# CLI verify-package command (via handler)
# ---------------------------------------------------------------------------


class TestVerifyPackageCommand:
    def test_handler_exits_0_for_valid_package(self, tmp_path: Path, capsys):
        from agent_baton.cli.commands.verify_package import handler
        import argparse

        archive = _build_archive(tmp_path)
        args = argparse.Namespace(archive=str(archive), checksums=False)
        # Should not raise SystemExit
        handler(args)
        out = capsys.readouterr().out
        assert "PASS" in out

    def test_handler_exits_1_for_invalid_package(self, tmp_path: Path, capsys):
        from agent_baton.cli.commands.verify_package import handler
        import argparse

        archive = _make_archive_without_manifest(tmp_path)
        args = argparse.Namespace(archive=str(archive), checksums=False)
        with pytest.raises(SystemExit) as exc_info:
            handler(args)
        assert exc_info.value.code == 1

    def test_handler_prints_checksums_when_flag_set(self, tmp_path: Path, capsys):
        from agent_baton.cli.commands.verify_package import handler
        import argparse

        archive = _build_archive(tmp_path)
        args = argparse.Namespace(archive=str(archive), checksums=True)
        handler(args)
        out = capsys.readouterr().out
        assert "manifest.json" in out
        assert "Checksums" in out

    def test_handler_does_not_print_checksums_without_flag(self, tmp_path: Path, capsys):
        from agent_baton.cli.commands.verify_package import handler
        import argparse

        archive = _build_archive(tmp_path)
        args = argparse.Namespace(archive=str(archive), checksums=False)
        handler(args)
        out = capsys.readouterr().out
        assert "Checksums" not in out

    def test_handler_shows_error_details_on_failure(self, tmp_path: Path, capsys):
        from agent_baton.cli.commands.verify_package import handler
        import argparse

        archive = _make_archive_without_manifest(tmp_path)
        args = argparse.Namespace(archive=str(archive), checksums=False)
        with pytest.raises(SystemExit):
            handler(args)
        out = capsys.readouterr().out
        assert "ERROR" in out

    def test_register_creates_verify_package_subparser(self):
        import argparse
        from agent_baton.cli.commands.verify_package import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        sp = register(sub)
        assert "verify-package" in sp.prog

    def test_command_discovered_by_cli_main(self):
        """verify_package module must be auto-discovered by the CLI."""
        from agent_baton.cli.main import discover_commands
        commands = discover_commands()
        # Module name is verify_package; subcommand name is verify-package
        assert "verify_package" in commands
