"""``TeamBlueprintBuilder`` -- manager-mode team composition and role cards.

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 6 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §10.3-10.4,
§14 ("Team Blueprint and Role Cards"), §16 Milestone 3.

``TeamBlueprintBuilder(config).build(scope_map, plan)`` derives:

- one :class:`~agent_baton.models.manager.RoleCard` per role selected for
  the team (every distinct ``agent_name`` on *plan*'s steps, plus any role
  introduced by the specialist-diversification rule below, plus the
  configured adversarial-review role when policy requires it);
- a :class:`~agent_baton.models.manager.TeamBlueprint` tying those roles to
  workstream ownership, collaboration/escalation rules, and the
  phase/project policy snapshot that produced this team.

Design decisions taken where the implementation plan left latitude
(documented here so Wave 3 composition and future maintainers don't have
to reverse-engineer them from behavior):

1. **Workstream ownership baseline.** A workstream's owner is
   ``workstream.owner_role`` when the upstream :class:`ScopeMapBuilder`
   (M2) has already set it. Only when that is empty do we fall back to
   the modal ``agent_name`` among the positionally-aligned plan phase's
   steps (``scope_map.workstreams[i]`` aligned with ``plan.phases[i]`` --
   the scope map is built one workstream per phase, per the M2 design).
2. **Specialist diversification** (``team.prefer_specialists_over_generalists``,
   skipped when ``plan.complexity == "light"``): when one role ends up
   owning more than one workstream, each of that role's workstreams is
   reassigned to the highest-priority *unused* role in
   ``agent_baton.core.engine.planning.rules.phase_roles.PHASE_IDEAL_ROLES``
   for the workstream's phase, keyed by ``workstream.name.lower()`` --
   mirroring exactly how ``planning.stages.routing``/``phase_builder``
   look up that table (``PHASE_IDEAL_ROLES.get(phase.name.lower(), [])``).
   This assumes ``ScopeMapBuilder`` sets ``Workstream.name`` to the
   originating phase's ``name`` (the natural 1:1 mapping given "one
   workstream per phase"); Wave 3 should confirm this against the actual
   M2 implementation before relying on it in composition.
3. **Required knowledge packs** attach only to roles whose *effective*
   step_type is ``"developing"`` or ``"testing"``: the role's own step's
   ``step_type`` when it appears on a plan step, else the fallback in
   ``agent_baton.core.engine.planning.rules.step_types.AGENT_STEP_TYPE``
   (default ``"developing"`` for unknown agents, matching that module's
   documented fallback). This is a deliberate exception to the
   "required_knowledge_packs must always be non-empty" reading of the
   role-card template (spec §14.2): a pure-planning role (e.g.
   ``architect``, step_type ``"planning"``) legitimately gets ``[]``.
4. **Role-card "Handoff Requirements" section renders
   ``RoleCard.expected_handoffs``** (this builder sets it to
   ``"handoff to <owner of the next dependent workstream>"`` entries),
   not the literal illustrative checklist text in the spec §14.2 example
   ("changed files", "decisions made", ...). The template's six section
   *headers*, in order, are what's normative; per-role content is
   necessarily data-driven.
5. **The adversarial-review role card is a fixed template**
   (mission/owns/does_not_own from the plan's Task 6 instructions
   verbatim) with ``required_knowledge_packs=["review-rubric"]`` -- not
   computed from the registry (that's M5's ``KnowledgePlanBuilder``) but
   matching the PRD §20 example brief ("review-rubric -- required for
   adversarial review").

Determinism: all iteration is over ordered lists (``plan.phases``,
``phase.steps``, ``scope_map.workstreams``) or dicts built by inserting in
that same order -- never over a ``set`` -- so output ordering is stable
across runs for the same input.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.rules.phase_roles import PHASE_IDEAL_ROLES
from agent_baton.core.engine.planning.rules.step_types import AGENT_STEP_TYPE
from agent_baton.models.manager import RoleCard, TeamBlueprint

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.models.execution import MachinePlan, PlanPhase
    from agent_baton.models.manager import ScopeMap, Workstream

# Fixed additions to every implementation role's "Does Not Own" list,
# verbatim from docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 6.
_DOES_NOT_OWN_EXTRAS: tuple[str, ...] = (
    "product requirements",
    "final adversarial review",
    "unrelated refactors",
)

# Fixed "Escalation Triggers" every role card carries, verbatim from
# spec §14.2's role-card template.
_ROLE_ESCALATION_TRIGGERS: tuple[str, ...] = (
    "required change crosses assigned path boundary",
    "test strategy is missing",
    "API contract ambiguity blocks implementation",
    "design assumption appears wrong",
)

# Team-level (blueprint, not per-role) escalation triggers.
_TEAM_ESCALATION_TRIGGERS: tuple[str, ...] = (
    "scope expansion beyond an assigned workstream",
    "knowledge gap blocks a workstream",
    "adversarial review veto",
    "director decision required",
)

# step_types that count as "implementation" for the required-knowledge-
# packs rule (design decision #3 above).
_IMPLEMENTATION_STEP_TYPES: frozenset[str] = frozenset({"developing", "testing"})

_REVIEW_ROLE_KNOWLEDGE_PACKS: tuple[str, ...] = ("review-rubric",)


class TeamBlueprintBuilder:
    """Builds a :class:`TeamBlueprint` and one :class:`RoleCard` per role."""

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(
        self, scope_map: "ScopeMap", plan: "MachinePlan"
    ) -> tuple[TeamBlueprint, dict[str, RoleCard]]:
        """Build the team blueprint and role cards for *plan*/*scope_map*."""
        plan_roles = self._collect_plan_roles(plan)
        workstream_owner = self._initial_owners(scope_map, plan, plan_roles)
        workstream_owner = self._diversify_specialists(
            scope_map, workstream_owner, plan.complexity
        )

        role_order: list[str] = list(plan_roles)
        for role in workstream_owner.values():
            if role and role not in role_order:
                role_order.append(role)

        role_cards: dict[str, RoleCard] = {
            role: self._build_role_card(role, scope_map, workstream_owner, plan)
            for role in role_order
        }

        review_role = self._review_role_name()
        if review_role is not None and review_role not in role_cards:
            role_cards[review_role] = self._build_review_role_card(review_role)
            role_order.append(review_role)

        blueprint = TeamBlueprint(
            task_id=plan.task_id,
            team_name=self._team_name(plan),
            mission=(plan.task_summary or "").strip(),
            roles=[role_cards[role] for role in role_order],
            workstream_assignments=dict(workstream_owner),
            collaboration_rules=self._collaboration_rules(),
            escalation_triggers=list(_TEAM_ESCALATION_TRIGGERS),
            phase_policies=self._phase_policies(),
        )
        return blueprint, role_cards

    # ------------------------------------------------------------------
    # Role collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_plan_roles(plan: "MachinePlan") -> list[str]:
        """Unique ``agent_name``s across *plan*'s steps, first-seen order."""
        roles: list[str] = []
        for phase in plan.phases:
            for step in phase.steps:
                if step.agent_name and step.agent_name not in roles:
                    roles.append(step.agent_name)
        return roles

    # ------------------------------------------------------------------
    # Workstream ownership
    # ------------------------------------------------------------------

    def _initial_owners(
        self, scope_map: "ScopeMap", plan: "MachinePlan", plan_roles: list[str]
    ) -> dict[str, str]:
        owners: dict[str, str] = {}
        for index, ws in enumerate(scope_map.workstreams):
            if ws.owner_role:
                owners[ws.id] = ws.owner_role
                continue
            fallback = plan_roles[0] if plan_roles else "claude"
            if index < len(plan.phases):
                owners[ws.id] = self._modal_agent(plan.phases[index]) or fallback
            else:
                owners[ws.id] = fallback
        return owners

    @staticmethod
    def _modal_agent(phase: "PlanPhase") -> str:
        """Most-common ``agent_name`` among *phase*'s steps.

        Ties broken by first appearance (``max`` over an order-preserving
        list scans left-to-right and only replaces on strictly-greater
        counts, so the first-seen name wins any tie).
        """
        counts: dict[str, int] = {}
        order: list[str] = []
        for step in phase.steps:
            if not step.agent_name:
                continue
            if step.agent_name not in counts:
                counts[step.agent_name] = 0
                order.append(step.agent_name)
            counts[step.agent_name] += 1
        if not order:
            return ""
        return max(order, key=lambda name: counts[name])

    def _diversify_specialists(
        self,
        scope_map: "ScopeMap",
        workstream_owner: dict[str, str],
        complexity: str,
    ) -> dict[str, str]:
        """Reassign owners so one role doesn't own every workstream.

        See design decision #2 in the module docstring.
        """
        if not self.config.team.prefer_specialists_over_generalists:
            return workstream_owner
        if complexity == "light":
            return workstream_owner

        by_owner: dict[str, list["Workstream"]] = {}
        for ws in scope_map.workstreams:
            by_owner.setdefault(workstream_owner.get(ws.id, ""), []).append(ws)

        result = dict(workstream_owner)
        for owner_role, workstreams in by_owner.items():
            if len(workstreams) <= 1:
                continue
            used_roles = {owner_role}
            for ws in workstreams:
                ideal_roles = PHASE_IDEAL_ROLES.get(ws.name.lower(), [])
                candidate = next((r for r in ideal_roles if r not in used_roles), None)
                if candidate:
                    result[ws.id] = candidate
                    used_roles.add(candidate)
        return result

    # ------------------------------------------------------------------
    # Role cards
    # ------------------------------------------------------------------

    def _build_role_card(
        self,
        role: str,
        scope_map: "ScopeMap",
        workstream_owner: dict[str, str],
        plan: "MachinePlan",
    ) -> RoleCard:
        owned = [ws for ws in scope_map.workstreams if workstream_owner.get(ws.id) == role]
        owned_ids = {ws.id for ws in owned}
        owned_names = [ws.name or ws.id for ws in owned]
        other_names = [ws.name or ws.id for ws in scope_map.workstreams if ws.id not in owned_ids]

        owns: list[str] = []
        for ws in owned:
            for deliverable in ws.deliverables:
                if deliverable not in owns:
                    owns.append(deliverable)
        if not owns:
            owns = [f"{name} deliverables" for name in owned_names] or [
                f"{role} contributions to the plan"
            ]

        does_not_own: list[str] = []
        for name in other_names:
            if name not in does_not_own:
                does_not_own.append(name)
        does_not_own.extend(_DOES_NOT_OWN_EXTRAS)

        step_type = self._role_step_type(role, plan)
        required_packs = (
            list(self.config.knowledge_packs.required_for_code_steps)
            if step_type in _IMPLEMENTATION_STEP_TYPES
            else []
        )

        handoffs: list[str] = []
        for ws in scope_map.workstreams:
            if ws.id in owned_ids:
                continue
            if any(dep in owned_ids for dep in ws.dependencies):
                dependent_owner = workstream_owner.get(ws.id)
                if dependent_owner and dependent_owner != role:
                    entry = f"handoff to {dependent_owner}"
                    if entry not in handoffs:
                        handoffs.append(entry)

        mission = (
            f"Own {', '.join(owned_names)}." if owned_names else f"Own the {role} role."
        )

        return RoleCard(
            role=role,
            agent_name=role,
            mission=mission,
            owns=owns,
            does_not_own=does_not_own,
            required_knowledge_packs=required_packs,
            default_context_budget=self.config.context.default_step_token_budget,
            expected_handoffs=handoffs,
            escalation_triggers=list(_ROLE_ESCALATION_TRIGGERS),
        )

    @staticmethod
    def _role_step_type(role: str, plan: "MachinePlan") -> str:
        for phase in plan.phases:
            for step in phase.steps:
                if step.agent_name == role:
                    return step.step_type
        return AGENT_STEP_TYPE.get(role, "developing")

    # ------------------------------------------------------------------
    # Adversarial review role
    # ------------------------------------------------------------------

    def _review_role_name(self) -> str | None:
        """Name of the configured adversarial-review role, or ``None``.

        Injected when phase-completion review isn't ``off``, or when
        project-completion review is ``always`` -- matching spec §14.3
        ("always: add review step after every completed phase" /
        "off: do not inject") plus the project-completion escape hatch
        from the Task 6 implementation notes.
        """
        phase_policy = self.config.policies.phase_completion.adversarial_review
        project_policy = self.config.policies.project_completion.adversarial_review
        if phase_policy != "off" or project_policy == "always":
            return self.config.policies.review_agents.adversarial_review
        return None

    def _build_review_role_card(self, role: str) -> RoleCard:
        return RoleCard(
            role=role,
            agent_name=role,
            mission="Adversarial phase review",
            owns=["phase review verdicts"],
            does_not_own=["implementation"],
            required_knowledge_packs=list(_REVIEW_ROLE_KNOWLEDGE_PACKS),
            default_context_budget=self.config.context.default_step_token_budget,
            expected_handoffs=["handoff to project completion review"],
            escalation_triggers=list(_ROLE_ESCALATION_TRIGGERS),
        )

    # ------------------------------------------------------------------
    # Team-level fields
    # ------------------------------------------------------------------

    @staticmethod
    def _team_name(plan: "MachinePlan") -> str:
        label = (plan.task_type or "delivery").replace("_", " ").replace("-", " ").strip()
        return f"{label.title()} Team" if label else "Delivery Team"

    def _collaboration_rules(self) -> list[str]:
        return [
            "Workstream owners hand off via phase handoff artifacts, not ad-hoc messages.",
            "Cross-workstream edits require the owning role's acknowledgement before merge.",
            f"Scope expansions are routed per policy: {self.config.scoping.scope_expansion_policy}.",
        ]

    def _phase_policies(self) -> dict[str, object]:
        return {
            "adversarial_review": self.config.policies.phase_completion.adversarial_review,
            "handoff_required": self.config.policies.phase_completion.handoff_required,
            "gates": self.config.policies.phase_completion.gates,
            "project_completion_adversarial_review": (
                self.config.policies.project_completion.adversarial_review
            ),
        }
