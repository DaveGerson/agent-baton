"""Bead-informed plan enrichment: BeadAnalyzer.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

``BeadAnalyzer`` mines historical beads to produce
:class:`~agent_baton.models.pattern.PlanStructureHint` objects that the
planner can apply during phase construction.  Three analysis passes run
in sequence:

1. **Warning frequency** — when the same file or module appears in many
   warning beads across recent executions, recommend adding a review phase
   before the implementation proceeds.

2. **Discovery file clustering** — when multiple discoveries reference the
   same file path, surface that file as a context file for the agent about
   to work on it.

3. **Decision reversal** — when a decision bead is later superseded or
   contradicted by another bead in the same task, recommend an approval gate
   before the phase that triggered the reversal.

All analysis is best-effort.  If the bead store is unavailable or returns
no data the analyzer returns an empty list without raising.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.pattern import PlanStructureHint

_log = logging.getLogger(__name__)

# Minimum number of warning beads referencing the same path before the
# analyzer recommends a review phase.
_WARNING_FREQUENCY_THRESHOLD = 2

# Minimum discovery beads referencing the same file path before a
# context-file hint is emitted.
_DISCOVERY_CLUSTER_THRESHOLD = 2

# Simple file path regex — matches path-like tokens in bead content.
_PATH_RE = re.compile(r"[\w./\\-]{3,}\.\w{1,6}")


def _extract_paths(text: str) -> list[str]:
    """Extract probable file paths from *text*."""
    return [m.group(0) for m in _PATH_RE.finditer(text)]


class BeadAnalyzer:
    """Mine historical beads to produce plan structure hints.

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    Instantiate once per plan creation cycle; call :meth:`analyze` with a
    live bead store and the task description.  The returned hints are then
    applied by ``IntelligentPlanner._apply_bead_hints()``.
    """

    def analyze(
        self,
        bead_store: "BeadStore",
        task_description: str = "",
        task_id: str | None = None,
    ) -> "list[PlanStructureHint]":
        """Run all three analysis passes and return the combined hints.

        Args:
            bead_store: Live :class:`~agent_baton.core.engine.bead_store.BeadStore`.
            task_description: The current task summary (used for relevance
                filtering in future iterations; currently unused).
            task_id: If given, scope the analysis to beads from this execution.
                When ``None``, the analyzer queries across all recent beads.

        Returns:
            List of :class:`~agent_baton.models.pattern.PlanStructureHint`
            objects.  May be empty.
        """
        if bead_store is None:
            return []
        try:
            return self._analyze(bead_store, task_description, task_id)
        except Exception as exc:
            _log.debug("BeadAnalyzer.analyze failed (non-fatal): %s", exc)
            return []

    def _analyze(
        self,
        bead_store: "BeadStore",
        task_description: str,
        task_id: str | None,
    ) -> "list[PlanStructureHint]":
        from agent_baton.models.pattern import PlanStructureHint

        hints: list[PlanStructureHint] = []

        # Fetch recent warning and discovery beads.
        warnings = bead_store.query(
            task_id=task_id, bead_type="warning", limit=200
        )
        discoveries = bead_store.query(
            task_id=task_id, bead_type="discovery", limit=200
        )
        decisions = bead_store.query(
            task_id=task_id, bead_type="decision", limit=200
        )

        # Pass 1: Warning frequency → add_review_phase hint.
        hints.extend(self._pass_warning_frequency(warnings))

        # Pass 2: Discovery file clustering → add_context_file hints.
        hints.extend(self._pass_discovery_clustering(discoveries))

        # Pass 3: Decision reversal → add_approval_gate hints.
        hints.extend(self._pass_decision_reversal(decisions))

        return hints

    # ------------------------------------------------------------------
    # Pass 1 — Warning frequency
    # ------------------------------------------------------------------

    def _pass_warning_frequency(
        self, warning_beads: "list"
    ) -> "list[PlanStructureHint]":
        """Emit add_review_phase hints for frequently-warned paths."""
        from agent_baton.models.pattern import PlanStructureHint

        path_to_bead_ids: dict[str, list[str]] = {}

        for bead in warning_beads:
            paths = _extract_paths(bead.content)
            for path in paths:
                path_to_bead_ids.setdefault(path, []).append(bead.bead_id)

        hints: list[PlanStructureHint] = []
        for path, bead_ids in path_to_bead_ids.items():
            if len(bead_ids) >= _WARNING_FREQUENCY_THRESHOLD:
                hints.append(PlanStructureHint(
                    hint_type="add_review_phase",
                    reason=(
                        f"File '{path}' appeared in {len(bead_ids)} warning bead(s) "
                        f"across recent executions — adding a review phase reduces "
                        f"the chance of repeating the same mistake."
                    ),
                    evidence_bead_ids=bead_ids[:10],
                    metadata={"file": path},
                ))

        return hints

    # ------------------------------------------------------------------
    # Pass 2 — Discovery file clustering
    # ------------------------------------------------------------------

    def _pass_discovery_clustering(
        self, discovery_beads: "list"
    ) -> "list[PlanStructureHint]":
        """Emit add_context_file hints for frequently-discovered paths."""
        from agent_baton.models.pattern import PlanStructureHint

        path_to_bead_ids: dict[str, list[str]] = {}

        for bead in discovery_beads:
            # Check both content text and affected_files.
            paths = _extract_paths(bead.content)
            paths.extend(bead.affected_files or [])
            for path in paths:
                path_to_bead_ids.setdefault(path, []).append(bead.bead_id)

        hints: list[PlanStructureHint] = []
        seen: set[str] = set()
        for path, bead_ids in path_to_bead_ids.items():
            if path in seen:
                continue
            if len(bead_ids) >= _DISCOVERY_CLUSTER_THRESHOLD:
                seen.add(path)
                hints.append(PlanStructureHint(
                    hint_type="add_context_file",
                    reason=(
                        f"File '{path}' referenced by {len(bead_ids)} discovery "
                        f"bead(s) — pre-loading it reduces redundant re-discovery."
                    ),
                    evidence_bead_ids=bead_ids[:10],
                    metadata={"file": path},
                ))

        return hints

    # ------------------------------------------------------------------
    # Pass 3 — Decision reversal detection
    # ------------------------------------------------------------------

    def _pass_decision_reversal(
        self, decision_beads: "list"
    ) -> "list[PlanStructureHint]":
        """Emit add_approval_gate hints when decisions are contradicted."""
        from agent_baton.models.pattern import PlanStructureHint

        hints: list[PlanStructureHint] = []

        for bead in decision_beads:
            # A decision bead with a "contradicts" or "supersedes" link indicates
            # that a prior decision was reversed — this is the high-signal case.
            reversal_link_types = {"contradicts", "supersedes"}
            reversals = [
                lnk for lnk in (bead.links or [])
                if lnk.link_type in reversal_link_types
            ]
            if not reversals:
                continue

            target_ids = [lnk.target_bead_id for lnk in reversals]
            hints.append(PlanStructureHint(
                hint_type="add_approval_gate",
                reason=(
                    f"Decision bead {bead.bead_id} reversed or contradicted "
                    f"prior decision(s) ({', '.join(target_ids)}) — "
                    f"inserting an approval gate reduces the risk of diverging "
                    f"implementation choices."
                ),
                evidence_bead_ids=[bead.bead_id] + target_ids,
                metadata={"reversed_bead_ids": target_ids},
            ))

        return hints
