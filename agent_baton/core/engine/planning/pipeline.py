"""Pipeline runner for planning stages.

The pipeline is intentionally trivial: a list of stages, run in order,
each handed the draft from the previous.  No retries, no parallelism, no
conditional dispatch — those would hide control flow that should be
visible in the stages themselves.

If a stage raises, the pipeline lets it propagate.  Stages that want
soft-fail behavior (the classic ``except Exception: pass`` pattern in the
old planner) must catch internally and record the failure on the draft
or via the logger.  The pipeline does not silently swallow exceptions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .draft import PlanDraft
from .protocols import Stage
from .services import PlannerServices

logger = logging.getLogger(__name__)


@dataclass
class Pipeline:
    """Runs a fixed list of stages over a draft."""

    stages: list[Stage]

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        for stage in self.stages:
            logger.debug("planning.pipeline: running stage %s", stage.name)
            draft = stage.run(draft, services)
        return draft
