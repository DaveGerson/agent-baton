"""Capability-gap model and bounded talent-factory lifecycle decision.

Full policy narrative: docs/internal/talent-factory-contract.md. This
module is the executable core of that contract — a small, dependency-free
decision layer the planning pipeline (and, later, execution-time
re-planning) calls into when a step needs a capability that may not exist.

Three concerns, kept intentionally separate:

* :class:`CapabilityGap` — an **evidence-backed** description of what's
  missing and why. Every gap must carry at least one
  :class:`CapabilityGapEvidence` item; a gap with no evidence is a bug in
  the caller, not a valid model state (enforced in ``__post_init__``).
* :func:`detect_missing_role_gap` / :func:`detect_weak_description_gap` —
  pure detectors that turn planner-observable signals into a
  ``CapabilityGap`` (or ``None`` when there's no gap). They distinguish a
  **missing role** (a capability that plausibly doesn't exist yet) from a
  **weak task description** (a routing problem, not a capability problem)
  and from **missing knowledge** (the role exists; it lacks reference
  material).
* :func:`decide_talent_lifecycle` — applies the bounded,
  policy-controlled lifecycle to a gap and returns a single
  :class:`TalentLifecycleDecision`. It never generates anything itself;
  it only decides whether generation is *permitted* right now, and what
  the safe fallback is when it isn't.

Nothing in this module talks to the filesystem, dispatches an agent, or
mutates the agent registry — see docs/internal/talent-factory-contract.md
for where those responsibilities live (talent-builder itself, plus the
validation/rollback steps that consume ``TalentLifecycleDecision``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

__all__ = [
    "CapabilityGapKind",
    "PermittedArtifactType",
    "TalentLifecycleAction",
    "CapabilityGapEvidence",
    "CapabilityGap",
    "TalentLifecycleDecision",
    "detect_missing_role_gap",
    "detect_weak_description_gap",
    "decide_talent_lifecycle",
]


class CapabilityGapKind(str, Enum):
    """What kind of gap this is — determines what generation (if any) applies."""

    #: No agent definition matches the requested role/specialty.
    MISSING_ROLE = "missing_role"
    #: The task description itself lacks enough signal to route
    #: confidently. Not a capability problem — asking for clarification
    #: is always the right move, never generation.
    WEAK_TASK_DESCRIPTION = "weak_task_description"
    #: The role exists but lacks reference material for the domain at hand.
    MISSING_KNOWLEDGE = "missing_knowledge"


class PermittedArtifactType(str, Enum):
    """Artifact kinds the talent factory is allowed to produce for a gap.

    Skills and plugins are deliberately excluded from the defaults below —
    per the talent-factory contract, the default product for a capability
    gap is a Baton agent definition or a knowledge pack. Skill/plugin
    creation only enters ``permitted_artifacts`` when a caller explicitly
    requests it (e.g. a user says "turn this into a reusable skill");
    detectors in this module never default to it.
    """

    AGENT = "agent"
    KNOWLEDGE_PACK = "knowledge_pack"
    SKILL = "skill"
    PLUGIN = "plugin"


class TalentLifecycleAction(str, Enum):
    """The bounded set of outcomes ``decide_talent_lifecycle`` can return."""

    #: Generate: dispatch talent-builder to produce the permitted artifact(s).
    DISPATCH_TALENT_BUILDER = "dispatch_talent_builder"
    #: Safe fallback: proceed with the closest existing generalist agent /
    #: existing knowledge, and record the gap for visibility. Never blocks
    #: the plan.
    FALLBACK_GENERIC_AGENT = "fallback_generic_agent"
    #: Budget or policy exhausted without resolving the gap; re-plan the
    #: unresolved work and let a human/manager decide instead of retrying
    #: or recursing further.
    QUEUE_FOR_MANAGER = "queue_for_manager"
    #: The gap is a weak-description problem — ask the caller, don't build.
    REQUEST_CLARIFICATION = "request_clarification"


# Default artifact types authorized per gap kind. Deliberately conservative:
# WEAK_TASK_DESCRIPTION never authorizes generation of anything.
_DEFAULT_ARTIFACTS_BY_KIND: dict[CapabilityGapKind, tuple[PermittedArtifactType, ...]] = {
    CapabilityGapKind.MISSING_ROLE: (PermittedArtifactType.AGENT,),
    CapabilityGapKind.MISSING_KNOWLEDGE: (PermittedArtifactType.KNOWLEDGE_PACK,),
    CapabilityGapKind.WEAK_TASK_DESCRIPTION: (),
}

_DEFAULT_FALLBACK_BY_KIND: dict[CapabilityGapKind, str] = {
    CapabilityGapKind.MISSING_ROLE: (
        "route to the closest existing generalist agent (e.g. architect or "
        "backend-engineer) and record the gap on plan_diagnostics for "
        "later review — never block the plan on a missing role"
    ),
    CapabilityGapKind.MISSING_KNOWLEDGE: (
        "proceed with the knowledge already resolved for this step and "
        "record a knowledge_gap bead for follow-up (see "
        "agent_baton/core/engine/knowledge_gap.py)"
    ),
    CapabilityGapKind.WEAK_TASK_DESCRIPTION: (
        "request clarification from the caller instead of guessing or "
        "generating capability"
    ),
}

#: Capability names that can never be a generation target — makes
#: recursive self-generation of the talent factory structurally
#: impossible rather than merely policy-discouraged (see
#: docs/internal/talent-factory-contract.md §"No recursive spawning").
NON_GENERABLE_CAPABILITIES: frozenset[str] = frozenset({"talent-builder"})


@dataclass(frozen=True)
class CapabilityGapEvidence:
    """A single observation supporting a capability-gap determination.

    ``source`` names the detector/stage that produced the evidence (e.g.
    ``"roster_stage.explicit_agent"``); ``detail`` is a human-readable
    explanation suitable for ``plan_diagnostics`` and audit logs.
    """

    source: str
    detail: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("CapabilityGapEvidence.source must be non-empty")
        if not self.detail.strip():
            raise ValueError("CapabilityGapEvidence.detail must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "detail": self.detail}


@dataclass(frozen=True)
class CapabilityGap:
    """An evidence-backed capability gap.

    A ``CapabilityGap`` with no evidence is not a valid model state — it
    is indistinguishable from a hunch, and the talent factory must never
    generate on a hunch. Construction fails loudly (``ValueError``)
    instead of silently accepting an unsupported gap.

    ``permitted_artifacts`` and ``fallback`` default from ``kind`` when
    not supplied explicitly, so callers only need to override them when
    a specific gap needs a non-default artifact set (e.g. a caller who
    explicitly asked for a skill).
    """

    requested_capability: str
    kind: CapabilityGapKind
    evidence: tuple[CapabilityGapEvidence, ...]
    # ``None`` means "not supplied — derive from kind"; an explicit ``()``
    # is a real value (e.g. a caller who determined *nothing* is currently
    # authorized for this gap) and must survive __post_init__ unchanged.
    permitted_artifacts: tuple[PermittedArtifactType, ...] | None = None
    fallback: str = ""

    def __post_init__(self) -> None:
        if not self.requested_capability.strip():
            raise ValueError("CapabilityGap.requested_capability must be non-empty")
        if not self.evidence:
            raise ValueError(
                f"CapabilityGap for {self.requested_capability!r} requires at "
                "least one evidence item — capability gaps must be "
                "evidence-backed, not asserted"
            )
        if self.permitted_artifacts is None:
            object.__setattr__(
                self,
                "permitted_artifacts",
                _DEFAULT_ARTIFACTS_BY_KIND.get(self.kind, ()),
            )
        if not self.fallback:
            object.__setattr__(
                self,
                "fallback",
                _DEFAULT_FALLBACK_BY_KIND.get(self.kind, "queue for manager review"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_capability": self.requested_capability,
            "kind": self.kind.value,
            "evidence": [e.to_dict() for e in self.evidence],
            "permitted_artifacts": [a.value for a in self.permitted_artifacts],
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class TalentLifecycleDecision:
    """The outcome of applying the bounded talent-factory lifecycle to a gap."""

    action: TalentLifecycleAction
    reason: str
    gap: CapabilityGap

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "gap": self.gap.to_dict(),
        }


# ---------------------------------------------------------------------------
# Detectors — pure functions, planner-observable signal -> CapabilityGap|None
# ---------------------------------------------------------------------------


def detect_missing_role_gap(
    requested_agent: str,
    *,
    known_agents: Iterable[str],
    source: str = "roster_stage.explicit_agent",
) -> CapabilityGap | None:
    """Detect a MISSING_ROLE gap for an explicitly requested agent name.

    ``known_agents`` should be the set of *base* agent names the registry
    actually has definitions for (flavored variants like
    ``backend-engineer--python`` should already be reduced to their base).
    Returns ``None`` when the requested agent (by base name) is known —
    this is a routing/flavor question, not a capability gap.
    """
    base = requested_agent.split("--", 1)[0]
    if base in set(known_agents):
        return None
    return CapabilityGap(
        requested_capability=requested_agent,
        kind=CapabilityGapKind.MISSING_ROLE,
        evidence=(
            CapabilityGapEvidence(
                source=source,
                detail=(
                    f"'{requested_agent}' was explicitly requested but does not "
                    f"match any registered agent definition (base name "
                    f"'{base}' not found in the agent registry)."
                ),
            ),
        ),
    )


def detect_weak_description_gap(
    task_summary: str,
    *,
    min_words: int = 3,
    source: str = "classification.task_summary",
) -> CapabilityGap | None:
    """Detect a WEAK_TASK_DESCRIPTION gap for near-empty/uninformative summaries.

    This is deliberately distinct from :func:`detect_missing_role_gap`: a
    weak description means the *task* lacks enough signal to route
    confidently, not that a capability doesn't exist. Per the
    talent-factory contract this must never trigger generation — the
    correct response is always ``REQUEST_CLARIFICATION``
    (:func:`decide_talent_lifecycle` enforces this regardless of policy).
    """
    words = [w for w in task_summary.strip().split() if w]
    if len(words) >= min_words:
        return None
    return CapabilityGap(
        requested_capability=task_summary.strip() or "(empty task summary)",
        kind=CapabilityGapKind.WEAK_TASK_DESCRIPTION,
        evidence=(
            CapabilityGapEvidence(
                source=source,
                detail=(
                    f"Task summary has {len(words)} word(s); fewer than the "
                    f"{min_words}-word floor needed to route with confidence."
                ),
            ),
        ),
    )


def detect_missing_knowledge_gap(
    role: str,
    *,
    domain: str,
    source: str = "knowledge_resolver",
) -> CapabilityGap:
    """Build a MISSING_KNOWLEDGE gap for a role that exists but lacks a pack.

    Unlike the other detectors this one always returns a gap (callers
    already know the role resolved — see
    ``agent_baton/core/engine/knowledge_gap.py`` for the runtime signal
    that triggers this) — it exists so knowledge gaps are represented with
    the same evidence-backed shape as role/description gaps.
    """
    return CapabilityGap(
        requested_capability=role,
        kind=CapabilityGapKind.MISSING_KNOWLEDGE,
        evidence=(
            CapabilityGapEvidence(
                source=source,
                detail=(
                    f"'{role}' resolved to a known agent but no knowledge pack "
                    f"covers domain '{domain}'."
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Lifecycle decision
# ---------------------------------------------------------------------------


def decide_talent_lifecycle(
    gap: CapabilityGap,
    *,
    allow_talent_builder: bool = True,
    skip_init: bool = False,
    recursion_depth: int = 0,
    max_recursion_depth: int = 0,
    attempts_used: int = 0,
    retry_budget: int = 1,
) -> TalentLifecycleDecision:
    """Apply the bounded, policy-controlled talent-factory lifecycle to *gap*.

    Checks run in a fixed order; earlier checks are hard stops that
    override later, more permissive ones. See
    docs/internal/talent-factory-contract.md for the full decision table
    and rationale for each guard.

    Args:
        gap: The evidence-backed gap to decide on.
        allow_talent_builder: ``team.allow_talent_builder`` from manager
            config (``agent_baton/core/config/manager.py``).
        skip_init: ``--skip-init`` CLI flag / equivalent caller override.
        recursion_depth: How many talent-builder generations already sit
            in the ancestry chain that led to this gap (0 for a
            first-generation gap detected directly from a task).
        max_recursion_depth: Policy ceiling on ``recursion_depth``. Default
            0 means "talent-builder may never generate from a gap that
            itself came from a prior talent-builder dispatch" — the
            re-planning path (``QUEUE_FOR_MANAGER`` /
            ``FALLBACK_GENERIC_AGENT``) is used instead of deeper nesting.
        attempts_used: Prior generation attempts already spent on this gap
            in the current plan/session.
        retry_budget: Policy ceiling on ``attempts_used``.

    Returns:
        A single :class:`TalentLifecycleDecision`.
    """
    # 1. Weak descriptions never generate — ask, don't build. This check
    #    is first because it overrides every policy knob below: there is
    #    no configuration that makes it correct to generate an agent for
    #    a task nobody has described yet.
    if gap.kind == CapabilityGapKind.WEAK_TASK_DESCRIPTION:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.REQUEST_CLARIFICATION,
            reason=(
                "weak task description cannot be resolved by generating "
                "capability — the gap is in the request, not the roster"
            ),
            gap=gap,
        )

    # 2. Structural recursion guard. talent-builder can never be asked to
    #    generate itself or another talent-builder, regardless of policy,
    #    depth, or budget — this makes recursive self-generation
    #    impossible by construction rather than merely discouraged.
    base_capability = gap.requested_capability.split("--", 1)[0]
    if base_capability in NON_GENERABLE_CAPABILITIES:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.FALLBACK_GENERIC_AGENT,
            reason=(
                "recursive talent-builder generation is structurally "
                "disallowed regardless of policy"
            ),
            gap=gap,
        )
    if recursion_depth > max_recursion_depth:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.QUEUE_FOR_MANAGER,
            reason=(
                f"recursion depth {recursion_depth} exceeds "
                f"max_recursion_depth={max_recursion_depth}; re-planning the "
                "unresolved work instead of spawning another talent-builder"
            ),
            gap=gap,
        )

    # 3. Explicit opt-outs — caller/policy said no generation this run.
    if skip_init:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.FALLBACK_GENERIC_AGENT,
            reason=(
                "--skip-init (or equivalent override) requested; using "
                "bundled generic agents instead of generating"
            ),
            gap=gap,
        )
    if not allow_talent_builder:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.FALLBACK_GENERIC_AGENT,
            reason="team.allow_talent_builder=False in manager config",
            gap=gap,
        )

    # 4. Retry budget — bounded, not infinite.
    if attempts_used >= retry_budget:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.QUEUE_FOR_MANAGER,
            reason=(
                f"retry budget exhausted ({attempts_used}/{retry_budget} "
                "attempts used); escalating to manager instead of retrying "
                "indefinitely"
            ),
            gap=gap,
        )

    # 5. Nothing generable is permitted for this gap kind — fall back.
    if not gap.permitted_artifacts:
        return TalentLifecycleDecision(
            action=TalentLifecycleAction.FALLBACK_GENERIC_AGENT,
            reason=f"no permitted artifact types for gap kind '{gap.kind.value}'",
            gap=gap,
        )

    return TalentLifecycleDecision(
        action=TalentLifecycleAction.DISPATCH_TALENT_BUILDER,
        reason=(
            f"evidence-backed {gap.kind.value} gap with budget remaining "
            f"({attempts_used}/{retry_budget} attempts used); dispatching "
            "talent-builder"
        ),
        gap=gap,
    )
