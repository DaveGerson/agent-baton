"""Agent router — detects project stack and maps to agent flavors."""
from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass, field

from agent_baton.core.orchestration.registry import AgentRegistry

logger = logging.getLogger(__name__)


# Directories to skip when scanning subdirectories for stack signals.
_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".git",
})


# Reviewer-class agents — quality/governance gate roles that MUST NEVER appear
# as ``implementer`` team members in an ``implement``-type phase.  Their job is
# to review/audit the work, not co-author it.  Including them as implementers:
#   - Burns opus tokens (these agents typically run on opus) producing
#     implementation code they shouldn't write.
#   - Confuses the role taxonomy — an auditor's verdict in compliance reports
#     becomes meaningless if it co-authored the work.
# The planner filters these out when expanding team-step membership for
# implement-type phases (and warns when a ``--agents`` override tries to
# include them).  They remain valid for review/gate phases.
REVIEWER_AGENTS: frozenset[str] = frozenset({
    "auditor",
    "code-reviewer",
    "security-reviewer",
    "plan-reviewer",
    "spec-document-reviewer",
})


def is_reviewer_agent(agent_name: str) -> bool:
    """Return True if ``agent_name`` is a reviewer-class agent.

    Strips the ``--<flavor>`` suffix before lookup so flavored variants like
    ``code-reviewer--python`` are matched against the base name.
    """
    base = agent_name.split("--")[0]
    return base in REVIEWER_AGENTS

# Stack detection signals: filename → (language, framework_hint)
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

# Map (language, framework) → agent flavor suffix
FLAVOR_MAP: dict[tuple[str, str | None], dict[str, str]] = {
    ("python", None): {"backend-engineer": "python"},
    ("python", "django"): {"backend-engineer": "python"},
    ("python", "fastapi"): {"backend-engineer": "python"},
    # Mixed stack: Python backend with React frontend subfolder
    ("python", "react"): {"backend-engineer": "python", "frontend-engineer": "react"},
    ("javascript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("javascript", None): {"backend-engineer": "node"},
    ("typescript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("typescript", None): {"backend-engineer": "node"},
    ("csharp", "dotnet"): {"frontend-engineer": "dotnet"},
}


@dataclass
class StackProfile:
    """Detected project stack information.

    ``language`` is the *primary* language used for agent routing and gate
    command selection.  ``languages`` contains every language detected
    across root and subdirectories (in detection order, deduplicated)
    so callers (e.g. the planner) can emit gates for multi-stack repos.
    ``frameworks`` mirrors ``languages``: every framework hint surfaced
    by FRAMEWORK_SIGNALS, regardless of where it lives.
    """
    language: str | None = None
    framework: str | None = None
    detected_files: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


class AgentRouter:
    """Detect project stack and route to the best agent flavor."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def detect_stack(self, project_root: Path | None = None) -> StackProfile:
        """Scan project files to determine language and framework.

        Scans up to two levels of subdirectories (root + visible children +
        visible grandchildren), skipping hidden directories and common
        build/cache directories.

        Args:
            project_root: Directory to scan. Defaults to cwd.

        Returns:
            StackProfile with detected language/framework.
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

        # Check framework signals first (more specific).
        #
        # Priority rules (parallel to PACKAGE_SIGNALS / bd-5a7c, fixes bd-75e8):
        # 1. Gather ALL framework matches across scan_dirs; record every
        #    (language, framework) pair into ``profile.languages`` and
        #    ``profile.frameworks`` so multi-stack repos (Python backend +
        #    Next.js frontend) don't lose information.
        # 2. Root-level framework signals win for ``profile.language`` /
        #    ``profile.framework``.  A subdir-level framework match (e.g.
        #    ``pmo-ui/next.config.js``) MUST NOT clobber a root signal
        #    (e.g. ``manage.py`` at root).
        # 3. When no root framework signal exists, fall back to the first
        #    subdir match (insertion order of FRAMEWORK_SIGNALS, then
        #    scan_dir order).
        def _add_lang(lang: str) -> None:
            if lang not in profile.languages:
                profile.languages.append(lang)

        def _add_framework(fw: str) -> None:
            if fw not in profile.frameworks:
                profile.frameworks.append(fw)

        root_framework_lang: str | None = None
        root_framework: str | None = None
        subdir_framework_lang: str | None = None
        subdir_framework: str | None = None
        for filename, (lang, framework) in FRAMEWORK_SIGNALS.items():
            for scan_dir in scan_dirs:
                candidate = scan_dir / filename
                if not candidate.exists():
                    continue
                _add_lang(lang)
                _add_framework(framework)
                rel = str(candidate.relative_to(root))
                if rel not in profile.detected_files:
                    profile.detected_files.append(rel)
                if scan_dir == root:
                    if root_framework_lang is None:
                        root_framework_lang = lang
                        root_framework = framework
                elif subdir_framework_lang is None:
                    subdir_framework_lang = lang
                    subdir_framework = framework

        if root_framework_lang is not None:
            # Root-level framework signal — authoritative for primary language.
            profile.language = root_framework_lang
            profile.framework = root_framework
        # Note: a subdir-only framework signal is NOT applied here.  We let
        # the PACKAGE_SIGNALS pass below establish the primary language
        # first (so a Python root beats a Next.js subdir), then fall back
        # to ``subdir_framework_lang`` only if no package signal claimed
        # the language.

        # Check package manager signals.
        #
        # Priority rules (per bd-5a7c):
        # 1. Root-level signals take priority over subdirectory signals.
        #    If the project root contains pyproject.toml, the primary
        #    language is Python — even when ``pmo-ui/package.json`` lives
        #    in a subdirectory.
        # 2. When BOTH pyproject.toml and a Node/TS signal sit at the root
        #    (rare — e.g. a typed-stub tsconfig.json next to a Python
        #    project), prefer Python.  The common monorepo pattern is
        #    Python backend + JS frontend, not the reverse.
        # 3. Within Node/TS root signals, allow ``typescript`` to override
        #    ``javascript`` (tsconfig.json is more specific than package.json).
        # 4. When no root signal exists, fall back to scanning subdirectories.
        # 5. Always populate ``languages`` with every distinct language
        #    seen (root + subdirs) in detection order.
        def _add_lang(lang: str) -> None:
            if lang not in profile.languages:
                profile.languages.append(lang)

        # First pass: collect *all* root-level package signals.
        root_python_seen = False
        root_node_lang: str | None = None  # tracks javascript -> typescript override
        for filename, (lang, _) in PACKAGE_SIGNALS.items():
            if (root / filename).exists():
                _add_lang(lang)
                if filename not in profile.detected_files:
                    profile.detected_files.append(filename)
                if lang == "python":
                    root_python_seen = True
                elif lang in ("javascript", "typescript"):
                    if (
                        root_node_lang is None
                        or (lang == "typescript" and root_node_lang == "javascript")
                    ):
                        root_node_lang = lang

        # Resolve the primary root language using the rules above.
        root_language: str | None = None
        if root_python_seen:
            # Rule 2: Python wins at the root, even alongside Node/TS signals.
            root_language = "python"
        elif root_node_lang is not None:
            root_language = root_node_lang
        else:
            # No python/node root signal — fall back to first non-node match
            # (go, rust, ruby, java, kotlin, etc.).
            for filename, (lang, _) in PACKAGE_SIGNALS.items():
                if (root / filename).exists():
                    root_language = lang
                    break

        if root_language is not None:
            # Root signal found — use it as the primary language.
            # (Don't overwrite a framework-driven language like ``django``.)
            if profile.language is None:
                profile.language = root_language
        else:
            # No root signal — fall back to subdirectory scan.
            for filename, (lang, _) in PACKAGE_SIGNALS.items():
                for scan_dir in scan_dirs:
                    if scan_dir == root:
                        continue
                    if (scan_dir / filename).exists():
                        _add_lang(lang)
                        if (
                            profile.language is None
                            or (lang == "typescript" and profile.language == "javascript")
                        ):
                            profile.language = lang
                        rel = str((scan_dir / filename).relative_to(root))
                        if rel not in profile.detected_files:
                            profile.detected_files.append(rel)
                        break

        # Rule 5 (cont.): even when a root language was chosen, scan
        # subdirectories so ``languages`` reflects the full multi-stack
        # picture (e.g. Python at root + Node in pmo-ui/).
        if root_language is not None:
            for filename, (lang, _) in PACKAGE_SIGNALS.items():
                for scan_dir in scan_dirs:
                    if scan_dir == root:
                        continue
                    if (scan_dir / filename).exists():
                        _add_lang(lang)
                        rel = str((scan_dir / filename).relative_to(root))
                        if rel not in profile.detected_files:
                            profile.detected_files.append(rel)
                        break

        # Subdir framework fallback (bd-75e8): if no package signal or root
        # framework signal claimed the primary language, take the first
        # subdir-level framework match.  This preserves the prior behaviour
        # for repos like ``my-next-app/`` placed inside an empty parent dir,
        # while keeping a Python-root + JS-subdir repo correctly classified
        # as Python.
        if profile.language is None and subdir_framework_lang is not None:
            profile.language = subdir_framework_lang
            profile.framework = subdir_framework
        elif (
            profile.framework is None
            and subdir_framework is not None
            and profile.language == subdir_framework_lang
        ):
            # Same language at root + framework hint in subdir → adopt the
            # framework hint (e.g. JS root + subdir/next.config.js → react).
            profile.framework = subdir_framework

        # Scan for .csproj / .sln (glob patterns).
        #
        # Priority rules (parallel to PACKAGE_SIGNALS / bd-5a7c, fixes bd-fb2d):
        # 1. Surface every csharp signal seen anywhere (vendored sub-repos,
        #    sample projects, etc.) into ``profile.languages`` so multi-stack
        #    repos remain visible to downstream callers.
        # 2. Only let csharp overwrite ``profile.language`` when the .csproj /
        #    .sln lives at the repo ROOT.  A vendored ``vendor/foo.csproj``
        #    inside a Python repo MUST NOT clobber Python as the primary
        #    language.
        csharp_at_root = (
            any(root.glob("*.csproj")) or any(root.glob("*.sln"))
        )
        csharp_anywhere = csharp_at_root
        if not csharp_anywhere:
            for scan_dir in scan_dirs:
                if scan_dir == root:
                    continue
                if any(scan_dir.glob("*.csproj")) or any(scan_dir.glob("*.sln")):
                    csharp_anywhere = True
                    break
        if csharp_anywhere:
            _add_lang("csharp")
            if "*.csproj" not in profile.detected_files:
                profile.detected_files.append("*.csproj")
        if csharp_at_root:
            profile.language = "csharp"

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
                            # Only set language if root-level detection
                            # didn't already establish one (e.g. Python).
                            if profile.language is None:
                                profile.language = "javascript"
                            profile.framework = "react"
                            rel = str(vite_file.relative_to(root))
                            if rel not in profile.detected_files:
                                profile.detected_files.append(rel)
                            break
                    except Exception:
                        logger.debug(
                            "Failed to parse package.json at %s for Vite/React detection — skipping",
                            pkg_json,
                            exc_info=True,
                        )

        logger.debug(
            "Stack detection result: language=%s framework=%s files=%s",
            profile.language,
            profile.framework,
            profile.detected_files,
        )
        return profile

    def route(
        self,
        base_name: str,
        stack: StackProfile | None = None,
        project_root: Path | None = None,
    ) -> str:
        """Determine the best agent name for a role given the project stack.

        Args:
            base_name: Base agent name (e.g., "backend-engineer").
            stack: Pre-detected stack profile, or None to auto-detect.
            project_root: Project root for auto-detection.

        Returns:
            Agent name — either flavored (e.g., "backend-engineer--python")
            or base if no matching flavor exists.
        """
        if stack is None:
            stack = self.detect_stack(project_root)

        # FLAVOR_MAP keys require a non-None language; when no language was
        # detected there is no flavor to apply, so return the base name early.
        if stack.language is None:
            logger.debug("route(%s): no language detected — returning base name", base_name)
            return base_name

        # Look up flavor mapping for this stack
        key = (stack.language, stack.framework)
        flavors = FLAVOR_MAP.get(key, {})
        if not flavors:
            # Try language-only fallback
            key = (stack.language, None)
            flavors = FLAVOR_MAP.get(key, {})

        logger.debug(
            "route(%s): stack=(%s, %s) flavor_map_key=%s flavors=%s",
            base_name,
            stack.language,
            stack.framework,
            key,
            flavors,
        )

        # Check LearnedOverrides for project-specific flavor corrections that
        # should take precedence over the hardcoded FLAVOR_MAP.
        try:
            from agent_baton.core.learn.overrides import LearnedOverrides
            _overrides = LearnedOverrides()
            _flavor_overrides = _overrides.get_flavor_overrides()
            if _flavor_overrides:
                # Build composite stack key the same way the engine does
                _stack_key = (
                    f"{stack.language}/{stack.framework}"
                    if stack.framework
                    else stack.language
                )
                _stack_entry = _flavor_overrides.get(_stack_key, {})
                if base_name in _stack_entry:
                    _learned_flavor = _stack_entry[base_name]
                    _candidate = f"{base_name}--{_learned_flavor}"
                    if self._registry.get(_candidate):
                        logger.debug(
                            "route(%s): applying learned override → %s",
                            base_name,
                            _candidate,
                        )
                        return _candidate
        except Exception:
            logger.warning(
                "Failed to load learned flavor overrides — using default FLAVOR_MAP",
                exc_info=True,
            )

        suggested_flavor = flavors.get(base_name)
        if suggested_flavor:
            candidate = f"{base_name}--{suggested_flavor}"
            # Verify the flavored agent exists in the registry
            if self._registry.get(candidate):
                logger.debug("route(%s): selected flavor → %s", base_name, candidate)
                return candidate
            else:
                logger.debug(
                    "route(%s): flavor '%s' not in registry — falling back to base",
                    base_name,
                    candidate,
                )

        logger.debug("route(%s): no flavor match — returning base name", base_name)
        return base_name

    def route_team(
        self,
        roles: list[str],
        stack: StackProfile | None = None,
        project_root: Path | None = None,
    ) -> dict[str, str]:
        """Route a list of base roles to their best agent names.

        Args:
            roles: List of base agent names.
            stack: Pre-detected stack, or None.
            project_root: For auto-detection.

        Returns:
            Dict mapping base role → resolved agent name.
        """
        if stack is None:
            stack = self.detect_stack(project_root)

        return {role: self.route(role, stack) for role in roles}
