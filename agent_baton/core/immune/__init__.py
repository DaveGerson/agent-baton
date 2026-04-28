"""Wave 6.2 Part B — Immune System (bd-be76).

Long-lived daemon that sweeps the project on a strict daily token budget,
auto-files beads for findings, and dispatches self-healing micro-agents for
high-confidence simple fixes.

Disabled by default; set ``BATON_IMMUNE_ENABLED=1`` or ``immune.enabled: true``
in ``baton.yaml`` to activate.

Key classes:

* :class:`~agent_baton.core.immune.daemon.ImmuneDaemon` — tick-loop daemon.
* :class:`~agent_baton.core.immune.scheduler.SweepScheduler` — rotating priority queue.
* :class:`~agent_baton.core.immune.sweeper.Sweeper` — dispatches sweep agents.
* :class:`~agent_baton.core.immune.cache.ContextCache` — monthly-rebuilt project snapshot.
* :class:`~agent_baton.core.immune.triage.FindingTriage` — bead + auto-fix gating.
"""
from __future__ import annotations

from agent_baton.core.immune.cache import ContextCache
from agent_baton.core.immune.daemon import ImmuneConfig, ImmuneDaemon
from agent_baton.core.immune.scheduler import SweepScheduler, SweepTarget
from agent_baton.core.immune.sweeper import Sweeper, SweepFinding
from agent_baton.core.immune.triage import FindingTriage

__all__ = [
    "ContextCache",
    "FindingTriage",
    "ImmuneConfig",
    "ImmuneDaemon",
    "SweepFinding",
    "SweepScheduler",
    "SweepTarget",
    "Sweeper",
]
