"""Auto-visualization -- fires after state-changing execution operations.

Called internally by ``baton execute`` subcommands to provide automatic
progress visualization without user intervention.  Renders a compact
terminal status bar (to stderr) and saves an HTML snapshot to a
predictable path.
"""
from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def auto_viz(
    state: object,
    *,
    context_root: Path | None = None,
    quiet: bool = False,
) -> None:
    """Render a compact status bar and save an HTML snapshot.

    Called after ``baton execute record/gate/complete/start`` to give
    automatic progress visibility.

    Args:
        state: ``ExecutionState`` object (already loaded by the caller).
        context_root: Path to ``.claude/team-context/`` for HTML save location.
        quiet: If ``True``, skip terminal rendering (still save HTML).
    """
    try:
        from agent_baton.visualize.snapshot import PlanSnapshot

        snapshot = PlanSnapshot.from_state(state)
    except Exception as exc:
        _log.debug("auto_viz: snapshot build failed: %s", exc)
        return

    # 1. Compact terminal rendering (to stderr so it doesn't break stdout protocol)
    if not quiet:
        try:
            from agent_baton.visualize.compact import render_compact

            render_compact(snapshot)
        except Exception as exc:
            _log.debug("auto_viz: compact render failed: %s", exc)

    # 2. Auto-save HTML snapshot and print link
    if context_root is not None:
        try:
            viz_path = _save_html_snapshot(snapshot, context_root, getattr(state, "task_id", ""))
            if viz_path and not quiet:
                _print_viz_link(viz_path)
        except Exception as exc:
            _log.debug("auto_viz: HTML save failed: %s", exc)


def auto_viz_from_plan(
    plan: object,
    *,
    context_root: Path | None = None,
) -> None:
    """Render compact viz from a plan (no execution state yet).

    Called after ``baton execute start`` to show initial plan overview.
    """
    try:
        from agent_baton.visualize.snapshot import PlanSnapshot

        snapshot = PlanSnapshot.from_plan(plan)
    except Exception as exc:
        _log.debug("auto_viz_from_plan: snapshot build failed: %s", exc)
        return

    try:
        from agent_baton.visualize.compact import render_compact

        render_compact(snapshot)
    except Exception as exc:
        _log.debug("auto_viz_from_plan: compact render failed: %s", exc)

    if context_root is not None:
        try:
            task_id = getattr(plan, "task_id", "")
            viz_path = _save_html_snapshot(snapshot, context_root, task_id)
            if viz_path:
                _print_viz_link(viz_path)
        except Exception as exc:
            _log.debug("auto_viz_from_plan: HTML save failed: %s", exc)


def _save_html_snapshot(
    snapshot: object,
    context_root: Path,
    task_id: str,
) -> Path | None:
    """Save HTML visualization to a predictable location.

    Writes to two locations:

    1. ``.claude/team-context/viz.html`` -- latest execution (always overwritten)
    2. ``.claude/team-context/executions/<task_id>/viz.html`` -- per-execution archive

    Returns the path to the latest viz.html, or None on failure.
    """
    from agent_baton.visualize.web_renderer import render_html

    html = render_html(snapshot)  # type: ignore[arg-type]

    # Always-current file
    latest_path = context_root / "viz.html"
    latest_path.write_text(html, encoding="utf-8")

    # Per-execution archive
    if task_id:
        exec_dir = context_root / "executions" / task_id
        if exec_dir.is_dir():
            exec_path = exec_dir / "viz.html"
            exec_path.write_text(html, encoding="utf-8")

    return latest_path


def _print_viz_link(viz_path: Path) -> None:
    """Print a clickable link to the viz HTML file."""
    import sys

    try:
        from rich.console import Console
        from rich.text import Text

        c = Console(stderr=True)
        line = Text()
        line.append("  📊 ", style="dim")
        line.append("Viz: ", style="dim")
        line.append(f"file://{viz_path.resolve()}", style="underline cyan")
        c.print(line)
    except ImportError:
        print(f"  Viz: file://{viz_path.resolve()}", file=sys.stderr)
