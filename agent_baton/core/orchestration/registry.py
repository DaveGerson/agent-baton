"""Agent registry -- loads and queries agent definitions from disk.

The registry is the canonical source of truth for which agents the system
can dispatch.  It reads agent definition files (Markdown with YAML
frontmatter) from two locations, applied in order:

1. **Global** -- ``~/.claude/agents/`` (user-wide defaults).
2. **Project** -- ``.claude/agents/`` (per-project overrides).

Project-level definitions override global ones with the same name, so a
team can customize or extend the default roster without forking it.

Agent names follow the ``<base>--<flavor>`` convention.  The registry
supports flavor-aware lookups: ``find_best_match("backend-engineer",
"python")`` will return ``backend-engineer--python`` if it exists, falling
back to the unflavored ``backend-engineer`` otherwise.

This module is a pure in-memory index -- it does not persist state or
emit events.  It is consumed by :class:`AgentRouter` (for stack-aware
routing) and by the planner (for agent validation).
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_baton.models.agent import AgentDefinition
from agent_baton.models.enums import AgentCategory
from agent_baton.utils.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Load, index, and query agent definitions from markdown files.

    The registry builds an in-memory dictionary keyed by agent name.  It
    searches both project-level (``.claude/agents/``) and global
    (``~/.claude/agents/``) directories, with project-level taking
    precedence on name collisions.

    Lifecycle:
        1. Instantiate the registry.
        2. Call :meth:`load_default_paths` (or :meth:`load_directory` for
           custom locations).
        3. Query with :meth:`get`, :meth:`find_best_match`,
           :meth:`get_flavors`, or :meth:`by_category`.

    Collaborators:
        - :class:`AgentRouter` -- uses this registry to validate that a
          flavored agent actually exists before routing to it.
        - Planner / Executor -- looks up agent definitions to build
          delegation prompts and determine model/permission settings.

    Attributes:
        _agents: Internal dictionary mapping agent name to its
            :class:`AgentDefinition`.  Access via the ``agents`` property
            for a defensive copy.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    @property
    def agents(self) -> dict[str, AgentDefinition]:
        return dict(self._agents)

    @property
    def names(self) -> list[str]:
        return list(self._agents.keys())

    def load_directory(self, directory: Path, *, override: bool = False) -> int:
        """Load all .md agent definitions from a directory.

        Args:
            directory: Path to scan for .md files.
            override: If True, agents from this directory replace existing ones
                      with the same name (used for project-level overrides).

        Returns:
            Number of agents loaded.
        """
        if not directory.is_dir():
            return 0

        count = 0
        for path in sorted(directory.glob("*.md")):
            agent = self._parse_agent_file(path)
            if agent is None:
                continue
            if override or agent.name not in self._agents:
                self._agents[agent.name] = agent
                count += 1
        return count

    def load_default_paths(self) -> int:
        """Load agents from standard locations (global then project override).

        Returns:
            Total number of agents loaded.
        """
        global_dir = Path.home() / ".claude" / "agents"
        project_dir = (Path(".claude") / "agents").resolve()

        count = self.load_directory(global_dir)
        count += self.load_directory(project_dir, override=True)
        return count

    def get(self, name: str) -> AgentDefinition | None:
        """Look up an agent by exact name."""
        return self._agents.get(name)

    def get_flavors(self, base_name: str) -> list[AgentDefinition]:
        """Return all flavored variants of a base agent.

        Example: get_flavors("backend-engineer") returns
        [backend-engineer--node, backend-engineer--python, ...]
        """
        return [
            a for a in self._agents.values()
            if a.base_name == base_name and a.is_flavored
        ]

    def get_base(self, name: str) -> AgentDefinition | None:
        """Return the base (unflavored) agent for a given name.

        Works whether you pass "backend-engineer" or "backend-engineer--python".
        """
        base = name.split("--")[0] if "--" in name else name
        return self._agents.get(base)

    def find_best_match(self, base_name: str, flavor: str | None = None) -> AgentDefinition | None:
        """Find the best agent match: exact flavor > base.

        Args:
            base_name: The base agent name (e.g., "backend-engineer").
            flavor: Optional flavor (e.g., "python").

        Returns:
            The best matching agent, or None.
        """
        if flavor:
            exact = self.get(f"{base_name}--{flavor}")
            if exact:
                return exact
        return self.get(base_name)

    def by_category(self, category: AgentCategory) -> list[AgentDefinition]:
        """Return all agents in a given category."""
        return [a for a in self._agents.values() if a.category == category]

    def _parse_agent_file(self, path: Path) -> AgentDefinition | None:
        """Parse a single agent markdown file into an AgentDefinition.

        The file format is Markdown with optional YAML frontmatter.
        Frontmatter fields recognized: ``name``, ``description``, ``model``,
        ``permissionMode``, ``color``, ``tools`` (comma-separated string or
        list), and ``knowledge_packs`` (comma-separated string or list).

        If ``name`` is absent from the frontmatter, the filename stem is used
        (e.g. ``backend-engineer--python.md`` becomes
        ``backend-engineer--python``).

        Args:
            path: Path to the ``.md`` agent definition file.

        Returns:
            An :class:`AgentDefinition` on success, or ``None`` if the file
            cannot be read or decoded.
        """
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning(
                "Failed to read agent definition file %s — agent will not be available",
                path,
                exc_info=True,
            )
            return None

        metadata, body = parse_frontmatter(content)
        if not metadata.get("name"):
            # Derive name from filename if not in frontmatter
            name = path.stem
        else:
            name = metadata["name"]

        description = metadata.get("description", "")
        if isinstance(description, str):
            description = description.strip()

        tools_raw = metadata.get("tools", "")
        if isinstance(tools_raw, str):
            tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
        elif isinstance(tools_raw, list):
            tools = tools_raw
        else:
            tools = []

        kp_raw = metadata.get("knowledge_packs", [])
        if isinstance(kp_raw, str):
            knowledge_packs = [k.strip() for k in kp_raw.split(",") if k.strip()]
        elif isinstance(kp_raw, list):
            knowledge_packs = [str(k).strip() for k in kp_raw if k]
        else:
            knowledge_packs = []

        return AgentDefinition(
            name=name,
            description=description,
            model=metadata.get("model", ""),
            permission_mode=metadata.get("permissionMode", "default"),
            color=metadata.get("color"),
            tools=tools,
            instructions=body,
            source_path=path,
            knowledge_packs=knowledge_packs,
        )
