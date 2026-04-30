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
    _render_step_chips(console, current)


def _render_phase_bar(
    console: Console,
    snap: PlanSnapshot,
    phase: PhaseSnapshot,
) -> None:
    """Render the main status line with phase info and progress bar."""
    total_phases = len(snap.phases)
    pct = snap.progress_pct

    # Status marker
    markers: dict[str, tuple[str, str]] = {
        "running": ("●", "cyan"),
        "gate_pending": ("●", "yellow"),
        "complete": ("✓", "green"),
        "failed": ("✗", "red"),
        "pending": ("○", "dim"),
    }
    marker_char, marker_style = markers.get(phase.status, ("○", "dim"))

    line = Text()
    line.append("── ", style="dim")
    line.append(marker_char, style=marker_style)
    line.append(f" Phase {phase.phase_id}/{total_phases}: ", style="bold")
    line.append(_truncate(phase.name, 25))
    line.append(" ─── ", style="dim")
    line.append(f"{pct:.0f}% ", style="bold cyan")

    # Progress bar (20 chars)
    bar_width = 20
    filled = int(pct / 100 * bar_width)
    line.append("█" * filled, style="cyan")
    line.append("░" * (bar_width - filled), style="dim")

    line.append(" ── ", style="dim")
    line.append(f"{snap.steps_complete}/{snap.total_steps} done", style="white")

    if snap.steps_running > 0:
        line.append(f" · {snap.steps_running} running", style="cyan")
    if snap.steps_failed > 0:
        line.append(f" · {snap.steps_failed} failed", style="red")
    if snap.total_cost_usd > 0:
        line.append(f" · ${snap.total_cost_usd:.2f}", style="dim")

    line.append(" ──", style="dim")

    console.print(line)


def _render_step_chips(console: Console, phase: PhaseSnapshot) -> None:
    """Render step status chips on the second line."""
    markers: dict[str, tuple[str, str]] = {
        "complete": ("✓", "green"),
        "running": ("●", "cyan"),
        "dispatched": ("●", "cyan"),
        "failed": ("✗", "red"),
        "pending": ("○", "dim"),
        "skipped": ("⊘", "dim"),
        "interrupted": ("●", "yellow"),
        "interacting": ("●", "bright_cyan"),
    }

    line = Text("   ")
    for i, step in enumerate(phase.steps):
        if i > 0:
            line.append("  ")
        char, style = markers.get(step.status, ("○", "dim"))
        line.append(char, style=style)
        line.append(f" {step.step_id} ", style=style)
        # Truncate agent name to fit
        agent = step.agent_name
        if agent == "team" and step.team:
            agent = step.team[0].agent_name
        agent_short = agent[:15]
        line.append(agent_short, style=style)

    console.print(line)


def _render_complete_bar(console: Console, snap: PlanSnapshot) -> None:
    """Render completion summary bar."""
    if snap.execution_status == "complete":
        marker = "✓"
        marker_style = "green"
        label = "COMPLETE"
    elif snap.execution_status == "failed":
        marker = "✗"
        marker_style = "red"
        label = "FAILED"
    else:
        marker = "⊘"
        marker_style = "yellow"
        label = snap.execution_status.upper()

    elapsed = _format_duration(snap.elapsed_seconds)

    line = Text()
    line.append("═══ ", style="dim")
    line.append(marker, style=marker_style)
    line.append(f" {label} ", style=f"bold {marker_style}")
    line.append("═══ ", style="dim")
    line.append(f"{len(snap.phases)} phases · {snap.total_steps} steps", style="white")
    if snap.total_tokens > 0:
        line.append(f" · {snap.total_tokens:,} tokens", style="dim")
    if snap.total_cost_usd > 0:
        line.append(f" · ${snap.total_cost_usd:.2f}", style="dim")
    if snap.elapsed_seconds > 0:
        line.append(f" · {elapsed}", style="dim")
    line.append(" ═══", style="dim")

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
