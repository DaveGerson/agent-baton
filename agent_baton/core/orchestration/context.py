"""Context manager — shared context, mission log, and codebase profile.

Supports task-scoped directories for parallel plan execution::

    .claude/team-context/
      executions/<task-id>/       ← task-scoped files
        plan.json
        plan.md
        context.md
        mission-log.md
      shared/                     ← cross-task shared data
        codebase-profile.md
      active-task-id.txt          ← pointer to default task

When ``task_id`` is provided, per-task files (plan, context, mission log)
are written inside the task's execution directory.  Shared files (codebase
profile) remain at the root or ``shared/`` level.

When ``task_id`` is ``None``, the legacy flat layout is used for backward
compatibility.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_baton.models.execution import MachinePlan
from agent_baton.models.plan import MissionLogEntry

_EXECUTIONS_DIR = "executions"
_SHARED_DIR = "shared"


class ContextManager:
    """Manage the .claude/team-context/ directory and its files.

    Handles reading/writing:
    - plan.md / plan.json — execution plan (task-scoped when task_id set)
    - context.md — shared project context for agents (task-scoped)
    - mission-log.md — timestamped record of agent completions (task-scoped)
    - codebase-profile.md — cached codebase research (shared across tasks)

    Args:
        team_context_dir: Root of the team-context directory.
        task_id: When set, per-task files are written to
            ``executions/<task_id>/`` instead of the root.
    """

    def __init__(
        self,
        team_context_dir: Path | None = None,
        task_id: str | None = None,
    ) -> None:
        self._root = (team_context_dir or Path(".claude/team-context")).resolve()
        self._task_id = task_id
        if task_id:
            self._task_dir = self._root / _EXECUTIONS_DIR / task_id
        else:
            self._task_dir = self._root  # legacy flat layout

    @property
    def dir(self) -> Path:
        """Root team-context directory."""
        return self._root

    @property
    def task_dir(self) -> Path:
        """Task-scoped directory (same as root if no task_id)."""
        return self._task_dir

    @property
    def task_id(self) -> str | None:
        return self._task_id

    def ensure_dir(self) -> None:
        """Create the task directory if it doesn't exist."""
        self._task_dir.mkdir(parents=True, exist_ok=True)

    # ── Plan ───────────────────────────────────────────────

    @property
    def plan_path(self) -> Path:
        return self._task_dir / "plan.md"

    @property
    def plan_json_path(self) -> Path:
        return self._task_dir / "plan.json"

    def write_plan(self, plan: MachinePlan) -> Path:
        """Write an execution plan to disk (both .md and .json)."""
        import json
        self.ensure_dir()
        self.plan_path.write_text(plan.to_markdown(), encoding="utf-8")
        # Also write JSON for machine consumption
        tmp = self.plan_json_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.rename(self.plan_json_path)
        return self.plan_path

    def read_plan(self) -> str | None:
        """Read the execution plan from disk, or None if it doesn't exist."""
        if self.plan_path.exists():
            return self.plan_path.read_text(encoding="utf-8")
        return None

    # ── Shared Context ─────────────────────────────────────

    @property
    def context_path(self) -> Path:
        return self._task_dir / "context.md"

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
        return self._task_dir / "mission-log.md"

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
            self.init_mission_log("Untitled")
            with self.mission_log_path.open("a", encoding="utf-8") as f:
                f.write(text)

    def read_mission_log(self) -> str | None:
        """Read the mission log, or None."""
        if self.mission_log_path.exists():
            return self.mission_log_path.read_text(encoding="utf-8")
        return None

    # ── Codebase Profile (shared across tasks) ─────────────

    @property
    def profile_path(self) -> Path:
        # Profile is project-level, not task-level — lives at root
        return self._root / "codebase-profile.md"

    def write_profile(self, content: str) -> Path:
        """Write the codebase profile cache."""
        self._root.mkdir(parents=True, exist_ok=True)
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

    # ── Discovery ──────────────────────────────────────────

    @staticmethod
    def list_task_ids(context_root: Path) -> list[str]:
        """List all task IDs that have execution directories."""
        exec_dir = context_root / _EXECUTIONS_DIR
        if not exec_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in exec_dir.iterdir()
            if child.is_dir()
        )
