"""Rich-based terminal renderer for plan visualization.

Produces a structured, color-coded view of a ``PlanSnapshot`` on the
terminal using the ``rich`` library.  Gracefully exits with an install
hint when ``rich`` is not available.
"""
from __future__ import annotations

from agent_baton.visualize._colors import RISK_COLORS, STATUS_COLORS
from agent_baton.visualize.snapshot import (
    GateSnapshot,
    PhaseSnapshot,
    PlanSnapshot,
    StepSnapshot,
)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


# ---------------------------------------------------------------------------
# Status markers (terminal glyphs)
# ---------------------------------------------------------------------------

_STATUS_MARKER: dict[str, tuple[str, str]] = {
    # status -> (glyph, rich_style)
    "complete":     ("✓", "green"),        # check
    "passed":       ("✓", "green"),
    "running":      ("●", "cyan"),          # filled circle
    "dispatched":   ("●", "cyan"),
    "failed":       ("✗", "red"),            # cross
    "pending":      ("○", "dim"),            # open circle
    "not_started":  ("○", "dim"),
    "skipped":      ("⊘", "dim"),            # circled division slash
    "interrupted":  ("●", "yellow"),
    "interacting":  ("●", "bright_cyan"),
    "gate_pending": ("●", "yellow"),
}


def _marker(status: str) -> Text:
    """Return a styled single-char status marker."""
    glyph, style = _STATUS_MARKER.get(status, ("○", "dim"))
    return Text(glyph, style=style)


def _truncate(text: str, width: int = 35) -> str:
    """Truncate text with '...' suffix when it exceeds *width*."""
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
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


def _format_tokens(tokens: int) -> str:
    """Format token count with comma separators."""
    return f"{tokens:,}"


def _format_cost(cost: float) -> str:
    """Format USD cost."""
    return f"${cost:.2f}"


def _progress_bar(pct: float, width: int = 20) -> Text:
    """Build a block-character progress bar."""
    filled = int(pct / 100 * width)
    empty = width - filled
    bar = Text()
    bar.append("█" * filled, style="cyan")
    bar.append("░" * empty, style="dim")
    return bar


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_header(console: Console, snap: PlanSnapshot) -> None:
    """Render the top panel with task info and classification badges."""
    header = Text()
    header.append("Agent Baton · Plan Viewer", style="bold white")
    console.print(Panel(header, style="blue", expand=True))
    console.print()

    # Task identity
    console.print(f"  Task:     {snap.task_summary}")
    console.print(f"  Task ID:  {snap.task_id}")
    console.print(f"  Created:  {snap.created_at}")
    console.print()

    # Classification badges row 1
    risk_style = RISK_COLORS.get(snap.risk_level, ("white", "", "#ffffff"))[0]
    line1 = Text("  Risk: ")
    line1.append("■ ", style=risk_style)
    line1.append(snap.risk_level, style=risk_style)
    line1.append(f"    Budget: {snap.budget_tier}")
    line1.append(f"    Complexity: {snap.complexity}")
    line1.append(f"    Stack: {snap.detected_stack}")
    console.print(line1)

    # Classification badges row 2
    line2 = Text(f"  Type: {snap.task_type}")
    line2.append(f"   Mode: {snap.execution_mode}")
    line2.append(f"        Source: {snap.classification_source}")
    console.print(line2)
    console.print()


def _render_status_bar(console: Console, snap: PlanSnapshot) -> None:
    """Render the execution progress bar and summary counts."""
    if snap.execution_status == "not_started":
        return

    # Status dot
    status_line = Text("  Status: ")
    m = _marker(snap.execution_status)
    status_line.append_text(m)
    status_line.append(f" {snap.execution_status}")
    status_line.append("     Progress: ")
    status_line.append_text(_progress_bar(snap.progress_pct))
    status_line.append(f"  {snap.progress_pct:.0f}%")
    console.print(status_line)

    # Elapsed and step counts
    elapsed = _format_duration(snap.elapsed_seconds)
    steps_line = Text(f"  Elapsed: {elapsed}")
    steps_line.append(
        f"       Steps: {snap.steps_complete}/{snap.total_steps} done"
        f" · {snap.steps_running} running"
        f" · {snap.steps_failed} failed"
    )
    console.print(steps_line)

    # Tokens and cost
    tokens_line = Text(f"  Tokens: {_format_tokens(snap.total_tokens)}")
    tokens_line.append(f"       Cost: {_format_cost(snap.total_cost_usd)}")
    console.print(tokens_line)
    console.print()


def _render_step(console: Console, step: StepSnapshot) -> None:
    """Render a single step line with status marker and metadata."""
    m = _marker(step.status)
    status_style = STATUS_COLORS.get(step.status, ("white", "", "#ffffff"))[0]
    parallel_suffix = "  ∥" if step.parallel_safe else ""

    if step.team:
        # Team step -- show "Team Step" label
        line = Text("  ")
        line.append_text(m)
        line.append("  ")
        line.append(step.step_id, style=status_style)
        line.append("  Team Step")
        line.append(parallel_suffix, style="dim")
        console.print(line)

        # Team members with tree chars
        for i, member in enumerate(step.team):
            is_last = i == len(step.team) - 1
            branch = "└─" if is_last else "├─"
            member_marker = _marker(member.status)
            member_line = Text(f"           {branch} ")
            member_line.append_text(member_marker)
            member_line.append(f" {member.member_id}  ", style=status_style)
            member_line.append(f"{member.agent_name}", style=status_style)
            member_line.append(f"   {member.role}", style="dim")
            console.print(member_line)
    else:
        # Regular step
        agent_pad = f"{step.agent_name:<20}"
        desc = _truncate(step.task_description)
        line = Text("  ")
        line.append_text(m)
        line.append("  ")
        line.append(step.step_id, style=status_style)
        line.append("  ")
        line.append(agent_pad, style=status_style)
        line.append(f" {step.model:<7} ")
        line.append(desc)
        line.append(parallel_suffix, style="dim")
        console.print(line)

    # Dependencies (shown below in dim)
    if step.depends_on:
        dep_str = ", ".join(step.depends_on)
        console.print(Text(f"           └─ depends on: {dep_str}", style="dim"))


def _render_gate(console: Console, gate: GateSnapshot) -> None:
    """Render a gate line with status marker."""
    m = _marker(gate.status)
    gate_line = Text("  Gate: ")
    gate_line.append(f"{gate.gate_type} ")
    gate_line.append_text(m)
    gate_line.append(f" {gate.status.upper()}", style=STATUS_COLORS.get(gate.status, ("white", "", "#ffffff"))[0])
    if gate.command:
        gate_line.append(f" — {gate.command}", style="dim")
    console.print(gate_line)


def _render_phase(console: Console, phase: PhaseSnapshot, snap: PlanSnapshot) -> None:
    """Render a complete phase block."""
    # Phase header with status badge
    status_style = STATUS_COLORS.get(phase.status, ("white", "", "#ffffff"))[0]
    m = _marker(phase.status)

    # Build right-side badge
    badge = Text()
    badge.append_text(m)
    badge.append(f" {phase.status.upper()}", style=status_style)

    # Use Rule for the phase separator
    phase_title = f" Phase {phase.phase_id}: {phase.name} "
    console.print()
    rule = Rule(
        title=phase_title,
        style="dim",
        end=f" {badge.plain} ",
    )
    # Manually build the phase header to include styled badge
    header_line = Text()
    header_line.append(f"── Phase {phase.phase_id}: {phase.name} ", style="bold")
    # Pad to fill
    pad_width = max(0, console.width - len(header_line.plain) - len(badge.plain) - 4)
    header_line.append("─" * pad_width, style="dim")
    header_line.append(" ")
    header_line.append_text(badge)
    header_line.append(" ──", style="dim")
    console.print(header_line)

    # Steps
    for step in phase.steps:
        _render_step(console, step)

    # Gate
    if phase.gate:
        _render_gate(console, phase.gate)

    console.print()


def _render_footer(console: Console, snap: PlanSnapshot) -> None:
    """Render the agent list and amendment count."""
    if snap.total_agents:
        agents_str = ", ".join(snap.total_agents)
        # Wrap at ~70 chars
        console.print(Text(f"  Agents: {agents_str}", style="dim"))

    if snap.amendment_count > 0:
        console.print(Text(f"  Amendments: {snap.amendment_count}", style="dim"))
    else:
        console.print(Text("  Amendments: 0", style="dim"))

    console.print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(snapshot: PlanSnapshot, console: Console | None = None) -> None:
    """Render a ``PlanSnapshot`` to the terminal using Rich.

    Args:
        snapshot: The plan snapshot to visualize.
        console: Optional Rich ``Console`` instance. A new one is created
            when not provided.

    Raises:
        SystemExit: When the ``rich`` package is not installed.
    """
    if not _HAS_RICH:
        print("error: 'rich' is required for terminal visualization.")
        print("  Install with: pip install agent-baton[viz]")
        raise SystemExit(1)

    console = console or Console()
    _render_header(console, snapshot)
    _render_status_bar(console, snapshot)
    for phase in snapshot.phases:
        _render_phase(console, phase, snapshot)
    _render_footer(console, snapshot)
