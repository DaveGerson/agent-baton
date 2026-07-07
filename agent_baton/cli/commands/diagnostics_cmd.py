"""``baton doctor`` -- developer-facing installation health report."""
from __future__ import annotations

import argparse
import json
import os
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
    if not payload["ok"]:
        raise SystemExit(1)


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
        _check_beads_workspace(root),
        _check_git(root),
        _check_git_worktree(root),
        _check_claude_cli(),
        _check_team_context(root),
        _check_planner_validation(root),
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
    validation_error = validation.get("validation_error")
    status = "warning" if validation_errors or validation_error else "ok"
    suffix = (
        f"; {validation_errors} validation errors"
        if validation_errors
        else f"; validation unavailable: {validation_error}"
        if validation_error
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
    missing_manifest_count = (
        (project["count"] - project["with_manifest"])
        + (global_["count"] - global_["with_manifest"])
    )
    registry_degraded_count = registry_details.get("registry_degraded_count", 0)
    registry_error = registry_details.get("registry_error")
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
    degraded = bool(
        missing_manifest_count or registry_degraded_count or registry_error
    )
    status = "warning" if degraded else "ok"
    message_bits = [f"{total} knowledge packs found"]
    if missing_manifest_count:
        message_bits.append(
            f"{missing_manifest_count} missing knowledge.yaml"
        )
    else:
        message_bits.append(
            f"{project['with_manifest'] + global_['with_manifest']} have knowledge.yaml"
        )
    if registry_degraded_count:
        message_bits.append(
            f"{registry_degraded_count} degraded in registry"
        )
    if registry_error:
        message_bits.append("registry diagnostics unavailable")
    return DoctorCheck(
        id="knowledge_packs",
        label="Knowledge packs",
        status=status,
        message="; ".join(message_bits),
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
    invalid_count = validation.get("invalid_count", 0)
    validation_error = validation.get("validation_error")
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
    status = "warning" if invalid_count or validation_error else "ok"
    suffix = (
        f"; {invalid_count} invalid"
        if invalid_count
        else f"; validation unavailable: {validation_error}"
        if validation_error
        else ""
    )
    return DoctorCheck(
        id="assurance_packs",
        label="Assurance packs",
        status=status,
        message=f"{total} assurance packs found{suffix}",
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
    """Verify the ``bd`` CLI is resolvable (ADR-13b WP-G: bd is mandatory).

    Unlike the other optional local CLIs checked by doctor (e.g. ``claude``),
    ``bd`` is not optional after WP-G removed the SQLite bead-store fallback:
    every bead-backed feature (incidents, retrospectives, knowledge capture,
    the PMO scorecard) silently goes dark without it. A missing ``bd``
    binary is therefore reported as a failing (``error``) check, not a
    warning, with an actionable remediation message.

    Honors ``BATON_BD_BIN`` (same override the runtime uses via
    :class:`agent_baton.core.engine.bd_client.BdClient`) so an operator who
    has pointed baton at a non-PATH binary is not falsely flagged.

    The resolved path must be an existing FILE. ``Path.exists()`` alone
    (the prior check) also returns ``True`` for a directory, so a
    ``BATON_BD_BIN`` accidentally pointed at a directory (e.g.
    ``.../bd/`` instead of ``.../bd/bd``) used to pass as "ok" (F8); it
    now fails with a message that says so specifically, rather than the
    generic "not found".
    """
    configured = os.environ.get("BATON_BD_BIN", "").strip()
    executable = configured or "bd"
    found = shutil.which(executable)
    if not found:
        candidate = Path(executable)
        if candidate.is_file():
            found = str(candidate)
        elif candidate.exists():
            # Exists but is not a file (directory, socket, ...) -- see the
            # docstring note on F8; a more specific, more actionable
            # failure than "not found at all".
            return DoctorCheck(
                id="bd",
                label="bd availability",
                status="error",
                message=(
                    f"BATON_BD_BIN={executable!r} exists but is not a file "
                    "(looks like a directory). Point it at the actual bd "
                    "binary, not its containing directory."
                ),
                details={"executable": executable, "path": None},
            )

    if not found:
        return DoctorCheck(
            id="bd",
            label="bd availability",
            status="error",
            message=(
                f"Required 'bd' CLI not found (looked for {executable!r} on PATH). "
                "bd is mandatory after ADR-13b WP-G -- bead-backed features "
                "(incidents, retrospectives, knowledge capture, the PMO "
                "scorecard) cannot record data without it. Install it with "
                "`npm install -g @beads/bd` (or `brew install beads`), or "
                "set BATON_BD_BIN to an existing bd binary."
            ),
            details={"executable": executable, "path": None},
        )
    return DoctorCheck(
        id="bd",
        label="bd availability",
        status="ok",
        message=f"{executable} found at {found}",
        details={"executable": executable, "path": found},
    )


def _check_beads_workspace(project_root: Path) -> DoctorCheck:
    beads_dir = project_root / ".beads"
    expected_files = [
        "config.yaml",
        "interactions.jsonl",
        "metadata.json",
    ]
    present_files = [
        name for name in expected_files if (beads_dir / name).is_file()
    ]
    missing_files = [
        name for name in expected_files if name not in present_files
    ]
    exists = beads_dir.is_dir()
    status = "ok" if exists and not missing_files else "warning"
    if status == "ok":
        message = "Beads workspace files are present"
    elif not exists:
        message = f"Beads workspace directory is missing at {beads_dir}"
    else:
        message = (
            "Beads workspace is missing expected files: "
            + ", ".join(missing_files)
        )
    return DoctorCheck(
        id="beads_workspace",
        label="Beads workspace",
        status=status,
        message=message,
        details={
            "path": str(beads_dir),
            "exists": exists,
            "missing_files": missing_files,
            "present_files": present_files,
        },
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


def _check_git_worktree(project_root: Path) -> DoctorCheck:
    if not shutil.which("git"):
        return DoctorCheck(
            id="git_worktree",
            label="Git worktree topology",
            status="warning",
            message="git executable not found on PATH",
            details={
                "branch": None,
                "git_dir": None,
                "git_common_dir": None,
                "is_linked_worktree": False,
                "is_submodule": False,
                "detached_head": None,
                "path": None,
            },
        )

    inside = _git(["rev-parse", "--is-inside-work-tree"], project_root)
    if inside["returncode"] != 0 or inside["stdout"].strip() != "true":
        return DoctorCheck(
            id="git_worktree",
            label="Git worktree topology",
            status="warning",
            message="Git metadata is not readable for this project root",
            details={
                "branch": None,
                "git_dir": None,
                "git_common_dir": None,
                "is_linked_worktree": False,
                "is_submodule": False,
                "detached_head": None,
                "inside_work_tree": inside["stdout"].strip(),
                "inside_work_tree_probe": inside,
            },
        )

    git_dir_probe = _git(["rev-parse", "--git-dir"], project_root)
    git_common_dir_probe = _git(["rev-parse", "--git-common-dir"], project_root)
    superproject_probe = _git(
        ["rev-parse", "--show-superproject-working-tree"],
        project_root,
    )
    branch_probe = _git(["branch", "--show-current"], project_root)
    head_probe = _git(["rev-parse", "--abbrev-ref", "HEAD"], project_root)

    probes = {
        "git_dir": git_dir_probe,
        "git_common_dir": git_common_dir_probe,
        "superproject": superproject_probe,
        "branch": branch_probe,
        "head": head_probe,
    }
    unreadable = [
        name for name, probe in probes.items() if probe["returncode"] != 0
    ]
    branch = branch_probe["stdout"].strip() or None
    git_dir = git_dir_probe["stdout"].strip() or None
    git_common_dir = git_common_dir_probe["stdout"].strip() or None
    head_name = head_probe["stdout"].strip()
    is_submodule = bool(superproject_probe["stdout"].strip())
    is_linked_worktree = bool(
        git_dir
        and git_common_dir
        and git_dir != git_common_dir
        and not is_submodule
    )
    detached_head = head_name == "HEAD"
    details = {
        "branch": branch,
        "git_dir": git_dir,
        "git_common_dir": git_common_dir,
        "is_linked_worktree": is_linked_worktree,
        "is_submodule": is_submodule,
        "detached_head": detached_head,
    }
    if unreadable:
        details["probe_failures"] = unreadable
        details["probes"] = probes
        return DoctorCheck(
            id="git_worktree",
            label="Git worktree topology",
            status="warning",
            message=(
                "Git metadata could not be fully read: "
                + ", ".join(unreadable)
            ),
            details=details,
        )
    return DoctorCheck(
        id="git_worktree",
        label="Git worktree topology",
        status="ok",
        message="Git worktree metadata is readable",
        details=details,
    )


def _git(args: list[str], cwd: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "--no-optional-locks", *args],
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


# os.access(W_OK) reads POSIX permission bits only; on Windows it ignores
# NTFS ACLs, so a directory can pass this check yet still refuse writes.
_WRITABLE_CHECK_CAVEAT = (
    "metadata check only; NTFS ACLs are not evaluated, so writes may still "
    "be denied at runtime"
)


def _check_team_context(project_root: Path) -> DoctorCheck:
    path = project_root / ".claude" / "team-context"
    if not path.is_dir():
        return DoctorCheck(
            id="team_context",
            label=".claude/team-context",
            status="warning",
            message=f"{path} does not exist",
            details={
                "path": str(path),
                "writable": False,
                "writable_check": "metadata-only",
                "writable_check_caveat": _WRITABLE_CHECK_CAVEAT,
            },
        )
    writable, error = _probe_writable_directory(path)
    if not writable:
        return DoctorCheck(
            id="team_context",
            label=".claude/team-context",
            status="warning",
            message=f"{path} does not appear writable: {error}",
            details={
                "path": str(path),
                "writable": False,
                "writable_check": "metadata-only",
                "writable_check_caveat": _WRITABLE_CHECK_CAVEAT,
            },
        )
    return DoctorCheck(
        id="team_context",
        label=".claude/team-context",
        status="ok",
        message=f"{path} appears writable",
        details={
            "path": str(path),
            "writable": True,
            "writable_check": "metadata-only",
            "writable_check_caveat": _WRITABLE_CHECK_CAVEAT,
        },
    )


def _probe_writable_directory(path: Path) -> tuple[bool, str]:
    try:
        if not path.exists():
            return False, "path does not exist"
        if not path.is_dir():
            return False, "path is not a directory"
        if not os.access(path, os.W_OK):
            return False, "metadata check denied write access"
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


def _with_fallback_caveat(message: str, details: dict[str, Any]) -> str:
    """Append a caveat when the plan came from an unguided fallback guess.

    Only applies once a plan was actually found (``plan_path`` set) — a
    ``fallback-first-found`` selection with no plan at all is just "no plan",
    not a guess worth flagging.
    """
    if details.get("plan_selection") == "fallback-first-found" and details.get(
        "plan_path"
    ):
        return (
            f"{message} (caveat: no active task resolved; validating the "
            "first saved plan found, not necessarily the current one)"
        )
    return message


def _check_planner_validation(project_root: Path) -> DoctorCheck:
    plan_candidates = _saved_plan_candidates(project_root)
    plan_path, active_task_state = _select_saved_plan_for_validation(project_root)
    details: dict[str, Any] = {
        "active_task_id": active_task_state["active_task_id"],
        "active_task_source": active_task_state["active_task_source"],
        "plan_selection": active_task_state["plan_selection"],
        "plan_candidates": [str(path) for path in plan_candidates],
        "plan_path": None,
        "machine_plan_importable": False,
        "validator_importable": False,
        "findings_count": 0,
        "error_count": 0,
        "warning_count": 0,
    }
    if "active_task_sqlite_probe" in active_task_state:
        details["active_task_sqlite_probe"] = active_task_state[
            "active_task_sqlite_probe"
        ]
    try:
        from agent_baton.models.execution import MachinePlan

        details["machine_plan_importable"] = MachinePlan.__name__ == "MachinePlan"
    except Exception as exc:
        details["import_error"] = str(exc)
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message="MachinePlan import failed",
            details=details,
        )

    try:
        from agent_baton.cli.commands.execution.plan_validate_cmd import (
            _validate_plan,
        )

        details["validator_importable"] = True
    except Exception as exc:
        details["import_error"] = str(exc)
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message="Plan validation import failed",
            details=details,
        )

    if plan_path is None:
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="warning",
            message="No saved plan is available to validate",
            details=details,
        )

    details["plan_path"] = str(plan_path)
    if active_task_state["active_plan_missing"]:
        details["active_plan_missing"] = True
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="warning",
            message=f"Active task plan is missing: {plan_path}",
            details=details,
        )
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        details["read_error"] = str(exc)
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message=_with_fallback_caveat(
                f"Saved plan could not be parsed: {exc}", details
            ),
            details=details,
        )

    if not isinstance(data, dict):
        details["validation_error"] = (
            "Saved plan JSON must be an object at the top level"
        )
        details["plan_data_type"] = type(data).__name__
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message=_with_fallback_caveat(
                "Saved plan JSON has invalid top-level shape", details
            ),
            details=details,
        )

    try:
        findings = _validate_plan(data)
    except Exception as exc:
        details["validation_error"] = str(exc)
        details["validation_exception_type"] = type(exc).__name__
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message=_with_fallback_caveat(
                f"Saved plan validation could not run: {exc}", details
            ),
            details=details,
        )

    error_count = sum(
        1 for finding in findings if finding.get("severity") == "error"
    )
    warning_count = sum(
        1 for finding in findings if finding.get("severity") == "warning"
    )
    details.update({
        "findings_count": len(findings),
        "error_count": error_count,
        "warning_count": warning_count,
        "findings": findings,
    })
    if error_count:
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="error",
            message=_with_fallback_caveat(
                f"Saved plan validation found {error_count} errors and "
                f"{warning_count} warnings",
                details,
            ),
            details=details,
        )
    if warning_count:
        return DoctorCheck(
            id="planner_validation",
            label="Planner validation",
            status="warning",
            message=_with_fallback_caveat(
                f"Saved plan validation found {warning_count} warnings", details
            ),
            details=details,
        )
    return DoctorCheck(
        id="planner_validation",
        label="Planner validation",
        status="ok",
        message=_with_fallback_caveat(
            "Saved plan validation passed with no findings", details
        ),
        details=details,
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


def _find_saved_plan(project_root: Path) -> Path | None:
    for candidate in _saved_plan_candidates(project_root):
        if candidate.is_file():
            return candidate
    return None


def _select_saved_plan_for_validation(
    project_root: Path,
) -> tuple[Path | None, dict[str, Any]]:
    context_root = project_root / ".claude" / "team-context"
    (
        active_task_id,
        active_task_source,
        active_task_details,
    ) = _resolve_active_task_for_validation(
        context_root
    )
    active_task_state: dict[str, Any] = {
        "active_task_id": active_task_id,
        "active_task_source": active_task_source,
        "active_plan_missing": False,
        "plan_selection": "active-task" if active_task_id else "fallback-first-found",
        **active_task_details,
    }
    if active_task_id:
        active_plan_path = (
            context_root / "executions" / active_task_id / "plan.json"
        )
        if active_plan_path.is_file():
            return active_plan_path, active_task_state
        active_task_state["active_plan_missing"] = True
        return active_plan_path, active_task_state
    return _find_saved_plan(project_root), active_task_state


def _resolve_active_task_for_validation(
    context_root: Path,
) -> tuple[str | None, str | None, dict[str, Any]]:
    active_task_id = os.environ.get("BATON_TASK_ID", "").strip()
    if active_task_id:
        return active_task_id, "env", {}

    active_task_id, active_task_details = _read_active_task_id_from_sqlite(
        context_root
    )
    if active_task_id:
        return active_task_id, "sqlite", active_task_details

    active_task_id = _read_active_task_id_from_file_marker(context_root)
    if active_task_id:
        return active_task_id, "file", active_task_details
    return None, None, active_task_details


def _read_active_task_id_from_sqlite(
    context_root: Path,
) -> tuple[str | None, dict[str, Any]]:
    # Honour BATON_DB_PATH (mirrors bead_cmd._resolve_db_path's override
    # precedence) so doctor probes the same DB the rest of the CLI uses.
    override = os.environ.get("BATON_DB_PATH", "").strip()
    if override:
        db_path = Path(override).expanduser().resolve()
    else:
        db_path = context_root / "baton.db"
    from agent_baton.core.storage.active_task import (
        read_active_task_id_from_db_copy,
    )

    probe = read_active_task_id_from_db_copy(db_path)
    if probe.degraded:
        return (
            None,
            {"active_task_sqlite_probe": probe.degradation_details()},
        )
    return probe.task_id, {}


def _read_active_task_id_from_file_marker(context_root: Path) -> str | None:
    try:
        from agent_baton.core.engine.persistence import StatePersistence

        return StatePersistence.get_active_task_id(context_root)
    except Exception:
        return None

def _saved_plan_candidates(project_root: Path) -> list[Path]:
    context_root = project_root / ".claude" / "team-context"
    candidates = [
        context_root / "plan.json",
        project_root / "plan.json",
    ]
    executions_dir = context_root / "executions"
    if executions_dir.is_dir():
        for task_dir in sorted(
            child for child in executions_dir.iterdir() if child.is_dir()
        ):
            candidates.append(task_dir / "plan.json")
    return candidates
