"""``baton policy-check`` — Claude Code PreToolUse hook enforcement.

Reads a PreToolUse JSON payload from stdin, evaluates the tool call against
the active guardrail policy, and emits a deny decision (stdout JSON) when a
blocking rule is triggered.

Exit codes
----------
0   Always — fail-open by default.  ``BATON_POLICY_FAIL_CLOSED=1`` changes
    the exit code to 2 on error (bad stdin / unreadable policy) so the
    hook framework treats the error as a deny.

Deny output shape (stdout, exit 0):
    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                             "permissionDecision": "deny",
                             "permissionDecisionReason": "<reason>"}}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# Risk level → preset key mapping (mirrors executor.py / risk_and_policy.py).
_RISK_TO_PRESET: dict[str, str] = {
    "LOW": "standard_dev",
    "MEDIUM": "standard_dev",
    "HIGH": "regulated",
    "CRITICAL": "regulated",
}

# File-writing tool names whose input carries a single file_path.
_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _extract_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Return candidate file paths from a tool input dict.

    For file tools (Write/Edit/MultiEdit/NotebookEdit) we use ``file_path``
    directly.  For Bash we extract path-like tokens from ``command`` (tokens
    that contain "/" or start with "./" or "../" or "/"); flags (starting
    with "-") and pure-word tokens are excluded; capped at 5 tokens.
    """
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


def _resolve_preset_key(cwd: Path) -> str:
    """Determine the active policy preset key.

    Resolution order:
    1. ``.claude/active-policy.json`` → "preset" key
    2. ``.claude/team-context/plan.json`` → risk_level via _RISK_TO_PRESET
    3. Fallback: "standard_dev"
    """
    active_path = cwd / ".claude" / "active-policy.json"
    if active_path.exists():
        try:
            data = json.loads(active_path.read_text(encoding="utf-8"))
            preset = data.get("preset", "")
            if preset:
                return str(preset)
        except (OSError, json.JSONDecodeError):
            pass

    plan_path = cwd / ".claude" / "team-context" / "plan.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            risk = str(plan.get("risk_level", "")).upper()
            if risk in _RISK_TO_PRESET:
                return _RISK_TO_PRESET[risk]
        except (OSError, json.JSONDecodeError):
            pass

    return "standard_dev"


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "policy-check",
        help="PreToolUse hook: evaluate a tool call against the active policy",
    )
    p.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="Agent name (overrides $CLAUDE_AGENT_NAME)",
    )
    p.add_argument(
        "--cwd",
        metavar="DIR",
        default=None,
        help="Project root for policy resolution (default: cwd)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    fail_closed = os.environ.get("BATON_POLICY_FAIL_CLOSED", "0") == "1"

    # ── Read stdin ─────────────────────────────────────────────────────────────
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception as exc:
        print(
            f"baton policy-check: failed to parse stdin: {exc}",
            file=sys.stderr,
        )
        if fail_closed:
            sys.exit(2)
        return

    tool_name: str = str(payload.get("tool_name", ""))
    tool_input: dict = payload.get("tool_input") or {}
    session_id: str = str(payload.get("session_id", ""))

    if not tool_name:
        # No tool name — nothing to check.
        return

    # ── Resolve agent identity ─────────────────────────────────────────────────
    agent_name: str = (
        args.agent
        or os.environ.get("CLAUDE_AGENT_NAME", "")
        or "unknown-agent"
    )

    # ── Resolve project root ───────────────────────────────────────────────────
    cwd = Path(args.cwd) if args.cwd else Path.cwd()

    # ── Resolve active preset ──────────────────────────────────────────────────
    try:
        preset_key = _resolve_preset_key(cwd)
    except Exception as exc:
        print(
            f"baton policy-check: error resolving preset: {exc}",
            file=sys.stderr,
        )
        if fail_closed:
            sys.exit(2)
        return

    # ── Load policy ────────────────────────────────────────────────────────────
    try:
        from agent_baton.core.govern.policy import PolicyEngine

        engine = PolicyEngine(policies_dir=cwd / ".claude" / "policies")
        policy_set = engine.load_preset(preset_key)
        if policy_set is None:
            # Unknown preset key — fail-open; nothing to enforce.
            print(
                f"baton policy-check: preset '{preset_key}' not found — skipping",
                file=sys.stderr,
            )
            if fail_closed:
                sys.exit(2)
            return
    except Exception as exc:
        print(
            f"baton policy-check: error loading policy: {exc}",
            file=sys.stderr,
        )
        if fail_closed:
            sys.exit(2)
        return

    # ── Map tool call to evaluate() arguments ─────────────────────────────────
    allowed_paths = _extract_paths(tool_name, tool_input)
    tools = [tool_name]

    # ── Evaluate ───────────────────────────────────────────────────────────────
    try:
        violations = engine.evaluate(policy_set, agent_name, allowed_paths, tools)
    except Exception as exc:
        print(
            f"baton policy-check: error during evaluation: {exc}",
            file=sys.stderr,
        )
        if fail_closed:
            sys.exit(2)
        return

    # ── Filter to per-call blocking violations ─────────────────────────────────
    # require_agent / require_gate are structural/plan-level — never deny here.
    # path_allow with severity "warn" → advisory stderr only.
    block_violations = [
        v for v in violations
        if v.rule.rule_type in ("path_block", "tool_restrict")
        and v.rule.severity == "block"
    ]
    warn_violations = [
        v for v in violations
        if v.rule.rule_type == "path_allow"
        and v.rule.severity == "warn"
    ]

    # Advisory warnings to stderr.
    for v in warn_violations:
        print(
            f"baton policy-check [advisory]: {v.rule.description} — {v.details}",
            file=sys.stderr,
        )

    if not block_violations:
        return

    # ── Deny ───────────────────────────────────────────────────────────────────
    v = block_violations[0]

    # Determine what matched.
    if v.rule.rule_type == "path_block" and allowed_paths:
        matched = allowed_paths[0]
    elif v.rule.rule_type == "tool_restrict":
        matched = tool_name
    else:
        matched = v.details

    reason = (
        f"{preset_key} rule {v.rule.name} ({v.rule.rule_type}): "
        f"{v.rule.description}. "
        f"Matched: {matched} against pattern '{v.rule.pattern}'."
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
