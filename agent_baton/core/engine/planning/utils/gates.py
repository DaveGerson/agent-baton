"""Gate construction, test-file scoping, and project-config application.

Extracted from ``_legacy_planner.IntelligentPlanner``.
"""
from __future__ import annotations

import glob
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agent_baton.models.execution import PlanGate

if TYPE_CHECKING:
    from agent_baton.core.config import ProjectConfig
    from agent_baton.core.orchestration.router import StackProfile
    from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GateScope type (moved from _legacy_planner)
# ---------------------------------------------------------------------------

GateScope = Literal["focused", "full", "smoke"]

# ---------------------------------------------------------------------------
# Gate commands by detected stack
# ---------------------------------------------------------------------------

_STACK_GATE_COMMANDS: dict[str | None, dict[str, str]] = {
    "python": {"test": "pytest --cov", "build": "pytest"},
    "typescript": {"test": "npm test", "build": "npx tsc --noEmit"},
    "javascript": {"test": "npm test", "build": "npm test"},
    "go": {"test": "go test ./...", "build": "go build ./..."},
    "rust": {"test": "cargo test", "build": "cargo build"},
    "java": {"test": "mvn test", "build": "mvn compile"},
    "ruby": {"test": "bundle exec rake test", "build": "bundle exec rake"},
    "kotlin": {"test": "gradle test", "build": "gradle build"},
    "csharp": {"test": "dotnet test", "build": "dotnet build"},
}

_DEFAULT_GATE_COMMANDS: dict[str, str] = {"test": "pytest --cov", "build": "pytest"}

DOMAIN_KEYS_BY_AGENT_BASE: dict[str, str] = {
    "backend-engineer": "backend",
    "frontend-engineer": "frontend",
    "test-engineer": "test",
    "data-engineer": "data",
    "devops-engineer": "devops",
    "documentation-architect": "docs",
    "documentation-engineer": "docs",
}

_SKIP_GATE_SCOPE_PATTERNS: tuple[str, ...] = (
    "__init__.py",
    "_validators.py",
    "agent_baton/templates/",
    "__pycache__/",
)

_MAX_GATE_TEST_FILES = 20


# ---------------------------------------------------------------------------
# Test-file scoping (moved from _legacy_planner)
# ---------------------------------------------------------------------------

def _test_files_for_changes(
    changed_paths: list[str],
    project_root: Path | None = None,
) -> list[str]:
    """Map a list of changed source paths to the test files that cover them."""
    root = project_root or Path(".")
    found: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p not in seen:
            seen.add(p)
            found.append(p)

    for src in changed_paths:
        src_norm = src.replace("\\", "/")

        if any(pat in src_norm for pat in _SKIP_GATE_SCOPE_PATTERNS):
            continue

        fname = Path(src_norm).name
        if fname.startswith("test_") or "/tests/" in src_norm or src_norm.startswith("tests/"):
            if (root / src_norm).exists():
                _add(src_norm)
            continue

        stem = Path(src_norm).stem

        candidates: list[str] = [
            f"tests/test_{stem}.py",
            f"tests/test_{stem}_*.py",
            f"tests/integration/test_{stem}*.py",
            f"tests/unit/test_{stem}*.py",
        ]

        for pattern in candidates:
            for match in sorted(glob.glob(str(root / pattern))):
                rel = str(Path(match).relative_to(root)).replace("\\", "/")
                _add(rel)

    if len(found) > _MAX_GATE_TEST_FILES:
        return []
    return found


def _coverage_package_for_changes(changed_paths: list[str]) -> str:
    """Derive the most specific ``--cov=<pkg>`` argument for changed paths."""
    pkg_paths: list[Path] = []
    for p in changed_paths:
        norm = p.replace("\\", "/")
        if "agent_baton/" in norm:
            pkg_paths.append(Path(norm).parent)
        elif norm.startswith("agent_baton"):
            pkg_paths.append(Path(norm).parent)

    if not pkg_paths:
        return ""

    parts_list = [list(p.parts) for p in pkg_paths]
    common: list[str] = []
    for parts in zip(*parts_list):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break

    if not common or common[0] != "agent_baton":
        return ""
    return "/".join(common)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def command_for_gate_type(gate_type: str) -> str:
    """Map a gate-type string to a sensible default command."""
    mapping = {
        "pytest": "pytest",
        "test": "pytest",
        "lint": "ruff check .",
        "ruff": "ruff check .",
        "mypy": "mypy .",
        "build": "python -m build",
        "format": "ruff format --check .",
    }
    return mapping.get(gate_type, "")


def default_gate(
    phase_name: str,
    stack: "StackProfile | None" = None,
    changed_paths: list[str] | None = None,
    gate_scope: "GateScope" = "focused",
    project_root: Path | None = None,
) -> PlanGate | None:
    """Return an appropriate QA gate for a phase name."""
    name_lower = phase_name.lower()
    if name_lower in ("investigate", "research", "review", "design", "feedback"):
        return None

    language = stack.language if stack else None
    commands = _STACK_GATE_COMMANDS.get(language, _DEFAULT_GATE_COMMANDS)

    if language:
        try:
            from agent_baton.core.learn.overrides import LearnedOverrides
            _gate_overrides = LearnedOverrides().get_gate_overrides()
            _lang_gates = _gate_overrides.get(language, {})
            if _lang_gates:
                commands = dict(commands)
                commands.update(_lang_gates)
        except Exception:
            pass

    is_python_stack = language in (None, "python")

    if name_lower == "test":
        if gate_scope == "full" or not is_python_stack:
            return PlanGate(
                gate_type="test",
                command=commands["test"],
                description="Run full test suite with coverage report.",
                fail_on=["test failure", "coverage below threshold"],
            )
        if gate_scope == "smoke":
            return PlanGate(
                gate_type="test",
                command="pytest --co -q",
                description="Collect-only smoke check — verifies test discovery without running.",
                fail_on=["collection error"],
            )
        test_files = _test_files_for_changes(changed_paths or [], project_root)
        if test_files:
            cov_pkg = _coverage_package_for_changes(changed_paths or [])
            cov_flag = f" --cov={cov_pkg}" if cov_pkg else " --cov"
            files_str = " ".join(test_files)
            return PlanGate(
                gate_type="test",
                command=f"pytest{cov_flag} {files_str}",
                description=(
                    f"Run focused test suite (scoped to {len(test_files)} file(s)) "
                    f"with coverage report. bd-124f."
                ),
                fail_on=["test failure", "coverage below threshold"],
            )
        return PlanGate(
            gate_type="test",
            command="pytest --co -q",
            description=(
                "No specific test files found for changed paths; "
                "running collect-only smoke check. bd-124f."
            ),
            fail_on=["collection error"],
        )

    if gate_scope == "full" or not is_python_stack:
        return PlanGate(
            gate_type="build",
            command=commands["build"],
            description="Run test suite to verify the implementation builds cleanly.",
            fail_on=["test failure", "import error"],
        )
    if gate_scope == "smoke":
        return PlanGate(
            gate_type="build",
            command='python -c "import agent_baton; print(\'ok\')"',
            description="Import smoke check — fast sanity that the package imports cleanly.",
            fail_on=["import error"],
        )
    test_files = _test_files_for_changes(changed_paths or [], project_root)
    if test_files:
        files_str = " ".join(test_files)
        return PlanGate(
            gate_type="build",
            command=f"pytest {files_str}",
            description=(
                f"Run focused build check ({len(test_files)} file(s) scoped to "
                f"changed paths). bd-124f."
            ),
            fail_on=["test failure", "import error"],
        )
    return PlanGate(
        gate_type="build",
        command='python -c "import agent_baton; print(\'ok\')"',
        description=(
            "No specific test files found for changed paths; "
            "running import smoke check. bd-124f."
        ),
        fail_on=["import error"],
    )


def apply_project_config(
    phases: "list[PlanPhase]",
    project_config: "ProjectConfig",
    isolation_overrides: dict[str, str],
) -> None:
    """Apply ``baton.yaml`` defaults to *phases* in place."""
    cfg = project_config
    if cfg.is_empty():
        return

    for phase in phases:
        for step in phase.steps:
            paths_for_match = list(step.allowed_paths) + list(step.context_files)
            routed = cfg.route_agent_for_paths(paths_for_match)
            if routed:
                step.agent_name = routed
            else:
                base = step.agent_name.split("--")[0]
                domain = DOMAIN_KEYS_BY_AGENT_BASE.get(base)
                if domain:
                    preferred = cfg.default_agents.get(domain)
                    if preferred:
                        step.agent_name = preferred

            if cfg.excluded_paths:
                blocked = list(step.blocked_paths)
                seen = set(blocked)
                for p in cfg.excluded_paths:
                    if p not in seen:
                        blocked.append(p)
                        seen.add(p)
                step.blocked_paths = blocked

            if cfg.default_isolation:
                isolation_overrides[step.step_id] = cfg.default_isolation

        if cfg.default_gates:
            existing_types: set[str] = set()
            if phase.gate is not None:
                existing_types.add(phase.gate.gate_type)
            for gate_type in cfg.default_gates:
                if gate_type in existing_types:
                    continue
                new_gate = PlanGate(
                    gate_type=gate_type,
                    command=command_for_gate_type(gate_type),
                    description=f"Project config: enforce {gate_type}",
                )
                if phase.gate is None:
                    phase.gate = new_gate
                else:
                    phase.gate.description = (
                        f"{phase.gate.description}; "
                        f"plus {gate_type} (project config)"
                    ).strip("; ")
                existing_types.add(gate_type)
