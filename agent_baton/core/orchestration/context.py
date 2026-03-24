"""Context manager — shared context, mission log, and codebase profile."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_baton.models.execution import MachinePlan
from agent_baton.models.plan import MissionLogEntry


class ContextManager:
    """Manage the .claude/team-context/ directory and its files.

    Handles reading/writing:
    - plan.md — execution plan
    - context.md — shared project context for agents
    - mission-log.md — timestamped record of agent completions
    - codebase-profile.md — cached codebase research
    """

    def __init__(self, team_context_dir: Path | None = None) -> None:
        self._dir = team_context_dir or Path(".claude/team-context")

    @property
    def dir(self) -> Path:
        return self._dir

    def ensure_dir(self) -> None:
        """Create the team-context directory if it doesn't exist."""
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Plan ───────────────────────────────────────────────

    @property
    def plan_path(self) -> Path:
        return self._dir / "plan.md"

    def write_plan(self, plan: MachinePlan) -> Path:
        """Write an execution plan to disk."""
        self.ensure_dir()
        self.plan_path.write_text(plan.to_markdown(), encoding="utf-8")
        return self.plan_path

    def read_plan(self) -> str | None:
        """Read the execution plan from disk, or None if it doesn't exist."""
        if self.plan_path.exists():
            return self.plan_path.read_text(encoding="utf-8")
        return None

    # ── Shared Context ─────────────────────────────────────

    @property
    def context_path(self) -> Path:
        return self._dir / "context.md"

    def write_context(
        self,
        task: str,
        stack: str = "",
        architecture: str = "",
        conventions: str = "",
        guardrails: str = "",
        agent_assignments: str = "",
        domain_context: str = "",
    ) -> Path:
        """Write the shared context document from structured sections."""
        self.ensure_dir()
        sections = [
            f"# Team Context — {task}",
            "",
            "## Stack",
            stack or "_Not yet researched._",
            "",
            "## Architecture",
            architecture or "_Not yet researched._",
            "",
            "## Conventions",
            conventions or "_Not yet researched._",
            "",
        ]

        if domain_context:
            sections.extend(["## Domain Context", domain_context, ""])

        sections.extend([
            "## Guardrails",
            guardrails or "_Standard Development preset._",
            "",
            "## Agent Assignments",
            agent_assignments or "_See plan.md._",
            "",
        ])

        self.context_path.write_text("\n".join(sections), encoding="utf-8")
        return self.context_path

    def read_context(self) -> str | None:
        """Read the shared context document, or None."""
        if self.context_path.exists():
            return self.context_path.read_text(encoding="utf-8")
        return None

    # ── Mission Log ────────────────────────────────────────

    @property
    def mission_log_path(self) -> Path:
        return self._dir / "mission-log.md"

    def init_mission_log(self, task: str, risk_level: str = "LOW") -> Path:
        """Initialize a new mission log."""
        self.ensure_dir()
        content = "\n".join([
            f"# Mission Log — {task}",
            "",
            f"Started: {datetime.now().isoformat()}",
            f"Risk level: {risk_level}",
            "",
            "---",
            "",
        ])
        self.mission_log_path.write_text(content, encoding="utf-8")
        return self.mission_log_path

    def append_to_mission_log(self, entry: MissionLogEntry) -> None:
        """Append an entry to the mission log."""
        self.ensure_dir()
        text = entry.to_markdown() + "\n---\n\n"

        if self.mission_log_path.exists():
            with self.mission_log_path.open("a", encoding="utf-8") as f:
                f.write(text)
        else:
            # Auto-initialize if the log doesn't exist
            self.init_mission_log("Untitled")
            with self.mission_log_path.open("a", encoding="utf-8") as f:
                f.write(text)

    def read_mission_log(self) -> str | None:
        """Read the mission log, or None."""
        if self.mission_log_path.exists():
            return self.mission_log_path.read_text(encoding="utf-8")
        return None

    # ── Codebase Profile ───────────────────────────────────

    @property
    def profile_path(self) -> Path:
        return self._dir / "codebase-profile.md"

    def write_profile(self, content: str) -> Path:
        """Write the codebase profile cache."""
        self.ensure_dir()
        self.profile_path.write_text(content, encoding="utf-8")
        return self.profile_path

    def read_profile(self) -> str | None:
        """Read the cached codebase profile, or None."""
        if self.profile_path.exists():
            return self.profile_path.read_text(encoding="utf-8")
        return None

    def profile_exists(self) -> bool:
        return self.profile_path.exists()

    # ── Recovery ───────────────────────────────────────────

    def recovery_files_exist(self) -> dict[str, bool]:
        """Check which recovery files exist for session resumption."""
        return {
            "plan": self.plan_path.exists(),
            "context": self.context_path.exists(),
            "mission_log": self.mission_log_path.exists(),
            "profile": self.profile_path.exists(),
        }
