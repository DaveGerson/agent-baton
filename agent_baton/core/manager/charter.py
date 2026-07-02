"""``ProjectCharterBuilder`` -- deterministic project charter (M2).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5,
docs/internal/manager-mode-pmo-design.md ("Locked decision 3"), and PRD
§4.1 / §10.1 / §16 Milestone 2.

Every field is derived from the ``MachinePlan`` the 7-stage planner has
already produced (phases/steps/risk/complexity/task_type/detected_stack),
the task summary the director typed, and repo signals under
*project_root* -- never invented. When a signal is missing the builder
records an assumption instead of guessing (see :func:`_ambiguity`).

``ProjectCharterBuilder.build`` takes no clock and no randomness: two
calls with the same inputs always produce an equal ``ProjectCharter``.
Optional LLM polish lives in a separate module
(:mod:`agent_baton.core.manager.enrich`) and is never invoked from here --
callers that want it call :func:`agent_baton.core.manager.enrich.maybe_enrich_charter`
explicitly on the result.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.models.manager import ProjectCharter

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.models.execution import MachinePlan

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class ProjectCharterBuilder:
    """Builds a :class:`ProjectCharter` from a plan, task summary, and repo root."""

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(
        self, plan: "MachinePlan", task_summary: str, project_root: Path
    ) -> ProjectCharter:
        summary = task_summary.strip()
        objective = _sentence_case(summary)

        likely_repo_areas, areas_are_assumed = _likely_repo_areas(
            plan, summary, Path(project_root)
        )

        assumptions: list[str] = [_base_assumption(areas_are_assumed)]

        ambiguous, high_impact = _ambiguity(plan, summary, likely_repo_areas)
        manager_decision_points: list[str] = []
        if ambiguous:
            assumptions.append(
                f"Task description is short/underspecified ({summary!r}); "
                "the scope below is inferred from the plan, not stated "
                "explicitly by the director."
            )
            if high_impact:
                policy = self.config.manager_mode.ambiguity_policy
                if policy in ("ask_when_high_impact", "always_ask"):
                    manager_decision_points.append(
                        "Task is ambiguous and touches a "
                        f"{plan.complexity}-complexity/{plan.risk_level}-risk "
                        "change -- confirm intended scope with the director "
                        "before implementation proceeds."
                    )

        return ProjectCharter(
            task_id=plan.task_id,
            objective=objective,
            title=objective,
            background=_background(plan),
            in_scope=_in_scope(plan),
            out_of_scope=_out_of_scope(self.config),
            assumptions=assumptions,
            constraints=_constraints(self.config, plan),
            risks=_risks(plan),
            manager_decision_points=manager_decision_points,
            success_criteria=_success_criteria(plan),
            likely_repo_areas=likely_repo_areas,
        )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def charter_to_markdown(charter: ProjectCharter) -> str:
    """Render *charter* as readable Markdown (PRD §4.1 acceptance criterion).

    ``agent_baton.core.manager.artifacts.write_all`` lazy-imports this
    function by exactly this name/signature -- do not rename.
    """
    lines: list[str] = [f"# Project Charter: {charter.title or charter.task_id}", ""]

    lines.append("## Objective")
    lines.append(charter.objective or "_Not specified._")
    lines.append("")

    lines.append("## Background")
    lines.append(charter.background or "_Not specified._")
    lines.append("")

    for heading, items in (
        ("In Scope", charter.in_scope),
        ("Out of Scope", charter.out_of_scope),
        ("Assumptions", charter.assumptions),
        ("Constraints", charter.constraints),
        ("Risks", charter.risks),
        ("Manager Decision Points", charter.manager_decision_points),
        ("Success Criteria", charter.success_criteria),
        ("Likely Repo Areas", charter.likely_repo_areas),
    ):
        lines.append(f"## {heading}")
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("_None recorded._")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Field derivation helpers
# ---------------------------------------------------------------------------

def _sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def _background(plan: "MachinePlan") -> str:
    task_type = plan.task_type or "general"
    stack = plan.detected_stack or "an unspecified stack"
    return (
        f"This is a {plan.complexity}-complexity {task_type} task "
        f"(risk: {plan.risk_level}) targeting {stack}."
    )


def _in_scope(plan: "MachinePlan") -> list[str]:
    items: list[str] = []
    for phase in plan.phases:
        if phase.name and phase.name not in items:
            items.append(phase.name)
    for step in plan.all_steps:
        for deliverable in step.deliverables:
            if deliverable not in items:
                items.append(deliverable)
    return items


def _out_of_scope(config: "ManagerConfig") -> list[str]:
    items = ["Repo areas outside the scope map", "Unrelated refactors"]
    if config.scoping.allow_cross_scope_edits == "block":
        items.append("Any cross-workstream edits (blocked by scoping policy)")
    elif config.scoping.allow_cross_scope_edits == "manager_approval":
        items.append("Cross-workstream edits without manager approval")
    if config.scoping.out_of_scope_policy == "block_or_escalate":
        items.append(
            "Anything not explicitly listed in a workstream's allowed paths"
        )
    return items


def _constraints(config: "ManagerConfig", plan: "MachinePlan") -> list[str]:
    constraints: list[str] = []
    max_agents = config.team.max_agents_by_complexity.get(plan.complexity)
    if max_agents:
        constraints.append(
            f"Team size capped at {max_agents} agents for "
            f"{plan.complexity}-complexity work "
            "(config: team.max_agents_by_complexity)."
        )
    if config.scoping.require_allowed_paths:
        constraints.append(
            "Each workstream declares allowed_paths; agents may not edit "
            "outside them without manager approval."
        )
    if config.scoping.require_scope_contracts:
        constraints.append(
            "Every non-trivial step requires a scope contract before dispatch."
        )
    return constraints


def _insight_description(insight: object) -> str:
    if isinstance(insight, dict):
        return str(insight.get("description", "")).strip()
    return str(getattr(insight, "description", "") or "").strip()


def _risks(plan: "MachinePlan") -> list[str]:
    risks: list[str] = []
    if plan.risk_level:
        risks.append(f"Overall plan risk classified as {plan.risk_level}.")
    for insight in plan.foresight_insights:
        description = _insight_description(insight)
        if description:
            risks.append(description)
    return risks


def _success_criteria(plan: "MachinePlan") -> list[str]:
    criteria: list[str] = []
    for phase in plan.phases:
        if phase.gate is not None and phase.gate.command:
            criteria.append(f"Gate passes: `{phase.gate.command}`")
    criteria.append("All workstream deliverables produced")
    return criteria


def _base_assumption(areas_are_assumed: bool) -> str:
    if areas_are_assumed:
        return (
            "No repo areas could be confidently inferred from planned "
            "steps or the task summary; likely_repo_areas is left empty "
            "rather than guessing paths."
        )
    return (
        "Scope, in-scope areas, and likely repo areas below are inferred "
        "from the plan's phases/steps and repo signals; confirm with the "
        "director if this differs from intent."
    )


def _first_segment(path_str: str) -> str:
    normalized = path_str.strip().replace("\\", "/").strip("/")
    if not normalized:
        return ""
    return normalized.split("/", 1)[0]


def _likely_repo_areas(
    plan: "MachinePlan", summary: str, project_root: Path
) -> tuple[list[str], bool]:
    """Derive candidate repo areas without inventing paths.

    Preference order:

    1. First path segment of every step's ``allowed_paths``/
       ``context_files`` (the planner already knows real repo paths).
    2. Top-level directories of *project_root* whose name appears as a
       whole word in the task summary (a repo signal independent of the
       planner).
    3. Empty -- the caller records an assumption instead of guessing.

    Returns ``(areas, was_assumed)``; ``was_assumed`` is ``True`` only for
    the empty-list case (step 3), so the caller knows to record an
    assumption rather than silently shipping an empty list.
    """
    segments: list[str] = []
    for step in plan.all_steps:
        for raw_path in (*step.allowed_paths, *step.context_files):
            segment = _first_segment(raw_path)
            if segment and segment not in segments:
                segments.append(segment)
    if segments:
        return segments, False

    words = {w.lower() for w in _WORD_RE.findall(summary)}
    found: list[str] = []
    if project_root.is_dir():
        for entry in sorted(project_root.iterdir()):
            if (
                entry.is_dir()
                and not entry.name.startswith(".")
                and entry.name.lower() in words
            ):
                found.append(entry.name)
    if found:
        return found, False

    return [], True


def _risk_at_least(risk_level: str, threshold: str) -> bool:
    return _RISK_ORDER.get((risk_level or "LOW").upper(), 0) >= _RISK_ORDER.get(
        threshold.upper(), 0
    )


def _ambiguity(
    plan: "MachinePlan", summary: str, likely_repo_areas: list[str]
) -> tuple[bool, bool]:
    """Heuristic deciding whether the charter should flag scope ambiguity
    and whether that ambiguity is "high impact" enough to surface a
    manager decision point.

    A task is **ambiguous** when either:

    * the task summary is short (fewer than 8 words) -- not enough detail
      to pin down scope with confidence, or
    * no repo areas could be inferred (``likely_repo_areas`` empty) from
      planned steps or repo signals.

    An ambiguous task is **high impact** when the plan is also
    ``medium``/``heavy`` complexity, or its risk level is ``MEDIUM`` or
    above -- i.e. getting the scope wrong would be expensive to unwind.
    Callers gate the resulting manager decision point on
    ``config.manager_mode.ambiguity_policy`` (``ask_when_high_impact`` or
    ``always_ask`` escalate; ``record_and_continue`` does not).

    Returns ``(ambiguous, high_impact)``.
    """
    ambiguous = len(summary.split()) < 8 or not likely_repo_areas
    high_impact = ambiguous and (
        plan.complexity in ("medium", "heavy") or _risk_at_least(plan.risk_level, "MEDIUM")
    )
    return ambiguous, high_impact
