"""Wave 6.2 Part B — FindingTriage: bead filing and auto-fix dispatch.

Design decisions (from wave-6-2-design.md Part B):

- **Bead always.** Every finding is filed as a ``discovery`` bead regardless
  of confidence level.
- **Auto-fix gated.** An auto-fix self-heal micro-agent is dispatched only
  when:

  1. ``finding.confidence >= config.auto_fix_threshold`` (default 0.85).
  2. ``finding.kind`` is in :attr:`FindingTriage.AUTO_FIX_KINDS`.
  3. ``budget.has_headroom_for_auto_fix()`` returns ``True``.

- **No human gates.** Per ``feedback_no_human_compliance_gates.md`` —
  compliance and auto-fix are automated; human review is surfaced via
  beads only.

Auto-fix dispatch reuses Wave 5.2 :class:`~agent_baton.core.engine.selfheal.SelfHealEscalator`
infrastructure: a synthetic ``step_id`` is minted and the Haiku micro-agent
receives the finding's ``auto_fix_directive`` as its prompt.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.utils.time import utcnow_zulu as _utcnow_str

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.core.govern.budget import BudgetEnforcer
    from agent_baton.core.immune.daemon import ImmuneConfig
    from agent_baton.core.immune.sweeper import SweepFinding

_log = logging.getLogger(__name__)

__all__ = ["FindingTriage"]


class FindingTriage:
    """Files beads for every immune finding and optionally dispatches auto-fix.

    Args:
        bead_store: Project-scoped :class:`~agent_baton.core.engine.bead_store.BeadStore`.
        budget: :class:`~agent_baton.core.govern.budget.BudgetEnforcer` for headroom checks.
        config: :class:`~agent_baton.core.immune.daemon.ImmuneConfig` for threshold/allowlist.
        launcher: ``ClaudeCodeLauncher`` instance for auto-fix dispatch.
    """

    AUTO_FIX_KINDS: frozenset[str] = frozenset({
        "deprecated-api-trivial",
        "doc-drift-signature",
        "stale-comment",
    })

    def __init__(
        self,
        bead_store: "BeadStore",
        budget: "BudgetEnforcer",
        config: "ImmuneConfig",
        launcher: object,
    ) -> None:
        self._bead_store = bead_store
        self._budget = budget
        self._config = config
        self._launcher = launcher

    def handle(self, finding: "SweepFinding") -> str:
        """Process a sweep finding: file a bead and possibly dispatch auto-fix.

        Args:
            finding: The :class:`~agent_baton.core.immune.sweeper.SweepFinding`
                to process.

        Returns:
            The ``bead_id`` of the filed bead.
        """
        bead_id = self._file_finding_bead(finding)

        if (
            finding.confidence >= self._config.auto_fix_threshold
            and finding.kind in self.AUTO_FIX_KINDS
            and self._budget.has_headroom_for_auto_fix()
            and self._config.auto_fix
            and finding.auto_fix_directive
        ):
            self._dispatch_self_heal_micro_agent(finding, bead_id)

        return bead_id

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _file_finding_bead(self, finding: "SweepFinding") -> str:
        """Write a ``discovery`` bead for *finding* and return its ``bead_id``."""
        from agent_baton.models.bead import Bead

        step_id = f"immune-sweep-{finding.kind}"
        content = (
            f"Immune sweep finding: {finding.description}\n"
            f"Kind: {finding.kind}\n"
            f"Confidence: {finding.confidence:.2f}\n"
            f"Affected lines: {finding.affected_lines}\n"
            f"Path: {finding.target.path}"
        )
        if finding.auto_fix_directive:
            content += f"\nAuto-fix directive: {finding.auto_fix_directive}"

        # Generate a stable bead_id from kind + path + timestamp prefix.
        import hashlib
        digest = hashlib.sha256(
            f"{finding.kind}:{finding.target.path}:{_utcnow_str()}".encode()
        ).hexdigest()[:6]
        bead_id = f"bd-{digest}"

        bead = Bead(
            bead_id=bead_id,
            task_id="",  # project-scoped (no active execution)
            step_id=step_id,
            agent_name=f"immune-{finding.kind}",
            bead_type="discovery",
            content=content,
            confidence="medium" if finding.confidence < 0.85 else "high",
            scope="project",
            tags=["immune", finding.kind, "bd-be76"],
            affected_files=[str(finding.target.path)],
            status="open",
            created_at=_utcnow_str(),
            summary=finding.description[:120],
        )
        written_id = self._bead_store.write(bead)
        _log.info(
            "FindingTriage: filed bead %s for %s/%s (confidence=%.2f)",
            written_id or bead_id, finding.kind, finding.target.path, finding.confidence,
        )
        return written_id or bead_id

    def _dispatch_self_heal_micro_agent(
        self, finding: "SweepFinding", bead_id: str
    ) -> None:
        """Launch a Haiku self-heal micro-agent to apply the auto-fix.

        Reuses Wave 5.2 SelfHealEscalator infrastructure: we build a
        synthetic step-like dispatch using ClaudeCodeLauncher directly at
        the Haiku tier.  The finding's ``auto_fix_directive`` becomes the
        primary prompt.
        """
        step_id = f"immune-autofix-{uuid.uuid4().hex[:8]}"
        prompt = (
            f"[Immune System Auto-Fix] (bead: {bead_id})\n\n"
            f"Finding: {finding.description}\n"
            f"Kind: {finding.kind}\n"
            f"File: {finding.target.path}\n"
            f"Affected lines: {finding.affected_lines}\n\n"
            f"Directive:\n{finding.auto_fix_directive}\n\n"
            f"Apply the directive carefully.  Make the minimal change needed.  "
            f"Do not change unrelated code."
        )
        try:
            _log.info(
                "FindingTriage: dispatching auto-fix step_id=%s for bead %s",
                step_id, bead_id,
            )
            self._launcher.launch(  # type: ignore[union-attr]
                agent_name="self-heal-haiku",
                prompt=prompt,
                cwd_override=str(finding.target.path.parent)
                if finding.target.path.is_file()
                else str(finding.target.path),
            )
            # Record the token spend against the immune daily budget.
            # Estimate: 4K input + 1K output for a Haiku micro-fix.
            self._budget.record_immune_spend(
                target_path=str(finding.target.path),
                kind=finding.kind,
                tokens_in=4_000,
                tokens_out=1_000,
            )
        except Exception as exc:
            _log.warning(
                "FindingTriage: auto-fix dispatch failed for %s: %s", bead_id, exc
            )
