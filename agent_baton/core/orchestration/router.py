"""Agent router -- detects project technology stack and maps roles to agent flavors.

The router bridges the gap between the generic role names used in execution
plans (``backend-engineer``, ``frontend-engineer``) and the technology-
specific agent variants that actually know the right idioms for the project
(``backend-engineer--python``, ``frontend-engineer--react``).

Stack detection algorithm:
    1. Build a scan list: the project root, its visible non-hidden child
       directories, and their visible children (two levels deep).  Build
       artifacts (``node_modules``, ``dist``, ``__pycache__``, etc.) are
       skipped.
    2. Check for **framework signals** first (``next.config.js`` implies
       React + JavaScript, ``angular.json`` implies Angular + TypeScript,
       etc.).  These are the most specific and take highest priority.
    3. Check for **package-manager signals** (``pyproject.toml``,
       ``package.json``, ``go.mod``).  Root-level signals override
       subdirectory signals.  Within the same tier, ``typescript`` can
       upgrade ``javascript`` when ``tsconfig.json`` is found.
    4. Check for ``.csproj`` / ``.sln`` files (C# / .NET detection).
    5. As a final heuristic, look for ``vite.config.*`` alongside a
       ``package.json`` that lists ``react`` as a dependency.

Once the stack is known, :data:`FLAVOR_MAP` translates (language, framework)
tuples into agent flavor suffixes.  The router verifies that the flavored
agent actually exists in the :class:`AgentRegistry` before recommending it;
if not, it falls back to the base (unflavored) agent name.

This design ensures that adding a new language or framework only requires
adding entries to the signal dictionaries and flavor map -- no control-flow
changes.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from agent_baton.core.orchestration.registry import AgentRegistry


# Directories to skip when scanning subdirectories for stack signals.
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".git",
})

# Package-manager signals: filename -> (language, framework_hint).
# These are generic language indicators (no framework opinion).  Root-level
# hits take priority over subdirectory hits during detection.
PACKAGE_SIGNALS: dict[str, tuple[str, str | None]] = {
    "package.json": ("javascript", None),
    "tsconfig.json": ("typescript", None),
    "pyproject.toml": ("python", None),
    "requirements.txt": ("python", None),
    "setup.py": ("python", None),
    "go.mod": ("go", None),
    "Cargo.toml": ("rust", None),
    "Gemfile": ("ruby", None),
    "build.gradle": ("java", None),
    "build.gradle.kts": ("kotlin", None),
    "pom.xml": ("java", None),
}

# Framework signals: filename -> (language, framework).
# More specific than package signals.  Checked first during detection so
# that a ``next.config.js`` is recognised as React before a bare
# ``package.json`` is treated as generic JavaScript.
FRAMEWORK_SIGNALS: dict[str, tuple[str, str]] = {
    "next.config.js": ("javascript", "react"),
    "next.config.ts": ("typescript", "react"),
    "next.config.mjs": ("javascript", "react"),
    "nuxt.config.js": ("javascript", "vue"),
    "nuxt.config.ts": ("typescript", "vue"),
    "angular.json": ("typescript", "angular"),
    "svelte.config.js": ("javascript", "svelte"),
    "appsettings.json": ("csharp", "dotnet"),
    "manage.py": ("python", "django"),
    "wsgi.py": ("python", "django"),
}

# Flavor map: (language, framework) -> {base_agent_name: flavor_suffix}.
# This is the translation table that turns a detected stack into agent
# flavor recommendations.  Multiple roles can be mapped in a single entry
# (e.g. React projects get both ``frontend-engineer--react`` and
# ``backend-engineer--node``).
FLAVOR_MAP: dict[tuple[str, str | None], dict[str, str]] = {
    ("python", None): {"backend-engineer": "python"},
    ("python", "django"): {"backend-engineer": "python"},
    ("python", "fastapi"): {"backend-engineer": "python"},
    ("javascript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("javascript", None): {"backend-engineer": "node"},
    ("typescript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("typescript", None): {"backend-engineer": "node"},
    ("csharp", "dotnet"): {"frontend-engineer": "dotnet"},
}


@dataclass
class StackProfile:
    """Detected project technology stack.

    Produced by :meth:`AgentRouter.detect_stack` and consumed by
    :meth:`AgentRouter.route` to select the correct agent flavor.

    Attributes:
        language: Primary programming language detected (e.g. ``"python"``,
            ``"typescript"``, ``"csharp"``).  ``None`` when detection
            found no recognizable signals.
        framework: Framework detected (e.g. ``"react"``, ``"django"``,
            ``"dotnet"``).  ``None`` when only a language-level signal was
            found.
        detected_files: Relative paths of the signal files that contributed
            to the detection.  Useful for debugging routing decisions.
    """

    language: str | None = None
    framework: str | None = None
    detected_files: list[str] = field(default_factory=list)


class AgentRouter:
    """Detect the project technology stack and route roles to agent flavors.

    The router is stateless apart from its reference to the
    :class:`AgentRegistry`.  Each call to :meth:`route` or
    :meth:`route_team` can optionally accept a pre-computed
    :class:`StackProfile` to avoid redundant filesystem scans.

    The routing algorithm:
        1. Determine the project's ``(language, framework)`` tuple via
           :meth:`detect_stack`.
        2. Look up the tuple in :data:`FLAVOR_MAP` to get a mapping of
           base agent names to flavor suffixes.
        3. If a flavor suffix is found for the requested role, verify
           the flavored agent exists in the registry before returning it.
        4. Fall back to the base agent name when no flavor match exists
           or the flavored definition is missing.

    Attributes:
        _registry: The :class:`AgentRegistry` used to validate that
            suggested flavored agents actually have definitions on disk.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def detect_stack(self, project_root: Path | None = None) -> StackProfile:
        """Scan project files to determine the primary language and framework.

        The detection follows a priority chain designed to resolve ambiguity
        in polyglot projects:

        1. **Framework signals** (highest priority) -- config files like
           ``next.config.js`` or ``angular.json`` that imply both a language
           and a framework.  Checked across all scan directories.
        2. **Root package signals** -- package manager files (``pyproject.toml``,
           ``package.json``) found in the project root.  Within this tier,
           ``typescript`` can upgrade ``javascript`` when ``tsconfig.json``
           appears alongside ``package.json``.
        3. **Subdirectory package signals** -- same files found in child
           directories, used only when no root-level signal was found.
        4. **C# / .NET detection** -- ``.csproj`` or ``.sln`` files found
           via glob in any scan directory.
        5. **Vite + React heuristic** (lowest priority) -- ``vite.config.*``
           alongside a ``package.json`` that lists ``react`` as a dependency.
           Only applies when no framework was detected by earlier stages.

        Scanning is limited to two directory levels (root, children,
        grandchildren), skipping hidden directories and common build/cache
        directories listed in ``_SKIP_DIRS``.

        Args:
            project_root: Directory to scan.  Defaults to the current
                working directory.

        Returns:
            A :class:`StackProfile` with the detected language, framework,
            and the relative paths of the signal files that contributed to
            the decision.
        """
        root = project_root or Path.cwd()
        profile = StackProfile()

        # Build the list of directories to scan: root, its visible children,
        # and their visible children (i.e. up to two levels deep).
        scan_dirs: list[Path] = [root]
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            scan_dirs.append(child)
            for grandchild in sorted(child.iterdir()):
                if not grandchild.is_dir():
                    continue
                if grandchild.name.startswith(".") or grandchild.name in _SKIP_DIRS:
                    continue
                scan_dirs.append(grandchild)

        # Check framework signals first (more specific)
        for filename, (lang, framework) in FRAMEWORK_SIGNALS.items():
            for scan_dir in scan_dirs:
                if (scan_dir / filename).exists():
                    profile.language = lang
                    profile.framework = framework
                    rel = str((scan_dir / filename).relative_to(root))
                    if rel not in profile.detected_files:
                        profile.detected_files.append(rel)
                    break

        # Check package manager signals.
        # Root-level signals take priority: if the project root contains a
        # package signal (e.g. pyproject.toml), that defines the primary
        # language.  Subdirectory signals (e.g. pmo-ui/package.json) are
        # secondary and won't override a root-level detection.
        # Within the same priority tier, allow "typescript" to override
        # "javascript" when tsconfig.json is found alongside package.json.
        root_language: str | None = None
        for filename, (lang, _) in PACKAGE_SIGNALS.items():
            if (root / filename).exists():
                if (
                    root_language is None
                    or (lang == "typescript" and root_language == "javascript")
                ):
                    root_language = lang
                rel = filename
                if rel not in profile.detected_files:
                    profile.detected_files.append(rel)

        if root_language is not None:
            # Root signal found — use it as the primary language.
            if profile.language is None:
                profile.language = root_language
        else:
            # No root signal — fall back to subdirectory scan.
            for filename, (lang, _) in PACKAGE_SIGNALS.items():
                for scan_dir in scan_dirs:
                    if (scan_dir / filename).exists():
                        if (
                            profile.language is None
                            or (lang == "typescript" and profile.language == "javascript")
                        ):
                            profile.language = lang
                        rel = str((scan_dir / filename).relative_to(root))
                        if rel not in profile.detected_files:
                            profile.detected_files.append(rel)
                        break

        # Scan for .csproj / .sln (glob patterns across all scan_dirs)
        for scan_dir in scan_dirs:
            if any(scan_dir.glob("*.csproj")) or any(scan_dir.glob("*.sln")):
                profile.language = "csharp"
                if "*.csproj" not in profile.detected_files:
                    profile.detected_files.append("*.csproj")
                break

        # Vite + React: vite.config.ts/js alongside a package.json that
        # lists "react" as a dependency.  Only override when the current
        # profile has no framework set (framework signals take priority).
        if profile.framework is None:
            for scan_dir in scan_dirs:
                vite_file: Path | None = None
                for vite_name in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
                    candidate = scan_dir / vite_name
                    if candidate.exists():
                        vite_file = candidate
                        break
                if vite_file is None:
                    continue
                # Check for a package.json that references "react"
                pkg_json = scan_dir / "package.json"
                if pkg_json.exists():
                    try:
                        import json
                        pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                        deps = {
                            **pkg.get("dependencies", {}),
                            **pkg.get("devDependencies", {}),
                        }
                        if "react" in deps:
                            profile.language = "javascript"
                            profile.framework = "react"
                            rel = str(vite_file.relative_to(root))
                            if rel not in profile.detected_files:
                                profile.detected_files.append(rel)
                            break
                    except Exception:
                        pass

        return profile

    def route(
        self,
        base_name: str,
        stack: StackProfile | None = None,
        project_root: Path | None = None,
    ) -> str:
        """Determine the best agent name for a role given the project stack.

        Resolution order:
            1. Look up ``(language, framework)`` in ``FLAVOR_MAP``.
            2. If no match, try ``(language, None)`` as a language-only
               fallback.
            3. If a flavor suffix is found for *base_name*, verify the
               flavored agent definition exists in the registry.
            4. Return the flavored name if it exists; otherwise return
               *base_name* unchanged.

        Args:
            base_name: Base agent name (e.g. ``"backend-engineer"``).
            stack: Pre-detected stack profile.  When ``None``,
                :meth:`detect_stack` is called automatically.
            project_root: Project root passed to :meth:`detect_stack`
                when *stack* is ``None``.

        Returns:
            The best agent name -- either a flavored variant (e.g.
            ``"backend-engineer--python"``) or the original *base_name*
            when no matching flavor exists in the registry.
        """
        if stack is None:
            stack = self.detect_stack(project_root)

        # Look up flavor mapping for this stack
        key = (stack.language, stack.framework)
        flavors = FLAVOR_MAP.get(key, {})
        if not flavors:
            # Try language-only fallback
            key = (stack.language, None)
            flavors = FLAVOR_MAP.get(key, {})

        suggested_flavor = flavors.get(base_name)
        if suggested_flavor:
            candidate = f"{base_name}--{suggested_flavor}"
            # Verify the flavored agent exists in the registry
            if self._registry.get(candidate):
                return candidate

        return base_name

    def route_team(
        self,
        roles: list[str],
        stack: StackProfile | None = None,
        project_root: Path | None = None,
    ) -> dict[str, str]:
        """Route a list of base roles to their best agent names.

        Convenience wrapper around :meth:`route` that detects the stack
        once and reuses it for all roles, avoiding repeated filesystem
        scans when routing an entire team.

        Args:
            roles: List of base agent names (e.g.
                ``["backend-engineer", "frontend-engineer"]``).
            stack: Pre-detected stack profile, or ``None`` to
                auto-detect once.
            project_root: Passed to :meth:`detect_stack` when *stack*
                is ``None``.

        Returns:
            Dictionary mapping each base role to its resolved agent name.
            Values may be flavored (``"backend-engineer--python"``) or
            unchanged (``"test-engineer"``) depending on the stack.
        """
        if stack is None:
            stack = self.detect_stack(project_root)

        return {role: self.route(role, stack) for role in roles}
