"""``ManagerReportBuilder`` -- manager-facing brief and status report artifacts (M7).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10,
docs/internal/manager-mode-pmo-design.md ("Report template: ... mirrors
``RetrospectiveEngine``"), and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §15.1/§15.2/§16
Milestone 7.

Two artifacts, mirroring :class:`~agent_baton.core.observe.retrospective.RetrospectiveEngine`'s
generate/save split:

* :meth:`ManagerReportBuilder.build_brief` -- written once, right after
  manager-mode planning (``manager-brief.md``). Sections, in order:
  Objective, Assumptions, Workstreams, Team, Knowledge Packs, Configured
  Policies, Manager Decision Points, Risks (PRD §15.1).
* :meth:`ManagerReportBuilder.build_report` -- written during/after
  execution (``manager-report.md``). Sections, in order: Status,
  Phase/Workstream Progress, Team Activity, Handoffs Completed, Knowledge
  Gaps, Scope Changes, Reviews, Gates, Open Decisions, Incidents (only when
  non-empty), Final Recommendation (PRD §15.2).

Composition/ownership rule (binding, Wave 1 review): workstream ownership
*authority* is :attr:`~agent_baton.models.manager.TeamBlueprint.workstream_assignments`
-- never ``Workstream.owner_role`` (the scope map's pre-diversification
baseline) and never a step's ``agent_name``. Every helper in this module
that renders a workstream's owner reads it from
``blueprint.workstream_assignments``. A role that owns zero workstreams
after diversification (a "displaced generalist") is listed under Team
only -- it must never appear as a workstream's owner (there is nothing to
misattribute to it, since ``workstream_assignments`` simply has no entry
pointing at it).

No raw logs by default (PRD: "raw logs are not included by default",
``ReportingConfig.include_raw_logs_by_default``): report data is built
exclusively from curated, already-summarized fields -- step **status**
counts, artifact names, decision summaries -- never a step's
``task_description`` (the dispatch prompt) or a ``StepResult.outcome``'s
free-text body. If a future increment wants raw-log inclusion behind
``include_raw_logs_by_default``, it should add an explicit opt-in section
rather than loosening the fields this module already reads.

Determinism: like every other manager-mode builder, this module reads no
clock and does not construct :class:`~agent_baton.models.manager.ManagerDecision`
objects itself -- ``created_at`` is always caller-supplied (see
:mod:`agent_baton.core.manager.decisions`). The one exception is reading
directory mtimes indirectly via ``Path.glob`` sorting, which is by name,
not time, and therefore deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.core.manager.artifacts import write_text
from agent_baton.core.manager.paths import ManagerArtifactPaths

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.manager.artifacts import ManagerArtifacts
    from agent_baton.models.execution import MachinePlan
    from agent_baton.models.manager import KnowledgePlan, ScopeMap, TeamBlueprint

_REVIEW_STEP_PREFIX = "review-"
_TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})
_NONE_RECORDED = "_None recorded._"


class ManagerReportBuilder:
    """Builds the manager brief (post-planning) and manager report
    (during/after execution) for a single manager-mode execution.

    Bound to one :class:`~agent_baton.core.manager.paths.ManagerArtifactPaths`
    so it can read sidecar files it doesn't otherwise receive as arguments
    (``handoffs/*.md`` for "Handoffs Completed", ``decision-log.jsonl`` for
    "Scope Changes"/"Open Decisions") -- see :meth:`read_decision_log`.
    """

    def __init__(self, config: "ManagerConfig", paths: ManagerArtifactPaths) -> None:
        self.config = config
        self.paths = paths

    # ------------------------------------------------------------------
    # Brief (post-planning) -- PRD §15.1
    # ------------------------------------------------------------------

    def build_brief(self, artifacts: "ManagerArtifacts", plan: "MachinePlan") -> str:
        """Render ``manager-brief.md`` from *artifacts* and *plan*.

        Degrades gracefully when ``artifacts.charter`` is ``None`` (e.g.
        the CLI rebuilding from sidecars that have no JSON form for the
        charter -- see ``ManagerArtifactPaths.charter``, markdown-only):
        Objective falls back to ``plan.task_summary`` and the
        Assumptions/Manager-Decision-Points/Risks sections render as
        "None recorded" rather than raising.
        """
        charter = artifacts.charter
        scope_map = artifacts.scope_map
        blueprint = artifacts.blueprint
        knowledge_plan = artifacts.knowledge_plan

        lines: list[str] = [f"# Manager Brief: {plan.task_id}", ""]

        lines.append("## Objective")
        objective = (charter.objective if charter else "") or plan.task_summary
        lines.append(objective or "_Not specified._")
        lines.append("")

        lines.append("## Assumptions")
        lines.extend(_bullets(charter.assumptions if charter else []))
        lines.append("")

        lines.append("## Workstreams")
        lines.extend(_workstream_table(scope_map, blueprint))
        lines.append("")

        lines.append("## Team")
        lines.extend(_team_summary(blueprint))
        lines.append("")

        lines.append("## Knowledge Packs")
        lines.extend(_knowledge_summary(knowledge_plan, artifacts.context_bundles))
        lines.append("")

        lines.append("## Configured Policies")
        lines.extend(_policy_summary(self.config))
        lines.append("")

        lines.append("## Manager Decision Points")
        lines.extend(_bullets(charter.manager_decision_points if charter else []))
        lines.append("")

        lines.append("## Risks")
        lines.extend(_bullets(charter.risks if charter else []))
        lines.append("")

        return _finish(lines)

    def save_brief(self, artifacts: "ManagerArtifacts", plan: "MachinePlan") -> Path:
        write_text(self.paths.manager_brief, self.build_brief(artifacts, plan))
        return self.paths.manager_brief

    # ------------------------------------------------------------------
    # Report (during/after execution) -- PRD §15.2
    # ------------------------------------------------------------------

    def build_report_data(
        self,
        plan: "MachinePlan",
        artifacts: "ManagerArtifacts",
        execution_state: dict[str, Any] | None = None,
        beads: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Collect the structured data both :meth:`build_report` (Markdown)
        and ``baton report --json`` render from.

        Single source of truth so the Markdown and JSON renderings can
        never drift: every value here is already curated (status strings,
        counts, decision summaries) -- see the module docstring's "No raw
        logs by default" note.
        """
        blueprint = artifacts.blueprint
        scope_map = artifacts.scope_map
        knowledge_plan = artifacts.knowledge_plan

        status = (execution_state or {}).get("status") or "planned"

        step_status: dict[str, str] = {}
        for record in (execution_state or {}).get("step_results", []) or []:
            step_id = record.get("step_id")
            if step_id:
                step_status[step_id] = record.get("status", "")

        workstreams = _workstream_progress(scope_map, blueprint, plan, step_status, bool(execution_state))

        team: list[dict[str, Any]] = []
        if blueprint is not None:
            owned_counts = _owned_workstream_counts(blueprint)
            for card in blueprint.roles:
                team.append({
                    "role": card.role,
                    "mission": card.mission,
                    "workstreams_owned": owned_counts.get(card.role, 0),
                })

        handoffs_completed: list[str] = []
        if self.paths.handoffs_dir.is_dir():
            handoffs_completed = sorted(
                p.name for p in self.paths.handoffs_dir.glob("phase-*-handoff.md")
            )

        knowledge_gaps: list[str] = []
        if knowledge_plan is not None:
            for missing in knowledge_plan.missing_packs:
                knowledge_gaps.append(f"missing pack: {missing.name} ({missing.reason})")
            for name in knowledge_plan.stale_packs:
                knowledge_gaps.append(f"stale pack: {name}")
        for gap in (execution_state or {}).get("pending_gaps", []) or []:
            description = gap.get("description", "") if isinstance(gap, dict) else str(gap)
            if description:
                knowledge_gaps.append(description)

        decision_log = self.read_decision_log()
        scope_changes = [
            {
                "decision_id": entry.get("decision_id", ""),
                "summary": entry.get("summary", ""),
                "resolved": bool(entry.get("resolved_at")),
            }
            for entry in decision_log
            if entry.get("decision_type") == "scope_expansion"
        ]
        open_decisions = [
            {
                "decision_id": entry.get("decision_id", ""),
                "decision_type": entry.get("decision_type", ""),
                "summary": entry.get("summary", ""),
            }
            for entry in decision_log
            if not entry.get("resolved_at")
        ]

        review_step_ids = [
            step.step_id for step in plan.all_steps if step.step_id.startswith(_REVIEW_STEP_PREFIX)
        ]
        reviews_completed = [sid for sid in review_step_ids if step_status.get(sid) == "complete"]
        reviews_pending = [sid for sid in review_step_ids if step_status.get(sid) != "complete"]

        gates_completed, gates_pending = _gate_progress(plan, execution_state)

        final_recommendation = _final_recommendation(status, reviews_completed, open_decisions)

        incidents = _bead_notes(beads)

        return {
            "task_id": plan.task_id,
            "status": status,
            "workstreams": workstreams,
            "team": team,
            "handoffs_completed": handoffs_completed,
            "knowledge_gaps": knowledge_gaps,
            "scope_changes": scope_changes,
            "reviews": {"completed": reviews_completed, "pending": reviews_pending},
            "gates": {"completed": gates_completed, "pending": gates_pending},
            "open_decisions": open_decisions,
            "final_recommendation": final_recommendation,
            "incidents": incidents,
        }

    def build_report(
        self,
        plan: "MachinePlan",
        artifacts: "ManagerArtifacts",
        execution_state: dict[str, Any] | None = None,
        beads: list[Any] | None = None,
    ) -> str:
        data = self.build_report_data(plan, artifacts, execution_state, beads)
        return _render_report_markdown(data)

    def save_report(
        self,
        plan: "MachinePlan",
        artifacts: "ManagerArtifacts",
        execution_state: dict[str, Any] | None = None,
        beads: list[Any] | None = None,
    ) -> Path:
        write_text(self.paths.manager_report, self.build_report(plan, artifacts, execution_state, beads))
        return self.paths.manager_report

    # ------------------------------------------------------------------
    # Shared: decision log (used by report data + ``baton team``)
    # ------------------------------------------------------------------

    def read_decision_log(self) -> list[dict[str, Any]]:
        """Read ``decision-log.jsonl``, deduped by ``decision_id``.

        ``append_decision_log`` (``agent_baton.core.manager.artifacts``) is
        explicitly append-only and never rewrites a prior line -- so a
        decision that is later amended (e.g. a resolution recorded) would
        appear as a *second* line sharing the same ``decision_id``. This
        reader keeps the last line seen per ``decision_id``, which is the
        only reading that is both forward-compatible with a future
        resolution-append mechanism and consistent with "last write wins"
        semantics elsewhere in this package (e.g. ``StepResult`` re-dispatch
        records). Entries with a missing/empty ``decision_id`` are kept
        as-is (never deduped against each other).
        """
        path = self.paths.decision_log
        if not path.is_file():
            return []
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            decision_id = entry.get("decision_id") or f"_unindexed_{len(order)}"
            if decision_id not in by_id:
                order.append(decision_id)
            by_id[decision_id] = entry
        return [by_id[decision_id] for decision_id in order]


# ---------------------------------------------------------------------------
# Brief helpers
# ---------------------------------------------------------------------------


def _bullets(items: list[str] | None) -> list[str]:
    if not items:
        return [_NONE_RECORDED]
    return [f"- {item}" for item in items]


def _workstream_owner(ws_id: str, blueprint: "TeamBlueprint | None") -> str:
    """The *authoritative* owner of workstream *ws_id*.

    Always ``TeamBlueprint.workstream_assignments`` -- never
    ``Workstream.owner_role`` (the scope map's pre-diversification
    baseline) and never a step's ``agent_name``. See the module docstring.
    """
    if blueprint is None:
        return ""
    return blueprint.workstream_assignments.get(ws_id, "")


def _workstream_table(scope_map: "ScopeMap | None", blueprint: "TeamBlueprint | None") -> list[str]:
    if scope_map is None or not scope_map.workstreams:
        return [_NONE_RECORDED]
    lines = ["| Workstream | Owner | Allowed Paths |", "|---|---|---|"]
    for ws in scope_map.workstreams:
        owner = _workstream_owner(ws.id, blueprint) or "(unassigned)"
        allowed_paths_str = ", ".join(ws.allowed_paths) if ws.allowed_paths else "(none)"
        lines.append(f"| {ws.name or ws.id} | {owner} | {allowed_paths_str} |")
    return lines


def _owned_workstream_counts(blueprint: "TeamBlueprint") -> dict[str, int]:
    counts: dict[str, int] = {}
    for role in blueprint.workstream_assignments.values():
        if role:
            counts[role] = counts.get(role, 0) + 1
    return counts


def _team_summary(blueprint: "TeamBlueprint | None") -> list[str]:
    if blueprint is None or not blueprint.roles:
        return [_NONE_RECORDED]
    owned_counts = _owned_workstream_counts(blueprint)
    lines: list[str] = []
    for card in blueprint.roles:
        count = owned_counts.get(card.role, 0)
        descriptor = f"{count} workstream(s)" if count else "no workstream (support role)"
        lines.append(f"- **{card.role}** -- {card.mission or '(no mission set)'} ({descriptor})")
    return lines


def _knowledge_summary(
    knowledge_plan: "KnowledgePlan | None",
    context_bundles: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    if knowledge_plan is not None and knowledge_plan.selected_packs:
        for pack in knowledge_plan.selected_packs:
            lines.append(f"- {pack.name} (confidence: {pack.confidence}, status: {pack.status})")
    else:
        lines.append(_NONE_RECORDED)

    if knowledge_plan is not None and knowledge_plan.missing_packs:
        lines.append("")
        lines.append("**Missing:**")
        for missing in knowledge_plan.missing_packs:
            lines.append(f"- {missing.name} -- {missing.reason}")

    if knowledge_plan is not None and knowledge_plan.stale_packs:
        lines.append("")
        lines.append("**Stale:**")
        for name in knowledge_plan.stale_packs:
            lines.append(f"- {name}")

    warnings = [
        warning
        for bundle in (context_bundles or {}).values()
        for warning in getattr(bundle, "truncation_warnings", [])
    ]
    if warnings:
        lines.append("")
        lines.append("**Truncation Warnings:**")
        for warning in warnings:
            lines.append(f"- {warning}")

    return lines


def _policy_summary(config: "ManagerConfig") -> list[str]:
    return [
        f"- Phase adversarial review: {config.policies.phase_completion.adversarial_review}",
        f"- Project adversarial review: {config.policies.project_completion.adversarial_review}",
        f"- Handoff required: {config.policies.phase_completion.handoff_required}",
        f"- Gates: {config.policies.phase_completion.gates}",
        f"- Scope expansion policy: {config.scoping.scope_expansion_policy}",
        f"- Out-of-scope policy: {config.scoping.out_of_scope_policy}",
    ]


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _workstream_progress(
    scope_map: "ScopeMap | None",
    blueprint: "TeamBlueprint | None",
    plan: "MachinePlan",
    step_status: dict[str, str],
    execution_started: bool,
) -> list[dict[str, Any]]:
    if scope_map is None:
        return []
    workstreams: list[dict[str, Any]] = []
    for index, ws in enumerate(scope_map.workstreams):
        owner = _workstream_owner(ws.id, blueprint)
        phase = plan.phases[index] if index < len(plan.phases) else None
        step_ids = [s.step_id for s in phase.steps] if phase is not None else []
        steps_total = len(step_ids)
        steps_complete = sum(1 for sid in step_ids if step_status.get(sid) == "complete")
        steps_failed = sum(1 for sid in step_ids if step_status.get(sid) == "failed")

        if not execution_started:
            ws_status = "not_started"
        elif steps_failed:
            ws_status = "blocked"
        elif steps_total and steps_complete == steps_total:
            ws_status = "complete"
        elif any(step_status.get(sid) for sid in step_ids):
            ws_status = "in_progress"
        else:
            ws_status = "pending"

        workstreams.append({
            "id": ws.id,
            "name": ws.name,
            "owner": owner,
            "status": ws_status,
            "steps_total": steps_total,
            "steps_complete": steps_complete,
        })
    return workstreams


def _gate_progress(
    plan: "MachinePlan", execution_state: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gate_status_by_phase: dict[int, bool] = {}
    for gate_result in (execution_state or {}).get("gate_results", []) or []:
        phase_id = gate_result.get("phase_id")
        if phase_id is not None:
            gate_status_by_phase[phase_id] = gate_result.get("passed", False)

    completed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for phase in plan.phases:
        if phase.gate is None:
            continue
        entry = {"phase_id": phase.phase_id, "gate_type": phase.gate.gate_type}
        if phase.phase_id in gate_status_by_phase:
            completed.append({**entry, "passed": gate_status_by_phase[phase.phase_id]})
        else:
            pending.append(entry)
    return completed, pending


def _final_recommendation(
    status: str, reviews_completed: list[str], open_decisions: list[dict[str, Any]]
) -> str | None:
    if status not in _TERMINAL_STATUSES:
        return None
    if status == "complete":
        return (
            f"Project complete. {len(reviews_completed)} review(s) passed, "
            f"{len(open_decisions)} open decision(s) remaining."
        )
    return (
        f"Execution ended with status '{status}'. Review open decisions and "
        "failed steps before re-dispatching."
    )


def _bead_notes(beads: list[Any] | None) -> list[str]:
    notes: list[str] = []
    for bead in beads or []:
        if isinstance(bead, dict):
            bead_type = bead.get("type", "note")
            message = bead.get("message", "")
        else:
            bead_type = getattr(bead, "type", "note")
            message = getattr(bead, "message", "") or str(bead)
        if message:
            notes.append(f"[{bead_type}] {message}")
    return notes


def _render_report_markdown(data: dict[str, Any]) -> str:
    lines: list[str] = [f"# Manager Report: {data['task_id']}", ""]

    lines.append("## Status")
    lines.append(str(data["status"]))
    lines.append("")

    lines.append("## Phase / Workstream Progress")
    if data["workstreams"]:
        lines.append("| Workstream | Owner | Status | Steps |")
        lines.append("|---|---|---|---|")
        for ws in data["workstreams"]:
            lines.append(
                f"| {ws['name'] or ws['id']} | {ws['owner'] or '(unassigned)'} | "
                f"{ws['status']} | {ws['steps_complete']}/{ws['steps_total']} |"
            )
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    lines.append("## Team Activity")
    if data["team"]:
        for member in data["team"]:
            descriptor = (
                f"owns {member['workstreams_owned']} workstream(s)"
                if member["workstreams_owned"]
                else "no workstream (support role)"
            )
            lines.append(f"- {member['role']}: {descriptor}")
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    lines.append("## Handoffs Completed")
    lines.extend(_bullets(data["handoffs_completed"]))
    lines.append("")

    lines.append("## Knowledge Gaps")
    lines.extend(_bullets(data["knowledge_gaps"]))
    lines.append("")

    lines.append("## Scope Changes")
    if data["scope_changes"]:
        for change in data["scope_changes"]:
            marker = "resolved" if change["resolved"] else "open"
            lines.append(f"- [{marker}] {change['summary']} ({change['decision_id']})")
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    lines.append("## Reviews")
    completed = ", ".join(data["reviews"]["completed"]) or "(none)"
    pending = ", ".join(data["reviews"]["pending"]) or "(none)"
    lines.append(f"- Completed: {completed}")
    lines.append(f"- Pending: {pending}")
    lines.append("")

    lines.append("## Gates")
    gates_completed = ", ".join(f"{g['phase_id']}:{g['gate_type']}" for g in data["gates"]["completed"]) or "(none)"
    gates_pending = ", ".join(f"{g['phase_id']}:{g['gate_type']}" for g in data["gates"]["pending"]) or "(none)"
    lines.append(f"- Completed: {gates_completed}")
    lines.append(f"- Pending: {gates_pending}")
    lines.append("")

    lines.append("## Open Decisions")
    if data["open_decisions"]:
        for decision in data["open_decisions"]:
            lines.append(
                f"- {decision['summary']} ({decision['decision_id']}, {decision['decision_type']})"
            )
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    if data["incidents"]:
        lines.append("## Incidents")
        lines.extend(_bullets(data["incidents"]))
        lines.append("")

    lines.append("## Final Recommendation")
    lines.append(data["final_recommendation"] or "_Execution in progress; no final recommendation yet._")
    lines.append("")

    return _finish(lines)


def _finish(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"
