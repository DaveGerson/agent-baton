"""CLI helper for recording governance override invocations (G1.6, bd-1a09).

Wraps :class:`agent_baton.core.govern.override_log.OverrideLog` with the
prompt / no-tty / argv plumbing every CLI override path needs.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _resolve_db_path() -> Path:
    """Locate the project ``baton.db`` for override-log persistence.

    Honours ``BATON_DB_PATH`` if set; otherwise reuses the team-context
    resolver from the execute CLI so the override row lands in the same
    DB as the execution.
    """
    import os

    explicit = os.environ.get("BATON_DB_PATH")
    if explicit:
        return Path(explicit)
    # Reuse the upward-walk team-context resolver from the execute CLI.
    from agent_baton.cli.commands.execution.execute import _resolve_context_root

    return _resolve_context_root() / "baton.db"


def record_override(
    flag: str,
    justification: str | None,
    *,
    command: str | None = None,
    argv: list[str] | None = None,
    interactive: bool | None = None,
) -> str | None:
    """Record an override invocation; return its ``override_id`` or ``None``.

    Behaviour:

    * If *flag* is empty / falsy, this is a no-op and the function
      returns ``None``.
    * If *justification* is empty AND ``sys.stdin.isatty()`` is true,
      prompt the operator interactively for one.
    * If *justification* is empty AND non-interactive, log the override
      with an empty justification and emit a stderr warning.
    * Otherwise the supplied justification is recorded as-is.

    Args:
        flag: Which override flag fired (``"--force"``, ``"--skip-gate"`` …).
        justification: Operator-supplied reason; may be ``None`` / blank.
        command: Logical CLI command name (default: derived from ``argv``).
        argv: Full process argv (default: ``sys.argv``).
        interactive: Override TTY detection (test seam).

    Returns:
        The new ``override_id`` on success, or ``None`` when *flag* was
        not actually used (no-op path).
    """
    if not flag:
        return None

    import os.path as _osp

    raw_argv = list(argv) if argv is not None else list(sys.argv)
    # Trim argv[0] to its basename so we don't leak $HOME / install paths
    # into the audit row's args_json.
    if raw_argv:
        raw_argv = [_osp.basename(raw_argv[0])] + raw_argv[1:]
    derived_command = command or _derive_command(raw_argv)

    import os

    is_interactive = (
        interactive
        if interactive is not None
        else bool(getattr(sys.stdin, "isatty", lambda: False)())
    )
    # Interactive prompt is OPT-IN to keep developer flow unblocked.
    # Set BATON_OVERRIDE_PROMPT=1 in high-trust orgs that want the prompt.
    prompt_enabled = os.environ.get("BATON_OVERRIDE_PROMPT", "").lower() in (
        "1", "true", "yes", "on",
    )

    text = (justification or "").strip()
    if not text and is_interactive and prompt_enabled:
        text = _prompt_for_justification(flag)
    if not text:
        sys.stderr.write(
            f"warning: {flag} used without --justification; recording empty "
            f"justification (set BATON_OVERRIDE_PROMPT=1 to require one).\n"
        )

    from agent_baton.core.govern.override_log import OverrideLog

    log = OverrideLog(db_path=_resolve_db_path())
    return log.record(
        flag=flag,
        command=derived_command,
        args=raw_argv,
        justification=text or None,
    )


def _derive_command(argv: list[str]) -> str:
    """Derive a logical command name like ``"baton execute gate"`` from argv."""
    if not argv:
        return "baton"
    parts = ["baton"]
    # Skip the executable path; collect non-flag positional tokens.
    for token in argv[1:]:
        if token.startswith("-"):
            break
        parts.append(token)
        if len(parts) >= 4:
            break
    return " ".join(parts)


def _prompt_for_justification(flag: str) -> str:
    """Interactively ask the operator for a justification."""
    sys.stderr.write(
        f"{flag} requires a justification for the audit log.\n"
        f"Enter reason (or Ctrl-C to abort): "
    )
    sys.stderr.flush()
    try:
        line = input().strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    return line
