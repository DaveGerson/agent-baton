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

Both backends are **supported**. They trade off different properties —
worktree wins on isolation, resumability, nesting, and frontmatter
fidelity; claude-teams wins on native Agent Teams UX (inter-teammate
messaging, shared task list, lead plan-approval). Pick per task; the
comparison table lives in ``docs/engine-and-runtime.md`` §18.

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

from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_baton.models.execution import MachinePlan, PlanStep
from agent_baton.utils.frontmatter import parse_frontmatter

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
# Claude-teams backend (supported, opt-in)
# ---------------------------------------------------------------------------

# Official Agent Teams guidance: keep teams small (≤5 members) so the lead
# can coordinate effectively. Larger teams should be split.
_MAX_TEAM_MEMBERS = 5

CLAUDE_TEAMS_CAVEATS: tuple[str, ...] = (
    "no resume: baton execute resume cannot revive in-flight Claude Teams teammates",
    "no nesting: nested teams are flattened for Claude Teams dispatch",
    "one team at a time: a lead session can coordinate only one Agent Team at a time",
    "fixed permissions: teammate permissions are fixed at spawn time",
    "missing skills/MCP frontmatter: subagent skills and mcpServers frontmatter are not honored for teammates",
)


class UnknownTeamBackendError(ValueError):
    """Raised when strict backend selection rejects an unknown backend."""


@dataclass(frozen=True)
class TeamReadinessDiagnostics:
    """Structured readiness summary emitted before a team step is dispatched."""

    backend: str
    step_id: str
    member_count: int
    top_level_member_count: int
    nested_team_count: int
    shared_files: list[str]
    shared_contracts: list[dict[str, object]]
    synthesis_strategy: str
    conflict_strategy: str
    warnings: list[str]
    report_path: str = ""

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "step_id": self.step_id,
            "member_count": self.member_count,
            "top_level_member_count": self.top_level_member_count,
            "nested_team_count": self.nested_team_count,
            "shared_files": list(self.shared_files),
            "shared_contracts": [dict(c) for c in self.shared_contracts],
            "synthesis_strategy": self.synthesis_strategy,
            "conflict_strategy": self.conflict_strategy,
            "warning_count": self.warning_count,
            "warnings": list(self.warnings),
            "report_path": self.report_path,
        }

    def with_report_path(self, report_path: str) -> "TeamReadinessDiagnostics":
        return replace(self, report_path=report_path)


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _flatten_members(team: list["TeamMember"]) -> list["TeamMember"]:
    """Depth-first flatten of a team roster, descending into ``sub_team``.

    Returns every member — top-level and nested — in a single flat list.
    Agent Teams cannot nest, so the claude-teams backend flattens sub-teams
    into the flat roster (annotating each with its coordinating lead in the
    spawn prompt).  Used by the frontmatter safety audit so nested teammates
    are flagged too.
    """
    flat: list["TeamMember"] = []
    for member in team:
        flat.append(member)
        if member.sub_team:
            flat.extend(_flatten_members(member.sub_team))
    return flat


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
            # --- Degrade-loudly warnings emitted at dispatch time ---------
            # 1b. Frontmatter safety audit: agents declaring skills/mcpServers
            #     are not honored as teammates. Log + render per-agent blocks.
            safety_flags = self._audit_step_agents(step, team_context_root)
            for agent_name, fields in safety_flags.items():
                _log.warning(
                    "ClaudeTeamsBackend: agent %r in team-%s declares %s "
                    "frontmatter — NOT honored when used as a teammate; "
                    "those capabilities will be missing.",
                    agent_name, step.step_id, "+".join(fields),
                )

            # 1a. Nested-team degradation: Agent Teams cannot nest. Warn when
            #     any member carries a sub_team (flattened in the spawn.md).
            nested_leads = [m.member_id for m in step.team if m.sub_team]
            if nested_leads:
                _log.warning(
                    "ClaudeTeamsBackend: team-%s has nested sub-teams under "
                    "lead(s) %s — Agent Teams cannot nest; sub-team structure "
                    "is FLATTENED in spawn.md. Use the worktree backend to "
                    "preserve nesting.",
                    step.step_id, nested_leads,
                )

            if len(step.team) > _MAX_TEAM_MEMBERS:
                _log.warning(
                    "ClaudeTeamsBackend: team-%s has %d members (> %d "
                    "recommended) — consider splitting into smaller teams.",
                    step.step_id, len(step.team), _MAX_TEAM_MEMBERS,
                )

            team_dir = team_context_root / "teams" / f"team-{step.step_id}"
            team_dir.mkdir(parents=True, exist_ok=True)
            spawn = team_dir / "spawn.md"
            spawn.write_text(
                self._render_spawn_prompt(plan, step, safety_flags),
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

    @staticmethod
    def _audit_step_agents(
        step: PlanStep, team_context_root: Path,
    ) -> dict[str, list[str]]:
        """Return ``{agent_name: [fields]}`` for team agents whose frontmatter
        declares ``skills``/``mcpServers`` (not honored as teammates).

        Resolves the agents directory relative to the team-context root
        (``.claude/team-context`` → ``.claude/agents``). Falls back to a
        repo-level ``agents/`` dir. Best-effort: returns ``{}`` on any error,
        and only flags agents that actually appear in this step's team.
        """
        team_agents = {m.agent_name for m in _flatten_members(step.team)}
        if not team_agents:
            return {}
        candidate_dirs = [
            team_context_root.parent / "agents",   # .claude/agents
            team_context_root.parent.parent / "agents",  # repo agents/
        ]
        for agents_dir in candidate_dirs:
            try:
                flagged = audit_agents_for_teammate_safety(agents_dir)
            except Exception:  # noqa: BLE001 — degrade loudly, never throw
                continue
            scoped = {a: f for a, f in flagged.items() if a in team_agents}
            if scoped:
                return scoped
        return {}

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

    def _render_spawn_prompt(
        self,
        plan: MachinePlan,
        step: PlanStep,
        safety_flags: dict[str, list[str]] | None = None,
    ) -> str:
        safety_flags = safety_flags or {}
        risk = (plan.risk_level or "").upper()
        high_risk = risk in ("HIGH", "CRITICAL")

        # --- Member roster (flattened) -----------------------------------
        # Agent Teams cannot nest, so any sub_team a lead carries is
        # flattened into the flat member list with an annotation pointing
        # back at the coordinating lead.
        member_lines: list[str] = []
        has_nested = False
        for m in step.team:
            member_lines.append(
                f"- **{m.member_id}** — spawn subagent `{m.agent_name}` "
                f"(model: `{m.model}`, role: {m.role})\n"
                f"  - File-scope contract / task: {m.task_description[:300]}"
            )
            for sub in m.sub_team:
                has_nested = True
                member_lines.append(
                    f"- **{sub.member_id}** — spawn subagent `{sub.agent_name}` "
                    f"(model: `{sub.model}`, role: {sub.role}) "
                    f"_(sub-team of {m.member_id}, coordinated by that lead)_\n"
                    f"  - File-scope contract / task: {sub.task_description[:300]}"
                )
        members_block = "\n".join(member_lines) or "- (no team members)"

        # --- Per-agent safety warning blocks -----------------------------
        safety_block = ""
        if safety_flags:
            lines = [
                f"- WARNING: agent `{agent}` declares "
                f"{'/'.join(fields)} — NOT honored as a teammate; "
                f"those capabilities will be missing. Re-inject the needed "
                f"context into that teammate's prompt, or run this team under "
                f"the worktree backend instead."
                for agent, fields in sorted(safety_flags.items())
            ]
            safety_block = (
                "\n## ⚠ Teammate capability warnings\n\n"
                + "\n".join(lines)
                + "\n"
            )

        # --- Nested-team flattening warning ------------------------------
        nested_block = ""
        if has_nested:
            nested_block = (
                "\n> WARNING: Native Agent Teams cannot nest — sub-team "
                "structure is flattened here; the worktree backend preserves "
                "nesting. Each sub-team member is listed flat above with its "
                "coordinating lead noted in parentheses.\n"
            )

        # --- Team-size guidance ------------------------------------------
        size_block = ""
        if len(step.team) > _MAX_TEAM_MEMBERS:
            size_block = (
                f"\n> NOTE: this team has {len(step.team)} members. Official "
                f"guidance is ≤{_MAX_TEAM_MEMBERS} teammates per lead so "
                f"coordination stays manageable. Consider splitting this into "
                f"smaller sub-steps.\n"
            )

        # --- Plan-approval requirement -----------------------------------
        has_reviewer = any(m.role == "reviewer" for m in step.team)
        approval_block = ""
        if has_reviewer or high_risk:
            why = "a reviewer-role member is present" if has_reviewer else (
                f"plan risk is {risk}"
            )
            approval_block = (
                "\n## Teammate plan approval (REQUIRED)\n\n"
                f"Because {why}, the lead MUST require each implementing "
                "teammate to submit its plan and obtain lead approval BEFORE "
                "writing any code (baton's mailbox plan-approval flow / Agent "
                "Teams shared task list). Record each approval decision as a "
                "note via:\n\n"
                "```bash\n"
                "baton execute team-record --task-id "
                f"{plan.task_id} --step-id {step.step_id} "
                "--member-id <MEMBER_ID> --status complete "
                "--outcome \"plan approved by lead: <summary>\" "
                "--hook-source claude-teams\n"
                "```\n"
            )

        hook_cmd = self.hook_record_command(
            task_id=plan.task_id, step_id=step.step_id, member_id="<MEMBER_ID>",
        )

        return (
            "# Claude Code Agent Team spawn prompt\n\n"
            f"Task: {plan.task_summary}\n"
            f"Team step: {step.step_id} — {step.task_description}\n"
            f"Plan risk: {risk or 'UNSET'}\n\n"
            "Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.\n\n"
            "## Spawn instructions\n\n"
            "Create an agent team. Spawn **each member by its subagent "
            "definition name** (from `.claude/agents/`) with the model shown "
            "— Agent Teams honors each teammate's tools and model from its "
            "frontmatter. Partition files explicitly: each member's task "
            "below is its file-scope contract; do not let two teammates write "
            "the same files.\n\n"
            f"{members_block}\n"
            f"{nested_block}"
            f"{size_block}"
            f"{safety_block}"
            f"{approval_block}"
            "\n## After spawn — wire result callbacks\n\n"
            "Install this `TaskCompleted` hook in `.claude/settings.json` so "
            "teammate results flow back into baton automatically (substitute "
            "the finishing teammate's `<MEMBER_ID>`):\n\n"
            "```json\n"
            "{\n"
            '  "hooks": {\n'
            '    "TaskCompleted": [\n'
            "      {\n"
            '        "hooks": [\n'
            "          {\n"
            '            "type": "command",\n'
            f'            "command": "{hook_cmd}"\n'
            "          }\n"
            "        ]\n"
            "      }\n"
            "    ]\n"
            "  }\n"
            "}\n"
            "```\n\n"
            "Hint: a `TeammateIdle` hook can nudge idle teammates to pick up "
            "the next shared-task-list item or report blockers to the lead.\n\n"
            "## Known limitations of this backend\n\n"
            "- No in-process resumption — `/resume` cannot revive teammates "
            "(`baton execute resume` cannot revive an in-flight team).\n"
            "- One team at a time; clean up before another team step.\n"
            "- No nested teams (sub-teams are flattened above).\n"
            "- Fixed permissions at spawn time; adjust permissions before launch.\n"
            "- Subagent `skills` / `mcpServers` frontmatter is NOT honored on "
            "teammates.\n"
        )


# ---------------------------------------------------------------------------
# Readiness diagnostics + report artifact
# ---------------------------------------------------------------------------

def build_team_readiness_diagnostics(
    *,
    plan: MachinePlan,
    step: PlanStep,
    backend_name: str,
    team_context_root: Path | None = None,
) -> TeamReadinessDiagnostics:
    """Build the runtime readiness payload for a team step.

    The payload is deliberately small and serializable so it can be written to
    ``team-report.json``, copied into ``MachinePlan.plan_diagnostics``, and
    summarized in dispatch messages without changing plan schema.
    """
    flat_members = _flatten_members(step.team)
    nested_team_count = sum(1 for member in flat_members if member.sub_team)
    synthesis = step.synthesis
    warnings: list[str] = []

    if backend_name == ClaudeTeamsBackend.name:
        warnings.extend(CLAUDE_TEAMS_CAVEATS)
        if nested_team_count:
            warnings.append(
                f"team has {nested_team_count} nested team(s); claude-teams "
                "will flatten the structure"
            )
        if len(step.team) > _MAX_TEAM_MEMBERS:
            warnings.append(
                f"team has {len(step.team)} top-level members; recommended "
                f"maximum is {_MAX_TEAM_MEMBERS}"
            )
        if team_context_root is not None:
            safety_flags = ClaudeTeamsBackend._audit_step_agents(
                step, team_context_root,
            )
            for agent_name, fields in sorted(safety_flags.items()):
                warnings.append(
                    f"agent {agent_name} declares {'/'.join(fields)} "
                    "frontmatter; claude-teams teammates will miss it"
                )

    shared_contracts = [
        {
            "member_id": member.member_id,
            "agent_name": member.agent_name,
            "role": member.role,
            "task_description": member.task_description,
            "deliverables": list(member.deliverables),
        }
        for member in flat_members
    ]

    return TeamReadinessDiagnostics(
        backend=backend_name,
        step_id=step.step_id,
        member_count=len(flat_members),
        top_level_member_count=len(step.team),
        nested_team_count=nested_team_count,
        shared_files=_dedupe(list(step.context_files or [])),
        shared_contracts=shared_contracts,
        synthesis_strategy=(
            synthesis.strategy if synthesis is not None else "concatenate"
        ),
        conflict_strategy=(
            synthesis.conflict_handling if synthesis is not None else "auto_merge"
        ),
        warnings=warnings,
    )


def write_team_readiness_report(
    *,
    diagnostics: TeamReadinessDiagnostics,
    team_context_root: Path,
) -> TeamReadinessDiagnostics:
    """Write ``team-report.json`` under the existing team artifact directory."""
    team_dir = team_context_root / "teams" / f"team-{diagnostics.step_id}"
    team_dir.mkdir(parents=True, exist_ok=True)
    report = team_dir / "team-report.json"
    try:
        report_path = str(report.relative_to(team_context_root))
    except ValueError:
        report_path = str(report)
    diagnostics = diagnostics.with_report_path(report_path)
    report.write_text(
        json.dumps(diagnostics.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return diagnostics


def format_team_readiness_summary(
    diagnostics: TeamReadinessDiagnostics,
) -> str:
    """Return a one-line summary safe for existing CLI/API message fields."""
    parts = [
        f"Team readiness: backend={diagnostics.backend}",
        f"members={diagnostics.member_count}",
        f"nested={diagnostics.nested_team_count}",
        f"shared_files={len(diagnostics.shared_files)}",
        f"contracts={len(diagnostics.shared_contracts)}",
        f"synthesis={diagnostics.synthesis_strategy}",
        f"conflict={diagnostics.conflict_strategy}",
        f"warnings={diagnostics.warning_count}",
    ]
    if diagnostics.report_path:
        parts.append(f"report={diagnostics.report_path}")
    if diagnostics.warnings:
        parts.append("warning_notes=[" + "; ".join(diagnostics.warnings[:5]) + "]")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, type[TeamBackend]] = {
    "worktree": WorktreeTeamBackend,
    "claude-teams": ClaudeTeamsBackend,
}


def _strict_backend_selection_enabled() -> bool:
    return os.environ.get("BATON_TEAMS_BACKEND_STRICT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def select_team_backend(name: str | None = None) -> TeamBackend:
    """Return the configured TeamBackend instance.

    Resolution order:
    1. Explicit *name* argument.
    2. ``BATON_TEAMS_BACKEND`` env var.
    3. Default: ``"worktree"``.

    Unknown values log a warning and fall back to ``"worktree"`` by default
    so a typo in settings never breaks execution. Set
    ``BATON_TEAMS_BACKEND_STRICT=1`` to fail instead.
    """
    chosen = (name or os.environ.get("BATON_TEAMS_BACKEND") or "worktree").strip().lower()
    cls = _BACKENDS.get(chosen)
    if cls is None:
        msg = (
            f"Unknown BATON_TEAMS_BACKEND={chosen!r}. "
            f"Valid choices: {sorted(_BACKENDS)}"
        )
        if _strict_backend_selection_enabled():
            raise UnknownTeamBackendError(msg)
        _log.warning(
            "%s; falling back to 'worktree'.", msg,
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

    Read the .md files in *agents_dir*, parse the YAML header through the
    shared frontmatter utility, and flag any agent with non-empty
    ``skills:`` or ``mcpServers:`` values.
    """
    flagged: dict[str, list[str]] = {}
    if not agents_dir.exists():
        return flagged
    for md in sorted(agents_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        metadata, _body = parse_frontmatter(text)
        if not isinstance(metadata, dict):
            continue
        agent_name = md.stem
        problems: list[str] = []
        for field in ("skills", "mcpServers"):
            if field in metadata and _frontmatter_value_is_non_empty(metadata[field]):
                problems.append(field)
        if problems:
            flagged[agent_name] = problems
    return flagged


def _frontmatter_value_is_non_empty(value: object) -> bool:
    """Return True when a parsed frontmatter value declares real content."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(value)


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
    "TeamReadinessDiagnostics",
    "UnknownTeamBackendError",
    "build_team_readiness_diagnostics",
    "format_team_readiness_summary",
    "write_team_readiness_report",
    "select_team_backend",
    "audit_agents_for_teammate_safety",
    "check_resumability_constraints",
]
