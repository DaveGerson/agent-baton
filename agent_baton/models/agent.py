"""Agent definition model — the in-memory representation of an agent.

Each ``.md`` file under ``agents/`` (or ``.claude/agents/``) is parsed
into an ``AgentDefinition`` at startup.  The registry, router, and
planner all consume this model to discover, select, and configure agents.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.models.enums import AgentCategory


@dataclass
class AgentDefinition:
    """Parsed agent definition loaded from a markdown file.

    Agent definitions are the fundamental building block of the roster.
    They carry the agent's identity, model preference, permission scope,
    and the full instruction prompt that will be injected when the
    agent is dispatched by the execution engine.

    Attributes:
        name: Canonical agent name, optionally including a flavor suffix
            separated by ``--`` (e.g. ``backend-engineer--python``).
        description: Short human-readable summary of the agent's role.
        model: LLM model to use (e.g. ``"sonnet"``, ``"opus"``).
        permission_mode: Claude Code permission mode for the agent session.
        color: Optional ANSI color for CLI output.
        tools: List of MCP tool names the agent is allowed to use.
        instructions: Full markdown body from the agent definition file,
            injected as the system prompt during dispatch.
        source_path: Filesystem path of the ``.md`` file this was parsed
            from, or ``None`` if created programmatically.
        knowledge_packs: Names of knowledge packs that should be attached
            to every dispatch of this agent (declared in frontmatter).
    """

    name: str
    description: str
    model: str = "sonnet"
    permission_mode: str = "default"
    color: str | None = None
    tools: list[str] = field(default_factory=list)
    instructions: str = ""  # The markdown body after frontmatter
    source_path: Path | None = None
    knowledge_packs: list[str] = field(default_factory=list)  # baseline pack names

    @property
    def base_name(self) -> str:
        """Return the base agent name without flavor suffix.
        e.g., 'backend-engineer--python' -> 'backend-engineer'
        """
        return self.name.split("--")[0] if "--" in self.name else self.name

    @property
    def flavor(self) -> str | None:
        """Return the flavor suffix, or None if this is a base agent.
        e.g., 'backend-engineer--python' -> 'python'
        """
        parts = self.name.split("--")
        return parts[1] if len(parts) > 1 else None

    @property
    def is_flavored(self) -> bool:
        return "--" in self.name

    @property
    def category(self) -> AgentCategory:
        """Classify the agent into a functional category based on its base name.

        Returns:
            The matching ``AgentCategory``, defaulting to ``ENGINEERING``
            for unrecognized agent names.
        """
        engineering = {"architect", "backend-engineer", "frontend-engineer",
                       "devops-engineer", "test-engineer", "data-engineer"}
        data = {"data-scientist", "data-analyst", "visualization-expert"}
        domain = {"subject-matter-expert"}
        review = {"security-reviewer", "code-reviewer", "auditor"}
        meta = {"talent-builder", "orchestrator"}

        base = self.base_name
        if base in engineering:
            return AgentCategory.ENGINEERING
        elif base in data:
            return AgentCategory.DATA
        elif base in domain:
            return AgentCategory.DOMAIN
        elif base in review:
            return AgentCategory.REVIEW
        elif base in meta:
            return AgentCategory.META
        return AgentCategory.ENGINEERING  # default
