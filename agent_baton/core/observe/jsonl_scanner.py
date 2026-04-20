"""Scan Claude Code session JSONL files for real per-step token usage.

Claude Code writes one JSONL file per session to::

    ~/.claude/projects/<url-encoded-project-path>/<session_id>.jsonl

Each line is a JSON object.  ``type="assistant"`` lines contain a
``message`` dict whose ``usage`` field holds the token breakdown::

    {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": 1234,
                "output_tokens": 56,
                "cache_read_input_tokens": 78901,
                "cache_creation_input_tokens": 234
            },
            "model": "claude-sonnet-4-6-20251001"
        },
        "timestamp": "2026-04-17T13:00:05.123Z"
    }

``SessionTokenScan`` aggregates all assistant turns whose ``timestamp``
falls between ``step_started_at`` and an optional ``step_ended_at`` (or
end-of-file when not provided).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

_log = logging.getLogger(__name__)


class SessionTokenScan(NamedTuple):
    """Aggregated token counts from a session JSONL window.

    Attributes:
        input_tokens: Sum of ``input_tokens`` across matched assistant turns.
        cache_read_tokens: Sum of ``cache_read_input_tokens``.
        cache_creation_tokens: Sum of ``cache_creation_input_tokens``.
        output_tokens: Sum of ``output_tokens``.
        model_id: Most-frequent non-synthetic model string observed.  Empty
            when no model info is available.
        turns_scanned: Number of assistant turns that fell in the window.
    """

    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    model_id: str
    turns_scanned: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.output_tokens


_EMPTY = SessionTokenScan(0, 0, 0, 0, "", 0)


def _parse_ts(s: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string into a UTC-aware datetime."""
    if not s:
        return None
    try:
        # Python 3.11+ handles 'Z' suffix natively; older versions need a shim.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def _project_slug(project_root: Path) -> str:
    """Return the URL-encoded project slug Claude Code uses as a directory name.

    Claude Code derives this by percent-encoding the absolute project path,
    replacing ``/`` with ``%2F`` (i.e. standard percent-encoding via
    ``urllib.parse.quote(path, safe='')``) and prepending a ``-`` when the
    path starts with ``/`` (i.e. on POSIX).

    The slug is the directory name under ``~/.claude/projects/``.
    """
    path_str = str(project_root.resolve())
    encoded = urllib.parse.quote(path_str, safe="")
    # Claude Code prefixes with '-' for absolute POSIX paths.
    if path_str.startswith("/"):
        encoded = "-" + encoded[len("%2F"):]  # drop the leading %2F, add -
    return encoded


def _find_session_jsonl(session_id: str, project_root: Path | None = None) -> Path | None:
    """Locate the JSONL file for *session_id*.

    Searches in order:
    1. ``~/.claude/projects/<slug>/<session_id>.jsonl`` when *project_root*
       is given.
    2. All subdirectories of ``~/.claude/projects/`` when *project_root* is
       absent or the slug-derived path doesn't exist.

    Returns ``None`` when not found.
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return None

    target_name = f"{session_id}.jsonl"

    if project_root is not None:
        slug = _project_slug(project_root)
        candidate = claude_projects / slug / target_name
        if candidate.exists():
            return candidate

    # Fallback: scan all project dirs.
    for subdir in claude_projects.iterdir():
        if not subdir.is_dir():
            continue
        candidate = subdir / target_name
        if candidate.exists():
            return candidate

    return None


def scan_session(
    session_id: str,
    step_started_at: str,
    *,
    step_ended_at: str = "",
    project_root: Path | None = None,
) -> SessionTokenScan:
    """Scan a Claude Code session JSONL and aggregate token usage for one step.

    Reads only lines whose ``timestamp`` field falls within the half-open
    interval ``[step_started_at, step_ended_at)``.  When *step_ended_at* is
    empty, all lines from *step_started_at* to end-of-file are included.

    Args:
        session_id: The session UUID (value of ``$CLAUDE_SESSION_ID``).
        step_started_at: ISO 8601 UTC timestamp marking when the step was
            dispatched (lower bound, inclusive).
        step_ended_at: ISO 8601 UTC timestamp marking when the step ended
            (upper bound, exclusive).  Empty means "scan to EOF".
        project_root: Repository root used to derive the Claude Code project
            slug.  Falls back to ``Path.cwd()`` when ``None``.

    Returns:
        A :class:`SessionTokenScan` with aggregated token counts.
        Returns ``_EMPTY`` (all zeros) when the file cannot be found,
        parsed, or contains no matching assistant turns.
    """
    if not session_id or not step_started_at:
        return _EMPTY

    root = project_root or Path(os.environ.get("BATON_PROJECT_ROOT", str(Path.cwd())))
    jsonl_path = _find_session_jsonl(session_id, root)
    if jsonl_path is None:
        _log.debug("session JSONL not found for session_id=%s", session_id)
        return _EMPTY

    start_dt = _parse_ts(step_started_at)
    end_dt = _parse_ts(step_ended_at) if step_ended_at else None

    if start_dt is None:
        _log.debug("could not parse step_started_at=%r", step_started_at)
        return _EMPTY

    total_input = 0
    total_cache_read = 0
    total_cache_creation = 0
    total_output = 0
    model_counts: dict[str, int] = {}
    turns = 0

    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "assistant":
                    continue

                ts = _parse_ts(obj.get("timestamp", ""))
                if ts is None or ts < start_dt:
                    continue
                if end_dt is not None and ts >= end_dt:
                    continue

                msg = obj.get("message", {})
                usage = msg.get("usage", {})
                total_input += usage.get("input_tokens", 0)
                total_cache_read += usage.get("cache_read_input_tokens", 0)
                total_cache_creation += usage.get("cache_creation_input_tokens", 0)
                total_output += usage.get("output_tokens", 0)

                model = msg.get("model", "")
                if model and "synthetic" not in model:
                    model_counts[model] = model_counts.get(model, 0) + 1

                turns += 1

    except OSError as exc:
        _log.warning("could not read session JSONL %s: %s", jsonl_path, exc)
        return _EMPTY

    if turns == 0:
        _log.debug(
            "no assistant turns found in window [%s, %s) for session %s",
            step_started_at,
            step_ended_at or "EOF",
            session_id,
        )
        return _EMPTY

    best_model = max(model_counts, key=model_counts.__getitem__) if model_counts else ""

    return SessionTokenScan(
        input_tokens=total_input,
        cache_read_tokens=total_cache_read,
        cache_creation_tokens=total_cache_creation,
        output_tokens=total_output,
        model_id=best_model,
        turns_scanned=turns,
    )
