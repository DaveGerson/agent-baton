"""Wave 6.2 Part A — ``baton swarm`` CLI subcommand (bd-707d).

Exposes swarm refactoring via:

    baton swarm refactor <directive-json> [--max-agents N] [--language python]
                                          [--model claude-haiku]
                                          [-y | --yes]
                                          [--dry-run]
                                          [--require-approval-bead [BEAD_ID]]
                                          [--no-require-approval-bead]

The directive is passed as a JSON string to avoid argparse juggling complex
types.  Example:

    baton swarm refactor '{"kind":"rename-symbol","old":"mymod.Foo","new":"mymod.Bar"}'

EXPERIMENTAL gate (bd-18f6)
----------------------------
``baton swarm`` is gated behind ``BATON_EXPERIMENTAL=swarm``.  The current
dispatcher (Wave 6.2 Part A) is a v1 stub: partition plans are real but
agent dispatch is **not yet wired** end-to-end (see bd-c925, bd-2b9f).

Without the flag the command exits immediately with exit code 2 and an
explanatory message.  This keeps the stub off the happy-path for users who
have not opted in.

Feature gate: ``BATON_SWARM_ENABLED=1`` required (in env or baton.yaml).
When disabled the command exits with a clear error message.

Sign-off gate (bd-707d)
-----------------------
Without ``--yes`` the operator is shown a partition preview (files affected,
chunk count, estimated cost) and must type ``y`` / ``yes`` to proceed.  EOF
or empty input → refuse (safe default).

With ``--yes`` the preview is still printed but the prompt is skipped.

With ``--dry-run`` the preview is printed and the command exits without
dispatching or prompting.

With ``--require-approval-bead`` the command additionally demands that a
recent open approval bead tagged ``swarm-refactor`` exists in the bead
store.  Even ``--yes`` cannot bypass this check.

On interactive confirmation an approval bead is automatically filed so
every dispatch has a permanent audit-trail entry.

Team-mode enforcement (end-user readiness #8)
---------------------------------------------
When ``BATON_APPROVAL_MODE=team`` is set in the environment, ``--require-
approval-bead`` is **on by default** to enforce the second-reviewer pattern
teams need.  Operators can override this with ``--no-require-approval-bead``,
but doing so files an audit WARNING bead so the override is traceable.

When ``BATON_APPROVAL_MODE=local`` (or unset), the opt-in behavior from
PR #59 is preserved.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_SWARM_ENABLED_ENV = "BATON_SWARM_ENABLED"
_DISABLED_MSG = (
    "Swarm is disabled; set BATON_SWARM_ENABLED=1 in baton.yaml or the "
    "BATON_SWARM_ENABLED environment variable to enable it."
)

_EXPERIMENTAL_ENV = "BATON_EXPERIMENTAL"
_EXPERIMENTAL_BLOCKED_MSG = (
    "error: `baton swarm` is experimental — current dispatcher does not invoke real agents.\n"
    "To opt in (development/testing only), set BATON_EXPERIMENTAL=swarm.\n"
    "See bd-c925 / bd-2b9f for the integration roadmap."
)
_EXPERIMENTAL_WARNING_MSG = (
    "[EXPERIMENTAL] swarm dispatcher is a v1 stub — chunk plans are real but "
    "agent dispatch is not yet wired (bd-c925)."
)

# Conservative cost model (Wave 6.2 Part A design, bd-707d).
# Assume est_tokens_per_chunk input tokens; half that as output.
# Pricing per 1 M tokens (April 2026 baseline):
#   Haiku:   $0.25 input / $1.25 output
#   Sonnet:  $3.00 input / $15.00 output
#   Opus:    $15.00 input / $75.00 output
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku":       (0.25 / 1_000_000,  1.25 / 1_000_000),
    "haiku":              (0.25 / 1_000_000,  1.25 / 1_000_000),
    "haiku-1":            (0.25 / 1_000_000,  1.25 / 1_000_000),
    "haiku-2":            (0.25 / 1_000_000,  1.25 / 1_000_000),
    "claude-sonnet":      (3.00 / 1_000_000, 15.00 / 1_000_000),
    "sonnet":             (3.00 / 1_000_000, 15.00 / 1_000_000),
    "sonnet-1":           (3.00 / 1_000_000, 15.00 / 1_000_000),
    "sonnet-2":           (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-opus":        (15.00 / 1_000_000, 75.00 / 1_000_000),
    "opus":               (15.00 / 1_000_000, 75.00 / 1_000_000),
}
_DEFAULT_EST_TOKENS_PER_CHUNK = 8_000
_APPROVAL_BEAD_TAG = "swarm-refactor"
_APPROVAL_BEAD_MAX_AGE_MINUTES = 5
_APPROVAL_MODE_ENV = "BATON_APPROVAL_MODE"


# ---------------------------------------------------------------------------
# Registration + handler (auto-discovered by cli/main.py)
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``swarm`` subcommand and its sub-subcommands."""
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "swarm",
        help=(
            "[EXPERIMENTAL] Massive parallel AST-aware swarm refactoring "
            "(Wave 6.2, bd-707d). Requires BATON_EXPERIMENTAL=swarm to run."
        ),
        description=(
            "[EXPERIMENTAL] Dispatch up to 100 Haiku agents in parallel to apply "
            "a single refactor directive across provably-independent code chunks.  "
            "The v1 dispatcher stub produces real partition plans but does NOT yet "
            "invoke real agents end-to-end (see bd-c925 / bd-2b9f).  "
            "Requires BATON_EXPERIMENTAL=swarm and BATON_SWARM_ENABLED=1."
        ),
    )
    swarm_sub = parser.add_subparsers(dest="swarm_command", metavar="COMMAND")
    swarm_sub.required = True

    # -- refactor sub-subcommand --
    refactor_parser = swarm_sub.add_parser(
        "refactor",
        help="Apply a refactor directive via a swarm of parallel Haiku agents.",
        description=(
            "Partition the codebase into independent AST chunks and dispatch "
            "one Haiku agent per chunk.  Results are coalesced via sequential "
            "deterministic rebase.\n\n"
            "A partition preview is always shown before dispatch.  Without "
            "--yes the operator must confirm interactively."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_REFACTOR_EPILOG,
    )
    refactor_parser.add_argument(
        "directive_json",
        metavar="DIRECTIVE_JSON",
        help=(
            "JSON object describing the refactor directive.  "
            'Required keys: "kind" (rename-symbol | change-signature | '
            "replace-import | migrate-api) plus directive-specific fields.  "
            "Pass as a single-quoted string."
        ),
    )
    refactor_parser.add_argument(
        "--max-agents",
        type=int,
        default=100,
        metavar="N",
        help="Maximum number of parallel chunk agents (default: 100, max: 100).",
    )
    refactor_parser.add_argument(
        "--language",
        choices=["python"],
        default="python",
        help="Target language for AST partitioning (default: python; v1 only).",
    )
    refactor_parser.add_argument(
        "--model",
        default="claude-haiku",
        metavar="MODEL",
        help="LLM model tier for chunk agents (default: claude-haiku).",
    )
    refactor_parser.add_argument(
        "--codebase-root",
        default=None,
        metavar="PATH",
        help=(
            "Root directory of the Python project to refactor.  "
            "Defaults to the current working directory."
        ),
    )
    refactor_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Print the partition preview and exit without prompting or "
            "dispatching agents.  Useful for 'what would happen' previews."
        ),
    )
    refactor_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        default=False,
        dest="yes",
        help=(
            "Skip the interactive confirmation prompt (use for CI/automation; "
            "the preview is still printed for the audit trail)."
        ),
    )
    refactor_parser.add_argument(
        "--require-approval-bead",
        nargs="?",
        const=_SENTINEL_REQUIRE_APPROVAL,
        default=None,
        metavar="BEAD_ID",
        dest="require_approval_bead",
        help=(
            "Require an operator approval bead before dispatching.  "
            "Without BEAD_ID: looks up a recent open approval bead tagged "
            f"'{_APPROVAL_BEAD_TAG}' (created within the last "
            f"{_APPROVAL_BEAD_MAX_AGE_MINUTES} minutes).  "
            "With BEAD_ID: verifies the named bead exists, has "
            "bead_type='approval', tag 'swarm-refactor', and status='open'.  "
            "Even --yes cannot bypass this check.  "
            "Intended for regulated environments.  "
            "Always on by default when BATON_APPROVAL_MODE=team."
        ),
    )
    refactor_parser.add_argument(
        "--no-require-approval-bead",
        action="store_true",
        default=False,
        dest="no_require_approval_bead",
        help=(
            "Explicitly disable the approval-bead requirement.  "
            "In local mode (default) this is already the default behavior.  "
            "In team mode (BATON_APPROVAL_MODE=team) using this flag overrides "
            "the team-mode default and files an audit WARNING bead so the "
            "override is traceable."
        ),
    )

    return parser


# Sentinel for --require-approval-bead with no BEAD_ID argument.
_SENTINEL_REQUIRE_APPROVAL = "__REQUIRE_APPROVAL_BEAD_LOOKUP__"

_REFACTOR_EPILOG = """\
Examples:

  Rename a symbol (interactive prompt):
    baton swarm refactor '{"kind":"rename-symbol","old":"mymod.OldName","new":"mymod.NewName"}'

  Replace an import, skip prompt (CI mode):
    baton swarm refactor --yes '{"kind":"replace-import","old":"requests","new":"httpx"}'

  Preview without dispatching:
    baton swarm refactor --dry-run '{"kind":"replace-import","old":"requests","new":"httpx"}'

  Require a pre-filed approval bead (regulated env):
    baton swarm refactor --require-approval-bead '{"kind":"rename-symbol",...}'

  Require a specific approval bead by ID:
    baton swarm refactor --require-approval-bead bd-ab12 '{"kind":"rename-symbol",...}'

  Change a function signature:
    baton swarm refactor '{"kind":"change-signature","symbol":"mymod.my_func","transform":{"add_param":"timeout=30"}}'

  Migrate an API pattern:
    baton swarm refactor '{"kind":"migrate-api","old_call_pattern":"requests.get(...)","new_call_template":"httpx.get(...)"}'

Environment:
  BATON_EXPERIMENTAL=swarm   Required to run (stub — real dispatch not wired).
  BATON_SWARM_ENABLED=1      Required to enable swarm dispatch.
"""


def handler(args: argparse.Namespace) -> None:
    """Dispatch the appropriate swarm sub-command handler."""
    # Experimental gate must fire BEFORE the sign-off gate so blocked users
    # never see the sign-off prompt (bd-18f6).
    _check_experimental()
    _check_enabled()
    if args.swarm_command == "refactor":
        _handle_refactor(args)
    else:
        print(f"Unknown swarm command: {args.swarm_command}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_refactor(args: argparse.Namespace) -> None:
    """Parse directive JSON and dispatch the swarm with sign-off gate."""
    # Parse directive
    try:
        directive_data = json.loads(args.directive_json)
    except json.JSONDecodeError as exc:
        print(
            f"Error: DIRECTIVE_JSON is not valid JSON: {exc}\n"
            "Pass the directive as a single-quoted JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from agent_baton.core.swarm.partitioner import RefactorDirective
        directive = RefactorDirective.from_dict(directive_data)
    except (KeyError, ValueError) as exc:
        print(f"Error: Invalid directive: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Validate max_agents
    max_agents = min(args.max_agents, 100)
    if max_agents < 1:
        print("Error: --max-agents must be at least 1.", file=sys.stderr)
        sys.exit(1)

    codebase_root = Path(args.codebase_root) if args.codebase_root else Path.cwd()
    if not codebase_root.is_dir():
        print(
            f"Error: --codebase-root {codebase_root} is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --dry-run: partition + print preview, then stop.
    if args.dry_run:
        _dry_run_partition(directive, codebase_root, max_agents, args.model)
        return

    # Partition first so we can show the preview.
    chunks = _partition(directive, codebase_root, max_agents)
    if not chunks:
        print("No call sites found — nothing to refactor.")
        return

    # Always print the preview (audit trail even for --yes).
    preview_text = _build_preview_text(chunks, directive, args.model)
    print(preview_text)

    # Resolve approval mode (read fresh from env — never from a cached object).
    approval_mode = _get_approval_mode()
    no_require = getattr(args, "no_require_approval_bead", False)
    explicit_require = getattr(args, "require_approval_bead", None)

    # Print the mode notice at launch so operators always know which regime
    # is active and how to change it.
    _print_approval_mode_notice(approval_mode, no_require)

    # Resolve the effective require_approval value:
    #   team mode  → on by default (sentinel), unless --no-require-approval-bead
    #   local mode → honour whatever the operator passed (opt-in, PR #59 compat)
    if approval_mode == "team" and not no_require:
        # Default to sentinel lookup when the operator did not explicitly pass
        # --require-approval-bead with a specific bead ID.
        effective_require: str | None = (
            explicit_require if explicit_require is not None
            else _SENTINEL_REQUIRE_APPROVAL
        )
    else:
        effective_require = explicit_require

    # When team mode is overridden via --no-require-approval-bead, file an
    # audit bead so the bypass is permanently traceable.
    if approval_mode == "team" and no_require:
        operator = _get_operator_identity()
        _file_team_override_audit_bead(
            operator=operator,
            directive=directive,
        )

    # --require-approval-bead gate (cannot be bypassed by --yes).
    if effective_require is not None:
        _check_approval_bead(effective_require)

    # Determine approval_bead_id to pass to dispatcher.
    approval_bead_id = ""

    # Interactive confirmation (skipped when --yes).
    skip_prompt = getattr(args, "yes", False)
    if skip_prompt:
        print(
            "[SIGN-OFF] --yes flag set: skipping interactive prompt.  "
            "Proceeding with dispatch."
        )
    else:
        confirmed = _prompt_confirm(
            "Proceed with swarm dispatch?",
            default_no=True,
        )
        if not confirmed:
            print("Aborted — operator did not confirm.", file=sys.stderr)
            sys.exit(1)
        # Auto-file approval bead on confirmation.
        approval_bead_id = _file_approval_bead(
            chunks=chunks,
            directive=directive,
            model=args.model,
            preview_text=preview_text,
            codebase_root=codebase_root,
        )

    _dispatch_swarm(
        directive=directive,
        codebase_root=codebase_root,
        max_agents=max_agents,
        model=args.model,
        chunks=chunks,
        approval_bead_id=approval_bead_id,
    )


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------


def _estimate_cost(model: str, total_tokens: int) -> float:
    """Estimate USD cost using conservative all-input half-output assumption."""
    in_price, out_price = _PRICING.get(model, _PRICING["claude-haiku"])
    # Conservative: all tokens as input, half as output.
    return total_tokens * in_price + (total_tokens // 2) * out_price


def _build_preview_text(
    chunks: list,
    directive: object,
    model: str,
    est_tokens_per_chunk: int = _DEFAULT_EST_TOKENS_PER_CHUNK,
) -> str:
    """Build a human-readable preview string for the planned swarm dispatch.

    Always printed before dispatch (even with --yes) for the audit trail.
    """
    total_files = len({f for chunk in chunks for f in chunk.files})
    total_call_sites = sum(len(chunk.call_sites) for chunk in chunks)
    n_chunks = len(chunks)
    est_tokens_total = n_chunks * est_tokens_per_chunk
    est_cost_usd = _estimate_cost(model, est_tokens_total)

    lines: list[str] = [
        "=== SWARM REFACTOR PREVIEW ===",
        f"Directive: {directive.kind} — {_directive_summary(directive)}",
        f"Model: {model}",
        f"Chunks: {n_chunks} (max-agents)",
        f"Files affected: {total_files}",
        f"Call sites: {total_call_sites}",
        f"Estimated tokens: ~{est_tokens_total:,}",
        f"Estimated cost: ~${est_cost_usd:.2f}",
        "",
        "Files (first 20):",
    ]

    sorted_files = sorted({str(f) for chunk in chunks for f in chunk.files})
    for f in sorted_files[:20]:
        lines.append(f"  {f}")
    if len(sorted_files) > 20:
        lines.append(f"  ... and {len(sorted_files) - 20} more")
    lines.append("")

    return "\n".join(lines)


def _directive_summary(directive: object) -> str:
    """Return a one-line human-readable summary of a RefactorDirective."""
    kind = getattr(directive, "kind", "unknown")
    if kind == "rename-symbol":
        return f"{getattr(directive, 'old', '?')} → {getattr(directive, 'new', '?')}"
    if kind == "change-signature":
        return f"symbol={getattr(directive, 'symbol', '?')}"
    if kind == "replace-import":
        return f"{getattr(directive, 'old', '?')} → {getattr(directive, 'new', '?')}"
    if kind == "migrate-api":
        old = getattr(directive, "old_call_pattern", "?")
        new = getattr(directive, "new_call_template", "?")
        return f"{old} → {new}"
    return kind


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def _prompt_confirm(prompt: str, default_no: bool = True) -> bool:
    """Interactive confirmation.  Returns False on EOF / empty / 'n' / 'no'.

    Args:
        prompt: The question to display.
        default_no: When True (default), an empty answer means NO.  When
            False, an empty answer means YES.

    Returns:
        True only when the operator explicitly typed 'y', 'yes', 'Y', or 'YES'.
    """
    suffix = "[y/N]" if default_no else "[Y/n]"
    try:
        answer = input(f"{prompt} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return not default_no
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Approval bead helpers
# ---------------------------------------------------------------------------


def _check_approval_bead(require_approval: str) -> None:
    """Verify the required approval bead constraint.

    Args:
        require_approval: Either _SENTINEL_REQUIRE_APPROVAL (lookup mode) or
            a specific bead_id (verify mode).

    Exits with code 1 when the constraint is not satisfied.
    """
    bead_store = _get_bead_store()
    if bead_store is None:
        print(
            "Error: --require-approval-bead set but no bead store is available "
            "(no baton.db found).  File a bead first: "
            "baton beads create --type approval --tag swarm-refactor",
            file=sys.stderr,
        )
        sys.exit(1)

    if require_approval == _SENTINEL_REQUIRE_APPROVAL:
        # Lookup mode: find any recent approval bead with the right tag.
        recent = bead_store.find_recent_approvals(
            tag=_APPROVAL_BEAD_TAG,
            max_age_minutes=_APPROVAL_BEAD_MAX_AGE_MINUTES,
        )
        if not recent:
            print(
                f"Error: --require-approval-bead set but no open approval bead "
                f"tagged '{_APPROVAL_BEAD_TAG}' was found in the last "
                f"{_APPROVAL_BEAD_MAX_AGE_MINUTES} minutes.\n"
                "File one with: "
                "baton beads create --type approval --tag swarm-refactor",
                file=sys.stderr,
            )
            sys.exit(1)
        bead_id = recent[0].bead_id
        print(
            f"[SIGN-OFF] Approval bead found: {bead_id} "
            f"(created {recent[0].created_at}).  Proceeding."
        )
    else:
        # Verify mode: validate a specific bead_id.
        bead_id = require_approval
        bead = bead_store.read(bead_id)
        if bead is None:
            print(
                f"Error: --require-approval-bead: bead '{bead_id}' not found.",
                file=sys.stderr,
            )
            sys.exit(1)
        if bead.bead_type != "approval":
            print(
                f"Error: --require-approval-bead: bead '{bead_id}' has "
                f"bead_type='{bead.bead_type}', expected 'approval'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if _APPROVAL_BEAD_TAG not in bead.tags:
            print(
                f"Error: --require-approval-bead: bead '{bead_id}' is missing "
                f"tag '{_APPROVAL_BEAD_TAG}'.  Tags present: {bead.tags}.",
                file=sys.stderr,
            )
            sys.exit(1)
        if bead.status != "open":
            print(
                f"Error: --require-approval-bead: bead '{bead_id}' has "
                f"status='{bead.status}', expected 'open' (not closed/expired).",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"[SIGN-OFF] Approval bead verified: {bead_id} "
            f"(created {bead.created_at}).  Proceeding."
        )


def _file_approval_bead(
    chunks: list,
    directive: object,
    model: str,
    preview_text: str,
    codebase_root: Path,
) -> str:
    """File an approval bead on operator confirmation.

    Returns the bead_id (for print + passing to dispatcher), or "" if the
    bead store is unavailable (non-fatal — the dispatch still proceeds).
    """
    bead_store = _get_bead_store()
    if bead_store is None:
        _log.debug(
            "_file_approval_bead: no bead store available; skipping bead creation"
        )
        return ""

    try:
        from agent_baton.models.bead import Bead
        import datetime as _dt
        import hashlib as _hashlib

        affected_files = sorted({str(f) for chunk in chunks for f in chunk.files})
        operator = _get_operator_identity()
        content = (
            f"Operator '{operator}' confirmed swarm dispatch.\n\n"
            f"{preview_text}"
        )
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bead_id = "bd-" + _hashlib.sha256(
            f"swarm-approval:{now}:{operator}:{directive.kind}".encode()  # type: ignore[union-attr]
        ).hexdigest()[:8]

        bead = Bead(
            bead_id=bead_id,
            task_id="",
            step_id="swarm-signoff",
            agent_name="operator",
            bead_type="approval",
            content=content,
            confidence="high",
            scope="project",
            tags=[_APPROVAL_BEAD_TAG, "operator-confirmed"],
            affected_files=affected_files,
            status="open",
            created_at=now,
            source="manual",
            token_estimate=len(content) // 4,
        )
        written_id = bead_store.write(bead)
        if written_id:
            print(f"[SIGN-OFF] Approval bead filed: {written_id}")
        return written_id or bead_id
    except Exception as exc:
        _log.warning("_file_approval_bead: non-fatal error: %s", exc)
        return ""


def _get_operator_identity() -> str:
    """Return the current OS user name, or 'unknown' on any failure."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _get_approval_mode() -> str:
    """Return the current approval mode, read fresh from env each call.

    Returns 'team' when BATON_APPROVAL_MODE=team; 'local' otherwise (default).
    Never cached — callers rely on seeing the live env value.
    """
    return os.environ.get(_APPROVAL_MODE_ENV, "local").strip().lower()


def _print_approval_mode_notice(approval_mode: str, no_require: bool) -> None:
    """Print the mode notice at swarm-launch time.

    Describes which approval regime is active and how the operator can
    change it, so there is never any ambiguity about what will happen.
    """
    if approval_mode == "team":
        if no_require:
            print(
                "[swarm] approval mode: team (second-reviewer enforced). "
                "--no-require-approval-bead override active — audit bead will be filed."
            )
        else:
            print(
                "[swarm] approval mode: team (second-reviewer enforced). "
                "Pass --no-require-approval-bead to override (audited)."
            )
    else:
        print(
            "[swarm] approval mode: local (single-reviewer). "
            "Set BATON_APPROVAL_MODE=team for second-reviewer enforcement."
        )


def _file_team_override_audit_bead(
    operator: str,
    directive: object,
) -> None:
    """File a WARNING audit bead when team-mode approval is overridden.

    Non-fatal — if the bead store is unavailable the override still proceeds,
    but a log warning is emitted so the gap is visible.
    """
    bead_store = _get_bead_store()
    if bead_store is None:
        _log.warning(
            "_file_team_override_audit_bead: no bead store available; "
            "team-mode override by '%s' will not be recorded",
            operator,
        )
        return

    try:
        from agent_baton.models.bead import Bead
        import datetime as _dt
        import hashlib as _hashlib

        directive_kind = getattr(directive, "kind", "unknown")
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bead_id = "bd-" + _hashlib.sha256(
            f"team-override:{now}:{operator}:{directive_kind}".encode()
        ).hexdigest()[:8]

        content = (
            f"WARNING: team-mode approval requirement overridden by user "
            f"'{operator}' for swarm task '{directive_kind}' at {now}."
        )

        bead = Bead(
            bead_id=bead_id,
            task_id="",
            step_id="swarm-team-override",
            agent_name="operator",
            bead_type="warning",
            content=content,
            confidence="high",
            scope="project",
            tags=[_APPROVAL_BEAD_TAG, "team-override", "audit"],
            affected_files=[],
            status="open",
            created_at=now,
            source="manual",
            token_estimate=len(content) // 4,
        )
        written_id = bead_store.write(bead)
        if written_id:
            print(
                f"[swarm] AUDIT: team-mode override bead filed: {written_id}"
            )
    except Exception as exc:
        _log.warning("_file_team_override_audit_bead: non-fatal error: %s", exc)


def _get_bead_store():
    """Return a BeadStore for the project baton.db, or None on failure.

    Resolution order (mirrors BATON_DB_PATH convention from claude_launcher.py):
    1. BATON_DB_PATH environment variable.
    2. Walk up from cwd looking for .claude/team-context/baton.db.
    """
    try:
        from agent_baton.core.engine.bead_store import BeadStore

        db_path = _find_baton_db()
        if db_path is None:
            return None
        return BeadStore(db_path=db_path)
    except Exception as exc:
        _log.debug("_get_bead_store: %s", exc)
        return None


def _find_baton_db() -> Optional[Path]:
    """Locate baton.db via BATON_DB_PATH env or directory walk."""
    env_path = os.environ.get("BATON_DB_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None

    # Walk up from cwd looking for .claude/team-context/baton.db
    candidate = Path.cwd()
    for _ in range(10):
        db = candidate / ".claude" / "team-context" / "baton.db"
        if db.exists():
            return db
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


# ---------------------------------------------------------------------------
# Partition helpers
# ---------------------------------------------------------------------------


def _partition(directive: object, codebase_root: Path, max_agents: int) -> list:
    """Partition the codebase and return chunks.  Exits on error."""
    try:
        from agent_baton.core.swarm.partitioner import ASTPartitioner
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    partitioner = _build_partitioner(codebase_root)
    try:
        return partitioner.partition(directive, max_chunks=max_agents)  # type: ignore[arg-type]
    except Exception as exc:
        print(f"Error during partitioning: {exc}", file=sys.stderr)
        sys.exit(1)


def _build_partitioner(codebase_root: Path) -> object:
    from agent_baton.core.swarm.partitioner import ASTPartitioner
    return ASTPartitioner(codebase_root)


def _dry_run_partition(
    directive: object,
    codebase_root: Path,
    max_agents: int,
    model: str,
) -> None:
    """Partition and print preview without dispatching agents."""
    chunks = _partition(directive, codebase_root, max_agents)

    if not chunks:
        print("No call sites found — nothing to refactor.")
        return

    preview_text = _build_preview_text(chunks, directive, model)
    print(preview_text)
    print("[DRY RUN] Use without --dry-run to dispatch agents.")

    # Detailed per-chunk breakdown.
    print(f"Chunk breakdown ({len(chunks)} chunk(s)):")
    for i, chunk in enumerate(chunks, start=1):
        print(
            f"  Chunk {i}: id={chunk.chunk_id[:12]} "
            f"files={len(chunk.files)} "
            f"call_sites={len(chunk.call_sites)} "
            f"est_tokens={chunk.estimated_tokens} "
            f"proof={chunk.independence_proof.kind}"
        )
        for f in chunk.files[:3]:
            print(f"    - {f}")
        if len(chunk.files) > 3:
            print(f"    ... and {len(chunk.files) - 3} more")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_swarm(
    directive: object,
    codebase_root: Path,
    max_agents: int,
    model: str,
    chunks: list,
    approval_bead_id: str = "",
) -> None:
    """Build SwarmDispatcher and run the swarm."""
    try:
        from agent_baton.core.govern.budget import BudgetEnforcer
        from agent_baton.core.swarm.dispatcher import SwarmBudgetError, SwarmDispatcher
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    budget = BudgetEnforcer()

    try:
        result = _run_with_engine(
            directive=directive,  # type: ignore[arg-type]
            budget=budget,
            max_agents=max_agents,
            model=model,
            codebase_root=codebase_root,
            approval_bead_id=approval_bead_id,
        )
    except SwarmBudgetError as exc:
        print(f"Budget check failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Swarm error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        _log.exception("Swarm dispatch failed")
        sys.exit(1)

    print(
        f"\nSwarm complete:\n"
        f"  swarm_id:        {result.swarm_id}\n"
        f"  succeeded:       {result.n_succeeded}\n"
        f"  failed:          {result.n_failed}\n"
        f"  total_tokens:    {result.total_tokens:,}\n"
        f"  cost_usd:        ${result.total_cost_usd:.4f}\n"
        f"  wall_clock:      {result.wall_clock_sec:.1f}s\n"
        f"  coalesce_branch: {result.coalesce_branch}"
    )
    if result.approval_bead_id:
        print(f"  approval_bead:   {result.approval_bead_id}")
    if result.failed_chunks:
        print(f"  failed_chunks:   {', '.join(c[:8] for c in result.failed_chunks)}")


def _run_with_engine(
    directive: object,
    budget: object,
    max_agents: int,
    model: str,
    codebase_root: Path,
    approval_bead_id: str = "",
) -> object:
    """Construct minimal engine/worktree_mgr stubs for CLI-path swarm dispatch."""
    from agent_baton.core.engine.worktree_manager import WorktreeManager
    from agent_baton.core.swarm.dispatcher import SwarmDispatcher
    from agent_baton.core.swarm.partitioner import ASTPartitioner

    partitioner = ASTPartitioner(codebase_root)
    worktree_mgr = WorktreeManager(
        project_root=codebase_root,
        max_concurrent=max_agents,
    )

    # CLI path: engine is a lightweight namespace (plan synthesis only,
    # no full execution loop needed for the partition→synthesize path).
    class _CliEngine:
        _bead_store = None

    dispatcher = SwarmDispatcher(
        engine=_CliEngine(),  # type: ignore[arg-type]
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,  # type: ignore[arg-type]
    )
    return dispatcher.dispatch(
        directive=directive,  # type: ignore[arg-type]
        max_agents=max_agents,
        model=model,
        approval_bead_id=approval_bead_id,
    )


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------


def _check_experimental() -> None:
    """Block execution unless BATON_EXPERIMENTAL contains 'swarm' (bd-18f6).

    Parses BATON_EXPERIMENTAL as a comma-separated list so multiple flags
    can coexist (e.g. BATON_EXPERIMENTAL=swarm,immune).

    - If 'swarm' is absent: print error to stderr and exit 2.
    - If 'swarm' is present: print a one-line warning to stderr so the
      operator is reminded the dispatcher is a stub, then return normally.
    """
    flags = {
        f.strip()
        for f in os.environ.get(_EXPERIMENTAL_ENV, "").split(",")
        if f.strip()
    }
    if "swarm" not in flags:
        print(_EXPERIMENTAL_BLOCKED_MSG, file=sys.stderr)
        sys.exit(2)
    print(_EXPERIMENTAL_WARNING_MSG, file=sys.stderr)


def _check_enabled() -> None:
    """Exit with a clear error when swarm is not enabled."""
    if os.environ.get(_SWARM_ENABLED_ENV, "0").strip().lower() not in ("1", "true", "yes"):
        print(_DISABLED_MSG, file=sys.stderr)
        sys.exit(1)
