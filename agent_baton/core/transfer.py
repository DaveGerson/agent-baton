"""Cross-project knowledge transfer — copy agents, knowledge, and references between projects."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.core.registry import AgentRegistry
from agent_baton.core.scoring import PerformanceScorer


@dataclass
class TransferManifest:
    """What to transfer between projects."""

    agents: list[str] = field(default_factory=list)          # agent filenames (e.g. "architect.md")
    knowledge_packs: list[str] = field(default_factory=list)  # knowledge dir names (e.g. "agent-baton")
    references: list[str] = field(default_factory=list)       # reference filenames (e.g. "git-strategy.md")
    source_project: str = ""
    reason: str = ""

    def to_markdown(self) -> str:
        """Render the manifest as a human-readable markdown summary."""
        lines: list[str] = [
            "# Transfer Manifest",
            "",
        ]
        if self.source_project:
            lines += [f"**Source project:** `{self.source_project}`", ""]
        if self.reason:
            lines += [f"**Reason:** {self.reason}", ""]

        lines.append(f"## Agents ({len(self.agents)})")
        if self.agents:
            for name in sorted(self.agents):
                lines.append(f"- {name}")
        else:
            lines.append("_(none)_")
        lines.append("")

        lines.append(f"## Knowledge Packs ({len(self.knowledge_packs)})")
        if self.knowledge_packs:
            for name in sorted(self.knowledge_packs):
                lines.append(f"- {name}")
        else:
            lines.append("_(none)_")
        lines.append("")

        lines.append(f"## References ({len(self.references)})")
        if self.references:
            for name in sorted(self.references):
                lines.append(f"- {name}")
        else:
            lines.append("_(none)_")
        lines.append("")

        return "\n".join(lines)


class ProjectTransfer:
    """Transfer agents, knowledge, and references between projects.

    The canonical .claude/ layout used by this class:

        <project_root>/
        └── .claude/
            ├── agents/           *.md
            ├── knowledge/        <pack-name>/*.md
            └── references/       *.md
    """

    def __init__(self, source_root: Path | None = None) -> None:
        self._source = source_root or Path.cwd()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def source_root(self) -> Path:
        return self._source

    def _claude_dir(self, root: Path) -> Path:
        return root / ".claude"

    def _agents_dir(self, root: Path) -> Path:
        return self._claude_dir(root) / "agents"

    def _knowledge_dir(self, root: Path) -> Path:
        return self._claude_dir(root) / "knowledge"

    def _references_dir(self, root: Path) -> Path:
        return self._claude_dir(root) / "references"

    # ------------------------------------------------------------------
    # Discover
    # ------------------------------------------------------------------

    def discover_transferable(self, min_score: float = 0.0) -> TransferManifest:
        """Discover what's available to transfer from this project.

        Lists all project-level agents (from .claude/agents/), knowledge pack
        directory names (from .claude/knowledge/), and reference filenames
        (from .claude/references/).

        Args:
            min_score: If > 0, only include agents whose scorecard
                       first_pass_rate is >= this threshold. Agents with no
                       usage data are included when min_score == 0.

        Returns:
            A TransferManifest describing available items.
        """
        agents_dir = self._agents_dir(self._source)
        knowledge_dir = self._knowledge_dir(self._source)
        refs_dir = self._references_dir(self._source)

        # Collect agent filenames
        agent_names: list[str] = []
        if agents_dir.is_dir():
            candidate_files = sorted(agents_dir.glob("*.md"))
            if min_score > 0.0:
                # Load registry scoped to the project's agents directory
                registry = AgentRegistry()
                registry.load_directory(agents_dir)
                scorer = PerformanceScorer()
                for path in candidate_files:
                    agent_name = path.stem
                    scorecard = scorer.score_agent(agent_name)
                    # Include agents that either have sufficient score OR
                    # have no usage data at all (times_used == 0 means unknown,
                    # not bad — callers that want strict filtering pass min_score > 0
                    # but we only exclude agents with confirmed low scores).
                    if scorecard.times_used == 0 or scorecard.first_pass_rate >= min_score:
                        agent_names.append(path.name)
            else:
                agent_names = [p.name for p in candidate_files]

        # Collect knowledge pack directory names
        knowledge_packs: list[str] = []
        if knowledge_dir.is_dir():
            knowledge_packs = sorted(
                d.name for d in knowledge_dir.iterdir() if d.is_dir()
            )

        # Collect reference filenames
        references: list[str] = []
        if refs_dir.is_dir():
            references = sorted(p.name for p in refs_dir.glob("*.md"))

        return TransferManifest(
            agents=agent_names,
            knowledge_packs=knowledge_packs,
            references=references,
            source_project=str(self._source),
        )

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def export_to(
        self,
        target_root: Path,
        manifest: TransferManifest,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Copy selected items from this project to a target project.

        Copies to:
          - agents    → target_root/.claude/agents/
          - knowledge → target_root/.claude/knowledge/<pack>/
          - references → target_root/.claude/references/

        Skips existing destination files unless force=True.

        Returns:
            Dict with counts: {"agents": N, "knowledge": N, "references": N}
        """
        counts: dict[str, int] = {"agents": 0, "knowledge": 0, "references": 0}

        # Agents
        src_agents = self._agents_dir(self._source)
        dst_agents = self._agents_dir(target_root)
        for filename in manifest.agents:
            src_file = src_agents / filename
            if not src_file.is_file():
                continue
            dst_file = dst_agents / filename
            if self._copy_file(src_file, dst_file, force=force):
                counts["agents"] += 1

        # Knowledge packs
        src_knowledge = self._knowledge_dir(self._source)
        dst_knowledge = self._knowledge_dir(target_root)
        for pack_name in manifest.knowledge_packs:
            src_pack = src_knowledge / pack_name
            if not src_pack.is_dir():
                continue
            dst_pack = dst_knowledge / pack_name
            copied = self._copy_directory(src_pack, dst_pack, force=force)
            counts["knowledge"] += copied

        # References
        src_refs = self._references_dir(self._source)
        dst_refs = self._references_dir(target_root)
        for filename in manifest.references:
            src_file = src_refs / filename
            if not src_file.is_file():
                continue
            dst_file = dst_refs / filename
            if self._copy_file(src_file, dst_file, force=force):
                counts["references"] += 1

        return counts

    def import_from(
        self,
        source_root: Path,
        manifest: TransferManifest,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Import items from another project into this project.

        Inverse of export_to: reads from source_root and writes to self._source.

        Returns:
            Dict with counts: {"agents": N, "knowledge": N, "references": N}
        """
        other = ProjectTransfer(source_root=source_root)
        return other.export_to(self._source, manifest, force=force)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_file(src: Path, dst: Path, *, force: bool) -> bool:
        """Copy src → dst.  Returns True if copied, False if skipped."""
        if dst.exists() and not force:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True

    @staticmethod
    def _copy_directory(src_dir: Path, dst_dir: Path, *, force: bool) -> int:
        """Recursively copy all .md files from src_dir to dst_dir.

        Returns the number of files copied (not skipped).
        """
        copied = 0
        for src_file in sorted(src_dir.rglob("*.md")):
            rel = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel
            if dst_file.exists() and not force:
                continue
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1
        return copied
