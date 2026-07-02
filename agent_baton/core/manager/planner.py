"""Manager-mode post-processor around ``IntelligentPlanner.create_plan()``.

See docs/internal/manager-mode-pmo-design.md ("Architecture") and
docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 4 and Wave 3 / Task 11.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.manager.artifacts import ManagerArtifacts, write_all, write_text
from agent_baton.core.manager.context_bundles import (
    ContextBundleBuilder,
    ScopeContractBuilder,
    contract_to_markdown,
    is_nontrivial_step,
)
from agent_baton.core.manager.charter import ProjectCharterBuilder
from agent_baton.core.manager.enrich import maybe_enrich_charter
from agent_baton.core.manager.knowledge_plan import KnowledgePlanBuilder
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.phase_policy import PhasePolicyApplier
from agent_baton.core.manager.reports import ManagerReportBuilder
from agent_baton.core.manager.role_cards import render_role_card
from agent_baton.core.manager.scope import ScopeMapBuilder
from agent_baton.core.manager.team_blueprint import TeamBlueprintBuilder
from agent_baton.models.manager import RoleCard, Workstream as _WorkstreamModel

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
    from agent_baton.models.execution import MachinePlan, PlanStep
    from agent_baton.models.manager import KnowledgePlan, ScopeMap, TeamBlueprint, Workstream

logger = logging.getLogger(__name__)

# A workstream-shaped stand-in for the rare defensive case where a step's
# phase has no positionally-aligned Workstream (should not happen given
# ScopeMapBuilder always builds one workstream per plan phase -- see
# ManagerModePlanner._compose -- but ScopeContractBuilder.build requires a
# non-None Workstream, so this keeps that call site total).
_EMPTY_WORKSTREAM = _WorkstreamModel()


class ManagerModePlanner:
    """Post-processor that turns a finished :class:`MachinePlan` into the
    full manager-mode PMO artifact set.

    Composition order (binding -- see docs/internal/manager-mode-pmo-plan.md
    Task 4's docstring and Task 11's ``test_composition_order``; do not
    reorder):

        charter -> (optional LLM enrichment, wired HERE, never inside a
        builder) -> scope map -> blueprint + role cards -> knowledge plan
        -> ``PhasePolicyApplier.apply`` (the only plan mutation -- injects
        adversarial-review steps) -> scope contracts + context bundles
        over the FINAL step list (so injected review steps get both, too)
        -> manager brief -> ``write_all``.

    Knowledge plan is built *before* the policy applier runs, so injected
    review steps never existed when ``KnowledgePlanBuilder`` iterated
    ``plan.phases`` -- they pick up required knowledge exclusively via
    their role card's ``required_knowledge_packs`` (e.g. ``review-rubric``
    for the adversarial-review role), never via
    ``knowledge_packs.required_for_code_steps`` (gated on
    ``step_type in ("developing", "testing")`` upstream in both
    ``TeamBlueprintBuilder`` and ``KnowledgePlanBuilder`` -- review steps
    are ``step_type="reviewing"``).

    Ownership authority for resolving a step's :class:`RoleCard` is
    :attr:`TeamBlueprint.workstream_assignments` -- never
    ``Workstream.owner_role``, never a bare ``step.agent_name`` lookup
    performed independently of the blueprint. See
    :func:`_resolve_role_card` for the exact two-branch rule (primary:
    the phase's workstream owner when the step's agent matches that
    owner; fallback: the role card matching ``step.agent_name`` directly
    -- this is how injected review steps, whose agent is the configured
    review role rather than the phase's owner, get the *review* role's
    card instead of the phase owner's).

    Calling convention (enforced by the caller, not this class): callers
    invoke :meth:`build_and_write` only when the plan itself is being
    persisted (``baton plan --save``); for a preview (``--dry-run``) they
    call :meth:`build` alone so nothing is written to disk. ``build()``
    never touches the filesystem -- as a consequence, a step's own scope
    contract / role card have not been written yet when its context
    bundle is assembled during a dry-run preview, so their ``must_read``
    token estimates come back ``0`` with a "missing file" truncation
    warning (truthful: those files genuinely do not exist yet in a
    preview). :meth:`build_and_write` avoids this by writing each scope
    contract's Markdown sidecar and each role card's Markdown -- the two
    ``must_read`` entries every bundle carries for itself -- to disk
    *before* building that step's bundle, so real-run token accounting is
    accurate. ``write_all`` still performs the authoritative final write
    pass (including the JSON contracts, which are never pre-written) --
    the early write is a superset-safe, idempotent head start solely for
    token-estimation accuracy.
    """

    def __init__(
        self,
        config: "ManagerConfig",
        *,
        project_root: Path,
        team_context_dir: Path,
        knowledge_registry: "KnowledgeRegistry | None" = None,
        cli_gate_scope_explicit: bool = False,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.team_context_dir = Path(team_context_dir)
        self.cli_gate_scope_explicit = cli_gate_scope_explicit
        self._knowledge_registry = knowledge_registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, plan: "MachinePlan", task_summary: str) -> ManagerArtifacts:
        """Build every PMO artifact for *plan*. Never writes to disk."""
        return self._compose(plan, task_summary, persist_sidecars_early=False)

    def build_and_write(self, plan: "MachinePlan", task_summary: str) -> ManagerArtifacts:
        """Build every PMO artifact for *plan* and persist it via ``write_all``."""
        paths = self._paths(plan)
        artifacts = self._compose(
            plan, task_summary, persist_sidecars_early=True, paths=paths
        )
        write_all(paths, artifacts)
        return artifacts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _paths(self, plan: "MachinePlan") -> ManagerArtifactPaths:
        # Resolve eagerly so every derived sidecar path is absolute --
        # token estimation in ContextBundleBuilder is cwd-relative
        # otherwise (see docs/internal/manager-mode-pmo-plan.md Task 11
        # binding notes).
        return ManagerArtifactPaths(self.team_context_dir.resolve(), plan.task_id)

    def _registry(self) -> "KnowledgeRegistry":
        if self._knowledge_registry is None:
            from agent_baton.core.orchestration.knowledge_registry import (
                KnowledgeRegistry,
            )

            registry = KnowledgeRegistry()
            registry.load_default_paths()
            self._knowledge_registry = registry
        return self._knowledge_registry

    def _compose(
        self,
        plan: "MachinePlan",
        task_summary: str,
        *,
        persist_sidecars_early: bool,
        paths: ManagerArtifactPaths | None = None,
    ) -> ManagerArtifacts:
        config = self.config
        paths = paths or self._paths(plan)
        artifacts = ManagerArtifacts()

        # charter -> optional LLM enrichment (wired here, not in the builder)
        charter = ProjectCharterBuilder(config).build(plan, task_summary, self.project_root)
        charter = maybe_enrich_charter(charter, task_summary)
        artifacts.charter = charter

        # scope map
        scope_map = ScopeMapBuilder(config).build(charter, plan)
        artifacts.scope_map = scope_map

        # Positional phase -> workstream correspondence (ScopeMapBuilder
        # builds exactly one Workstream per plan.phases entry, in order --
        # see scope.py). Captured now, before the policy applier runs, but
        # remains valid afterwards: PhasePolicyApplier only appends steps
        # to existing phases, it never adds/removes/reorders phases.
        workstream_by_phase_id: dict[int, "Workstream"] = {
            phase.phase_id: ws for phase, ws in zip(plan.phases, scope_map.workstreams)
        }

        # blueprint + role cards
        blueprint, role_cards = TeamBlueprintBuilder(config).build(scope_map, plan)
        artifacts.blueprint = blueprint
        artifacts.role_cards_md = {
            role: render_role_card(card) for role, card in role_cards.items()
        }

        # knowledge plan -- built BEFORE the policy applier runs (see class
        # docstring): injected review steps do not exist yet, so they can
        # never pick up `required_for_code_steps` packs via per-step
        # attachment; they get knowledge exclusively through their role
        # card's `required_knowledge_packs`.
        knowledge_plan = KnowledgePlanBuilder(config, self._registry()).build(
            plan, [card.role for card in blueprint.roles]
        )
        artifacts.knowledge_plan = knowledge_plan

        # PhasePolicyApplier.apply -- the ONLY plan mutation (injects
        # adversarial-review steps; optionally rescales gates).
        decisions = PhasePolicyApplier(config).apply(
            plan, cli_gate_scope_explicit=self.cli_gate_scope_explicit
        )
        # Fold the *actual* policy decisions into the blueprint so the
        # brief's "Configured Policies" section reports what really ran
        # (e.g. gate_scope_applied), not just the static config values.
        blueprint.phase_policies = {
            **blueprint.phase_policies,
            "gate_scope_applied": decisions.gate_scope_applied,
            "injected_review_steps": list(decisions.injected_review_steps),
            "final_review_step": decisions.final_review_step,
        }

        # scope contracts + context bundles over the FINAL step list, so
        # injected review steps get both too.
        self._build_contracts_and_bundles(
            plan,
            paths=paths,
            scope_map=scope_map,
            blueprint=blueprint,
            role_cards=role_cards,
            knowledge_plan=knowledge_plan,
            workstream_by_phase_id=workstream_by_phase_id,
            artifacts=artifacts,
            persist_sidecars_early=persist_sidecars_early,
        )

        # manager brief
        report_builder = ManagerReportBuilder(config, paths)
        artifacts.brief_md = report_builder.build_brief(artifacts, plan)

        return artifacts

    def _build_contracts_and_bundles(
        self,
        plan: "MachinePlan",
        *,
        paths: ManagerArtifactPaths,
        scope_map: "ScopeMap",
        blueprint: "TeamBlueprint",
        role_cards: dict[str, RoleCard],
        knowledge_plan: "KnowledgePlan",
        workstream_by_phase_id: dict[int, "Workstream"],
        artifacts: ManagerArtifacts,
        persist_sidecars_early: bool,
    ) -> None:
        config = self.config
        include_prior_handoff = config.context.include_prior_phase_handoff
        written_role_cards: set[str] = set()

        for phase_index, phase in enumerate(plan.phases):
            workstream = workstream_by_phase_id.get(phase.phase_id)

            prior_handoff_paths: list[str] = []
            if include_prior_handoff:
                prior_handoff_paths = [
                    str(paths.phase_handoff(prior_phase.phase_id))
                    for prior_phase in plan.phases[:phase_index]
                ]

            for step in phase.steps:
                if not is_nontrivial_step(step):
                    continue

                role_card = _resolve_role_card(
                    step, workstream, blueprint, role_cards, artifacts.warnings
                )
                if role_card.role not in artifacts.role_cards_md:
                    artifacts.role_cards_md[role_card.role] = render_role_card(role_card)

                contract_workstream = workstream if workstream is not None else _EMPTY_WORKSTREAM
                contract = ScopeContractBuilder(config).build(
                    step, contract_workstream, role_card, scope_map=scope_map
                )
                contract_md = contract_to_markdown(contract)
                artifacts.scope_contracts[step.step_id] = contract
                artifacts.scope_contracts_md[step.step_id] = contract_md

                contract_md_path = paths.scope_contract(step.step_id, ext="md")
                role_card_path = paths.role_card(role_card.role)

                if persist_sidecars_early:
                    write_text(contract_md_path, contract_md)
                    if role_card.role not in written_role_cards:
                        write_text(
                            role_card_path, artifacts.role_cards_md[role_card.role]
                        )
                        written_role_cards.add(role_card.role)

                bundle = ContextBundleBuilder(config).build(
                    step,
                    contract_md_path,
                    role_card,
                    knowledge_plan,
                    prior_handoff_paths,
                    role_card_path=role_card_path,
                    task_id=plan.task_id,
                )
                artifacts.context_bundles[step.step_id] = bundle


def _resolve_role_card(
    step: "PlanStep",
    workstream: "Workstream | None",
    blueprint: "TeamBlueprint",
    role_cards: dict[str, RoleCard],
    warnings: list[str],
) -> RoleCard:
    """Resolve the :class:`RoleCard` that owns *step*'s dispatch context.

    Primary: when *step*'s agent IS the assigned owner of its phase's
    workstream (``TeamBlueprint.workstream_assignments`` -- the sole
    ownership authority, never ``Workstream.owner_role``), use that
    owner's card.

    Fallback: otherwise (e.g. an injected review step, whose agent is the
    configured review role rather than the phase's owner) use the role
    card matching ``step.agent_name`` directly -- every agent named on any
    plan step, plus the review role when policy adds one, always has a
    card (``TeamBlueprintBuilder`` guarantees this), so this fallback
    covers "steps outside workstream ownership" in practice.

    A synthetic minimal card is only ever built as a last-resort safety
    net (should not occur given the guarantee above) and is recorded in
    *warnings*.
    """
    owner_role = ""
    if workstream is not None:
        owner_role = blueprint.workstream_assignments.get(workstream.id, "")

    if step.agent_name and step.agent_name == owner_role:
        card = role_cards.get(owner_role)
        if card is not None:
            return card

    card = role_cards.get(step.agent_name)
    if card is not None:
        return card

    if owner_role:
        card = role_cards.get(owner_role)
        if card is not None:
            return card

    warnings.append(
        f"No role card found for step {step.step_id!r} (agent "
        f"{step.agent_name!r}); using a minimal fallback card."
    )
    return RoleCard(
        role=step.agent_name,
        agent_name=step.agent_name,
        mission=f"Own the {step.agent_name or 'unassigned'} role.",
    )
