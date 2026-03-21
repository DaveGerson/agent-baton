"""Enhanced packaging module — checksum validation and dependency tracking."""
from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.core.govern.validator import AgentValidator


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PackageDependency:
    """A named dependency on another agent-baton package."""

    name: str
    min_version: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "min_version": self.min_version}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PackageDependency:
        return cls(
            name=str(data.get("name", "")),
            min_version=str(data.get("min_version", "")),
        )


@dataclass
class EnhancedManifest:
    """Extended package manifest with checksums and dependency tracking.

    Backward-compatible with the existing PackageManifest: ``from_dict`` accepts
    old manifests that lack the ``checksums`` and ``dependencies`` fields.
    """

    name: str
    version: str = "1.0.0"
    description: str = ""
    baton_version: str = "0.1.0"
    created_at: str = ""
    agents: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    knowledge_packs: list[str] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)
    dependencies: list[PackageDependency] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

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
            "checksums": dict(self.checksums),
            "dependencies": [d.to_dict() for d in self.dependencies],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EnhancedManifest:
        raw_deps: list[object] = list(data.get("dependencies", []))  # type: ignore[arg-type]
        deps: list[PackageDependency] = []
        for item in raw_deps:
            if isinstance(item, dict):
                deps.append(PackageDependency.from_dict(item))

        raw_checksums = data.get("checksums", {})
        checksums: dict[str, str] = (
            {str(k): str(v) for k, v in raw_checksums.items()}  # type: ignore[union-attr]
            if isinstance(raw_checksums, dict)
            else {}
        )

        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "1.0.0")),
            description=str(data.get("description", "")),
            baton_version=str(data.get("baton_version", "0.1.0")),
            created_at=str(data.get("created_at", "")),
            agents=list(data.get("agents", [])),           # type: ignore[arg-type]
            references=list(data.get("references", [])),   # type: ignore[arg-type]
            knowledge_packs=list(data.get("knowledge_packs", [])),  # type: ignore[arg-type]
            checksums=checksums,
            dependencies=deps,
        )


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass
class PackageValidationResult:
    """Result of a comprehensive package validation run."""

    valid: bool
    errors: list[str]
    warnings: list[str]
    agent_count: int
    reference_count: int
    knowledge_count: int
    checksums: dict[str, str]


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class PackageVerifier:
    """Validates the integrity and content of agent-baton package archives."""

    # ------------------------------------------------------------------
    # Checksums
    # ------------------------------------------------------------------

    def compute_checksums(self, archive_path: Path) -> dict[str, str]:
        """Compute SHA-256 checksums for every file inside *archive_path*.

        Args:
            archive_path: Path to a ``.tar.gz`` package archive.

        Returns:
            Mapping of ``{relative_archive_path: hex_digest}``.  The
            ``manifest.json`` member is included.

        Raises:
            FileNotFoundError: If *archive_path* does not exist.
            tarfile.TarError: If the archive cannot be opened.
        """
        if not archive_path.is_file():
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        result: dict[str, str] = {}
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                digest = hashlib.sha256(f.read()).hexdigest()
                result[member.name] = digest
        return result

    def verify_checksums(
        self,
        archive_path: Path,
        expected: dict[str, str],
    ) -> list[str]:
        """Verify archive contents against *expected* checksums.

        Args:
            archive_path: Path to the ``.tar.gz`` archive.
            expected: Mapping of ``{member_name: expected_hex_digest}``.

        Returns:
            List of problem descriptions (empty list means all files match).
            Problems include: mismatched digest, file present in *expected*
            but missing from archive.
        """
        actual = self.compute_checksums(archive_path)
        problems: list[str] = []

        for name, expected_digest in expected.items():
            if name not in actual:
                problems.append(f"missing from archive: {name}")
            elif actual[name] != expected_digest:
                problems.append(
                    f"checksum mismatch for {name}: "
                    f"expected {expected_digest}, got {actual[name]}"
                )

        return problems

    # ------------------------------------------------------------------
    # Comprehensive validation
    # ------------------------------------------------------------------

    def validate_package(self, archive_path: Path) -> PackageValidationResult:
        """Run comprehensive validation on *archive_path*.

        Checks performed:
        - Archive is readable and contains ``manifest.json``
        - All required manifest fields are populated
        - Checksums are internally consistent (manifest checksums, if present,
          match actual file digests)
        - All agents listed in the manifest pass ``AgentValidator``

        Args:
            archive_path: Path to the ``.tar.gz`` package.

        Returns:
            A :class:`PackageValidationResult` describing the outcome.
        """
        errors: list[str] = []
        warnings: list[str] = []
        agent_count = 0
        reference_count = 0
        knowledge_count = 0
        checksums: dict[str, str] = {}

        # ── 1. Archive must exist ─────────────────────────────────────────
        if not archive_path.is_file():
            return PackageValidationResult(
                valid=False,
                errors=[f"archive not found: {archive_path}"],
                warnings=[],
                agent_count=0,
                reference_count=0,
                knowledge_count=0,
                checksums={},
            )

        # ── 2. Archive must be a valid .tar.gz with manifest.json ─────────
        try:
            checksums = self.compute_checksums(archive_path)
        except (tarfile.TarError, OSError) as exc:
            return PackageValidationResult(
                valid=False,
                errors=[f"cannot open archive: {exc}"],
                warnings=[],
                agent_count=0,
                reference_count=0,
                knowledge_count=0,
                checksums={},
            )

        if "manifest.json" not in checksums:
            return PackageValidationResult(
                valid=False,
                errors=["manifest.json is missing from the archive"],
                warnings=[],
                agent_count=0,
                reference_count=0,
                knowledge_count=0,
                checksums=checksums,
            )

        # ── 3. Read and parse manifest ────────────────────────────────────
        manifest: EnhancedManifest | None = None
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                member = tar.getmember("manifest.json")
                fobj = tar.extractfile(member)
                if fobj is None:
                    errors.append("manifest.json could not be read from archive")
                else:
                    raw = json.loads(fobj.read().decode("utf-8"))
                    manifest = EnhancedManifest.from_dict(raw)
        except (tarfile.TarError, json.JSONDecodeError, KeyError, OSError) as exc:
            errors.append(f"cannot parse manifest.json: {exc}")

        if manifest is None:
            return PackageValidationResult(
                valid=False,
                errors=errors,
                warnings=warnings,
                agent_count=0,
                reference_count=0,
                knowledge_count=0,
                checksums=checksums,
            )

        # ── 4. Required manifest fields ───────────────────────────────────
        if not manifest.name or not manifest.name.strip():
            errors.append("manifest 'name' field is empty")

        if not manifest.version or not manifest.version.strip():
            errors.append("manifest 'version' field is empty")

        if not manifest.baton_version or not manifest.baton_version.strip():
            errors.append("manifest 'baton_version' field is empty")

        if not manifest.created_at or not manifest.created_at.strip():
            warnings.append("manifest 'created_at' field is empty")

        # ── 5. Count content ──────────────────────────────────────────────
        agent_count = len(manifest.agents)
        reference_count = len(manifest.references)
        knowledge_count = len(manifest.knowledge_packs)

        if agent_count == 0 and reference_count == 0 and knowledge_count == 0:
            warnings.append("package contains no agents, references, or knowledge packs")

        # ── 6. Verify manifest checksums (if present) ─────────────────────
        if manifest.checksums:
            mismatches = self.verify_checksums(archive_path, manifest.checksums)
            for problem in mismatches:
                errors.append(f"checksum validation failed: {problem}")

        # ── 7. Validate agents with AgentValidator ────────────────────────
        if manifest.agents:
            agent_errors = self._validate_agents_in_archive(archive_path, manifest.agents)
            errors.extend(agent_errors)

        valid = len(errors) == 0
        return PackageValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            agent_count=agent_count,
            reference_count=reference_count,
            knowledge_count=knowledge_count,
            checksums=checksums,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_agents_in_archive(
        self,
        archive_path: Path,
        agent_names: list[str],
    ) -> list[str]:
        """Extract agent files into a temp dir and run AgentValidator on them."""
        errors: list[str] = []
        validator = AgentValidator()

        with tempfile.TemporaryDirectory(prefix="baton-verify-") as tmp_str:
            tmp_dir = Path(tmp_str)
            agents_dir = tmp_dir / "agents"
            agents_dir.mkdir()

            # Extract only agents/ members
            try:
                with tarfile.open(archive_path, "r:gz") as tar:
                    for name in agent_names:
                        member_name = f"agents/{name}"
                        try:
                            member = tar.getmember(member_name)
                        except KeyError:
                            errors.append(
                                f"agent '{name}' listed in manifest but not found "
                                f"in archive as '{member_name}'"
                            )
                            continue
                        fobj = tar.extractfile(member)
                        if fobj is None:
                            errors.append(f"agent '{name}' could not be read from archive")
                            continue
                        dest = agents_dir / name
                        dest.write_bytes(fobj.read())
            except (tarfile.TarError, OSError) as exc:
                errors.append(f"error extracting agents for validation: {exc}")
                return errors

            # Run validator on each extracted agent file
            for name in agent_names:
                agent_path = agents_dir / name
                if not agent_path.exists():
                    continue  # already reported above
                result = validator.validate_file(agent_path)
                if not result.valid:
                    for err in result.errors:
                        errors.append(f"agent '{name}': {err}")

        return errors
