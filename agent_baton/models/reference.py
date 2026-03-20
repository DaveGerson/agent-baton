from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReferenceDocument:
    """A parsed reference document from the references/ directory."""
    name: str
    description: str
    content: str
    source_path: Path | None = None

    @property
    def filename(self) -> str:
        if self.source_path:
            return self.source_path.name
        return f"{self.name}.md"
