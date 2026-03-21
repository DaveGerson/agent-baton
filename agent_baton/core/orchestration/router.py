"""Agent router — detects project stack and maps to agent flavors."""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from agent_baton.core.orchestration.registry import AgentRegistry


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
    ("javascript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("javascript", None): {"backend-engineer": "node"},
    ("typescript", "react"): {"frontend-engineer": "react", "backend-engineer": "node"},
    ("typescript", None): {"backend-engineer": "node"},
    ("csharp", "dotnet"): {"frontend-engineer": "dotnet"},
}


@dataclass
class StackProfile:
    """Detected project stack information."""
    language: str | None = None
    framework: str | None = None
    detected_files: list[str] = field(default_factory=list)


class AgentRouter:
    """Detect project stack and route to the best agent flavor."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def detect_stack(self, project_root: Path | None = None) -> StackProfile:
        """Scan project files to determine language and framework.

        Args:
            project_root: Directory to scan. Defaults to cwd.

        Returns:
            StackProfile with detected language/framework.
        """
        root = project_root or Path.cwd()
        profile = StackProfile()

        # Check framework signals first (more specific)
        for filename, (lang, framework) in FRAMEWORK_SIGNALS.items():
            if (root / filename).exists():
                profile.language = lang
                profile.framework = framework
                profile.detected_files.append(filename)

        # Check package manager signals
        for filename, (lang, _) in PACKAGE_SIGNALS.items():
            if (root / filename).exists():
                if profile.language is None:
                    profile.language = lang
                profile.detected_files.append(filename)

        # Scan for .csproj / .sln (glob patterns)
        if any(root.glob("*.csproj")) or any(root.glob("*.sln")):
            profile.language = "csharp"
            profile.detected_files.append("*.csproj")

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
