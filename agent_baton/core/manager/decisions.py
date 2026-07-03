"""``DecisionPacketBuilder`` -- typed :class:`ManagerDecision` wrapper over
the existing :class:`~agent_baton.core.runtime.decisions.DecisionManager` (M7).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10,
docs/internal/manager-mode-pmo-design.md ("Decisions: ... Reuse, don't
rebuild"), and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §15.3/§16
Milestone 7.

``DecisionPacketBuilder.create()`` has three effects, all exercised by
``tests/manager/test_decision_packets.py``:

1. Renders *decision* as Markdown (spec §15.3 template) to
   ``decisions/<decision_id>.md`` (:meth:`ManagerArtifactPaths.decision`).
2. Appends *decision* as one JSON line to ``decision-log.jsonl``
   (:func:`agent_baton.core.manager.artifacts.append_decision_log`).
3. When a live :class:`~agent_baton.core.runtime.decisions.DecisionManager`
   is supplied at construction time, files a
   :class:`~agent_baton.models.decision.DecisionRequest` against it so the
   existing ``baton execute decide`` surface picks the decision up --
   this builder does not reimplement human-decision plumbing, it only
   translates a manager-mode :class:`ManagerDecision` into that existing
   protocol.

Per Wave 0 self-review note ("``ManagerDecision.created_at`` is
caller-supplied everywhere -> no clock reads inside builders ->
deterministic tests"): this module never reads the clock. A caller that
constructs a ``ManagerDecision`` with an empty ``created_at`` gets a
degraded-but-deterministic ``decision_id`` (hashing an empty string) --
that is a caller bug, not something this builder repairs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.manager.artifacts import append_decision_log, write_text
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.models.decision import DecisionRequest
from agent_baton.models.manager import ManagerDecision

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.runtime.decisions import DecisionManager

# spec §15.3 example is "# Manager Decision Required: Scope Expansion" for
# decision_type "scope_expansion" -- title-cased with underscores as spaces.
# Kept as an explicit mapping (rather than a blind `.replace("_", " ").title()`
# for every value) so a future decision_type with an acronym or irregular
# casing can override it here without a special case scattered elsewhere.
_DECISION_TITLES: dict[str, str] = {
    "scope_expansion": "Scope Expansion",
    "ambiguity": "Ambiguity",
    "knowledge_gap": "Knowledge Gap",
    "review_veto": "Review Veto",
    "approval": "Approval",
}

_DEFAULT_OPTIONS: tuple[str, ...] = ("approve", "reject")


def compute_decision_id(summary: str, created_at: str) -> str:
    """``dec-<8 hex chars>`` -- deterministic hash of ``summary + created_at``
    (docs/internal/manager-mode-pmo-plan.md Task 10, "decision-id rule").

    Same *summary*/*created_at* pair always yields the same id; this is
    what makes ``DecisionPacketBuilder.create()`` idempotent when a signal
    fires more than once for what is semantically the same decision.
    """
    digest = hashlib.sha1((summary + created_at).encode("utf-8")).hexdigest()
    return f"dec-{digest[:8]}"


def _decision_title(decision_type: str) -> str:
    return _DECISION_TITLES.get(decision_type, decision_type.replace("_", " ").title())


def decision_to_markdown(decision: ManagerDecision) -> str:
    """Render *decision* as Markdown following spec §15.3's template exactly:
    ``# Manager Decision Required: <Title>`` followed by ``## Summary``,
    ``## Context``, ``## Options`` (numbered list), ``## Recommendation``.

    Per-decision content is necessarily data-driven from the model's
    fields rather than the spec's single illustrative example -- the same
    precedent as ``role_cards.render_role_card`` (see that module's design
    decision #4): the four section *headers*, in order, are what's
    normative.
    """
    lines: list[str] = [f"# Manager Decision Required: {_decision_title(decision.decision_type)}", ""]

    lines.append("## Summary")
    lines.append(decision.summary or "_Not specified._")
    lines.append("")

    lines.append("## Context")
    lines.append(decision.context or "_Not specified._")
    lines.append("")

    lines.append("## Options")
    if decision.options:
        lines.extend(f"{index}. {option}" for index, option in enumerate(decision.options, start=1))
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("## Recommendation")
    lines.append(decision.recommended_option or "_Not specified._")
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


class DecisionPacketBuilder:
    """Writes decision packets and (optionally) files them with
    :class:`~agent_baton.core.runtime.decisions.DecisionManager`.

    Attributes:
        config: Manager-mode configuration (kept for constructor-shape
            consistency with the other M7 builder,
            :class:`~agent_baton.core.manager.reports.ManagerReportBuilder`;
            not currently read by :meth:`create`, but callers routing by
            policy -- e.g. ``scoping.scope_expansion_policy`` -- read it
            *before* deciding whether to call :meth:`create` at all, so
            the builder itself does not need to re-derive the policy).
        paths: Sidecar path conventions for the owning execution.
        decision_manager: Optional live
            :class:`~agent_baton.core.runtime.decisions.DecisionManager`.
            When ``None`` (the default), :meth:`create` still writes the
            Markdown packet and appends the JSONL log -- it just skips the
            ``DecisionRequest`` filing step.
    """

    def __init__(
        self,
        config: "ManagerConfig",
        paths: ManagerArtifactPaths,
        decision_manager: "DecisionManager | None" = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.decision_manager = decision_manager

    def create(self, decision: ManagerDecision) -> Path:
        """Write *decision*'s packet, append the audit log, and (if a
        ``DecisionManager`` was supplied) file a ``DecisionRequest``.

        Populates ``decision.decision_id`` in place via
        :func:`compute_decision_id` when the caller left it empty, so the
        Markdown path, the JSONL entry, and (when applicable) the
        ``DecisionRequest.request_id`` all agree on the same id.

        Returns the path to the written Markdown packet.
        """
        if not decision.decision_id:
            decision.decision_id = compute_decision_id(decision.summary, decision.created_at)

        packet_path = self.paths.decision(decision.decision_id)
        write_text(packet_path, decision_to_markdown(decision))
        append_decision_log(self.paths, decision)

        if self.decision_manager is not None:
            self._file_decision_request(decision, packet_path)

        return packet_path

    def _file_decision_request(self, decision: ManagerDecision, packet_path: Path) -> None:
        request = DecisionRequest(
            request_id=decision.decision_id,
            task_id=decision.task_id,
            decision_type=decision.decision_type,
            summary=decision.summary,
            options=list(decision.options) if decision.options else list(_DEFAULT_OPTIONS),
            context_files=[str(packet_path)],
            created_at=decision.created_at,
        )
        self.decision_manager.request(request)
