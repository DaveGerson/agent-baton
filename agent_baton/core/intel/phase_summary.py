"""Phase-summary bead synthesis for cross-phase context continuity.

At each phase boundary the executor calls ``synthesize_phase_summary``
to distill the completed phase into a single bead.  When dispatching
the first step of a new phase, ``collect_phase_summary_chain`` retrieves
the most recent prior summaries and ``render_phase_summary_section``
formats them for injection into the delegation prompt.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_baton.models.bead import Bead, _generate_bead_id
from agent_baton.utils.time import utcnow_zulu as _utcnow

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.execution import StepResult

_log = logging.getLogger(__name__)

PHASE_SUMMARY_MAX_CHARS: int = 2000
PHASE_SUMMARY_MAX_CHAIN: int = 3
_MAX_FILES = 20
_MAX_DECISIONS = 5
_MAX_WARNINGS = 3
_MAX_OUTCOME_CHARS = 80


def synthesize_phase_summary(
    phase_id: int,
    phase_name: str,
    step_results: list[StepResult],
    decision_beads: list[Bead],
    warning_beads: list[Bead],
    task_id: str,
    bead_count: int,
) -> Bead:
    """Build a phase-summary bead from completed step results and beads."""
    files: list[str] = []
    seen_files: set[str] = set()
    for r in step_results:
        for f in getattr(r, "files_changed", []) or []:
            if f and f not in seen_files:
                seen_files.add(f)
                files.append(f)

    parts: list[str] = [f"Phase {phase_id}: {phase_name}"]

    if files:
        file_list = ", ".join(files[:_MAX_FILES])
        if len(files) > _MAX_FILES:
            file_list += f" (+{len(files) - _MAX_FILES} more)"
        parts.append(f"Files: {file_list}")

    if decision_beads:
        dec_lines = []
        for b in decision_beads[:_MAX_DECISIONS]:
            dec_lines.append(b.content[:_MAX_OUTCOME_CHARS])
        parts.append("Decisions: " + "; ".join(dec_lines))

    if warning_beads:
        warn_lines = []
        for b in warning_beads[:_MAX_WARNINGS]:
            warn_lines.append(b.content[:60])
        parts.append("Warnings: " + "; ".join(warn_lines))

    step_lines = []
    for r in step_results:
        outcome_short = (getattr(r, "outcome", "") or "")[:_MAX_OUTCOME_CHARS]
        outcome_short = outcome_short.replace("\n", " ").strip()
        if not outcome_short:
            outcome_short = r.status
        step_lines.append(f"  {r.step_id}: {outcome_short}")

    if step_lines:
        parts.append("Steps:")
        parts.extend(step_lines)

    content = "\n".join(parts)
    if len(content) > PHASE_SUMMARY_MAX_CHARS:
        # Truncate steps first, then decisions
        while step_lines and len(content) > PHASE_SUMMARY_MAX_CHARS:
            step_lines.pop()
            parts_rebuild = parts[:parts.index("Steps:") + 1] + step_lines
            if not step_lines:
                parts_rebuild = [p for p in parts if not p.startswith("Steps:") and not p.startswith("  ")]
            content = "\n".join(parts_rebuild)
        if len(content) > PHASE_SUMMARY_MAX_CHARS:
            content = content[:PHASE_SUMMARY_MAX_CHARS - 3] + "..."

    ts = _utcnow()
    return Bead(
        bead_id=_generate_bead_id(task_id, f"phase-{phase_id}", content, ts, bead_count),
        task_id=task_id,
        step_id=f"phase-{phase_id}",
        agent_name="engine",
        bead_type="outcome",
        content=content,
        confidence="high",
        scope="phase",
        tags=["phase-summary", f"phase-{phase_id}"],
        affected_files=files[:_MAX_FILES],
        created_at=ts,
        source="agent-signal",
    )


def collect_phase_summary_chain(
    bead_store: BeadStore,
    task_id: str,
    current_phase_id: int,
    max_chain: int = PHASE_SUMMARY_MAX_CHAIN,
) -> list[Bead]:
    """Return the most recent ``max_chain`` phase-summary beads prior to
    ``current_phase_id``."""
    all_beads = bead_store.query(task_id=task_id, limit=500)
    phase_summaries = [
        b for b in all_beads
        if b.bead_type == "outcome"
        and b.scope == "phase"
        and "phase-summary" in (b.tags or [])
    ]

    def _phase_num(b: Bead) -> int:
        for tag in b.tags or []:
            if tag.startswith("phase-") and tag != "phase-summary":
                try:
                    return int(tag.split("-", 1)[1])
                except (ValueError, IndexError):
                    pass
        return -1

    prior = [b for b in phase_summaries if _phase_num(b) < current_phase_id]
    prior.sort(key=_phase_num)
    return prior[-max_chain:]


def render_phase_summary_section(chain: list[Bead]) -> str:
    """Render phase-summary beads into a prompt section."""
    if not chain:
        return ""

    lines = [
        "## Prior Phase Context",
        "The following phases completed before your current phase.",
        "",
    ]

    for bead in chain:
        lines.append(f"### {bead.content.split(chr(10), 1)[0]}")
        remaining = bead.content.split("\n", 1)
        if len(remaining) > 1:
            lines.append(remaining[1])
        lines.append("")

    return "\n".join(lines).rstrip()
