"""Manager-mode sidecar artifact path conventions.

``ManagerArtifactPaths`` is the single source of truth for where
manager-mode PMO artifacts live on disk, rooted at
``<team_context_dir>/executions/<task_id>/`` (see
docs/internal/manager-mode-pmo-design.md). Never hardcode these paths
elsewhere — import and use this class.
"""
from __future__ import annotations

import re
from pathlib import Path


class ManagerArtifactPaths:
    """Sidecar artifact locations for a single manager-mode execution.

    Attributes:
        root: ``<team_context_dir>/executions/<task_id>``.
    """

    def __init__(self, team_context_dir: Path, task_id: str) -> None:
        self.root = Path(team_context_dir) / "executions" / task_id

    # ------------------------------------------------------------------
    # Fixed single-file artifacts
    # ------------------------------------------------------------------

    @property
    def charter(self) -> Path:
        return self.root / "project-charter.md"

    @property
    def scope_map(self) -> Path:
        return self.root / "scope-map.json"

    @property
    def team_blueprint(self) -> Path:
        return self.root / "team-blueprint.json"

    @property
    def knowledge_plan(self) -> Path:
        return self.root / "knowledge-plan.json"

    @property
    def manager_brief(self) -> Path:
        return self.root / "manager-brief.md"

    @property
    def manager_report(self) -> Path:
        return self.root / "manager-report.md"

    @property
    def decision_log(self) -> Path:
        return self.root / "decision-log.jsonl"

    # ------------------------------------------------------------------
    # Directories holding per-entity artifacts
    # ------------------------------------------------------------------

    @property
    def role_cards_dir(self) -> Path:
        return self.root / "role-cards"

    @property
    def scope_contracts_dir(self) -> Path:
        return self.root / "scope-contracts"

    @property
    def context_bundles_dir(self) -> Path:
        return self.root / "context-bundles"

    @property
    def handoffs_dir(self) -> Path:
        return self.root / "handoffs"

    @property
    def decisions_dir(self) -> Path:
        return self.root / "decisions"

    @property
    def scope_evidence_dir(self) -> Path:
        """Independently-computed diff evidence backing scope-expansion
        decisions (Phase 3 "Make scope contracts authoritative", 3.2).

        One JSON file per decision, keyed by ``decision_id`` -- see
        ``agent_baton.core.manager.scope_amendment.write_scope_evidence``.
        Kept separate from ``decisions_dir`` (which holds the
        human-facing Markdown packet) because this is a machine-readable
        record of exactly which step/paths/real-diff a decision concerns,
        used to resolve the decision without re-parsing free text.
        """
        return self.root / "scope-evidence"

    # ------------------------------------------------------------------
    # Per-entity artifact path builders
    # ------------------------------------------------------------------

    def role_card(self, role: str) -> Path:
        return self.role_cards_dir / f"{self._sanitize(role)}.md"

    def scope_contract(self, step_id: str, ext: str = "md") -> Path:
        return self.scope_contracts_dir / f"{self._sanitize(step_id)}.{ext}"

    def scope_evidence(self, decision_id: str) -> Path:
        return self.scope_evidence_dir / f"{self._sanitize(decision_id)}.json"

    def context_bundle(self, step_id: str) -> Path:
        return self.context_bundles_dir / f"{self._sanitize(step_id)}.json"

    def phase_handoff(self, n: int) -> Path:
        return self.handoffs_dir / f"phase-{n}-handoff.md"

    def decision(self, decision_id: str) -> Path:
        return self.decisions_dir / f"{self._sanitize(decision_id)}.md"

    @staticmethod
    def _sanitize(s: str) -> str:
        """Replace filesystem-unsafe characters (e.g. ``/`` in ``step_id``)."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", s)
