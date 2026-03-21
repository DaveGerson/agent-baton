"""Agent Sharing / Packaging — create and install distributable .tar.gz archives.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract tar members after validating no path traversal.

    Prevents CVE-2007-4559 class attacks where crafted archives contain
    members with ``..`` or absolute paths that write outside *dest*.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        member_path = (dest / member.name).resolve()
        if not member_path.is_relative_to(dest_resolved):
            raise ValueError(
                f"Path traversal detected in archive member: {member.name}"
            )
    tar.extractall(dest)


@dataclass
class PackageManifest:
    """Manifest for a distributable agent-baton package."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    baton_version: str = "0.1.0"   # minimum compatible baton version
    created_at: str = ""
    agents: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    knowledge_packs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "baton_version": self.baton_version,
            "created_at": self.created_at,
            "agents": list(self.agents),
            "references": list(self.references),
            "knowledge_packs": list(self.knowledge_packs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PackageManifest:
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "1.0.0")),
            description=str(data.get("description", "")),
            baton_version=str(data.get("baton_version", "0.1.0")),
            created_at=str(data.get("created_at", "")),
            agents=list(data.get("agents", [])),           # type: ignore[arg-type]
            references=list(data.get("references", [])),   # type: ignore[arg-type]
            knowledge_packs=list(data.get("knowledge_packs", [])),  # type: ignore[arg-type]
        )


class PackageBuilder:
    """Create and install distributable archives of agent-baton configurations.

    Archive layout (name-version.tar.gz):
        manifest.json
        agents/*.md
        references/*.md
        knowledge/<pack>/**/*.md  (when included)
    """

    def __init__(self, source_root: Path | None = None) -> None:
        self._source = source_root or Path.cwd()

    @property
    def source_root(self) -> Path:
        return self._source

    def _claude_dir(self, root: Path | None = None) -> Path:
        return (root or self._source) / ".claude"

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        include_agents: bool = True,
        include_references: bool = True,
        include_knowledge: bool = False,
        output_dir: Path | None = None,
    ) -> Path:
        """Create a .tar.gz package from the source project's .claude/ directory.

        Package structure::

            name-version.tar.gz
            ├── manifest.json
            ├── agents/*.md
            ├── references/*.md
            └── knowledge/**/*.md  (if include_knowledge=True)

        Args:
            name: Package name (used in the archive filename).
            version: Semantic version string.
            description: Human-readable description.
            include_agents: Include .claude/agents/*.md files.
            include_references: Include .claude/references/*.md files.
            include_knowledge: Include .claude/knowledge/ tree.
            output_dir: Directory to write the archive to.  Defaults to cwd.

        Returns:
            Path to the created .tar.gz file.
        """
        out_dir = output_dir or Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        archive_path = out_dir / f"{name}-{version}.tar.gz"

        claude = self._claude_dir()
        agents_dir = claude / "agents"
        refs_dir = claude / "references"
        knowledge_dir = claude / "knowledge"

        # Collect files and build manifest lists
        agent_files: list[Path] = []
        ref_files: list[Path] = []
        knowledge_files: list[tuple[Path, str]] = []  # (abs_path, archive_member_name)
        knowledge_pack_names: list[str] = []

        if include_agents and agents_dir.is_dir():
            agent_files = sorted(agents_dir.glob("*.md"))

        if include_references and refs_dir.is_dir():
            ref_files = sorted(refs_dir.glob("*.md"))

        if include_knowledge and knowledge_dir.is_dir():
            for pack_dir in sorted(knowledge_dir.iterdir()):
                if pack_dir.is_dir():
                    knowledge_pack_names.append(pack_dir.name)
                    for md_file in sorted(pack_dir.rglob("*.md")):
                        rel = md_file.relative_to(knowledge_dir)
                        knowledge_files.append((md_file, str(Path("knowledge") / rel)))

        manifest = PackageManifest(
            name=name,
            version=version,
            description=description,
            created_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            agents=[f.name for f in agent_files],
            references=[f.name for f in ref_files],
            knowledge_packs=knowledge_pack_names,
        )

        manifest_json = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False)

        with tarfile.open(archive_path, "w:gz") as tar:
            # manifest.json (written from memory)
            self._add_string_to_tar(tar, manifest_json, "manifest.json")

            # agents/
            for src in agent_files:
                tar.add(src, arcname=f"agents/{src.name}")

            # references/
            for src in ref_files:
                tar.add(src, arcname=f"references/{src.name}")

            # knowledge/
            for src, arcname in knowledge_files:
                tar.add(src, arcname=arcname)

        return archive_path

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------

    def extract(
        self,
        archive_path: Path,
        target_dir: Path | None = None,
    ) -> PackageManifest:
        """Extract a package archive and return its manifest.

        Args:
            archive_path: Path to the .tar.gz file.
            target_dir: Extraction destination.  Defaults to a new temp dir.

        Returns:
            The PackageManifest read from the extracted manifest.json.

        Raises:
            FileNotFoundError: If archive_path does not exist.
            KeyError: If manifest.json is not present in the archive.

        Note:
            When *target_dir* is not provided, a temporary directory is created.
            The caller is responsible for cleaning it up when done.
        """
        if not archive_path.is_file():
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        # When no target_dir is given, the caller is responsible for cleanup
        # of the returned directory.
        dest = target_dir or Path(tempfile.mkdtemp(prefix="baton-pkg-"))

        with tarfile.open(archive_path, "r:gz") as tar:
            _safe_extractall(tar, dest)

        manifest_path = dest / "manifest.json"
        if not manifest_path.exists():
            raise KeyError("manifest.json not found in archive")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return PackageManifest.from_dict(data)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def install_package(
        self,
        archive_path: Path,
        scope: str = "project",
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Install a package: extract, validate, copy to scope.

        Args:
            archive_path: Path to the .tar.gz file.
            scope: ``"user"`` (→ ~/.claude/) or ``"project"`` (→ .claude/).
            force: Overwrite existing files.

        Returns:
            Dict with counts: {"agents": N, "references": N, "knowledge": N}
        """
        if scope == "user":
            base = Path.home() / ".claude"
        else:
            base = self._claude_dir()

        with tempfile.TemporaryDirectory(prefix="baton-install-") as tmp_str:
            tmp_dir = Path(tmp_str)

            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_extractall(tar, tmp_dir)

            manifest_path = tmp_dir / "manifest.json"
            if not manifest_path.exists():
                raise KeyError("manifest.json missing from package")

            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PackageManifest.from_dict(data)

            counts: dict[str, int] = {"agents": 0, "references": 0, "knowledge": 0}

            # Install agents
            src_agents_dir = tmp_dir / "agents"
            if src_agents_dir.is_dir():
                dst_agents_dir = base / "agents"
                for filename in manifest.agents:
                    src_file = src_agents_dir / filename
                    if not src_file.is_file():
                        continue
                    dst_file = dst_agents_dir / filename
                    if self._copy_file(src_file, dst_file, force=force):
                        counts["agents"] += 1

            # Install references
            src_refs_dir = tmp_dir / "references"
            if src_refs_dir.is_dir():
                dst_refs_dir = base / "references"
                for filename in manifest.references:
                    src_file = src_refs_dir / filename
                    if not src_file.is_file():
                        continue
                    dst_file = dst_refs_dir / filename
                    if self._copy_file(src_file, dst_file, force=force):
                        counts["references"] += 1

            # Install knowledge packs
            src_knowledge_dir = tmp_dir / "knowledge"
            if src_knowledge_dir.is_dir():
                dst_knowledge_dir = base / "knowledge"
                for pack_name in manifest.knowledge_packs:
                    src_pack = src_knowledge_dir / pack_name
                    if not src_pack.is_dir():
                        continue
                    dst_pack = dst_knowledge_dir / pack_name
                    for src_file in sorted(src_pack.rglob("*.md")):
                        rel = src_file.relative_to(src_pack)
                        dst_file = dst_pack / rel
                        if self._copy_file(src_file, dst_file, force=force):
                            counts["knowledge"] += 1

        return counts

    # ------------------------------------------------------------------
    # Read manifest without full extraction
    # ------------------------------------------------------------------

    def read_manifest(self, archive_path: Path) -> PackageManifest | None:
        """Read manifest.json from the archive without extracting all files.

        Returns:
            PackageManifest if found, None if the archive is unreadable or
            manifest.json is missing.
        """
        if not archive_path.is_file():
            return None
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                try:
                    member = tar.getmember("manifest.json")
                except KeyError:
                    return None
                f = tar.extractfile(member)
                if f is None:
                    return None
                data = json.loads(f.read().decode("utf-8"))
        except (tarfile.TarError, json.JSONDecodeError, OSError):
            return None

        return PackageManifest.from_dict(data)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_file(src: Path, dst: Path, *, force: bool) -> bool:
        """Copy src to dst. Returns True if copied, False if skipped."""
        if dst.exists() and not force:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True

    @staticmethod
    def _add_string_to_tar(tar: tarfile.TarFile, content: str, arcname: str) -> None:
        """Add a string as a file into an open TarFile (in-memory, no temp file)."""
        import io
        encoded = content.encode("utf-8")
        info = tarfile.TarInfo(name=arcname)
        info.size = len(encoded)
        tar.addfile(info, io.BytesIO(encoded))
