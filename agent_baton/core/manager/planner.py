"""Manager-mode post-processor around ``IntelligentPlanner.create_plan()``.

See docs/internal/manager-mode-pmo-design.md ("Architecture") and
docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 4.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.manager.artifacts import ManagerArtifacts, write_all
from agent_baton.core.manager.paths import ManagerArtifactPaths

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.models.execution import MachinePlan


class ManagerModePlanner:
    """Post-processor. Wave 0: builds nothing yet — writes nothing, returns
    empty ManagerArtifacts.

    Wave 3 fills composition in THIS ORDER (do not reorder):

        charter -> scope map -> blueprint+role cards -> knowledge plan
        -> PhasePolicyApplier.apply (mutates plan)
        -> scope contracts + context bundles (over the FINAL step list,
           so injected review steps get bundles)
        -> manager brief -> write_all

    Calling convention (enforced by the caller, not this class): callers
    invoke :meth:`build_and_write` only when the plan itself is being
    persisted (``baton plan --save``); for a preview (``--dry-run``) they
    call :meth:`build` alone (or skip calling this class entirely) so
    nothing is written to disk.
    """

    def __init__(
        self,
        config: "ManagerConfig",
        *,
        project_root: Path,
        team_context_dir: Path,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.team_context_dir = Path(team_context_dir)

    def build(self, plan: "MachinePlan", task_summary: str) -> ManagerArtifacts:
        """Build PMO artifacts for *plan*. Wave 0: always empty."""
        return ManagerArtifacts()

    def build_and_write(self, plan: "MachinePlan", task_summary: str) -> ManagerArtifacts:
        """Build artifacts and persist them via ``write_all``.

        Wave 0: :meth:`build` always returns an empty ``ManagerArtifacts``,
        so ``write_all`` writes nothing (every field is ``None``/empty).
        """
        artifacts = self.build(plan, task_summary)
        paths = ManagerArtifactPaths(self.team_context_dir, plan.task_id)
        write_all(paths, artifacts)
        return artifacts
