from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.models.enums import AgentCategory


@dataclass
class AgentDefinition:
    """Represents a parsed agent definition from a markdown file."""
    name: str
    description: str
    model: str = "sonnet"
    permission_mode: str = "default"
    color: str | None = None
    tools: list[str] = field(default_factory=list)
    instructions: str = ""  # The markdown body after frontmatter
    source_path: Path | None = None

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
        """Categorize the agent based on its name."""
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
