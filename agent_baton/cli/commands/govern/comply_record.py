"""``baton comply-record`` — Claude Code PostToolUse/Stop hook compliance recorder.

Reads a PostToolUse or Stop JSON payload from stdin and appends an entry to the
hash-chained compliance-audit.jsonl log.

Exit codes
----------
0   Always — fail-open by default.
    ``BATON_COMPLIANCE_FAIL_CLOSED=1`` → exit 1 on write errors.

Malformed or empty stdin is silently ignored (exit 0) regardless of
``BATON_COMPLIANCE_FAIL_CLOSED``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# File-writing tool names — kept in sync with policy_check.py.
_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _extract_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Extract file paths from a tool input dict (mirrors policy_check logic)."""
    if tool_name in _FILE_TOOLS:
        fp = tool_input.get("file_path") or tool_input.get("path", "")
        if fp:
            return [str(fp)]
        return []

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        tokens = command.split()
        paths: list[str] = []
        for tok in tokens:
            if tok.startswith("-"):
                continue
            if "/" in tok or tok.startswith("./") or tok.startswith("../"):
                paths.append(tok)
                if len(paths) >= 5:
                    break
        return paths

    return []


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "comply-record",
        help="PostToolUse/Stop hook: append an entry to the compliance audit log",
    )
    p.add_argument(
        "--event-type",
        metavar="TYPE",
        default="hook_tool_use",
        help='Event type to record (default: hook_tool_use; use session_stop for Stop hooks)',
    )
    p.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help=(
            "Path to compliance-audit.jsonl "
            "(default: .claude/team-context/compliance-audit.jsonl)"
        ),
    )
    p.add_argument(
        "--cwd",
        metavar="DIR",
        default=None,
        help="Project root for log path resolution (default: cwd)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    fail_closed = os.environ.get("BATON_COMPLIANCE_FAIL_CLOSED", "0") == "1"

    # ── Read stdin — malformed is always silent exit 0 ─────────────────────────
    try:
        raw = sys.stdin.read()
        payload: dict = json.loads(raw) if raw.strip() else {}
    except Exception:
        # Malformed stdin → silent exit 0 regardless of fail_closed.
        return

    agent_name: str = os.environ.get("CLAUDE_AGENT_NAME", "") or "unknown-agent"
    event_type: str = args.event_type or "hook_tool_use"

    tool_name: str = str(payload.get("tool_name", ""))
    tool_input: dict = payload.get("tool_input") or {}
    session_id: str = str(payload.get("session_id", ""))

    file_paths = _extract_paths(tool_name, tool_input)

    # ── Build entry ────────────────────────────────────────────────────────────
    entry: dict = {
        "event_type": event_type,
        "tool_name": tool_name or None,
        "file_paths": file_paths,
        "session_id": session_id or None,
        "agent_name": agent_name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }

    # ── Resolve log path ───────────────────────────────────────────────────────
    cwd = Path(args.cwd) if args.cwd else Path.cwd()
    if args.log:
        log_path = Path(args.log)
    else:
        log_path = cwd / ".claude" / "team-context" / "compliance-audit.jsonl"

    # ── Append via ComplianceChainWriter ───────────────────────────────────────
    try:
        from agent_baton.core.govern.compliance import ComplianceChainWriter

        writer = ComplianceChainWriter(log_path=log_path)
        writer.append(entry)
    except Exception as exc:
        print(
            f"baton comply-record: write error: {exc}",
            file=sys.stderr,
        )
        if fail_closed:
            sys.exit(1)
