"""Context manager -- shared context, mission log, and codebase profile.

This module manages the ``team-context/`` directory tree that serves as the
shared filesystem interface between the orchestrator and dispatched agents.
Each agent reads the context document to understand the project environment,
and the orchestrator writes mission log entries after each agent completes.

Directory layout with task-scoped isolation for parallel execution::

    .claude/team-context/
      executions/<task-id>/       <- task-scoped files (one per concurrent plan)
        plan.json                    machine-readable execution plan
        plan.md                      human-readable plan rendering
        context.md                   stack/architecture/conventions snapshot
        mission-log.md               timestamped agent completion log
      shared/                     <- cross-task shared data
        codebase-profile.md          cached codebase research
      active-task-id.txt          <- pointer to the default task

When ``task_id`` is provided, per-task files (plan, context, mission log)
are written inside the task's execution directory.  Shared files (codebase
profile) remain at the root level.

When ``task_id`` is ``None``, the legacy flat layout is used for backward
compatibility -- all files are written directly to the ``team-context/``
root.  This mode is preserved so that older plans and single-task
workflows continue to function without migration.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_baton.models.execution import MachinePlan
from agent_baton.models.plan import MissionLogEntry

_EXECUTIONS_DIR = "executions"
_SHARED_DIR = "shared"


class ContextManager:
    """Manage the ``.claude/team-context/`` directory and its files.

    This is the filesystem interface that the execution engine uses to share
    state between the orchestrator and dispatched agents.  Each file type
    serves a distinct purpose:

    - **plan.md / plan.json** -- the execution plan in human-readable and
      machine-readable forms.  Task-scoped.
    - **context.md** -- a snapshot of the project's stack, architecture,
      conventions, guardrails, and agent assignments.  Read by every
      dispatched agent to stay aligned.  Task-scoped.
    - **mission-log.md** -- a timestamped, append-only record of agent
      completions and outcomes.  Used for traceability and session
      recovery.  Task-scoped.
    - **codebase-profile.md** -- cached research about the codebase
      (structure, key patterns, conventions).  Shared across tasks because
      it describes the project rather than a specific execution.

    Attributes:
        _root: Resolved root directory of the ``team-context/`` tree.
        _task_id: Optional task identifier for directory scoping.
        _task_dir: Resolved directory for task-scoped files.  Equal to
            ``_root`` in legacy (no task_id) mode, or
            ``_root/executions/<task_id>/`` in scoped mode.
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
        """Write an execution plan to disk in both Markdown and JSON formats.

        The JSON file is written atomically via a temporary file and rename
        to prevent partial reads by concurrent processes.  The Markdown
        rendering is written directly (it is advisory, not machine-parsed).

        Args:
            plan: The execution plan to persist.

        Returns:
            Path to the written ``plan.md`` file.
        """
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
        """Write the shared context document from structured sections.

        Produces a Markdown file that every dispatched agent reads before
        starting work.  Sections with empty content are rendered as
        placeholder stubs so the document structure remains consistent.

        Args:
            task: Short task description used in the document title.
            stack: Detected technology stack summary.
            architecture: Project architecture notes.
            conventions: Coding conventions and patterns.
            guardrails: Active guardrail preset description.
            agent_assignments: Agent-to-step mapping summary.
            domain_context: Optional domain-specific business context
                (omitted from the output when empty).

        Returns:
            Path to the written ``context.md`` file.
        """
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
        """Initialize a new mission log with a header and metadata.

        Creates (or overwrites) the mission log file with a title, ISO
        timestamp, and risk level.  Subsequent entries are appended via
        :meth:`append_to_mission_log`.

        Args:
            task: Short task description used in the log title.
            risk_level: Risk classification string (e.g. ``"LOW"``,
                ``"MEDIUM"``, ``"HIGH"``).

        Returns:
            Path to the created ``mission-log.md`` file.
        """
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
        """Append a completion entry to the mission log.

        If the mission log file does not yet exist, it is auto-initialized
        with a placeholder title before appending.  Each entry is separated
        by a Markdown horizontal rule for readability.

        Args:
            entry: The mission log entry to append, typically recording
                an agent's completion status and outcome summary.
        """
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
        """Check which recovery-relevant files exist for session resumption.

        Used by the ``baton execute resume`` command to determine whether
        a crashed or interrupted session has enough state on disk to
        continue execution without re-planning.

        Returns:
            Dictionary with keys ``"plan"``, ``"context"``,
            ``"mission_log"``, and ``"profile"``, each mapping to a
            boolean indicating whether the corresponding file exists.
        """
        return {
            "plan": self.plan_path.exists(),
            "context": self.context_path.exists(),
            "mission_log": self.mission_log_path.exists(),
            "profile": self.profile_path.exists(),
        }

    # ── Discovery ──────────────────────────────────────────

    @staticmethod
    def list_task_ids(context_root: Path) -> list[str]:
        """List all task IDs that have execution directories.

        Scans the ``executions/`` subdirectory of *context_root* for
        child directories, each representing a task-scoped execution.

        Args:
            context_root: The root ``team-context/`` directory to scan.

        Returns:
            Sorted list of task ID strings.  Empty list if the
            ``executions/`` directory does not exist.
        """
        exec_dir = context_root / _EXECUTIONS_DIR
        if not exec_dir.is_dir():
            return []
        return sorted(
            child.name
            for child in exec_dir.iterdir()
            if child.is_dir()
        )
