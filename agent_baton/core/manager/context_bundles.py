"""``ScopeContractBuilder`` + ``ContextBundleBuilder`` -- per-step scope
discipline and dispatch context (M4).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 8 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §10.5-10.6,
§13 ("Scoping and Context Discipline"), §11.4 ("Prompt/dispatch
integration"), §16 Milestone 4.

Ownership rule (binding -- see the manager-mode-pmo-plan.md Task 8
"CRITICAL composition rule" from the Wave 1 review): workstream ownership
authority is ``TeamBlueprint.workstream_assignments``, never
``Workstream.owner_role`` and never ``step.agent_name`` (these diverge
after specialist diversification, see
``agent_baton.core.manager.team_blueprint``'s module docstring, design
decision #2). Both builders below take the resolved :class:`RoleCard` as
an explicit parameter and trust it for the dispatched agent's identity --
they never re-derive ownership from ``workstream.owner_role`` or
``step.agent_name`` internally. ``role_card.agent_name`` is preferred over
``step.agent_name`` for the artifacts' ``agent_name`` field; ``step.agent_name``
is only a defensive fallback for a role card built with an empty
``agent_name`` (should not happen in practice -- ``TeamBlueprintBuilder``
always sets it -- but ``RoleCard.agent_name`` defaults to ``""``).

Determinism: no clock reads, no randomness. Every list is built by
iterating over an already-ordered input (the caller's ``step``/
``workstream``/``role_card``/``knowledge_plan``/``prior_handoff_paths``)
or a fixed module-level tuple -- never over a ``set``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.models.manager import (
    ContextBundle,
    ContextReference,
    KnowledgePackReference,
    RoleCard,
    ScopeContract,
)

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.models.execution import PlanStep
    from agent_baton.models.manager import KnowledgePlan, ScopeMap, Workstream

# ---------------------------------------------------------------------------
# Nontrivial-step predicate
# ---------------------------------------------------------------------------

# Sentinel step_type values that mean "not a dispatched agent step" -- kept
# as a set (rather than a single literal) in case a future step_type joins
# "gate" as a non-agent marker. Confirmed against
# agent_baton/core/engine/planning/rules/step_types.py: no step_type value
# currently equals "gate" in the wild (gates are separate ``PlanGate``
# objects on ``PlanPhase.gate``), but ``PlanStep.step_type`` is a plain
# ``str`` field with no enum enforcement, so a future producer emitting a
# command-only "gate" step_type must still be excluded here.
_NON_AGENT_STEP_TYPES: frozenset[str] = frozenset({"gate"})


def is_nontrivial_step(step: "PlanStep") -> bool:
    """A step warrants a scope contract + context bundle.

    Per docs/internal/manager-mode-pmo-plan.md Task 8: has ``agent_name``,
    ``step_type`` not in :data:`_NON_AGENT_STEP_TYPES`, and no ``command``
    (a ``command``-bearing step runs a shell command directly rather than
    dispatching an agent -- there is no one to hand a scope contract to).
    """
    return (
        bool(step.agent_name)
        and step.step_type not in _NON_AGENT_STEP_TYPES
        and not step.command
    )


# ---------------------------------------------------------------------------
# ScopeContractBuilder
# ---------------------------------------------------------------------------

# Standard escalation triggers every scope contract carries, verbatim from
# spec §11.4's suggested prompt structure ("Escalation Triggers" / "Escalate
# if"). Distinct from ``team_blueprint._ROLE_ESCALATION_TRIGGERS`` -- these
# are the step-level (contract) triggers, not the role-level ones.
_STANDARD_ESCALATION_TRIGGERS: tuple[str, ...] = (
    "scope expansion needed",
    "knowledge gap blocks work",
    "assigned paths are insufficient",
    "design assumption appears invalid",
)

# Fixed "Definition of Done" items appended after step/workstream
# deliverables, verbatim from docs/internal/manager-mode-pmo-plan.md Task 8.
_FIXED_DEFINITION_OF_DONE: tuple[str, ...] = (
    "handoff summary written",
    "no unrelated refactors",
)

# Fixed "Out of Scope" items present on every contract even when no
# ``ScopeMap`` is supplied (see :meth:`ScopeContractBuilder.build`'s
# ``scope_map`` parameter) -- mirrors spec §13.1's example
# ("global refactors" / boundary-crossing changes).
_FIXED_OUT_OF_SCOPE: tuple[str, ...] = (
    "changes outside allowed paths",
    "unrelated refactors",
)


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication, dropping empty strings."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _first_sentence(text: str) -> str:
    """The first ``.``/``!``/``?``-terminated sentence of *text*.

    Falls back to the whole (stripped) string when no terminator is found
    (e.g. a short imperative task description with no punctuation).
    """
    stripped = text.strip()
    if not stripped:
        return ""
    for terminator in (". ", "! ", "? "):
        idx = stripped.find(terminator)
        if idx != -1:
            return stripped[: idx + 1].strip()
    if stripped[-1] in ".!?":
        return stripped
    return stripped


class ScopeContractBuilder:
    """Builds a :class:`ScopeContract` for one nontrivial step."""

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(
        self,
        step: "PlanStep",
        workstream: "Workstream",
        role_card: RoleCard,
        *,
        scope_map: "ScopeMap | None" = None,
    ) -> ScopeContract:
        """Build the contract for *step*, owned (per *role_card*) within
        *workstream*.

        *scope_map* is optional context beyond the three pinned positional
        parameters (see the module docstring's ownership rule): when
        supplied, its ``out_of_scope`` list and the names of sibling
        workstreams enrich ``out_of_scope``. Without it, ``out_of_scope``
        still resolves to the fixed defaults below -- it is never empty.
        """
        mission = _first_sentence(step.task_description) or f"Complete step {step.step_id}."

        in_scope = _dedupe(list(workstream.deliverables) + list(step.deliverables))
        if not in_scope:
            fallback = step.task_description.strip()
            in_scope = [fallback] if fallback else [f"Step {step.step_id} deliverables"]

        other_workstream_names: list[str] = []
        scope_map_out_of_scope: list[str] = []
        if scope_map is not None:
            scope_map_out_of_scope = list(scope_map.out_of_scope)
            for other in scope_map.workstreams:
                if other.id == workstream.id:
                    continue
                name = other.name or other.id
                if name:
                    other_workstream_names.append(f"{name} workstream")

        out_of_scope = _dedupe(
            scope_map_out_of_scope + other_workstream_names + list(_FIXED_OUT_OF_SCOPE)
        )

        allowed_paths = list(step.allowed_paths) or list(workstream.allowed_paths)

        definition_of_done = _dedupe(
            list(step.deliverables) + list(_FIXED_DEFINITION_OF_DONE)
        )

        escalation_triggers = _dedupe(
            list(role_card.escalation_triggers) + list(_STANDARD_ESCALATION_TRIGGERS)
        )

        return ScopeContract(
            step_id=step.step_id,
            agent_name=role_card.agent_name or step.agent_name,
            workstream_id=workstream.id,
            mission=mission,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
            allowed_paths=allowed_paths,
            expected_artifacts=list(step.deliverables),
            definition_of_done=definition_of_done,
            escalation_triggers=escalation_triggers,
        )


def _bullets(items: list[str]) -> list[str]:
    if not items:
        return ["- (none)"]
    return [f"- {item}" for item in items]


def contract_to_markdown(contract: ScopeContract) -> str:
    """Render *contract* as Markdown, following spec §13.1's template
    exactly: ``# Scope Contract: Step <step_id>`` followed by the
    ``## Mission`` / ``## In Scope`` / ``## Out of Scope`` /
    ``## Allowed Paths`` / ``## Definition of Done`` / ``## Escalate If``
    sections, in that order.
    """
    lines: list[str] = [f"# Scope Contract: Step {contract.step_id}", ""]

    lines.append("## Mission")
    lines.append(contract.mission or "(unspecified)")
    lines.append("")

    lines.append("## In Scope")
    lines.extend(_bullets(contract.in_scope))
    lines.append("")

    lines.append("## Out of Scope")
    lines.extend(_bullets(contract.out_of_scope))
    lines.append("")

    lines.append("## Allowed Paths")
    lines.extend(_bullets(contract.allowed_paths))
    lines.append("")

    lines.append("## Definition of Done")
    lines.extend(_bullets(contract.definition_of_done))
    lines.append("")

    lines.append("## Escalate If")
    lines.extend(_bullets(contract.escalation_triggers))
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# ContextBundleBuilder
# ---------------------------------------------------------------------------

# Priority rank for reference-only knowledge attachments, mirroring the
# resolver's own 4-layer pipeline (explicit > agent-declared > tag >
# relevance) plus the lowest tier, gap-suggested (see
# agent_baton/models/knowledge.py ``KnowledgeAttachment.source`` docstring).
# Lower rank == higher priority == kept longer under overflow. Unknown
# ``source`` strings sort last (rank 99) rather than raising, since this is
# a soft prioritization signal, not a contract.
_ATTACHMENT_PRIORITY: dict[str, int] = {
    "explicit": 0,
    "agent-declared": 1,
    "planner-matched:tag": 2,
    "planner-matched:relevance": 3,
    "gap-suggested": 4,
}


def _file_token_estimate(path_str: str) -> tuple[int, bool]:
    """``(chars // 4, found)`` for the file at *path_str*.

    Returns ``(0, False)`` when the path is empty, does not exist, or is
    unreadable -- callers decide whether/how to surface that as a warning.
    """
    if not path_str:
        return 0, False
    try:
        candidate = Path(path_str)
        if not candidate.is_file():
            return 0, False
        return candidate.stat().st_size // 4, True
    except OSError:
        return 0, False


class ContextBundleBuilder:
    """Builds a :class:`ContextBundle` for one nontrivial step.

    Assembles the bundle from the step's own context files, the scope
    contract just built for it, the owning role card's required knowledge
    packs, the plan-wide :class:`KnowledgePlan`'s per-step pack selection,
    and any prior phase handoff artifacts -- then enforces the token
    budget via :meth:`_apply_overflow` (see that method's docstring for the
    fixed drop order).
    """

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(
        self,
        step: "PlanStep",
        contract_path: "str | Path",
        role_card: RoleCard,
        knowledge_plan: "KnowledgePlan",
        prior_handoff_paths: "list[str] | None" = None,
        *,
        role_card_path: "str | Path | None" = None,
        task_id: str = "",
    ) -> ContextBundle:
        contract_path_str = str(contract_path)
        truncation_warnings: list[str] = []

        must_read = self._build_must_read(
            step, contract_path_str, role_card_path, truncation_warnings
        )
        reference_only = self._build_reference_only(step)
        knowledge_packs = self._build_knowledge_packs(
            step, role_card, knowledge_plan, truncation_warnings
        )
        handoff_items = self._build_handoff_items(prior_handoff_paths or [])

        total_tokens = (
            sum(r.token_estimate for r in must_read)
            + sum(r.token_estimate for r in reference_only)
            + sum(p.token_estimate for p in knowledge_packs)
            + sum(tokens for _path, tokens in handoff_items)
        )

        token_budget = (
            role_card.default_context_budget
            or self.config.context.default_step_token_budget
        )

        total_tokens = self._apply_overflow(
            reference_only, handoff_items, total_tokens, token_budget, truncation_warnings
        )

        return ContextBundle(
            task_id=task_id,
            step_id=step.step_id,
            agent_name=role_card.agent_name or step.agent_name,
            scope_contract_path=contract_path_str,
            must_read=must_read,
            reference_only=reference_only,
            knowledge_packs=knowledge_packs,
            prior_handoffs=[path for path, _tokens in handoff_items],
            decisions=[],
            constraints=list(role_card.does_not_own),
            token_budget=token_budget,
            estimated_tokens=total_tokens,
            truncation_warnings=truncation_warnings,
        )

    # ------------------------------------------------------------------
    # must_read / reference_only / knowledge_packs / handoffs
    # ------------------------------------------------------------------

    @staticmethod
    def _build_must_read(
        step: "PlanStep",
        contract_path_str: str,
        role_card_path: "str | Path | None",
        truncation_warnings: list[str],
    ) -> list[ContextReference]:
        must_read: list[ContextReference] = []

        contract_tokens, contract_found = _file_token_estimate(contract_path_str)
        if not contract_found:
            truncation_warnings.append(
                f"Missing file for token estimate: {contract_path_str}"
            )
        must_read.append(
            ContextReference(
                path=contract_path_str,
                kind="doc",
                reason="scope contract",
                token_estimate=contract_tokens,
            )
        )

        if role_card_path is not None:
            role_card_path_str = str(role_card_path)
            role_tokens, role_found = _file_token_estimate(role_card_path_str)
            if not role_found:
                truncation_warnings.append(
                    f"Missing file for token estimate: {role_card_path_str}"
                )
            must_read.append(
                ContextReference(
                    path=role_card_path_str,
                    kind="doc",
                    reason="role card",
                    token_estimate=role_tokens,
                )
            )

        for file_path in step.context_files:
            tokens, found = _file_token_estimate(file_path)
            if not found:
                truncation_warnings.append(f"Missing file for token estimate: {file_path}")
            must_read.append(
                ContextReference(
                    path=file_path,
                    kind="file",
                    reason="step context file",
                    token_estimate=tokens,
                )
            )

        return must_read

    @staticmethod
    def _build_reference_only(step: "PlanStep") -> list[ContextReference]:
        reference_only: list[ContextReference] = []
        for attachment in step.knowledge:
            if getattr(attachment, "delivery", "") != "reference":
                continue
            path = getattr(attachment, "path", "")
            token_estimate = getattr(attachment, "token_estimate", 0)
            if not token_estimate:
                token_estimate, _found = _file_token_estimate(path)
            reference_only.append(
                ContextReference(
                    path=path,
                    kind="doc",
                    reason=getattr(attachment, "source", ""),
                    token_estimate=token_estimate,
                )
            )
        # Highest priority first so overflow (which pops from the end) drops
        # the lowest-priority reference first. Stable sort preserves the
        # resolver's original relative order within a priority tier.
        reference_only.sort(key=lambda ref: _ATTACHMENT_PRIORITY.get(ref.reason, 99))
        return reference_only

    def _build_knowledge_packs(
        self,
        step: "PlanStep",
        role_card: RoleCard,
        knowledge_plan: "KnowledgePlan",
        truncation_warnings: list[str],
    ) -> list[KnowledgePackReference]:
        max_docs = self.config.context.max_knowledge_docs_per_step

        pack_names: list[str] = []
        for name in role_card.required_knowledge_packs:
            if name not in pack_names:
                pack_names.append(name)
        for name in knowledge_plan.per_step_packs.get(step.step_id, []):
            if name not in pack_names:
                pack_names.append(name)

        kept_names = pack_names[:max_docs]
        cut_names = pack_names[max_docs:]
        if cut_names:
            # F4.3 (Wave 2 review): the [:max_docs] cap can silently cut a
            # pack the project config marks required for every code step
            # (``config.knowledge_packs.required_for_code_steps``) -- that
            # is a config-driven guarantee, distinct from a role card's own
            # ``required_knowledge_packs``, so callers need a signal when
            # the cap breaks it.
            required_for_code_steps = set(self.config.knowledge_packs.required_for_code_steps)
            for name in cut_names:
                if name in required_for_code_steps:
                    truncation_warnings.append(
                        "Required knowledge pack dropped by max_knowledge_docs_per_step "
                        f"cap: {name}"
                    )

        packs_by_name = {pack.name: pack for pack in knowledge_plan.selected_packs}
        required = set(role_card.required_knowledge_packs)
        packs: list[KnowledgePackReference] = []
        for name in kept_names:
            if name in packs_by_name:
                packs.append(packs_by_name[name])
            else:
                # bd-t8u: a pack name the knowledge plan never selected
                # (canonical case: a role-required pack absent from the
                # registry, e.g. review-rubric) must NOT attach as a phantom
                # reference (path="", token_estimate=0) -- the knowledge
                # plan already reports it under ``missing_packs``; surface
                # it on the bundle as a warning naming the pack instead.
                origin = "role-required" if name in required else "step-attached"
                truncation_warnings.append(
                    f"Missing knowledge pack: {name} "
                    f"({origin}; not in knowledge plan selected_packs)"
                )
        return packs

    @staticmethod
    def _build_handoff_items(prior_handoff_paths: list[str]) -> list[tuple[str, int]]:
        """``(path, token_estimate)`` pairs, oldest first (caller-supplied
        chronological order is trusted -- see :meth:`build`'s parameter
        docs). Missing handoff files are not warned about: unlike the
        contract/role-card/context-file must-reads, a missing prior
        handoff (e.g. before phase 1 completes) is an expected, not an
        exceptional, condition.
        """
        items: list[tuple[str, int]] = []
        for path in prior_handoff_paths:
            tokens, _found = _file_token_estimate(path)
            items.append((path, tokens))
        return items

    # ------------------------------------------------------------------
    # Overflow
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_overflow(
        reference_only: list[ContextReference],
        handoff_items: list[tuple[str, int]],
        total_tokens: int,
        token_budget: int,
        truncation_warnings: list[str],
    ) -> int:
        """Enforce *token_budget*, mutating *reference_only* and
        *handoff_items* in place; returns the updated token total.

        Fixed drop order (spec §13.3 / Task 8 -- do not reorder):

        1. ``reference_only``, lowest-priority-first (i.e. popped from the
           end of the priority-sorted list built by
           :meth:`_build_reference_only`).
        2. Oldest ``prior_handoffs`` beyond the latest one -- the most
           recent handoff (last element) is never dropped.

        The scope contract (``must_read[0]``), required knowledge packs,
        and the single latest handoff are never touched -- there is no
        third drop tier in this implementation because the plan's fixed
        order names only these two.
        """
        while total_tokens > token_budget and (reference_only or len(handoff_items) > 1):
            if reference_only:
                dropped = reference_only.pop()
                total_tokens -= dropped.token_estimate
                truncation_warnings.append(
                    f"Dropped reference doc to fit token budget: {dropped.path}"
                )
                continue
            dropped_path, dropped_tokens = handoff_items.pop(0)
            total_tokens -= dropped_tokens
            truncation_warnings.append(
                f"Dropped prior handoff to fit token budget: {dropped_path}"
            )

        if total_tokens > token_budget:
            # F4.2 (Wave 2 review): everything droppable (reference docs,
            # all-but-the-latest handoff) is already gone and the bundle is
            # still over budget -- the residual overrun is the scope
            # contract / required knowledge packs / latest handoff, none of
            # which this method ever drops. Surface it rather than letting
            # the caller silently dispatch an over-budget bundle.
            overrun = total_tokens - token_budget
            truncation_warnings.append(
                f"Token budget exceeded by {overrun} token(s) with no further context "
                f"droppable (budget={token_budget}, total={total_tokens})."
            )
        return total_tokens
