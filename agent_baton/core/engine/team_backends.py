"""Pluggable team-execution backends (A1).

Two implementations:

* :class:`WorktreeTeamBackend` (default) — the existing baton behavior:
  team members are dispatched as parallel ``Agent`` calls under git
  worktree isolation. Resumable via ``baton execute resume``. Skills
  and MCP servers from agent frontmatter are honored. This is what
  ships today; the backend class is a thin observer that recognises a
  team is in flight.

* :class:`ClaudeTeamsBackend` (opt-in via ``BATON_TEAMS_BACKEND=claude-teams``)
  — produces a natural-language spawn prompt directing an outer
  Claude Code session to create a real Agent Team via the experimental
  ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`` feature. The Claude Code
  team's hooks (``TaskCreated``, ``TaskCompleted``, ``TeammateIdle``)
  call back into baton via ``baton execute team-record`` to keep the
  baton mailbox + executor state in sync.

Known limitations of the claude-teams backend (Anthropic-side, not
baton-side):

* No in-process teammate resumption — sessions resumed via ``baton
  execute resume`` cannot revive in-flight Claude-Teams teammates.
* One team at a time per lead session.
* No nested teams.
* Permissions fixed at spawn.
* ``skills`` and ``mcpServers`` frontmatter on subagent definitions is
  **not** honored when used as a teammate; agents that depend on those
  must either be wrapped or excluded when this backend is active. See
  :func:`audit_agents_for_teammate_safety`.

See ``docs/internal/agent-teams-and-goal-design.md`` for the design
rationale.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_baton.models.execution import MachinePlan, PlanStep

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class TeamBackend(Protocol):
    """Strategy interface for executing a team step.

    All methods are best-effort observers: failures must NOT block the
    underlying engine's team dispatch. Implementations should log and
    swallow non-fatal errors.
    """

    name: str

    def on_team_dispatched(
        self,
        *,
        plan: MachinePlan,
        step: PlanStep,
        team_context_root: Path,
    ) -> None:
        """Called when a team step transitions from pending → dispatched.

        WorktreeTeamBackend: no-op (existing dispatcher path handles
        the work). ClaudeTeamsBackend: writes a spawn prompt artifact
        the lead orchestrator picks up.
        """
        ...

    def hook_record_command(
        self,
        *,
        task_id: str,
        step_id: str,
        member_id: str,
    ) -> str:
        """Return the CLI command Claude Code's per-teammate hook should
        invoke when that teammate finishes a task.

        Used by the hook bridge (A1.c). Returns the empty string when
        the backend does not use external hook callbacks.
        """
        ...


# ---------------------------------------------------------------------------
# Worktree backend (default)
# ---------------------------------------------------------------------------

class WorktreeTeamBackend:
    """Default backend: parallel worktree-isolated dispatch.

    No external coordination required — the executor's existing
    ``_team_dispatch_action`` does the work, and the mailbox (A2)
    captures coordination events. This backend exists so callers have
    a uniform interface; almost everything it does is a no-op.
    """

    name = "worktree"

    def on_team_dispatched(
        self,
        *,
        plan: MachinePlan,
        step: PlanStep,
        team_context_root: Path,
    ) -> None:  # noqa: D401, ARG002
        _log.debug(
            "WorktreeTeamBackend.on_team_dispatched(step_id=%s) — no-op",
            step.step_id,
        )

    def hook_record_command(
        self,
        *,
        task_id: str,  # noqa: ARG002
        step_id: str,  # noqa: ARG002
        member_id: str,  # noqa: ARG002
    ) -> str:
        return ""  # no external hooks


# ---------------------------------------------------------------------------
# Claude-teams backend (experimental, opt-in)
# ---------------------------------------------------------------------------

class ClaudeTeamsBackend:
    """Delegate to Claude Code's experimental Agent Teams feature.

    On team dispatch, write a spawn-prompt artifact under
    ``.claude/team-context/teams/{team-id}/spawn.md`` for the lead
    orchestrator session to consume. The lead reads it and emits the
    natural-language instruction that Claude Code interprets as "create
    a team with these members".

    Hooks on the Claude-Teams side (``TaskCompleted`` etc) call back
    into baton via the bridge CLI (A1.c) so the engine state and
    mailbox stay in sync.
    """

    name = "claude-teams"

    def on_team_dispatched(
        self,
        *,
        plan: MachinePlan,
        step: PlanStep,
        team_context_root: Path,
    ) -> None:
        try:
            team_dir = team_context_root / "teams" / f"team-{step.step_id}"
            team_dir.mkdir(parents=True, exist_ok=True)
            spawn = team_dir / "spawn.md"
            spawn.write_text(
                self._render_spawn_prompt(plan, step),
                encoding="utf-8",
            )
            _log.info(
                "ClaudeTeamsBackend: wrote spawn prompt for team-%s",
                step.step_id,
            )
        except Exception as exc:  # noqa: BLE001 — backends must not throw
            _log.warning(
                "ClaudeTeamsBackend.on_team_dispatched failed for "
                "step %s (non-fatal): %s",
                step.step_id, exc,
            )

    def hook_record_command(
        self,
        *,
        task_id: str,
        step_id: str,
        member_id: str,
    ) -> str:
        # baton goal/execute is invoked relative to the project root;
        # callers are responsible for chdir or BATON_DB_PATH.
        return (
            f"baton execute team-record --task-id {task_id} "
            f"--step-id {step_id} --member-id {member_id} "
            f"--hook-source claude-teams"
        )

    def _render_spawn_prompt(self, plan: MachinePlan, step: PlanStep) -> str:
        member_lines = "\n".join(
            f"- {m.member_id}: {m.agent_name} ({m.role}) — "
            f"{m.task_description[:200]}"
            for m in step.team
        ) or "- (no team members)"
        return (
            "# Claude Code Agent Team spawn prompt\n\n"
            f"Task: {plan.task_summary}\n"
            f"Team step: {step.step_id} — {step.task_description}\n\n"
            "Create an agent team with the following members. Use the "
            "subagent definitions in `.claude/agents/` by name for each "
            "teammate.\n\n"
            f"{member_lines}\n\n"
            "## After spawn\n\n"
            "Wire Claude Code's `TaskCompleted` hook for this team to run:\n\n"
            "```bash\n"
            f"{self.hook_record_command(task_id=plan.task_id, step_id=step.step_id, member_id='<MEMBER_ID>')}\n"
            "```\n\n"
            "## Known limitations of this backend\n\n"
            "- No in-process resumption — `/resume` cannot revive teammates.\n"
            "- One team at a time; clean up before another team step.\n"
            "- No nested teams.\n"
            "- Subagent `skills` / `mcpServers` frontmatter is NOT honored.\n"
        )


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, type[TeamBackend]] = {
    "worktree": WorktreeTeamBackend,
    "claude-teams": ClaudeTeamsBackend,
}


def select_team_backend(name: str | None = None) -> TeamBackend:
    """Return the configured TeamBackend instance.

    Resolution order:
    1. Explicit *name* argument.
    2. ``BATON_TEAMS_BACKEND`` env var.
    3. Default: ``"worktree"``.

    Unknown values log a warning and fall back to ``"worktree"`` so a
    typo in settings never breaks execution.
    """
    chosen = (name or os.environ.get("BATON_TEAMS_BACKEND") or "worktree").strip().lower()
    cls = _BACKENDS.get(chosen)
    if cls is None:
        _log.warning(
            "Unknown BATON_TEAMS_BACKEND=%r; falling back to 'worktree'. "
            "Valid choices: %s",
            chosen, sorted(_BACKENDS),
        )
        cls = WorktreeTeamBackend
    return cls()


# ---------------------------------------------------------------------------
# Agent frontmatter safety audit (A1.e helper)
# ---------------------------------------------------------------------------

def audit_agents_for_teammate_safety(
    agents_dir: Path,
) -> dict[str, list[str]]:
    """Return ``{agent_name: ["skills", "mcpServers"]}`` for agents whose
    YAML frontmatter declares load-bearing fields that Claude Code Agent
    Teams does NOT honor when the agent is used as a teammate.

    Read the .md files in *agents_dir*, parse the YAML header (a thin
    inline parser — no PyYAML dep so the helper stays cheap to import),
    and flag any agent with non-empty ``skills:`` or ``mcpServers:``
    fields.
    """
    flagged: dict[str, list[str]] = {}
    if not agents_dir.exists():
        return flagged
    for md in sorted(agents_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        # Frontmatter starts and ends with --- on its own line.
        if not text.startswith("---"):
            continue
        try:
            _, fm, _ = text.split("---", 2)
        except ValueError:
            continue
        agent_name = md.stem
        problems: list[str] = []
        for field in ("skills", "mcpServers"):
            # Look for "field:" at start of a line, followed by a
            # non-empty value or a yaml list.
            for line in fm.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{field}:"):
                    rhs = stripped.split(":", 1)[1].strip()
                    if rhs and rhs not in ("[]", "{}", "null", "~"):
                        problems.append(field)
                    break
        if problems:
            flagged[agent_name] = problems
    return flagged


def check_resumability_constraints(plan: MachinePlan) -> list[str]:
    """Return planner-time warnings for the claude-teams backend (A1.d).

    Claude Code's experimental Agent Teams feature cannot revive
    in-process teammates after ``/resume``. When the active backend is
    ``claude-teams`` AND the budget tier is ``long-running``, placing
    team phases late in the plan creates a real footgun: a crash at a
    late team phase means the work cannot be picked up where it left
    off.

    Returns a list of human-readable warning strings (empty when the
    plan is safe or the backend is not claude-teams). Caller decides
    whether to emit as a CLI warning, a bead, or both.
    """
    warnings: list[str] = []
    backend_name = os.environ.get("BATON_TEAMS_BACKEND", "worktree").strip().lower()
    if backend_name != "claude-teams":
        return warnings
    if (plan.budget_tier or "").lower() not in ("long-running", "long_running"):
        return warnings

    team_phase_ids = [
        p.phase_id for p in plan.phases
        if any(s.team for s in p.steps)
    ]
    if not team_phase_ids:
        return warnings

    # "Late" = anything beyond the first half of the phase count when
    # there are 4+ phases. For smaller plans, any team phase is
    # effectively late under long-running and gets flagged.
    n_phases = len(plan.phases)
    if n_phases >= 4:
        cutoff = n_phases // 2
        late_team_phases = [pid for pid in team_phase_ids if pid > cutoff]
    else:
        late_team_phases = list(team_phase_ids)

    if late_team_phases:
        warnings.append(
            f"BATON_TEAMS_BACKEND=claude-teams + budget_tier=long-running: "
            f"team phases at {late_team_phases} cannot resume after a crash. "
            f"Consider rearranging team work earlier in the plan, or splitting "
            f"the long-running plan into shorter resumable segments. See "
            f"docs/internal/agent-teams-and-goal-design.md."
        )
    return warnings


__all__ = [
    "TeamBackend",
    "WorktreeTeamBackend",
    "ClaudeTeamsBackend",
    "select_team_backend",
    "audit_agents_for_teammate_safety",
    "check_resumability_constraints",
]
