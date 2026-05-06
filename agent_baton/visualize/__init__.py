"""Plan visualization — render-ready snapshots and terminal/web renderers.

Public API:

- ``PlanSnapshot`` — render-ready data adapter built from a ``MachinePlan``
  or ``ExecutionState``.
- ``render_cli`` — Rich-based terminal renderer (requires ``rich``).
- ``render_html`` — Self-contained HTML visualization.
- ``render_compact`` — Compact 2-line status bar for inline progress display.
- ``auto_viz`` — Auto-visualization hook for execution state changes.
"""
from __future__ import annotations

from agent_baton.visualize.snapshot import PlanSnapshot
from agent_baton.visualize.cli_renderer import render as render_cli
from agent_baton.visualize.web_renderer import render_html
from agent_baton.visualize.compact import render_compact
from agent_baton.visualize.auto import auto_viz

__all__ = [
    "PlanSnapshot",
    "render_cli",
    "render_html",
    "render_compact",
    "auto_viz",
]
