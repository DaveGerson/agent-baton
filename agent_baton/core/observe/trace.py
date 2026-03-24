"""Trace recorder and renderer — capture and display structured task execution traces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.trace import TaskTrace, TraceEvent


class TraceRecorder:
    """Record structured task traces as JSON files under team-context/traces/.

    Each trace captures a DAG of timestamped events for a single orchestrated
    task.  Traces are persisted to `<team_context_root>/traces/<task_id>.json`.

    Usage::

        recorder = TraceRecorder()
        trace = recorder.start_trace("my-task-id", plan_snapshot=plan.to_dict())
        recorder.record_event(trace, "agent_start", agent_name="architect", phase=1, step=1)
        # ... more events ...
        path = recorder.complete_trace(trace, outcome="SHIP")
    """

    _DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")

    def __init__(self, team_context_root: Path | None = None) -> None:
        self._root = (team_context_root or self._DEFAULT_CONTEXT_ROOT).resolve()

    @property
    def traces_dir(self) -> Path:
        return self._root / "traces"

    # ── Write ──────────────────────────────────────────────────────────────

    def start_trace(
        self,
        task_id: str,
        plan_snapshot: dict | None = None,
    ) -> TaskTrace:
        """Create and return a new in-memory trace.

        The trace is NOT written to disk until :meth:`complete_trace` is called.
        """
        return TaskTrace(
            task_id=task_id,
            plan_snapshot=plan_snapshot or {},
            events=[],
            started_at=_utcnow(),
            completed_at=None,
            outcome=None,
        )

    def record_event(
        self,
        trace: TaskTrace,
        event_type: str,
        agent_name: str | None = None,
        phase: int = 0,
        step: int = 0,
        details: dict | None = None,
        duration_seconds: float | None = None,
    ) -> TraceEvent:
        """Append a new event to *trace* and return it."""
        event = TraceEvent(
            timestamp=_utcnow(),
            event_type=event_type,
            agent_name=agent_name,
            phase=phase,
            step=step,
            details=details or {},
            duration_seconds=duration_seconds,
        )
        trace.events.append(event)
        return event

    def complete_trace(
        self,
        trace: TaskTrace,
        outcome: str | None = None,
    ) -> Path:
        """Finalise *trace*, write it to disk, and return the file path."""
        trace.completed_at = _utcnow()
        trace.outcome = outcome

        self.traces_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.traces_dir / f"{trace.task_id}.json"
        out_path.write_text(
            json.dumps(trace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path

    # ── Read ───────────────────────────────────────────────────────────────

    def load_trace(self, task_id: str) -> TaskTrace | None:
        """Load a trace from disk by task_id.  Returns None if not found."""
        path = self.traces_dir / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TaskTrace.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def list_traces(self, count: int = 10) -> list[Path]:
        """Return the *count* most recently modified trace files."""
        if not self.traces_dir.exists():
            return []
        files = sorted(
            self.traces_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files[:count]

    def get_last_trace(self) -> TaskTrace | None:
        """Load and return the most recently modified trace, or None."""
        recent = self.list_traces(count=1)
        if not recent:
            return None
        return self.load_trace(recent[0].stem)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class TraceRenderer:
    """Render :class:`TaskTrace` objects as human-readable text."""

    # Indent used for event lines within a phase block.
    _INDENT = "  "

    def render_timeline(self, trace: TaskTrace) -> str:
        """Return a full human-readable timeline string for *trace*.

        Format::

            Task: <task_id>
            Started: <started_at>
            Outcome: <outcome>

            Phase 1 (<name or "Phase 1">):
              HH:MM:SS  [event_type]  agent_name  — detail
              ...
        """
        lines: list[str] = []
        lines.append(f"Task: {trace.task_id}")
        lines.append(f"Started: {trace.started_at}")
        if trace.outcome is not None:
            lines.append(f"Outcome: {trace.outcome}")

        if not trace.events:
            lines.append("")
            lines.append("(no events recorded)")
            return "\n".join(lines)

        # Group events by phase number; preserve insertion order.
        phases: dict[int, list[TraceEvent]] = {}
        for ev in trace.events:
            phases.setdefault(ev.phase, []).append(ev)

        for phase_num in sorted(phases.keys()):
            events = phases[phase_num]
            phase_label = _phase_label(phase_num, trace.plan_snapshot)
            lines.append("")
            lines.append(f"Phase {phase_num} ({phase_label}):")

            for ev in events:
                time_str = _hms(ev.timestamp)
                label = f"[{ev.event_type}]"
                agent_part = f"  {ev.agent_name}" if ev.agent_name else ""
                duration_part = (
                    f" ({int(ev.duration_seconds)}s)"
                    if ev.duration_seconds is not None
                    else ""
                )
                detail_part = _primary_detail(ev)
                detail_suffix = f" — {detail_part}" if detail_part else ""

                lines.append(
                    f"{self._INDENT}{time_str}  {label:<16}{agent_part}"
                    f"{duration_part}{detail_suffix}"
                )

        return "\n".join(lines)

    def render_summary(self, trace: TaskTrace) -> str:
        """Return a compact one-screen summary for *trace*."""
        events = trace.events
        total_events = len(events)

        # Unique agent names (excluding None).
        agents: list[str] = []
        seen: set[str] = set()
        for ev in events:
            if ev.agent_name and ev.agent_name not in seen:
                agents.append(ev.agent_name)
                seen.add(ev.agent_name)

        # Gate results.
        gate_results: list[str] = [
            ev.details.get("result", "?")
            for ev in events
            if ev.event_type == "gate_result"
        ]

        # Task duration.
        duration_str = _duration(trace.started_at, trace.completed_at)

        lines: list[str] = [
            f"Task:     {trace.task_id}",
            f"Outcome:  {trace.outcome or 'N/A'}",
            f"Duration: {duration_str}",
            f"Events:   {total_events}",
            f"Agents:   {len(agents)} ({', '.join(agents) if agents else 'none'})",
        ]

        if gate_results:
            lines.append(f"Gates:    {', '.join(gate_results)}")

        # Count event types.
        type_counts: dict[str, int] = {}
        for ev in events:
            type_counts[ev.event_type] = type_counts.get(ev.event_type, 0) + 1
        if type_counts:
            counts_str = "  ".join(
                f"{k}={v}"
                for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
            )
            lines.append(f"Breakdown: {counts_str}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _hms(iso: str) -> str:
    """Extract HH:MM:SS from an ISO timestamp string, falling back to the raw value."""
    if "T" in iso:
        time_part = iso.split("T", 1)[1]
        # Strip timezone suffix (+00:00 or Z).
        for sep in ("+", "-", "Z"):
            if sep in time_part:
                time_part = time_part.split(sep)[0]
                break
        return time_part[:8]
    return iso


def _duration(started_at: str, completed_at: str | None) -> str:
    """Return a human-readable duration string, or 'in progress'."""
    if not completed_at:
        return "in progress"
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
        secs = int((end - start).total_seconds())
        if secs < 60:
            return f"{secs}s"
        minutes, remainder = divmod(secs, 60)
        if minutes < 60:
            return f"{minutes}m {remainder}s"
        hours, mins = divmod(minutes, 60)
        return f"{hours}h {mins}m {remainder}s"
    except (ValueError, TypeError):
        return "unknown"


def _phase_label(phase_num: int, plan_snapshot: dict) -> str:
    """Try to read the phase name from the plan snapshot, else return a fallback."""
    try:
        phases = plan_snapshot.get("phases", [])
        # plan_snapshot phases are 0-indexed; phase_num in events is 1-indexed.
        idx = phase_num - 1
        if 0 <= idx < len(phases):
            name = phases[idx].get("name", "")
            if name:
                return name
    except (AttributeError, TypeError):
        pass
    return f"Phase {phase_num}"


def _primary_detail(ev: TraceEvent) -> str:
    """Return the most useful single detail string for a timeline row."""
    d = ev.details
    if not d:
        return ""
    # Prefer human-readable fields in priority order.
    for key in ("message", "reason", "result", "file", "gate", "note"):
        value = d.get(key, "")
        if value:
            return str(value)
    # Fall back to the first value in the dict.
    first_val = next(iter(d.values()), "")
    return str(first_val) if first_val else ""
