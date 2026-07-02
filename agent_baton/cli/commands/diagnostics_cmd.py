"""``baton doctor`` -- developer-facing installation health report."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any


_PYTHON_MIN = (3, 10)


@dataclass(frozen=True)
class DoctorCheck:
    """Single doctor check result."""

    id: str
    label: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "doctor",
        help=(
            "Check Baton installation health, knowledge packs, assurance packs, "
            "PMO UI assets, and optional local CLIs"
        ),
        description=(
            "Check Baton installation health, knowledge packs, assurance packs, "
            "PMO UI assets, and optional local CLIs."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the doctor report as JSON",
    )
    return parser


def handler(args: argparse.Namespace) -> None:
    payload = build_report(project_root=Path.cwd())
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(payload))


def build_report(project_root: Path | None = None) -> dict[str, Any]:
    root = (project_root or Path.cwd()).expanduser().resolve()
    checks = [
        _check_python(),
        _check_package_version(),
        _check_bundled_agents(),
        _check_project_agents(root),
        _check_knowledge_packs(root),
        _check_assurance_packs(root),
        _check_pmo_ui_assets(root),
        _check_package_resources(),
        _check_bd(),
        _check_git(root),
        _check_claude_cli(),
        _check_team_context(root),
        _check_terminology(),
    ]
    summary = _summary(checks)
    return {
        "schema_version": 1,
        "ok": summary["error"] == 0,
        "project_root": str(root),
        "summary": summary,
        "checks": [check.to_dict() for check in checks],
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "Baton doctor",
        f"Project root: {payload['project_root']}",
        (
            "Summary: "
            f"ok={summary['ok']} warnings={summary['warning']} "
            f"errors={summary['error']}"
        ),
        "",
    ]
    for check in payload["checks"]:
        status = str(check["status"]).upper()
        label = check["label"]
        message = check["message"]
        lines.append(f"[{status}] {label}: {message}")
    return "\n".join(lines)


def _summary(checks: list[DoctorCheck]) -> dict[str, int]:
    counts = {"ok": 0, "warning": 0, "error": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _check_python() -> DoctorCheck:
    current = sys.version_info
    required = f">={_PYTHON_MIN[0]}.{_PYTHON_MIN[1]}"
    version = f"{current.major}.{current.minor}.{current.micro}"
    if (current.major, current.minor) < _PYTHON_MIN:
        return DoctorCheck(
            id="python",
            label="Python version",
            status="error",
            message=f"{version} is below Baton requirement {required}",
            details={"version": version, "requires": required},
        )
    return DoctorCheck(
        id="python",
        label="Python version",
        status="ok",
        message=f"{version} satisfies Baton requirement {required}",
        details={"version": version, "requires": required},
    )


def _check_package_version() -> DoctorCheck:
    version = _package_version()
    return DoctorCheck(
        id="package_version",
        label="Package version",
        status="ok",
        message=f"agent-baton {version}",
        details={"distribution": "agent-baton", "version": version},
    )


def _package_version() -> str:
    try:
        return metadata.version("agent-baton")
    except metadata.PackageNotFoundError:
        try:
            import agent_baton

            return getattr(agent_baton, "__version__", "dev")
        except Exception:
            return "dev"


def _check_bundled_agents() -> DoctorCheck:
    names = _bundled_agent_names()
    if not names:
        return DoctorCheck(
            id="bundled_agents",
            label="Bundled agents",
            status="error",
            message="No bundled agents were found in package resources",
            details={"count": 0, "names": []},
        )
    status = "ok" if "talent-builder" in names else "warning"
    message = (
        f"{len(names)} bundled agents available; talent-builder present"
        if status == "ok"
        else f"{len(names)} bundled agents available; talent-builder missing"
    )
    return DoctorCheck(
        id="bundled_agents",
        label="Bundled agents",
        status=status,
        message=message,
        details={"count": len(names), "names": names},
    )


def _bundled_agent_names() -> list[str]:
    try:
        import importlib.resources as pkg_resources

        root = pkg_resources.files("agent_baton").joinpath("_bundled_agents")
        if not root.is_dir():  # type: ignore[union-attr]
            return []
        names = []
        for entry in root.iterdir():  # type: ignore[union-attr]
            name = getattr(entry, "name", "")
            if name.endswith(".md") and name != "CLAUDE.md":
                names.append(Path(name).stem)
        return sorted(names)
    except Exception:
        return []


def _check_project_agents(project_root: Path) -> DoctorCheck:
    agents_dir = project_root / ".claude" / "agents"
    names = _markdown_stems(agents_dir)
    validation = _validate_agent_dir(agents_dir)
    if not names:
        return DoctorCheck(
            id="project_agents",
            label="Project agents",
            status="warning",
            message=f"No project agents found at {agents_dir}",
            details={
                "path": str(agents_dir),
                "count": 0,
                "names": [],
                **validation,
            },
        )
    validation_errors = validation.get("validation_errors", 0)
    status = "warning" if validation_errors else "ok"
    suffix = (
        f"; {validation_errors} validation errors"
        if validation_errors
        else ""
    )
    return DoctorCheck(
        id="project_agents",
        label="Project agents",
        status=status,
        message=f"{len(names)} project agents found{suffix}",
        details={
            "path": str(agents_dir),
            "count": len(names),
            "names": names,
            **validation,
        },
    )


def _check_knowledge_packs(project_root: Path) -> DoctorCheck:
    project_dir = project_root / ".claude" / "knowledge"
    global_dir = Path.home() / ".claude" / "knowledge"
    project = _count_pack_dirs(project_dir, manifest_name="knowledge.yaml")
    global_ = _count_pack_dirs(global_dir, manifest_name="knowledge.yaml")
    registry_details = _load_knowledge_registry_details(project_root)
    total = project["count"] + global_["count"]
    if total == 0:
        return DoctorCheck(
            id="knowledge_packs",
            label="Knowledge packs",
            status="warning",
            message=(
                "No knowledge packs found; expected manifests are named "
                "knowledge.yaml"
            ),
            details={
                **_pack_details(project_dir, global_dir, project, global_),
                **registry_details,
            },
        )
    return DoctorCheck(
        id="knowledge_packs",
        label="Knowledge packs",
        status="ok",
        message=(
            f"{total} knowledge packs found; "
            f"{project['with_manifest']} have knowledge.yaml"
        ),
        details={
            **_pack_details(project_dir, global_dir, project, global_),
            **registry_details,
        },
    )


def _check_assurance_packs(project_root: Path) -> DoctorCheck:
    project_dir = project_root / ".claude" / "packs"
    global_dir = Path.home() / ".claude" / "packs"
    project = _count_pack_dirs(project_dir, manifest_name="pack.json")
    global_ = _count_pack_dirs(global_dir, manifest_name="pack.json")
    validation = _validate_assurance_pack_dirs(project_dir, global_dir)
    total = project["count"] + global_["count"]
    if total == 0:
        return DoctorCheck(
            id="assurance_packs",
            label="Assurance packs",
            status="warning",
            message="No assurance packs found at .claude/packs",
            details={
                **_pack_details(project_dir, global_dir, project, global_),
                **validation,
            },
        )
    return DoctorCheck(
        id="assurance_packs",
        label="Assurance packs",
        status="ok",
        message=f"{total} assurance packs found",
        details={
            **_pack_details(project_dir, global_dir, project, global_),
            **validation,
        },
    )


def _check_pmo_ui_assets(project_root: Path) -> DoctorCheck:
    pmo_root = project_root / "pmo-ui"
    dist_index = pmo_root / "dist" / "index.html"
    source_index = pmo_root / "index.html"
    source_app = pmo_root / "src" / "App.tsx"
    dist_exists = dist_index.is_file()
    source_exists = source_index.is_file() and source_app.is_file()
    if dist_exists:
        status = "ok"
        message = "Built PMO UI static assets are available"
    elif source_exists:
        status = "warning"
        message = "PMO UI source exists, but pmo-ui/dist/index.html is not built"
    else:
        status = "warning"
        message = "PMO UI assets were not found"
    return DoctorCheck(
        id="pmo_ui_assets",
        label="PMO UI assets",
        status=status,
        message=message,
        details={
            "pmo_root": str(pmo_root),
            "dist_index": str(dist_index),
            "dist_exists": dist_exists,
            "source_exists": source_exists,
        },
    )


def _check_package_resources() -> DoctorCheck:
    resources = {
        "bundled_agents": _package_resource_state("_bundled_agents", "*.md"),
        "references": _package_resource_state("_bundled_references", "*.md"),
        "templates": _package_resource_state("_bundled_templates", "*"),
        "pmo_static_assets": _package_resource_state("_bundled_pmo_ui", "*"),
    }
    missing = [
        name
        for name, state in resources.items()
        if state["status"] != "ok"
    ]
    if missing:
        return DoctorCheck(
            id="package_resources",
            label="Package resources",
            status="warning",
            message=(
                "Package-resource audit found missing optional resources: "
                + ", ".join(missing)
            ),
            details={"resources": resources},
        )
    return DoctorCheck(
        id="package_resources",
        label="Package resources",
        status="ok",
        message="Package-resource audit found all expected resource groups",
        details={"resources": resources},
    )


def _package_resource_state(resource_dir: str, pattern: str) -> dict[str, Any]:
    try:
        import importlib.resources as pkg_resources

        root = pkg_resources.files("agent_baton").joinpath(resource_dir)
        if not root.is_dir():  # type: ignore[union-attr]
            return {
                "status": "warning",
                "count": 0,
                "path": f"agent_baton/{resource_dir}",
                "message": "resource directory is not bundled",
            }
        entries = [
            entry
            for entry in root.iterdir()  # type: ignore[union-attr]
            if _resource_entry_matches(getattr(entry, "name", ""), pattern)
        ]
        if not entries:
            return {
                "status": "warning",
                "count": 0,
                "path": f"agent_baton/{resource_dir}",
                "message": "resource directory is bundled but empty",
            }
        return {
            "status": "ok",
            "count": len(entries),
            "path": f"agent_baton/{resource_dir}",
            "message": "resource directory is bundled",
        }
    except Exception as exc:
        return {
            "status": "warning",
            "count": 0,
            "path": f"agent_baton/{resource_dir}",
            "message": f"resource audit skipped: {exc}",
        }


def _resource_entry_matches(name: str, pattern: str) -> bool:
    if pattern == "*":
        return name not in {"", "__pycache__"}
    if pattern == "*.md":
        return name.endswith(".md")
    return bool(name)


def _check_bd() -> DoctorCheck:
    return _check_optional_cli(
        check_id="bd",
        label="bd availability",
        executable="bd",
        missing_message="Optional bd CLI not found on PATH",
    )


def _check_claude_cli() -> DoctorCheck:
    return _check_optional_cli(
        check_id="claude_cli",
        label="Claude CLI availability",
        executable="claude",
        missing_message="Optional Claude CLI not found on PATH",
    )


def _check_optional_cli(
    *,
    check_id: str,
    label: str,
    executable: str,
    missing_message: str,
) -> DoctorCheck:
    found = shutil.which(executable)
    if not found:
        return DoctorCheck(
            id=check_id,
            label=label,
            status="warning",
            message=missing_message,
            details={"executable": executable, "path": None},
        )
    return DoctorCheck(
        id=check_id,
        label=label,
        status="ok",
        message=f"{executable} found at {found}",
        details={"executable": executable, "path": found},
    )


def _check_git(project_root: Path) -> DoctorCheck:
    if not shutil.which("git"):
        return DoctorCheck(
            id="git",
            label="Git repo status",
            status="warning",
            message="git executable not found on PATH",
            details={"path": None},
        )

    inside = _git(["rev-parse", "--is-inside-work-tree"], project_root)
    if inside["returncode"] != 0 or inside["stdout"].strip() != "true":
        return DoctorCheck(
            id="git",
            label="Git repo status",
            status="warning",
            message="Project root is not inside a git work tree",
            details=inside,
        )

    branch = _git(["branch", "--show-current"], project_root)
    status = _git(["status", "--porcelain"], project_root)
    dirty_lines = [
        line for line in status["stdout"].splitlines() if line.strip()
    ]
    if status["returncode"] != 0:
        return DoctorCheck(
            id="git",
            label="Git repo status",
            status="warning",
            message="Unable to read git status",
            details={"status": status, "branch": branch["stdout"].strip()},
        )
    if dirty_lines:
        return DoctorCheck(
            id="git",
            label="Git repo status",
            status="warning",
            message=f"Git work tree has {len(dirty_lines)} changed paths",
            details={
                "branch": branch["stdout"].strip(),
                "dirty_count": len(dirty_lines),
                "dirty_paths": dirty_lines[:20],
            },
        )
    return DoctorCheck(
        id="git",
        label="Git repo status",
        status="ok",
        message="Git work tree is clean",
        details={"branch": branch["stdout"].strip(), "dirty_count": 0},
    )


def _git(args: list[str], cwd: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc)}
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _check_team_context(project_root: Path) -> DoctorCheck:
    path = project_root / ".claude" / "team-context"
    if not path.is_dir():
        return DoctorCheck(
            id="team_context",
            label=".claude/team-context",
            status="warning",
            message=f"{path} does not exist",
            details={"path": str(path), "writable": False},
        )
    writable, error = _probe_writable_directory(path)
    if not writable:
        return DoctorCheck(
            id="team_context",
            label=".claude/team-context",
            status="warning",
            message=f"{path} is not writable: {error}",
            details={"path": str(path), "writable": False},
        )
    return DoctorCheck(
        id="team_context",
        label=".claude/team-context",
        status="ok",
        message=f"{path} is writable",
        details={"path": str(path), "writable": True},
    )


def _probe_writable_directory(path: Path) -> tuple[bool, str]:
    probe = path / ".baton-doctor-write-test"
    try:
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def _check_terminology() -> DoctorCheck:
    return DoctorCheck(
        id="terminology",
        label="Terminology",
        status="ok",
        message=(
            "Canonical agent is talent-builder; knowledge pack manifests use "
            "knowledge.yaml; assurance packs live under .claude/packs"
        ),
        details={
            "canonical_agent": "talent-builder",
            "knowledge_manifest": "knowledge.yaml",
            "knowledge_pack_dir": ".claude/knowledge",
            "assurance_pack_dir": ".claude/packs",
        },
    )


def _markdown_stems(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(p.stem for p in path.glob("*.md") if p.is_file())


def _validate_agent_dir(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {
            "validated_count": 0,
            "validation_warnings": 0,
            "validation_errors": 0,
        }
    try:
        from agent_baton.core.govern.validator import AgentValidator

        results = AgentValidator().validate_directory(path)
    except Exception as exc:
        return {
            "validated_count": 0,
            "validation_warnings": 0,
            "validation_errors": 0,
            "validation_error": str(exc),
        }
    return {
        "validated_count": len(results),
        "validation_warnings": sum(1 for result in results if result.warnings),
        "validation_errors": sum(1 for result in results if result.errors),
    }


def _count_pack_dirs(path: Path, *, manifest_name: str) -> dict[str, Any]:
    if not path.is_dir():
        return {"count": 0, "with_manifest": 0, "names": []}
    dirs = sorted(p for p in path.iterdir() if p.is_dir())
    return {
        "count": len(dirs),
        "with_manifest": sum(1 for p in dirs if (p / manifest_name).is_file()),
        "names": [p.name for p in dirs],
    }


def _pack_details(
    project_dir: Path,
    global_dir: Path,
    project: dict[str, Any],
    global_: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_path": str(project_dir),
        "global_path": str(global_dir),
        "project_count": project["count"],
        "global_count": global_["count"],
        "project_with_manifest": project["with_manifest"],
        "global_with_manifest": global_["with_manifest"],
        "project_names": project["names"],
        "global_names": global_["names"],
    }


def _load_knowledge_registry_details(project_root: Path) -> dict[str, Any]:
    try:
        from agent_baton.core.orchestration.knowledge_registry import (
            KnowledgeRegistry,
        )

        registry = KnowledgeRegistry()
        loaded = registry.load_default_paths(project_root=project_root)
        return {
            "registry_loaded_count": loaded,
            "registry_well_formed_count": registry.well_formed_pack_count,
            "registry_degraded_count": registry.degraded_pack_count,
            "registry_degraded_names": sorted(registry.degraded_pack_names),
        }
    except Exception as exc:
        return {
            "registry_loaded_count": 0,
            "registry_well_formed_count": 0,
            "registry_degraded_count": 0,
            "registry_degraded_names": [],
            "registry_error": str(exc),
        }


def _validate_assurance_pack_dirs(*roots: Path) -> dict[str, Any]:
    try:
        from agent_baton.core.govern.packs import validate_pack
    except Exception as exc:
        return {"validation_error": str(exc), "invalid_count": 0}

    invalid: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pack_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            errors = validate_pack(pack_dir)
            if errors:
                invalid.append({
                    "pack": pack_dir.name,
                    "path": str(pack_dir),
                    "errors": [str(error) for error in errors],
                })
    return {"invalid_count": len(invalid), "invalid_packs": invalid}
