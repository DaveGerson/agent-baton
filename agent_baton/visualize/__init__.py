"""Plan visualization — render-ready snapshots and terminal/web renderers.

Public API:

- ``PlanSnapshot`` — render-ready data adapter built from a ``MachinePlan``
  or ``ExecutionState``.
- ``render_cli`` — Rich-based terminal renderer (requires ``rich``).
- ``render_html`` — Self-contained HTML visualization.
"""
from __future__ import annotations

from agent_baton.visualize.snapshot import PlanSnapshot
from agent_baton.visualize.cli_renderer import render as render_cli
from agent_baton.visualize.web_renderer import render_html

__all__ = [
    "PlanSnapshot",
    "render_cli",
    "render_html",
]
