"""Registry client -- publish and pull agent-baton packages via a local
registry directory.

The registry is an ordinary filesystem directory (typically backed by a git
repository) that stores multiple versions of multiple packages. Each package
version lives in its own subdirectory under ``packages/<name>/<version>/``
and contains the extracted archive contents (``manifest.json``, ``agents/``,
``references/``, ``knowledge/``).

An ``index.json`` file at the registry root tracks all published packages
and their versions. The ``RegistryClient`` reads and updates this index
on every publish/pull operation.

Workflow:

1. **Initialize** a registry with ``init_registry()`` (creates directory
   structure and empty ``index.json``).
2. **Publish** a ``.tar.gz`` archive with ``publish()`` -- extracts the
   archive into the versioned directory and updates the index.
3. **List/search** available packages with ``list_packages()`` and
   ``search()``.
4. **Pull** a package with ``pull()`` -- re-packages the registry
   directory contents into a temporary archive and delegates to
   ``PackageBuilder.install_package()`` for installation.

The caller is responsible for git commit/push operations if the registry
directory is a git repository.

**Status: Experimental** -- built and tested but not yet validated with real
usage data.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.distribute.sharing import PackageBuilder, _safe_extractall
from agent_baton.models.registry import RegistryEntry, RegistryIndex


class RegistryClient:
    """Manages a local registry directory and installs packages from it.

    The registry is an ordinary directory (typically a git repo) with the
    following layout::

        registry_path/
        ├── index.json
        └── packages/
            ├── data-science/
            │   ├── 1.0.0/
            │   │   ├── manifest.json
            │   │   ├── agents/
            │   │   └── references/
            │   └── 2.0.0/
            │       ├── manifest.json
            │       └── agents/
            └── web-development/
                └── 1.0.0/
                    ├── manifest.json
                    └── agents/

    ``publish`` operates on a *local* registry directory.  The caller is
    responsible for committing and pushing to a remote git repo if desired.

    ``pull`` reads from a local registry directory and uses
    :class:`~agent_baton.core.distribute.sharing.PackageBuilder` to install
    the package into the current project or user scope.
    """

    # Name of the top-level index file inside the registry directory.
    INDEX_FILENAME = "index.json"

    def __init__(
        self,
        registry_url: str | None = None,
        local_cache: Path | None = None,
    ) -> None:
        self.registry_url = registry_url
        self.local_cache = local_cache or Path.home() / ".cache" / "agent-baton" / "registry"

    # ------------------------------------------------------------------
    # Registry initialisation
    # ------------------------------------------------------------------

    def init_registry(self, registry_path: Path) -> None:
        """Create an empty registry directory structure with a blank index.

        Creates the following layout if it does not already exist::

            registry_path/
            ├── index.json   (empty index)
            └── packages/    (empty directory)

        Calling ``init_registry`` on an existing registry is a no-op; existing
        files are never overwritten.

        Args:
            registry_path: Directory to initialise as a registry.
        """
        registry_path.mkdir(parents=True, exist_ok=True)
        (registry_path / "packages").mkdir(exist_ok=True)

        index_file = registry_path / self.INDEX_FILENAME
        if not index_file.exists():
            index = RegistryIndex(
                updated_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            )
            self._write_index(registry_path, index)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(self, archive_path: Path, registry_path: Path) -> RegistryEntry:
        """Publish a package archive to a local registry directory.

        Extracts the archive, copies its contents into
        ``registry_path/packages/<name>/<version>/``, and updates
        ``index.json``.  Each version is stored in its own subdirectory so
        multiple versions of the same package can coexist on disk.

        Args:
            archive_path: Path to the ``.tar.gz`` package archive to publish.
            registry_path: Root of the local registry directory.  Must have
                been initialised with :meth:`init_registry` or already contain
                a valid ``index.json``.

        Returns:
            The :class:`~agent_baton.models.registry.RegistryEntry` that was
            written to the index.

        Raises:
            FileNotFoundError: If *archive_path* does not exist.
            KeyError: If ``manifest.json`` is missing from the archive.
            ValueError: If a package with the same name *and* version is
                already present in the registry.
        """
        if not archive_path.is_file():
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        # Read the manifest without full extraction first to fail fast.
        builder = PackageBuilder()
        manifest = builder.read_manifest(archive_path)
        if manifest is None:
            raise KeyError("manifest.json not found in archive")

        # Check for duplicate name+version.
        index = self._read_index(registry_path)
        existing_versions = [
            e.version for e in index.packages.get(manifest.name, [])
        ]
        if manifest.version in existing_versions:
            raise ValueError(
                f"Package '{manifest.name}' version '{manifest.version}' is already "
                f"in the registry.  Bump the version or remove the existing entry first."
            )

        # Each version gets its own directory: packages/<name>/<version>/
        pkg_dir = registry_path / "packages" / manifest.name / manifest.version
        pkg_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="baton-publish-") as tmp_str:
            tmp_dir = Path(tmp_str)
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_extractall(tar, tmp_dir)

            # Copy manifest.json
            src_manifest = tmp_dir / "manifest.json"
            if src_manifest.exists():
                shutil.copy2(src_manifest, pkg_dir / "manifest.json")

            # Copy agents/
            src_agents = tmp_dir / "agents"
            if src_agents.is_dir():
                dst_agents = pkg_dir / "agents"
                if dst_agents.exists():
                    shutil.rmtree(dst_agents)
                shutil.copytree(src_agents, dst_agents)

            # Copy references/
            src_refs = tmp_dir / "references"
            if src_refs.is_dir():
                dst_refs = pkg_dir / "references"
                if dst_refs.exists():
                    shutil.rmtree(dst_refs)
                shutil.copytree(src_refs, dst_refs)

            # Copy knowledge/
            src_knowledge = tmp_dir / "knowledge"
            if src_knowledge.is_dir():
                dst_knowledge = pkg_dir / "knowledge"
                if dst_knowledge.exists():
                    shutil.rmtree(dst_knowledge)
                shutil.copytree(src_knowledge, dst_knowledge)

        # Build the registry entry — path points to the versioned directory.
        relative_path = str(Path("packages") / manifest.name / manifest.version)
        entry = RegistryEntry(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            path=relative_path,
            published_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            baton_version=manifest.baton_version,
            agent_count=len(manifest.agents),
            reference_count=len(manifest.references),
        )

        # Update the index.
        if manifest.name not in index.packages:
            index.packages[manifest.name] = []
        index.packages[manifest.name].append(entry)
        index.updated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        self._write_index(registry_path, index)

        return entry

    # ------------------------------------------------------------------
    # List / Search
    # ------------------------------------------------------------------

    def list_packages(self, registry_path: Path) -> list[RegistryEntry]:
        """Return the latest version of every package in the registry.

        Args:
            registry_path: Root of the local registry directory.

        Returns:
            A list of :class:`~agent_baton.models.registry.RegistryEntry`,
            one per unique package name (the most recent version for each).
            Returns an empty list if the registry is empty or ``index.json``
            does not exist.
        """
        index = self._read_index(registry_path)
        result: list[RegistryEntry] = []
        for entries in index.packages.values():
            if entries:
                result.append(entries[-1])  # last = most recently published
        return result

    def search(self, registry_path: Path, query: str) -> list[RegistryEntry]:
        """Search packages by name substring (case-insensitive).

        Returns the latest version of each matching package.

        Args:
            registry_path: Root of the local registry directory.
            query: Substring to search for in package names.

        Returns:
            Matching :class:`~agent_baton.models.registry.RegistryEntry` list
            (latest version per name).
        """
        lower_query = query.lower()
        return [
            entry
            for entry in self.list_packages(registry_path)
            if lower_query in entry.name.lower()
        ]

    # ------------------------------------------------------------------
    # Pull / Install
    # ------------------------------------------------------------------

    def pull(
        self,
        registry_path: Path,
        package_name: str,
        version: str | None = None,
        install_scope: str = "project",
        *,
        force: bool = False,
        project_root: Path | None = None,
    ) -> dict[str, int]:
        """Install a package from a local registry into the current project.

        Finds the requested package in the registry, re-packages its
        extracted contents into a temporary archive, and delegates to
        :meth:`~agent_baton.core.distribute.sharing.PackageBuilder.install_package`.

        Args:
            registry_path: Root of the local registry directory.
            package_name: Name of the package to install.
            version: Specific version to install.  If ``None``, the latest
                published version is used.
            install_scope: ``"project"`` (→ ``.claude/``) or ``"user"``
                (→ ``~/.claude/``).
            force: Overwrite existing files.
            project_root: Override the installation root for project scope.
                Defaults to the current working directory.

        Returns:
            Dict with counts: ``{"agents": N, "references": N, "knowledge": N}``

        Raises:
            KeyError: If the package is not found in the registry.
            ValueError: If the requested version does not exist.
        """
        index = self._read_index(registry_path)
        entries = index.packages.get(package_name)
        if not entries:
            raise KeyError(f"Package '{package_name}' not found in registry")

        if version is None:
            entry = entries[-1]  # latest
        else:
            matching = [e for e in entries if e.version == version]
            if not matching:
                available = ", ".join(e.version for e in entries)
                raise ValueError(
                    f"Version '{version}' of package '{package_name}' not found. "
                    f"Available: {available}"
                )
            entry = matching[0]

        # The package contents live at registry_path / entry.path
        pkg_dir = registry_path / entry.path

        # Re-create an archive from the extracted package directory so we can
        # reuse PackageBuilder.install_package() without duplicating logic.
        with tempfile.TemporaryDirectory(prefix="baton-pull-") as tmp_str:
            tmp_dir = Path(tmp_str)
            archive_path = tmp_dir / f"{entry.name}-{entry.version}.tar.gz"
            self._repack_directory(pkg_dir, archive_path)

            builder = PackageBuilder(source_root=project_root or Path.cwd())
            counts = builder.install_package(archive_path, scope=install_scope, force=force)

        return counts

    # ------------------------------------------------------------------
    # Index I/O
    # ------------------------------------------------------------------

    def _read_index(self, registry_path: Path) -> RegistryIndex:
        """Read ``index.json`` from *registry_path*.

        Returns an empty :class:`~agent_baton.models.registry.RegistryIndex`
        if the file does not exist (treats a missing index as an empty registry).
        """
        index_file = registry_path / self.INDEX_FILENAME
        if not index_file.exists():
            return RegistryIndex()
        data = json.loads(index_file.read_text(encoding="utf-8"))
        return RegistryIndex.from_dict(data)

    def _write_index(self, registry_path: Path, index: RegistryIndex) -> None:
        """Write *index* to ``index.json`` inside *registry_path*."""
        registry_path.mkdir(parents=True, exist_ok=True)
        index_file = registry_path / self.INDEX_FILENAME
        index_file.write_text(
            json.dumps(index.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _repack_directory(pkg_dir: Path, archive_path: Path) -> None:
        """Create a ``.tar.gz`` archive from an extracted package directory.

        Expects ``pkg_dir`` to contain ``manifest.json`` and optionally
        ``agents/``, ``references/``, and ``knowledge/`` subdirectories.

        Args:
            pkg_dir: The extracted package directory inside the registry.
            archive_path: Destination path for the new ``.tar.gz`` file.
        """
        with tarfile.open(archive_path, "w:gz") as tar:
            manifest_file = pkg_dir / "manifest.json"
            if manifest_file.exists():
                tar.add(manifest_file, arcname="manifest.json")

            for subdir in ("agents", "references", "knowledge"):
                src_dir = pkg_dir / subdir
                if src_dir.is_dir():
                    for file_path in sorted(src_dir.rglob("*")):
                        if file_path.is_file():
                            rel = file_path.relative_to(pkg_dir)
                            tar.add(file_path, arcname=str(rel))
