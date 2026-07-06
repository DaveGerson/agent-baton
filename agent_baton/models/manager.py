"""PMO (manager-mode) domain models — spec: docs/internal/manager-mode-pmo-design.md.

Sidecar Pydantic models for the manager-mode post-processor
(``agent_baton.core.manager.planner.ManagerModePlanner``). These are
JSON-round-trippable via ``to_dict``/``from_dict`` and are persisted as
sidecar artifacts under ``.claude/team-context/executions/<task_id>/``
(see ``agent_baton.core.manager.paths.ManagerArtifactPaths``) rather than
as fields on ``MachinePlan`` — see spec §10 "Data Models" and the plan
docstring rationale ("Prefer sidecar models initially to avoid
destabilizing MachinePlan").

Field-default convention (per docs/internal/manager-mode-pmo-plan.md
Wave 0 / Task 2): all list fields use ``Field(default_factory=list)``;
optional strings default to ``""``. The exceptions are
``ProjectCharter.task_id``/``objective`` and ``ManagerDecision.decision_type``,
which are required (no default) — see the docstrings on those fields.
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field


class ManagerModel(BaseModel):
    """Common base for PMO domain models.

    Mirrors the ``to_dict``/``from_dict`` convention used by
    ``agent_baton.core.config.manager.ManagerConfig``: ``to_dict`` dumps
    to a JSON-friendly ``dict``, ``from_dict`` re-validates through the
    constructor (Pydantic auto-hydrates nested ``ManagerModel`` fields
    from dicts). ``extra="ignore"`` gives forward-compat for sidecar
    artifacts written by a newer version of the planner.
    """

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**data)


class ProjectCharter(ManagerModel):
    """Manager-mode project charter (spec §10.1).

    ``task_id`` and ``objective`` are required — a charter without a task
    to anchor to or a stated objective is not useful and signals a
    builder bug rather than a legitimately "empty" charter.
    """

    task_id: str
    objective: str
    title: str = ""
    background: str = ""
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    manager_decision_points: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    likely_repo_areas: list[str] = Field(default_factory=list)


class Workstream(ManagerModel):
    """A unit of scoped work within a :class:`ScopeMap` (spec §10.2)."""

    id: str = ""
    name: str = ""
    objective: str = ""
    likely_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    owner_role: str = ""
    dependencies: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ScopeMap(ManagerModel):
    """Workstream decomposition of a manager-mode plan (spec §10.2)."""

    task_id: str = ""
    workstreams: list[Workstream] = Field(default_factory=list)
    cross_cutting_concerns: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    scope_expansion_policy: str = "queue_for_manager"


class RoleCard(ManagerModel):
    """A role's charter within a :class:`TeamBlueprint` (spec §10.4)."""

    role: str = ""
    agent_name: str = ""
    mission: str = ""
    owns: list[str] = Field(default_factory=list)
    does_not_own: list[str] = Field(default_factory=list)
    required_knowledge_packs: list[str] = Field(default_factory=list)
    default_context_budget: int = 12000
    expected_handoffs: list[str] = Field(default_factory=list)
    escalation_triggers: list[str] = Field(default_factory=list)


class TeamBlueprint(ManagerModel):
    """Ad-hoc team composition for a manager-mode plan (spec §10.3)."""

    task_id: str = ""
    team_name: str = ""
    mission: str = ""
    roles: list[RoleCard] = Field(default_factory=list)
    workstream_assignments: dict[str, str] = Field(default_factory=dict)
    collaboration_rules: list[str] = Field(default_factory=list)
    escalation_triggers: list[str] = Field(default_factory=list)
    phase_policies: dict[str, Any] = Field(default_factory=dict)


class ScopeContract(ManagerModel):
    """Per-step scope contract dispatched alongside a step (spec §10.5)."""

    step_id: str = ""
    agent_name: str = ""
    workstream_id: str = ""
    mission: str = ""
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    escalation_triggers: list[str] = Field(default_factory=list)


class ContextReference(ManagerModel):
    """A single document/file reference within a :class:`ContextBundle`."""

    path: str = ""
    kind: Literal["file", "doc", "handoff", "bead"] = "file"
    reason: str = ""
    token_estimate: int = 0


class KnowledgePackReference(ManagerModel):
    """A knowledge pack attached to a step, role, or bundle."""

    name: str = ""
    path: str = ""
    reason: str = ""
    confidence: str = "medium"
    status: str = "active"
    token_estimate: int = 0
    documents: list[str] = Field(default_factory=list)


class MissingKnowledgePack(ManagerModel):
    """A required/default knowledge pack absent from the registry."""

    name: str = ""
    reason: str = ""
    proposed_sources: list[str] = Field(default_factory=list)


class ContextBundle(ManagerModel):
    """Per-step context bundle assembled for dispatch (spec §10.6)."""

    task_id: str = ""
    step_id: str = ""
    agent_name: str = ""
    scope_contract_path: str = ""
    must_read: list[ContextReference] = Field(default_factory=list)
    reference_only: list[ContextReference] = Field(default_factory=list)
    knowledge_packs: list[KnowledgePackReference] = Field(default_factory=list)
    prior_handoffs: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    token_budget: int = 12000
    estimated_tokens: int = 0
    truncation_warnings: list[str] = Field(default_factory=list)


class KnowledgePlan(ManagerModel):
    """Plan-wide knowledge pack selection/gap analysis (spec §10.7)."""

    task_id: str = ""
    selected_packs: list[KnowledgePackReference] = Field(default_factory=list)
    missing_packs: list[MissingKnowledgePack] = Field(default_factory=list)
    stale_packs: list[str] = Field(default_factory=list)
    per_role_packs: dict[str, list[str]] = Field(default_factory=dict)
    per_step_packs: dict[str, list[str]] = Field(default_factory=dict)


class ManagerDecision(ManagerModel):
    """A director-facing decision packet (spec §10.8).

    ``decision_type`` is required — every decision must be classified so
    routing (scope-expansion queueing, ambiguity resolution, etc.) can
    dispatch on it; an unclassified decision is a builder bug.
    """

    decision_type: Literal[
        "scope_expansion", "ambiguity", "knowledge_gap", "review_veto", "approval"
    ]
    decision_id: str = ""
    task_id: str = ""
    summary: str = ""
    context: str = ""
    options: list[str] = Field(default_factory=list)
    recommended_option: str = ""
    created_at: str = ""
    resolved_at: str | None = None
    resolution: str | None = None
