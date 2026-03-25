"""Reference document model — supplementary docs injected into agent prompts.

Reference documents live in the ``references/`` directory and provide
domain knowledge, coding standards, or procedural guidance that agents
receive as additional context during dispatch.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReferenceDocument:
    """A reference document loaded from the ``references/`` directory.

    References are distinct from knowledge packs — they are always
    available to the orchestrator and can be attached to any agent
    dispatch.  They are typically short, high-signal documents like
    API contracts, style guides, or runbooks.

    Attributes:
        name: Identifier derived from the file's YAML frontmatter.
        description: Short summary of what the document covers.
        content: Full markdown body of the document.
        source_path: Filesystem path the document was loaded from.
    """

    name: str
    description: str
    content: str
    source_path: Path | None = None

    @property
    def filename(self) -> str:
        if self.source_path:
            return self.source_path.name
        return f"{self.name}.md"
