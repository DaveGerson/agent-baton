"""Codebase briefing generator.

Produces a concise (~60 line) summary of a repository so that agents
dispatched via Agent Baton start with non-trivial context.  Stdlib only;
no external dependencies are introduced.

Public entry points
-------------------
- :class:`CodebaseBrief` — dataclass containing the structured brief.
- :class:`CodebaseBriefer` — generator with a single :meth:`generate`
  classmethod for use from the CLI.

Performance budget: the briefer is metadata-only and must complete in
under two seconds on a typical repo.  We deliberately avoid full-tree
walks — only the project root, its top-level children, and a small set
of well-known files (CLAUDE.md, README, pyproject.toml, package.json,
Makefile, justfile) are inspected.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# tomllib was added in 3.11.  Guard the import so we degrade gracefully on
# 3.10 (the project pins 3.11+ but the briefer should not crash if used
# from a stripped environment).  When tomllib is unavailable we fall back
# to a small regex-based reader that handles the subset of pyproject.toml
# we care about (top-level [project] table + [project.scripts] entries).
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


def _fallback_parse_pyproject(text: str) -> dict:
    """Tiny regex-based pyproject.toml reader used when tomllib is missing.

    Only extracts what the briefer needs: ``[project]`` keys (name, version,
    requires-python, dependencies), ``[project.scripts]`` mapping, and the
    presence of ``[tool.pytest.ini_options]``.  Anything else is ignored.
    """
    project: dict = {}
    scripts: dict[str, str] = {}
    tool: dict = {}

    # Split by section headings.
    sections: dict[str, list[str]] = {"__root__": []}
    current = "__root__"
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(raw)

    def _parse_value(v: str) -> object:
        v = v.strip()
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        if v.startswith("'") and v.endswith("'"):
            return v[1:-1]
        if v.startswith("["):
            # Naive list parse: split on commas, strip quotes.
            inner = v.strip("[]").strip()
            if not inner:
                return []
            items = [p.strip().strip('"').strip("'") for p in inner.split(",")]
            return [i for i in items if i]
        return v

    if "project" in sections:
        for line in sections["project"]:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, _, v = s.partition("=")
            project[k.strip()] = _parse_value(v)

    if "project.scripts" in sections:
        for line in sections["project.scripts"]:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, _, v = s.partition("=")
            scripts[k.strip()] = str(_parse_value(v))

    # optional-dependencies handled by [project.optional-dependencies.<group>]
    opt: dict[str, list] = {}
    for sec_name, sec_lines in sections.items():
        if not sec_name.startswith("project.optional-dependencies."):
            continue
        group = sec_name.split(".", 2)[2]
        # Could be a table-of-arrays form, e.g.:
        # [project.optional-dependencies]
        # dev = ["pytest"]
        # We try the heading-based form here; the inline form is below.
        items: list[str] = []
        for line in sec_lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            items.append(s.strip(",").strip().strip('"').strip("'"))
        if items:
            opt[group] = items

    # Inline form: [project.optional-dependencies] table with key = [list]
    if "project.optional-dependencies" in sections:
        for line in sections["project.optional-dependencies"]:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            val = _parse_value(v)
            if isinstance(val, list):
                opt[k.strip()] = val

    if opt:
        project["optional-dependencies"] = opt
    if scripts:
        project["scripts"] = scripts

    if "tool.pytest.ini_options" in sections:
        tool["pytest"] = {"ini_options": True}

    return {"project": project, "tool": tool}


def _read_pyproject(path: Path) -> dict:
    """Read pyproject.toml using tomllib when available, fallback otherwise."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            return {}
    try:
        return _fallback_parse_pyproject(text)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directories to skip when listing repo shape.
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "venv",
    ".venv",
    "env",
    ".env",
    "target",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "htmlcov",
    "site-packages",
})

# Frontend signal files (presence in a directory marks it as frontend).
_FRONTEND_SIGNALS: frozenset[str] = frozenset({
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "next.config.js",
    "next.config.ts",
    "nuxt.config.js",
    "nuxt.config.ts",
    "svelte.config.js",
    "angular.json",
    "index.html",
})

# Container signal files.
_CONTAINER_SIGNALS: frozenset[str] = frozenset({
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
})

# Heuristic directory descriptions, applied by exact name match.
_DIR_HINTS: dict[str, str] = {
    "tests": "test suite",
    "test": "test suite",
    "__tests__": "test suite",
    "spec": "test suite",
    "docs": "documentation",
    "doc": "documentation",
    "examples": "example code",
    "scripts": "developer scripts",
    "bin": "executable scripts",
    "agents": "agent definitions",
    "references": "reference docs",
    "templates": "templates",
    "audit-reports": "audit reports",
    "proposals": "design proposals",
    "migrations": "database migrations",
    "alembic": "database migrations",
    "public": "static assets",
    "static": "static assets",
    "assets": "static assets",
    "config": "configuration",
    "configs": "configuration",
}

# Test runner detection: signal file -> (runner, all-cmd, scoped-cmd).
_TEST_RUNNER_SIGNALS: list[tuple[str, str, str, str]] = [
    # (signal_glob, runner_name, all_cmd, scoped_cmd)
    ("pyproject.toml", "pytest", "pytest", "pytest tests/<module>/ -x -v"),
    ("pytest.ini", "pytest", "pytest", "pytest tests/<module>/ -x -v"),
    ("tox.ini", "pytest", "pytest", "pytest tests/<module>/ -x -v"),
    ("vitest.config.ts", "vitest", "vitest run", "vitest run <path>"),
    ("vitest.config.js", "vitest", "vitest run", "vitest run <path>"),
    ("jest.config.js", "jest", "jest", "jest <path>"),
    ("jest.config.ts", "jest", "jest", "jest <path>"),
    ("Cargo.toml", "cargo test", "cargo test", "cargo test <name>"),
    ("go.mod", "go test", "go test ./...", "go test ./<pkg>/..."),
    ("*.csproj", "dotnet test", "dotnet test", "dotnet test --filter <name>"),
    ("*.sln", "dotnet test", "dotnet test", "dotnet test --filter <name>"),
]

# Convention scan keywords: lines containing one of these (whole word) are
# considered "convention" lines worth surfacing.
_CONVENTION_KEYWORDS: tuple[str, ...] = ("MUST", "ALWAYS", "NEVER")
_CONVENTION_RE = re.compile(
    r"\b(" + "|".join(_CONVENTION_KEYWORDS) + r")\b"
)

_MAX_CONVENTION_LINES = 10
_MAX_ENTRY_POINTS = 8
_CLAUDE_MD_SCAN_LIMIT = 100


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CodebaseBrief:
    """Structured codebase briefing.

    Each field is optional — the briefer fills in only what it can detect.
    Use :meth:`to_markdown` for the human-readable rendering and
    :meth:`to_dict` for tooling.
    """

    repo_name: str = ""
    repo_path: str = ""
    language: str | None = None
    frameworks: list[str] = field(default_factory=list)
    test_runner: str | None = None
    test_run_all: str | None = None
    test_run_scoped: str | None = None
    layout: list[tuple[str, str]] = field(default_factory=list)
    entry_points: list[tuple[str, str]] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    git_branch: str | None = None
    git_ahead_of_master: int | None = None
    git_recent_commits: list[str] = field(default_factory=list)
    git_dirty: bool = False
    open_beads: int | None = None
    completed_plans: int | None = None

    # ---------------------------------------------------------------- to_dict

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation.

        Tuples are flattened into ``{"name": ..., "description": ...}``
        objects so consumers don't need to know the dataclass shape.
        """
        d = asdict(self)
        d["layout"] = [
            {"name": name, "description": desc}
            for (name, desc) in self.layout
        ]
        d["entry_points"] = [
            {"name": name, "description": desc}
            for (name, desc) in self.entry_points
        ]
        return d

    # ------------------------------------------------------------ to_markdown

    def to_markdown(self) -> str:
        """Render the brief as a single-page markdown document."""
        lines: list[str] = []
        title = self.repo_name or "(unknown repo)"
        lines.append(f"# Codebase Brief — {title}")
        lines.append("")

        # Stack
        stack_parts: list[str] = []
        if self.language:
            stack_parts.append(self.language)
        for fw in self.frameworks:
            stack_parts.append(fw)
        if self.test_runner:
            stack_parts.append(self.test_runner)
        if stack_parts:
            lines.append("**Stack:** " + " · ".join(stack_parts))
            lines.append("")

        # Layout
        if self.layout:
            lines.append("**Layout:**")
            width = max(len(name) for name, _ in self.layout)
            for name, desc in self.layout:
                lines.append(f"- `{name.ljust(width)}` — {desc}")
            lines.append("")

        # Entry points
        if self.entry_points:
            lines.append("**Entry points:**")
            for name, desc in self.entry_points:
                lines.append(f"- `{name}` — {desc}")
            lines.append("")

        # Conventions
        if self.conventions:
            lines.append("**Conventions (from CLAUDE.md):**")
            for line in self.conventions:
                lines.append(f"- {line}")
            lines.append("")

        # Tests
        if self.test_run_all or self.test_run_scoped:
            lines.append("**Tests:**")
            if self.test_run_all:
                lines.append(f"- All:    `{self.test_run_all}`")
            if self.test_run_scoped:
                lines.append(f"- Scoped: `{self.test_run_scoped}`")
            lines.append("")

        # Health
        health_lines: list[str] = []
        if self.git_branch:
            ahead = (
                f" ({self.git_ahead_of_master} ahead of master)"
                if self.git_ahead_of_master
                else ""
            )
            health_lines.append(f"- branch: {self.git_branch}{ahead}")
        if self.open_beads is not None or self.completed_plans is not None:
            beads = self.open_beads if self.open_beads is not None else "?"
            plans = (
                self.completed_plans if self.completed_plans is not None else "?"
            )
            health_lines.append(
                f"- bead store: {beads} open · {plans} completed plans"
            )
        if self.git_branch:
            tree = "dirty" if self.git_dirty else "clean"
            health_lines.append(f"- working tree: {tree}")
        if self.git_recent_commits:
            health_lines.append("- recent commits:")
            for subj in self.git_recent_commits:
                health_lines.append(f"    - {subj}")
        if health_lines:
            lines.append("**Health:**")
            lines.extend(health_lines)
            lines.append("")

        # Drop trailing blank line
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class CodebaseBriefer:
    """Generates a :class:`CodebaseBrief` for a given repository."""

    @classmethod
    def generate(cls, repo_root: Path) -> CodebaseBrief:
        """Inspect *repo_root* and return a populated brief.

        The implementation deliberately avoids full-tree walks; it relies
        on metadata files (pyproject.toml, package.json, Cargo.toml, etc.)
        and a single ``os.scandir`` call on the project root.
        """
        repo_root = repo_root.resolve()
        brief = CodebaseBrief(
            repo_name=repo_root.name,
            repo_path=str(repo_root),
        )

        cls._fill_stack(brief, repo_root)
        cls._fill_layout(brief, repo_root)
        cls._fill_entry_points(brief, repo_root)
        cls._fill_conventions(brief, repo_root)
        cls._fill_tests(brief, repo_root)
        cls._fill_health(brief, repo_root)

        return brief

    # ----------------------------------------------------------- stack

    @staticmethod
    def _fill_stack(brief: CodebaseBrief, root: Path) -> None:
        """Detect language + frameworks using existing PACKAGE/FRAMEWORK signals.

        We import the router's signal tables to stay aligned with the
        router's view of the world but we run our own light-weight scan
        (no agent registry needed).
        """
        try:
            from agent_baton.core.orchestration.router import (
                FRAMEWORK_SIGNALS,
                PACKAGE_SIGNALS,
            )
        except Exception:  # pragma: no cover - defensive
            FRAMEWORK_SIGNALS = {}
            PACKAGE_SIGNALS = {}

        language: str | None = None
        frameworks: list[str] = []

        # Pass 1 — package signals at root.  We pick the strongest signal
        # available because many polyglot repos (e.g. a Python backend with
        # a TypeScript frontend tucked alongside it) carry several manifest
        # files at the root.  Order = priority.
        _LANG_PRIORITY = (
            "python",
            "rust",
            "go",
            "java",
            "kotlin",
            "ruby",
            "csharp",
            "typescript",
            "javascript",
        )
        root_langs: set[str] = set()
        for filename, (lang, _) in PACKAGE_SIGNALS.items():
            if (root / filename).exists():
                root_langs.add(lang)
        for lang in _LANG_PRIORITY:
            if lang in root_langs:
                language = lang
                break

        # Pass 2 — framework signals across root + visible top-level dirs.
        scan_dirs: list[Path] = [root]
        try:
            for child in os.scandir(root):
                if child.is_dir() and not child.name.startswith("."):
                    if child.name in _SKIP_DIRS:
                        continue
                    scan_dirs.append(Path(child.path))
        except OSError:
            pass

        for scan_dir in scan_dirs:
            for filename, (lang, fw) in FRAMEWORK_SIGNALS.items():
                if (scan_dir / filename).exists():
                    if language is None:
                        language = lang
                    if fw and fw not in frameworks:
                        frameworks.append(fw)

        # Vite + React heuristic (mirrors router behaviour).
        for scan_dir in scan_dirs:
            vite = next(
                (
                    scan_dir / n
                    for n in (
                        "vite.config.ts",
                        "vite.config.js",
                        "vite.config.mjs",
                    )
                    if (scan_dir / n).exists()
                ),
                None,
            )
            if vite is None:
                continue
            pkg = scan_dir / "package.json"
            if pkg.exists():
                try:
                    data = json.loads(pkg.read_text(encoding="utf-8"))
                    deps = {
                        **data.get("dependencies", {}),
                        **data.get("devDependencies", {}),
                    }
                    if "react" in deps and "react" not in frameworks:
                        frameworks.append("react")
                    if "vue" in deps and "vue" not in frameworks:
                        frameworks.append("vue")
                except Exception:
                    pass

        # Python framework hints from pyproject.toml dependencies.
        py_proj = root / "pyproject.toml"
        if language == "python" and py_proj.exists():
            data = _read_pyproject(py_proj)
            project = data.get("project", {}) if isinstance(data, dict) else {}
            deps_field = project.get("dependencies", []) or []
            optional_deps = project.get("optional-dependencies", {}) or {}
            all_deps: list[str] = list(deps_field)
            if isinstance(optional_deps, dict):
                for v in optional_deps.values():
                    if isinstance(v, list):
                        all_deps.extend(v)
            joined = " ".join(all_deps).lower()
            for fw_key, fw_label in (
                ("fastapi", "fastapi"),
                ("django", "django"),
                ("flask", "flask"),
                ("starlette", "starlette"),
            ):
                if fw_key in joined and fw_label not in frameworks:
                    frameworks.append(fw_label)

        # Add Python version when we can read it.
        display_lang = language
        if language == "python":
            py_version = CodebaseBriefer._detect_python_version(root)
            if py_version:
                display_lang = f"python {py_version}"

        brief.language = display_lang
        brief.frameworks = frameworks

    @staticmethod
    def _detect_python_version(root: Path) -> str | None:
        """Pull the requires-python lower bound from pyproject.toml."""
        py_proj = root / "pyproject.toml"
        if not py_proj.exists():
            return None
        data = _read_pyproject(py_proj)
        requires = (
            data.get("project", {}).get("requires-python")
            if isinstance(data, dict)
            else None
        )
        if not isinstance(requires, str):
            return None
        m = re.search(r"(\d+\.\d+)", requires)
        return m.group(1) if m else None

    # ---------------------------------------------------------- layout

    @classmethod
    def _fill_layout(cls, brief: CodebaseBrief, root: Path) -> None:
        """List top-level directories with a one-line description."""
        entries: list[tuple[str, str]] = []
        try:
            children = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            return

        for child in children:
            name = child.name
            if name.startswith("."):
                continue
            if name in _SKIP_DIRS:
                continue
            if not child.is_dir():
                continue
            child_path = Path(child.path)
            desc = cls._describe_dir(name, child_path)
            entries.append((name, desc))

        brief.layout = entries

    @staticmethod
    def _describe_dir(name: str, path: Path) -> str:
        """Heuristically describe a top-level directory."""
        # Hardcoded hints first.
        if name in _DIR_HINTS:
            return _DIR_HINTS[name]

        # Frontend signals.
        try:
            child_names = {p.name for p in path.iterdir()}
        except OSError:
            child_names = set()
        if child_names & _FRONTEND_SIGNALS:
            return "frontend"
        if child_names & _CONTAINER_SIGNALS:
            return "containerised"

        # Python package directory (has __init__.py).
        if (path / "__init__.py").exists():
            return "Python package"

        # Cargo crate.
        if (path / "Cargo.toml").exists():
            return "Rust crate"

        # Go module.
        if (path / "go.mod").exists():
            return "Go module"

        # Node package.
        if (path / "package.json").exists():
            return "Node package"

        # README first non-empty line.
        for readme in ("README.md", "README.rst", "README.txt", "README"):
            readme_path = path / readme
            if readme_path.exists():
                first = CodebaseBriefer._first_meaningful_line(readme_path)
                if first:
                    return first

        return "directory"

    @staticmethod
    def _first_meaningful_line(path: Path, max_len: int = 80) -> str:
        """Return the first non-empty, non-heading-marker line of *path*."""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    # Strip leading markdown heading hashes.
                    cleaned = line.lstrip("#").strip()
                    if cleaned:
                        if len(cleaned) > max_len:
                            cleaned = cleaned[: max_len - 1] + "…"
                        return cleaned
        except OSError:
            return ""
        return ""

    # ----------------------------------------------------- entry points

    @classmethod
    def _fill_entry_points(cls, brief: CodebaseBrief, root: Path) -> None:
        """Detect Python console_scripts, Node bin/main, and Make/just targets."""
        eps: list[tuple[str, str]] = []

        # ---- Python pyproject.toml [project.scripts]
        py_proj = root / "pyproject.toml"
        if py_proj.exists():
            data = _read_pyproject(py_proj)
            scripts = (
                data.get("project", {}).get("scripts", {})
                if isinstance(data, dict)
                else {}
            )
            if isinstance(scripts, dict):
                for cmd, target in scripts.items():
                    eps.append((cmd, f"console script ({target})"))

        # ---- Node package.json bin / main / scripts.start
        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            bin_field = data.get("bin")
            if isinstance(bin_field, dict):
                for cmd, target in bin_field.items():
                    eps.append((cmd, f"node bin ({target})"))
            elif isinstance(bin_field, str):
                name = data.get("name", "node-bin")
                eps.append((name, f"node bin ({bin_field})"))
            main = data.get("main")
            if isinstance(main, str):
                eps.append((f"node {main}", "main entry"))
            scripts = data.get("scripts", {})
            if isinstance(scripts, dict) and "start" in scripts:
                eps.append(("npm start", str(scripts["start"])))

        # ---- Makefile targets
        makefile = root / "Makefile"
        if makefile.exists():
            for tgt in cls._parse_make_targets(makefile)[:4]:
                eps.append((f"make {tgt}", "Makefile target"))

        # ---- justfile targets
        for justfile_name in ("justfile", "Justfile", ".justfile"):
            justfile = root / justfile_name
            if justfile.exists():
                for tgt in cls._parse_make_targets(justfile)[:4]:
                    eps.append((f"just {tgt}", "justfile recipe"))
                break

        # Cap at _MAX_ENTRY_POINTS, preserving insertion order.
        brief.entry_points = eps[:_MAX_ENTRY_POINTS]

    @staticmethod
    def _parse_make_targets(path: Path) -> list[str]:
        """Return a list of (best-effort) target names from Make/just files.

        Lines of the form ``name:`` or ``name: deps`` at column zero are
        considered targets.  Lines starting with ``.`` (special targets) and
        pattern rules (containing ``%``) are skipped.
        """
        targets: list[str] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    if not raw or raw[0] in (" ", "\t", "#", "\n"):
                        continue
                    if ":" not in raw:
                        continue
                    name = raw.split(":", 1)[0].strip()
                    if not name or name.startswith(".") or "%" in name:
                        continue
                    if "=" in name:  # Variable assignment, not a target.
                        continue
                    if not re.match(r"^[A-Za-z0-9_\-]+$", name):
                        continue
                    if name not in targets:
                        targets.append(name)
        except OSError:
            return []
        return targets

    # ---------------------------------------------------- conventions

    @staticmethod
    def _fill_conventions(brief: CodebaseBrief, root: Path) -> None:
        """Pull MUST/ALWAYS/NEVER lines from the top of CLAUDE.md."""
        claude = root / "CLAUDE.md"
        if not claude.exists():
            return
        found: list[str] = []
        try:
            with claude.open("r", encoding="utf-8", errors="replace") as fh:
                for idx, raw in enumerate(fh):
                    if idx >= _CLAUDE_MD_SCAN_LIMIT:
                        break
                    line = raw.strip()
                    if not line:
                        continue
                    if not _CONVENTION_RE.search(line):
                        continue
                    # Strip leading markdown bullet/heading marks.
                    cleaned = line.lstrip("-*# ").strip()
                    # Strip surrounding bold markers for cleaner output.
                    cleaned = cleaned.replace("**", "")
                    if cleaned and cleaned not in found:
                        found.append(cleaned)
                        if len(found) >= _MAX_CONVENTION_LINES:
                            break
        except OSError:
            return
        brief.conventions = found

    # --------------------------------------------------------- tests

    @staticmethod
    def _fill_tests(brief: CodebaseBrief, root: Path) -> None:
        """Detect the test runner and stash its conventional invocation."""
        for signal, runner, run_all, run_scoped in _TEST_RUNNER_SIGNALS:
            if "*" in signal:
                if any(root.glob(signal)):
                    brief.test_runner = runner
                    brief.test_run_all = run_all
                    brief.test_run_scoped = run_scoped
                    return
            else:
                if (root / signal).exists():
                    # For pyproject.toml, only claim pytest if dev deps look
                    # like they include it (otherwise just leave runner unset
                    # to avoid lying).
                    if signal == "pyproject.toml":
                        if not CodebaseBriefer._pyproject_has_pytest(root):
                            continue
                    brief.test_runner = runner
                    brief.test_run_all = run_all
                    brief.test_run_scoped = run_scoped
                    return

    @staticmethod
    def _pyproject_has_pytest(root: Path) -> bool:
        """True when pyproject.toml mentions pytest as a dependency or config."""
        py_proj = root / "pyproject.toml"
        if not py_proj.exists():
            return False
        data = _read_pyproject(py_proj)
        if not isinstance(data, dict):
            # Last-ditch substring scan.
            try:
                return "pytest" in py_proj.read_text(encoding="utf-8").lower()
            except OSError:
                return False
        # tool.pytest.ini_options is a strong signal.
        if "pytest" in data.get("tool", {}):
            return True
        project = data.get("project", {})
        deps = list(project.get("dependencies", []) or [])
        opt = project.get("optional-dependencies", {}) or {}
        if isinstance(opt, dict):
            for v in opt.values():
                if isinstance(v, list):
                    deps.extend(v)
        if any("pytest" in str(d).lower() for d in deps):
            return True
        # Fallback substring check (covers minimal pyprojects we couldn't parse).
        try:
            return "pytest" in py_proj.read_text(encoding="utf-8").lower()
        except OSError:
            return False

    # -------------------------------------------------------- health

    @staticmethod
    def _fill_health(brief: CodebaseBrief, root: Path) -> None:
        """Populate git/branch/bead-store snapshot."""
        # Branch.
        branch = CodebaseBriefer._git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch:
            brief.git_branch = branch

        # Ahead count vs master/main (whichever exists locally).
        for base in ("master", "main"):
            count = CodebaseBriefer._git(
                root, ["rev-list", "--count", f"{base}..HEAD"]
            )
            if count is not None:
                try:
                    brief.git_ahead_of_master = int(count)
                except ValueError:
                    brief.git_ahead_of_master = None
                break

        # Recent commit subjects.
        log = CodebaseBriefer._git(
            root, ["log", "-3", "--pretty=%s"]
        )
        if log:
            brief.git_recent_commits = [
                ln for ln in log.splitlines() if ln.strip()
            ]

        # Dirty flag.
        status = CodebaseBriefer._git(root, ["status", "--porcelain"])
        if status is not None:
            brief.git_dirty = bool(status.strip())

        # Bead store snapshot (graceful fallback if the DB is missing).
        db_path = root / ".claude" / "team-context" / "baton.db"
        if db_path.exists():
            counts = CodebaseBriefer._bead_counts(db_path)
            if counts is not None:
                brief.open_beads, brief.completed_plans = counts

    @staticmethod
    def _git(root: Path, args: list[str]) -> str | None:
        """Run ``git -C root <args>`` returning stdout (stripped) or None."""
        if not (root / ".git").exists():
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    @staticmethod
    def _bead_counts(db_path: Path) -> tuple[int, int] | None:
        """Return (open_bead_count, completed_plan_count) from baton.db.

        Defensive: any unexpected schema yields ``None``.
        """
        try:
            import sqlite3
        except ImportError:  # pragma: no cover
            return None
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.cursor()
                # Bead counts.
                open_count = 0
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM beads WHERE status = 'open'"
                    )
                    open_count = int(cur.fetchone()[0])
                except sqlite3.Error:
                    open_count = 0
                # Plan counts.
                completed_plans = 0
                # Try the executions table first; tolerate absence.
                for sql in (
                    "SELECT COUNT(*) FROM executions WHERE status = 'complete'",
                    "SELECT COUNT(*) FROM plans WHERE status = 'complete'",
                ):
                    try:
                        cur.execute(sql)
                        completed_plans = int(cur.fetchone()[0])
                        break
                    except sqlite3.Error:
                        continue
                return open_count, completed_plans
            finally:
                conn.close()
        except sqlite3.Error:
            return None


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def render(brief: CodebaseBrief, fmt: str = "markdown") -> str:
    """Render *brief* in the requested format ('markdown' or 'json')."""
    if fmt == "json":
        return json.dumps(brief.to_dict(), indent=2, sort_keys=False) + "\n"
    return brief.to_markdown()


__all__ = ["CodebaseBrief", "CodebaseBriefer", "render"]
