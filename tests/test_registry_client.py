"""Tests for agent_baton.core.distribute.registry_client — RegistryClient."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from agent_baton.core.distribute.registry_client import RegistryClient
from agent_baton.core.distribute.sharing import PackageBuilder
from agent_baton.models.registry import RegistryEntry, RegistryIndex


# ---------------------------------------------------------------------------
# Helpers — build minimal .claude/ project layout + archive
# ---------------------------------------------------------------------------

_AGENT_MD = """\
---
name: test-agent
description: A test agent for registry tests.
model: sonnet
---

# Test Agent

You are a test agent.
"""

_REF_MD = "# Reference\n\nThis is a reference.\n"


def _make_project(root: Path) -> Path:
    """Create a minimal .claude/ layout under *root*."""
    agents_dir = root / ".claude" / "agents"
    refs_dir = root / ".claude" / "references"
    agents_dir.mkdir(parents=True)
    refs_dir.mkdir(parents=True)
    (agents_dir / "test-agent.md").write_text(_AGENT_MD, encoding="utf-8")
    (refs_dir / "guide.md").write_text(_REF_MD, encoding="utf-8")
    return root


def _build_archive(
    tmp_path: Path,
    name: str = "test-pkg",
    version: str = "1.0.0",
) -> Path:
    """Build a real .tar.gz archive from a minimal project."""
    src = _make_project(tmp_path / f"src-{name}-{version}")
    builder = PackageBuilder(source_root=src)
    archive = builder.build(
        name=name,
        version=version,
        description=f"Test package {name}",
        include_agents=True,
        include_references=True,
        output_dir=tmp_path / "archives",
    )
    return archive


@pytest.fixture
def registry(tmp_path: Path):
    """Shared fixture: an initialised empty registry."""
    client = RegistryClient()
    reg = tmp_path / "reg"
    client.init_registry(reg)
    return client, reg, tmp_path


# ---------------------------------------------------------------------------
# RegistryEntry serde
# ---------------------------------------------------------------------------

class TestRegistryEntrySerde:
    def test_round_trip(self):
        entry = RegistryEntry(
            name="data-science",
            version="2.0.0",
            description="DS agents",
            path="packages/data-science",
            published_at="2026-03-20T10:00:00+00:00",
            baton_version="0.1.0",
            agent_count=3,
            reference_count=2,
        )
        restored = RegistryEntry.from_dict(entry.to_dict())
        assert restored.name == entry.name
        assert restored.version == entry.version
        assert restored.description == entry.description
        assert restored.path == entry.path
        assert restored.published_at == entry.published_at
        assert restored.baton_version == entry.baton_version
        assert restored.agent_count == entry.agent_count
        assert restored.reference_count == entry.reference_count

    def test_from_dict_defaults(self):
        entry = RegistryEntry.from_dict({"name": "minimal", "version": "1.0.0", "path": "packages/minimal", "published_at": ""})
        assert entry.agent_count == 0
        assert entry.reference_count == 0
        assert entry.baton_version == "0.1.0"

    def test_to_dict_is_json_serialisable(self):
        entry = RegistryEntry(
            name="pkg",
            version="1.0.0",
            description="x",
            path="packages/pkg",
            published_at="2026-01-01T00:00:00+00:00",
            baton_version="0.1.0",
        )
        raw = json.dumps(entry.to_dict())
        assert '"name": "pkg"' in raw


# ---------------------------------------------------------------------------
# RegistryIndex serde
# ---------------------------------------------------------------------------

class TestRegistryIndexSerde:
    def test_round_trip_empty(self):
        idx = RegistryIndex(updated_at="2026-03-20T00:00:00+00:00")
        restored = RegistryIndex.from_dict(idx.to_dict())
        assert restored.packages == {}
        assert restored.updated_at == idx.updated_at

    def test_round_trip_with_entries(self):
        entry = RegistryEntry(
            name="pkg",
            version="1.0.0",
            description="d",
            path="packages/pkg",
            published_at="2026-01-01T00:00:00+00:00",
            baton_version="0.1.0",
        )
        idx = RegistryIndex(
            packages={"pkg": [entry]},
            updated_at="2026-03-20T00:00:00+00:00",
        )
        restored = RegistryIndex.from_dict(idx.to_dict())
        assert "pkg" in restored.packages
        assert len(restored.packages["pkg"]) == 1
        assert restored.packages["pkg"][0].version == "1.0.0"


# ---------------------------------------------------------------------------
# RegistryClient.init_registry
# ---------------------------------------------------------------------------

class TestInitRegistry:
    def test_creates_index_json(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "registry"
        client.init_registry(reg)
        assert (reg / "index.json").exists()

    def test_creates_packages_directory(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "registry"
        client.init_registry(reg)
        assert (reg / "packages").is_dir()

    def test_index_json_has_empty_packages(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "registry"
        client.init_registry(reg)
        data = json.loads((reg / "index.json").read_text(encoding="utf-8"))
        assert data["packages"] == {}

    def test_idempotent_on_existing_registry(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "registry"
        client.init_registry(reg)
        # Write a known value into index.json
        (reg / "index.json").write_text('{"packages":{"x":[]},"updated_at":"t"}', encoding="utf-8")
        # init_registry again should NOT overwrite
        client.init_registry(reg)
        data = json.loads((reg / "index.json").read_text(encoding="utf-8"))
        assert "x" in data["packages"]

    def test_creates_nested_path(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "deep" / "nested" / "registry"
        client.init_registry(reg)
        assert (reg / "index.json").exists()


# ---------------------------------------------------------------------------
# RegistryClient.publish
# ---------------------------------------------------------------------------

class TestPublish:
    def test_publish_creates_package_directory(self, registry):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        client.publish(archive, reg)
        # Each version is stored under packages/<name>/<version>/
        assert (reg / "packages" / "my-pkg" / "1.0.0").is_dir()

    # DECISION: Parameterize test_publish_copies_manifest_json,
    # test_publish_copies_agents, and test_publish_copies_references into one
    # test. Each file is a distinct path inside the version directory.
    @pytest.mark.parametrize("relative_path", [
        Path("manifest.json"),
        Path("agents") / "test-agent.md",
        Path("references") / "guide.md",
    ])
    def test_publish_copies_file_to_registry(self, registry, relative_path):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        client.publish(archive, reg)
        assert (reg / "packages" / "my-pkg" / "1.0.0" / relative_path).exists()

    def test_publish_updates_index(self, registry):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        client.publish(archive, reg)
        data = json.loads((reg / "index.json").read_text(encoding="utf-8"))
        assert "my-pkg" in data["packages"]
        assert len(data["packages"]["my-pkg"]) == 1
        assert data["packages"]["my-pkg"][0]["version"] == "1.0.0"

    def test_publish_returns_registry_entry(self, registry):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        entry = client.publish(archive, reg)
        assert isinstance(entry, RegistryEntry)
        assert entry.name == "my-pkg"
        assert entry.version == "1.0.0"
        assert entry.agent_count == 1
        assert entry.reference_count == 1

    def test_publish_multiple_versions_same_package(self, registry):
        client, reg, tmp_path = registry
        arc1 = _build_archive(tmp_path, "my-pkg", "1.0.0")
        arc2 = _build_archive(tmp_path, "my-pkg", "2.0.0")
        client.publish(arc1, reg)
        client.publish(arc2, reg)
        data = json.loads((reg / "index.json").read_text(encoding="utf-8"))
        assert len(data["packages"]["my-pkg"]) == 2
        versions = [e["version"] for e in data["packages"]["my-pkg"]]
        assert "1.0.0" in versions
        assert "2.0.0" in versions

    def test_publish_duplicate_version_raises(self, registry):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        client.publish(archive, reg)
        # Build a second archive with the same name+version from a fresh source.
        archive2 = _build_archive(tmp_path / "extra", "my-pkg", "1.0.0")
        with pytest.raises(ValueError, match="already in the registry"):
            client.publish(archive2, reg)

    def test_publish_raises_for_missing_archive(self, registry):
        client, reg, tmp_path = registry
        with pytest.raises(FileNotFoundError):
            client.publish(tmp_path / "ghost.tar.gz", reg)

    def test_publish_raises_for_invalid_archive(self, registry):
        """An archive without manifest.json should raise KeyError."""
        client, reg, tmp_path = registry
        bad = tmp_path / "bad.tar.gz"
        import io
        with tarfile.open(bad, "w:gz") as tar:
            data = b"not a package"
            info = tarfile.TarInfo(name="readme.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(KeyError, match="manifest.json"):
            client.publish(bad, reg)

    def test_publish_path_field_is_relative(self, registry):
        client, reg, tmp_path = registry
        archive = _build_archive(tmp_path, "my-pkg", "1.0.0")
        entry = client.publish(archive, reg)
        assert not Path(entry.path).is_absolute()
        assert entry.path.startswith("packages/")
        # Path includes the version component so multiple versions can coexist.
        assert "1.0.0" in entry.path


# ---------------------------------------------------------------------------
# RegistryClient.list_packages
# ---------------------------------------------------------------------------

class TestListPackages:
    def test_empty_registry_returns_empty_list(self, registry):
        client, reg, tmp_path = registry
        assert client.list_packages(reg) == []

    # DECISION: Parameterize test_lists_single_package and test_lists_multiple_packages
    # into one test. The single case verifies the exact name; the multiple case
    # verifies the full set. Both are preserved as parameter tuples.
    @pytest.mark.parametrize("pkg_names,expected_names", [
        (["pkg-a"], {"pkg-a"}),
        (["pkg-a", "pkg-b"], {"pkg-a", "pkg-b"}),
    ])
    def test_lists_packages(self, registry, pkg_names, expected_names):
        client, reg, tmp_path = registry
        for name in pkg_names:
            client.publish(_build_archive(tmp_path, name, "1.0.0"), reg)
        names = {e.name for e in client.list_packages(reg)}
        assert names == expected_names

    def test_returns_latest_version_per_package(self, registry):
        """list_packages returns the most recently published version."""
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "pkg-a", "1.0.0"), reg)
        client.publish(_build_archive(tmp_path, "pkg-a", "2.0.0"), reg)
        entries = client.list_packages(reg)
        assert len(entries) == 1
        assert entries[0].version == "2.0.0"

    def test_missing_index_returns_empty_list(self, tmp_path: Path):
        client = RegistryClient()
        reg = tmp_path / "reg"
        reg.mkdir()  # exists but no index.json
        assert client.list_packages(reg) == []


# ---------------------------------------------------------------------------
# RegistryClient.search
# ---------------------------------------------------------------------------

class TestSearch:
    # DECISION: Parameterize all 5 search tests into 2. The first covers
    # substring match, case insensitivity, no-match, and multi-match by varying
    # the query and expected result set. The empty-query "matches all" case
    # is kept separate as it tests a distinct code path (no filter applied).
    @pytest.mark.parametrize("packages,query,expected_names", [
        (
            ["data-science", "web-development"],
            "data",
            {"data-science"},
        ),
        (
            ["Data-Science"],
            "DATA",
            {"Data-Science"},
        ),
        (
            ["pkg-a"],
            "zzz",
            set(),
        ),
        (
            ["python-agents", "python-tools", "web-agents"],
            "python",
            {"python-agents", "python-tools"},
        ),
    ])
    def test_search_filter(self, registry, packages, query, expected_names):
        client, reg, tmp_path = registry
        for name in packages:
            client.publish(_build_archive(tmp_path, name, "1.0.0"), reg)
        results = client.search(reg, query)
        assert {r.name for r in results} == expected_names

    def test_search_empty_query_matches_all(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "pkg-a", "1.0.0"), reg)
        client.publish(_build_archive(tmp_path, "pkg-b", "1.0.0"), reg)
        results = client.search(reg, "")
        assert len(results) == 2


# ---------------------------------------------------------------------------
# RegistryClient.pull
# ---------------------------------------------------------------------------

class TestPull:
    def test_pull_installs_agents(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)

        install_root = tmp_path / "project"
        install_root.mkdir()
        client.pull(
            registry_path=reg,
            package_name="my-pkg",
            install_scope="project",
            project_root=install_root,
        )
        assert (install_root / ".claude" / "agents" / "test-agent.md").exists()

    def test_pull_installs_references(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)

        install_root = tmp_path / "project"
        install_root.mkdir()
        client.pull(
            registry_path=reg,
            package_name="my-pkg",
            install_scope="project",
            project_root=install_root,
        )
        assert (install_root / ".claude" / "references" / "guide.md").exists()

    def test_pull_returns_counts(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)

        install_root = tmp_path / "project"
        install_root.mkdir()
        counts = client.pull(
            registry_path=reg,
            package_name="my-pkg",
            install_scope="project",
            project_root=install_root,
        )
        assert counts["agents"] >= 1
        assert counts["references"] >= 1
        assert "knowledge" in counts

    def test_pull_latest_when_multiple_versions(self, registry):
        client, reg, tmp_path = registry

        # Publish two versions; each has a differently-named agent so we can
        # distinguish which version was installed.
        src_v1 = tmp_path / "src-v1"
        (src_v1 / ".claude" / "agents").mkdir(parents=True)
        (src_v1 / ".claude" / "references").mkdir()
        (src_v1 / ".claude" / "agents" / "agent-v1.md").write_text(_AGENT_MD.replace("test-agent", "agent-v1"), encoding="utf-8")

        src_v2 = tmp_path / "src-v2"
        (src_v2 / ".claude" / "agents").mkdir(parents=True)
        (src_v2 / ".claude" / "references").mkdir()
        (src_v2 / ".claude" / "agents" / "agent-v2.md").write_text(_AGENT_MD.replace("test-agent", "agent-v2"), encoding="utf-8")

        builder1 = PackageBuilder(source_root=src_v1)
        arc1 = builder1.build("versioned-pkg", version="1.0.0", output_dir=tmp_path / "archives")
        builder2 = PackageBuilder(source_root=src_v2)
        arc2 = builder2.build("versioned-pkg", version="2.0.0", output_dir=tmp_path / "archives")

        client.publish(arc1, reg)
        client.publish(arc2, reg)

        install_root = tmp_path / "project"
        install_root.mkdir()
        client.pull(
            registry_path=reg,
            package_name="versioned-pkg",
            install_scope="project",
            project_root=install_root,
        )
        # Should install v2 (latest)
        assert (install_root / ".claude" / "agents" / "agent-v2.md").exists()

    def test_pull_specific_version(self, registry):
        client, reg, tmp_path = registry

        src_v1 = tmp_path / "src-v1"
        (src_v1 / ".claude" / "agents").mkdir(parents=True)
        (src_v1 / ".claude" / "references").mkdir()
        (src_v1 / ".claude" / "agents" / "agent-v1.md").write_text(_AGENT_MD.replace("test-agent", "agent-v1"), encoding="utf-8")

        src_v2 = tmp_path / "src-v2"
        (src_v2 / ".claude" / "agents").mkdir(parents=True)
        (src_v2 / ".claude" / "references").mkdir()
        (src_v2 / ".claude" / "agents" / "agent-v2.md").write_text(_AGENT_MD.replace("test-agent", "agent-v2"), encoding="utf-8")

        builder1 = PackageBuilder(source_root=src_v1)
        arc1 = builder1.build("versioned-pkg", version="1.0.0", output_dir=tmp_path / "archives")
        builder2 = PackageBuilder(source_root=src_v2)
        arc2 = builder2.build("versioned-pkg", version="2.0.0", output_dir=tmp_path / "archives")

        client.publish(arc1, reg)
        client.publish(arc2, reg)

        install_root = tmp_path / "project"
        install_root.mkdir()
        client.pull(
            registry_path=reg,
            package_name="versioned-pkg",
            version="1.0.0",
            install_scope="project",
            project_root=install_root,
        )
        assert (install_root / ".claude" / "agents" / "agent-v1.md").exists()
        assert not (install_root / ".claude" / "agents" / "agent-v2.md").exists()

    def test_pull_nonexistent_package_raises(self, registry):
        client, reg, tmp_path = registry
        with pytest.raises(KeyError, match="not found in registry"):
            client.pull(
                registry_path=reg,
                package_name="ghost-package",
                project_root=tmp_path / "project",
            )

    def test_pull_nonexistent_version_raises(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)
        with pytest.raises(ValueError, match="not found"):
            client.pull(
                registry_path=reg,
                package_name="my-pkg",
                version="99.0.0",
                project_root=tmp_path / "project",
            )

    def test_pull_force_overwrites_existing(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)

        install_root = tmp_path / "project"
        (install_root / ".claude" / "agents").mkdir(parents=True)
        existing = install_root / ".claude" / "agents" / "test-agent.md"
        existing.write_text("old content", encoding="utf-8")

        client.pull(
            registry_path=reg,
            package_name="my-pkg",
            install_scope="project",
            project_root=install_root,
            force=True,
        )
        assert existing.read_text(encoding="utf-8") != "old content"

    def test_pull_skips_existing_without_force(self, registry):
        client, reg, tmp_path = registry
        client.publish(_build_archive(tmp_path, "my-pkg", "1.0.0"), reg)

        install_root = tmp_path / "project"
        (install_root / ".claude" / "agents").mkdir(parents=True)
        existing = install_root / ".claude" / "agents" / "test-agent.md"
        existing.write_text("old content", encoding="utf-8")

        client.pull(
            registry_path=reg,
            package_name="my-pkg",
            install_scope="project",
            project_root=install_root,
            force=False,
        )
        assert existing.read_text(encoding="utf-8") == "old content"
