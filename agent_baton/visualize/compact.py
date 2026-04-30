"""Compact status bar for inline execution visualization.

Renders a tight 2-3 line status summary suitable for printing after
every state-changing operation in the execution loop.  Output goes to
*stderr* so it never interferes with the stdout protocol that Claude
Code parses.

Examples::

    -- * Phase 2/5: Implementation --- 45% ########............ -- 5/11 done . $0.42 --
       v 2.1 backend-eng  * 2.2 python-dev  * 2.3 frontend-dev  o 2.4 wire-cli

    === v COMPLETE === 5 phases . 11 steps . 128,450 tokens . $0.42 . 5m 42s ===
"""
from __future__ import annotations

import sys

from agent_baton.visualize._colors import RISK_COLORS, STATUS_COLORS
from agent_baton.visualize.snapshot import PlanSnapshot, PhaseSnapshot, StepSnapshot

try:
    from rich.console import Console
    from rich.text import Text

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


def render_compact(snapshot: PlanSnapshot, console: Console | None = None) -> None:
    """Print a 2-3 line status summary of the execution."""
    if not _HAS_RICH:
        # Fallback: plain text
        _render_plain(snapshot)
        return

    console = console or Console(stderr=True)  # stderr so it doesn't interfere with stdout protocol

    # Find current phase
    current: PhaseSnapshot | None = None
    for phase in snapshot.phases:
        if phase.status in ("running", "gate_pending"):
            current = phase
            break

    if snapshot.execution_status in ("complete", "failed", "cancelled"):
        _render_complete_bar(console, snapshot)
        return

    if current is None:
        # Not started or between phases
        if snapshot.phases:
            current = snapshot.phases[0]
        else:
            return

    _render_phase_bar(console, snapshot, current)
    _render_all_steps(console, snapshot)


def _render_phase_bar(
    console: Console,
    snap: PlanSnapshot,
    phase: PhaseSnapshot,
) -> None:
    """Render the main status line with phase info and progress bar."""
    total_phases = len(snap.phases)
    pct = snap.progress_pct

    markers: dict[str, tuple[str, str]] = {
        "running": ("🔥", "bold yellow"),
        "gate_pending": ("🛎", "bold yellow"),
        "complete": ("🍽", "bold green"),
        "failed": ("💥", "bold red"),
        "pending": ("🥟", "dim"),
    }
    marker_char, marker_style = markers.get(phase.status, ("🥟", "dim"))

    # Phase progress pips: ■ for done, ▪ for current, □ for pending
    pips = Text()
    for i, p in enumerate(snap.phases):
        if p.status == "complete":
            pips.append("■", style="green")
        elif p.status in ("running", "gate_pending"):
            pips.append("▣", style="bold cyan")
        elif p.status == "failed":
            pips.append("■", style="red")
        else:
            pips.append("□", style="dim")

    line = Text()
    line.append("  ")
    line.append(marker_char)
    line.append(f" Phase {phase.phase_id}/{total_phases} ", style="bold")
    line.append_text(pips)
    line.append(f"  {_truncate(phase.name, 30)}", style="bold white")
    console.print(line)

    # Progress bar line
    bar_width = 32
    filled = int(pct / 100 * bar_width)
    bar = Text("  ")
    bar.append("  ")
    bar.append("▓" * filled, style="bold cyan")
    bar.append("░" * (bar_width - filled), style="dim")
    bar.append(f" {pct:.0f}%", style="bold white")
    bar.append(f"  {snap.steps_complete}/{snap.total_steps} done", style="white")
    if snap.steps_running > 0:
        bar.append(f" · {snap.steps_running} baking", style="yellow")
    if snap.steps_failed > 0:
        bar.append(f" · {snap.steps_failed} burnt", style="red")
    if snap.total_cost_usd > 0:
        bar.append(f"  ${snap.total_cost_usd:.2f}", style="dim")
    if snap.elapsed_seconds > 0:
        bar.append(f"  {_format_duration(snap.elapsed_seconds)}", style="dim")
    console.print(bar)


def _render_all_steps(console: Console, snap: PlanSnapshot) -> None:
    """Render ALL steps across ALL phases as the full pipeline chain."""
    markers: dict[str, tuple[str, str]] = {
        "complete": ("✓", "green"),
        "running": ("◉", "bold cyan"),
        "dispatched": ("◉", "bold cyan"),
        "failed": ("✗", "bold red"),
        "pending": ("·", "dim"),
        "skipped": ("—", "dim"),
        "interrupted": ("!", "yellow"),
        "interacting": ("↔", "bright_cyan"),
    }

    phase_emoji: dict[str, str] = {
        "complete": "🍽",
        "running": "🔥",
        "gate_pending": "👅",
        "failed": "💥",
        "pending": "🥟",
    }

    for phase in snap.phases:
        emoji = phase_emoji.get(phase.status, "🥟")
        p_style = "bold white" if phase.status in ("running", "gate_pending") else "dim" if phase.status == "pending" else "green" if phase.status == "complete" else "red"

        line = Text("     ")
        line.append(emoji)
        line.append(f" {phase.phase_id}.", style=p_style)

        for i, step in enumerate(phase.steps):
            if i > 0:
                line.append(" ", style="dim")
            char, style = markers.get(step.status, ("·", "dim"))
            line.append(char, style=style)
            agent = step.agent_name
            if agent == "team" and step.team:
                agent = step.team[0].agent_name
            line.append(f"{agent[:12]}", style=style)

        # Gate indicator
        if phase.gate:
            g = phase.gate
            if g.status == "passed":
                line.append(" ✓gate", style="green")
            elif g.status == "failed":
                line.append(" ✗gate", style="bold red")

        console.print(line)


def _render_complete_bar(console: Console, snap: PlanSnapshot) -> None:
    """Render completion summary bar."""
    if snap.execution_status == "complete":
        emoji = "🍽"
        marker_style = "bold green"
        label = "SERVED"
    elif snap.execution_status == "failed":
        emoji = "💥"
        marker_style = "bold red"
        label = "BURNT"
    else:
        emoji = "🛎"
        marker_style = "bold yellow"
        label = snap.execution_status.upper()

    elapsed = _format_duration(snap.elapsed_seconds)

    line = Text()
    line.append("  ")
    line.append(emoji)
    line.append(f" {label} ", style=marker_style)
    line.append("━━ ", style="dim")
    line.append(f"{len(snap.phases)} phases · {snap.total_steps} steps", style="white")
    if snap.total_tokens > 0:
        line.append(f" · {snap.total_tokens:,} tok", style="dim")
    if snap.total_cost_usd > 0:
        line.append(f" · ${snap.total_cost_usd:.2f}", style="dim")
    if snap.elapsed_seconds > 0:
        line.append(f" · {elapsed}", style="dim")
    line.append(" ━━", style="dim")

    console.print(line)


def _render_plain(snap: PlanSnapshot) -> None:
    """Fallback plain-text rendering when rich is not available."""
    current: PhaseSnapshot | None = None
    for p in snap.phases:
        if p.status in ("running", "gate_pending"):
            current = p
            break
    if snap.execution_status in ("complete", "failed"):
        print(
            f"  {snap.execution_status.upper()} "
            f"-- {snap.steps_complete}/{snap.total_steps} steps",
            file=sys.stderr,
        )
        return
    if current:
        print(
            f"  Phase {current.phase_id}/{len(snap.phases)}: {current.name} "
            f"-- {snap.progress_pct:.0f}% "
            f"-- {snap.steps_complete}/{snap.total_steps} done",
            file=sys.stderr,
        )


def _truncate(text: str, width: int = 25) -> str:
    """Truncate text to *width* characters, adding ellipsis if needed."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _format_duration(seconds: float) -> str:
    """Format seconds into a compact human-readable duration string."""
    if seconds <= 0:
        return "0s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)
